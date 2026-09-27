# Outline — the field guide

Each chapter is **one operating rule + the measured failure that earned it +
the guardrail that now enforces it.** Facts below are pulled from the repo as
of 2026-09-26 and name their source. When a chapter is drafted, every number
in it must trace to one of these sources, or be re-measured and the source
updated. That is the book's own rule applied to the book.

**v1** marks the 8-chapter cut (+ Appendix A) to write only after the waitlist
says it's wanted. It ships to Pro subscribers as a perk (see `README.md`).
`sample-chapter.md` is chapter 4, drafted in full.

Voice: first person, flat register, the story post's tone. Real numbers, real
rough edges. The reader should finish each chapter able to add one guardrail
to their own repo the same evening.

---

## Part I — The job

### 1. What "directing" actually means **(v1)**

The human's job is not reviewing code. It's catching misses in _design_: the
case nobody considered, the decision that was locally fine and globally wrong.
Seeing the gap and saying it precisely enough that the agent closes it.
Throughput at this scale makes line-by-line review a fiction, and the chapter
says so plainly, then shows what replaces it.

- ~10,500 commits, ~700,000 lines of Python, built in a 9pm–1am window by
  directing Claude Code sessions — `marketing/launch/copy/01-story-post.md`
- "I correct misses in design far more often than errors in code" — same source
- The honest objections and the answers that held up —
  `marketing/launch/copy/05-faq-crib-sheet.md`

### 2. The test suite is the institutional memory **(v1)**

Agents have no memory of last month's regression and no embarrassment about
reintroducing it. A hard test gate on every merge is the only memory a solo
operation has. Without it, agent-built software "isn't a codebase, it's a
pile."

- 18,000+ unit tests gating every merge — story post; README "What works today"
- The test count in CLAUDE.md read "~11,440" for two months while the real
  suite was ~5,800 functions larger: memory that isn't measured drifts too —
  CLAUDE.md, Key Numbers
- What tests can't see: "nothing in CI tells you that the thing you carefully
  built shouldn't exist" — story post

### 3. Write the rules where the agent reads them — and keep them true **(v1)**

CLAUDE.md is the operating manual every session treats as ground truth, which
makes a stale line more dangerous than a missing one. The chapter covers what
belongs in it, how claims rot, and how counts are kept true mechanically.

- A false line ("dev_diary has no graph_def row") was copied into a test,
  which then excluded dev_diary from the drift gate; an SEO fix went to dead
  code and dev_diary posts shipped without a meta description for three months
  behind a green test — CLAUDE.md, dev_diary note
- "Currently broken" outlived the fix by a month — CLAUDE.md, scheduled-agents
  note on `claude-md-sync`
- Rewording one bullet killed its sync anchor; the next nightly refreshed every
  other count and froze that one, logging nothing →
  `scripts/ci/claude_md_anchor_lint.py`, `scripts/sync_claude_md_db_stats.py`

---

## Part II — Green is not a result

### 4. A check that scanned nothing has not passed **(v1 — drafted in `sample-chapter.md`)**

- Every CI lint copied into an empty tree: 10 of 12 printed "clean" and exited
  0; three printed no count at all — `scripts/ci/lib_scan_floor.py` docstring
- The fix: `require_dir` / `require_scanned`, plus a test that runs every lint
  in an empty tree and asserts it fails —
  `src/cofounder_agent/tests/unit/scripts/test_ci_lint_scan_floor.py`
- A legitimate zero vs a disarmed one: floor on lockfiles _discovered_, not
  _checked_ — CLAUDE.md, scan-floor principle
- The same shape in production: `checkpoint_prune` removed nothing it should
  have for months (~20,000 rows) while every signal read healthy —
  `docs/architecture/retention-backlog.md`

### 5. Liveness is not correctness **(v1)**

"Did it run?" and "did it do the right thing?" produce identical telemetry
when the thing is misconfigured. Declare the invariant (the backlog that
should be zero) and alarm on its persistence. Anything that can't declare one
is reported as unmonitored, never as a passing zero.

- `last_error` NULL, `last_run_at` current, `total_deleted` non-zero, panel
  green — all while the policy was wrong — `docs/architecture/retention-backlog.md`
- A container alive but failing its healthcheck is never restarted by Docker;
  the watch sees only what a healthcheck sees — `docs/operations/self-healing.md`
- chatterbox restarted 507 times behind a green board — CLAUDE.md, brain
  daemon section (`container_restart_loop_probe`)

### 6. A producer must have a consumer **(v1)**

The recurring defect isn't a crash. It's a mechanism that runs correctly,
writes its output, reports healthy, and is read by nobody. The producer can't
see it from its own side. Two rules: derive expectations, never hand-list
them; and compare two independent recorders of the same event.

- `qa_numeric_fidelity` ran 37 times in 30 days while its `qa_gates` row read
  `total_runs=0` — the eighth recurrence of one missing-alias shape, each time
  past a guard whose hand-typed list was written by the person who forgot the
  alias — CLAUDE.md, producer/consumer principle
- A worker heartbeat every 30 seconds that nothing has ever queried — same
  source
- Two QA rails read "on" in every config surface and produced 0 reviews in 60
  days because no pipeline node wired them — CLAUDE.md, `multi_model_qa.py` row
- The guardrail: `scripts/ci/consumer_contract_lint.py` (+ baseline, ratchet)

### 7. Silence is the failure mode

Fire-and-forget tasks, swallowed exceptions, empty error strings, thresholds
widened until the alarm can't fire. Fail loud, name the cause, notify a human.

