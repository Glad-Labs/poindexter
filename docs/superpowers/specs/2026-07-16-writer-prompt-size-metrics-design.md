# Writer prompt-size metrics

**Date:** 2026-07-16
**Status:** Approved (operator confirmed the design)
**Issue:** [Glad-Labs/poindexter#868](https://github.com/Glad-Labs/poindexter/issues/868)

## Problem

The niche/two-pass writer path (`atoms.two_pass_writer`, the primary
`canonical_blog` route since most tasks carry a `niche_slug`) assembles a
single draft prompt out of several independently-built context sources:

- up to 20 internal RAG snippets (`writer_rag_two_pass_snippet_limit`, ×
  `writer_rag_context_snippet_max_chars`) from the atom's own pgvector query
- a `research_context`/SOURCES block (`writer_core.py::_collect_research_context`)
  that itself layers three sources: caller-attached text,
  `ResearchService.build_context()` (which does its own internal-post-linking
  lookup plus fetched web-page text), and a third, independent pgvector call
  (`build_rag_context`) also hunting for related internal posts
- an optional dev_diary `context_bundle` (PRs/commits/decisions/audit)
- unbounded niche `writer_prompt_override` + operator
  `writing_style_reference`
- an optional `internal_grounding` prior-work-anchor section (#822)

A revise pass (`_revise_node`, fired when QA loops) sends a similarly-shaped
prompt again, 0-N times depending on `qa_rewrite_max_attempts`.

There's already code-level evidence this gets redundant — a comment in
`writer_core.py` documents a real case (task `f7a9ce17`) where
`research_context` alone hit ~9K chars, "~half redundant," from two renders
overlapping on a re-run. That specific case got a narrow fix (skip the
rebuild when the caller-attached blob is already a complete render), but
nothing measures the _steady-state_ total on a normal first pass, and there's
no way today to see which section actually dominates without reading code.

Separately: on the 8-16GB VRAM consumer hardware this pipeline targets,
`num_ctx` is dynamically clamped by `vram_budget.py::max_safe_num_ctx` to fit
available VRAM — so an oversized prompt isn't just a latency/quality concern,
it directly competes with the model's own weight + KV-cache footprint for the
same budget.

## Decision

Compute per-section character counts at the exact points in
`atoms/two_pass_writer.py` where each section is already built as a discrete
string (no re-parsing, no duplicate computation), thread them up through the
existing `result` dict → `metrics` dict chain that `prompt_template_key` /
`variant_id` already use, and let them land in `atom_runs.metrics` (JSONB) —
the same seam `content.generate_draft` already writes through. Add a new
Grafana panel row on the Pipeline dashboard reading `atom_runs` directly, the
same way the existing media Gate-2 panels do.

### Fork — sink mechanism: extend `StageResult.metrics` → `atom_runs` (chosen)

| Option                                                          | Verdict    | Why                                                                                                                                                                                                                                                                                                                                |
| --------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Extend existing `StageResult.metrics` → `atom_runs.metrics`** | **Chosen** | Zero new DB objects. Exact precedent: `prompt_template_key`, `prompt_template_version`, `variant_id` already flow this way from `two_pass_writer.run()` into `content.generate_draft`'s `StageResult`. Capture is already best-effort/non-blocking (`atom_runs_capture_enabled`).                                                  |
| Custom Prometheus metric                                        | Rejected   | The worker isn't a scrape target for app-level metrics today — Prometheus only scrapes `windows_exporter`/`nvidia-smi-exporter` directly. Would require standing up new export infrastructure for one metric family, inconsistent with how every comparable business metric (QA Rails, Findings, media Gate-2) is already exposed. |
| Dedicated new table/column                                      | Rejected   | `atom_runs.metrics` exists for exactly this purpose per its own docstring ("the composition -> outcome substrate"). A bespoke table would fragment observability instead of consolidating it, plus a migration for no schema benefit over an existing JSONB column.                                                                |

### Scope: two-pass draft + revise only

Covers `_draft_node` and `_revise_node` in `atoms/two_pass_writer.py` — the
primary `canonical_blog` path and the one with the documented redundancy.
The legacy `content_generator.generate_blog_post()` path (manual/pre-niche
tasks, a minority) is out of scope for this change; it has its own
internal-links mechanism (`_internal_links_cache`) and isn't part of the
redundancy this issue is chasing.

## Design

### Where it's measured

`generate_with_context()` in `ai_content_generator.py` is the one place
holding the true, fully-rendered prompt string — topic, angle, instructions,
and the snippet block, all substituted into the
`atoms.two_pass_writer.generate_with_context` template. It keeps returning a
bare `str` — changing its return type to a tuple would break ~40 existing
tests in `test_two_pass_writer.py` that monkeypatch it with fakes returning a
bare string (which silently unpacks into its first N characters instead of
raising, corrupting the mocked draft rather than failing loud). Instead it
gains an optional `prompt_metrics: dict[str, int] | None = None` parameter
that it populates in place (`{"prompt_chars": ..., "snippet_chars": ...}`)
when passed — a no-op for every existing caller, including test fakes (which
already accept `**_kw` and simply won't populate it). `_call_draft()` inside
`_draft_node` passes a dict and reads it back after the call; the equivalent
revise-side prompt is already a local variable in `_revise_node` before it's
sent, so no parameter is needed there — `len()` at the point it's built.

The section-level breakdown doesn't need a second measurement pass:
`_draft_node` already builds each context section as its own string before
concatenating it onto `instruction` (override block, `context_bundle`
ground-truth block, `research_context` SOURCES block, `internal_grounding`
section). Capturing `len()` at each of those existing assembly points, before
the `f"{instruction}\n\n---\n\n{section}"` concatenation, is the entire
implementation — no parsing, no drift risk against what's actually sent.

### Metric fields

Flat top-level keys on `content.generate_draft`'s `StageResult.metrics`,
matching the existing style (`content_length`, `model_used`, `variant_id`):

```
writer_prompt_draft_chars              # total chars of the fully-rendered draft prompt
writer_prompt_revise_chars             # sum across all revise-pass prompts (0 if no revision ran)
writer_prompt_revise_calls             # how many revise passes ran
writer_prompt_snippet_chars            # portion from two_pass_writer's own RAG snippets
writer_prompt_research_chars           # portion from research_context/SOURCES
writer_prompt_context_bundle_chars     # portion from dev_diary GROUND TRUTH bundle (usually 0)
writer_prompt_override_chars           # portion from niche writer_prompt_override + operator writing_style_directive
writer_prompt_internal_grounding_chars # portion from the #822 prior-work-anchor section
```

The five breakdown fields (`snippet` / `research` / `context_bundle` /
`override` / `internal_grounding`) are captured on the **draft** call only —
that's where the redundancy this issue is chasing actually lives. Revise
passes contribute only to the `revise_chars` / `revise_calls` aggregate, not
their own breakdown; a revise prompt is shaped differently (it sends
`aug_block` external-lookup results, not a fresh SOURCES/snippet stack) so a
matching breakdown wouldn't mean the same thing.

No pre-computed token estimate is stored. A `chars / 4` estimate is a
one-line addition to the Grafana query itself (`ROUND(chars / 4.0)`), which
keeps the heuristic visible and tunable on the dashboard instead of buried in
application code.

These fields are absent (not zero) for tasks that don't go through
`atoms.two_pass_writer` (the legacy path) — Grafana panels filter on
`atom = 'content.generate_draft' AND metrics ? 'writer_prompt_draft_chars'`
so legacy-path rows simply don't contribute rather than showing as zero.

### Wiring

`_draft_node` and `_revise_node` return their captured counts on `state`
(mirroring how `internal_grounding_anchor_injected` already rides state).
`two_pass_writer.run()` already surfaces extra fields from `result` into the
tuple `_generate_via_two_pass_atom()` unpacks in `writer_core.py` — the new
fields join `prompt_template_key` / `variant_id` in that same forwarding
block, landing in `metrics`, then `stage_metrics`, then
`StageResult(metrics=stage_metrics)`. No change to `content_generate_draft.py`
(the atom shim) — it already passes `context_updates` through unfiltered and
`atom_runs` capture reads `StageResult.metrics` directly.

### Grafana

New row on the **Pipeline** dashboard, next to the existing
"Quality — scores & output" / "QA — rejections & validation" rows, querying
`atom_runs WHERE atom = 'content.generate_draft'`:

1. **Timeseries** — avg `writer_prompt_draft_chars` over time (trend)
2. **Bar gauge** — avg of each breakdown field side by side (which section
   dominates, at a glance)
3. **Stat** — % of tasks with `writer_prompt_revise_calls > 0` and their avg
   added chars (cost of revision loops)
4. **Table** — most recent 25 tasks by total chars (`draft + revise`), for
   spot-checking outliers, joined to `pipeline_tasks` for the topic

Mirrors the existing `rawSql` pattern already used by the media Gate-2 panels
(`pipeline-merged.json`), e.g.:

```sql
SELECT
  created_at AS "time",
  (metrics ->> 'writer_prompt_draft_chars')::numeric AS "draft_chars",
  (metrics ->> 'writer_prompt_revise_chars')::numeric AS "revise_chars"
FROM atom_runs
WHERE atom = 'content.generate_draft'
  AND metrics ? 'writer_prompt_draft_chars'
  AND $__timeFilter(created_at)
ORDER BY created_at;
```

## Testing

Unit tests in `tests/unit/services/atoms/test_two_pass_writer.py` (existing
file — `generate_with_context()`'s own tests are colocated here, not in
`test_ai_content_generator.py`, since this file is its primary consumer),
`tests/unit/services/atoms/test_writer_atom_variant_hook.py`, and
`tests/unit/services/stages/test_generate_content.py` (both existing files
with proven patching helpers for `_generate_via_two_pass_atom` and the
legacy path respectively — see the implementation plan for the exact tests):

1. `generate_with_context()`, called with a `prompt_metrics` dict, populates
   it with `prompt_chars`/`snippet_chars` matching the actual rendered
   prompt for a known topic/angle/instructions/snippet fixture; called
   without one (existing behavior), returns the same content as before with
   no error.
2. `_draft_node` returns the five breakdown fields on state, each matching
   the length of the section string it was built from (override present /
   absent, context_bundle present / absent, internal_grounding present /
   absent — table-driven over the on/off combinations already covered by
   existing `_draft_node` tests).
3. `_revise_node` returns `revise_chars` for its own rendered prompt; running
   two revise passes back to back accumulates `revise_chars` and
   `revise_calls` correctly (covers the loop case, not just a single call).
4. `_generate_via_two_pass_atom()` forwards all eight fields into the
   `metrics` dict returned to `writer_core.execute()`.
5. Legacy (non-niche) path: `execute()`'s `stage_metrics` does **not** contain
   the `writer_prompt_*` keys (confirms the out-of-scope claim rather than
   leaving it implicit).

## Out of scope

- The legacy `content_generator.generate_blog_post()` path (non-niche tasks).
- Any alerting/threshold on prompt size — this ships observability only;
  once real distribution data exists, a follow-up can add a threshold if the
  data supports one.
- Collapsing the three overlapping internal-post-lookup mechanisms
  (`two_pass_writer`'s snippet RAG, `ResearchService._find_internal_links`,
  `build_rag_context`) into one — that's the likely _next_ step once this
  metric confirms where the redundancy actually lives, not part of this
  change.

## Routing

- **Issue:** filed on `Glad-Labs/poindexter` (writer/pipeline code is public
  substrate).
- **PR:** against `origin` (`Glad-Labs/glad-labs-stack`), body `Closes
Glad-Labs/poindexter#868`, auto-mirrors to the public repo.

## Task breakdown

1. Write failing unit tests per the Testing section above.
2. Give `generate_with_context()` the optional `prompt_metrics` output
   parameter; update its one call site in `_draft_node`.
3. Capture the four breakdown lengths in `_draft_node` at their existing
   assembly points; capture `revise_chars`/`revise_calls` in `_revise_node`.
4. Forward the eight fields through `two_pass_writer.run()`'s result dict →
   `_generate_via_two_pass_atom()` → `metrics` → `stage_metrics`.
5. Add the Grafana panel row to `infrastructure/grafana/dashboards/pipeline-merged.json`.
6. Run the touched unit suites green; lint/type-check the touched files.
7. Commit, push to `origin`, open PR closing the issue.
