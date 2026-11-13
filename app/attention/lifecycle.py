"""Attention lifecycle over plain JSON files.

open.json / snoozed.json / resolved.json under .local/attention/. Items stay
OPEN across runs until the source proves them complete; resolved history is
pruned after RESOLVED_RETENTION_DAYS because the source systems already hold
the authoritative history.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.attention import rules
from app.models import Activity, AttentionItem, AttentionStatus, Source
from app.storage import LocalPaths, atomic_write_json, read_json

RESOLVED_RETENTION_DAYS = 30


class AttentionStore:
    def __init__(self, paths: LocalPaths):
        self.paths = paths
        self.open: dict[str, AttentionItem] = self._load("open.json")
        self.snoozed: dict[str, AttentionItem] = self._load("snoozed.json")
        self.resolved: dict[str, AttentionItem] = self._load("resolved.json")

    def _load(self, name: str) -> dict[str, AttentionItem]:
        data = read_json(self.paths.attention / name, {"version": 1, "items": {}})
        items = {}
        for key, raw in data.get("items", {}).items():
            items[key] = AttentionItem.model_validate(raw)
        return items

    def merge(self, candidates: list[AttentionItem],
              activities: list[Activity], now: datetime) -> None:
        self._wake_expired_snoozes(now)

        for candidate in candidates:
            existing = self.open.get(candidate.key) or self.snoozed.get(candidate.key)
            if existing is not None:
                existing.last_seen_at = now
                existing.reason = candidate.reason
                existing.title = candidate.title
                existing.summary = candidate.summary
                existing.url = candidate.url
                existing.due_at = candidate.due_at
                existing.priority = candidate.priority
            else:
                # A key that was previously resolved and fires again is a
                # genuinely new occurrence — fresh item, fresh first_seen.
                self.open[candidate.key] = candidate

        self._track_dates(activities, now)
        self._resolve_completed(activities, now)
        self._prune_resolved(now)

    def date_object_ids(self, source: Source) -> list[str]:
        """Objects behind open or snoozed date items, for the connector to
        re-read each run: their due date can move without a new event."""
        return sorted({
            item.object_id
            for pool in (self.open, self.snoozed)
            for item in pool.values()
            if item.source is source and item.category in rules.DATE_CATEGORIES
        })

    def reportable(self, now: datetime) -> list[AttentionItem]:
        return [i for i in self.open.values() if i.status is AttentionStatus.OPEN]

    def mark_reported(self, keys: list[str], now: datetime) -> None:
        for key in keys:
            item = self.open.get(key)
            if item is not None:
                item.report_count += 1
                item.last_reported_at = now

    def snooze(self, key: str, until: datetime) -> bool:
        item = self.open.pop(key, None)
        if item is None:
            return False
        item.status = AttentionStatus.SNOOZED
        item.snoozed_until = until
        self.snoozed[key] = item
        return True

    def dismiss(self, key: str, now: datetime) -> bool:
        item = self.open.pop(key, None) or self.snoozed.pop(key, None)
        if item is None:
            return False
        item.status = AttentionStatus.DISMISSED
        item.resolved_at = now
        self.resolved[key] = item
        return True

    def save(self) -> None:
        self._write("open.json", self.open)
        self._write("snoozed.json", self.snoozed)
        self._write("resolved.json", self.resolved)

    def _write(self, name: str, items: dict[str, AttentionItem]) -> None:
        atomic_write_json(
            self.paths.attention / name,
            {"version": 1,
             "items": {k: v.model_dump(mode="json") for k, v in items.items()}},
        )

    def _wake_expired_snoozes(self, now: datetime) -> None:
        for key in list(self.snoozed):
            item = self.snoozed[key]
            if item.snoozed_until is None or item.snoozed_until <= now:
                item.status = AttentionStatus.OPEN
                item.snoozed_until = None
                self.open[key] = self.snoozed.pop(key)

    def _track_dates(self, activities: list[Activity], now: datetime) -> None:
        """Keep OVERDUE / DUE_SOON items true to the source's current state.

        A snapshot activity states the object's assignee and due date as they
        are now, so a date item whose object no longer yields its category
        (due date moved, unassigned, left the window) is resolved, and the
        rest take the snapshot's due date and title. Every remaining date item
        then gets its reason recomputed, so it says what the meta line says.
        """
        current: dict[tuple[Source, str], Activity] = {}
        for a in activities:
            if a.snapshot:
                current[(a.source, a.object_id)] = a
        for pool in (self.open, self.snoozed):
            for key in list(pool):
                item = pool[key]
                if item.category not in rules.DATE_CATEGORIES:
                    continue
                snap = current.get((item.source, item.object_id))
                if snap is not None:
                    if rules.date_category(snap, now) is not item.category:
                        item.status = AttentionStatus.RESOLVED
                        item.resolved_at = now
                        self.resolved[key] = pool.pop(key)
                        continue
                    item.due_at = snap.due_at
                    if snap.title:
                        item.title = snap.title
                if item.due_at is not None:
                    item.reason = rules.date_reason(item.due_at, now)

    def _resolve_completed(self, activities: list[Activity], now: datetime) -> None:
        completed = {(a.source, a.object_id) for a in activities if a.completed is True}
        if not completed:
            return
        for pool in (self.open, self.snoozed):
            for key in list(pool):
                item = pool[key]
                if (item.source, item.object_id) in completed:
                    item.status = AttentionStatus.RESOLVED
                    item.resolved_at = now
                    self.resolved[key] = pool.pop(key)

    def _prune_resolved(self, now: datetime) -> None:
        cutoff = now - timedelta(days=RESOLVED_RETENTION_DAYS)
        for key in list(self.resolved):
            resolved_at = self.resolved[key].resolved_at
            if resolved_at is not None and resolved_at < cutoff:
                del self.resolved[key]
