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

**Milestones 1 and 2 are implemented** — the pure-local skeleton and the
deterministic attention engine, driven by mock fixture data:

- atomic JSON state under `.local/` (no database, crash-safe writes)
- single-run file locking
- normalised `Activity` model and connector protocol
- `MockConnector` with realistic fixtures
- deterministic rules: overdue, due soon, direct mention, new assignment, new reply
- deduplication with a bounded, pruned recent-event set
- ranking, open/snoozed/resolved lifecycle, resolved-history pruning
- morning / urgent / weekly renderers
- comprehensive tests

**Not yet implemented** (later milestones): Claude classification, Slack
delivery, live Jira/Slack/Google Drive/Basecamp/Confluence connectors, OAuth,
and the launchd/systemd scheduler installer. Commands that need them say so.

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
