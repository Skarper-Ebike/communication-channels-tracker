from datetime import timedelta

from app.config import AppConfig
from app.models import AttentionCategory, Source
from app.output.renderer import render_morning, render_urgent, render_weekly
from tests.conftest import NOW, make_item


def sample_items():
    return [
        make_item(AttentionCategory.OVERDUE, score=100, priority="high",
                  title="Checkout regression", object_id="ACME-242",
                  due_at=NOW - timedelta(days=2),
                  reason="Assigned to you and 2 days overdue.",
                  url="https://example.atlassian.net/browse/ACME-242"),
        make_item(AttentionCategory.DUE_SOON, score=70,
                  title="Release notes for 1.4", object_id="ACME-251",
                  due_at=NOW + timedelta(hours=20),
                  reason="Assigned to you and due in 20 hours."),
        make_item(AttentionCategory.DIRECT_MENTION, score=45,
                  source=Source.GOOGLE_DRIVE, object_id="q3-board-deck",
                  title="Q3 Board Deck",
                  reason="Lucy mentioned you directly.",
                  url="https://docs.google.com/presentation/d/q3-board-deck"),
    ]


def test_morning_briefing_sections_and_ordering():
    text = render_morning(sample_items(), reviewed_count=6,
                          config=AppConfig(), now=NOW)
    assert "☀️ YOUR MORNING BRIEFING" in text
    assert "🔴 NEEDS YOU" in text
    assert "🟠 WORTH KNOWING" in text
    assert text.index("Checkout regression") < text.index("Release notes for 1.4")
    assert "1. Checkout regression" in text
    assert "Jira · 2 days overdue · high priority" in text
    assert "→ https://example.atlassian.net/browse/ACME-242" in text
    assert "• Q3 Board Deck — Lucy mentioned you directly." in text
    assert "📌 OUTSTANDING" in text
    assert "1 assigned task overdue" in text
    assert "Reviewed 6 other activities." in text


def test_morning_briefing_reports_degraded_sources():
    text = render_morning([], reviewed_count=0, config=AppConfig(), now=NOW,
                          degraded=["google_drive"])
    assert "⚠️ Google Drive could not be checked." in text
    assert "doctor" in text


def test_morning_briefing_empty_state():
    text = render_morning([], reviewed_count=0, config=AppConfig(), now=NOW)
    assert "Nothing needs you right now." in text


def test_urgent_quiet_by_default():
    assert render_urgent(sample_items()[1:], AppConfig(), NOW) == \
        "Nothing urgent right now."


def test_urgent_surfaces_only_top_items():
    text = render_urgent(sample_items(), AppConfig(), NOW)
    assert "🚨 URGENT" in text
    assert "Checkout regression" in text
    assert "Release notes" not in text


def test_weekly_hanging_and_upcoming():
    items = [
        make_item(AttentionCategory.OVERDUE, title="Old thing",
                  report_count=4, first_seen_at=NOW - timedelta(days=11),
                  last_seen_at=NOW),
        make_item(AttentionCategory.DUE_SOON, title="Next week thing",
                  object_id="ACME-9", due_at=NOW + timedelta(days=4)),
    ]
    text = render_weekly(items, NOW)
    assert "THINGS THAT KEEP HANGING AROUND" in text
    assert "Old thing — surfaced in 4 briefings" in text
    assert "COMING NEXT WEEK" in text
    assert "Next week thing" in text
