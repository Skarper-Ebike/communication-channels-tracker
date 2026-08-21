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

    if _yes("Configure Slack input (monitor selected channels)? [Y/n]"):
        _setup_slack_input(config, env_updates)

    if _yes("Configure Google Drive/Slides? [Y/n]"):
        _setup_google(config, env_updates, paths)

    if _yes("Configure Basecamp? [Y/n]"):
        _setup_basecamp(config, env_updates, paths)

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
    email = _ask("Jira email (blank to keep existing)") \
        or os.environ.get("JIRA_EMAIL", "")
    token = _ask("Jira API token from id.atlassian.com/manage-profile/"
                 "security/api-tokens (blank to keep existing)")

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


def _setup_slack_input(config: AppConfig, env_updates: dict[str, str]) -> None:
    import os

    print("Slack input needs a user token (xoxp-…) with read scopes.\n"
          "On your existing Slack app: OAuth & Permissions → User Token Scopes\n"
          "→ add channels:history, groups:history, users:read → reinstall app\n"
          "→ copy the User OAuth Token.")
    token = _ask("SLACK_USER_TOKEN (leave blank to keep existing)")
    if token:
        env_updates["SLACK_USER_TOKEN"] = token
        os.environ["SLACK_USER_TOKEN"] = token

    channels = _ask("Channel IDs to monitor, comma-separated — open channel "
                    "details → About → Channel ID (blank to keep existing)")
    if channels:
        config.connectors.slack.channels = [
            c.strip() for c in channels.split(",") if c.strip()]

    output_id = _ask("Channel ID of your private agent channel (so the agent "
                     f"never reads its own output) "
                     f"[{config.slack_output.channel_id or 'unset'}]")
    if output_id:
        config.slack_output.channel_id = output_id

    effective = token or os.environ.get("SLACK_USER_TOKEN", "")
    if effective and config.connectors.slack.channels:
        from app.connectors.slack import SlackConnector

        result = SlackConnector(config.connectors.slack, effective).validate()
        print(f"{'✓' if result.ok else '✗'} Slack input {result.detail}")
        config.connectors.slack.enabled = result.ok
    else:
        print("✗ Slack input left disabled (missing token or channels)")


def _setup_google(config: AppConfig, env_updates: dict[str, str],
                  paths: LocalPaths) -> None:
    import os

    from app.connectors import google_drive
    from app.setup.oauth import OAuthApp, OAuthError, TokenStore, run_local_flow

    print("Google needs an OAuth client (type: Desktop app) from\n"
          "  https://console.cloud.google.com/apis/credentials\n"
          "with the Google Drive API enabled on the project.")
    client_id = _ask("GOOGLE_CLIENT_ID (blank to keep existing)") \
        or os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = _ask("GOOGLE_CLIENT_SECRET (blank to keep existing)") \
        or os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        print("✗ Google left disabled (missing client credentials)")
        return
    env_updates["GOOGLE_CLIENT_ID"] = client_id
    env_updates["GOOGLE_CLIENT_SECRET"] = client_secret

    try:
        tokens = run_local_flow(OAuthApp(
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url=google_drive.TOKEN_URL,
            client_id=client_id, client_secret=client_secret,
            scopes=google_drive.SCOPES,
            extra_auth_params={"access_type": "offline", "prompt": "consent"},
        ))
    except OAuthError as exc:
        print(f"✗ Google authentication failed: {exc}")
        return
    TokenStore(paths.secrets / "google-token.json", google_drive.TOKEN_URL,
               client_id, client_secret).save(tokens)
    config.connectors.google_drive.enabled = True
    print("✓ Google connected")


def _setup_basecamp(config: AppConfig, env_updates: dict[str, str],
                    paths: LocalPaths) -> None:
    import os

    from app.connectors import basecamp
    from app.setup.oauth import OAuthApp, OAuthError, TokenStore, run_local_flow

    print("Basecamp needs an app from\n"
          "  https://launchpad.37signals.com/integrations\n"
          "with product 'Basecamp 2' ticked and EXACTLY this Redirect URI:\n"
          "  http://127.0.0.1:8484/")
    client_id = _ask("BASECAMP_CLIENT_ID (blank to keep existing)") \
        or os.environ.get("BASECAMP_CLIENT_ID", "")
    client_secret = _ask("BASECAMP_CLIENT_SECRET (blank to keep existing)") \
        or os.environ.get("BASECAMP_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        print("✗ Basecamp left disabled (missing client credentials)")
        return
    env_updates["BASECAMP_CLIENT_ID"] = client_id
    env_updates["BASECAMP_CLIENT_SECRET"] = client_secret

    try:
        tokens = run_local_flow(OAuthApp(
            auth_url=f"{basecamp.LAUNCHPAD}/authorization/new",
            token_url=basecamp.TOKEN_URL,
            client_id=client_id, client_secret=client_secret,
            extra_auth_params={"type": "web_server"},
            extra_token_params={"type": "web_server"},
            callback_port=8484,
        ))
    except OAuthError as exc:
        print(f"✗ Basecamp authentication failed: {exc}")
        return
    TokenStore(paths.secrets / "basecamp-token.json", basecamp.TOKEN_URL,
               client_id, client_secret,
               extra_refresh_params={"type": "refresh"}).save(tokens)
    config.connectors.basecamp.enabled = True
    print("✓ Basecamp connected")


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
