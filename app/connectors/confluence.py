"""Confluence Cloud connector (same Atlassian site + API token as Jira).

Per §40: selected spaces, recently updated pages, new comments and replies —
never a local mirror of the wiki. Two bounded CQL searches per run, both
ordered by lastmodified asc:

  1. pages/blogposts (no body — title, editor, and time are enough);
  2. comments (with body, stripped to text for the AI pass; replies are
     themselves comments, so they arrive the same way).

State: account_id / display_name, last_sync (max version timestamp seen).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import ConfluenceConnectorConfig
from app.connectors.base import (
    Action,
    ActionRequest,
    ActionResult,
    SyncResult,
    ValidationResult,
)
from app.models import Activity, Source
from app.net import get_with_retry

log = logging.getLogger("work-agent")

PAGE_SIZE = 50
MAX_PAGES = 5
INITIAL_LOOKBACK_HOURS = 24
CURSOR_OVERLAP_MINUTES = 5


class ConfluenceConnector:
    name = "confluence"

    def __init__(self, config: ConfluenceConnectorConfig, email: str,
                 token: str, client: httpx.Client | None = None,
                 now: datetime | None = None):
        self.config = config
        self.site = config.base_url.rstrip("/")
        self.now = now or datetime.now(timezone.utc)
        self._client = client or httpx.Client(
            base_url=self.site, auth=(email, token), timeout=20,
            headers={"Accept": "application/json"},
        )

    def authenticate(self) -> None:
        pass  # API token auth — nothing interactive to do

    def validate(self) -> ValidationResult:
        try:
            me = get_with_retry(self._client,
                                "/wiki/rest/api/user/current").json()
        except Exception as exc:
            return ValidationResult(ok=False, detail=str(exc))
        return ValidationResult(
            ok=True, detail=f"authenticated as {me.get('displayName')}")

    def initial_sync(self) -> SyncResult:
        return self.fetch_changes({})

    def fetch_changes(self, state: dict) -> SyncResult:
        state = dict(state)
        if not state.get("account_id"):
            me = get_with_retry(self._client,
                                "/wiki/rest/api/user/current").json()
            state["account_id"] = me.get("accountId", "")
            state["display_name"] = me.get("displayName", "")
        self.account_id = state["account_id"]

        floor = self._floor(state.get("last_sync"))
        stamp = floor.strftime("%Y-%m-%d %H:%M")
        scope = ""
        if self.config.spaces:
            keys = ", ".join(f'"{k}"' for k in self.config.spaces)
            scope = f" and space in ({keys})"

        activities: list[Activity] = []
        max_when: str | None = state.get("last_sync")

        pages = self._search(
            f'type in (page, blogpost) and lastmodified >= "{stamp}"{scope}',
            expand="version,space")
        comments = self._search(
            f'type = comment and lastmodified >= "{stamp}"{scope}',
            expand="version,space,container,body.storage")

        for content, is_comment in [(c, False) for c in pages] + \
                                   [(c, True) for c in comments]:
            version = content.get("version") or {}
            when = version.get("when", "")
            if max_when is None or when > max_when:
                max_when = when
            activity = self._normalize(content, is_comment)
            if activity is not None:
                activities.append(activity)

        if max_when is not None:
            state["last_sync"] = max_when
        state["version"] = 1
        return SyncResult(activities=activities, state=state)

    # -- internals ---------------------------------------------------------

    def _search(self, cql: str, expand: str) -> list[dict]:
        results: list[dict] = []
        start = 0
        for _ in range(MAX_PAGES):
            page = get_with_retry(self._client, "/wiki/rest/api/content/search", {
                "cql": f"{cql} order by lastmodified asc",
                "expand": expand, "limit": PAGE_SIZE, "start": start,
            }).json()
            batch = page.get("results", [])
            results.extend(batch)
            if len(batch) < PAGE_SIZE:
                return results
            start += PAGE_SIZE
        log.warning("confluence: truncated at %d pages; rest next run",
                    MAX_PAGES)
        return results

    def _normalize(self, content: dict, is_comment: bool) -> Activity | None:
        version = content.get("version") or {}
        author = version.get("by") or {}
        if author.get("accountId") == self.account_id:
            return None  # the user's own edits/comments are not news to them

        when = version.get("when", "")
        webui = (content.get("_links") or {}).get("webui", "")
        url = f"{self.site}/wiki{webui}" if webui else self.site
        space = (content.get("space") or {}).get("key", "")

        if not is_comment:
            return Activity(
                id=f"confluence:{content.get('id')}:v{version.get('number')}",
                source=Source.CONFLUENCE,
                object_type=content.get("type", "page"),
                object_id=str(content.get("id")),
                event_type="page_updated",
                actor_id=author.get("accountId"),
                actor_name=author.get("displayName"),
                title=content.get("title"),
                url=url,
                created_at=_parse_dt(when) or self.now,
                metadata={"space": space},
            )

        storage = ((content.get("body") or {}).get("storage") or {})
        html = storage.get("value", "")
        container = content.get("container") or {}
        return Activity(
            id=f"confluence:{content.get('id')}:v{version.get('number')}",
            source=Source.CONFLUENCE,
            object_type="comment",
            object_id=str(container.get("id") or content.get("id")),
            thread_id=str(content.get("id")),
            event_type="comment",
            actor_id=author.get("accountId"),
            actor_name=author.get("displayName"),
            title=container.get("title") or content.get("title"),
            text=_strip_html(html),
            # Storage-format mentions carry the account id; substring match
            # is exact enough here.
            mentions_user=bool(self.account_id) and self.account_id in html,
            url=url,
            created_at=_parse_dt(when) or self.now,
            metadata={"space": space},
        )

    def _floor(self, last_sync: str | None) -> datetime:
        if last_sync:
            parsed = _parse_dt(last_sync)
            if parsed is not None:
                return parsed - timedelta(minutes=CURSOR_OVERLAP_MINUTES)
        return self.now - timedelta(hours=INITIAL_LOOKBACK_HOURS)

    def normalize(self, raw_item):
        raise NotImplementedError("confluence normalisation happens in fetch_changes")

    def available_actions(self, activity: Activity) -> list[Action]:
        return []

    def execute_action(self, request: ActionRequest) -> ActionResult:
        return ActionResult(ok=False, detail="confluence is read-only by design")


def _strip_html(html: str) -> str:
    from app.connectors.basecamp import _strip_html as strip

    return strip(html)


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
