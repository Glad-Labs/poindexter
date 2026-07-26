# Scheduled agents (Windows Task Scheduler)

Autonomous maintenance sessions that run on the operator's PC via Windows Task
Scheduler, defined in [`scripts/claude-sessions.ps1`](../../scripts/claude-sessions.ps1).
They open PRs against `Glad-Labs/glad-labs-stack` (the code source of truth;
poindexter is a force-rebuilt mirror that can't take code) and never commit to
`main` directly. Issues they file are content-routed (OSS → poindexter,
business/internal → glad-labs-stack).

## Two tiers + a disabled pair

As of the 2026-07-09 rewire, the sessions no longer run through Claude Code on
the Max subscription. Seven run as plain Python scripts under
[`scripts/ops_sessions/`](../../scripts/ops_sessions/); two that need a frontier
cloud model are kept **disabled** pending a metered-API decision.

| Session             | Tier                    | Schedule    | What it does                                  | Output                       |
| ------------------- | ----------------------- | ----------- | --------------------------------------------- | ---------------------------- |
| `dependency-review` | deterministic           | daily 06:30 | merge green patch-bump dependabot PRs         | `gh pr merge`                |
| `codebase-audit`    | deterministic           | Wed 02:00   | `ruff --fix` F401/F841                        | lint PR                      |
| `doc-sync`          | deterministic           | Fri 05:00   | verify/repair CLAUDE.md path references       | PR (or flag)                 |
| `claude-md-sync`    | deterministic           | daily 02:30 | DB-count sync + migration-drift surface       | PR / Discord note            |
| `triage-sweep`      | deterministic           | Mon 07:00   | weekly sweep + keyword area-labels            | label edits + Discord digest |
| `alert-triage`      | local LLM               | daily 01:00 | classify noisy `alert_events` (bug vs real)   | probe-bug issues             |
| `test-health`       | local LLM               | daily 03:00 | fix simple test failures behind a re-run gate | PR                           |
| `issue-resolver`    | **disabled** (frontier) | daily 05:00 | fix one scoped open issue                     | —                            |
| `test-expansion`    | **disabled** (drop)     | daily 04:00 | add tests to low-coverage files               | —                            |

The deterministic five make **zero model calls**. The two local-LLM sessions
make one structured [Ollama](http://localhost:11434) call per unit of work.

> **`codebase-audit` lost its bandit half on 2026-07-17.** It used to file one
> GitHub issue per bandit finding. That produced 91 issues — every one examined
> was a false positive (#2594-#2623, all closed by #2644) — which buried the 18
> genuine engineering issues three pages deep in the backlog. Bandit is a
> textual matcher with no dataflow analysis, so it cannot distinguish a real
> injection from the sanctioned `services/` pattern (hardcoded identifier +
> asyncpg bind params); that makes it a bad issue-filer and a fine ratchet. It
> now runs in CI as [`scripts/ci/bandit_lint.py`](../../scripts/ci/bandit_lint.py)
> against `scripts/ci/bandit_baseline.json`, blocking a **net-new** finding at PR
> time and filing nothing. The dedup logic added in #2645 went with it: nothing
> is filed, so nothing can be re-filed.

## Why the rewire

The sessions authenticated with the **Max subscription OAuth**, not an API key.
That token decayed: by 2026-07-09 the on-disk access token had expired (2026-06-19)
and its refresh token was blank, so headless runs sent a dead token and got
`401 Invalid authentication credentials`. Re-logging-in would revive them for a
few weeks, then break again — and it leans on exactly the automated-on-subscription
path that Anthropic's 2026-06-15 billing split is closing (programmatic agent use
bills at full API rates). So the worthwhile sessions were re-homed onto
deterministic scripts + the local Ollama fleet, which never expire and never bill.
Full rationale: [`docs/superpowers/specs/2026-07-09-scheduled-agents-rewire-design.md`](../superpowers/specs/2026-07-09-scheduled-agents-rewire-design.md).

## How a session runs

`Run-Session` reads three fields per session:

- **`Command`** — the payload. Launched under the **main checkout's** poetry env
  (`{mainPkg}` = `<repo>/src/cofounder_agent`, which owns the provisioned venv) so
  tooling resolves; the script self-locates its repo root from `{runDir}` and
  reuses `sys.executable` for its own `ruff`/`pytest`/etc. This is deliberate — a
  fresh worktree has **no** venv of its own, so nothing may run `poetry run` from
  inside it.
- **`NeedsWorktree`** — `$true` for sessions that commit + open PRs (they get an
  isolated git worktree off the latest `origin/main`, with the shared checkout's
  `node_modules` junctioned in for the husky hook); `$false` for read/act-via-API
  sessions (`dependency-review`, `triage-sweep`, `alert-triage`) that merge PRs,
  file issues, or edit labels and never mutate the local checkout.
- **`Enabled`** — `$false` keeps the definition but skips registration.

### The CWD trap (stack#2809)

Those first two fields pull in opposite directions: the process CWD is the
**shared checkout's** package dir (that's where the venv is), while a
`NeedsWorktree` session commits inside **its worktree**. So every `git`/`gh`
call in a session script must pass an explicit `cwd` — anything that infers the
repo from the ambient CWD reads the shared checkout instead.

`gh pr create` is exactly that kind of command, and it was the one call in the
chain missing a `cwd`. It failed nightly for six weeks (2026-06-10 → 07-26),
reporting whatever state the shared checkout happened to be in — `must be on a
branch named differently than "main"`, or `you must first push the current
branch` — while the session's own worktree branch was committed and pushed
perfectly well. Sessions log success unconditionally no longer: use
`_common.commit_and_open_pr(...)`, which resolves the branch from the worktree,
pins it with an explicit `--head`, checks every step's return code, and returns
`None` after a `notify_operator` warning if any step fails.

Symptom to watch for: orphaned `auto/<session>-<stamp>` branches on `origin`
with no PR attached. `git ls-remote --heads origin 'refs/heads/auto/*'` lists
them; anything older than a day with no matching PR means the PR step is broken
again.

## Operating it

```powershell
.\claude-sessions.ps1 -List        # show registered tasks + definitions (enabled/disabled, script/claude.exe)
.\claude-sessions.ps1 -Install     # (re)register every ENABLED session; disabled ones are skipped
.\claude-sessions.ps1 -Uninstall   # remove all session tasks
.\claude-sessions.ps1 -Session dependency-review   # run one session now
```

`-Install` skips disabled sessions rather than unregistering them. If a session
was previously enabled and is now disabled, run `-Uninstall` then `-Install` for a
clean slate.

- **Logs:** `~/.poindexter/logs/claude-sessions/<session>-<timestamp>.log`
  (+ `.err`) — the same location the old sessions used.
- **Graceful failure:** DB- or Ollama-dependent sessions treat a connection
  failure (e.g. the stack is down after a reboot) as a `notify_operator` warning
  (→ Discord) plus a clean non-zero exit — never a crash, never a false success.
  The same rule covers the commit → push → PR chain: a session that opened no PR
  exits non-zero and notifies, and `run-session.sh` records that rc in the log
  (`session <name> complete (rc=N)`) rather than always signing off green.

## Configuration

Host-side env knobs read by [`scripts/ops_sessions/_common.py`](../../scripts/ops_sessions/_common.py)
(resolved before any DB is reachable, so not `app_settings`):

| Var                        | Default                  | Purpose                          |
| -------------------------- | ------------------------ | -------------------------------- |
| `OPS_OLLAMA_URL`           | `http://localhost:11434` | local Ollama endpoint            |
| `OPS_OLLAMA_MODEL_TRIAGE`  | `llama3.2:3b`            | `alert-triage` classifier model  |
| `OPS_OLLAMA_MODEL_TESTFIX` | `qwen2.5-coder:7b`       | `test-health` fix-proposer model |

DB URL and the Discord webhook resolve through the existing
`~/.poindexter/bootstrap.toml` chain — no new secrets on disk.

## Deferred: metered `issue-resolver`

`issue-resolver` is the one session where a good autonomous bug-fix PR genuinely
frees the operator, but only a frontier model produces fixes worth reviewing. It
stays disabled until there's a decision to run it metered on the API behind a hard
`cost_guard` cap. `test-expansion` is disabled as low-value (auto-generated tests
add review load without catching bugs). Re-enabling either is an `Enabled = $true`
flip in `claude-sessions.ps1` — but for `issue-resolver` that also means wiring a
cloud model + spend cap first.
