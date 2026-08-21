"""Interactive first-run setup.

Writes .local/config.yaml and .local/.env (0600) on this machine only.
Tokens are typed by the user, validated against the source API where
possible, and never echoed back or logged.
"""

from __future__ import annotations

import os
import sys

from app.config import AppConfig, Identity, load_config, save_config
from app.storage import LocalPaths


def run_setup(paths: LocalPaths) -> int:
    print("Personal Work Agent Setup\n")
    if not sys.stdin.isatty():
        print("Setup is interactive; run it from a terminal.")
        return 1

    paths.ensure()
    config = load_config(paths)

    name = _ask(f"Your name [{config.identity.name}]") or config.identity.name
    timezone = _ask(
        f"Timezone (e.g. Europe/London) [{config.identity.timezone}]"
    ) or config.identity.timezone
    config.identity = Identity(name=name, timezone=timezone)

    env_updates: dict[str, str] = {}

    if _yes(f"Configure Jira? [{'Y/n' if not config.connectors.jira.enabled else 'Y/n, currently on'}]"):
        _setup_jira(config, env_updates)

    if _yes("Configure Slack output (private channel webhook)? [Y/n]"):
        _setup_slack(config, env_updates, paths)

    if _yes("Configure Claude (Anthropic API key for classification)? [Y/n]"):
        key = _ask("ANTHROPIC_API_KEY (leave blank to keep existing/env)")
        if key:
            env_updates["ANTHROPIC_API_KEY"] = key

    save_config(paths, config)
    if env_updates:
        update_env(paths, env_updates)

    print("\n✓ Config written to .local/config.yaml")
    print("✓ Secrets written to .local/.env (owner-only permissions)")
    print("\nNext:")
    print("  uv run python -m app.cli doctor")
    print("  uv run python -m app.cli briefing --dry-run")
    print("  uv run python -m app.cli install-scheduler")
    return 0


def _setup_jira(config: AppConfig, env_updates: dict[str, str]) -> None:
    jira = config.connectors.jira
    base_url = _ask(f"Jira URL [{jira.base_url or 'https://example.atlassian.net'}]") \
        or jira.base_url
    email = _ask("Jira email") or os.environ.get("JIRA_EMAIL", "")
    token = _ask("Jira API token (id.atlassian.com/manage-profile/security/api-tokens)")

    jira.base_url = base_url
    if email:
        env_updates["JIRA_EMAIL"] = email
    if token:
        env_updates["JIRA_API_TOKEN"] = token

    effective_email = email or os.environ.get("JIRA_EMAIL", "")
    effective_token = token or os.environ.get("JIRA_API_TOKEN", "")
    if base_url and effective_email and effective_token:
        from app.connectors.jira import JiraConnector

        result = JiraConnector(jira, effective_email, effective_token).validate()
        if result.ok:
            print(f"✓ Jira {result.detail}")
            jira.enabled = True
        else:
            print(f"✗ Jira check failed: {result.detail}")
            print("  Saved anyway — fix and re-run: "
                  "uv run python -m app.cli reconnect jira")
            jira.enabled = True
    else:
        print("✗ Jira left disabled (missing URL, email, or token)")

    projects = _ask("Extra Jira projects to watch beyond your own issues — "
                    "keys or full names, comma-separated (blank for none)")
    if projects:
        jira.projects = [p.strip() for p in projects.split(",") if p.strip()]


def _setup_slack(config: AppConfig, env_updates: dict[str, str],
                 paths: LocalPaths) -> None:
    print("Create an Incoming Webhook for your private channel:\n"
          "  https://api.slack.com/messaging/webhooks")
    webhook = _ask("Webhook URL (leave blank to keep existing)")
    if webhook:
        env_updates[config.slack_output.webhook_env] = webhook
        os.environ[config.slack_output.webhook_env] = webhook

    if _yes("Send a test message now? [Y/n]"):
        from app.output import slack

        try:
            slack.post_message(
                "👋 Personal Work Agent connected. Briefings will arrive here.",
                config)
            print("✓ Test message sent")
        except slack.SlackDeliveryError as exc:
            print(f"✗ {exc}")


def update_env(paths: LocalPaths, updates: dict[str, str]) -> None:
    """Merge KEY=VALUE pairs into .local/.env, preserving unrelated lines."""
    paths.ensure()
    lines: list[str] = []
    if paths.env_file.exists():
        lines = paths.env_file.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    paths.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(paths.env_file, 0o600)


def _ask(prompt: str) -> str:
    return input(f"{prompt}:\n> ").strip()


def _yes(prompt: str) -> bool:
    answer = input(f"{prompt}\n> ").strip().lower()
    return answer in ("", "y", "yes")