- 8+ posts lost their podcast and video for 13 days because a fire-and-forget
  task swallowed the error — `services/jobs/media_reconciliation.py` docstring
- `str(httpx.ReadTimeout)` is the empty string, so the log read
  `render failed ()` for weeks — `docs/architecture/featured-image-reliability.md`
- Ingestion taps pulled in nothing for 17 days — story post
- A webhook route unreachable from the internet, its silence hidden behind a
  freshness threshold raised from 7 to 180 days — CLAUDE.md, newsletter row

---

## Part III — Guardrails that scale with the agent

### 8. Ratchets, not issue-filers **(v1)**

A noisy scanner pointed at an issue tracker buries the real findings. Freeze
today's findings in a baseline, block only net-new ones, file nothing, and
annotate exceptions in the code with a reason.

- Bandit filed 91 GitHub issues; every one examined was a false positive, and
  they buried 18 genuine issues three pages deep — CLAUDE.md, static-analysis
  principle → `scripts/ci/bandit_lint.py` + `bandit_baseline.json`
- Of semgrep's 47 findings, 44 were one rule matching a log line that reads
  "requires login"; another flagged a parameterised query as SQL injection →
  vendored rules, `scripts/ci/semgrep_lint.py`, `infrastructure/semgrep/`
- Baselines keyed per file _per rule_, so a new finding can't ride in behind a
  deleted one — same source

### 9. Make the architecture a lint

Architecture described in a doc is a wish; architecture enforced in CI is a
fact. The agent can't drift across a boundary that fails the build.

- "The graph must be the whole truth": a pipeline step may not import a
  sibling step, or placing one silently runs the other —
  `scripts/ci/atom_independence_lint.py`
- "The service layer is the contract": no business logic or raw SQL in the
  HTTP / CLI / MCP adapters — `scripts/ci/adapter_purity_lint.py`,
  `docs/architecture/2026-06-10-transport-adapter-contract.md`
- Own the interfaces, rent the implementations —
  `docs/architecture/business-os-endgame.md`

### 10. Config in the database, and the three-writers problem

Every tunable in one table beats env-var sprawl, until three different seed
sources race to write the first value. First writer wins, and which writer is
first depends on the install path.

- 2,000+ settings in `app_settings`; three seed sources; overlapping keys must
  agree → `scripts/ci/settings_seed_value_drift_lint.py` — CLAUDE.md,
  migrations section
- A hardcoded per-niche key leaked one niche's opt-in to another and
  auto-published a post without authorisation — CLAUDE.md, auto-publish row

### 11. Measure before you believe

The number you're afraid of may not be the number you're paying. The metric
you trust may be measuring the wrong thing.

- Against a 35-value fact block, 81 of 99 _invented_ percentages were
  "explained" by some pair of real numbers, blinding the rail meant to catch
  them — `docs/architecture/numeric-fidelity.md`
- Same GPU: one model pays a 3% wall-clock tax, another 80% — residency
  economics, not model speed — `docs/architecture/decode-split-capture.md`
- A Python set's hash order decided CPU or GPU at each container start: 9 of
  20 processes ran the speech model on the CPU for days, with no error
  anywhere — `docs/operations/speaches-kokoro-gpu.md`
- An analytics cursor advanced to `now()` lost 40 real page views —
  `docs/architecture/analytics-ingestion-lag.md`

---

## Part IV — Running it

### 12. Self-healing with humility

Restart only what's safe to bounce, verify after, and never let an LLM improvise
a remediation on a container it has no rule for.

- Per-container restart rules, `remediation=rules_only`, verify-after —
  `docs/operations/self-healing.md`
- A wall plug opened at 03:04 with mains present; the box sat dark 12 hours
  behind a green board — CLAUDE.md, outlet guard

### 13. Scheduled agents, and what they really cost

The unattended sessions that merged dependency PRs and fixed tests ran on a
subscription token that quietly decayed. Seven were rewired onto deterministic
scripts and local models that never expire and never bill; two frontier-model
sessions wait on a metered-API decision.

- `docs/operations/scheduled-agents.md`,
  `docs/superpowers/specs/2026-07-09-scheduled-agents-rewire-design.md`

### 14. The economics of one person and a fleet of agents **(v1)**

What it costs, where the hours go, and what stays scarce.

- About $69 a month to run (electricity + API), about $240 a month in
  subscriptions, roughly $3,700 a year; about $10k of hardware — story post
- "Generation is cheap now; judgment is the scarce input" — story post
- Optional, if you're willing: the product freeze declared on 2026-09-16 and
  the 271 PRs merged in the ten days after it. Building is the comfortable
  part, and that is its own lesson.

### 15. Where it went wrong anyway

The failures that cost the most weren't events. They were things that quietly
stopped being true: a tap that ingests nothing, a config that drifted from what
you believed was running, a number quoted for months that described an older
system. Every one was found sideways.

- Story post, "Things that went wrong anyway"

---

## Appendices

### A. The guardrail kit **(v1)**

Drop-in, repo-agnostic versions of the guardrails in chapters 3, 4, 6 and 8,
generalised from `scripts/ci/`:

- `lib_scan_floor.py` + the empty-tree test
- A per-file-per-rule ratchet-baseline runner for any scanner
- A doc-anchor sync + dead-anchor lint for counts quoted in agent-facing docs
- A starting consumer-contract check for "table written, never read"

This is also the natural companion repo, and a reason to buy rather than
browse the public docs.

### B. A CLAUDE.md skeleton

The section structure that survived a year of daily sessions, with the
anti-patterns (claims without a date or a source, counts without an anchor)
called out inline.
