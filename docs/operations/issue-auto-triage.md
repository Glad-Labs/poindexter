# Issue Auto-Triage

Three layers, one rule (**cite-or-surface**): a label is applied only when the
issue's content justifies it; otherwise the axis stays bare and is surfaced.

## Layers

1. `triage-on-open.yml` — on issue open, stamps `type` from the CC prefix. Both repos.
2. `findings_alert_router` — stamps `finding` + `findings.<kind>.labels` on filed findings.
3. Weekly sweep (this doc) — applies derivable `type` across the backlog, applies
   `area` only when it can cite a reason, and posts `priority`/`milestone`
   PROPOSALS to Discord for one-tap approval. Never auto-applies priority/milestone.

## Config (app_settings)

- `findings.<kind>.labels` — comma-separated labels for filed findings (e.g. `bug,pipeline`).
- `triage.sweep.enabled` (default `true`), `triage.sweep.surface_channel` (default `discord`).

## Weekly sweep — local scheduled task (Mondays 07:00 America/New_York)

Runs as a Windows scheduled task `Claude Session - triage-sweep`, defined in
`scripts/claude-sessions.ps1` and registered via that script's `schtasks` flow
(same mechanism as the other autonomous agents). It is NOT a remote
`/schedule` routine — the sweep needs local `gh` auth, the gladlabs Discord MCP,
and local DB access, none of which a remote cloud agent has.

**Routing:** issues are content-routed to both repos (OSS → poindexter,
business/internal → glad-labs-stack), and a label is an issue-write, so the
sweep applies the derivable `type` label in BOTH repos. (Code/PRs still go to
`glad-labs-stack` only — but the sweep never touches code.)

The session prompt (authoritative copy lives in `claude-sessions.ps1`) runs
`run_weekly_sweep.py`, then for each gap applies `area` only when
the body cites one subsystem, composes `priority`/`milestone` proposals
(never auto-applied), and posts one Discord digest via the gladlabs
`discord_post` tool. Cite-or-surface throughout.

## Approving proposals

Reply in the Discord thread (or tell the next run) which proposals to apply. The
agent applies approved priority/milestone via `gh issue edit`. Unapproved issues
stay bare — that's the queue, working as intended.
