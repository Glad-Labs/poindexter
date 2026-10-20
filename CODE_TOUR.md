# Code tour

Poindexter is a large codebase. You don't need to read most of it to see how
it's built. These six stops are each one short file that shows one idea. Most
of them also record the incident that shaped the code, with the numbers.

Read each file's opening docstring first. The "why" is in those docstrings,
and the code mostly carries it out.

## 1. The pipeline is data, not code

- [`services/canonical_blog_spec.py`](src/cofounder_agent/poindexter/services/canonical_blog_spec.py) (272 lines)
- [`plugins/atom.py`](src/cofounder_agent/poindexter/plugins/atom.py) (194 lines)

The whole content pipeline — about 50 steps covering research, writing,
images, 17 QA checks, SEO and publishing — is one Python dict. It's stored in
Postgres and compiled into a LangGraph graph at run time. Each step is an
**atom**: a small module that declares what it reads, what it writes, its
retry policy and its cost class in an `AtomMeta`.

What to notice:

- The spec imports nothing, so a database migration can load it without
  pulling in the runtime.
- Atoms never import each other. A graph built from atoms has to be the whole
  truth of what runs, and a CI check enforces that.
- The graph is checked when it's built: every step's inputs must be produced
  by an earlier step.

## 2. A QA check that does arithmetic instead of guessing

- [`services/numeric_fidelity.py`](src/cofounder_agent/poindexter/services/numeric_fidelity.py) (478 lines)
- [`modules/content/atoms/qa_numeric_fidelity.py`](src/cofounder_agent/poindexter/modules/content/atoms/qa_numeric_fidelity.py) (222 lines)

The other anti-hallucination checks are judgements: patterns, or an LLM asked
whether a draft is faithful. This one is arithmetic. A number the draft
presents as a sourced fact must match a number in the research it was given,
at the precision the author wrote.

What to notice:

- **It was tuned against real posts, not by intuition.** Checking every number
  flagged a third of ordinary prose, and every one of those flags was wrong:
  years, hypotheticals, figures of speech. So only numbers in a sentence that
  attributes them ("according to…", a citation link) are scored.
- **A feature was switched off because it measured badly.** Allowing a number
  to be derived from two source numbers sounded reasonable. But against a
  35-value research corpus it "explained" 81 of 99 invented percentages.
  Turning it off cost one extra false positive per 40 posts.
- The module is pure: no database, browser or LLM, so every rule is
  unit-tested directly.

## 3. LLM output is checked by code before it's kept

- [`services/post_summaries.py`](src/cofounder_agent/poindexter/services/post_summaries.py) (138 lines)
- [`services/jobs/generate_post_summaries.py`](src/cofounder_agent/poindexter/services/jobs/generate_post_summaries.py) (57 lines)

A small, complete feature. A local model writes a two- or three-sentence
summary of each published post. Code then checks it: within the length limit,
not about itself ("This article…"), and every number in it appears in the
post. A failed summary gets one retry with the reason. A second failure is
recorded so the post isn't retried for a week.

What to notice:

- The model proposes and code verifies. That pattern runs through the whole
  pipeline.
- It runs as a scheduled job rather than a pipeline step, so it backfills old
  posts and doesn't change the graph's contract. The docstring explains the
  trade-off.
- The prompt lives in a versioned prompt pack, not in the Python file, and
  every setting comes from the database.

## 4. A one-line fix for a silent data-loss bug

- [`services/watermark_cursor.py`](src/cofounder_agent/poindexter/services/watermark_cursor.py) (123 lines)

Page views come from Cloudflare's analytics store, which makes a row
queryable a few seconds after it's written. A poll that moved its cursor to
"now" stepped permanently over rows that weren't visible yet. Nothing errored;
40 real page views (7.3%) simply vanished in one month.

What to notice:

- The fix is one expression. The docstring spends its length on the evidence
  (a control pair of identical writes, one lost and one kept) and on the three
  properties the function must keep.
- Two jobs had the same bug, so the rule lives in one function both call. A
  test fails if either grows its own copy.

## 5. Running unattended: state that survives a restart

- [`brain/probe_schedule.py`](src/cofounder_agent/poindexter/brain/probe_schedule.py) (141 lines)
- [`utils/findings.py`](src/cofounder_agent/poindexter/utils/findings.py) (121 lines)

The watchdog daemon runs health probes on schedules. It kept each probe's
last-run time in memory, so every restart made every probe due at once. Over
30 days that came to 111 restarts and 111 of one probe's 120 pages. Last-run
times now persist in Postgres.

What to notice:

- Every failure mode is spelled out and falls back to running the probe: an
  extra run is recoverable, a probe that never runs is silent.
- `emit_finding` is how any job reports a problem. Detection and delivery are
  separate, so a broken alert channel can't lose a finding, and routing is
  configured per kind in the database.

## 6. CI that can't pass by checking nothing

- [`scripts/ci/lib_scan_floor.py`](scripts/ci/lib_scan_floor.py) (113 lines)

An audit copied every CI lint into an empty directory: 10 of 12 printed
"clean" and exited 0. A lint whose scan root moved would have stayed green
forever. Every lint now fails if it examined nothing, and a test runs each one
in an empty tree to prove it.

What to notice:

- The same idea, "a check that didn't run hasn't passed", recurs across the
  codebase: probes, QA rails that append no result, and backups that are
  restore-tested.
- Noisy scanners (bandit, semgrep, mypy) run as **ratchets**: they block new
  findings and file nothing, after bandit filed 91 issues that were all false
  positives.

## Where the complexity is

Some files are much larger than the ones above, and they're the first ones a
reviewer tends to open:

| File                                             | Lines  |
| ------------------------------------------------ | ------ |
| `services/video_renderers/shot_list_renderer.py` | ~6,700 |
| `services/gpu_scheduler.py`                      | ~4,200 |
| `brain/brain_daemon.py`                          | ~4,000 |
| `modules/content/multi_model_qa.py`              | ~3,700 |

They grew as features landed. Splitting the daemon is tracked in
[poindexter#715](https://github.com/Glad-Labs/poindexter/issues/715) and the QA
module in [poindexter#643](https://github.com/Glad-Labs/poindexter/issues/643);
the renderer and the GPU scheduler aren't tracked yet. They're covered by
tests, but they aren't the best place to judge the design. Start with the
files above.

For the architecture as a whole, see the
[architecture overview](https://gladlabs.mintlify.app/docs/architecture/overview).
