"""Slack Block Kit versions of the briefings.

The plain-text renderer stays the terminal/fallback format; these builders
produce the rich Slack layout: header, bold linked titles instead of raw
URLs, dividers between zones, and a quiet context footer. Slack limits:
~50 blocks/message, 3000 chars/section — everything here caps well below.
"""

from __future__ import annotations

from datetime import datetime

from app.config import AppConfig
from app.models import AttentionItem
from app.output.renderer import (
    NEEDS_YOU_THRESHOLD,
    SOURCE_LABELS,
    _outstanding_lines,
    _source_line,
)

MAX_NEEDS_YOU = 10
MAX_WORTH_KNOWING = 15
MAX_DIGEST_BULLETS = 8
SECTION_LIMIT = 2900


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
            blocks.append(_section(
                f"*{n}. {_link(item.url, item.title)}*\n"
                f"{_esc(_source_line(item, now))}\n"
                f"_{_esc(item.reason)}_"))
        if len(needs_you) > MAX_NEEDS_YOU:
            blocks.append(_context(
                f"…and {len(needs_you) - MAX_NEEDS_YOU} more above the "
                f"threshold."))
    else:
        blocks.append(_section("*🔴 Needs you*\nNothing needs you right now."))

    if worth_knowing:
        blocks.append(_divider())
        lines = [f"• {_link(i.url, i.title)} — {_esc(i.reason)}"
                 for i in worth_knowing[:MAX_WORTH_KNOWING]]
        blocks.append(_section("*🟠 Worth knowing*\n" + "\n".join(lines)))

    blocks.extend(_digest_blocks(digest, digest_counts, digest_hours))

    footer = _outstanding_lines(ranked, now)
    footer.append(f"Reviewed {reviewed_count + below} other "
                  f"{'activity' if reviewed_count + below == 1 else 'activities'}")
    blocks.append(_divider())
    blocks.append(_context("📌 " + " · ".join(footer)))

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
        blocks.append(_section(
            f"*{_link(item.url, item.title)}*\n"
            f"{_esc(_source_line(item, now))}\n"
            f"_{_esc(item.reason)}_"))
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
        for label, entries in (("Decisions", digest.decisions),
                               ("Issues", digest.issues),
                               ("Notes", digest.notes)):
            if entries:
                bullets = "\n".join(f"• {_esc(e)}"
                                    for e in entries[:MAX_DIGEST_BULLETS])
                blocks.append(_section(f"*{label}*\n{bullets}"))
    else:
        total = sum(counts.values())
        sources = ", ".join(f"{n} {SOURCE_LABELS.get(s, s)}"
                            for s, n in sorted(counts.items()))
        blocks.append(_section(f"*🧭 Team digest — last {hours}h*\n"
                               f"{total} team activities ({sources}) — "
                               f"AI summary unavailable."))
    return blocks


# -- primitives ------------------------------------------------------------

def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;") \
                       .replace(">", "&gt;")


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


def _divider() -> dict:
    return {"type": "divider"}
