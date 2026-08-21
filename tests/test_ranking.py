from datetime import timedelta

from app.attention.ranking import score
from app.models import AttentionCategory
from tests.conftest import NOW, make_item


def test_overdue_scores_90():
    assert score(make_item(AttentionCategory.OVERDUE), NOW) == 90


def test_high_priority_adds_10():
    assert score(make_item(AttentionCategory.OVERDUE, priority="high"), NOW) == 100


def test_score_clamped_to_100():
    item = make_item(AttentionCategory.OVERDUE, priority="critical")
    assert score(item, NOW) == 100


def test_due_within_24h_scores_70():
    item = make_item(AttentionCategory.DUE_SOON, due_at=NOW + timedelta(hours=20))
    assert score(item, NOW) == 70


def test_due_within_3_days_scores_45():
    item = make_item(AttentionCategory.DUE_SOON, due_at=NOW + timedelta(hours=60))
    assert score(item, NOW) == 45


def test_direct_mention_scores_45():
    assert score(make_item(AttentionCategory.DIRECT_MENTION), NOW) == 45


def test_new_reply_scores_35():
    assert score(make_item(AttentionCategory.NEW_REPLY), NOW) == 35


def test_low_confidence_penalty():
    item = make_item(AttentionCategory.DIRECT_MENTION, confidence=0.5)
    assert score(item, NOW) == 25


def test_repeated_report_penalty():
    item = make_item(AttentionCategory.DIRECT_MENTION, report_count=3)
    assert score(item, NOW) == 35


def test_floor_is_zero():
    item = make_item(AttentionCategory.NOISE, confidence=0.1, report_count=5)
    assert score(item, NOW) == 0
