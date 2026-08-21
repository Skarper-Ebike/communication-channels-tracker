"""Jira Cloud connector (API token auth, the user's own account).

Polls by issue `updated` timestamp (§37) — never refetches whole projects.
Deterministic facts (assignee, due date, status, new assignment) come straight
from the API; comment text goes on to the AI pass like any other activity.

State (all local, cursor advanced only after the run succeeds):
  last_updated_at       ISO cursor for incremental JQL
  seen_issue_versions   {key: updated} to skip unchanged issues
  assigned_keys         issues known to be assigned to the user, so a key
                        newly appearing as assigned is a NEW_ASSIGNMENT
  account_id / display_name   cached identity
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import JiraConnectorConfig
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
MAX_PAGES = 20
INITIAL_LOOKBACK_DAYS = 7
CURSOR_OVERLAP_MINUTES = 5
MAX_SEEN_VERSIONS = 500

FIELDS = "summary,assignee,duedate,priority,status,issuetype,created,updated,comment"

DONE_CATEGORY = "done"


class JiraConnector:
    name = "jira"

    def __init__(self, config: JiraConnectorConfig, email: str, token: str,
                 client: httpx.Client | None = None,
                 now: datetime | None = None):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.now = now or datetime.now(timezone.utc)
        self._client = client or httpx.Client(
            base_url=self.base_url, auth=(email, token), timeout=20,
            headers={"Accept": "application/json"},
        )

    def authenticate(self) -> None:
        pass  # API token auth — nothing interactive to do

    def validate(self) -> ValidationResult:
        try:
            me = get_with_retry(self._client, "/rest/api/3/myself").json()
        except Exception as exc:
            return ValidationResult(ok=False, detail=str(exc))
        return ValidationResult(ok=True,
                                detail=f"authenticated as {me.get('displayName')}")

    def initial_sync(self) -> SyncResult:
        return self.fetch_changes({})

    def fetch_changes(self, state: dict) -> SyncResult:
        state = dict(state)
        if not state.get("account_id"):
            me = get_with_retry(self._client, "/rest/api/3/myself").json()
            state["account_id"] = me["accountId"]
            state["display_name"] = me.get("displayName", "")
        account_id = state["account_id"]

        cursor = state.get("last_updated_at")
        initial = cursor is None
        issues = self._search(self._build_jql(cursor))

        seen_versions: dict[str, str] = dict(state.get("seen_issue_versions", {}))
        assigned_keys: set[str] = set(state.get("assigned_keys", []))
        comment_floor = self._comment_floor(cursor)

        activities: list[Activity] = []
        max_updated = cursor
        for issue in issues:
            key = issue["key"]
            fields = issue.get("fields", {})
            updated = fields.get("updated", "")
            if seen_versions.get(key) == updated:
                continue
            seen_versions[key] = updated
            if max_updated is None or updated > max_updated:
                max_updated = updated

            assignee = fields.get("assignee") or {}
            assigned = assignee.get("accountId") == account_id
            newly_assigned = assigned and not initial and key not in assigned_keys
            if assigned:
                assigned_keys.add(key)
            else:
                assigned_keys.discard(key)

            activities.extend(self._issue_activities(
                key, fields, assigned=assigned, newly_assigned=newly_assigned,
                account_id=account_id, comment_floor=comment_floor,
            ))

        state["seen_issue_versions"] = dict(
            sorted(seen_versions.items(), key=lambda kv: kv[1])[-MAX_SEEN_VERSIONS:]
        )
        state["assigned_keys"] = sorted(assigned_keys)
        if max_updated is not None:
            state["last_updated_at"] = max_updated
        state["version"] = 1
        state["last_sync"] = self.now.isoformat()
        return SyncResult(activities=activities, state=state)

    # -- internals ---------------------------------------------------------

    def _build_jql(self, cursor: str | None) -> str:
        scope = "assignee = currentUser()"
        if self.config.projects:
            projects = ", ".join(self.config.projects)
            scope = f"({scope} OR project in ({projects}))"

        if cursor is None:
            assigned_open = ("assignee = currentUser() "
                             "AND statusCategory != Done")
            since = (f'updated >= -{INITIAL_LOOKBACK_DAYS}d AND {scope}')
            return f"(({assigned_open}) OR ({since})) ORDER BY updated ASC"

        overlap = _parse_dt(cursor) - timedelta(minutes=CURSOR_OVERLAP_MINUTES)
        stamp = overlap.strftime("%Y-%m-%d %H:%M")
        return f'updated >= "{stamp}" AND {scope} ORDER BY updated ASC'

    def _search(self, jql: str) -> list[dict]:
        issues: list[dict] = []
        next_page_token: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {
                "jql": jql, "fields": FIELDS, "maxResults": PAGE_SIZE,
            }
            if next_page_token:
                params["nextPageToken"] = next_page_token
            page = get_with_retry(self._client, "/rest/api/3/search/jql",
                                  params=params).json()
            issues.extend(page.get("issues", []))
            next_page_token = page.get("nextPageToken")
            if not next_page_token:
                return issues
        log.warning("jira: stopped after %d pages; remaining changes are "
                    "picked up next run", MAX_PAGES)
        return issues

    def _issue_activities(self, key: str, fields: dict, *, assigned: bool,
                          newly_assigned: bool, account_id: str,
                          comment_floor: datetime | None) -> list[Activity]:
        url = f"{self.base_url}/browse/{key}"
        status = fields.get("status") or {}
        completed = (status.get("statusCategory") or {}).get("key") == DONE_CATEGORY
        priority = (fields.get("priority") or {}).get("name")
        updated = fields.get("updated", "")
        common = dict(
            source=Source.JIRA, object_type="issue", object_id=key, url=url,
            title=fields.get("summary"), assigned_to_user=assigned,
            due_at=_parse_due(fields.get("duedate")),
            completed=completed,
            priority=priority.lower() if priority else None,
        )

        activities = [Activity(
            id=f"jira:{key}:updated:{updated}",
            event_type="issue_updated",
            created_at=_parse_dt(updated) if updated else self.now,
            updated_at=_parse_dt(updated) if updated else None,
            metadata={"status": status.get("name")},
            **common,
        )]

        if newly_assigned:
            activities.append(Activity(
                id=f"jira:{key}:assigned:{updated}",
                event_type="assigned",
                created_at=_parse_dt(updated) if updated else self.now,
                **common,
            ))

        for comment in (fields.get("comment") or {}).get("comments", []):
            stamp = comment.get("updated") or comment.get("created") or ""
            author = comment.get("author") or {}
            if author.get("accountId") == account_id:
                continue  # the user's own comments are not news to them
            if comment_floor is not None and \
                    (not stamp or _parse_dt(stamp) <= comment_floor):
                continue
            body = comment.get("body")
            activities.append(Activity(
                id=f"jira:{key}:comment:{comment.get('id')}:{stamp}",
                event_type="comment",
                actor_id=author.get("accountId"),
                actor_name=author.get("displayName"),
                text=adf_to_text(body),
                created_at=_parse_dt(stamp) if stamp else self.now,
                mentions_user=adf_mentions(body, account_id),
                **common,
            ))
        return activities

    def _comment_floor(self, cursor: str | None) -> datetime | None:
        if cursor is not None:
            return _parse_dt(cursor) - timedelta(minutes=CURSOR_OVERLAP_MINUTES)
        return self.now - timedelta(days=INITIAL_LOOKBACK_DAYS)

    def normalize(self, raw_item: Any) -> list[Activity]:
        raise NotImplementedError("jira normalisation happens in fetch_changes")

    def available_actions(self, activity: Activity) -> list[Action]:
        return []

    def execute_action(self, request: ActionRequest) -> ActionResult:
        return ActionResult(ok=False, detail="jira write-back is not implemented")


def _parse_dt(stamp: str) -> datetime:
    dt = datetime.fromisoformat(stamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_due(duedate: str | None) -> datetime | None:
    # Jira due dates are bare dates; treat as due end-of-day so nothing is
    # "overdue" while its due day is still in progress.
    if not duedate:
        return None
    return datetime.fromisoformat(duedate).replace(
        hour=23, minute=59, tzinfo=timezone.utc)


def adf_to_text(node: Any) -> str:
    """Flatten an Atlassian Document Format tree to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(child) for child in node)
    if not isinstance(node, dict):
        return ""
    node_type = node.get("type")
    if node_type == "text":
        return node.get("text", "")
    if node_type == "mention":
        return (node.get("attrs") or {}).get("text", "")
    if node_type == "hardBreak":
        return "\n"
    inner = adf_to_text(node.get("content", []))
    if node_type in ("paragraph", "heading", "listItem", "blockquote"):
        return inner + "\n"
    return inner


def adf_mentions(node: Any, account_id: str) -> bool:
    if isinstance(node, list):
        return any(adf_mentions(child, account_id) for child in node)
    if not isinstance(node, dict):
        return False
    if node.get("type") == "mention" and \
            (node.get("attrs") or {}).get("id") == account_id:
        return True
    return adf_mentions(node.get("content", []), account_id)
