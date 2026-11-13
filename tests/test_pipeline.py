"""End-to-end mock pipeline: the Milestone 1+2 acceptance path."""

import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from app import runner
from app.connectors.base import SyncResult
from app.storage import LocalPaths
from tests.conftest import NOW, make_activity, make_item

REPO_ROOT = Path(__file__).resolve().parents[1]


def mock_paths(paths) -> LocalPaths:
    # Mock runs are isolated under .local/mock/ so fixtures never pollute
    # real attention state.
    return LocalPaths(root=paths.root / "mock")


def test_mock_briefing_renders_and_persists(paths):
    result = runner.run("briefing", mock=True, paths=paths, now=NOW)
    assert "☀️ YOUR MORNING BRIEFING" in result.text
    assert "🔴 NEEDS YOU" in result.text
    assert "Checkout regression" in result.text          # overdue, high priority
    assert "Approve homepage design" in result.text      # overdue basecamp todo
    assert "Q3 Board Deck" in result.text                # drive mention
    isolated = mock_paths(paths)
    assert (isolated.attention / "open.json").exists()
    assert (isolated.state / "recent-event-ids.json").exists()
    assert (isolated.state / "mock.json").exists()
    # ...and nothing leaked into the real state tree.
    assert not (paths.attention / "open.json").exists()
    assert not (paths.state / "mock.json").exists()


def test_second_run_keeps_items_open_and_increments_reports(paths):
    runner.run("briefing", mock=True, paths=paths, now=NOW)
    result2 = runner.run("briefing", mock=True, paths=paths, now=NOW)
    # Fixture events are deduped, but unresolved items persist via lifecycle.
    assert "Checkout regression" in result2.text

    from app.attention.lifecycle import AttentionStore
    store = AttentionStore(mock_paths(paths))
    overdue = store.open["jira:ACME-242:overdue"]
    assert overdue.report_count == 2


def test_mock_run_never_delivers_to_slack(paths, monkeypatch):
    monkeypatch.setenv("WORK_AGENT_SLACK_WEBHOOK",
                       "https://hooks.slack.com/services/T/B/X")
    result = runner.run("briefing", mock=True, paths=paths, now=NOW)
    assert result.delivered is None


def test_digest_buffer_survives_sync_and_drains_on_briefing(paths):
    from app.digest import DigestBuffer

    # An hourly-style sync consumes the events…
    runner.run("sync", mock=True, paths=paths, now=NOW)
    isolated = mock_paths(paths)
    assert DigestBuffer(isolated).entries_since(NOW)

    # …but the morning briefing still digests the whole day, then drains.
    result = runner.run("briefing", mock=True, paths=paths, now=NOW)
    assert "🧭 TEAM DIGEST" in result.text
    assert "team activities" in result.text  # counts fallback (no AI key)
    assert DigestBuffer(isolated).entries_since(NOW) == []


def test_dry_run_writes_nothing(paths):
    result = runner.run("briefing", mock=True, dry_run=True, paths=paths, now=NOW)
    assert "☀️ YOUR MORNING BRIEFING" in result.text
    isolated = mock_paths(paths)
    assert not (isolated.attention / "open.json").exists()
    assert not (isolated.state / "mock.json").exists()


def test_completed_fixture_item_never_surfaces(paths):
    result = runner.run("briefing", mock=True, paths=paths, now=NOW)
    assert "API migration" not in result.text


def test_cli_briefing_mock(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "briefing", "--mock"],
        capture_output=True, text=True, cwd=REPO_ROOT,
        env={"PATH": "/usr/bin:/bin", "WORK_AGENT_HOME": str(tmp_path / ".local")},
    )
    assert proc.returncode == 0, proc.stderr
    assert "☀️ YOUR MORNING BRIEFING" in proc.stdout


