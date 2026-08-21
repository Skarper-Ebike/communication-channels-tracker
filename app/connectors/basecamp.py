"""Basecamp 2 (BCX) connector — the classic API at basecamp.com/{id}/api/v1.

Two polls per run, both bounded (§39):
  1. the user's assigned todos (people/{id}/assigned_todos.json) — the
     deterministic overdue/due-soon signals;
  2. the global events feed (events.json?since=…) — comments, messages, and
     todo completions (which resolve open attention items).

OAuth is Launchpad's non-standard flavour (`type=web_server` / `type=refresh`);
tokens live in .local/secrets/basecamp-token.json. Endpoint shapes verified
against basecamp/bcx-api docs (events.md, todos.md, people.md).

State: base_url, person_id, person_name, last_sync.
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

EVENTS_PAGE_SIZE = 50
MAX_EVENT_PAGES = 5
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
        if not _bcx_accounts(auth):
            return ValidationResult(
                ok=False, detail="no Basecamp 2 account on this login")
        return ValidationResult(
            ok=True,
            detail=f"authenticated as {identity.get('email_address', 'unknown')}")

    def initial_sync(self) -> SyncResult:
        return self.fetch_changes({})

    def fetch_changes(self, state: dict) -> SyncResult:
        state = dict(state)
        base = state.get("base_url") or self._discover_base()

        if not state.get("person_id"):
            me = self._get(f"{base}/people/me.json")
            state["person_id"] = me["id"]
            state["person_name"] = me.get("name", "")
        self.person_id = state["person_id"]
        self.person_name = state.get("person_name", "")

        activities: list[Activity] = []
        for todo in self._assigned_todos(base):
            activities.append(self._todo_activity(todo))

        floor = self._floor(state.get("last_sync"))
        max_created: str | None = state.get("last_sync")
        for event in self._events(base, floor):
            created = event.get("created_at", "")
            if max_created is None or created > max_created:
                max_created = created
            activity = self._event_activity(event)
            if activity is not None:
                activities.append(activity)

        state["base_url"] = base
        if max_created is not None:
            state["last_sync"] = max_created
        state["version"] = 1
        return SyncResult(activities=activities, state=state)

    # -- internals ---------------------------------------------------------

    def _discover_base(self) -> str:
        if self.config.account_id:
            return f"https://basecamp.com/{self.config.account_id}/api/v1"
        accounts = _bcx_accounts(self._get(f"{LAUNCHPAD}/authorization.json"))
        if not accounts:
            raise RuntimeError("no Basecamp 2 (bcx) account for this login")
        return accounts[0]["href"].rstrip("/")

    def _assigned_todos(self, base: str) -> list[dict]:
        payload = self._get(f"{base}/people/{self.person_id}/assigned_todos.json")
        # The documented person resource only shows the wrapper; in practice
        # this returns todolists with `assigned_todos` — accept a flat todo
        # list too, defensively.
        todos: list[dict] = []
        items = payload if isinstance(payload, list) else \
            payload.get("todolists", payload.get("assigned_todos", []))
        for item in items:
            if "assigned_todos" in item:
                todos.extend(item["assigned_todos"])
            elif "content" in item:
                todos.append(item)
        return todos

    def _todo_activity(self, todo: dict) -> Activity:
        updated = todo.get("updated_at", "")
        return Activity(
            id=f"basecamp:todo-{todo.get('id')}:updated:{updated}",
            source=Source.BASECAMP,
            object_type="todo",
            object_id=str(todo.get("id")),
            event_type="todo_updated",
            title=todo.get("content", ""),
            url=todo.get("app_url", ""),
            created_at=_parse_dt(updated) or self.now,
            assigned_to_user=True,  # by definition of the endpoint
            due_at=_parse_due(todo.get("due_at") or todo.get("due_on")),
            completed=bool(todo.get("completed")),
        )

    def _events(self, base: str, floor: datetime):
        # "+" in the since offset must be url-encoded; httpx params handle it.
        since = floor.isoformat()
        for page in range(1, MAX_EVENT_PAGES + 1):
            params = {"since": since}
            if page > 1:
                params["page"] = page
            items = self._get(f"{base}/events.json", params)
            yield from items
            if len(items) < EVENTS_PAGE_SIZE:
                return
        log.warning("basecamp: events truncated at %d pages", MAX_EVENT_PAGES)

    def _event_activity(self, event: dict) -> Activity | None:
        creator = event.get("creator") or {}
        eventable = event.get("eventable") or {}
        action = event.get("action", "")
        object_id = str(eventable.get("id", event.get("id")))

        if eventable.get("type") == "Todo" and "completed" in action \
                and "uncompleted" not in action:
            # Someone finished a todo: resolves any open attention item on it,
            # regardless of who completed it.
            return Activity(
                id=f"basecamp:event-{event.get('id')}",
                source=Source.BASECAMP,
                object_type="todo",
                object_id=object_id,
                event_type="todo_completed",
                actor_id=str(creator.get("id", "")),
                actor_name=creator.get("name"),
                title=event.get("target") or event.get("summary", ""),
                url=event.get("html_url", ""),
                created_at=_parse_dt(event.get("created_at")) or self.now,
                completed=True,
            )

        if creator.get("id") == self.person_id:
            return None  # the user's own actions are not news to them

        text = _strip_html(event.get("excerpt")
                           or event.get("raw_excerpt") or "")
        is_comment = "comment" in action
        summary = event.get("summary", "")
        mentions = bool(self.person_name) and \
            self.person_name in f"{summary} {text}"
        return Activity(
            id=f"basecamp:event-{event.get('id')}",
            source=Source.BASECAMP,
            object_type=eventable.get("type", "event").lower(),
            object_id=object_id,
            event_type="comment" if is_comment else "event",
            actor_id=str(creator.get("id", "")),
            actor_name=creator.get("name"),
            title=event.get("target") or summary,
            text=text or summary or None,
            # Name-in-text is the best mention signal BCX gives us; the AI
            # pass judges actual intent.
            mentions_user=mentions,
            url=event.get("html_url", ""),
            created_at=_parse_dt(event.get("created_at")) or self.now,
        )

    def _floor(self, last_sync: str | None) -> datetime:
        if last_sync:
            parsed = _parse_dt(last_sync)
            if parsed is not None:
                return parsed - timedelta(minutes=CURSOR_OVERLAP_MINUTES)
        return self.now - timedelta(days=INITIAL_LOOKBACK_DAYS)

    def _get(self, url: str, params: dict | None = None):
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self.tokens.access_token()}"}
            try:
                return get_with_retry(
                    HeaderClient(self._client, headers), url, params).json()
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


def _bcx_accounts(auth: dict) -> list[dict]:
    return [a for a in auth.get("accounts", [])
            if a.get("product") == "bcx"]


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


def _parse_due(due: str | None) -> datetime | None:
    if not due:
        return None
    parsed = _parse_dt(due)
    if parsed is not None and len(due) > 10:
        return parsed  # due_at carries a real time
    try:
        # due_on is a bare date; due end-of-day, same convention as Jira.
        return datetime.fromisoformat(due[:10]).replace(
            hour=23, minute=59, tzinfo=timezone.utc)
    except ValueError:
        return None
