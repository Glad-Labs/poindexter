# Model Router (deleted)

**Status: REMOVED 2026-05-08.** `services/model_router.py` + its companions (`usage_tracker.py`, `model_constants.py`) were deleted in the Phase 2 cleanup once LiteLLM had been on prod long enough to validate the cutover.

For the equivalent functionality today:

- **Per-call provider routing + cost tracking + retries:** [`services/litellm_provider.md`](litellm_provider.md) — the `LiteLLMProvider` plugin is the primary LLM router on prod for all four cost tiers (`plugin.llm_provider.primary.{free,budget,standard,premium}='litellm'`).
- **Model selection:** [`../cost-tier-routing.md`](../cost-tier-routing.md) — each step reads its own `*_model` `app_settings` pin (e.g. `pipeline_writer_model`, `pipeline_critic_model`); the `cost_tier.<tier>.model` indirection was removed in PR #1907.
- **Per-1K-token cost lookups:** `services/cost_lookup.py` — wraps `litellm.model_cost`.
