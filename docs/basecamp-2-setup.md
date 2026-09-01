# Basecamp 2 → your daily briefing

Once connected, your assigned Basecamp todos appear in the 08:30 briefing —
overdue/due-soon under 🔴 Needs you with deep links; comments and messages
naming you get surfaced; completing a todo auto-clears it; and all Basecamp
activity feeds the 🧭 Team digest. The hourly urgent scan covers newly
overdue work.

Prerequisites: the agent installed and set up (`uv sync`, `setup` done), and
your own Basecamp 2 login.

## 1. App credentials (one of two ways)

- **Easiest:** ask for the shared Skarper app's Client ID + Secret (internal
  password manager — never in this repo). You still authorize with your own
  Basecamp login, so the agent only sees what you can see.
- **Or register your own** at https://launchpad.37signals.com/integrations:
  Register another application → tick product **Basecamp 2** → Redirect URI
  **exactly** `http://127.0.0.1:8484/` (trailing slash matters).

## 2. Connect

```bash
uv run python -m app.cli reconnect basecamp
```

Paste the Client ID and Secret; the browser opens Basecamp's consent page —
click "Yes, I'll allow access". Tokens are stored in
`.local/secrets/basecamp-token.json` (0600, your machine only, auto-refreshed).

## 3. Verify

```bash
uv run python -m app.cli doctor
uv run python -m app.cli briefing --dry-run
```

Expect "✓ Basecamp authenticated as you@…". The first pull covers your open
assigned todos plus 7 days of team events.

## Daily-workflow habits that make it useful

- **Keep todos assigned and dated.** "Assigned to you + due date passed" is
  what triggers Needs You. Unassigned, undated todos are invisible to the
  deterministic rules.
- **Complete todos in Basecamp** — the completion event clears the item from
  your briefing on the next run.
- **Mentions are name-matching** (Basecamp 2 has no structured @-mentions):
  write the person's actual name if you want their agent to flag it.
- Read the briefing, click through, act in Basecamp. Don't reply in the
  agent channel — command write-back isn't built yet.

## Troubleshooting

- "could not open callback port 8484" → free the port, re-run reconnect.
- Auth failures later → `doctor` shows it; `reconnect basecamp` fixes it.
- "no Basecamp 2 account on this login" → you authorized with a login that
  isn't a member of the company Basecamp.
