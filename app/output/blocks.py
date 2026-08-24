"""Slack Block Kit versions of the briefings.

The plain-text renderer stays the terminal/fallback format; these builders
produce the rich Slack layout: header, bold linked titles instead of raw
URLs, per-category emoji, auto-bolded key phrases (deadlines, actors),
dividers between zones, and a fields grid — Slack's reliable table-like
layout — for the outstanding counts. (Slack's true `table` block isn't
dependably supported via incoming webhooks, and a rejected block kills the
whole message, so fields it is.) Limits: ~50 blocks/message, 3000
chars/section — everything here caps well below.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.config import AppConfig
from app.models import AttentionCategory, AttentionItem
from app.output.renderer import (
    NEEDS_YOU_THRESHOLD,
    SOURCE_LABELS,
    _due_within,
    _source_line,
)

MAX_NEEDS_YOU = 10
MAX_WORTH_KNOWING = 15
MAX_DIGEST_BULLETS = 8
MAX_TEAM_MEMBERS = 12
SECTION_LIMIT = 2900

CATEGORY_EMOJI = {
    AttentionCategory.OVERDUE: "⏰",
    AttentionCategory.DUE_SOON: "📅",
    AttentionCategory.NEW_ASSIGNMENT: "🆕",
    AttentionCategory.DIRECT_MENTION: "💬",
    AttentionCategory.NEW_REPLY: "↩️",
    AttentionCategory.DIRECT_REQUEST: "🙋",
    AttentionCategory.APPROVAL_REQUIRED: "✍️",
    AttentionCategory.BLOCKER: "🧱",
    AttentionCategory.DECISION: "📣",
    AttentionCategory.IMPORTANT_CHANGE: "📣",
    AttentionCategory.POSSIBLE_WAITING: "⏳",
    AttentionCategory.POSSIBLE_COMMITMENT: "🤝",
}

# Deterministic reason/source text gets its key phrase bolded automatically.
_EMPHASIS_PATTERNS = [
    re.compile(r"(\d+ days? overdue)"),
    re.compile(r"(due (?:in \d+ ?(?:h|hours?|days?)|today|tomorrow))",
               re.IGNORECASE),
    re.compile(r"^([A-Z][\w .'’-]{0,30}?)"
               r"(?= (?:mentioned|replied|appears|asks|asked|needs))"),
    re.compile(r"(newly assigned to you)", re.IGNORECASE),
]


def morning_blocks(items: list[AttentionItem], reviewed_count: int,
                   config: AppConfig, now: datetime,
                   degraded: list[str] | None = None,
                   digest=None, digest_counts: dict[str, int] | None = None,
                   digest_hours: int = 24) -> list[dict]:
    ranked = sorted(items, key=lambda i: i.score, reverse=True)
    needs_you = [i for i in ranked if i.score >= NEEDS_YOU_THRESHOLD]
    worth_knowing = [i for i in ranked
                     if config.attention.morning_threshold <= i.score
                     < NEEDS_YOU_THRESHOLD]
    below = len(ranked) - len(needs_you) - len(worth_knowing)

    blocks = [_header(f"☀️ Morning briefing — {now.strftime('%A %-d %B')}")]

    if needs_you:
        blocks.append(_section("*🔴 Needs you*"))
        for n, item in enumerate(needs_you[:MAX_NEEDS_YOU], 1):
            emoji = CATEGORY_EMOJI.get(item.category, "🔔")
            blocks.append(_section(
                f"*{n}. {emoji} {_link(item.url, item.title)}*\n"
                f"{_emphasise(_esc(_source_line(item, now)))}\n"
                f"_{_emphasise(_esc(item.reason))}_"))
        if len(needs_you) > MAX_NEEDS_YOU:
            blocks.append(_context(
                f"…and {len(needs_you) - MAX_NEEDS_YOU} more above the "
                f"threshold."))
    else:
        blocks.append(_section("*🔴 Needs you*\nNothing needs you right now."))

    if worth_knowing:
        blocks.append(_divider())
        lines = []
        for item in worth_knowing[:MAX_WORTH_KNOWING]:
            emoji = CATEGORY_EMOJI.get(item.category, "•")
            lines.append(f"{emoji} {_link(item.url, item.title)} — "
                         f"{_emphasise(_esc(item.reason))}")
        blocks.append(_section("*🟠 Worth knowing*\n" + "\n".join(lines)))

    blocks.extend(_digest_blocks(digest, digest_counts, digest_hours))

    blocks.append(_divider())
    stats = _outstanding_stats(ranked, now)
    stats.append(("👀 Reviewed", f"{reviewed_count + below} other "
                  f"{'activity' if reviewed_count + below == 1 else 'activities'}"))
    blocks.append(_fields("*📌 At a glance*", stats))

    for source in degraded or []:
        blocks.append(_context(
            f"⚠️ {SOURCE_LABELS.get(source, source)} could not be checked — "
            f"run `uv run python -m app.cli doctor`"))
    return blocks


def urgent_blocks(items: list[AttentionItem], config: AppConfig,
                  now: datetime) -> list[dict]:
    urgent = [i for i in sorted(items, key=lambda i: i.score, reverse=True)
              if i.score >= config.attention.urgent_threshold]
    blocks = [_header("🚨 Urgent")]
    for item in urgent[:MAX_NEEDS_YOU]:
        emoji = CATEGORY_EMOJI.get(item.category, "🔔")
        blocks.append(_section(
            f"*{emoji} {_link(item.url, item.title)}*\n"
            f"{_emphasise(_esc(_source_line(item, now)))}\n"
            f"_{_emphasise(_esc(item.reason))}_"))
    return blocks


def weekly_blocks(items: list[AttentionItem], now: datetime,
                  text_fallback: str) -> list[dict]:
    # The weekly is prose-shaped; reuse the plain rendering inside one
    # header + section rather than duplicating its logic.
    body = text_fallback.split("\n", 1)[1].strip() if "\n" in text_fallback \
        else text_fallback
    return [_header(f"📌 Weekly review — {now.strftime('%-d %B')}"),
            _section(_esc(body))]


def _digest_blocks(digest, counts: dict[str, int] | None,
                   hours: int) -> list[dict]:
    if digest is None and not counts:
        return []
    blocks = [_divider()]
    if digest is not None:
        blocks.append(_section(f"*🧭 Team digest — last {hours}h*\n"
                               f"{_esc(digest.overview)}"))
        team = getattr(digest, "team", [])
        if team:
            bullets = "\n".join(f"👤 {_esc(entry)}"
                                for entry in team[:MAX_TEAM_MEMBERS])
            blocks.append(_section(f"*👥 Team — who's on what*\n{bullets}"))
        for marker, label, entries in (("✅", "Decisions", digest.decisions),
                                       ("⚠️", "Issues", digest.issues),
                                       ("📝", "Notes", digest.notes)):
            if entries:
                bullets = "\n".join(f"{marker} {_esc(e)}"
                                    for e in entries[:MAX_DIGEST_BULLETS])
                blocks.append(_section(f"*{label}*\n{bullets}"))
    else:
        blocks.append(_fields(
            f"*🧭 Team digest — last {hours}h* (AI summary unavailable)",
            [(SOURCE_LABELS.get(s, s), f"{n} activities")
             for s, n in sorted(counts.items())]))
    return blocks


def _outstanding_stats(items: list[AttentionItem],
                       now: datetime) -> list[tuple[str, str]]:
    overdue = sum(1 for i in items
                  if i.category is AttentionCategory.OVERDUE)
    due_week = sum(1 for i in items
                   if i.category is not AttentionCategory.OVERDUE
                   and _due_within(i, now, days=7))
    long_runners = sum(1 for i in items if i.report_count >= 3)

    stats = []
    if overdue:
        stats.append(("⏰ Overdue", f"*{overdue}* assigned "
                      f"task{'s' if overdue != 1 else ''}"))
    if due_week:
        stats.append(("📅 Due in 7 days",
                      f"*{due_week}* item{'s' if due_week != 1 else ''}"))
    if long_runners:
        stats.append(("🔁 Long-running", f"*{long_runners}* seen in 3+ "
                      f"briefings"))
    return stats


# -- primitives ------------------------------------------------------------

def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;") \
                       .replace(">", "&gt;").replace("**", "*")


def _emphasise(text: str) -> str:
    """Bold the key phrase of deterministic text (deadlines, actors).

    AI-generated text arrives pre-bolded (the prompts ask for it), so
    anything already carrying asterisks is left alone.
    """
    if "*" in text:
        return text
    for pattern in _EMPHASIS_PATTERNS:
        emphasised, hits = pattern.subn(r"*\1*", text, count=1)
        if hits:
            return emphasised
    return text


def _link(url: str, label: str) -> str:
    if not url:
        return _esc(label)
    return f"<{url}|{_esc(label)}>"


def _header(text: str) -> dict:
    return {"type": "header",
            "text": {"type": "plain_text", "text": text[:150], "emoji": True}}


def _section(md: str) -> dict:
    if len(md) > SECTION_LIMIT:
        md = md[:SECTION_LIMIT] + "…"
    return {"type": "section", "text": {"type": "mrkdwn", "text": md}}


def _context(md: str) -> dict:
    return {"type": "context",
            "elements": [{"type": "mrkdwn", "text": md[:SECTION_LIMIT]}]}


def _fields(title: str, rows: list[tuple[str, str]]) -> dict:
    """Two-column table-like section: label / value pairs (max 10 fields)."""
    fields = []
    for label, value in rows[:5]:
        fields.append({"type": "mrkdwn", "text": f"*{label}*\n{value}"})
    block: dict = {"type": "section",
                   "text": {"type": "mrkdwn", "text": title}}
    if fields:
        block["fields"] = fields
    return block


def _divider() -> dict:
    return {"type": "divider"}
