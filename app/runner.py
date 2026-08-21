"""Single-run pipeline.

load config → acquire lock → load states → poll → normalise → rules →
merge attention → rank → render → (print) → write state atomically → exit.

Cursor rule: connector state, dedupe state and attention files are written
only after the whole run has processed successfully, and never on --dry-run.
"""

from __future__ import annotations

import logging
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


def run(command: str, *, mock: bool = False, dry_run: bool = False,
        paths: LocalPaths | None = None, now: datetime | None = None) -> RunResult:
    paths = paths or LocalPaths()
    paths.ensure()
    config = load_config(paths)
    now = now or config.now()

    with RunLock(paths.lock_file):
        return _run_locked(command, config, paths, now,
                           mock=mock, dry_run=dry_run)


def _run_locked(command: str, config: AppConfig, paths: LocalPaths,
                now: datetime, *, mock: bool, dry_run: bool) -> RunResult:
    started = time.monotonic()
    log.info("run started: command=%s mock=%s dry_run=%s", command, mock, dry_run)

    if mock:
        connectors = [MockConnector(now=now)]
    else:
        # Live connectors arrive in Milestones 5+.
        connectors = []

    activities = []
    new_states: dict[str, dict] = {}
    degraded: list[str] = []
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

    if not dry_run:
        store.mark_reported(reported, now)
        store.save()
        recent.mark_seen(fresh, now)
        recent.prune(now)
        recent.save()
        for name, state in new_states.items():
            atomic_write_json(paths.state / f"{name}.json", state)

    log.info("run finished in %.2fs: %d items, %d degraded",
             time.monotonic() - started, len(items), len(degraded))
    return RunResult(text=text, item_count=len(items),
                     reviewed_count=reviewed, degraded=degraded)
