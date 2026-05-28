# Phase 1 — Variant Experiments Harness (Design)

**Status:** Draft, awaiting operator review
**Author:** Drafted by Claude during the 2026-05-28 R&D-lab strategy session
**Predecessor:** Phase 0 (PR #695) — observation layer + `lab_outcomes_v1` view
**Successor:** Phase 2 — multi-armed bandit traffic allocation

---

## Goal

Let the content pipeline run **multiple variants** of (prompt × model × RAG config) per
task and record which variant produced the resulting post, so the lab can learn which
configurations earn operator approval / drive engagement.

This is the smallest step that converts the pipeline from a single-track production
system into a **read-only A/B harness**. No optimization yet — humans still pick winners
manually. Phase 2 introduces the bandit; Phase 1 is the prerequisite that gives the
bandit something to read.

## Non-goals

- **Auto-promotion of winning variants** — deferred to Phase 2 (bandit) / Phase 3
  (auto-promote).
- **Live traffic shifting** — every variant gets equal allocation in Phase 1. Bandit
  weighting comes in Phase 2.
- **Multi-axis variants in the writer atom yet** — Phase 1 starts with prompt-version
  variants only (single axis). Model + RAG-config variants are designed but deferred
  until prompt-axis is proven.
- **Operator-callable ad-hoc generation** ("make me a video about cats") — that's a
  separate dual-mode pipeline conversation, not part of the lab harness.

## What "variant" means

A **variant** is a named configuration of one or more axes the pipeline can vary.
Initial axes:

| Axis                      | Examples                                             | Where defined                                                  |
| ------------------------- | ---------------------------------------------------- | -------------------------------------------------------------- |
| `prompt_template_version` | `qa.writer.long_form/v3` vs `qa.writer.long_form/v4` | `prompt_templates` table (already exists)                      |
| `writer_model`            | `glm-4.7-5090` vs `qwen2.5-72b`                      | `app_settings.cost_tier.standard.model` (extended per-variant) |
| `rag_config`              | `{snippet_limit: 3}` vs `{snippet_limit: 8}`         | New `rag_configs` table (Phase 1)                              |

A **variant** belongs to an **experiment**. An experiment is a named test that holds
2-N variants in active rotation for a specific niche. Example:

```
experiment: glad-labs/writer-prompt-tuning-2026-q2
  variant A (baseline): qa.writer.long_form/v3
  variant B (test):     qa.writer.long_form/v4
  niche_slug: glad-labs
  status: active
  allocation: 50/50
```

## Schema delta

Two tables. Both additive, no breaking changes. Idempotent migration:

```sql
-- Experiments: a named test holding 2+ variants on a niche
CREATE TABLE IF NOT EXISTS experiments (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key             text NOT NULL UNIQUE,           -- 'glad-labs/writer-prompt-tuning-2026-q2'
  niche_slug      text NOT NULL,                  -- which niche this experiment runs in
  description     text NOT NULL DEFAULT '',
  status          text NOT NULL DEFAULT 'draft',  -- draft|active|paused|concluded
  created_at      timestamptz NOT NULL DEFAULT now(),
  activated_at    timestamptz,
  concluded_at    timestamptz,
  conclusion_note text,                           -- 'variant B won 73% approval, promoting'
  CHECK (status IN ('draft','active','paused','concluded'))
);

CREATE INDEX IF NOT EXISTS idx_experiments_niche_active
  ON experiments(niche_slug) WHERE status = 'active';

-- Variants: the actual configurations the writer atom samples from
CREATE TABLE IF NOT EXISTS experiment_variants (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id            uuid NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
  label                    text NOT NULL,         -- 'A', 'B', 'baseline', 'shorter-hooks'
  weight                   numeric NOT NULL DEFAULT 1.0,  -- equal weight in Phase 1
  prompt_template_key      text,
  prompt_template_version  integer,
  writer_model             text,
  rag_config               jsonb DEFAULT '{}'::jsonb,
  active                   boolean NOT NULL DEFAULT true,
  created_at               timestamptz NOT NULL DEFAULT now(),
  UNIQUE (experiment_id, label)
);

CREATE INDEX IF NOT EXISTS idx_experiment_variants_active
  ON experiment_variants(experiment_id) WHERE active;
```

The `prompt_template_key + prompt_template_version` columns already flow through
`lab_outcomes_v1` (Phase 0). Adding `variant_id` to `capability_outcomes` lets us
join experiment outcomes to per-atom telemetry:

```sql
ALTER TABLE capability_outcomes
  ADD COLUMN IF NOT EXISTS variant_id uuid REFERENCES experiment_variants(id);

CREATE INDEX IF NOT EXISTS idx_capability_outcomes_variant
  ON capability_outcomes(variant_id) WHERE variant_id IS NOT NULL;
```

And a small extension to `lab_outcomes_v1` (one CREATE OR REPLACE VIEW) that joins
the variant + experiment context so dashboards see everything in one read.

## Variant selection (Phase 1 — uniform random)

When a task fires for a niche with an active experiment, the writer atom calls a
new `services/experiment_runner.py::pick_variant(niche_slug, task_id)`:

```python
async def pick_variant(pool, niche_slug: str, task_id: str) -> ExperimentVariant | None:
    """Return a randomly-selected active variant for the niche's active
    experiment, or None if no experiment is active. Equal weight in Phase 1
    (uniform random over active variants of the most recent active experiment).
    Bandit weighting lives in Phase 2."""
```

Selection is **uniform random** per task. No stickiness ("once a task is in variant A,
all atoms for that task use A's config") — Phase 1 keeps it simple. The same task_id
flowing through 8 atoms uses the same variant; downstream atoms read the chosen
variant from `state["variant_id"]`.

When `pick_variant` returns a variant, the writer atom:

1. Sets `state["variant_id"] = variant.id`
2. Overrides `state["prompt_template_key"]` and `state["prompt_template_version"]` from the variant's fields (if set)
3. Overrides the resolved model from the variant's `writer_model` (if set)
4. Merges `variant.rag_config` into the snippet-fetch parameters
5. Threads `variant_id` through all downstream atoms via the existing state-dict mechanism

The recorder (`capability_outcomes.record_run`) is already wired in Phase 0 to read
prompt key + version from state; it just needs one more line to also stamp
`variant_id`.

## Reward model (Phase 1 — observe, don't optimize)

Three signals flow into per-variant scoring, **read-only in Phase 1**:

| Signal                    | Source                                                             | Lag                  |
| ------------------------- | ------------------------------------------------------------------ | -------------------- |
| **Operator approval**     | `published_post_edit_metrics.approver` IS NOT NULL                 | Hours-to-days        |
| **Edit distance**         | `published_post_edit_metrics.char_diff_count` / `pre_approve_len`  | Same                 |
| **Downstream engagement** | `lab_outcomes_v1.views_24h_post_publish` + `views_7d_post_publish` | 24h-7d after publish |

A per-variant scorecard view rolls these up:

```sql
CREATE OR REPLACE VIEW experiment_variant_scorecard_v1 AS
SELECT
  ev.experiment_id,
  ev.id AS variant_id,
  ev.label,
  COUNT(*) AS posts_attempted,
  COUNT(*) FILTER (WHERE lo.approver IS NOT NULL) AS posts_approved,
  ROUND(
    COUNT(*) FILTER (WHERE lo.approver IS NOT NULL)::numeric
    / NULLIF(COUNT(*), 0) * 100, 1
  ) AS approval_rate_pct,
  AVG(lo.char_diff_count::numeric / NULLIF(lo.pre_approve_len, 0)) FILTER (WHERE lo.approver IS NOT NULL)
    AS avg_edit_distance_pct,
  AVG(lo.views_24h_post_publish) AS avg_views_24h,
  AVG(lo.views_7d_post_publish) AS avg_views_7d,
  AVG(lo.actual_cost) AS avg_cost_per_post,
  SUM(lo.actual_cost) AS total_cost
FROM experiment_variants ev
LEFT JOIN lab_outcomes_v1 lo ON lo.variant_id = ev.id
GROUP BY ev.experiment_id, ev.id, ev.label;
```

Phase 1 = **operator looks at this view and manually concludes experiments**. Phase 2
= a bandit reads this view every N hours and shifts allocation weights.

## Operator surface

CLI is the source of truth (per `feedback_cli_first`):

```
poindexter experiments list
poindexter experiments create <key> --niche=glad-labs --description="..."
poindexter experiments add-variant <key> --label=A --prompt-template=qa.writer.long_form --prompt-version=3
poindexter experiments add-variant <key> --label=B --prompt-template=qa.writer.long_form --prompt-version=4
poindexter experiments activate <key>
poindexter experiments status <key>      # renders the scorecard
poindexter experiments conclude <key> --note "B won 73% approval — promoting" --winner=B
```

Grafana panel (extend the "Lab Observability" row added in Phase 0):

- Stat: active experiments count
- Table: experiment_variant_scorecard_v1 for the most recent active experiment
- Time series: approval_rate over rolling 7-day windows, one line per variant

Conclusion (`conclude --winner=B`) is the **manual** Phase 1 promotion mechanism: it
sets the winning variant's prompt template as the niche's new default and marks the
experiment `concluded`. No bandit. The operator stays in the loop, but with rigorous
data to act on instead of vibes.

## PR decomposition

| PR  | Scope                                                                                                | LOC  | Risk                             |
| --- | ---------------------------------------------------------------------------------------------------- | ---- | -------------------------------- |
| 1   | Migration (2 tables + ALTER + view extension)                                                        | ~150 | Low — additive only              |
| 2   | `services/experiment_runner.py::pick_variant` + writer-atom hook + state threading + recorder wiring | ~400 | Medium — touches writer hot path |
| 3   | `poindexter experiments` CLI (list/create/add-variant/activate/conclude/status)                      | ~300 | Low                              |
| 4   | Grafana panels + scorecard view                                                                      | ~80  | None                             |
| 5   | Integration tests: two-variant experiment, verify ~50/50 sampling, scorecard correctness             | ~200 | None                             |

Total: ~1130 LOC across 5 PRs, each independently mergeable. Could ship over 2-3
sessions.

## Operator decisions — resolved 2026-05-28

The 5 open questions from the design draft were resolved via direct conversation
with the operator. Decisions below; they're now binding constraints on the
implementation, not options.

### 1. Niche-scoping → confirmed + tightened

Experiments are **niche-scoped** (one niche at a time). Beyond that, the operator
emphasized **scientific-method control**: every experiment varies ONE axis only.

> "We need to try to balance control vs variable. The more we can have everything
> constant except the things we are comparing (like different models or prompts) the
> better. Like actual scientific method type stuff. Too many moving parts creates
> too much uncertainty and makes it hard to compare apples to apples."

Implementation implication: when a variant overrides one axis (e.g. `writer_model`),
the others MUST inherit the niche's current production config (prompt template,
prompt version, RAG settings). The variant's "model" field is set; "prompt_template_key

- version" inherit the niche default; "rag_config" inherits the niche default. The
  recorder still stamps all three on `capability_outcomes` so the scorecard can prove
  the held-constant axes were actually held constant.

A future "multi-axis variant" is explicitly out of scope. If we want to test
prompt-change AND model-change, that's two separate experiments run sequentially.

### 2. Stickiness → task-level (confirmed)

> "Task level is best. Less moving parts is easier to pin down actual differences
> between the thing we're testing and not just different results because different
> content is being judged."

All atoms within a task use the same variant. Removed the "per-atom sampling
might be nice later" hedge from the design — keeping that lane closed.

### 3. Reward model → views primary, evals supplementary, per-experiment objective

> "I think it may be good to use established industry evals to discover a winner,
> but ultimately a 'win' for a content creation business is going to be what brings
> the most views. That could change though depending on the niche or the users
> intent, but for now I would think just straight up views and what is more popular
> wins."

Phase 1 reward stack:

| Tier          | Signal                                           | Source                                       | Role                                                                                                                                                     |
| ------------- | ------------------------------------------------ | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Primary**   | `avg_views_24h` and `avg_views_7d` per variant   | `lab_outcomes_v1` (CF AE → page_views sync)  | Defines winners. Highest-views variant wins.                                                                                                             |
| **Secondary** | DeepEval / RAGAS / Guardrails scores per variant | `capability_outcomes.metrics->>'<eval_key>'` | Supplementary signal, surface on scorecard, no auto-decision weight                                                                                      |
| **Tertiary**  | Operator approval rate, edit distance            | `published_post_edit_metrics`                | Sanity check — if approval rate < 50% the experiment auto-pauses (safety gate) even if views look fine. Bad content with engagement still poisons brand. |
| **Cost lens** | `cost_per_approved_post` per variant             | `lab_outcomes_v1.actual_cost`                | Tiebreaker / economic veto — if A wins on views but costs 5× B, operator decides whether the lift is worth it                                            |

To support "could change depending on niche or user intent": the
`experiments.objective_function` column lets each experiment pick its primary metric:
`views_7d` (default), `views_24h`, `approval_rate`, `views_per_dollar`, or
`composite_score` (configurable weights). Phase 1 ships with `views_7d` as default;
operator can override per experiment.

This isn't auto-promotion — Phase 1 still has the operator concluding manually.
Objective function just makes the scorecard rank variants in the right order.

### 4. One active experiment per niche → confirmed

> "Yeah let's keep it simple here, just one active experiment per niche so we don't
> get lines crossed."

The `pick_variant` selector enforces this at SQL level (`WHERE status='active'
LIMIT 1` after `niche_slug` filter; activate-time CHECK constraint prevents two
active experiments on the same niche). Multi-experiment-per-niche stays out of scope.

### 5. First experiment → open-source writer model A/B on `glad-labs`

> "The biggest thing to experiment is going to be the open source models we use for
> writing. This is essentially the core of the project and could be the most
> consequential. Also models keep changing and new ones are constantly released so
> this gives us a huge range of options to test. And since they're 'black box'
> there's less variability in a way. Like we can't tweak little things like we could
> with prompts or the like."

Concrete model inventory verified against the operator's host (2026-05-28):

| Model                  | Param class     | Used in last 30d | Notes                                                                                                      |
| ---------------------- | --------------- | ---------------- | ---------------------------------------------------------------------------------------------------------- |
| `glm-4.7-5090:latest`  | ~30B            | 96 calls         | Current baseline (resolved via cost_tier.standard chain). Strongest current default.                       |
| `qwen3:30b`            | ~30B            | 0                | Established Qwen3, untested in production                                                                  |
| `qwen3.6:latest`       | likely 30-35B   | 0                | **Operator just pulled — newer generation, untested in production.** Highest "interesting unknown" signal. |
| `gemma4:latest`        | tbd             | 0                | **Operator just pulled — newer Gemma generation, untested.** Different family entirely.                    |
| `gemma3:27b`           | 27B             | 6 calls          | Slightly smaller — would conflate model-size with model-family.                                            |
| `qwen3.5:35b`          | 35B             | 0                | Larger — would conflate model-size with model-family.                                                      |
| `glm-4.7-flash:latest` | smaller distill | 0                | Same family, smaller. Clean control axis but less informative result.                                      |
| `gpt-oss:20b`          | 20B             | 0                | OpenAI open-weight. Different size class.                                                                  |

**Operator decision (2026-05-28): Experiment 0001 = `gemma4:31b` vs `qwen3.6:latest`,
50/50 allocation, on `glad-labs` niche.** Challenger-discovery posture — pit the two
newly-pulled candidates against each other; the winner becomes the challenger for
Experiment 0002 against the production baseline.

Experiment key: `glad-labs/writer-model-gemma4-vs-qwen36-2026-05`.

Why this pair:

- Both fresh-pulled, never tested in production — the harness is the right way to
  evaluate them, not vibes-testing on a few hand-prompted outputs
- Different families (Google Gemma vs Alibaba Qwen) → measures architecture quality
  across genuinely different lineages
- Both already loaded → no inference-warmup variance
- Similar param class — gemma4 at 31B, qwen3.6 size TBD on first load but expected ~30-35B range

**Risk this configuration carries — flagging explicitly:** during the ~3-4 week
experiment window, `glad-labs` posts will be produced 50/50 by gemma4 and qwen3.6 with
**zero traffic going to the known-good baseline** (`glm-4.7-5090:latest`). If both
candidates underperform baseline quality, that's ~30-50 worse-than-current
customer-facing posts before we have data to swap back. The quality auto-pause gate
(approval rate < 50% after 10 posts) catches _catastrophic_ underperformance but
doesn't catch _slightly worse_ — both variants holding at 55% approval would let the
experiment run to conclusion shipping slightly-degraded content the whole time.

Two ways to mitigate, operator picks:

- **(A) Accept the risk** as a deliberate "testing in production" trade — the win on
  time-to-knowing outweighs ~1 month of slightly-degraded content. Run as-spec.
- **(B) Convert to 3-way A/B/C** with `glm-4.7-5090:latest` as the third variant at
  33% allocation — keeps known-good content flowing while exploring. Each variant
  needs more posts to hit statistical significance (~45-75 per variant instead of
  30-50), extends the experiment to ~5-6 weeks. Still scientifically valid (one
  axis varying, three values).

Default per operator's call is (A). If (B) becomes preferred mid-experiment, the
baseline variant can be added via `poindexter experiments add-variant` and weights
re-balanced — the harness supports adding variants to an active experiment.

**Experiment 0002 (queued):** winner of 0001 vs `glm-4.7-5090:latest`. This is the
"can the newcomer dethrone the incumbent" test. Activates after 0001 concludes.

If we want to test `gpt-oss:20b` or `glm-4.7-flash:latest`, those become later
experiments — at smaller param classes they're answering a different question
(can a smaller model match a bigger one), not the same-class architecture question
0001/0002 are answering.

Activate Experiment 0001 after the Phase 1 PRs land + you've given it ~3-4 weeks
of dual-variant runs to reach statistical significance (~30-50 posts per variant
minimum, or ~45-75 per variant if you pick path B). At the current `glad-labs`
posting cadence that's roughly a calendar month (or ~6 weeks for path B).

## Posture: testing in production

The operator's closing principle is binding on every later decision:

> "The idea is essentially going against common conventions and 'testing in production'.
> We need to remember that at the end of the day we are a business and need to make
> money, so that is the target. We're not just running these experiments for research
> (although that is a nice byproduct)."

This shapes four hard constraints on the harness:

1. **Production-grade reliability.** A misconfigured variant must not crash the
   pipeline. The `pick_variant` selector defaults to "no variant active → use
   niche's existing production config" — failure mode is "nothing changes," not
   "writer atom errors out." Variant model-override that fails to dispatch falls back
   to the niche default rather than failing the task.

2. **Cost guards.** The cost-axis safety: if a variant's
   `avg_cost_per_approved_post` exceeds the niche's baseline by some factor (e.g.,
   3×), an alert fires (no auto-pause yet — Phase 2). The operator sees runaway
   compute cost before it adds up to real money.

