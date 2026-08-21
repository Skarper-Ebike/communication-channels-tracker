"""Scoring. Deliberately plain — this table is meant to be edited."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import AttentionCategory, AttentionItem

BASE_SCORES: dict[AttentionCategory, int] = {
    AttentionCategory.OVERDUE: 90,
    AttentionCategory.BLOCKER: 75,
    AttentionCategory.DIRECT_REQUEST: 70,
    AttentionCategory.APPROVAL_REQUIRED: 70,
    AttentionCategory.NEW_ASSIGNMENT: 60,
    AttentionCategory.POSSIBLE_WAITING: 55,
    AttentionCategory.DIRECT_MENTION: 45,
    AttentionCategory.DECISION: 45,
    AttentionCategory.IMPORTANT_CHANGE: 45,
    AttentionCategory.POSSIBLE_COMMITMENT: 40,
    AttentionCategory.NEW_REPLY: 35,
    AttentionCategory.FYI: 15,
    AttentionCategory.NOISE: 0,
}

HIGH_PRIORITIES = {"critical", "highest", "high", "urgent", "blocker"}

LOW_CONFIDENCE_THRESHOLD = 0.6
LOW_CONFIDENCE_PENALTY = 20
REPEATED_REPORT_COUNT = 3
REPEATED_REPORT_PENALTY = 10


def score(item: AttentionItem, now: datetime) -> int:
    if item.category is AttentionCategory.DUE_SOON:
        value = 70 if _due_within(item, now, hours=24) else 45
    else:
        value = BASE_SCORES.get(item.category, 30)

    if item.priority and item.priority.lower() in HIGH_PRIORITIES:
        value += 10

    if item.confidence < LOW_CONFIDENCE_THRESHOLD:
        value -= LOW_CONFIDENCE_PENALTY

    if item.report_count >= REPEATED_REPORT_COUNT:
        value -= REPEATED_REPORT_PENALTY

    return max(0, min(100, value))


def _due_within(item: AttentionItem, now: datetime, hours: int) -> bool:
    due = item.due_at
    if due is None:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due < now + timedelta(hours=hours)
