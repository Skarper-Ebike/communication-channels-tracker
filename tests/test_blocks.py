from datetime import timedelta

from app.ai.schemas import DigestResult
from app.config import AppConfig
from app.models import AttentionCategory
from app.output.blocks import morning_blocks, urgent_blocks, weekly_blocks
from tests.conftest import NOW, make_item


def sample_items():
    return [
        make_item(AttentionCategory.OVERDUE, score=100, priority="high",
                  title="Checkout <fix> & retest", object_id="ACME-242",
                  due_at=NOW - timedelta(days=2),
                  reason="Assigned to you and 2 days overdue.",
                  url="https://example.atlassian.net/browse/ACME-242"),
        make_item(AttentionCategory.DIRECT_MENTION, score=45,
                  title="Q3 Board Deck", object_id="deck",
                  reason="Lucy mentioned you directly.",
                  url="https://docs.google.com/presentation/d/deck"),
    ]


def texts(blocks):
    out = []
    for b in blocks:
        if "text" in b:
            out.append(b["text"]["text"])
        for el in b.get("elements", []):
            out.append(el["text"])
        for f in b.get("fields", []):
            out.append(f["text"])
    return "\n".join(out)


def test_morning_blocks_structure_and_links():
    blocks = morning_blocks(sample_items(), reviewed_count=6,
                            config=AppConfig(), now=NOW)
    assert blocks[0]["type"] == "header"
    assert "Morning briefing" in blocks[0]["text"]["text"]

    body = texts(blocks)
    # Titles are mrkdwn links, not raw URLs, and special chars are escaped.
    assert "<https://example.atlassian.net/browse/ACME-242|" \
           "Checkout &lt;fix&gt; &amp; retest>" in body
    assert "🔴 Needs you" in body
    assert "🟠 Worth knowing" in body
    # Key phrases auto-bolded in deterministic text.
    assert "_Assigned to you and *2 days overdue*._" in body
    assert "*Lucy* mentioned you directly." in body
    # Category emoji on items.
    assert "⏰" in body and "💬" in body
    # Footer is a fields grid ("table") with the counts.
    fields_blocks = [b for b in blocks if b.get("fields")]
    assert fields_blocks, "expected an At a glance fields section"
    assert "📌 At a glance" in fields_blocks[-1]["text"]["text"]
    assert "6 other activities" in texts(fields_blocks)


def test_morning_blocks_digest_and_degraded():
    digest = DigestResult(overview="Busy ESC day.",
                          team=["*Jess* — ESC debugging (Jira)."],
                          decisions=["Luv: proceed (CP1R-47)."],
                          issues=[], notes=[])
    blocks = morning_blocks([], reviewed_count=0, config=AppConfig(), now=NOW,
                            degraded=["google_drive"], digest=digest,
                            digest_counts={"jira": 3}, digest_hours=24)
    body = texts(blocks)
    assert "🧭 Team digest — last 24h" in body
    assert "Busy ESC day." in body
    assert "👥 Team — who's on what" in body
    assert "👤 *Jess* — ESC debugging (Jira)." in body
    assert "*Decisions*" in body and "✅ Luv: proceed" in body
    assert "*Issues*" not in body  # empty lists render nothing
    assert "⚠️ Google Drive could not be checked" in body


def test_morning_blocks_empty_state():
    blocks = morning_blocks([], reviewed_count=0, config=AppConfig(), now=NOW)
    assert "Nothing needs you right now." in texts(blocks)


def test_needs_you_capped_with_context_note():
    items = [make_item(AttentionCategory.OVERDUE, score=90,
                       object_id=f"ACME-{n}", title=f"Task {n}")
             for n in range(14)]
    blocks = morning_blocks(items, reviewed_count=0,
                            config=AppConfig(), now=NOW)
    assert "…and 4 more above the threshold." in texts(blocks)
    assert len(blocks) < 50  # Slack hard limit


def test_urgent_blocks():
    blocks = urgent_blocks(sample_items(), AppConfig(), NOW)
    body = texts(blocks)
    assert blocks[0]["text"]["text"] == "🚨 Urgent"
    assert "Checkout" in body
    assert "Q3 Board Deck" not in body  # below urgent threshold


def test_weekly_blocks_wrap_text_rendering():
    blocks = weekly_blocks([], NOW, "📌 THINGS\n\nbody line")
    assert blocks[0]["type"] == "header"
    assert "body line" in blocks[1]["text"]["text"]
