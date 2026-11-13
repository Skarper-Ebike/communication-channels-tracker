"""Deterministic attention rules.

Anything the source API already states as fact is decided here in code —
Claude is never asked to rediscover due dates, assignees, or mentions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import Activity, AttentionCategory, AttentionItem, attention_key

DUE_SOON_WINDOW = timedelta(hours=72)

DATE_CATEGORIES = frozenset({AttentionCategory.OVERDUE, AttentionCategory.DUE_SOON})


def evaluate(activities: list[Activity], now: datetime) -> list[AttentionItem]:
    """One AttentionItem per (source, object, category), deterministic only."""
    items: dict[str, AttentionItem] = {}
    for activity in activities:
        for item in _candidates(activity, now):
            items.setdefault(item.key, item)
    return list(items.values())


def evaluate_dates(activities: list[Activity], now: datetime) -> list[AttentionItem]:
    """OVERDUE / DUE_SOON for every snapshot activity, already seen or not.

    Dedupe keeps an unchanged object's activity out of `evaluate`, but a due
    date passes without any new event. A snapshot states the current due date,
    so its date category is decided afresh on every run.
    """
    items: dict[str, AttentionItem] = {}
    for a in activities:
        if not a.snapshot:
            continue
        category = date_category(a, now)
        if category is not None:
            item = _item(a, category, now, reason=date_reason(a.due_at, now))
            items.setdefault(item.key, item)
    return list(items.values())


def date_category(a: Activity, now: datetime) -> AttentionCategory | None:
    if a.completed is True or not a.assigned_to_user:
        return None
    due = _aware(a.due_at)
    if due is None:
        return None
    if due < now:
        return AttentionCategory.OVERDUE
    if due < now + DUE_SOON_WINDOW:
        return AttentionCategory.DUE_SOON
    return None


def date_reason(due: datetime, now: datetime) -> str:
    due = _aware(due)
    if due < now:
        days = max(1, (now - due).days)
        return f"Assigned to you and {days} day{'s' if days != 1 else ''} overdue."
    hours = int((due - now).total_seconds() // 3600)
    when = f"in {hours} hours" if hours < 48 else f"in {hours // 24} days"
    return f"Assigned to you and due {when}."


def _candidates(a: Activity, now: datetime):
    if a.completed is True:
        return

    category = date_category(a, now)
    if category is not None:
        yield _item(a, category, now, reason=date_reason(a.due_at, now))

    if a.mentions_user:
        actor = a.actor_name or "Someone"
        yield _item(a, AttentionCategory.DIRECT_MENTION, now,
                    reason=f"{actor} mentioned you directly.")

    if a.event_type == "assigned" and a.assigned_to_user:
        actor = a.actor_name or "Someone"
        yield _item(a, AttentionCategory.NEW_ASSIGNMENT, now,
                    reason=f"Newly assigned to you by {actor}.")

    if a.event_type == "reply" and a.directed_at_user:
        actor = a.actor_name or "Someone"
        yield _item(a, AttentionCategory.NEW_REPLY, now,
                    reason=f"{actor} replied in a thread you are part of.")


def _item(a: Activity, category: AttentionCategory, now: datetime,
          reason: str) -> AttentionItem:
    title = a.title or (a.text[:80] if a.text else a.object_id)
    return AttentionItem(
        key=attention_key(a.source, a.object_id, category),
        source=a.source,
        object_id=a.object_id,
        category=category,
        title=title,
        reason=reason,
        summary=(a.text or "")[:200],
        url=a.url,
        first_seen_at=now,
        last_seen_at=now,
        due_at=a.due_at,
        priority=a.priority,
    )


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
