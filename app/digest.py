"""Rolling buffer of the last day's activity for the team digest.

The hourly urgent scans consume events as they arrive, so the morning
briefing can't see "the whole day" from its own poll. Every run appends
compact records (truncated text, ids, actors — never full payloads) here;
the morning briefing summarises and drains it. Entries are pruned at 48h
and hard-capped, so this stays a small file, not an archive — the source
systems remain the record.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models import Activity
from app.storage import LocalPaths, atomic_write_json, read_json

FILE_NAME = "digest-buffer.json"
RETENTION_HOURS = 48
MAX_ENTRIES = 1000
TEXT_LIMIT = 300


def record_from_activity(a: Activity) -> dict:
    return {
        "id": a.id,
        "source": a.source.value,
        "object_id": a.object_id,
        "event_type": a.event_type,
        "actor": a.actor_name,
        "title": (a.title or "")[:120],
        "text": (a.text or "")[:TEXT_LIMIT],
        "ts": a.created_at.isoformat(),
    }


class DigestBuffer:
    def __init__(self, paths: LocalPaths):
        self.path = paths.state / FILE_NAME
        data = read_json(self.path, {"version": 1, "entries": []})
        self._entries: list[dict] = data.get("entries", [])

    def append(self, activities: list[Activity], now: datetime) -> None:
        seen = {e["id"] for e in self._entries}
        for a in activities:
            if a.id in seen:
                continue
            seen.add(a.id)
            self._entries.append(record_from_activity(a))
        self._prune(now)

    def entries_since(self, now: datetime, hours: int = 24,
                      limit: int = 150) -> list[dict]:
        floor = (now - timedelta(hours=hours)).isoformat()
        recent = [e for e in self._entries if e.get("ts", "") >= floor]
        recent.sort(key=lambda e: e.get("ts", ""))
        return recent[-limit:]

    def counts_by_source(self, now: datetime, hours: int = 24) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries_since(now, hours, limit=MAX_ENTRIES):
            counts[entry["source"]] = counts.get(entry["source"], 0) + 1
        return counts

    def drain(self) -> None:
        """Called after a morning briefing consumed the buffer."""
        self._entries = []

    def save(self) -> None:
        atomic_write_json(self.path,
                          {"version": 1, "entries": self._entries})

    def _prune(self, now: datetime) -> None:
        floor = (now - timedelta(hours=RETENTION_HOURS)).isoformat()
        self._entries = [e for e in self._entries
                         if e.get("ts", "") >= floor][-MAX_ENTRIES:]
