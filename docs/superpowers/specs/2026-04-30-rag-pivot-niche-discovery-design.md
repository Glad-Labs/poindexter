# RAG Pivot + Niche-Aware Topic Discovery — Design

**Status:** Approved (brainstorming complete, awaiting implementation plan)
**Date:** 2026-04-30
**Author:** Brainstorming session — Matt + Claude (Opus 4.7)
**Implementation skill to invoke next:** `superpowers:writing-plans`

## Why

The content pipeline currently summarizes external content (Hacker News, dev.to, web search). It produces ~20 4000-word drafts per day; most get rejected by Matt at the title/topic level — meaning we burn the full pipeline cost (research → draft → QA → image → SEO) on posts that lose at the very first human touch.

The economic argument:

> One LLM call to rank 5 topics ≪ one full content pipeline run. Gating at topic selection avoids burning compute on posts that get rejected at the title/topic level anyway. Improving upstream selection pays for itself many times over in downstream compute saved.

The strategic argument:

> Glad Labs is a primary source about building an AI-run business. We have 8,500 claude_session embeddings, 4,095 brain_knowledge entries, 3,460 audit events, decision logs, git history. We can produce content nobody else can — first-person reporting on what an autonomous AI business actually does — instead of yet another summary of someone else's content.

The flexibility argument:

> Glad Labs is the FIRST niche this system serves. The system MUST be configurable as a niche, not pinned to a niche. Future operators (a homestead permaculture journal, an indie game devlog, a real-estate investing newsletter) configure their own niche with their own goals, sources, and writer style.

## What changes

| Surface                 | Before                                               | After                                                                                                                                         |
| ----------------------- | ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Topic discovery cadence | Pure cron, generates topics per niche-blind defaults | Per-niche, reactive (fires when prior batch resolves), with a per-niche minute floor                                                          |
| Topic batch shape       | Single proposed topic at a time, approve/reject      | Batch of N (default 5) candidates, operator ranks 1-5 + optionally edits the #1                                                               |
| Candidate sources       | External feeds only (HN, dev.to, web_search)         | External feeds + internal RAG sources (claude_sessions, brain_knowledge, audit_events, git_commits, decision_log, memory_files, post_history) |
| Ranking signal          | Heuristic + multi-model QA scores after generation   | Hybrid embedding-cosine + LLM scorer against per-niche weighted goals, before generation                                                      |
| Writer stage            | Researches external sources, summarizes              | Per-niche writer mode; for Glad Labs (TWO_PASS): internal-context-only first draft, external-fact-augmentation second pass                    |
| Operator gate           | `topic_decision` approve/reject on a single proposal | Same gate, light-reuse: gate pauses pipeline; new MCP tools mutate the batch (rank, edit, resolve)                                            |

## Data model

### `niches`

The first-class configuration surface. One row per audience the operator publishes for.

