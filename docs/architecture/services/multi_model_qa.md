# Multi-Model QA

**File:** `src/cofounder_agent/modules/content/multi_model_qa.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_multi_model_qa.py`
**Last reviewed:** 2026-06-22

## What it does

`MultiModelQA.review()` runs a draft through several reviewers — a
deterministic programmatic validator, an LLM critic that uses a
different model than the writer, and a fan of opt-in gates (citation
verifier, topic-delivery, internal-consistency, image relevance, web
fact-check, URL verification, rendered-preview screenshot). Each
reviewer returns a `ReviewerResult`; the aggregator weighs the scores,
applies validator-warning penalties, and decides `approved=True/False`
based on a configurable threshold (default `qa_final_score_threshold=70`).

The point is adversarial coverage. Different reviewers have different
blind spots: regex catches what LLMs miss, a Claude critic catches what
local Ollama misses, web fact-check catches post-cutoff product claims
the LLM critic would otherwise reject as fabricated. The aggregator
trusts the score over individual `approved` booleans because critics
nitpick approval status more than they nitpick the number.

## Public API

- `MultiModelQA(pool, settings_service=None)` — constructor.
- `await qa.review(title, content, topic="", research_sources=None, preview_url=None) -> MultiModelResult` —
  full review, returns the aggregated decision.
- `MultiModelResult.approved: bool` — final decision.
- `MultiModelResult.final_score: float` — weighted-average score (0-100).
- `MultiModelResult.reviews: list[ReviewerResult]` — per-reviewer detail.
- `MultiModelResult.summary: str` — single-line human summary.
- `MultiModelResult.format_feedback_text(max_chars=4000) -> str` —
  human-readable critique for the approval UI; lands in
  `pipeline_tasks.qa_feedback` and `pipeline_versions.qa_feedback`.
- `format_qa_feedback_from_reviews(qa_reviews, final_score, approved, max_chars)` —
  module-level helper for callers that hold the serialized review
  dicts (e.g. finalize) without reconstructing the `MultiModelResult`.
- `ReviewerResult(reviewer, approved, score, feedback, provider)` —
  per-reviewer record; `provider` is one of `programmatic`, `ollama`,
  `anthropic`, `consistency_gate`, `vision_gate`, `web_factcheck`,
  `url_verifier`, `http_head`.

## Configuration

All settings come from `app_settings` via the injected `settings_service`.

Aggregation:

- `qa_validator_weight` (default `0.4`) — programmatic validator weight.
- `qa_critic_weight` (default `0.6`) — Ollama/Anthropic/Google critic weight.
- `qa_gate_weight` (default `0.3`) — weight for consistency, vision,
  web fact-check, URL gates.
- `qa_final_score_threshold` (default `70`) — approval cut-off.
- `qa_consistency_veto_threshold` (default `50`) — consistency gate is
  advisory unless its score drops below this.
- `content_validator_warning_qa_penalty` (default `3` points/warning) —
  per-warning penalty applied to the final aggregated score (GH-91).

Critic + writer:

- `pipeline_critic_model` (default `gemma3:27b`) — primary critic.
- `qa_fallback_critic_model` (default `gemma3:27b`) — fallback when the
  primary returns empty or errors.
- `qa_thinking_model_max_tokens` (default `8000`) — used for thinking
  models like `glm-4.7`, `qwen3:30b`.
- `qa_standard_max_tokens` (default `1500`) — non-thinking models.
- `qa_temperature` (default `0.3`) — both critic and gates.
- `electricity_rate_kwh` — local critic cost telemetry input.

Gates (mostly opt-in):

- `qa_web_factcheck_enabled` (default `true`) — DuckDuckGo verification
  of product/spec claims.
- `qa_web_factcheck_match_ratio` (default `0.6`) — fraction of a claim's
  key terms that must appear in the search snippets to mark it VERIFIED.
- `qa_web_factcheck_num_results` / `_snippet_chars` / `_min_term_len` /
  `_max_claims` (defaults `3` / `500` / `2` / `3`) — search breadth and the
  claim-matching heuristics (previously hardcoded literals in the rail).
- `qa_citation_verify_enabled` (default `true`) — HTTP HEAD for cited URLs.
- `qa_citation_max_dead_ratio` (default `0.30`).
- `qa_citation_min_count` (default `0`).
- `qa_citation_timeout_seconds` (default `8.0`).
- `qa_vision_check_enabled` (default `false`) — inline image relevance
  via vision model (~10s/image).
