"""Event deduplication without a database.

Stable event keys plus a bounded, pruned local set of recently seen keys in
.local/state/recent-event-ids.json. The set only needs to cover the overlap
window between polls, not history — the source app is the system of record.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from app.models import Activity
from app.storage import LocalPaths, atomic_write_json, read_json

RETENTION_DAYS = 14
FILE_NAME = "recent-event-ids.json"


def event_key(a: Activity) -> str:
    if a.id:
        return a.id
    digest = hashlib.sha256(
        "|".join([
            a.source.value,
            a.object_id,
            a.actor_id or a.actor_name or "",
            a.created_at.isoformat(),
            (a.text or "").strip().lower(),
        ]).encode("utf-8")
    ).hexdigest()
    return f"{a.source.value}:sha256:{digest}"


class RecentEvents:
    def __init__(self, paths: LocalPaths):
        self.path = paths.state / FILE_NAME
        data = read_json(self.path, {"version": 1, "events": {}})
        self._events: dict[str, str] = data.get("events", {})

    def filter_new(self, activities: list[Activity]) -> list[Activity]:
        fresh: list[Activity] = []
        seen_this_run: set[str] = set()
        for a in activities:
            key = event_key(a)
            if key in self._events or key in seen_this_run:
                continue
            seen_this_run.add(key)
            fresh.append(a)
        return fresh

    def mark_seen(self, activities: list[Activity], now: datetime) -> None:
        stamp = now.isoformat()
        for a in activities:
            self._events[event_key(a)] = stamp

    def prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=RETENTION_DAYS)
        kept = {}
        for key, stamp in self._events.items():
            try:
                seen_at = datetime.fromisoformat(stamp)
            except ValueError:
                continue
            if seen_at >= cutoff:
                kept[key] = stamp
        self._events = kept

    def save(self) -> None:
        atomic_write_json(self.path, {"version": 1, "events": self._events})
