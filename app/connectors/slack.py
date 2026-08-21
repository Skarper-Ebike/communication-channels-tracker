"""Slack input connector: reads explicitly configured channels only (§35).

Self-feedback prevention (§36) is structural: the agent's own output channel
is never polled, bot messages are skipped, and the user's own messages are
never turned into activities (they only serve as thread context for the
"reply after your contribution" signal).

Auth: the user's own token (SLACK_USER_TOKEN, xoxp-…) with read scopes —
channels:history, groups:history, users:read.

State:
  user_id / team_domain     cached identity
  channels: {id: {last_timestamp}}   per-channel cursor (§11)
  user_names: {id: name}    display-name cache
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import SlackConnectorConfig
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

API = "https://slack.com/api"
PAGE_SIZE = 200
MAX_PAGES = 5
INITIAL_LOOKBACK_HOURS = 24


class SlackAPIError(Exception):
    pass


class SlackConnector:
    name = "slack"

    def __init__(self, config: SlackConnectorConfig, token: str,
                 output_channel_id: str | None = None,
                 client: httpx.Client | None = None,
                 now: datetime | None = None):
        self.config = config
        self.output_channel_id = output_channel_id
        self.now = now or datetime.now(timezone.utc)
        self._client = client or httpx.Client(
            base_url=API, timeout=20,
            headers={"Authorization": f"Bearer {token}"},
        )

    def authenticate(self) -> None:
        pass  # token auth — nothing interactive to do

    def validate(self) -> ValidationResult:
        try:
            identity = self._call("auth.test")
        except Exception as exc:
            return ValidationResult(ok=False, detail=str(exc))
        return ValidationResult(
            ok=True, detail=f"authenticated as {identity.get('user')}")

    def initial_sync(self) -> SyncResult:
        return self.fetch_changes({})

    def fetch_changes(self, state: dict) -> SyncResult:
        state = dict(state)
        if not state.get("user_id"):
            identity = self._call("auth.test")
            state["user_id"] = identity["user_id"]
            state["team_domain"] = identity.get("url", "").removeprefix(
                "https://").split(".")[0] or "app"
        self.user_id = state["user_id"]
        self.team_domain = state["team_domain"]
        self._names: dict[str, str] = dict(state.get("user_names", {}))

        channels_state = dict(state.get("channels", {}))
        default_oldest = (self.now
                          - timedelta(hours=INITIAL_LOOKBACK_HOURS)).timestamp()

        activities: list[Activity] = []
        for channel_id in self.config.channels:
            if channel_id == self.output_channel_id:
                log.warning("slack: skipping agent output channel %s", channel_id)
                continue
            oldest = channels_state.get(channel_id, {}).get(
                "last_timestamp") or f"{default_oldest:.6f}"
            latest_seen = oldest
            for message in self._history(channel_id, oldest):
                ts = message.get("ts", "")
                if ts > latest_seen:
                    latest_seen = ts
                activities.extend(
                    self._message_activities(channel_id, message, oldest))
            channels_state[channel_id] = {"last_timestamp": latest_seen}

        state["channels"] = channels_state
        state["user_names"] = self._names
        state["version"] = 1
        state["last_sync"] = self.now.isoformat()
        return SyncResult(activities=activities, state=state)

    # -- internals ---------------------------------------------------------

    def _message_activities(self, channel_id: str, message: dict,
                            oldest: str) -> list[Activity]:
        activities = []
        activity = self._to_activity(channel_id, message, event_type="message")
        if activity is not None:
            activities.append(activity)

        # Thread replies newer than the cursor, with "after the user's
        # contribution" decided from the thread itself.
        if message.get("reply_count") and \
                message.get("latest_reply", "") > oldest:
            thread = self._call("conversations.replies", {
                "channel": channel_id, "ts": message["ts"],
                "limit": PAGE_SIZE,
            }).get("messages", [])
            user_in_thread = any(m.get("user") == self.user_id for m in thread)
            for reply in thread[1:]:
                if reply.get("ts", "") <= oldest:
                    continue
                reply_activity = self._to_activity(
                    channel_id, reply, event_type="reply",
                    directed_at_user=user_in_thread,
                    thread_ts=message["ts"])
                if reply_activity is not None:
                    activities.append(reply_activity)
        return activities

    def _to_activity(self, channel_id: str, message: dict, *,
                     event_type: str, directed_at_user: bool = False,
                     thread_ts: str | None = None) -> Activity | None:
        if message.get("subtype") or message.get("bot_id"):
            return None  # joins/edits/bots — including our own briefings
        author = message.get("user")
        if not author or author == self.user_id:
            return None  # the user's own messages are not news to them
        ts = message["ts"]
        text = message.get("text", "")
        mentions = f"<@{self.user_id}>" in text
        return Activity(
            id=f"slack:{channel_id}:{ts}",
            source=Source.SLACK,
            object_type="message",
            object_id=channel_id,
            thread_id=thread_ts or message.get("thread_ts") or ts,
            event_type=event_type,
            actor_id=author,
            actor_name=self._user_name(author),
            text=self._resolve_mentions(text),
            url=(f"https://{self.team_domain}.slack.com/archives/"
                 f"{channel_id}/p{ts.replace('.', '')}"),
            created_at=datetime.fromtimestamp(float(ts), tz=timezone.utc),
            mentions_user=mentions,
            directed_at_user=directed_at_user or mentions,
        )

    def _history(self, channel_id: str, oldest: str) -> list[dict]:
        messages: list[dict] = []
        cursor = None
        for _ in range(MAX_PAGES):
            params = {"channel": channel_id, "oldest": oldest,
                      "limit": PAGE_SIZE, "inclusive": False}
            if cursor:
                params["cursor"] = cursor
            page = self._call("conversations.history", params)
            messages.extend(page.get("messages", []))
            cursor = (page.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        else:
            log.warning("slack: %s truncated at %d pages; rest next run",
                        channel_id, MAX_PAGES)
        return sorted(messages, key=lambda m: m.get("ts", ""))

    def _user_name(self, user_id: str) -> str:
        if user_id not in self._names:
            try:
                info = self._call("users.info", {"user": user_id})["user"]
                profile = info.get("profile", {})
                self._names[user_id] = (profile.get("display_name")
                                        or info.get("real_name") or user_id)
            except Exception:
                self._names[user_id] = user_id
        return self._names[user_id]

    def _resolve_mentions(self, text: str) -> str:
        return re.sub(r"<@([UW][A-Z0-9_]+)>",
                      lambda m: f"@{self._user_name(m.group(1))}", text)

    def _call(self, method: str, params: dict | None = None) -> dict:
        response = get_with_retry(self._client, f"/{method}", params=params)
        payload = response.json()
        if not payload.get("ok"):
            raise SlackAPIError(f"{method}: {payload.get('error', 'unknown')}")
        return payload

    def normalize(self, raw_item):
        raise NotImplementedError("slack normalisation happens in fetch_changes")

    def available_actions(self, activity: Activity) -> list[Action]:
        return []

    def execute_action(self, request: ActionRequest) -> ActionResult:
        return ActionResult(ok=False, detail="slack write-back is not implemented")
