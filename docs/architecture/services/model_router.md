# Model Router (deleted)

**Status: REMOVED 2026-05-08.** `services/model_router.py` + its companions (`usage_tracker.py`, `model_constants.py`) were deleted in the Phase 2 cleanup once LiteLLM had been on prod long enough to validate the cutover.

For the equivalent functionality today:

- **Per-call provider routing + cost tracking + retries:** [`services/litellm_provider.md`](litellm_provider.md) — the `LiteLLMProvider` plugin is the primary LLM router on prod for all four cost tiers (`plugin.llm_provider.primary.{free,budget,standard,premium}='litellm'`).
- **Cost-tier model selection:** [`../cost-tier-routing.md`](../cost-tier-routing.md) — callers do `model = await resolve_tier_model(pool, "standard")` from `services/llm_providers/dispatcher.py`; operators tune via `app_settings.cost_tier.<tier>.model` rows.
- **Per-1K-token cost lookups:** `services/cost_lookup.py` — wraps `litellm.model_cost`.