```sql
CREATE TABLE niches (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            TEXT UNIQUE NOT NULL,           -- e.g. 'glad-labs', 'permaculture-journal'
    name            TEXT NOT NULL,
    active          BOOLEAN NOT NULL DEFAULT true,
    target_audience_tags TEXT[] NOT NULL DEFAULT '{}',
        -- e.g. {'indie-devs','ai-curious','prospects'}; multi-audience per niche
    writer_prompt_override TEXT,                    -- NULL = use default writer prompt
    writer_rag_mode TEXT NOT NULL DEFAULT 'TOPIC_ONLY',
        -- ENUM: TOPIC_ONLY | CITATION_BUDGET | STORY_SPINE | TWO_PASS
        -- Glad Labs default: TWO_PASS
    batch_size      INT NOT NULL DEFAULT 5,
    discovery_cadence_minute_floor INT NOT NULL DEFAULT 60,
        -- the per-niche minute floor for the reactive trigger
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `niche_goals`

Weighted goals the ranker scores candidates against.

```sql
CREATE TABLE niche_goals (
    niche_id   UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    goal_type  TEXT NOT NULL,
        -- ENUM: TRAFFIC | EDUCATION | BRAND | AUTHORITY | REVENUE
        --     | COMMUNITY | NICHE_DEPTH
    weight_pct INT NOT NULL CHECK (weight_pct BETWEEN 0 AND 100),
    PRIMARY KEY (niche_id, goal_type)
);
-- Per-niche constraint enforced at write time: SUM(weight_pct) FOR niche_id ≈ 100
```

### `niche_sources`

Per-niche toggle + weight for each candidate source plugin. Plugins themselves stay in `services/topic_sources/` (unchanged from today).

```sql
CREATE TABLE niche_sources (
    niche_id    UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    source_name TEXT NOT NULL,
        -- e.g. 'hackernews', 'devto', 'web_search', 'codebase', 'knowledge', 'internal_rag'
    enabled     BOOLEAN NOT NULL DEFAULT true,
    weight_pct  INT NOT NULL CHECK (weight_pct BETWEEN 0 AND 100),
        -- influences how many candidates this source contributes to the pre-rank pool
    PRIMARY KEY (niche_id, source_name)
);
```

### `topic_batches`

The unit of operator interaction.

```sql
CREATE TABLE topic_batches (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    niche_id     UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    status       TEXT NOT NULL,
        -- ENUM: open | resolved | expired
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ NOT NULL,
        -- e.g. created_at + 7 days; auto-expire so dead batches don't block forever
    resolved_at  TIMESTAMPTZ,
    picked_candidate_id UUID,
        -- references either topic_candidates(id) or internal_topic_candidates(id);
        -- the picked_candidate_kind column below disambiguates
    picked_candidate_kind TEXT
        -- ENUM: external | internal | NULL (until resolved)
);
-- One open batch per niche at a time:
CREATE UNIQUE INDEX uq_one_open_batch_per_niche
    ON topic_batches (niche_id) WHERE status = 'open';
