# External topic candidates must earn their slot with internal grounding, not popularity alone

**Status:** Design — pending spec review (operator brainstormed 2026-07-10).
**Issue:** [Glad-Labs/poindexter#822](https://github.com/Glad-Labs/poindexter/issues/822) — the second, complementary half of the "amplify operator knowledge" pair. #820 (the internal source picks storyworthy material) shipped in PR #2093; this is the external-side counterpart, still open.
**Scope:** topic **discovery** scoring only — `services/topic_batch_service.py::_embed_and_pre_rank`, a new `services/topic_grounding.py`, the batch write + handoff, and four `app_settings`. The writer/generation path is untouched.
**Related, out of scope:** the writer-side session-grounding retrieval (#2200/#2203) is a _different seam_ (retrieval during generation) and is not modified; consuming the threaded grounding match inside the writer prompt is a deliberate follow-up (this spec ships the signal **data-only**); graded/continuous penalty and `git_commit` grounding are follow-ups.

## Problem

The content system's stated job is amplifying what the operator's business creates and discovers. External topic sources (hackernews / devto / web_search) are a **popularity signal** — what the audience is talking about right now — but an external headline with **no first-party angle** produces exactly the zero-new-value rewrite the system positions against (`feedback_amplify_operator_knowledge`: "external = popularity signal for our spin; no zero-value rewrites").

Today, `TopicBatchService` scores an external candidate against **niche goal vectors only** — there is no signal for "do _we_ already have material on this?". The funnel:

1. read pooled candidates (external + internal) per niche
2. dedup
3. deterministic topic-sanity filter (title word-quality — no corpus query)
4. **embedding pre-rank** against niche goal vectors → top-N
5. **LLM final-score** on the top-N → batch winners
6. source-diversity cap (`niche_internal_rag_batch_share_cap` — _limits_ internal share; the opposite concern)

Nowhere in 3–6 does an external candidate's score reflect whether the operator's corpus contains related material. A popular-but-ungrounded headline wins on goal-match + popularity and burns a full generation+QA run before anyone notices there was never a first-party angle — the same "burns a full generation+QA run" failure #820 documented for niche-blind internal topics, mirrored onto the external side.

## Decisions (operator, 2026-07-10)

1. **Soft penalty, not a hard gate.** An ungrounded external candidate gets a score _multiplier_ < 1, so it can still win if popularity is high enough and nothing better exists. Preserves content volume; symmetric with the existing soft `decay_factor` and soft share-cap. (Rejected: hard opt-in gate — starves thin-external sweeps; boost-only — a viral ungrounded headline still wins, barely changing behavior.)
2. **Grounding corpus = posts + memory + sessions.** `post_history` + `decision_log`/`memory_file` + `claude_session`. Matches #822's own list and the writer-side grounding filter. **Excludes** `audit_event` / `brain_knowledge` ops-noise so a status log can never manufacture false grounding.
3. **Half-2 (handoff threading) ships data-only.** When a grounded external candidate wins, the specific internal match that justified it is threaded into the task handoff `metadata`. Actually _reading_ it in the writer prompt is a separable follow-up.

## Goal

Every external candidate's pre-rank score reflects both popularity/goal-match **and** whether we have something of our own to add. Grounded candidates rise; ungrounded ones are softly de-weighted through the whole funnel (top-N cut, effective-score sort, and the shorter list the LLM final-scorer sees). Internal candidates — grounded by definition — are untouched. The match that justified a grounded external win rides along into the handoff so a later follow-up can anchor the writer's opening on it.

## Design

Approach: a **deterministic multiplier folded into the pre-rank**, computed from a single pgvector nearest-neighbor query that rides the candidate vector already embedded for goal-scoring. This is the only insertion point where the penalty shapes _selection_ (not just final tie-breaking), and it mirrors the existing `apply_decay` seam. Two rejected alternatives are recorded at the end.

### 1. New unit — `services/topic_grounding.py`

A focused, independently-testable module (keeps the already ~1,250-line `topic_batch_service.py` from growing; grounding is a distinct concern from batch orchestration).

```python
@dataclass
class GroundingMatch:
    source_table: str      # 'posts' | 'memory' | 'claude_sessions'
    source_id: str
    preview: str           # embeddings.text_preview of the nearest row
    similarity: float      # cosine similarity in [0, 1]

@dataclass
class GroundingResult:
    similarity: float | None   # best cosine similarity; None on fail-open
    grounded: bool             # similarity >= threshold (True on fail-open)
    match: GroundingMatch | None

async def internal_grounding(
    pool, candidate_vec: list[float], *, site_config: SiteConfig,
) -> GroundingResult: ...
```

**Query.** One statement over the union of the configured corpus kinds, top-1 by cosine distance. `<=>` is pgvector **cosine distance**, so `similarity = 1 - distance` — getting this inversion right is what makes the penalty fire in the correct direction:

```sql
SELECT source_table, source_id, text_preview,
       1 - (embedding <=> $1::vector) AS similarity
  FROM embeddings
 WHERE source_table = ANY($2::text[])
 ORDER BY embedding <=> $1::vector
 LIMIT 1
```

- Corpus kinds resolve through the **existing** `internal_rag_source` kind→source_table map (`post_history`→`posts`, `decision_log`/`memory_file`→`memory`, `claude_session`→`claude_sessions`), reusing that vocabulary rather than inventing a parallel one.
- **No time window** — unlike #820's recency lookback (a _sourcing_ concern), grounding is a _coverage_ question: an old post still grounds us. Deliberate divergence, called out in a comment.
- Vector passed in pgvector text form (`"[" + ",".join(...) + "]"`), the established pattern from `internal_rag_source._fetch_recent_snippets` / `embeddings_db.py` (no asyncpg vector codec).
- **Fail-open:** any exception (dimension mismatch, missing extension, stub pool) or an empty/blank vector → `GroundingResult(similarity=None, grounded=True, match=None)`. We never penalize on infrastructure failure and never sink the sweep — same posture as the dedup and empty-batch guards in `run_sweep`.

### 2. Wiring into `_embed_and_pre_rank` (external branch only)

The external loop already computes the candidate vector inside the `score_one` closure (`vec = await embed_text(text, ...)`) but currently discards it, returning only `(decayed_score, breakdown)`. **Refactor `score_one` to also return the vec** — `(score, breakdown, vec)` — so the grounding query reuses the embedding already computed for goal-scoring (no second embed call). Then, guarded by the master switch and **only** in the external branch (`int_scored` is untouched: internal candidates come _from_ the corpus, so grounding is tautological → implicit multiplier 1.0):

```python
score, breakdown, vec = await score_one(text, decay)   # score_one now also returns vec
grounding_match = None
if grounding_enabled and vec:
    g = await internal_grounding(self._pool, vec, site_config=self._site_config)
    if not g.grounded:
        score *= penalty_factor                        # soft penalty
    breakdown["_grounding"] = g.similarity if g.similarity is not None else 1.0
    grounding_match = g.match                           # stashed for the batch write
```

`ScoredCandidate` gains an optional `grounding_match: GroundingMatch | None = None` field carrying the match through to `_write_batch`. (The internal branch calls `score_one` too — it just ignores the returned vec and never runs the grounding block.)

Placing the multiplier here means it flows through **every** downstream stage: the `pool_external[:top_n]` cut, the `effective_score` sort, and the top-N list handed to `llm_final_score`.

### 3. Persist the match — `_write_batch` + migration

- **Observability slice** already free: `breakdown["_grounding"]` (a float) lands in the `topic_candidates.score_breakdown` jsonb that is already written and read back — no schema change for the similarity number.
- **The match object** needs a home. External candidates have **no** `supporting_refs` column (that exists only on `internal_topic_candidates`), and `score_breakdown` is typed `dict[str, float]` — overloading it with a nested object breaks the contract. So: a **new nullable `grounding_ref jsonb` column on `topic_candidates`** via a fresh timestamped migration (`grounding_ref jsonb NULL`), written from `c.grounding_match` (serialized) in the external INSERT. Schema DDL in a migration file is correct here (this is not seed data).

### 4. Read-back + handoff threading (data-only) — `CandidateView` + `_handoff_to_pipeline`

- `CandidateView` gains `grounding_ref: dict | None`, populated in `_load_open_batch` from the new column (internal rows → `None`).
- `_handoff_to_pipeline` adds one key to the existing `stage_data["metadata"]` block (alongside `angle` / `summary` / `discovered_by` / `niche_slug`):

  ```python
  "internal_grounding": winner.grounding_ref,   # {source_table, source_id, preview, similarity} or None
  ```

  This persists to `pipeline_versions.stage_data` → surfaced to the writer dispatcher via the `content_tasks` view's `metadata` projection, exactly like `angle` today. **No consumer is wired in this PR** — the writer prompt reading `internal_grounding` is the deliberate follow-up. The metadata key is additive and unknown-key-safe for every existing reader.

### 5. Settings (DB-first — seeded in `settings_defaults.py`, not a migration)

| Key                                       | Default                                                | Purpose                                                                 |
| ----------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------- |
| `niche_external_grounding_enabled`        | `true`                                                 | master switch; `false` fully restores prior behavior                    |
| `niche_external_grounding_source_kinds`   | `post_history,decision_log,memory_file,claude_session` | corpus (decision 2); CSV of `internal_rag` source-kind names            |
| `niche_external_grounding_threshold`      | `0.55`                                                 | cosine similarity ≥ this ⇒ grounded. **Provisional** — see Calibration  |
| `niche_external_grounding_penalty_factor` | `0.6`                                                  | multiplier applied to an ungrounded external candidate's pre-rank score |

Read via `site_config.get_*` in `_embed_and_pre_rank`; `source_kinds` parsed to a `source_table` list once per sweep and passed to `internal_grounding`. Unknown kinds are dropped with a warning (mirrors `internal_rag_source`'s `VALID_SOURCE_KINDS` validation).

### 6. Observability (`feedback_grafana_everything`)

- **Per-candidate:** `score_breakdown._grounding` (the similarity) is already persisted → visible in `show_batch` / the topics console.
- **Finding:** when a candidate is penalized, emit an advisory `finding` of kind `external_topic_ungrounded` (dedup-keyed per niche+sweep, aggregated) → routable to the Findings board, off by default routing (advisory).
- **Panel:** a small Pipeline-board panel — count of external candidates penalized for missing grounding, per sweep — so a niche that's _all_ ungrounded external (a corpus-coverage gap worth knowing about) is visible rather than silent.

## Calibration

`niche_external_grounding_threshold = 0.55` is a provisional guess. Before/after enabling, run a dry pass logging `_grounding` similarities across a real sweep (the values are persisted in `score_breakdown`) and pick the threshold from the observed distribution — the knee between "genuinely related" and "coincidental token overlap." This is a tuning task, not a code change (the threshold is an `app_setting`); noted here so the implementer captures the distribution during verification rather than shipping `0.55` unexamined.

## Testing (`feedback_docs_and_tests_default`)

- `tests/unit/services/test_topic_grounding.py` — grounded (sim ≥ threshold), ungrounded (sim < threshold), fail-open on query error, empty/blank vector, empty corpus-kinds list. Stub pool returning canned rows.
- `test_topic_batch_service.py` — the multiplier lands on an ungrounded **external** candidate and **not** on any internal candidate; `enabled=false` is a no-op; `breakdown["_grounding"]` is populated; `grounding_match` survives into the persisted row.
- Handoff test — a grounded external winner threads `metadata.internal_grounding`; an ungrounded/internal winner threads `None`; the key's presence never breaks existing metadata readers.
- `test_settings_defaults.py` — the four new keys exist with the documented defaults and types.
- Migration smoke — `grounding_ref` column adds cleanly and is nullable (no-op on fresh + real add on prod).

## Rejected alternatives

- **Grounding pass over the top-N only** (after pre-rank, before `llm_final_score`): cheaper (fewer queries) but grounding then can't influence which candidates _make_ the top-N cut — an ungrounded high-goal-match topic crowds out a grounded slightly-lower one before grounding is consulted. Wrong altitude; partially defeats #822.
- **Feed grounding into the LLM final-scorer prompt:** violates `feedback_calculated_vs_generated` — grounding is a deterministic cosine number; laundering it through an LLM makes it non-reproducible and un-tunable.

## Out of scope (follow-ups)

- Writer-prompt consumption of `metadata.internal_grounding` (this PR ships the data only).
- Graded/continuous penalty (a smooth `penalty_factor → 1.0` ramp across the threshold) — v1 is binary.
- `git_commit` grounding (not in the `embeddings` table today).
- Per-niche threshold/penalty overrides (the global `app_settings` are sufficient until a second niche needs a different bar).
