"""CLI entrypoint: `uv run python -m app.cli <command>`.

No user argument anywhere — the clone represents exactly one person.
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.config import AppConfig, Identity, load_config, save_config
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
        return _cmd_setup(paths)
    if args.command == "doctor":
        return _cmd_doctor(paths)
    if args.command == "status":
        return _cmd_status(paths)
    if args.command == "reconnect":
        print(f"'{args.service}' is not connected yet — live connectors arrive "
              f"in Milestones 5+. Nothing to reconnect.")
        return 0
    if args.command in ("install-scheduler", "uninstall-scheduler"):
        print("Local scheduling (launchd/systemd) ships once live connectors "
              "exist. For now, run briefings manually:\n"
              "  uv run python -m app.cli briefing --mock")
        return 0

    return _cmd_pipeline(args, paths)


def _cmd_pipeline(args: argparse.Namespace, paths: LocalPaths) -> int:
    from app import runner  # deferred so --help stays instant

    command = "briefing" if args.command in ("run", "dry-run") else args.command
    dry_run = args.dry_run or args.command == "dry-run"

    if not args.mock:
        print("No live connectors are implemented yet (Milestones 5+). "
              "Use --mock to run against local fixture data:\n"
              f"  uv run python -m app.cli {args.command} --mock")
        return 2

    try:
        result = runner.run(command, mock=True, dry_run=dry_run,
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


def _cmd_setup(paths: LocalPaths) -> int:
    print("Personal Work Agent Setup\n")
    if not sys.stdin.isatty():
        print("Setup is interactive; run it from a terminal.")
        return 1

    config = load_config(paths) if paths.config_file.exists() else AppConfig()

    name = input(f"Your name [{config.identity.name}]:\n> ").strip()
    timezone = input(
        f"Timezone (e.g. Europe/London) [{config.identity.timezone}]:\n> "
    ).strip()
    config.identity = Identity(
        name=name or config.identity.name,
        timezone=timezone or config.identity.timezone,
    )
    save_config(paths, config)

    print("\n✓ Local config written to .local/config.yaml")
    print("✓ Local state directories created")
    print("\nConnectors (Jira, Slack, Google Drive, Basecamp) arrive in later")
    print("milestones. Try the mock pipeline now:")
    print("  uv run python -m app.cli briefing --mock")
    return 0


def _cmd_doctor(paths: LocalPaths) -> int:
    print("PERSONAL WORK AGENT\n")
    ok = True

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

    from app.connectors.mock import MockConnector
    check("mock fixtures", MockConnector().validate().ok)

    for name in ("Jira", "Slack", "Google Drive", "Basecamp", "Claude"):
        print(f"– {name}: not configured (arrives in later milestones)")

    print("\nReady." if ok else "\nFix the items above, then re-run doctor.")
    return 0 if ok else 1


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
