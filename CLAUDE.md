# CLAUDE.md — Personal Work Agent

Fully local, per-employee work-briefing agent. One repo, distributed to
everyone; each clone represents exactly one person. A scheduled process starts,
polls the user's own accounts, updates small JSON files, posts one Slack
briefing, and exits. The user's machine is the application.

## Architecture rules (non-negotiable)

- **No infrastructure.** No database (not even SQLite), no server, no web
  framework, no queue, no Docker, no LangChain, no agent framework. The default
  answer to introducing infrastructure is no. Prefer: local file over database,
  scheduled process over server, source-API fact over Claude inference,
  ID+metadata+link over stored content, user-confirmed action over automatic
  action, better prioritisation over another integration.
- **`.local/` is the user.** All config, secrets, state, cache, and logs live
  in `.local/`, which is gitignored and never committed. No per-user files in
  the shared codebase, no user argument on any command.
- **State writes are atomic.** Always `app.storage.atomic_write_json` (tmp →
  flush → fsync → `os.replace`). A crash mid-write must leave the previous
  valid state intact. Corrupt files are quarantined to `.corrupt`, never
  crashed on.
- **Cursor rule.** Never advance a connector cursor before processing succeeds.
  Fetch → normalise → analyse → update attention → write files → save cursor.
  A failed connector keeps its old cursor and degrades the briefing visibly.
- **One run at a time.** Every pipeline run holds `.local/work-agent.lock`
  (`app.lock.RunLock`); a second concurrent run exits safely.
- **Deterministic before AI.** Anything the source API states (due dates,
  assignees, mentions, completion) is decided in `app/attention/rules.py`.
  Claude (Milestone 3+) only handles semantic ambiguity, gets a
  prompt-injection-hardened system prompt, returns strict JSON, and never
  calls tools or performs actions.
- **No autonomous mutations.** The agent never replies, comments, transitions,
  or completes anything on its own. Future writeback is explicit typed commands
  with a deterministic parser, executed with the user's credentials and logged
  to `.local/audit/actions.jsonl`.
- **No surveillance.** No manager dashboards, cross-user reporting, or
  productivity scoring — ever. This is the employee's private assistant.
- **Secrets never in the repo,** and never sent to Claude, never logged.
- **Versioned local files.** Every persisted schema carries `{"version": N}`;
  evolve with migration functions (`app.storage.migrate`), never by asking
  users to hand-edit state.

## Where things live

- `app/cli.py` — argparse entrypoint (`uv run python -m app.cli <cmd>`)
- `app/runner.py` — the single-run pipeline (lock → poll → rules → merge →
  rank → render → persist)
- `app/models.py` — `Activity` (in-memory only, per run) and `AttentionItem`
  (persisted); attention keys are `source:object_id:category`
- `app/storage.py`, `app/lock.py` — atomic JSON + paths, file lock
- `app/connectors/` — `base.py` protocol, `mock.py` (fixtures in
  `tests/fixtures/mock_activities.json`, with `*_offset_hours` relative dates)
- `app/attention/` — `rules.py` (deterministic categories), `dedupe.py`
  (bounded recent-event-ids), `ranking.py` (edit the table, keep it plain),
  `lifecycle.py` (open/snoozed/resolved JSON, 30-day resolved retention)
- `app/output/renderer.py` — morning / urgent / weekly text renderers

## Status

Milestones 1–2 done (mock pipeline + attention engine). Next, in order:
M3 Claude classification → M4 Slack output → M5 Jira → M6 Slack input →
M7 Google Drive → M8 Basecamp → M9 Confluence. Add one connector at a time
and let it run for days before the next. Scheduler installer (launchd/systemd)
ships alongside live connectors.

## Development

```bash
uv sync
uv run pytest
uv run python -m app.cli briefing --mock
```

Tests must stay green and must not require network, keys, or a configured
`.local/` (they use `WORK_AGENT_HOME` to isolate state). The full pipeline must
always work in `--mock` mode. A quiet agent beats a noisy one: when tuning
thresholds or renderers, the value is omission.
