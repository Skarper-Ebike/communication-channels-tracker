# Personal Work Agent

A lightweight personal work agent that runs **entirely on your own computer**.
On a schedule it checks your work systems — Jira, Confluence, Slack, Google
Drive/Slides, Basecamp 2 — with **your own credentials**, has Claude judge what actually
needs you, and posts one short briefing to **your own private Slack channel**:
what needs you, what you missed, who's waiting on you, what can be ignored.

The morning briefing also carries a **TEAM DIGEST** — a summary of everything
that happened across your connected sources in the last day, whether you were
involved or not: key notes, decisions made, and issues the team faced. Its
breadth is exactly your own visibility: the Slack channels you monitor, the
Jira projects you watch, the Basecamp events your account can see.

There is no backend, no shared server, no database, no central account system,
no dashboard. Each clone of this repository represents exactly one person.
Everything personal lives in `.local/` (gitignored, never leaves your machine).
The agent never writes to any work system — it only reads and reports.

---

## 1. Install

Requirements: macOS (Linux works except the scheduler installer), Python 3.11+,
[uv](https://docs.astral.sh/uv/) (`brew install uv`).

```bash
git clone git@github.com:Skarper-Ebike/communication-channels-tracker.git
cd communication-channels-tracker
uv sync
```

Try it immediately with fixture data (no accounts needed — runs in an
isolated sandbox, posts nothing):

```bash
uv run python -m app.cli briefing --mock
```

## 2. Gather your credentials

Everything below is **yours personally** — each colleague creates their own.
All of it ends up in `.local/.env` (owner-only permissions) or
`.local/secrets/` on your machine and nowhere else.

### Anthropic API key — `ANTHROPIC_API_KEY` (optional but recommended)

Powers the semantic layer ("James appears to be waiting for your approval").
Without it, briefings still work with the deterministic signals only.

1. [console.anthropic.com](https://console.anthropic.com) → **API Keys** →
   **Create Key** (starts `sk-ant-…`, shown once).
2. API billing is separate from any claude.ai subscription — set up billing
   under **Settings → Billing** and add a monthly spend limit (typical usage
   here is well under $10/month; the classification cache means unchanged
   text is never paid for twice).
3. Cheaper option: set `ai.model: claude-haiku-4-5` in `.local/config.yaml`
   (default is `claude-opus-5`).

### Jira — `JIRA_EMAIL` + `JIRA_API_TOKEN`

1. [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
   → **Create API token**.
2. If offered scopes, `read:jira-work` + `read:jira-user` is all it needs
   (the classic unscoped token also works). The agent never writes to Jira.
3. Tokens expire (up to 1 year). When `doctor` shows Jira failing, run
   `reconnect jira` with a fresh one.

You'll also be asked for your Jira site URL (`https://<org>.atlassian.net`)
and optionally extra project keys/names to watch beyond your own issues.

### Confluence — no new credentials

Confluence Cloud lives on the same Atlassian site and accepts the same email
+ API token as Jira. `setup` (or `reconnect confluence`) just asks for the
site URL and optionally space keys to watch (blank = everything you can see).

### Slack output — `WORK_AGENT_SLACK_WEBHOOK`

Where briefings arrive. A webhook can only ever post to one channel.

1. In Slack, create a **private** channel for yourself (e.g. `#agent-you`).
2. [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** →
   From scratch → name it, pick the workspace.
3. **Incoming Webhooks** → activate → **Add New Webhook to Workspace** →
   select your private channel → **Allow**.
4. Copy the `https://hooks.slack.com/services/…` URL. Treat it as a secret.

### Slack input — `SLACK_USER_TOKEN` (optional)

Lets the agent read the channels you choose for mentions, requests, and
thread replies. Reuses the app from the previous step:

1. App page → **OAuth & Permissions** → **User Token Scopes** → add
   `channels:history`, `groups:history`, `users:read`.
2. **Reinstall to Workspace**, then copy the **User OAuth Token** (`xoxp-…`).
3. Collect the **channel IDs** to monitor (channel → details → About →
   Channel ID), and the ID of your private agent channel — the agent uses
   that to make sure it never reads its own briefings.

DMs are never read (config flag exists but is off and unimplemented by design).

### Google Drive / Slides — `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` (optional)

1. [console.cloud.google.com](https://console.cloud.google.com) → create (or
   pick) a project → **APIs & Services → Library** → enable **Google Drive
   API**.
2. **APIs & Services → OAuth consent screen**: External is fine; add yourself
   as a test user (in Testing mode refresh tokens expire after ~7 days —
   publish the app to make them long-lived).
3. **Credentials → Create Credentials → OAuth client ID → Desktop app**.
   Copy the Client ID and Client Secret.
4. `reconnect google` opens your browser; approval stores a read-only Drive
   token in `.local/secrets/google-token.json`. Scope is `drive.readonly`.

### Basecamp 2 — `BASECAMP_CLIENT_ID` + `BASECAMP_CLIENT_SECRET` (optional)

1. [launchpad.37signals.com/integrations](https://launchpad.37signals.com/integrations)
   → **Register another application**.
2. Name/company/website are cosmetic (the consent screen is only shown to
   you). Tick product **Basecamp 2**.
3. **Redirect URI must be exactly** `http://127.0.0.1:8484/` (trailing slash
   included — the agent's one-shot OAuth callback listens there).
4. Copy the Client ID and Secret; `reconnect basecamp` runs the browser flow
   and stores tokens in `.local/secrets/basecamp-token.json`.

## 3. Connect and verify

Interactive, one question at a time — blank answers always keep existing
values, and each connector is validated live as you add it:

```bash
uv run python -m app.cli setup
```

To add or fix a single connector later without touching the rest:

```bash
uv run python -m app.cli reconnect jira      # or: slack | google | basecamp
```

Then check health and preview a briefing without sending or saving anything:

```bash
uv run python -m app.cli doctor
uv run python -m app.cli briefing --dry-run
```

## 4. Go live

```bash
uv run python -m app.cli briefing            # posts to your Slack channel
uv run python -m app.cli install-scheduler   # macOS launchd jobs
```

The scheduler installs three jobs: **morning briefing** weekdays 08:30,
**urgent scan** hourly (only posts when something crosses the urgent
threshold), **weekly summary** Friday 15:00. Times live in
`.local/config.yaml`; laptops that were asleep fire the job on wake.
Advice: enable one connector, live with it a few days, then add the next.

## Commands

| Command | What it does |
| --- | --- |
| `setup` | Interactive setup for identity + all connectors |
| `reconnect <service>` | Re-do one connector (`jira`, `confluence`, `slack`, `google`, `basecamp`) |
| `doctor` | Live health check of every configured piece |
| `briefing [--mock] [--dry-run] [--no-ai]` | Morning briefing |
| `urgent` | High-threshold scan (quiet unless something is urgent) |
| `weekly` | "What keeps hanging around" summary |
| `sync` | Poll sources and update state, no briefing |
| `status` | Open/snoozed/resolved counts and last sync |
| `install-scheduler` / `uninstall-scheduler` | Manage the launchd jobs |

All commands: `uv run python -m app.cli <command>`. `--mock` uses fixture
data in an isolated sandbox (`.local/mock/`) and never posts anywhere.

## Where your data lives

```text
.local/
├── config.yaml        non-secret config (URLs, channels, thresholds, times)
├── .env               tokens & keys you typed in (chmod 600)
├── secrets/           OAuth tokens: google-token.json, basecamp-token.json (600)
├── state/             per-connector cursors + digest-buffer.json (rolling
│                      48h of truncated excerpts feeding the team digest)
├── attention/         open / snoozed / resolved items (plain JSON)
├── cache/             Claude classification cache (30-day expiry)
└── logs/              run logs (no tokens, no webhook URLs, ever)
```

`.local/` is gitignored; `git pull && uv sync` updates the code and never
touches it. Only IDs, cursors, and short explanations are persisted — the
source systems remain the record. The one exception to "nothing leaves your
machine": text selected for classification is sent to the Anthropic API.
No manager dashboards, no cross-user anything — this is the employee's tool.

## Updating

```bash
git pull
uv sync
```

## What's left to do

Roughly in order:

- **Pilot judgment** — the current gate. Run for two weeks; the success
  test is whether "NEEDS YOU" is low-noise and trusted, not feature count.
- **Command write-back** (phase 2) — typed commands in your agent channel
  (`reply jira ACME-123 …`, `snooze ACME-123 2d`, `ignore …`) parsed
  deterministically, executed with your credentials, logged to
  `.local/audit/`. Never autonomous.
- **Linux scheduler** — systemd timer installer (today: macOS launchd only;
  Linux users get a printed cron/systemd one-liner).
- **Renderer grouping** — one issue can currently surface as several lines
  (mention + request + decision); group per object if the pilot finds it
  noisy.
- **macOS Keychain** for secrets (today: 0600 files, acceptable for MVP).
- **Slack DM opt-in**, richer Google Slides context, and stale-commitment
  tracking in the weekly — all only if pilot feedback asks for them.

Known limitations worth knowing: Basecamp 2 and Google comments have no
structured "mention" data, so "mentions you" there is name-matching plus
Claude's judgment; the agent only runs while your machine is awake (by
design — it catches up from cursors on wake).

## Development

```bash
uv run pytest
```

111 tests, no network or credentials required — connectors are tested
against mocked HTTP transports, and the full pipeline runs on fixtures.
Read `CLAUDE.md` for the architecture rules (atomic state, cursor rule,
no-infrastructure policy) before changing anything.
