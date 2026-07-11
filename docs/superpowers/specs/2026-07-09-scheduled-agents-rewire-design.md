# Scheduled-agent fleet rewire — off Claude Code OAuth — design

**Issue:** _to file — `Glad-Labs/glad-labs-stack` (operator automation; not OSS-product scope)_
**Date:** 2026-07-09
**Status:** Design approved 2026-07-09; spec under review
**Surfaced by:** operator ran `Claude Session - triage-sweep` manually 2026-07-09;
it exited in ~6s with `Failed to authenticate. API Error: 401 Invalid
authentication credentials`.

## Problem

The nine autonomous "Claude Session" scheduled tasks (`scripts/claude-sessions.ps1`,
Windows Task Scheduler) can no longer authenticate. They were disabled 2026-06-09
as a cost-control measure; re-running one now fails at the first API call.

Diagnosis (2026-07-09, evidence-based):

- **No API key is involved.** `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` /
  `CLAUDE_CODE_OAUTH_TOKEN` are unset at Process, User, and Machine scope. The
  operator's paraphrase ("failed for API key auth") was a misread — the sessions
  authenticate with the **Max subscription OAuth**, not a key.
- **The on-disk OAuth token is dead.** `~/.claude/.credentials.json`
  `claudeAiOauth` shows `subscriptionType = max`,
  `rateLimitTier = default_claude_max_20x`, `expiresAt = 2026-06-19 10:30:07`
  (expired 20 days ago), and — critically — an **empty `refreshToken`**. So the
  headless CLI loads an expired access token, has no way to refresh it, sends it,
  and the API returns 401. The task itself reports success (`LastResult: 0x0`);
  Claude inside it dies.
- **This is not self-healing.** Interactive sessions (this terminal, the phone
  chatroom) keep a live token in memory and refresh it in a trusted context, but
  that refresh is not persisted back to the shared file in a form the unattended
  cron process can reuse. Re-logging-in interactively would revive the sessions
  for a few weeks, then they break again on the next expiry — an indefinite
  treadmill.

This aligns with Anthropic's **2026-06-15 billing split**: programmatic agent use
(the `Task` subagent tool, the Workflow tool) now bills at full API token rates
separately from the Max subscription, and the operator's global settings already
`deny` `Task` and set `disableWorkflows: true`. The unattended-subscription cron
path is the last remaining automated-on-subscription surface, and its credential
decay is that surface closing. **We stop fighting it** rather than nurse an
expiring OAuth token forever.

## Decision

Re-home the sessions that are worth keeping onto execution paths that do not
depend on Claude Code OAuth:

- **Deterministic scripts** (no model) for the mechanical sessions — run free,
  forever, nothing to expire.
- **Local LLM via the existing Ollama fleet** for the two that need bounded
  judgment.
- **Leave the two frontier-model sessions disabled**, defined but unregistered,
  pending a separate metered-API decision.

Triage of the nine (agreed with the operator 2026-07-09):

| Session             | Bucket                  | Rationale                                                                                                                    |
| ------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `dependency-review` | deterministic           | semver-parse patch bumps → checks-green + age gate → `gh pr merge`. No language judgment.                                    |
| `codebase-audit`    | deterministic           | `ruff --fix --select F401,F841` **is** the fix; bandit emits file:line → templated issue.                                    |
| `doc-sync`          | deterministic           | regex-extract path refs → `Test-Path` → unique-basename resolve or flag. Detection is 100% mechanical.                       |
| `claude-md-sync`    | deterministic           | DB-count half is already a script; migration line is **extraction** of the migration docstring, not generation.              |
| `triage-sweep`      | deterministic           | already runs `run_weekly_sweep.py`; area label = keyword→label; Discord digest = template.                                   |
| `alert-triage`      | local LLM               | query is deterministic; "false-positive vs real failure" is bounded diagnosis over probe code + history.                     |
| `test-health`       | local LLM               | detecting failures is deterministic; fixing a _simple_ one is bounded judgment, gated by a deterministic re-run.             |
| `issue-resolver`    | **disabled** (frontier) | read issue → find code → correct targeted fix is real engineering; a weak fix is review noise. Worth metered API or nothing. |
| `test-expansion`    | **disabled** (drop)     | auto-generated tests are low-signal and add review load against ~11,440 existing tests.                                      |

## Architecture

### Reuse the harness, swap the payload

`claude-sessions.ps1`'s `Run-Session` already carries the load-bearing machinery:
per-session **git worktree isolation** (off freshly-fetched `origin/main`), the
**`node_modules` junction** that lets the husky pre-commit hook resolve its tools
in a fresh worktree, **timeout/kill**, and **logging** — with inline comments
citing real bugs (branch cross-contamination observed 2026-05-30). None of that is
model-specific and all of it is still needed for the sessions that commit and open
PRs.

Change: the hardcoded
`Start-Process claude.exe -p "<prompt>" --model … --dangerously-skip-permissions`
becomes a per-session **`Command`** invocation. Each session definition gains:

