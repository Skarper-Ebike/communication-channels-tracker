# Mareks's personal agents (mx branch)

Personal setup for Mareks, kept apart from Jakub's Personal Work Agent. Nothing in this folder is used by the `app/` code, and nothing outside `mx/` was changed on this branch.

Both agents are Claude scheduled tasks that run in a Claude Code cloud session with these connectors: Slack, Atlassian (Jira and Confluence), Gmail, Google Drive and Wispr Flow. There is no code to deploy. The prompt files are the whole agent.

## What is here

| Path | What it is |
| --- | --- |
| `jira-log/prompt.md` | The Jira decision log task, word for word as scheduled. |
| `jira-log/runs/` | Copies of each run's proposals, 11 to 12 Nov 2026. |
| `daily-brief/prompt.md` | The daily Slack brief task, word for word as scheduled. |
| `daily-brief/bc.sh` | Basecamp API helper used by the brief (reads secrets from env vars only). |
| `daily-brief/briefs/` | Briefs posted to #mareks-agent, as examples of the format. |

## 1. Jira decision log (active)

Moves dated facts, decisions, specs, costs and commitments from Gmail (Basecamp notification emails included), Slack and Wispr Flow meetings into one "Decision log" comment per Jira ticket.

* Schedule: weekdays 09:44, 12:44 and 15:44, cron `CRON_TZ=Europe/Riga 44 9,12,15 * * 1-5`.
* Untagged items are proposed in #mareks-agent and posted only after you approve in the thread: `post all`, `post 1 3`, `skip 2`, `2 to CRX-123`, `edit 1: text`, `fix 1: instruction`.
* Gmail threads with a Jira key in the subject are posted without asking.
* Entries are combined per thread per ticket, as a short heading plus 3 to 6 bullets.
* It only ever writes that one log comment per ticket. It never changes dates, statuses or anything else, and flags conflicts instead.

## 2. Daily Slack brief (disabled)

Morning brief in the format of Jakub's repo: Needs you, Worth knowing, Team digest, Who's on what, Decisions, Issues, Notes, At a glance.

* Was scheduled weekdays 08:14 London, cron `CRON_TZ=Europe/London 14 8 * * 1-5`.
* Turned off on 11 Nov 2026, because the local Personal Work Agent now posts the morning brief to the same channel.
* Basecamp 2 and 5 are read from notification emails. The API route in `bc.sh` needs `BASECAMP_CLIENT_ID`, `BASECAMP_CLIENT_SECRET` and `BASECAMP_REFRESH_TOKEN`, plus the hosts launchpad.37signals.com, basecamp.com and 3.basecampapi.com allowed. That was never set up.

## Moving to a new account

Scheduled tasks belong to the account and session that created them, so they do not move. Recreate them:

1. In claude.ai, connect Slack, Atlassian, Gmail, Google Drive and Wispr Flow on the new account.
2. Start a Claude Code cloud session and check those connectors appear as tools.
3. Ask Claude to create a recurring scheduled task bound to that session, with the contents of `jira-log/prompt.md` as the prompt and the cron above. Bind it to the session, not to "a new session each run": on the old account, fresh session tasks got no connectors.
4. Optional: do the same for `daily-brief/prompt.md`, only if the local agent is no longer posting.
5. Turn off or delete the old tasks on the old account, so two agents do not post to Slack.

Fixed IDs used by the prompts, all non secret:

* Slack channel #mareks-agent `C0CCV9382SX`, Mareks `U024CC0BNP2`, #cr_ph1 `C0C14CW8X54`
* Atlassian cloudId `f89de60c-ff6e-4fcc-9679-54e3805ba903`, Mareks's account `712020:03454b0d-1b67-4268-a0b0-192f4fdac194`
* Basecamp 2 account `2796974`, Basecamp 5 account `5582671`

## State at the time of this commit (12 Nov 2026)

All state lives in Slack and Jira, not in files. On 12 Nov these proposals in #mareks-agent were waiting for approval:

* Post 11 Nov 15:44: CRX-333 Maxmore MP quotation, CRX-34 v0.2 samples, CRX-180 Skarper Flow plan.
* Post 12 Nov 09:44: CRX-34 IPM motor, CRX-93 ESC vendor terms, CRX-483 lock handover findings (its item 3 was replaced).
* Post 12 Nov 12:44: CRX-305 Dingsen samples, CRX-481 cadence sensor top up, CRX-478 Tetro ECO and BOM review.

Unapproved items lapse after 4 working days.
