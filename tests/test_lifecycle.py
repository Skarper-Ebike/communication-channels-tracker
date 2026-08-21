from datetime import timedelta

from app.attention.lifecycle import AttentionStore
from app.models import AttentionStatus
from tests.conftest import NOW, make_activity, make_item


def test_new_item_opens(paths):
    store = AttentionStore(paths)
    store.merge([make_item()], [], NOW)
    assert len(store.reportable(NOW)) == 1


def test_still_open_item_keeps_first_seen_and_counts_reports(paths):
    store = AttentionStore(paths)
    item = make_item()
    store.merge([item], [], NOW)
    store.mark_reported([item.key], NOW)
    store.save()

    later = NOW + timedelta(days=1)
    store2 = AttentionStore(paths)
    store2.merge([make_item(reason="Assigned to you and 3 days overdue.")], [], later)
    store2.mark_reported([item.key], later)
    store2.save()

    store3 = AttentionStore(paths)
    [reported] = store3.reportable(later)
    assert reported.first_seen_at == NOW
    assert reported.last_seen_at == later
    assert reported.report_count == 2
    assert "3 days" in reported.reason


def test_completed_source_activity_resolves_item(paths):
    store = AttentionStore(paths)
    item = make_item()
    store.merge([item], [], NOW)
    store.save()

    done = make_activity(id="jira:ACME-1:updated:2", completed=True)
    later = NOW + timedelta(days=1)
    store2 = AttentionStore(paths)
    store2.merge([], [done], later)
    store2.save()

    store3 = AttentionStore(paths)
    assert store3.reportable(later) == []
    assert store3.resolved[item.key].status is AttentionStatus.RESOLVED
    assert store3.resolved[item.key].resolved_at == later


def test_snoozed_item_excluded_then_wakes(paths):
    store = AttentionStore(paths)
    item = make_item()
    store.merge([item], [], NOW)
    assert store.snooze(item.key, until=NOW + timedelta(days=2))
    store.save()

    store2 = AttentionStore(paths)
    store2.merge([], [], NOW + timedelta(days=1))
    assert store2.reportable(NOW + timedelta(days=1)) == []
    store2.save()

    store3 = AttentionStore(paths)
    store3.merge([], [], NOW + timedelta(days=3))
    [woken] = store3.reportable(NOW + timedelta(days=3))
    assert woken.status is AttentionStatus.OPEN
    assert woken.snoozed_until is None


def test_dismiss(paths):
    store = AttentionStore(paths)
    item = make_item()
    store.merge([item], [], NOW)
    assert store.dismiss(item.key, NOW)
    assert store.reportable(NOW) == []
    assert store.resolved[item.key].status is AttentionStatus.DISMISSED


def test_old_resolved_items_pruned(paths):
    store = AttentionStore(paths)
    item = make_item()
    store.merge([item], [], NOW)
    store.merge([], [make_activity(id="x", completed=True)], NOW)
    store.save()

    much_later = NOW + timedelta(days=40)
    store2 = AttentionStore(paths)
    store2.merge([], [], much_later)
    assert item.key not in store2.resolved


def test_resolved_key_reappearing_becomes_fresh_item(paths):
    store = AttentionStore(paths)
    item = make_item()
    store.merge([item], [], NOW)
    store.merge([], [make_activity(id="x", completed=True)], NOW)
    store.save()

    later = NOW + timedelta(days=2)
    store2 = AttentionStore(paths)
    store2.merge([make_item(first_seen_at=later, last_seen_at=later)], [], later)
    [fresh] = store2.reportable(later)
    assert fresh.first_seen_at == later
    assert fresh.report_count == 0