- `qa_vision_model` (default `qwen3-vl:30b`).
- `qa_vision_max_images` (default `3`).
- `qa_vision_pass_threshold` (default `60`).
- `qa_preview_screenshot_enabled` (default `false`) — full-page
  screenshot via Playwright + vision review.
- `qa_preview_vision_model` (default `qwen3-vl:30b`).
- `qa_preview_pass_threshold` (default `70`).
- `qa_preview_viewport_width/height` (defaults `1280` × `1024`).
- `qa_gate_max_tokens` / `qa_gate_timeout_seconds` (defaults `600` / `60`).
- `site_url`, `site_domain` — used by URL verifier to distinguish
  external citations from self-links.

## Dependencies

- **Reads from:**
  - `modules.content.content_validator.validate_content` and
    `verify_content_urls` (programmatic layer).
  - `services.citation_verifier` (HTTP HEAD path).
  - `services.web_research.WebResearcher` (DuckDuckGo fact check).
  - `services.preview_screenshot.capture_preview_screenshot` (Playwright).
  - `services.ollama_client.OllamaClient` (deliberately concrete — it
    exposes `configure_electricity` + `check_health` features the
    Provider Protocol does not).
  - `services.cost_lookup` for per-token cost; the critic model is the `pipeline_critic_model` per-step pin (the `cost_tier.*` resolution was removed in PR #1907 — it had replaced the deleted `services.model_router.get_model_router` after the 2026-05-08 Phase 2 cleanup). See [`../cost-tier-routing.md`](../cost-tier-routing.md).
- **Writes to:**
  - `audit_log` via `audit_log_bg` for `critic_fallback` events.
  - Cost rows return through the caller (the `qa.*` rail atoms that
    replaced the `cross_model_qa` stage in #355) which persists them to
    `cost_logs`.
- **External APIs:**
  - Local Ollama HTTP (critic, gates, vision, web-factcheck post-processing).
  - Outbound HTTP HEAD/GET for citation + URL verification + image download.
  - DuckDuckGo via `WebResearcher`.

## Failure modes

- **Programmatic validator critical issue (non-fact)** — short-circuits
  the entire review. Returns `approved=False`. Diagnose via
  `validation.issues`. The only "critical" exemption is
  `known_wrong_fact`, which gets a second chance from the web
  fact-check gate.
- **Ollama unreachable / health check times out** — critic + every gate
  silently skip. `approved` falls back to validator-only score.
  Visible in logs as `[MULTI_QA] Ollama not available` or `health check
timed out after 5s`. Watch the `critic_fallback` audit event for
  primary-critic failures that triggered the fallback chain.
- **Critic returns unparseable JSON** — logged as warning with first
  200 chars; reviewer skipped. If both primary and fallback
  unparseable, `critic_skipped=True` and the final score uses the
  validator only.
- **Dead links / fabricated URLs** — `url_verifier` returns
  `approved=False, score=max(0, 100 - 20*dead_count)`. Hard-blocks publish.
- **Vision model OOM / timeout** — gates return `None`, no veto
  applied. Increase `qa_gate_timeout_seconds` or disable the gate.
- **Web fact-check rate-limited** — DuckDuckGo errors are caught and
  logged as `[WEB_FACTCHECK] Failed (non-fatal)`. Reviewer skipped.

## Common ops

- **Soften the critic for a misbehaving niche:** raise
  `qa_final_score_threshold` (e.g. to 60) or lower `qa_critic_weight`.
- **Swap critic model:** `poindexter settings set pipeline_critic_model
ollama/glm-4.7` (or `anthropic/claude-haiku-4-5` for cloud — note
  cost guard interactions).
- **Disable vision QA:** `qa_vision_check_enabled=false` (default).
- **Enable rendered-preview gate (after Playwright install):**
  `qa_preview_screenshot_enabled=true` and ensure the calling stage
  passes `preview_url=/preview/{hash}`.
- **Audit recent critic fallbacks:**
  `SELECT created_at, payload FROM audit_log WHERE event_type = 'critic_fallback' ORDER BY created_at DESC LIMIT 50;`
- **Read the full feedback for a specific task:**
  `SELECT qa_feedback FROM pipeline_tasks WHERE task_id = '<uuid>';`

## See also

- `docs/architecture/anti-hallucination.md` — full reviewer catalogue +
  pipeline ordering.
- `docs/architecture/services/content_router_service.md` — the calling
  pipeline.
- `project_qa_critic_cutoff` (operator design note)
  — why the web fact-check gate exists.
- `project_multi_model_qa` (operator design note)
  — design vision for adversarial multi-model review.
