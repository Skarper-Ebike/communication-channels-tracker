from datetime import timedelta

from app.ai.schemas import DigestResult
from app.config import AppConfig
from app.digest import DigestBuffer
from app.output.renderer import render_morning
from tests.conftest import NOW, make_activity


def test_buffer_accumulates_across_runs_and_dedupes(paths):
    buffer = DigestBuffer(paths)
    buffer.append([make_activity(id="slack:C1:1", text="morning chat")], NOW)
    buffer.save()

    buffer2 = DigestBuffer(paths)
    buffer2.append([
        make_activity(id="slack:C1:1", text="morning chat"),   # duplicate
        make_activity(id="jira:ACME-1:updated:2", title="Fix bug"),
    ], NOW)
    entries = buffer2.entries_since(NOW)
    assert [e["id"] for e in entries] == ["slack:C1:1", "jira:ACME-1:updated:2"]


def test_buffer_prunes_old_entries_and_truncates_text(paths):
    buffer = DigestBuffer(paths)
    old = make_activity(id="old:1", created_at=NOW - timedelta(hours=60))
    fresh = make_activity(id="new:1", text="x" * 5000)
    buffer.append([old, fresh], NOW)
    entries = buffer.entries_since(NOW, hours=48, limit=100)
    assert [e["id"] for e in entries] == ["new:1"]
    assert len(entries[0]["text"]) == 300


def test_entries_since_window_and_limit(paths):
    buffer = DigestBuffer(paths)
    buffer.append([
        make_activity(id="a", created_at=NOW - timedelta(hours=30)),
        make_activity(id="b", created_at=NOW - timedelta(hours=2)),
        make_activity(id="c", created_at=NOW - timedelta(hours=1)),
    ], NOW)
    assert [e["id"] for e in buffer.entries_since(NOW, hours=24)] == ["b", "c"]
    assert [e["id"] for e in buffer.entries_since(NOW, hours=24, limit=1)] == ["c"]


def test_drain_empties_buffer(paths):
    buffer = DigestBuffer(paths)
    buffer.append([make_activity(id="a")], NOW)
    buffer.drain()
    buffer.save()
    assert DigestBuffer(paths).entries_since(NOW) == []


def test_counts_by_source(paths):
    buffer = DigestBuffer(paths)
    buffer.append([
        make_activity(id="j1"),
        make_activity(id="j2"),
        make_activity(id="s1", source="slack", object_id="C1"),
    ], NOW)
    assert buffer.counts_by_source(NOW) == {"jira": 2, "slack": 1}


def test_morning_briefing_renders_digest_section():
    digest = DigestResult(
        overview="The team spent the day debugging the new ESC current issue.",
        team=["*Jess* — ESC current debugging, 90A bench tests (Jira, Slack)."],
        decisions=["Luv: treat the failed unit as failed so other testing "
                   "continues (CP1R-47)."],
        issues=["Bench PSU browned out during 90A test (Jess, CP1R-47)."],
        notes=["Firmware 5.3 uploaded to the VESC tool share (Tom)."],
    )
    text = render_morning([], reviewed_count=0, config=AppConfig(), now=NOW,
                          digest=digest, digest_counts={"jira": 12},
                          digest_hours=24)
    assert "🧭 TEAM DIGEST — last 24h" in text
    assert "debugging the new ESC" in text
    assert "TEAM" in text and "ESC current debugging" in text
    assert "DECISIONS" in text and "treat the failed unit" in text
    assert "ISSUES" in text and "browned out" in text
    assert "NOTES" in text and "VESC tool share" in text
    # Digest sits before OUTSTANDING/footer.
    assert text.index("TEAM DIGEST") < text.index("Reviewed")


def test_counts_fallback_without_ai():
    text = render_morning([], reviewed_count=0, config=AppConfig(), now=NOW,
                          digest=None, digest_counts={"jira": 3, "slack": 9},
                          digest_hours=24)
    assert "12 team activities" in text
    assert "9 Slack" in text and "3 Jira" in text
    assert "AI summary unavailable" in text


def test_no_digest_no_section():
    text = render_morning([], reviewed_count=0, config=AppConfig(), now=NOW)
    assert "TEAM DIGEST" not in text
