from datetime import timedelta

from app.attention import rules
from app.models import AttentionCategory
from tests.conftest import NOW, make_activity


def cats(activities):
    return {i.category for i in rules.evaluate(activities, NOW)}


def test_overdue():
    a = make_activity(assigned_to_user=True, due_at=NOW - timedelta(days=2))
    items = rules.evaluate([a], NOW)
    assert [i.category for i in items] == [AttentionCategory.OVERDUE]
    assert "2 days overdue" in items[0].reason


def test_due_soon_within_72_hours():
    a = make_activity(assigned_to_user=True, due_at=NOW + timedelta(hours=20))
    assert cats([a]) == {AttentionCategory.DUE_SOON}


def test_due_far_out_is_ignored():
    a = make_activity(assigned_to_user=True, due_at=NOW + timedelta(days=10))
    assert cats([a]) == set()


def test_unassigned_due_date_is_ignored():
    a = make_activity(assigned_to_user=False, due_at=NOW - timedelta(days=2))
    assert cats([a]) == set()


def test_completed_produces_nothing():
    a = make_activity(assigned_to_user=True, due_at=NOW - timedelta(days=2),
                      completed=True)
    assert cats([a]) == set()


def test_direct_mention():
    a = make_activity(mentions_user=True, actor_name="James")
    items = rules.evaluate([a], NOW)
    assert [i.category for i in items] == [AttentionCategory.DIRECT_MENTION]
    assert "James" in items[0].reason


def test_new_assignment():
    a = make_activity(event_type="assigned", assigned_to_user=True)
    assert cats([a]) == {AttentionCategory.NEW_ASSIGNMENT}


def test_new_reply():
    a = make_activity(event_type="reply", directed_at_user=True)
    assert cats([a]) == {AttentionCategory.NEW_REPLY}


def test_one_activity_can_yield_multiple_categories():
    a = make_activity(assigned_to_user=True, due_at=NOW - timedelta(days=1),
                      mentions_user=True)
    assert cats([a]) == {AttentionCategory.OVERDUE, AttentionCategory.DIRECT_MENTION}


def test_same_object_twice_yields_one_item_per_category():
    a1 = make_activity(id="jira:ACME-1:updated:1", assigned_to_user=True,
                       due_at=NOW - timedelta(days=1))
    a2 = make_activity(id="jira:ACME-1:updated:2", assigned_to_user=True,
                       due_at=NOW - timedelta(days=1))
    assert len(rules.evaluate([a1, a2], NOW)) == 1
