"""Single-run pipeline.

load config → acquire lock → load states → poll → normalise → rules →
merge attention → rank → render → (print) → write state atomically → exit.

Cursor rule: connector state, dedupe state and attention files are written
only after the whole run has processed successfully, and never on --dry-run.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

from app.attention import dedupe, ranking, rules
from app.attention.lifecycle import AttentionStore
from app.config import AppConfig, load_config
from app.connectors.mock import MockConnector
from app.lock import RunLock
from app.output import renderer
from app.storage import LocalPaths, atomic_write_json, read_json

log = logging.getLogger("work-agent")


@dataclass
class RunResult:
    text: str
    item_count: int = 0
    reviewed_count: int = 0
    degraded: list[str] = field(default_factory=list)
    # None: sending not attempted (dry run, no webhook, nothing urgent).
    delivered: bool | None = None


def run(command: str, *, mock: bool = False, dry_run: bool = False,
        no_ai: bool = False, paths: LocalPaths | None = None,
        now: datetime | None = None) -> RunResult:
    paths = paths or LocalPaths()
    paths.ensure()
    config = load_config(paths)
    if mock:
        # Demo/test runs live in their own state subtree and never deliver,
        # so fixtures can never pollute real attention state or Slack.
        paths = LocalPaths(root=paths.root / "mock")
        paths.ensure()
    now = now or config.now()

    with RunLock(paths.lock_file):
        return _run_locked(command, config, paths, now,
                           mock=mock, dry_run=dry_run, no_ai=no_ai)


def _run_locked(command: str, config: AppConfig, paths: LocalPaths,
                now: datetime, *, mock: bool, dry_run: bool,
                no_ai: bool) -> RunResult:
    started = time.monotonic()
    log.info("run started: command=%s mock=%s dry_run=%s", command, mock, dry_run)

    activities = []
    new_states: dict[str, dict] = {}
    degraded: list[str] = []

    if mock:
        connectors = [MockConnector(now=now)]
    else:
        connectors = _live_connectors(config, degraded, now)
    for connector in connectors:
        state_path = paths.state / f"{connector.name}.json"
        state = read_json(state_path, {"version": 1})
        try:
            result = connector.fetch_changes(state)
        except Exception:
            # One broken source must not break the briefing — and its cursor
            # must not advance.
            log.exception("connector %s failed", connector.name)
            degraded.append(connector.name)
            continue
        activities.extend(result.activities)
        new_states[connector.name] = result.state
        log.info("connector %s: %d activities", connector.name, len(result.activities))

    recent = dedupe.RecentEvents(paths)
    fresh = recent.filter_new(activities)

    candidates = rules.evaluate(fresh, now)

    ai_cache = None
    if not no_ai and config.ai.enabled and _ai_available():
        from app.ai import analyse
        from app.ai.client import AnthropicAIClient

        ai_cache = analyse.ClassificationCache(paths)
        try:
            ai_client = AnthropicAIClient(model=config.ai.model)
        except Exception:
            log.exception("could not construct Anthropic client")
            ai_client = None
        if ai_client is not None:
            ai_result = analyse.run_ai_pass(fresh, ai_client, ai_cache, now)
            if ai_result.failed:
                degraded.append("claude")
            # Deterministic candidates win on key collisions.
            existing_keys = {c.key for c in candidates}
            candidates.extend(i for i in ai_result.items
                              if i.key not in existing_keys)
            ai_cache.prune(now)
            log.info("AI pass: %d classified, %d from cache, %d items",
                     ai_result.classified, ai_result.from_cache,
                     len(ai_result.items))

    store = AttentionStore(paths)
    store.merge(candidates, activities, now)

    items = store.reportable(now)
    for item in items:
        item.score = ranking.score(item, now)

    reviewed = len(fresh) - len({c.object_id for c in candidates})
    reviewed = max(0, reviewed)

    if command == "urgent":
        text = renderer.render_urgent(items, config, now)
        reported = [i.key for i in items
                    if i.score >= config.attention.urgent_threshold]
    elif command == "weekly":
        text = renderer.render_weekly(items, now)
        reported = [i.key for i in items]
    elif command == "sync":
        text = (f"Synced {len(connectors) - len(degraded)}/{len(connectors)} "
                f"sources: {len(activities)} activities, {len(fresh)} new, "
                f"{len(items)} open attention items.")
        reported = []
    else:  # morning briefing
        threshold = config.attention.morning_threshold
        text = renderer.render_morning(
            [i for i in items if i.score >= threshold],
            reviewed_count=reviewed + sum(1 for i in items if i.score < threshold),
            config=config, now=now, degraded=degraded,
        )
        reported = [i.key for i in items if i.score >= threshold]

    delivered = None
    if not dry_run and not mock and command in ("briefing", "urgent", "weekly"):
        # Urgent stays quiet unless something actually crossed the threshold.
        if command != "urgent" or reported:
            delivered = _deliver(text, config)

    if not dry_run:
        store.mark_reported(reported, now)
        store.save()
        recent.mark_seen(fresh, now)
        recent.prune(now)
        recent.save()
        if ai_cache is not None:
            ai_cache.save()
        for name, state in new_states.items():
            atomic_write_json(paths.state / f"{name}.json", state)

    log.info("run finished in %.2fs: %d items, %d degraded",
             time.monotonic() - started, len(items), len(degraded))
    return RunResult(text=text, item_count=len(items),
                     reviewed_count=reviewed, degraded=degraded,
                     delivered=delivered)


def _deliver(text: str, config: AppConfig) -> bool | None:
    from app.output import slack

    if slack.webhook_url(config) is None:
        return None
    try:
        slack.post_message(text, config)
    except slack.SlackDeliveryError as exc:
        log.error("%s", exc)
        return False
    return True


def _ai_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _live_connectors(config: AppConfig, degraded: list[str],
                     now: datetime) -> list:
    connectors = []
    if config.connectors.jira.enabled:
        from app.connectors.jira import JiraConnector

        email = os.environ.get("JIRA_EMAIL")
        token = os.environ.get("JIRA_API_TOKEN")
        if email and token and config.connectors.jira.base_url:
            connectors.append(
                JiraConnector(config.connectors.jira, email, token, now=now))
        else:
            log.error("jira enabled but JIRA_EMAIL/JIRA_API_TOKEN/base_url "
                      "incomplete")
            degraded.append("jira")

    if config.connectors.slack.enabled:
        from app.connectors.slack import SlackConnector

        token = os.environ.get("SLACK_USER_TOKEN")
        if token and config.connectors.slack.channels:
            connectors.append(SlackConnector(
                config.connectors.slack, token,
                output_channel_id=config.slack_output.channel_id, now=now))
        else:
            log.error("slack input enabled but SLACK_USER_TOKEN/channels "
                      "incomplete")
            degraded.append("slack")

    if config.connectors.google_drive.enabled:
        from app.connectors.google_drive import GoogleDriveConnector

        connectors.append(GoogleDriveConnector(
            config.connectors.google_drive, now=now))

    if config.connectors.basecamp.enabled:
        from app.connectors.basecamp import BasecampConnector

        connectors.append(BasecampConnector(
            config.connectors.basecamp, now=now))
    return connectors


def any_live_connectors(config: AppConfig) -> bool:
    c = config.connectors
    return (c.jira.enabled or c.slack.enabled or c.google_drive.enabled
            or c.basecamp.enabled)
