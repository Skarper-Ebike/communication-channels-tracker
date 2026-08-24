"""Briefing renderers. Plain text now; Slack blocks come with Milestone 4.

The value is omission: NEEDS YOU is only high-score items, everything below
the morning threshold is just a count.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import AppConfig
from app.models import AttentionItem

NEEDS_YOU_THRESHOLD = 70

SOURCE_LABELS = {
    "slack": "Slack",
    "jira": "Jira",
    "google_drive": "Google Drive",
    "basecamp": "Basecamp",
    "confluence": "Confluence",
}


def render_morning(items: list[AttentionItem], reviewed_count: int,
                   config: AppConfig, now: datetime,
                   degraded: list[str] | None = None,
                   digest=None, digest_counts: dict[str, int] | None = None,
                   digest_hours: int = 24) -> str:
    ranked = sorted(items, key=lambda i: i.score, reverse=True)
    needs_you = [i for i in ranked if i.score >= NEEDS_YOU_THRESHOLD]
    worth_knowing = [i for i in ranked
                     if config.attention.morning_threshold <= i.score < NEEDS_YOU_THRESHOLD]
    below = len(ranked) - len(needs_you) - len(worth_knowing)

    lines = [f"☀️ YOUR MORNING BRIEFING — {now.strftime('%A %-d %B, %H:%M')}", ""]

    lines.append("🔴 NEEDS YOU")
    lines.append("")
    if needs_you:
        for n, item in enumerate(needs_you, 1):
            lines.append(f"{n}. {item.title}")
            lines.append(f"   {_source_line(item, now)}")
            lines.append(f"   {item.reason}")
            lines.append(f"   → {item.url}")
            lines.append("")
    else:
        lines.append("Nothing needs you right now.")
        lines.append("")

    if worth_knowing:
        lines.append("🟠 WORTH KNOWING")
        lines.append("")
        for item in worth_knowing:
            lines.append(f"• {item.title} — {item.reason}")
            lines.append(f"  → {item.url}")
        lines.append("")

    lines.extend(_digest_lines(digest, digest_counts, digest_hours))

    outstanding = _outstanding_lines(ranked, now)
    if outstanding:
        lines.append("📌 OUTSTANDING")
        lines.append("")
        lines.extend(f"• {line}" for line in outstanding)
        lines.append("")

    for source in degraded or []:
        lines.append(f"⚠️ {SOURCE_LABELS.get(source, source)} could not be checked. "
                     f"Run: uv run python -m app.cli doctor")
    if degraded:
        lines.append("")

    total_other = reviewed_count + below
    noun = "activity" if total_other == 1 else "activities"
    lines.append(f"Reviewed {total_other} other {noun}.")
    return "\n".join(lines)


def render_urgent(items: list[AttentionItem], config: AppConfig,
                  now: datetime) -> str:
    urgent = [i for i in sorted(items, key=lambda i: i.score, reverse=True)
              if i.score >= config.attention.urgent_threshold]
    if not urgent:
        return "Nothing urgent right now."
    lines = ["🚨 URGENT", ""]
    for item in urgent:
        lines.append(f"• {item.title} — {item.reason}")
        lines.append(f"  {_source_line(item, now)}")
        lines.append(f"  → {item.url}")
    return "\n".join(lines)


def render_weekly(items: list[AttentionItem], now: datetime) -> str:
    ranked = sorted(items, key=lambda i: i.score, reverse=True)
    hanging = [i for i in ranked
               if i.report_count >= 3 or (now - i.first_seen_at) > timedelta(days=5)]
    due_next_week = [i for i in ranked if _due_within(i, now, days=7)]

    lines = ["📌 THINGS THAT KEEP HANGING AROUND", ""]
    if hanging:
        for item in hanging:
            age_days = (now - item.first_seen_at).days
            note = (f"surfaced in {item.report_count} briefings"
                    if item.report_count >= 3 else f"open for {age_days} days")
            lines.append(f"• {item.title} — {note}")
            lines.append(f"  → {item.url}")
    else:
        lines.append("Nothing has been hanging around. Good week.")
    lines.append("")
    lines.append("COMING NEXT WEEK")
    lines.append("")
    if due_next_week:
        lines.append(f"• {len(due_next_week)} assigned item"
                     f"{'s' if len(due_next_week) != 1 else ''} due")
        for item in due_next_week:
            lines.append(f"  – {item.title}")
    else:
        lines.append("• Nothing with a due date next week.")
    return "\n".join(lines)


def _digest_lines(digest, counts: dict[str, int] | None,
                  hours: int) -> list[str]:
    if digest is None and not counts:
        return []
    lines = [f"🧭 TEAM DIGEST — last {hours}h", ""]
    if digest is not None:
        lines.append(digest.overview)
        for header, entries in (("TEAM", getattr(digest, "team", [])),
                                ("DECISIONS", digest.decisions),
                                ("ISSUES", digest.issues),
                                ("NOTES", digest.notes)):
            if entries:
                lines.append("")
                lines.append(header)
                lines.extend(f"• {entry}" for entry in entries)
    else:
        total = sum(counts.values())
        sources = ", ".join(
            f"{n} {SOURCE_LABELS.get(source, source)}"
            for source, n in sorted(counts.items()))
        lines.append(f"{total} team activities ({sources}) — "
                     f"AI summary unavailable.")
    lines.append("")
    return lines


def _source_line(item: AttentionItem, now: datetime) -> str:
    parts = [SOURCE_LABELS.get(item.source.value, item.source.value)]
    due = _aware(item.due_at)
    if due is not None:
        delta = now - due
        if delta > timedelta(0):
            days = max(1, delta.days)
            parts.append(f"{days} day{'s' if days != 1 else ''} overdue")
        else:
            hours = int((-delta).total_seconds() // 3600)
            parts.append(f"due in {hours}h" if hours < 48 else f"due in {hours // 24} days")
    if item.priority:
        parts.append(f"{item.priority} priority")
    return " · ".join(parts)


def _outstanding_lines(items: list[AttentionItem], now: datetime) -> list[str]:
    overdue = sum(1 for i in items if i.category.value == "OVERDUE")
    due_week = sum(1 for i in items
                   if i.category.value != "OVERDUE" and _due_within(i, now, days=7))
    long_runners = sum(1 for i in items if i.report_count >= 3)

    lines = []
    if overdue:
        lines.append(f"{overdue} assigned task{'s' if overdue != 1 else ''} overdue")
    if due_week:
        lines.append(f"{due_week} item{'s' if due_week != 1 else ''} due in the next 7 days")
    if long_runners:
        lines.append(f"{long_runners} item{'s have' if long_runners != 1 else ' has'} "
                     f"appeared in 3+ briefings")
    return lines


def _due_within(item: AttentionItem, now: datetime, days: int) -> bool:
    due = _aware(item.due_at)
    return due is not None and now <= due < now + timedelta(days=days)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
