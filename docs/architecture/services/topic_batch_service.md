# Topic Batch Service

**File:** `src/cofounder_agent/services/topic_batch_service.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_topic_batch_service.py`
**Last reviewed:** 2026-04-30

## What it does

For a given niche, `TopicBatchService.run_sweep(niche_id=...)` discovers
fresh candidate topics from external sources (RSS, search, custom
plugins) AND from internal RAG sources (Claude sessions, brain
knowledge, audit events, decision log, memory files, post history),
embeds each candidate against the niche's goal vectors, pre-ranks the
top N per pool, scores the survivors with an LLM, writes a new
`topic_batches` row plus per-candidate rows (in `topic_candidates` for
external, `internal_topic_candidates` for internal), and opens the
operator approval gate. Carry-forward from the previous batch's
unpicked candidates is decay-multiplied (0.7^N at default) so old
ideas drift down naturally.

`show_batch`, `rank_batch`, `edit_winner`, `resolve_batch`,
`reject_batch` form the operator-side workflow that follows discovery.
`resolve_batch` hands the rank-1 candidate to the content pipeline by
inserting a `content_tasks` row with `topic_batch_id` provenance.

This service replaces the older `topic_proposal_service` and is per
the spec at
`docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md`.
Shipped 2026-04-30.

## Public API

- `TopicBatchService(pool)` — constructor.
- `await svc.run_sweep(niche_id) -> BatchSnapshot | None` — full
  discover → rank → write → gate flow. Returns `None` when the
  cadence floor hasn't elapsed or an open batch already exists for
  the niche.
- `await svc.show_batch(batch_id) -> BatchView` — unified ranked view
  of every candidate (external + internal merged), sorted by
  `effective_score = score * decay_factor` desc.
- `await svc.rank_batch(batch_id, ordered_candidate_ids)` — set
  `operator_rank` 1..N by position. Probes both candidate tables.
- `await svc.edit_winner(batch_id, topic=None, angle=None)` — rewrite
  the operator-edited topic/angle on the rank-1 candidate. Raises
  `ValueError` if nothing is ranked yet.
- `await svc.resolve_batch(batch_id)` — hand winner to the pipeline,
  flip batch status to `resolved`, record provenance.
- `await svc.reject_batch(batch_id, reason="")` — flip status to
  `expired` so the next sweep can claim the niche slot.
- Dataclasses: `BatchSnapshot`, `BatchView`, `CandidateView`.

## Configuration

All from `app_settings` via `site_config` (migration 0119 added these):

- `niche_top_n_per_pool` (default `5`) — top-N per pool fed into LLM
  final-score AND the slice inside `_embed_and_pre_rank`. Both ends
  of the funnel must stay consistent.
- `niche_carry_forward_decay_factor` (default `0.7`) — multiplier
  applied to each unpicked candidate's `decay_factor` per batch
  (0.7^3 ≈ 0.343 by batch 3).
- `niche_internal_rag_per_kind_limit` (default `4`) — per-kind cap on
  the internal RAG fetch (claude_session, brain_knowledge, etc).
- `niche_batch_expires_days` (default `7`) — `topic_batches.expires_at`.

Per-niche settings come from the `niches` table (set via NicheService),
not `app_settings`:

- `niche.batch_size` — final winner count.
- `niche.discovery_cadence_minute_floor` — min minutes between sweeps.
- `niche.writer_rag_mode` — threaded into `content_tasks` on resolve.

## Dependencies

- **Reads from:**
  - `niches`, `niche_sources`, `niche_goals` via `NicheService`.
  - `topic_batches`, `topic_candidates`, `internal_topic_candidates`,
    `discovery_runs` for cadence + carry-forward + state.
  - `services.topic_ranking.embed_text`, `goal_vector_for`,
    `weighted_cosine_score`, `apply_decay`, `llm_final_score` (lazy
    imports — see module docstring on the test-seam rationale).
  - `services.internal_rag_source.InternalRagSource` for internal
    candidates.
- **Writes to:**
  - `discovery_runs` (start + finish/error).
  - `topic_batches` (open → resolved/expired with
    `picked_candidate_id` + `picked_candidate_kind` provenance).
  - `topic_candidates` and `internal_topic_candidates` (per-candidate
    rows with `score`, `score_breakdown`, `rank_in_batch`,
    `decay_factor`, `operator_rank`, `operator_edited_topic`,
    `operator_edited_angle`).
  - `content_tasks` on `resolve_batch` — inserts a `pending` row with
    `topic_batch_id` set so provenance traces back from post to batch.
- **External APIs:** none directly. External topic sources (when
  wired) live in `services.topic_sources/` and call out from there.

## Failure modes

- **External discovery is a TODO** — `_discover_external` currently
  warns and returns `[]` if a niche has external sources configured.
  Wiring to `services.topic_discovery.TopicDiscovery` is a follow-up
  task. Look for the `external source(s) configured but
topic_discovery wiring is not yet implemented` warning in logs.
- **Cadence floor blocks sweep** — `_floor_elapsed` returns `False`,
  `run_sweep` logs "Sweep skipped — discovery cadence floor not
  elapsed" and returns `None`. Even errored runs count toward the
  floor (intentional — don't hammer external sources when something's
  wedged).
- **Open batch already exists** — `_open_batch_exists` short-circuits.
  Resolve or reject the existing batch first.
- **No rank-1 on resolve** — `resolve_batch` raises
  `ValueError("no operator_rank=1 candidate; rank first")`. Operator
  must call `rank_batch` first.
- **Discovery error** — caught, written to `discovery_runs.error`,
  re-raised. The `topic_batches` row is rolled back by the absence of
  the INSERT (it's the LAST step in the try block).
- **`topic_decision` gate is a stub** — `_open_topic_decision_gate`
  currently just emits a structured log line. Wiring to
  `services.approval_service` is a follow-up.

## Common ops

- **Trigger a sweep manually:**
  `await TopicBatchService(pool).run_sweep(niche_id=<uuid>)`.
- **List open batches per niche:**
  `SELECT n.slug, b.id, b.candidate_count, b.expires_at FROM topic_batches b JOIN niches n ON n.id = b.niche_id WHERE b.status = 'open';`
- **Inspect a batch:** `await svc.show_batch(batch_id=<uuid>)` returns
  a unified ranked list. UI-friendly.
- **Reject a stale open batch** to unblock the niche:
  `await svc.reject_batch(batch_id=<uuid>, reason="stale")`.
- **Tune carry-forward aggressiveness:** raise
  `niche_carry_forward_decay_factor` toward 1.0 to keep older
  candidates competitive longer; lower toward 0 to flush them faster.
- **Audit provenance for a published post:**
  `SELECT topic_batch_id FROM content_tasks WHERE task_id = '<uuid>';`
  then look up the batch + winner via `picked_candidate_id`.

## See also

- `docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md`
  — full design spec.
- `docs/architecture/niches-and-rag-modes.md` — niche/source/goal
  modeling and how `writer_rag_mode` flows downstream.
- `docs/architecture/services/content_router_service.md` — what
  happens after `resolve_batch` hands off the winner.
