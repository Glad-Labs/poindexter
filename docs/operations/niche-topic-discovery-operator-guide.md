# Niche Topic Discovery — Operator Guide

Day-to-day operator instructions for the niche-aware topic discovery + RAG
writer modes shipped on 2026-04-30 (Glad-Labs/poindexter#278).

For the architecture rationale (why this exists, table layout, decay math,
provenance trail) see
[`docs/architecture/niches-and-rag-modes.md`](../architecture/niches-and-rag-modes).
This page is the action surface — what to type, when to type it.

## What this is, in 3 sentences

Topic discovery now runs **per niche**. You configure a niche once, then
operate it as a stream of 5-candidate batches. The operator picks a winner
from each batch; that handoff creates a `content_task` that flows through
the regular pipeline.

## Day-zero setup

The Glad Labs niche is pre-seeded by **migration 0115**:

- slug `glad-labs`, writer mode `TWO_PASS`, batch size 5,
  cadence floor 60 minutes
- 5 weighted goals — `AUTHORITY` 35, `EDUCATION` 25, `BRAND` 20,
  `TRAFFIC` 15, `REVENUE` 5
- 5 enabled sources — `internal_rag` 50, `hackernews` 20, `devto` 15,
  `web_search` 10, `knowledge` 5

Confirm it's there:

```bash
poindexter topics niche list
poindexter topics niche show glad-labs
```

Adding a **new** niche today is a hand-job: there is no
`poindexter niche create` CLI yet (see "What's not yet built" below).
Until that lands, insert via SQL or call `services.niche_service.NicheService`
from a Python shell:

```bash
psql "$DATABASE_URL" <<'SQL'
INSERT INTO niches (slug, name, target_audience_tags,
                    writer_rag_mode, batch_size,
                    discovery_cadence_minute_floor)
VALUES ('real-estate', 'Real Estate Investing',
        ARRAY['investors','agents'],
        'CITATION_BUDGET', 5, 120)
RETURNING id;
SQL
# Then INSERT rows into niche_goals (must sum to ~100)
# and niche_sources (enable + weight) for that niche_id.
```

## The daily workflow

### 1. Trigger a sweep

There is **no top-level `poindexter topics sweep --niche <slug>` command yet.**
Sweeps run one of three ways:

- **Idle worker auto-trigger** — the existing `IdleWorker` background loop
  fires periodically (subject to the niche's `discovery_cadence_minute_floor`
  and the `topic_discovery_auto_enabled` master switch — see kill-switch
  section). This is the default for un-touched OSS installs.
- **Manual one-shot trigger** — set `topic_discovery_manual_trigger=true`
  via `poindexter settings set`; the next idle worker tick sees it and
  fires regardless of the auto switch.
- **Direct service call** — for operators who want immediate, deterministic
  control, call `TopicBatchService.run_sweep(niche_id=...)` from a Python
  shell or a one-off worker job.

```bash
# Manual one-shot via the settings switch:
poindexter settings set topic_discovery_manual_trigger true \
  --category topic_discovery
```

```python
# Or directly from a python shell against the live DB:
import asyncio, asyncpg
from services.topic_batch_service import TopicBatchService
from services.niche_service import NicheService

async def main():
    pool = await asyncpg.create_pool(DSN)
    n = await NicheService(pool).get_by_slug("glad-labs")
    await TopicBatchService(pool).run_sweep(niche_id=n.id)

asyncio.run(main())
```

### 2. View the open batch

```bash
poindexter topics show-batch --niche glad-labs
```

You'll see candidate IDs, kinds (`external` / `internal`), effective scores,
and titles.

### 3. Rank candidates

Pick your preferred order, best-first. The operator rank is the source of
truth — it does **not** have to match the system rank.

```bash
poindexter topics rank-batch <batch_id> --order id1,id2,id3,id4,id5
```

### 4. (Optional) Edit the winner's title or angle

```bash
poindexter topics edit-winner <batch_id> --topic "Sharper title"
poindexter topics edit-winner <batch_id> --angle "New framing"
poindexter topics edit-winner <batch_id> --topic "..." --angle "..."
```

### 5. Resolve — push the winner into the pipeline

```bash
poindexter topics resolve-batch <batch_id>
```

This marks the batch `resolved`, advances `operator_rank=1` to a
`content_task`, and unblocks the next sweep for that niche (subject to the
cadence floor).

### 6. Or — reject everything and ask for a fresh sweep

```bash
poindexter topics reject-batch <batch_id> --reason "all stale"
```

Unpicked candidates carry forward into the next batch with their score
multiplied by `niche_carry_forward_decay_factor` (default 0.7).

## The 4 writer RAG modes

Set per-niche on `niches.writer_rag_mode`. Tasks without a `writer_rag_mode`
fall back to the legacy generator, so pre-niche pipelines still work.

| Mode              | Snippet limit                                   | What it does                                                                                                                                                              | Use when                                                                     |
| ----------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `TOPIC_ONLY`      | `writer_rag_topic_only_snippet_limit` = 8       | Top 8 internal pgvector snippets dropped into the writer prompt. Single pass, no enforcement. Cheapest mode.                                                              | Broad niche, no hard citation contract, lowest cost per draft.               |
| `CITATION_BUDGET` | `writer_rag_citation_budget_snippet_limit` = 12 | Top 12 snippets fetched. Writer **must** cite ≥ N (default 3, key `writer_rag_citation_budget_min_citations`). Drafts under-budget are rejected pre-QA.                   | Authority/depth posts where unsupported claims must be cut.                  |
| `STORY_SPINE`     | `writer_rag_story_spine_snippet_limit` = 15     | Outline preprocessing pass (5-beat: hook / what_happened / why_it_matters / what_we_learned / close), then writer expands to prose.                                       | Long-form narrative, postmortems, decision history, journey-style retros.    |
| `TWO_PASS`        | `writer_rag_two_pass_snippet_limit` = 20        | Internal-only first draft → writer marks `[EXTERNAL_NEEDED: ...]` for missing facts → bounded external research → revise. LangGraph state machine. **Glad Labs default.** | First-person reporting on something nobody else has covered. Most expensive. |

**TWO_PASS revision loop** is hard-capped at
`writer_rag_two_pass_max_revision_loops` (default 3), and each external
research call is capped at `writer_rag_two_pass_research_max_sources`
(default 2 sources per `[EXTERNAL_NEEDED]` marker).

All four modes use `pipeline_writer_model` for the LLM call (no per-mode
model override yet — see "What's not yet built").

## The kill-switch for legacy auto-discovery

Migration 0118 introduced `topic_discovery_auto_enabled`:

- **Default `true`** — the legacy `IdleWorker` discovery loop keeps firing
  on its old signals (queue-low, stale-content, rejection-streak, 24h
  safety net). OSS installs without configured niches keep their existing
  behavior.
- **Set `false`** — all auto-firing branches bail out early with an INFO
  log. Manual triggers still work. Use this for any install where you
  want to drive everything through `poindexter topics`.

Flip it:

```bash
poindexter settings set topic_discovery_auto_enabled false \
  --category topic_discovery
```

Glad Labs operators running niches should set this to `false`.

## Tuning knobs you'll actually touch

Most of these live in `app_settings` and are seeded by migration 0119. The
two `niches` table columns at the top are per-niche, not app_setting.

| Knob                                       | Where                 | Default                | What it controls                                                  |
| ------------------------------------------ | --------------------- | ---------------------- | ----------------------------------------------------------------- |
| `niches.batch_size`                        | per-niche column      | 5 (Glad Labs)          | Candidates per batch                                              |
| `niches.discovery_cadence_minute_floor`    | per-niche column      | 60 min (Glad Labs)     | Minimum gap between sweeps for that niche                         |
| `niches.writer_rag_mode`                   | per-niche column      | `TWO_PASS` (Glad Labs) | Switch the writer mode                                            |
| `topic_discovery_auto_enabled`             | `app_settings`        | `true`                 | Legacy auto-discovery master kill-switch (see above)              |
| `niche_carry_forward_decay_factor`         | `app_settings`        | `0.7`                  | How fast unpicked candidates fade across batches                  |
| `niche_top_n_per_pool`                     | `app_settings`        | `5`                    | Top-N per pool fed to the LLM final-score stage                   |
| `niche_internal_rag_per_kind_limit`        | `app_settings`        | `4`                    | Items per source-kind fed into the internal RAG candidate pool    |
| `niche_batch_expires_days`                 | `app_settings`        | `7`                    | How long an open batch can sit before it expires                  |
| `niche_embedding_model`                    | `app_settings`        | `nomic-embed-text`     | Ollama embedding model (must match embeddings dim)                |
| `niche_ollama_chat_timeout_seconds`        | `app_settings`        | `60`                   | Per-call HTTP timeout for the LLM scorer / distillation calls     |
| `niche_goal_descriptions`                  | `app_settings` (JSON) | (7-key dict)           | Prose anchors for each `goal_type` — drives goal-vector embedding |
| `writer_rag_topic_only_snippet_limit`      | `app_settings`        | `8`                    | Snippet count for `TOPIC_ONLY`                                    |
| `writer_rag_citation_budget_snippet_limit` | `app_settings`        | `12`                   | Snippet count for `CITATION_BUDGET`                               |
| `writer_rag_citation_budget_min_citations` | `app_settings`        | `3`                    | Minimum internal citations the `CITATION_BUDGET` writer must hit  |
| `writer_rag_story_spine_snippet_limit`     | `app_settings`        | `15`                   | Snippet count for `STORY_SPINE`                                   |
| `writer_rag_two_pass_snippet_limit`        | `app_settings`        | `20`                   | Snippet count for `TWO_PASS` first draft                          |
| `writer_rag_two_pass_max_revision_loops`   | `app_settings`        | `3`                    | Hard cap on the `TWO_PASS` revise loop                            |
| `writer_rag_two_pass_research_max_sources` | `app_settings`        | `2`                    | Sources fetched per `[EXTERNAL_NEEDED]` marker                    |
| `writer_rag_context_snippet_max_chars`     | `app_settings`        | `500`                  | Per-snippet character cap when building the writer prompt block   |

Set any app_setting via:

```bash
poindexter settings set <key> <value> --category <category>
```

Per-niche columns currently require a SQL `UPDATE niches SET ... WHERE slug=...`
until the niche edit CLI lands.

## Troubleshooting

| Symptom                                                                                                   | Likely cause                                                                            | Fix                                                                                                     |
| --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Tasks land in `awaiting_approval` you never queued                                                        | Legacy auto-discovery still firing                                                      | `poindexter settings set topic_discovery_auto_enabled false --category topic_discovery`                 |
| `topics show-batch` says "No open batch"                                                                  | Either no sweep has run yet, or the last batch is still resolving                       | Trigger via the manual switch or direct `TopicBatchService.run_sweep` call (see daily workflow §1)      |
| Sweep runs but the batch is empty                                                                         | `internal_rag` source disabled in `niche_sources`, or no recent embeddings to mine from | `poindexter topics niche show <slug>` and check sources; verify `embeddings` table has recent rows      |
| Writer keeps citing the wrong source                                                                      | `TOPIC_ONLY` has no enforcement; you may want `CITATION_BUDGET`                         | Switch the niche's `writer_rag_mode`, or refine the niche's prompt override (see follow-up table below) |
| `TWO_PASS` keeps hitting the revision cap and ships drafts with `[EXTERNAL_NEEDED]` markers still in them | Hard cap of 3 loops reached                                                             | Bump `writer_rag_two_pass_max_revision_loops` cautiously — high values risk runaway cost                |
| Scoring LLM call times out                                                                                | Local Ollama model is slow or wedged                                                    | Bump `niche_ollama_chat_timeout_seconds` or check Ollama health                                         |

## What's not yet built

Honest gap list as of 2026-04-30:

- **No top-level `poindexter topics sweep --niche <slug>` CLI.** Sweeps are
  triggered by the idle worker, the manual-trigger setting, or a direct
  `TopicBatchService.run_sweep` call from Python.
- **No `poindexter niche create / edit / set-goal / enable-source` CLI.**
  New niches and goal/source edits go in via SQL or via the
  `services.niche_service.NicheService` Python API. The CLI subgroup
  currently exposes only `niche list` and `niche show`.
- **Writer-mode model selection is hardcoded to `pipeline_writer_model`.**
  Migration 0119 deliberately defers the cost-tier router migration —
  there's no per-mode "use the budget tier for STORY_SPINE outline,
  premium for the TWO_PASS revise" knob yet.
- **`niche_goal_descriptions` is a single global JSON blob in app_settings.**
  Per-niche goal prompt overrides need a `niche_goal_prompts` table
  keyed by `(niche_id, goal_type)`. Filed as a follow-up; the JSON shape
  is the unblock-now compromise.
- **Some commands shown in `docs/architecture/niches-and-rag-modes.md`
  (`poindexter topics discover`, `poindexter niche create`,
  `poindexter niche set-goal`, `poindexter niche enable-source`) are
  forward-looking** — they describe the intended end state, not the
  current CLI surface.
