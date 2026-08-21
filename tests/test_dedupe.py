from datetime import timedelta

from app.attention.dedupe import RecentEvents, event_key
from tests.conftest import NOW, make_activity


def test_same_event_twice_in_one_batch_yields_one(paths):
    a = make_activity()
    recent = RecentEvents(paths)
    assert len(recent.filter_new([a, a.model_copy()])) == 1


def test_seen_event_filtered_on_next_run(paths):
    a = make_activity()
    recent = RecentEvents(paths)
    fresh = recent.filter_new([a])
    recent.mark_seen(fresh, NOW)
    recent.save()

    recent2 = RecentEvents(paths)
    assert recent2.filter_new([a]) == []


def test_new_event_passes_through(paths):
    recent = RecentEvents(paths)
    recent.mark_seen([make_activity(id="jira:ACME-1:updated:1")], NOW)
    recent.save()

    recent2 = RecentEvents(paths)
    fresh = recent2.filter_new([make_activity(id="jira:ACME-1:updated:2")])
    assert len(fresh) == 1


def test_fallback_key_is_stable_content_hash():
    a1 = make_activity(id="", actor_name="James", text="hello")
    a2 = make_activity(id="", actor_name="James", text="hello")
    a3 = make_activity(id="", actor_name="James", text="different")
    assert event_key(a1) == event_key(a2)
    assert event_key(a1) != event_key(a3)
    assert event_key(a1).startswith("jira:sha256:")


def test_prune_drops_old_entries(paths):
    recent = RecentEvents(paths)
    recent.mark_seen([make_activity(id="old:1")], NOW - timedelta(days=20))
    recent.mark_seen([make_activity(id="new:1")], NOW - timedelta(days=2))
    recent.prune(NOW)
    recent.save()

    recent2 = RecentEvents(paths)
    assert recent2.filter_new([make_activity(id="old:1")])  # forgotten → fresh
    assert recent2.filter_new([make_activity(id="new:1")]) == []