- `Command` — the executable + args to run inside the worktree (e.g.
  `python scripts/ops-sessions/dependency_review.py`). Replaces the `Prompt`
  field for rewired sessions.
- `NeedsWorktree` — bool. `true` for the commit/PR sessions (`codebase-audit`,
  `doc-sync`, `claude-md-sync`, `test-health`); `false` for the ones that only
  read + act via API (`dependency-review` merges PRs, `alert-triage` files
  issues, `triage-sweep` edits labels + posts Discord) — these skip worktree
  setup entirely and run from the main checkout read-only.
- `Enabled` — bool, default `true`. The two frontier sessions set `false` and are
  skipped by `Install-Sessions`; their definitions (including the original
  `Prompt`) stay in the file for the future metered decision.

The 2 frontier sessions keep `claude.exe` as their `Command` but, being
`Enabled=$false`, never register.

### New `scripts/ops-sessions/` Python package

One module per rewired session plus a shared `_common.py`. Python for all seven:
testable, cross-platform, and matches the codebase (`triage-sweep` and
`claude-md-sync` already shell out to Python). PowerShell remains only as the
Task Scheduler harness.

`_common.py` provides:

- `resolve_database_url()` — delegates to the existing
  `brain.bootstrap.resolve_database_url()` (bootstrap.toml → env chain). No new
  config surface.
- `ollama_complete(prompt, *, model, system=None, format=None, timeout)` — thin
  `httpx` POST to `http://localhost:11434/api/chat` (host + model read from env
  with sane defaults; see Settings). Returns text, or raises a typed
  `OllamaUnavailable` on connection failure. **No LiteLLM, no app bootstrap** —
  one HTTP call, not a container.
- `gh(*args)` / `git(*args)` — subprocess wrappers with structured logging and
  non-zero-exit surfacing.
- `notify_ops(message, *, level)` — Discord ops webhook for graceful-failure
  surfacing (reuses `discord_ops_webhook_url` from bootstrap.toml).
- `session_logger(name)` — writes the same `~/.poindexter/logs/claude-sessions/`
  log the harness already tails, so existing observation habits keep working.

### Local-LLM calls hit Ollama directly

