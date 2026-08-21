"""Claude classification pass: selection, caching, retry, and mapping.

Cost control order (§57): deterministic filtering happened upstream; here we
select only text-bearing activities, check the local classification cache by
content hash, and call Claude once per batch for whatever is left. One retry
on failure; if the pass ultimately fails, the deterministic briefing ships
anyway and the failure is reported, never hidden.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.ai.client import AIClient, AIError
from app.ai.schemas import ClassifiedActivity
from app.models import Activity, AttentionCategory, AttentionItem, attention_key
from app.storage import LocalPaths, atomic_write_json, read_json

log = logging.getLogger("work-agent")

CACHE_FILE = "classifications.json"
CACHE_TTL_DAYS = 30
CACHE_MAX_ENTRIES = 5000
BATCH_SIZE = 20

# FYI and NOISE never become attention items.
ITEM_CATEGORIES = {
    "DIRECT_REQUEST", "APPROVAL_REQUIRED", "BLOCKER", "DECISION",
    "IMPORTANT_CHANGE", "POSSIBLE_WAITING", "POSSIBLE_COMMITMENT",
}


def content_hash(a: Activity) -> str:
    digest = hashlib.sha256("|".join([
        a.source.value, a.object_id, a.title or "",
        (a.text or "").strip(), a.actor_name or "",
    ]).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


class ClassificationCache:
    def __init__(self, paths: LocalPaths):
        self.path = paths.cache / CACHE_FILE
        data = read_json(self.path, {"version": 1, "entries": {}})
        self._entries: dict[str, dict] = data.get("entries", {})

    def get(self, key: str) -> dict | None:
        return self._entries.get(key)

    def put(self, key: str, record: dict, now: datetime) -> None:
        self._entries[key] = {**record, "classified_at": now.isoformat()}

    def prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=CACHE_TTL_DAYS)
        kept = {}
        for key, record in self._entries.items():
            try:
                at = datetime.fromisoformat(record["classified_at"])
            except (KeyError, ValueError):
                continue
            if at >= cutoff:
                kept[key] = record
        entries = sorted(kept.items(), key=lambda kv: kv[1]["classified_at"])
        self._entries = dict(entries[-CACHE_MAX_ENTRIES:])

    def save(self) -> None:
        atomic_write_json(self.path, {"version": 1, "entries": self._entries})


def select_for_ai(activities: list[Activity]) -> list[Activity]:
    """Only ambiguous, text-bearing activity goes to Claude."""
    return [a for a in activities
            if a.completed is not True and a.text and a.text.strip()]


@dataclass
class AIPassResult:
    items: list[AttentionItem] = field(default_factory=list)
    classified: int = 0
    from_cache: int = 0
    failed: bool = False


def run_ai_pass(activities: list[Activity], client: AIClient,
                cache: ClassificationCache, now: datetime) -> AIPassResult:
    result = AIPassResult()
    selected = select_for_ai(activities)
    by_id = {a.id: a for a in selected}

    uncached: list[Activity] = []
    records: dict[str, dict] = {}  # activity id -> classification record
    for a in selected:
        cached = cache.get(content_hash(a))
        if cached is not None:
            records[a.id] = cached
            result.from_cache += 1
        else:
            uncached.append(a)

    for start in range(0, len(uncached), BATCH_SIZE):
        batch = uncached[start:start + BATCH_SIZE]
        response = _classify_with_retry(client, batch)
        if response is None:
            result.failed = True
            break
        result.classified += len(batch)
        for classified in response.items:
            activity = by_id.get(classified.activity_id)
            if activity is None:
                log.warning("Claude returned unknown activity_id %r",
                            classified.activity_id)
                continue
            record = classified.model_dump()
            records[activity.id] = record
            cache.put(content_hash(activity), record, now)

    for activity_id, record in records.items():
        item = _to_item(by_id[activity_id], record, now)
        if item is not None:
            result.items.append(item)
    return result


def _classify_with_retry(client: AIClient, batch: list[Activity]):
    for attempt in (1, 2):
        try:
            return client.classify(batch)
        except AIError as exc:
            log.warning("classification attempt %d failed: %s", attempt, exc)
    return None


def _to_item(a: Activity, record: dict, now: datetime) -> AttentionItem | None:
    if not record.get("relevant") or record.get("category") not in ITEM_CATEGORIES:
        return None
    category = AttentionCategory(record["category"])
    return AttentionItem(
        key=attention_key(a.source, a.object_id, category),
        source=a.source,
        object_id=a.object_id,
        category=category,
        confidence=float(record.get("confidence", 0.5)),
        deterministic=False,
        title=a.title or (a.text[:80] if a.text else a.object_id),
        reason=record.get("reason", ""),
        summary=record.get("summary", ""),
        url=a.url,
        first_seen_at=now,
        last_seen_at=now,
        due_at=a.due_at,
        priority=a.priority,
    )
