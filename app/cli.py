"""CLI entrypoint: `uv run python -m app.cli <command>`.

No user argument anywhere — the clone represents exactly one person.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import AppConfig, load_config, save_config
from app.env import load_env
from app.lock import AlreadyRunning
from app.storage import LocalPaths, read_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="work-agent",
        description="Personal Work Agent — fully local work briefings.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="interactive first-run setup")
    sub.add_parser("doctor", help="check local configuration and state")
    sub.add_parser("status", help="show open attention items and sync state")

    for name, help_text in (
        ("briefing", "generate the morning briefing"),
        ("urgent", "scan for urgent items only"),
        ("weekly", "generate the weekly summary"),
        ("sync", "poll sources and update state without a briefing"),
        ("run", "full cycle: sync + morning briefing"),
        ("dry-run", "morning briefing without writing state or sending anything"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--mock", action="store_true",
                       help="use local fixture data instead of live APIs")
        p.add_argument("--dry-run", action="store_true",
                       help="do not write state or send anything")
        p.add_argument("--no-ai", action="store_true",
                       help="deterministic rules only (AI is not wired up yet)")

    reconnect = sub.add_parser("reconnect", help="re-authenticate a connector")
    reconnect.add_argument("service")

    sub.add_parser("install-scheduler", help="install the local scheduled jobs")
    sub.add_parser("uninstall-scheduler", help="remove the local scheduled jobs")

    args = parser.parse_args(argv)

    paths = LocalPaths()
    _setup_logging(paths)
    load_env(paths)

    if args.command == "setup":
        from app.setup.wizard import run_setup
        return run_setup(paths)
    if args.command == "doctor":
        return _cmd_doctor(paths)
    if args.command == "status":
        return _cmd_status(paths)
    if args.command == "reconnect":
        return _cmd_reconnect(args.service, paths)
    if args.command == "install-scheduler":
        return _cmd_scheduler(paths, install=True)
    if args.command == "uninstall-scheduler":
        return _cmd_scheduler(paths, install=False)

    return _cmd_pipeline(args, paths)


def _cmd_pipeline(args: argparse.Namespace, paths: LocalPaths) -> int:
    from app import runner  # deferred so --help stays instant

    command = "briefing" if args.command in ("run", "dry-run") else args.command
    dry_run = args.dry_run or args.command == "dry-run"

    if not args.mock and not runner.any_live_connectors(load_config(paths)):
        print("No live connectors are enabled yet. Run setup to connect Jira, "
              "or use --mock for local fixture data:\n"
              f"  uv run python -m app.cli {args.command} --mock")
        return 2

    try:
        result = runner.run(command, mock=args.mock, dry_run=dry_run,
                            no_ai=args.no_ai, paths=paths)
    except AlreadyRunning:
        print("Another run is already in progress; exiting safely.")
        return 0

    print(result.text)
    if dry_run:
        print("\n(dry run — no state written, nothing sent)")
    elif result.delivered is True:
        print("\n✓ Posted to your Slack channel.")
    elif result.delivered is False:
        print("\n⚠️ Slack delivery failed — briefing shown above only. "
              "Run: uv run python -m app.cli doctor")
        return 1
    return 0


def _cmd_doctor(paths: LocalPaths) -> int:
    import os

    print("PERSONAL WORK AGENT\n")
    ok = True
    config = load_config(paths)

    def check(label: str, passed: bool, fix: str = "") -> None:
        nonlocal ok
        print(f"{'✓' if passed else '✗'} {label}")
        if not passed:
            ok = False
            if fix:
                print(f"  Run: {fix}")

    check("local config", paths.config_file.exists(),
          "uv run python -m app.cli setup")
    check("local state directory", paths.state.is_dir(),
          "uv run python -m app.cli setup")

    jira = config.connectors.jira
    if jira.enabled:
        email = os.environ.get("JIRA_EMAIL")
        token = os.environ.get("JIRA_API_TOKEN")
        if email and token and jira.base_url:
            from app.connectors.jira import JiraConnector
            result = JiraConnector(jira, email, token).validate()
            check(f"Jira {result.detail}" if result.ok
                  else f"Jira: {result.detail}", result.ok,
                  "uv run python -m app.cli reconnect jira")
        else:
            check("Jira credentials in .local/.env", False,
                  "uv run python -m app.cli reconnect jira")
    else:
        print("– Jira: not configured")

    webhook = os.environ.get(config.slack_output.webhook_env)
    if webhook:
        check("Slack webhook configured", True)
    else:
        print(f"– Slack output: not configured "
              f"(set {config.slack_output.webhook_env} via setup)")

    slack_cfg = config.connectors.slack
    if slack_cfg.enabled:
        token = os.environ.get("SLACK_USER_TOKEN")
        if token and slack_cfg.channels:
            from app.connectors.slack import SlackConnector
            result = SlackConnector(slack_cfg, token).validate()
            check(f"Slack input {result.detail}" if result.ok
                  else f"Slack input: {result.detail}", result.ok,
                  "uv run python -m app.cli reconnect slack")
        else:
            check("Slack input token/channels", False,
                  "uv run python -m app.cli reconnect slack")
    else:
        print("– Slack input: not configured")

    if config.connectors.google_drive.enabled:
        from app.connectors.google_drive import GoogleDriveConnector
        result = GoogleDriveConnector(config.connectors.google_drive).validate()
        check(f"Google {result.detail}" if result.ok
              else f"Google: {result.detail}", result.ok,
              "uv run python -m app.cli reconnect google")
    else:
        print("– Google Drive: not configured")

    if config.connectors.basecamp.enabled:
        from app.connectors.basecamp import BasecampConnector
        result = BasecampConnector(config.connectors.basecamp).validate()
        check(f"Basecamp {result.detail}" if result.ok
              else f"Basecamp: {result.detail}", result.ok,
              "uv run python -m app.cli reconnect basecamp")
    else:
        print("– Basecamp: not configured")

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        check("Claude reachable", _claude_reachable(config),
              "check ANTHROPIC_API_KEY in .local/.env")
    else:
        print("– Claude: no API key (briefings run deterministic-only)")

    from app import scheduler
    jobs = scheduler.installed_jobs()
    if jobs:
        check(f"scheduler configured ({', '.join(jobs)})", True)
    else:
        print("– scheduler: not installed "
              "(uv run python -m app.cli install-scheduler)")

    print("– Confluence: arrives in a later milestone")

    print("\nReady." if ok else "\nFix the items above, then re-run doctor.")
    return 0 if ok else 1


def _claude_reachable(config: AppConfig) -> bool:
    try:
        import anthropic
        anthropic.Anthropic().models.retrieve(config.ai.model)
        return True
    except Exception:
        return False


def _cmd_reconnect(service: str, paths: LocalPaths) -> int:
    from app.setup import wizard

    handlers = {
        "jira": lambda cfg, env: wizard._setup_jira(cfg, env),
        "slack": lambda cfg, env: wizard._setup_slack_input(cfg, env),
        "google": lambda cfg, env: wizard._setup_google(cfg, env, paths),
        "basecamp": lambda cfg, env: wizard._setup_basecamp(cfg, env, paths),
    }
    if service not in handlers:
        print(f"'{service}' has no connector. Available: "
              + ", ".join(sorted(handlers)))
        return 2
    if not sys.stdin.isatty():
        print("reconnect is interactive; run it from a terminal.")
        return 1

    config = load_config(paths)
    env_updates: dict[str, str] = {}
    handlers[service](config, env_updates)
    save_config(paths, config)
    if env_updates:
        wizard.update_env(paths, env_updates)
    return 0


def _cmd_scheduler(paths: LocalPaths, *, install: bool) -> int:
    from app import scheduler

    config = load_config(paths)
    try:
        if install:
            jobs = scheduler.install(config, log_dir=paths.logs)
            print("✓ Installed scheduled jobs: " + ", ".join(jobs))
            print("  They run via launchd; missed times fire after wake.")
        else:
            jobs = scheduler.uninstall()
            print("✓ Removed scheduled jobs: " + (", ".join(jobs) or "none"))
    except scheduler.SchedulerError as exc:
        print(f"✗ {exc}")
        return 1
    return 0


def _cmd_status(paths: LocalPaths) -> int:
    open_items = read_json(paths.attention / "open.json",
                           {"items": {}}).get("items", {})
    snoozed = read_json(paths.attention / "snoozed.json",
                        {"items": {}}).get("items", {})
    resolved = read_json(paths.attention / "resolved.json",
                         {"items": {}}).get("items", {})
    mock_state = read_json(paths.state / "mock.json", {})

    print(f"Open attention items:    {len(open_items)}")
    print(f"Snoozed:                 {len(snoozed)}")
    print(f"Resolved (last 30 days): {len(resolved)}")
    print(f"Last mock sync:          {mock_state.get('last_sync', 'never')}")
    return 0


def _setup_logging(paths: LocalPaths) -> None:
    paths.ensure()
    logging.basicConfig(
        filename=paths.logs / "work-agent.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


if __name__ == "__main__":
    sys.exit(main())