`alert-triage` and `test-health` each make **one** structured Ollama call per unit
of work (per noisy alert; per simple failure), through `ollama_complete`. Direct
Ollama over LiteLLM for these ops calls: they are not content-pipeline calls, do
not need cost attribution or Langfuse tracing, and must not drag the full
`SiteConfig`/DI/DB-pool bootstrap into a 15-minute cron script. _(If we later want
these traced, swapping the helper's URL to the LiteLLM proxy is a one-line change
— the call sites don't move.)_

## Session specifications

Each script exits `0` on success (including the legitimate "nothing to do"),
writes a one-line summary to its log, and surfaces hard failures via `notify_ops`
plus a non-zero exit. Output contracts match today's behavior so nothing downstream
changes.

### `dependency_review.py` (deterministic, no worktree)

1. `gh pr list --repo Glad-Labs/glad-labs-stack --search 'is:pr is:open author:app/dependabot' --json number,title,headRefName,createdAt,statusCheckRollup`.
2. Keep rows where: title is a **patch** bump (third semver component changed,
   `major.minor` unchanged — parsed, not regex-guessed), **all** checks green,
   and `createdAt` older than 6h.
3. For each: `gh pr review --approve` then
   `gh pr merge --squash --delete-branch --auto`.
4. Log merged / skipped + reason. No LLM, no commit, no worktree.

### `codebase_audit.py` (deterministic, worktree)

1. `ruff check --fix --select F401,F841 <targets>` inside the worktree; stage the
   mechanical fixes.
2. `bandit -r <targets> -q -ll -f json`; for each MEDIUM/HIGH,
   `gh issue create --repo Glad-Labs/glad-labs-stack --label security` with a
   **templated** title and body (file:line, test id, snippet). Security issues
   stay in the private repo.
3. If ruff staged anything: commit on the session branch, push, open a PR to
   `glad-labs-stack --base main`. Else exit clean.

### `doc_sync.py` (deterministic, worktree)

1. Extract path-like references from `CLAUDE.md` (`src/…`, `docs/…`,
   `infrastructure/…`, `scripts/…`, `brain/…`).
2. `Test-Path`/`os.path.exists` each. For a break, search the tree for the
   basename: **exactly one** match → auto-correct the line; zero or many → leave
   a flag comment for human resolution (no guess).
3. Never touch numeric stat counts (the CI `sync-claude-md.yml` Action owns
   those). PR if changed.

### `claude_md_sync.py` (deterministic, worktree)

1. Run the existing `scripts/sync_claude_md_db_stats.py` through the main
   checkout's poetry env (it edits via its own `__file__` path).
2. Compare CLAUDE.md's "Latest as of" migration line to the newest timestamped
   file under `services/migrations/`; if newer, update the line by **extracting
   the migration's docstring first clause** (verbatim, not summarized).
3. Do not recompute repo file-stat counts. `git commit --no-verify` (the prettier
   hook mangles CLAUDE.md glob tokens), push, PR.

### `triage_sweep.py` (deterministic, no worktree)

1. Run `scripts/triage/run_weekly_sweep.py` (applies content-derived `type`
   labels, prints per-repo gap + milestone JSON).
2. For each gap missing `area`: apply the single best area label **only** when the
   body clearly cites one subsystem (keyword rules over
   backend/frontend/testing/infra/monitoring/pipeline/monetization); cross-cutting
   → leave bare.
3. Compose one-line priority/milestone **proposals** (never applied) from
   blocking/impact signals + the repo's milestone list.
4. Post one Discord digest via the ops webhook: "Weekly triage: N proposals",
   each issue + repo + proposed labels + one-line rationale. Cite-or-surface:
   never invent a value not grounded in the issue body.

### `alert_triage.py` (local LLM, no worktree)

1. `SELECT alertname, severity, COUNT(*), … FROM alert_events WHERE received_at >
NOW() - INTERVAL '24 hours' GROUP BY … ORDER BY COUNT DESC` (via
   `resolve_database_url()`).
2. For each `alertname` firing > 5×: load the most recent `dispatch_result` and
   the probe source (`brain/<name>_probe.py` if present) and make **one**
   `ollama_complete` call with `format=json` → `{classification: "probe_bug" |
"real_failure", reason, suspect_file}`.
3. `probe_bug` → `gh issue create --repo Glad-Labs/glad-labs-stack` with the
   reproduction + suspect file. `real_failure` → leave it (operator sees it on the
   morning brief). One issue per real probe bug.
4. `OllamaUnavailable` or DB-connect failure → `notify_ops(level=warn)` + exit
   non-zero. No silent pass.

### `test_health.py` (local LLM, worktree)

1. `poetry run pytest tests/unit/ -q --tb=short -p no:cacheprovider
--continue-on-collection-errors`. **Ignore** collection errors (E, known
   path-depth quirk); act only on real **failures** (F).
2. For each failure in a `tests/` file only: send the failing test source + the
   traceback to `ollama_complete` asking for a **minimal patch to the test**
   (never production code).
3. **Apply → re-run that specific test.** Green → keep. Not green → revert the
   patch and leave a `# FIXME:` note. This deterministic re-run is the guardrail
   that makes a weak local model safe: it can only ever land a fix that actually
   passes.
4. If anything landed: commit, push, PR. Else exit clean.

## Safety & failure behavior

- **test-health re-run gate** (above) is the core safeguard — the model proposes,
  pytest disposes.
- **Graceful stack-down.** DB-dependent (`alert-triage`, `claude-md-sync`) and
  Ollama-dependent (`alert-triage`, `test-health`) scripts treat connection
  failure as `notify_ops(warn)` + clean non-zero exit — never a crash, never a
  false success. (The PC-reboot case is common; see the brain-daemon-stale note.)
- **No secrets in scripts.** DB URL + webhook resolve through the existing
  bootstrap chain; nothing new on disk.
- **Fail-loud, no silent defaults.** A missing required input (e.g. no DB URL)
  notifies + exits non-zero, consistent with `feedback_no_silent_defaults`.

## Settings

Two new env-with-default knobs read by `_common.py` (not app_settings — these are
host-side ops-script config, resolved before any DB is reachable):

- `OPS_OLLAMA_URL` — default `http://localhost:11434`.
- `OPS_OLLAMA_MODEL_TRIAGE` / `OPS_OLLAMA_MODEL_TESTFIX` — small local defaults
  (e.g. a 3B-class model for classification; a local coder model for test fixes),
  tunable without code change.

## Testing & docs

- **Contract tests** (pure functions, no network; location per plan — the ops
  scripts sit outside the `cofounder_agent` package, so the plan pins whether the
  tests live in a `scripts/ops-sessions/tests/` module or import via a path shim
  under the existing suite): semver patch-bump detection, CLAUDE.md ref
  extraction, basename-match resolution, pytest-failure parsing, the alert
  classification prompt/JSON-parse round-trip, keyword→area-label mapping. Per
  `feedback_docs_and_tests_default`.
- **Docs:** replace the CLAUDE.md "Scheduled agents — all DISABLED" section with
  the new two-tier reality (deterministic + local-LLM live; two frontier disabled)
  and add a short `docs/operations/scheduled-agents.md` runbook (what each does,
  where logs land, how to enable/disable, the frontier-metered decision).

## Out of scope (YAGNI)

- No LiteLLM dependency for ops calls.
- No new Grafana board for ops-run history (file logs + Discord stay; a small
  run-history panel is a _possible later add_, not now — noted for
  `feedback_grafana_everything` follow-up).
- No change to the worktree/junction mechanics that already work.
- No metered-API wiring for `issue-resolver` — separate decision, separate spec.

## Future / deferred

- **Metered `issue-resolver`** behind a hard `cost_guard` cap on a frontier model,
  if the operator later decides an autonomous bug-fix PR is worth per-token spend.
  It is the one session where quality genuinely frees the operator.
- Optional ops-run-history Grafana panel.
