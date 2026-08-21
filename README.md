# Personal Work Agent

A lightweight personal work agent that runs **entirely on your own computer**.
It periodically checks your work systems (Jira, Slack, Google Drive, Basecamp,
later Confluence) with **your own credentials** and posts one short briefing to
**your own private Slack channel**, answering: what needs me, what did I miss,
who is waiting for me, and what can I safely ignore?

There is no backend, no shared server, no database, no central account system,
and no web dashboard. Each clone of this repository represents exactly one
person; your machine is the application. Everything personal lives in `.local/`
(never committed).

## Current status

**Milestones 1–5 are implemented:**

- pure-local skeleton: atomic JSON state under `.local/`, single-run locking,
  normalised `Activity` model, connector protocol, mock fixtures
- attention engine: deterministic rules (overdue, due soon, direct mention,
  new assignment, new reply), dedup, ranking, open/snoozed/resolved lifecycle
- Claude classification (Anthropic API, strict structured output, local
  30-day classification cache, injection-hardened prompt; skipped gracefully
  when no `ANTHROPIC_API_KEY` is set or `--no-ai` is passed)
- Slack delivery to your private channel via your own incoming webhook
- live **Jira** connector: assigned/overdue/due-soon work, new assignments,
  comments and mentions, incremental polling by `updated` cursor
- live **Slack input**: explicitly configured channels only, per-channel
  cursors, thread replies marked when you contributed to the thread; the
  agent's own output channel, bots, and your own messages are never analysed
- live **Google Drive/Slides**: incremental changes API + comments/replies
  on Docs/Slides/Sheets, one-shot localhost OAuth, tokens in `.local/secrets/`
- live **Basecamp**: todos (assignments, due dates, completion), comments,
  messages via the recordings API, Launchpad OAuth
- launchd scheduler installer (macOS): weekday morning briefing, hourly
  urgent scan, weekly summary — missed times fire after wake

**Not yet implemented**: Confluence (Milestone 9, deliberately last) and
command write-back. Add one connector to your own config at a time and let it
run for a few days before enabling the next.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
git clone git@github.com:Skarper-Ebike/communication-channels-tracker.git
cd communication-channels-tracker

uv sync

uv run python -m app.cli setup

uv run python -m app.cli doctor

uv run python -m app.cli briefing --mock
```

Setup asks for your name/timezone, then optionally connects Jira (URL, email,
API token), your private Slack channel webhook, and your Anthropic API key.
Secrets go to `.local/.env` with owner-only permissions. Finish with:

```bash
uv run python -m app.cli install-scheduler
```

## Commands

| Command | What it does |
| --- | --- |
| `setup` | Interactive first-run setup (writes `.local/config.yaml`) |
| `doctor` | Check local config, state, and connector health |
| `briefing [--mock] [--dry-run]` | Morning briefing |
| `urgent [--mock]` | High-threshold urgent scan (quiet by default) |
| `weekly [--mock]` | Weekly "what keeps hanging around" summary |
| `sync [--mock]` | Poll sources and update state, no briefing |
| `run` / `dry-run` | Full cycle / briefing without writing anything |
| `status` | Open/snoozed/resolved counts and last sync |
| `reconnect <service>` | Re-authenticate a connector (later milestone) |

All commands are per-person — there is no user argument anywhere.

## How it works

```text
local scheduler → work-agent CLI → source APIs (your credentials)
    → normalised activities → deterministic rules (+ Claude later)
    → attention list in .local/attention/*.json → briefing → your Slack
```

Every run: load config → acquire lock → load cursors → poll → rules → merge
attention state → rank → render → write state atomically → exit. Cursors are
only advanced after processing succeeds, so a crash never loses events. Nothing
stays running between jobs.

## Privacy

- Your credentials and state never leave your machine (`.local/` is gitignored).
- The agent can only see what *you* can see — source apps stay the authority.
- Raw content is held in memory for the run; only IDs, cursors, and short
  explanations are persisted.
- No manager dashboards, no cross-user anything. This is the employee's tool.
- Exception (later milestone): content selected for classification is sent to
  the Anthropic API.

## Development

```bash
uv run pytest
```

The full pipeline runs against fixtures in `tests/fixtures/` — no live APIs or
keys needed. See `CLAUDE.md` for architecture rules before contributing.
