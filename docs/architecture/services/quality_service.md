# Quality Service (Unified)

**File:** `src/cofounder_agent/services/quality_service.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_quality_service.py`
**Last reviewed:** 2026-04-30

## What it does

`UnifiedQualityService.evaluate(content, context, method)` runs a
single pass of the seven-criteria quality framework — clarity,
accuracy, completeness, relevance, SEO, readability, engagement —
against generated content and returns a `QualityAssessment` with the
per-dimension scores, an overall score (0-100), a pass/fail flag, and
a list of refinement suggestions.

This service is the _quality scoring_ layer (was content quality
score in the legacy pipeline). It complements `MultiModelQA`
(adversarial reviewer fan-out) and `content_validator` (programmatic
hard rules) — quality_service produces a numeric grade with feedback,
the others produce pass/fail decisions.

Three evaluation modes:

- **`PATTERN_BASED`** (default) — fast, deterministic. Heuristics
  delegated to `services.quality_scorers` (per-dimension functions),
  plus in-service artifact detection (photo metadata, leaked SDXL
  prompts, raw HTML), LLM pattern detection (cliché openers,
  buzzwords, filler phrases, generic transitions, repetitive starters,
  listicle titles, hedging, exclamation spam, formulaic structure),
  and Flesch-Kincaid grade-level scoring. No LLM calls.
- **`LLM_BASED`** — sends content + dimension JSON schema to the
  injected `llm_client`, parses the response. Falls back to
  `PATTERN_BASED` if no client is configured, the response has no
  parseable JSON, or the call errors.
- **`HYBRID`** — runs both, averages the dimension scores 50/50.
  Falls back to pattern-only if LLM is unavailable or itself fell
  back to pattern (avoids double-counting).

The service also tracks running statistics (`total_evaluations`,
`passing_count`, `failing_count`, `average_score`) for the lifetime
of the instance.

## Public API

- `UnifiedQualityService(model_router=None, database_service=None, qa_agent=None, llm_client=None)` —
  constructor. All deps optional; only `database_service` is needed
  for persistence and only `llm_client` for `LLM_BASED` / `HYBRID`.
- `await qs.evaluate(content, context=None, method=EvaluationMethod.PATTERN_BASED, store_result=True) -> QualityAssessment` —
  main entry point. `context` may include `topic`, `keywords`,
  `audience`, `target_length`, `task_id`, `content_id`. When
  `store_result=True` and `database_service` is set, persists to the
  `quality_evaluations` table via
  `database_service.create_quality_evaluation(...)`.
- `qs.detect_truncation(content) -> bool` — static helper; returns
  True if the LLM appears to have hit its output token limit.
  Truncated content cannot pass regardless of overall score.
- `qs.flesch_kincaid_grade_level(text) -> float` — static helper;
  delegates to `quality_scorers.flesch_kincaid_grade_level`.
- `qs.get_statistics() -> dict` — running counters and pass-rate.
- Module factories:
  - `get_quality_service(model_router=None, database_service=None, llm_client=None)`
  - `get_content_quality_service(...)` — backward-compat alias.
- Backward-compat aliases:
  - `ContentQualityService = UnifiedQualityService` (class alias).
- Re-exported types (so callers don't need to import from
  `quality_models`):
  - `EvaluationMethod` (`PATTERN_BASED`, `LLM_BASED`, `HYBRID`)
  - `QualityAssessment`, `QualityDimensions`, `QualityScore`,
    `RefinementType`

`QualityAssessment` shape (from `services.quality_models`):

- `dimensions: QualityDimensions` — seven 0-100 scores
- `overall_score: float` — 0-100 (after artifact + LLM-pattern penalties)
- `passing: bool` — `overall_score >= qa_pass_threshold` AND not truncated
- `feedback: str` — human-readable summary
- `suggestions: list[str]` — refinement hints
- `evaluation_method: EvaluationMethod`
- `content_length: int`, `word_count: int`
- `flesch_kincaid_grade_level: float`
- `truncation_detected: bool`

## Configuration

Pipeline-wide thresholds are loaded from `app_settings` via
`quality_scorers.qa_cfg()` (called as `_qa_cfg()` inside this service).
Every threshold has a sensible default — see `quality_scorers.py` for
the full list. The most-touched ones:

- `qa_pass_threshold` (default `70.0`) — overall-score cut-off for
  `passing=True`.
- `qa_critical_floor` (default `50.0`) — minimum-dimension floor (if
  clarity/readability/relevance falls below this the overall is capped
  at that value).
- `qa_artifact_penalty_per` (default `5.0`) — points subtracted per
  artifact category found (photo metadata, SDXL leak, etc.).
- `qa_artifact_penalty_max` (default `20.0`) — total artifact-penalty cap.
- `qa_fk_target_min` / `qa_fk_target_max` (defaults `8.0` / `12.0`) —
  Flesch-Kincaid grade-level acceptance band.

LLM-pattern detection (`_score_llm_patterns`) — the bulk of the
DB-tunable surface, all under the `qa_llm_*` prefix. Toggle the entire
detector with `qa_llm_patterns_enabled` (default `True`). Per-pattern
thresholds (selected — see `quality_service.py` lines 587-615 for the
full set):

