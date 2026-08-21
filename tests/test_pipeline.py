"""End-to-end mock pipeline: the Milestone 1+2 acceptance path."""

import subprocess
import sys
from pathlib import Path

from app import runner
from app.storage import LocalPaths
from tests.conftest import NOW

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
