# The writer opens on the internal thread an external topic connects to

**Status:** Design — pending spec review (operator brainstormed 2026-07-10).
**Issue:** [Glad-Labs/poindexter#822](https://github.com/Glad-Labs/poindexter/issues/822) — the **writer-consumer half** of the external-candidate internal-grounding pair. The discovery half (soft-penalize ungrounded external candidates, thread the winning match into the handoff **data-only**) shipped in PR [glad-labs-stack#2266](https://github.com/Glad-Labs/glad-labs-stack/pull/2266). This spec wires the writer to actually read that threaded match.
**Scope:** the **generation** path only — `modules/content/stages/generate_content.py` (a new read helper + one threaded kwarg), `modules/content/atoms/two_pass_writer.py` (a new state field + an optional prompt section), one `app_settings` key, tests, docs. The discovery/scoring path is untouched.
**Related, out of scope:** the writer's existing RAG snippet retrieval (`_embed_and_fetch_snippets`) and the session-grounding retrieval (#2200/#2203) are _different seams_ and are not modified; making the anchor wording DB-configurable, per-niche tuning, and linking non-post sources are follow-ups.

## Problem

The discovery half of #822 already does two things: it softly de-weights external topic candidates that have no first-party angle, and — when a grounded external candidate wins — it threads the specific internal match that justified it into the task handoff at `pipeline_versions.stage_data.metadata.internal_grounding` (shape `{source_table, source_id, preview, similarity}`). That match is the single most-related thing the operator's own corpus contains on the topic.

**Nothing reads it.** The signal is persisted and surfaced through the `content_tasks` view's `metadata` projection exactly like `angle`, but the writer never sees it. So a grounded external topic — one we picked _because_ we have prior work on it — gets drafted with no awareness of that prior work beyond whatever the writer's own generic RAG snippet fetch happens to surface. The stated goal ("amplify what the operator's business creates and discovers" — `feedback_amplify_operator_knowledge`) is only half-served: we _selected_ for the internal connection but don't _write_ to it.

The writer's own RAG fetch is not a substitute. `two_pass_writer._embed_and_fetch_snippets` retrieves the top-N nearest rows from `writer_rag_source_filter` (default `posts` **only**). The grounding match may be a `memory` or `claude_session` row the writer's fetch never touches, and even when it is a post, it arrives as one anonymous snippet among ~20 rather than the deliberately-chosen anchor the ranker already identified.

## Decisions (operator, 2026-07-10)

1. **Soft framing anchor, not a forced source.** The match is injected as an optional prompt section that invites the writer to connect its take to the prior work _where there's a genuine throughline_ — open on it, or weave it in, in our voice — and to link it when it's one of our published posts. The writer is explicitly told to ignore it when the connection is thin and **never** to parrot it. (Rejected: "priority cited source" — a stronger must-ground-and-cite pull reproduces the exact parroting/shoehorning failure `project_rag_corpus_pollution` documents; "link-only" — under-uses the signal and duplicates `internal_link_coherence`.)
2. **Inline section wording for v1.** The two section variants live inline in `_draft_node`, consistent with its existing `GROUND TRUTH` (context_bundle) and `SOURCES` (research_context) injected sections — which are also inline. Making the wording a DB-configurable `UnifiedPromptManager` key is a recorded follow-up, not v1. (This is a deliberate, operator-chosen exception to the general "prompts must be DB-configurable" preference, taken for consistency with the immediate sibling precedent.)
3. **Eligibility rides the writer's existing corpus knob.** The anchor is injected only when `grounding_ref.source_table` is in the writer's already-resolved `writer_rag_source_filter` allowlist (default `posts`). So by default only published-post anchors reach the writer; broadening the writer's corpus to `claude_sessions` opts those anchors in too. This filters on the existing structured seam (`feedback_filter_on_seams_not_slugs`) and defers the ops-content-exposure decision to the one knob that already governs what the writer may ground on.

## Goal

When a task carries an `internal_grounding` match and the match's source type is eligible, the writer receives one additional, clearly-soft prompt section naming the specific prior work this topic connects to — the scrubbed preview, and for posts a real inline URL — framed as "use it where genuinely relevant, in our voice; never force or parrot it." When there is no match, the flag is off, the source type is ineligible, or any resolution step fails, the writer prompt is byte-identical to today. Grounding can only _add_ an optional anchor; it can never degrade or zero a draft.

## Design

Approach: an **additive optional prompt section** in `_draft_node`, fed by a match read in `generate_content.py` and threaded through `two_pass_writer.run()`. This mirrors the three existing optional-context precedents in this exact writer — `writer_prompt_override`, `context_bundle` (→ `GROUND TRUTH`), and `research_context` (→ `SOURCES`) — each of which is read upstream, threaded into `run()`, seeded into `_State`, and rendered as a conditional section. Two rejected alternatives (a dedicated graph_def atom; post-draft link injection) are recorded at the end.

### 1. Read the match — `generate_content.py::_read_internal_grounding`

A new helper mirroring `_read_context_bundle` (same file). It reads the version-1 `pipeline_versions.stage_data` row directly (not through `database_service.get_task()`, which passes rows through `ModelConverter` and can reshape `metadata`) and digs out the match:

```python
async def _read_internal_grounding(
    self, database_service: Any, task_id: str,
) -> dict[str, Any] | None:
    """Pull stage_data.metadata.internal_grounding from the version-1 row.

    Set by topic_batch_service._handoff_to_pipeline on grounded external
    winners (#822 discovery half). None for internal / ungrounded winners,
    manual tasks, and dev_diary. Fail-open: any error → None (no anchor).
    """
    # SELECT stage_data FROM pipeline_versions WHERE task_id=$1 ORDER BY version ASC LIMIT 1
    # sd["metadata"]["internal_grounding"] → dict | None ; json-decode if str
```

`metadata` is one of the three keys the `content_tasks_update_redirect` trigger JSONB-merges, so `metadata.internal_grounding` survives the writer's later `pipeline_versions` upsert — the read is safe whether it runs before or after the writer, but (like `context_bundle`) it runs in this stage, before the writer, for a guaranteed-clean view. Returns `None` on any failure (logged at warning), so the writer simply gets no anchor.

### 2. Thread into the writer — `_generate_via_two_pass_atom` + `two_pass_writer.run()`

- `_generate_via_two_pass_atom` calls the new helper and passes the result into `two_pass_writer.run(internal_grounding=...)`, alongside the existing `context_bundle=` / `research_context=` kwargs.
- `two_pass_writer.run()` gains an `internal_grounding: dict | None = None` kwarg and seeds it into the initial `_State`.
- `_State` gains an `internal_grounding: dict[str, Any]` field (documented like its siblings). It is a plain dict → msgpack-clean for the MemorySaver checkpointer (no `_POOL_REGISTRY`-style out-of-band handling needed).

### 3. Render the section — `_draft_node`

A new conditional block appended **after** the `SOURCES` section (so authoritative grounding — `GROUND TRUTH`, `SOURCES` — precedes the softer "prior work" nudge). Guarded, eligibility-checked, scrubbed, and fail-open:

```python
ig = state.get("internal_grounding") or {}
if _internal_grounding_enabled(site_config) and ig:
    section = _build_internal_grounding_section(
        ig, site_config=site_config, pool=pool,
    )
    if section:
        instruction = f"{instruction}\n\n---\n\n{section}"
```

`_build_internal_grounding_section` (new, module-level, independently testable) does, in order — returning `""` (no section) at any failing step:

1. **Eligibility.** `ig["source_table"]` must be in `_resolve_snippet_source_filter(site_config)` (the writer's existing corpus allowlist, default `("posts",)`). Ineligible → `""`. This is what keeps ops content (`memory` / `claude_sessions`) out of the public writer prompt by default.
2. **Scrub, fail-closed.** `preview = scrub_rag_text(ig.get("preview") or "")`; on exception → `""` (never inject unscrubbed operator text — the same fail-closed backstop `_embed_and_fetch_snippets` applies to every snippet). Empty scrubbed preview → `""`.
3. **URL resolution (posts only).** For `source_table == "posts"`, `SELECT slug, title FROM posts WHERE id = $1`; build `url = f"{site_url}/posts/{slug}"` where `site_url = site_config.get("site_url", "")` (the established form — `ai_content_generator.py:165` presents internal post links to this same writer exactly this way), falling back to the relative `/posts/{slug}` when `site_url` is unset (a pipeline-recognized internal-link form). On lookup failure → treat as a non-linkable source (framing-only variant), never raise.
4. **Compose one of two inline variants:**
   - **Linkable (a post, url resolved)** — invites opening on / weaving in the connection and linking inline via the exact URL, with explicit "ignore if thin, never copy verbatim."
   - **Non-linkable (eligible non-post, or post whose URL didn't resolve)** — invites drawing on the prior experience in our voice, with "don't quote it, don't force it, don't name internal tooling/systems," a tighter guard because the source is ops-adjacent even after scrub.

Both variants are worded as soft and optional, in the register of the existing sibling sections. The exact copy is finalized in the plan; the anti-parrot / ignore-if-thin / no-fake-URL clauses are required content.

### 4. Setting (DB-first — seeded in `settings_defaults.py`, not a migration)

| Key                                 | Default | Purpose                                                                                                                                                         |
| ----------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `writer_internal_grounding_enabled` | `true`  | master switch for the writer-side anchor; independent of the discovery-side `niche_external_grounding_enabled`. `false` fully restores the prior writer prompt. |

Read via `site_config.get` in `_draft_node`, resolved through a `_internal_grounding_enabled(site_config)` helper that follows the file's established default-on / fail-safe pattern (`_expansion_enabled` is the template). No new corpus/threshold keys — eligibility reuses `writer_rag_source_filter`, and _whether_ a match exists was already decided at discovery time.

### 5. Observability (`feedback_grafana_everything`)

The writer already returns a metrics dict up through `run()` into the caller stage (feeding `capability_outcomes` / atom metrics). Record two cheap fields on it so anchor usage is queryable without a new table:

- `internal_grounding_anchor_injected: bool` — did an anchor actually reach the prompt this run.
- `internal_grounding_source_table: str | None` — which corpus type it came from.

This lets a Pipeline-board panel (or an ad-hoc query) answer "how often is the writer actually opening on a grounded internal thread, and from which source type" — the signal that tells us whether the #822 pair is doing its job end to end. A dedicated panel is optional in this PR (the field is the durable part); the discovery-side already has its penalized-candidates panel.

## Error handling — fail-open matrix

| Condition                                        | Result                                   |
| ------------------------------------------------ | ---------------------------------------- |
| No `internal_grounding` on the task              | no section (byte-identical prompt)       |
| `writer_internal_grounding_enabled=false`        | no section                               |
| `source_table` not in `writer_rag_source_filter` | no section (default: non-posts excluded) |
| `scrub_rag_text` raises                          | no section (fail-closed on leak risk)    |
| scrubbed preview empty                           | no section                               |
| post slug/URL lookup fails                       | framing-only variant (no link)           |
| read helper raises                               | `None` → no section                      |

No path raises into the writer graph; the worst case is "no anchor," which is exactly today's behavior.

## Testing (`feedback_docs_and_tests_default`)

- `test_generate_content.py` (or the stage's test module) — `_read_internal_grounding` returns the dict from a seeded version-1 row; returns `None` for a row with no match, a malformed `stage_data`, and a raising pool.
- `test_two_pass_writer.py` — `_build_internal_grounding_section`:
  - eligible **post** match → section present, contains the scrubbed preview and a `/posts/<slug>` link;
  - eligible **non-post** match (with `writer_rag_source_filter` broadened to include it) → non-linkable variant, no URL;
  - **ineligible** source (`memory` under the default `posts`-only filter) → `""`;
  - `scrub_rag_text` raising → `""` (monkeypatched to raise);
  - `writer_internal_grounding_enabled=false` → `_draft_node` appends no section;
  - no match → no section;
  - the section is appended **after** the `SOURCES` block when both are present.
- `test_settings_defaults.py` — `writer_internal_grounding_enabled` exists with default `true`, boolean type, sensible `owner`; the qa-key-span guard still holds (this key is not a `qa_` key).
- Scrub assertion — a preview containing a known operator-private token (per `rag_scrub`'s own fixtures) is scrubbed before it appears in the composed section.

## Rejected alternatives

- **A dedicated graph_def atom** (`content.apply_internal_grounding`) before `content.generate_draft`: the writer is a self-contained LangGraph sub-graph, so an outer atom still can't inject into `_draft_node`'s prompt without `_draft_node` consuming a new state field anyway — the atom would be pure ceremony around a change we still have to make inside the writer. Adds a node + `requires`/`produces` contract + graph re-seed for one prompt section. YAGNI.
- **Post-draft link injection via `internal_link_coherence`**: this is the "link-only" behavior the operator rejected in brainstorming. It produces no _framing_ anchor, only fires for posts, and largely duplicates a stage that already adds related-post links.

## Out of scope (follow-ups)

- DB-configurable anchor wording (`UnifiedPromptManager` key + SKILL.md/YAML default) — v1 is inline per decision 2.
- Linking non-post sources (no public URL exists for `memory` / `claude_session` today).
- Per-niche enable/threshold overrides (the global switch is sufficient until a second niche needs a different posture).
- A calibrated similarity floor for _writer_ injection (the discovery-side threshold already gates whether a match exists; the writer trusts that decision rather than re-thresholding).