3. **Quality auto-pause.** If a variant's operator-approval-rate drops below a
   threshold (default 50%) after a minimum sample (default 10 posts), the variant
   auto-pauses and the experiment falls back to single-variant operation on the
   survivor. Bad content shipping at 50% volume because the variant is dumb is the
   exact "testing in production" tail risk we have to guard against.

4. **Revenue-aligned scorecard.** `objective_function = 'views_7d'` is the default
   because views are the closest proxy to revenue we have today (ad impressions,
   audience growth). Once we have direct revenue attribution per post (later phase)
   the default flips to `revenue_per_post`. The harness anticipates this — it doesn't
   bake `views_7d` into Python constants, it reads `objective_function` from each
   experiment row.

These are not optional polish. They're what makes the harness operable on a
business that's running for real customers, not a research notebook.

## What this unlocks

After Phase 1 lands and runs on ONE niche for ~3-4 weeks (need ~30-50 posts per
variant for statistical signal), the operator can:

- **See, quantitatively, whether prompt variations win or lose** — instead of "this
  felt better" / "this seems worse"
- **Make ship/kill decisions on prompt versions** with data — promote the winner,
  retire the loser, spawn the next variant from the operator's intuition about why
  the winner won
- **Generalize the harness to other niches** by simply creating a new experiment

Phase 2 (bandit) becomes possible because the data flow is already proven. Phase 3
(auto-promote) becomes possible because the scorecard is already trusted.

The thing the operator should be reading on the beach (per the strategic-vision
conversation) starts existing here: a learnings digest that summarizes "this week
the lab ran experiments X, Y, Z; here's what's winning; here's what to try next."
Phase 4 (the digest surface) reads from `experiment_variant_scorecard_v1` and
LLM-summarizes it for Discord/Telegram delivery.