- `qa_llm_buzzword_warn_threshold` (`3`) / `qa_llm_buzzword_fail_threshold` (`5`)
- `qa_llm_buzzword_penalty_per` (`0.5`) / `qa_llm_buzzword_max_penalty` (`5.0`)
- `qa_llm_filler_warn_threshold` (`2`) / `qa_llm_filler_fail_threshold` (`4`)
- `qa_llm_opener_penalty` (`5.0`) — cliché AI opener
- `qa_llm_transition_penalty_per` (`1.0`) / `qa_llm_transition_min_count` (`2`)
- `qa_llm_listicle_title_penalty` (`2.0`)
- `qa_llm_hedge_ratio_threshold` (`0.02`) / `qa_llm_hedge_penalty` (`2.0`)
- `qa_llm_repetitive_starter_penalty_per` (`1.0`) / `qa_llm_repetitive_min_count` (`3`)
- `qa_llm_formulaic_structure_penalty` (`2.0`) /
  `qa_llm_formulaic_min_avg_words` (`50`) / `qa_llm_formulaic_variance` (`0.2`)
- `qa_llm_exclamation_threshold` (`5`) / `qa_llm_exclamation_penalty_per` (`0.3`)

Per-dimension scoring tunables (clarity word-per-sentence bands,
accuracy citation bonuses, completeness word-count step function,
relevance keyword-density gates, SEO/engagement baselines) all live
under the `qa_*` prefix in `quality_scorers.qa_cfg()`.

## Dependencies

- **Reads from:**
  - `services.quality_scorers` — every per-dimension scorer plus the
    `qa_cfg()` config loader.
  - `services.quality_models` — `EvaluationMethod`, `QualityAssessment`,
    `QualityDimensions`, `QualityScore`, `RefinementType` types.
  - `services.site_config` — indirectly via `quality_scorers.qa_cfg()`
    and directly inside `_score_llm_patterns()` for the `qa_llm_*` keys.
  - Injected `llm_client` (when `method != PATTERN_BASED`).
- **Writes to:**
  - `quality_evaluations` table — only when `database_service` is
    injected and `store_result=True`. Persistence is best-effort; any
    exception is logged at error and swallowed (the assessment still
    returns to the caller).
- **External APIs:** none directly. The injected `llm_client` is what
  talks to Ollama/cloud.
- **Sister-service callers:**
  - `agents.blog_quality_agent` — wraps the service for the agent
    framework.
  - `services.phases.content_phases` — quality phase in the legacy
    phase-based pipeline.
  - `services.stages.quality_evaluation` — the GH#117 stage-based
    pipeline equivalent.
  - `main.py` — constructed at startup as `UnifiedQualityService()`.

## Failure modes

- **`evaluate()` raises** — outer try/except catches anything from the
  per-method branches, logs `[_evaluate] Evaluation failed: <e>` at
  ERROR with traceback, returns a stub assessment (all 5.0/10, passing
  False, evaluator `UnifiedQualityService-Error`). Pipeline keeps going.
- **LLM client returns malformed JSON** — `_evaluate_llm_based`
  catches `JSONDecodeError`/`KeyError`/`TypeError`/`ValueError` and
  falls back to `PATTERN_BASED`. The LLM call itself can throw — also
  caught, also fallback.
- **No `llm_client` and `LLM_BASED` requested** — logs warning,
  returns a `PATTERN_BASED` assessment.
- **`HYBRID` with LLM fallback** — if `_evaluate_llm_based` returned
  a `PATTERN_BASED` result (because of one of the failures above),
  hybrid returns the pattern result alone (no double-weighting).
- **Truncated content** — `_evaluate_pattern_based` always sets
  `passing=False` regardless of score, and inserts an explicit
  truncation suggestion at the top of the suggestions list. The score
  itself is NOT zeroed — the dimensions reflect what's there.
- **Persistence failure** — `_store_evaluation` catches all exceptions,
  logs at error. Caller sees a successful assessment; the row just
  isn't there. No retry.
- **Missing `task_id` / `content_id` in context** — `_store_evaluation`
  logs at debug and returns without writing. (Without an ID the row
  has nothing to FK against; silent skip is the right call.)
- **`qa_llm_patterns_enabled = false`** — entire LLM-pattern detector
  short-circuits to `(0.0, [])`. Score is unaffected by buzzwords,
  filler, etc. Useful when validating a deliberately-stylized post.

## Common ops

- **Lower the pass bar for a genre that scores low for legitimate
  reasons** (e.g. very short news posts):
  `poindexter set qa_pass_threshold 60`
- **Disable buzzword penalties temporarily:**
  `poindexter set qa_llm_buzzword_penalty_per 0`
  (or the nuclear option: `qa_llm_patterns_enabled false`).
- **Inspect recent quality evaluations:**
  `SELECT created_at, overall_score, passing, evaluation_method
 FROM quality_evaluations
 ORDER BY created_at DESC LIMIT 50;`
- **Find LLM-pattern-heavy posts** — search the suggestions JSON
  column on `quality_evaluations` for `"AI writing pattern"` to see
  how often the writer falls back on slop patterns by category.
- **Run a one-off evaluation in the REPL:**
  ```python
  import asyncio
  from services.quality_service import UnifiedQualityService, EvaluationMethod
  qs = UnifiedQualityService()
  result = asyncio.run(qs.evaluate("# Post\n\nBody...", context={"topic": "fastapi"}))
  print(result.overall_score, result.passing, result.suggestions)
  ```
- **Debug "why did this pass with a 50?"** — check the truncation flag
  AND the FK grade-level vs target band; the suggestions list usually
  spells out the mid-tier reasons.

## See also

- `docs/architecture/services/multi_model_qa.md` — companion adversarial
  reviewer; uses quality scoring as one of several inputs.
- `docs/architecture/services/content_validator.md` — companion
  programmatic hard-rule layer (no scoring; pure pass/fail).
- `docs/architecture/anti-hallucination.md` — full QA pipeline picture.
- `services.quality_scorers` — per-dimension scoring functions and
  the `qa_cfg()` settings dictionary.
- `services.quality_models` — data classes for assessments, dimensions,
  evaluation methods, refinement types.
