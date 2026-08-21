"""Basecamp 3/4 connector (OAuth, the user's own account).

Per §39, structured work first: todos with assignees/due dates drive the
deterministic rules; comments and messages are text for the AI pass. Polling
uses the cross-project recordings endpoint (`GET /projects/recordings.json`)
per type, sorted by updated_at desc, stopping at the local floor — no project
mirroring.

OAuth is Launchpad's non-standard flavour (`type=web_server` / `type=refresh`);
tokens live in .local/secrets/basecamp-token.json.

State:
  account_id / person_id / person_name   discovered identity
  last_sync                              floor for the next poll (with overlap)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import httpx

from app.config import BasecampConnectorConfig
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

LAUNCHPAD = "https://launchpad.37signals.com"
TOKEN_URL = f"{LAUNCHPAD}/authorization/token"
USER_AGENT = ("Personal Work Agent "
              "(https://github.com/Skarper-Ebike/communication-channels-tracker)")

RECORDING_TYPES = ("Todo", "Comment", "Message")
MAX_PAGES_PER_TYPE = 5
INITIAL_LOOKBACK_DAYS = 7
CURSOR_OVERLAP_MINUTES = 5


def default_token_store(paths: LocalPaths | None = None) -> TokenStore:
    import os

    paths = paths or LocalPaths()
    return TokenStore(
        paths.secrets / "basecamp-token.json", TOKEN_URL,
        os.environ.get("BASECAMP_CLIENT_ID", ""),
        os.environ.get("BASECAMP_CLIENT_SECRET", ""),
        extra_refresh_params={"type": "refresh"},
    )


class BasecampConnector:
    name = "basecamp"

    def __init__(self, config: BasecampConnectorConfig,
                 token_store: TokenStore | None = None,
                 client: httpx.Client | None = None,
                 now: datetime | None = None):
        self.config = config
        self.tokens = token_store or default_token_store()
        self.now = now or datetime.now(timezone.utc)
        self._client = client or httpx.Client(
            timeout=20, headers={"User-Agent": USER_AGENT})

    def authenticate(self) -> None:
        pass  # interactive flow lives in the setup wizard / reconnect

    def validate(self) -> ValidationResult:
        try:
            auth = self._get(f"{LAUNCHPAD}/authorization.json")
        except Exception as exc:
            return ValidationResult(ok=False, detail=str(exc))
        identity = auth.get("identity", {})
        accounts = _basecamp_accounts(auth)
        if not accounts:
            return ValidationResult(ok=False, detail="no Basecamp account "
                                                     "on this login")
        return ValidationResult(
            ok=True,
            detail=f"authenticated as {identity.get('email_address', 'unknown')}")

    def initial_sync(self) -> SyncResult:
        return self.fetch_changes({})

    def fetch_changes(self, state: dict) -> SyncResult:
        state = dict(state)
        account_id = self.config.account_id or state.get("account_id")
        if not account_id:
            accounts = _basecamp_accounts(
                self._get(f"{LAUNCHPAD}/authorization.json"))
            if not accounts:
                raise RuntimeError("no Basecamp (bc3) account for this login")
            account_id = str(accounts[0]["id"])
        base = f"https://3.basecampapi.com/{account_id}"

        if not state.get("person_id"):
            me = self._get(f"{base}/my/profile.json")
            state["person_id"] = me["id"]
            state["person_name"] = me.get("name", "")
        self.person_id = state["person_id"]
        self.person_name = state.get("person_name", "")

        floor = self._floor(state.get("last_sync"))
        activities: list[Activity] = []
        max_updated: str | None = state.get("last_sync")
        for rtype in RECORDING_TYPES:
            for recording in self._recordings(base, rtype, floor):
                updated = recording.get("updated_at", "")
                if max_updated is None or updated > max_updated:
                    max_updated = updated
                activity = self._normalize(rtype, recording)
                if activity is not None:
                    activities.append(activity)

        state["account_id"] = account_id
        if max_updated is not None:
            state["last_sync"] = max_updated
        state["version"] = 1
        return SyncResult(activities=activities, state=state)

    # -- internals ---------------------------------------------------------

    def _recordings(self, base: str, rtype: str, floor: datetime):
        url = f"{base}/projects/recordings.json"
        params = {"type": rtype, "sort": "updated_at", "direction": "desc"}
        for _ in range(MAX_PAGES_PER_TYPE):
            response = self._get_response(url, params)
            items = response.json()
            for item in items:
                updated = _parse_dt(item.get("updated_at"))
                if updated is not None and updated <= floor:
                    return  # sorted desc: everything after this is older
                yield item
            next_url = response.links.get("next", {}).get("url")
            if not next_url:
                return
            url, params = next_url, None
        log.warning("basecamp: %s truncated at %d pages", rtype,
                    MAX_PAGES_PER_TYPE)

    def _normalize(self, rtype: str, r: dict) -> Activity | None:
        creator = r.get("creator") or {}
        title = r.get("title") or r.get("subject") or ""
        parent_title = (r.get("parent") or {}).get("title", "")
        common = dict(
            source=Source.BASECAMP,
            object_id=str(r.get("id")),
            url=r.get("app_url", ""),
            created_at=_parse_dt(r.get("updated_at")) or self.now,
            actor_id=str(creator.get("id", "")),
            actor_name=creator.get("name"),
        )

        if rtype == "Todo":
            assignees = r.get("assignees") or []
            assigned = any(a.get("id") == self.person_id for a in assignees)
            return Activity(
                id=f"basecamp:todo-{r.get('id')}:updated:{r.get('updated_at')}",
                object_type="todo",
                event_type="todo_updated",
                title=title or r.get("content", ""),
                text=_strip_html(r.get("description", "")) or None,
                assigned_to_user=assigned,
                due_at=_parse_due(r.get("due_on")),
                completed=bool(r.get("completed")),
                **common,
            )

        # Comments and Messages by the user are not news to them.
        if creator.get("id") == self.person_id:
            return None
        text = _strip_html(r.get("content", ""))
        mentions = bool(self.person_name) and self.person_name in text
        return Activity(
            id=f"basecamp:{rtype.lower()}-{r.get('id')}:{r.get('updated_at')}",
            object_type=rtype.lower(),
            thread_id=str((r.get("parent") or {}).get("id", "")) or None,
            event_type=rtype.lower(),
            title=title or parent_title,
            text=text,
            # Name-in-text is the best mention signal Basecamp's HTML gives
            # us; treat it as a mention, and let the AI pass judge intent.
            mentions_user=mentions,
            **common,
        )

    def _floor(self, last_sync: str | None) -> datetime:
        if last_sync:
            parsed = _parse_dt(last_sync)
            if parsed is not None:
                return parsed - timedelta(minutes=CURSOR_OVERLAP_MINUTES)
        return self.now - timedelta(days=INITIAL_LOOKBACK_DAYS)

    def _get(self, url: str, params: dict | None = None) -> dict | list:
        return self._get_response(url, params).json()

    def _get_response(self, url: str, params: dict | None = None):
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self.tokens.access_token()}"}
            try:
                return get_with_retry(
                    HeaderClient(self._client, headers), url, params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401 and attempt == 1:
                    self.tokens.refresh()
                    continue
                raise

    def normalize(self, raw_item):
        raise NotImplementedError("basecamp normalisation happens in fetch_changes")

    def available_actions(self, activity: Activity) -> list[Action]:
        return []

    def execute_action(self, request: ActionRequest) -> ActionResult:
        return ActionResult(ok=False, detail="basecamp write-back is not implemented")


def _basecamp_accounts(auth: dict) -> list[dict]:
    # Match on the API host, not the product label — current Basecamp
    # versions report different product strings (bc3, bc4, …) but all live
    # on 3.basecampapi.com.
    return [a for a in auth.get("accounts", [])
            if "basecampapi.com" in a.get("href", "")
            or a.get("product") == "bc3"]


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "li"):
            self.parts.append("\n")


def _strip_html(html: str) -> str:
    if not html:
        return ""
    extractor = _TextExtractor()
    extractor.feed(html)
    return "".join(extractor.parts).strip()


def _parse_dt(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_due(due_on: str | None) -> datetime | None:
    # due_on is a bare date; due end-of-day, same convention as Jira.
    if not due_on:
        return None
    try:
        return datetime.fromisoformat(due_on).replace(
            hour=23, minute=59, tzinfo=timezone.utc)
    except ValueError:
        return None
