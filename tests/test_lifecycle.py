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


# -- date items follow the source's current state ---------------------------

def _open_overdue(paths, **overrides):
    store = AttentionStore(paths)
    item = make_item(due_at=NOW - timedelta(days=2), **overrides)
    store.merge([item], [], NOW)
    store.save()
    return item


def test_overdue_item_resolved_when_snapshot_moves_due_date_later(paths):
    item = _open_overdue(paths)
    later = NOW + timedelta(days=1)
    moved = make_activity(id="jira:ACME-1:updated:2", snapshot=True,
                          assigned_to_user=True,
                          due_at=later + timedelta(days=30))
    store = AttentionStore(paths)
    store.merge([], [moved], later)
    store.save()

    store2 = AttentionStore(paths)
    assert store2.reportable(later) == []
    assert store2.resolved[item.key].status is AttentionStatus.RESOLVED
    assert store2.resolved[item.key].resolved_at == later


def test_overdue_item_resolved_when_snapshot_shows_unassigned(paths):
    item = _open_overdue(paths)
    gone = make_activity(snapshot=True, assigned_to_user=False,
                         due_at=NOW - timedelta(days=2))
    store = AttentionStore(paths)
    store.merge([], [gone], NOW)
    assert item.key in store.resolved


def test_overdue_item_kept_when_only_non_snapshot_activity_seen(paths):
    item = _open_overdue(paths)
    later = NOW + timedelta(days=1)
    event = make_activity(id="basecamp:event-1", snapshot=False, due_at=None)
    store = AttentionStore(paths)
    store.merge([], [event], later)
    [kept] = store.reportable(later)
    assert kept.key == item.key
    assert kept.due_at == NOW - timedelta(days=2)


def test_still_overdue_snapshot_keeps_item_and_refreshes_title(paths):
    # An unchanged issue re-read each run is deduped, so no candidate comes
    # with it; its snapshot alone must not resolve the item.
    item = _open_overdue(paths)
    later = NOW + timedelta(days=1)
    renamed = make_activity(snapshot=True, assigned_to_user=True,
                            title="Renamed issue",
                            due_at=NOW - timedelta(days=2))
    store = AttentionStore(paths)
    store.merge([], [renamed], later)
    [kept] = store.reportable(later)
    assert kept.key == item.key
    assert kept.title == "Renamed issue"
    assert kept.reason == "Assigned to you and 3 days overdue."


def test_reason_recomputed_from_due_date_without_candidates(paths):
    _open_overdue(paths)
    later = NOW + timedelta(days=10)
    store = AttentionStore(paths)
    store.merge([], [], later)
    [item] = store.reportable(later)
    assert item.reason == "Assigned to you and 12 days overdue."


def test_snoozed_date_item_reason_recomputed_too(paths):
    item = _open_overdue(paths)
    store = AttentionStore(paths)
    store.snooze(item.key, until=NOW + timedelta(days=30))
    store.merge([], [], NOW + timedelta(days=5))
    assert store.snoozed[item.key].reason == "Assigned to you and 7 days overdue."


def test_date_object_ids_lists_open_and_snoozed_date_items(paths):
    from app.models import AttentionCategory, Source

    store = AttentionStore(paths)
    store.merge([
        make_item(object_id="ACME-1", due_at=NOW - timedelta(days=1)),
        make_item(AttentionCategory.DUE_SOON, object_id="ACME-2",
                  due_at=NOW + timedelta(hours=5)),
        make_item(AttentionCategory.DIRECT_MENTION, object_id="ACME-3"),
        make_item(source=Source.BASECAMP, object_id="77"),
    ], [], NOW)
    store.snooze("jira:ACME-2:due_soon", until=NOW + timedelta(days=1))
    assert store.date_object_ids(Source.JIRA) == ["ACME-1", "ACME-2"]
    assert store.date_object_ids(Source.BASECAMP) == ["77"]