```

### `topic_candidates` (external)

Candidates from external source plugins (HN, dev.to, web_search, etc).

```sql
CREATE TABLE topic_candidates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id        UUID NOT NULL REFERENCES topic_batches(id) ON DELETE CASCADE,
    niche_id        UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,  -- denorm
    source_name     TEXT NOT NULL,
    source_ref      TEXT NOT NULL,         -- URL or external id from the source
    title           TEXT NOT NULL,
    summary         TEXT,
    score           NUMERIC NOT NULL,      -- 0-100, from the LLM final scorer
    score_breakdown JSONB NOT NULL,        -- {goal_type: contribution_pct, ...}
    rank_in_batch   INT NOT NULL,          -- system's pre-operator rank, 1-N
    operator_rank   INT,                   -- operator's rank, NULL until they act
    operator_edited_topic TEXT,            -- operator's optional title rewrite
    operator_edited_angle TEXT,            -- operator's optional summary rewrite
    decay_factor    NUMERIC NOT NULL DEFAULT 1.0,
        -- multiplied by 0.7 each time the candidate carries forward unpicked
    carried_from_batch_id UUID REFERENCES topic_batches(id),
        -- NULL on first appearance
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (batch_id, source_name, source_ref)
);
```

### `internal_topic_candidates` (RAG-derived)

Different shape from external, separate table.

```sql
CREATE TABLE internal_topic_candidates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id        UUID NOT NULL REFERENCES topic_batches(id) ON DELETE CASCADE,
    niche_id        UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    source_kind     TEXT NOT NULL,
        -- ENUM: claude_session | brain_knowledge | audit_event
        --     | git_commit | decision_log | memory_file | post_history
    primary_ref     TEXT NOT NULL,
        -- the row id / commit sha / memory file path that anchors this candidate
    supporting_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
        -- list of {source_kind, ref, snippet} the RAG retriever pulled in alongside
    distilled_topic TEXT NOT NULL,
        -- what the LLM extracted as the proposed post topic
    distilled_angle TEXT NOT NULL,
        -- the "why this matters / what we learned" framing
    score           NUMERIC NOT NULL,
    score_breakdown JSONB NOT NULL,
    rank_in_batch   INT NOT NULL,
    operator_rank   INT,
    operator_edited_topic TEXT,
    operator_edited_angle TEXT,
    decay_factor    NUMERIC NOT NULL DEFAULT 1.0,
    carried_from_batch_id UUID REFERENCES topic_batches(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### `discovery_runs`

Observability: when did the discovery worker fire, what did it produce.

```sql
CREATE TABLE discovery_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    niche_id        UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    candidates_generated      INT,
    candidates_carried_forward INT,
    batch_id        UUID REFERENCES topic_batches(id),
    error           TEXT
);
```

## Flow

### 1. Discovery sweep (per niche, single-niche per sweep)

Triggered by either:

- Reactive: a previous `topic_batch.status` transitions to `resolved` → the next sweep is scheduled, subject to the per-niche minute floor.
- Operator-on-demand: `poindexter topics discover --niche <slug>` (also subject to floor; pass `--force` to bypass).

Steps:

1. Pick a niche where the floor has elapsed AND no open batch exists.
2. For each enabled `niche_source`, ask the source plugin to produce candidates proportional to its `weight_pct` (target pool size: ~20 total).
3. Carry-forward: load any candidates from the niche's last batch that weren't picked, multiply their `decay_factor` by 0.7, include in the pool.
4. Embedding pre-rank: compute candidate embeddings; for each enabled `niche_goal`, cosine-similarity against a precomputed "goal vector" (an embedding of a fixed prose description per goal_type, see "Goal vectors" below). Weighted-sum across goals × `weight_pct` × `decay_factor` → preliminary score. Take top 10.
5. LLM final scoring: one `glm-4.7-5090` call (per the project's local-LLM policy) prompts the model with the niche's goals + weights and the top-10 list, gets back JSON { candidate_id: { score, score_breakdown } }.
6. Take top `batch_size` (default 5). Insert into `topic_candidates` / `internal_topic_candidates` with `batch_id`, write a new `topic_batches` row with `status='open'`.
7. Open the `topic_decision` gate (light-reuse — gate just flags "operator action needed").
8. Record the run in `discovery_runs`.

### 2. Operator interaction

- `poindexter topics show-batch [--niche <slug>]` — print the current open batch with system rank, score, score_breakdown, source info.
- `poindexter topics rank-batch <batch_id> --order <id1>,<id2>,<id3>,<id4>,<id5>` — operator's 1-5 ranking.
- `poindexter topics edit-winner <batch_id> [--topic <text>] [--angle <text>]` — optional rewrite of the #1 candidate's title/angle (writes to `operator_edited_topic` / `operator_edited_angle`).
- `poindexter topics resolve-batch <batch_id>` — finalizes: marks the batch resolved, sets `picked_candidate_id` to operator_rank=1, advances that candidate to the research stage.
- `poindexter topics reject-batch <batch_id> [--reason <text>]` — discards the batch, schedules a fresh sweep.

Same surface gets exposed as MCP tools (so the voice bot can drive it) and Telegram-friendly summaries (so phone-only operation works). All three are thin wrappers over the same service-layer functions.

### 3. Pipeline handoff

When `resolve-batch` runs:

1. The picked candidate's `operator_edited_topic` (if set) overrides `title` / `distilled_topic`.
2. A new `content_task` is created with `topic`, `angle`, the niche's `slug`, the niche's `writer_rag_mode`, and a `topic_batch_id` provenance pointer.
3. The existing pipeline (research → draft → QA → SEO → finalize) runs as today, but the writer stage reads `writer_rag_mode` and adapts:
   - **TOPIC_ONLY** — writer gets topic + angle, runs one embedding query, dumps top-N internal snippets as background context.
   - **CITATION_BUDGET** — writer required to cite ≥ N internal sources; content_validator extends existing citation rules to enforce.
   - **STORY_SPINE** — preprocessing LLM call reads top 10-15 internal snippets and produces a structured outline; writer expands the outline.
   - **TWO_PASS** — first draft from internal context only (no external research call). Second pass: a fact-augmentation step pulls external for any claims that need outside support; writer revises. Glad Labs default.

## Goal vectors

Each `goal_type` has a fixed prose description used to compute its embedding once, then cached. Stored in code (not DB) since they're configuration, not user data:

```python
GOAL_DESCRIPTIONS = {
    "TRAFFIC":     "Topic likely to attract organic search traffic; trending keyword, broad appeal, evergreen demand.",
    "EDUCATION":   "Topic that teaches the reader something concrete and useful they didn't know before.",
    "BRAND":       "Topic that reinforces the operator's positioning and unique perspective.",
    "AUTHORITY":   "Topic that demonstrates the operator's depth and expertise on something specific.",
    "REVENUE":     "Topic that drives a commercial outcome: signups, sales, conversions, paid feature awareness.",
    "COMMUNITY":   "Topic that resonates with the operator's existing audience; sparks discussion, shares, replies.",
    "NICHE_DEPTH": "Topic that goes deep on the operator's niche specialty rather than broad-audience content.",
}
```

These are what the embedding pre-ranker scores candidates against. Cosine similarity to each goal's vector × the niche's weight for that goal × the candidate's decay_factor.

## Migration plan

1. **Keep** `services/topic_discovery.py` (source-plugin runner — fine as-is).
2. **Keep** `services/topic_dedup.py` and `services/topic_dedup_semantic.py` (dedup layer — fine as-is).
3. **Replace** `services/topic_proposal_service.py` entirely with a new `services/topic_batch_service.py` that orchestrates discovery → pre-rank → LLM score → batch creation → operator interaction → handoff. The old service's callers get repointed.
4. **Light-reuse** the existing `topic_decision` approval gate. Gate stays as the pause/release mechanism; the batch row holds the data; new MCP tools mutate the batch.
5. **Schema migration** seeds Glad Labs as the first niche, with goals + sources matching today's behavior so nothing regresses on day-zero.

Existing topic_proposals data: there shouldn't be much in flight (Matt's been rejecting heavily). Migration drops the old table after a short observation window.

## Out of scope

- **Pipeline gateway caps** — the "max 1 task awaiting draft approval / max 1 task awaiting publish approval" backpressure feature. Genuinely separate concern (different code paths, different gates table). Punted to its own spec.
- **Multi-niche concurrency** — sweeps run one niche at a time. Future-friendly schema (every table has `niche_id`) but the worker is serial for v1.
- **Goal-driven analytics dashboard** — once `score_breakdown` data accumulates, a Grafana panel showing "which goal weights drive your accept rate" would be useful, but that's a follow-up.
- **Auto-tuning niche weights** — the system could learn from operator rankings to adjust `niche_goals.weight_pct`, but v1 is operator-set only.

## Acceptance criteria

- [ ] Glad Labs niche exists in `niches` with `writer_rag_mode='TWO_PASS'` and at least 3 goals weighted to sum to 100.
- [ ] A discovery sweep produces a batch of 5 candidates with mixed external + internal sources.
- [ ] `poindexter topics show-batch` renders the batch with score breakdown per candidate.
- [ ] `poindexter topics rank-batch` + `edit-winner` + `resolve-batch` flow advances a content_task into the existing pipeline.
- [ ] The new `topic_batch_id` provenance pointer is queryable from the `posts` row backwards to the batch the topic came from.
- [ ] Carrying forward an unpicked candidate cuts its score by ≥30% on the next batch (decay_factor ≤ 0.7).
- [ ] No more than one open batch per niche exists at any time (UNIQUE partial index enforces).
- [ ] The reactive trigger doesn't fire more often than the niche's `discovery_cadence_minute_floor`.
- [ ] Glad Labs writer in TWO_PASS mode produces a draft whose first pass cites ≥3 internal sources before the external augmentation pass.
- [ ] All existing pipeline stages (research → QA → SEO → finalize) still run unchanged downstream of the new topic flow.

## Open questions for the implementation plan

These don't block the spec, but the plan should answer:

1. Goal vector cache invalidation strategy when the GOAL_DESCRIPTIONS dict ships an updated string.
2. Whether `internal_topic_candidates.supporting_refs` JSONB should include the actual snippet text or only refs (snippets cost storage; refs cost a re-fetch at writer-stage).
3. The exact prompt template for the LLM final-scorer pass — there are several reasonable shapes.
4. Whether `topic_decision` gate's existing approve/reject CLI should remain available as a fallback (operator approve = "advance #1 unedited", operator reject = "discard batch") — likely yes for backwards compat.
5. How the niche slug surfaces in the UI when batches from multiple niches eventually overlap (post-v1 concern).