def test_cli_passes_mock_flag_through(monkeypatch, paths):
    """Regression: `briefing` without --mock must NOT run the mock pipeline."""
    import app.cli as cli
    from app import runner as runner_module

    seen = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs, command=command)
        return runner_module.RunResult(text="ok")

    monkeypatch.setattr(runner_module, "run", fake_run)
    monkeypatch.setattr(runner_module, "any_live_connectors", lambda c: True)
    monkeypatch.setenv("WORK_AGENT_HOME", str(paths.root))

    assert cli.main(["briefing"]) == 0
    assert seen["mock"] is False
    assert cli.main(["briefing", "--mock"]) == 0
    assert seen["mock"] is True


def test_cli_without_mock_explains_and_exits_2(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "app.cli", "briefing"],
        capture_output=True, text=True, cwd=REPO_ROOT,
        env={"PATH": "/usr/bin:/bin", "WORK_AGENT_HOME": str(tmp_path / ".local")},
    )
    assert proc.returncode == 2
    assert "--mock" in proc.stdout


# -- date items through the real pipeline (dedupe included) ------------------

class SnapshotConnector:
    """Re-reads the same objects every run, as Jira refresh and Basecamp do."""

    name = "snapshots"

    def __init__(self, activities):
        self.activities = activities

    def fetch_changes(self, state):
        return SyncResult(activities=self.activities, state=dict(state))


def run_live(monkeypatch, paths, activities, now):
    monkeypatch.setattr(runner, "_live_connectors",
                        lambda config, degraded, now, store:
                        [SnapshotConnector(activities)])
    runner.run("sync", paths=paths, now=now, no_ai=True)
    from app.attention.lifecycle import AttentionStore
    return AttentionStore(paths)


def test_unchanged_overdue_snapshot_stays_open_across_runs(paths, monkeypatch):
    snap = make_activity(snapshot=True, assigned_to_user=True,
                         due_at=NOW - timedelta(days=2))
    run_live(monkeypatch, paths, [snap], NOW)
    # Same activity id next run: dedupe drops it, the item must survive.
    store = run_live(monkeypatch, paths, [snap], NOW + timedelta(days=1))
    item = store.open["jira:ACME-1:overdue"]
    assert item.reason == "Assigned to you and 3 days overdue."


def test_moved_due_date_resolves_item_through_pipeline(paths, monkeypatch):
    overdue = make_activity(snapshot=True, assigned_to_user=True,
                            due_at=NOW - timedelta(days=2))
    run_live(monkeypatch, paths, [overdue], NOW)
    moved = make_activity(id="jira:ACME-1:updated:2", snapshot=True,
                          assigned_to_user=True, title="Renamed",
                          due_at=NOW + timedelta(days=30))
    store = run_live(monkeypatch, paths, [moved], NOW + timedelta(hours=1))
    assert "jira:ACME-1:overdue" not in store.open
    assert "jira:ACME-1:overdue" in store.resolved


def test_due_soon_becomes_overdue_without_new_event(paths, monkeypatch):
    snap = make_activity(snapshot=True, assigned_to_user=True,
                         due_at=NOW + timedelta(hours=20))
    store = run_live(monkeypatch, paths, [snap], NOW)
    assert "jira:ACME-1:due_soon" in store.open
    store = run_live(monkeypatch, paths, [snap], NOW + timedelta(days=2))
    assert "jira:ACME-1:due_soon" in store.resolved
    assert store.open["jira:ACME-1:overdue"].reason == \
        "Assigned to you and 1 day overdue."


def test_runner_hands_open_date_keys_to_jira(paths, monkeypatch):
    from app.attention.lifecycle import AttentionStore
    from app.config import AppConfig

    store = AttentionStore(paths)
    store.merge([make_item(object_id="CRX-281", due_at=NOW)], [], NOW)
    config = AppConfig()
    config.connectors.jira.enabled = True
    config.connectors.jira.base_url = "https://example.atlassian.net"
    monkeypatch.setenv("JIRA_EMAIL", "jakub@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    [jira] = runner._live_connectors(config, [], NOW, store)
    assert jira.refresh_keys == ["CRX-281"]
