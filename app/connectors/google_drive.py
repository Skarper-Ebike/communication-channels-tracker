"""Google Drive / Slides connector (read-only, incremental changes API).

Per §38: persist the change page token locally, surface changed files and —
for Docs/Slides/Sheets — new comments and replies. No slide diffing, no
content mirroring; briefing context is title, editor, modified time, and
comment text.

State:
  start_page_token   Drive changes cursor (initialised on first run, §41)
  last_sync          floor for comment modification times
  email / name       the user's own identity, to skip their own edits
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import GoogleDriveConnectorConfig
from app.connectors.base import (
    Action,
    ActionRequest,
    ActionResult,
    SyncResult,
    ValidationResult,
)
from app.models import Activity, Source
from app.net import HeaderClient, get_with_retry
from app.setup.oauth import TokenStore
from app.storage import LocalPaths

log = logging.getLogger("work-agent")

API = "https://www.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

PAGE_SIZE = 100
MAX_PAGES = 10

COMMENTABLE_TYPES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.spreadsheet",
}

CHANGE_FIELDS = ("nextPageToken,newStartPageToken,changes(fileId,removed,"
                 "file(id,name,mimeType,modifiedTime,webViewLink,"
                 "lastModifyingUser(displayName,emailAddress)))")
COMMENT_FIELDS = ("comments(id,author(displayName,emailAddress),content,"
                  "modifiedTime,resolved,quotedFileContent(value),"
                  "replies(id,author(displayName,emailAddress),content,"
                  "modifiedTime))")


def default_token_store(paths: LocalPaths | None = None) -> TokenStore:
    import os

    paths = paths or LocalPaths()
    return TokenStore(
        paths.secrets / "google-token.json", TOKEN_URL,
        os.environ.get("GOOGLE_CLIENT_ID", ""),
        os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    )


class GoogleDriveConnector:
    name = "google_drive"

    def __init__(self, config: GoogleDriveConnectorConfig,
                 token_store: TokenStore | None = None,
                 client: httpx.Client | None = None,
                 now: datetime | None = None):
        self.config = config
        self.tokens = token_store or default_token_store()
        self.now = now or datetime.now(timezone.utc)
        self._client = client or httpx.Client(base_url=API, timeout=20)

    def authenticate(self) -> None:
        pass  # interactive flow lives in the setup wizard / reconnect

    def validate(self) -> ValidationResult:
        try:
            about = self._get("/drive/v3/about", {"fields": "user"})
        except Exception as exc:
            return ValidationResult(ok=False, detail=str(exc))
        user = about.get("user", {})
        return ValidationResult(
            ok=True,
            detail=f"authenticated as {user.get('emailAddress', 'unknown')}")

    def initial_sync(self) -> SyncResult:
        return self.fetch_changes({})

    def fetch_changes(self, state: dict) -> SyncResult:
        state = dict(state)
        if not state.get("email"):
            user = self._get("/drive/v3/about", {"fields": "user"}).get("user", {})
            state["email"] = user.get("emailAddress", "")
            state["name"] = user.get("displayName", "")
        self.email = state["email"]

        if not state.get("start_page_token"):
            token = self._get("/drive/v3/changes/startPageToken",
                              {"supportsAllDrives": "true"})["startPageToken"]
            state.update(version=1, start_page_token=token,
                         last_sync=self.now.isoformat())
            log.info("google_drive: change cursor initialised")
            return SyncResult(activities=[], state=state)

        last_sync = state.get("last_sync")
        activities: list[Activity] = []
        page_token = state["start_page_token"]
        new_start: str | None = None
        for _ in range(MAX_PAGES):
            page = self._get("/drive/v3/changes", {
                "pageToken": page_token, "pageSize": PAGE_SIZE,
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true", "fields": CHANGE_FIELDS,
            })
            for change in page.get("changes", []):
                activities.extend(self._change_activities(change, last_sync))
            if "newStartPageToken" in page:
                new_start = page["newStartPageToken"]
                break
            page_token = page.get("nextPageToken", page_token)
        else:
            # More pages remain: keep the mid-stream token and continue next
            # run rather than dropping changes.
            new_start = page_token
            log.warning("google_drive: truncated at %d pages", MAX_PAGES)

        state["start_page_token"] = new_start
        state["last_sync"] = self.now.isoformat()
        state["version"] = 1
        return SyncResult(activities=activities, state=state)

    # -- internals ---------------------------------------------------------

    def _change_activities(self, change: dict, last_sync: str | None):
        file = change.get("file")
        if change.get("removed") or not file:
            return
        modifier = file.get("lastModifyingUser", {}) or {}
        if modifier.get("emailAddress") != self.email:
            yield Activity(
                id=f"google_drive:{file['id']}:changed:{file.get('modifiedTime')}",
                source=Source.GOOGLE_DRIVE,
                object_type="file",
                object_id=file["id"],
                event_type="file_changed",
                actor_name=modifier.get("displayName"),
                title=file.get("name"),
                url=file.get("webViewLink", ""),
                created_at=_parse_dt(file.get("modifiedTime")) or self.now,
                metadata={"mimeType": file.get("mimeType", "")},
            )
        if file.get("mimeType") in COMMENTABLE_TYPES:
            yield from self._comment_activities(file, last_sync)

    def _comment_activities(self, file: dict, last_sync: str | None):
        params: dict[str, Any] = {"pageSize": PAGE_SIZE,
                                  "fields": COMMENT_FIELDS}
        if last_sync:
            params["startModifiedTime"] = last_sync
        try:
            comments = self._get(f"/drive/v3/files/{file['id']}/comments",
                                 params).get("comments", [])
        except httpx.HTTPStatusError as exc:
            # Shared files can allow change-visibility but not comment reads.
            log.info("google_drive: comments unreadable for %s (HTTP %s)",
                     file.get("name"), exc.response.status_code)
            return

        for comment in comments:
            thread_authors = {(comment.get("author") or {}).get("emailAddress")}
            yield from self._one_comment(file, comment, comment,
                                         kind="comment",
                                         directed=False, floor=last_sync)
            for reply in comment.get("replies", []):
                directed = self.email in thread_authors
                thread_authors.add((reply.get("author") or {}).get("emailAddress"))
                yield from self._one_comment(file, comment, reply,
                                             kind="reply",
                                             directed=directed,
                                             floor=last_sync)

    def _one_comment(self, file: dict, thread: dict, item: dict, *,
                     kind: str, directed: bool, floor: str | None):
        author = item.get("author") or {}
        modified = item.get("modifiedTime", "")
        if author.get("emailAddress") == self.email:
            return
        if floor and modified and modified <= floor:
            return
        text = item.get("content", "")
        quoted = (thread.get("quotedFileContent") or {}).get("value")
        if quoted and kind == "comment":
            text = f'On "{quoted}": {text}'
        yield Activity(
            id=f"google_drive:{file['id']}:{kind}:{item.get('id')}:{modified}",
            source=Source.GOOGLE_DRIVE,
            object_type=kind,
            object_id=file["id"],
            thread_id=thread.get("id"),
            event_type=kind,
            actor_name=author.get("displayName"),
            title=file.get("name"),
            text=text,
            url=file.get("webViewLink", ""),
            created_at=_parse_dt(modified) or self.now,
            mentions_user=bool(self.email) and self.email in (text or ""),
            directed_at_user=directed,
        )

    def _get(self, path: str, params: dict) -> dict:
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self.tokens.access_token()}"}
            try:
                return get_with_retry(
                    HeaderClient(self._client, headers), path, params).json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401 and attempt == 1:
                    self.tokens.refresh()  # stale access token: one refresh
                    continue
                raise

    def normalize(self, raw_item):
        raise NotImplementedError("drive normalisation happens in fetch_changes")

    def available_actions(self, activity: Activity) -> list[Action]:
        return []

    def execute_action(self, request: ActionRequest) -> ActionResult:
        return ActionResult(ok=False, detail="drive is read-only by design")


def _parse_dt(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
