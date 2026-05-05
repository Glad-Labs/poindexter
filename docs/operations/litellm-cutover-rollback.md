# LiteLLM Cutover — Rollback Runbook

**Issue:** Glad-Labs/poindexter#372
**Migration:** `services/migrations/0160_litellm_cutover_default_providers.py`
**Affected component:** `services/llm_providers/dispatcher.py` —
the per-tier `plugin.llm_provider.primary.<tier>` resolution path
used by `get_provider()` / `dispatch_complete()` / `dispatch_embed()`.

This runbook documents the two ways to revert from `LiteLLMProvider`
back to `OllamaNativeProvider` if the cutover causes a regression
post-merge.

## Symptoms that should trigger a rollback

Any of:

- Cost dashboard's daily/monthly spend lines spike unexpectedly after
  the migration applies (the cutover should not change spend on local
  Ollama; a spike means LiteLLM is routing to a paid backend somehow).
- `cost_logs` rows start appearing with `provider != 'ollama'` for
  workflows that were 100% local before.
- Pipeline tasks fail with `LiteLLMConfigError`, `BadRequestError:
  Unable to map your input to a model`, or `ConnectionError` against
  an unexpected host.
- Langfuse traces stop landing entirely (vs slowing or partial loss).
- The writer pipeline starts producing empty drafts (the LiteLLM path
  doesn't have the same thinking-trace fallback that
  `OllamaClient.generate` has — see `services/ollama_client.py:495-513`).

## Rollback Path 1 — settings flip (fast, ~30s)

Use this when you want to revert one tier or all tiers without
reverting the migration record itself. The tier flip propagates on
the **next dispatcher call** — no DB-level migration runner needed.
The worker still needs a reload because settings are cached at
lifespan startup (see `services/site_config.py`).

```powershell
# Per tier — set only the ones you want to revert
poindexter settings set plugin.llm_provider.primary.standard ollama_native
poindexter settings set plugin.llm_provider.primary.budget   ollama_native
poindexter settings set plugin.llm_provider.primary.free     ollama_native
poindexter settings set plugin.llm_provider.primary.premium  ollama_native
poindexter settings set plugin.llm_provider.primary.flagship ollama_native

# Reload the worker so the SiteConfig in-memory cache picks up the change.
# (Settings cache loads at lifespan startup; runtime DB writes don't
# invalidate it on their own.)
docker compose restart cofounder-agent

# Verify
poindexter settings list 2>&1 | findstr "plugin.llm_provider.primary"
```

The dispatcher reads the row on every call (no cache there — see
`services/llm_providers/dispatcher.py:get_provider_name`), but the
**rest** of the worker reads from `SiteConfig`. The restart is purely
defensive — without it, anything other than `dispatcher.py` that
checks these keys would still see the LiteLLM value.

## Rollback Path 2 — full PR revert (clean, ~2min)

Use this when the cutover surfaced a structural problem (cost
regression, fundamentally wrong model namespace, missing feature in
LiteLLM) that won't be fixed by a settings flip. The cutover PR was
squash-merged so revert is one commit.

```powershell
# 1. Revert the merge commit on main
git revert <PR-merge-commit-SHA>
git push origin main

# 2. Roll back the migration on the live DB
#    (the PR's down() restores plugin.llm_provider.primary.<tier> to ollama_native)
poindexter migrations down 0160_litellm_cutover_default_providers

# 3. Reload the worker
docker compose restart cofounder-agent

# 4. Verify both layers
poindexter settings list 2>&1 | findstr "plugin.llm_provider.primary"
poindexter migrations status | findstr 0160
```

If `migrations down` isn't available in the running CLI version,
apply the down() body manually:

```sql
UPDATE app_settings
SET value = 'ollama_native', updated_at = NOW()
WHERE key LIKE 'plugin.llm_provider.primary.%' AND value = 'litellm';
```

## Verification after rollback

```powershell
# Should show all five tiers = ollama_native (or absent)
poindexter settings list 2>&1 | findstr "plugin.llm_provider.primary"

# Worker logs at next pipeline run should mention "ollama_native" or
# "OllamaClient" — not "litellm"
docker compose logs cofounder-agent --tail 200 | findstr -i "provider"

# Run the dev_diary smoke (cheapest end-to-end)
poindexter dev-diary run --dry-run
```

If the smoke run completes and `cost_logs` records the same
electricity cost shape as before the cutover (provider='ollama',
electricity_kwh populated, cost_usd <$0.001), the rollback is clean.

## Why a rollback might be needed

The cutover lands these behavioral changes ON the dispatcher path
(when callers actually use it — see the **Coverage Caveat** in the
migration docstring):

- **Model resolution:** `LiteLLMProvider._resolve_model` wraps bare
  model names in an `ollama/` prefix. Callers passing already-prefixed
  names (`anthropic/claude-haiku-4-5`) pass through unchanged. Bare
  names that aren't valid Ollama tags (`gpt-4o-mini` without a
  provider prefix) will newly route to Ollama and fail; pre-cutover
  the OllamaNativeProvider would have failed at the HTTP call instead
  of resolution time, but the failure mode shifts.
- **Token counting:** Both paths populate `prompt_tokens` /
  `completion_tokens` from Ollama's `prompt_eval_count` /
  `eval_count`, but the prompt construction differs slightly
  (LiteLLM passes the full `messages` list to Ollama's `/api/chat`;
  OllamaNativeProvider concatenates `role: content\n\n` and uses the
  legacy single-prompt path). Counts can differ by a few tokens for
  the same input.
- **Cost ledger:** `OllamaClient.generate` computes per-call
  electricity cost from GPU-power-draw × duration and stamps it on
  the response (`response['cost']`). LiteLLM doesn't compute that
  number — for the cost_guard to keep reporting electricity, the
  caller has to either keep the OllamaClient electricity path live
  alongside the LiteLLM completion path, or migrate the electricity
  cost calculation into a callback. As of this cutover the
  electricity attribution flow stays on the OllamaClient side; only
  callers that already used `dispatcher.dispatch_complete` are
  affected.
- **Langfuse spans:** Once any caller routes through
  `litellm.acompletion`, the spans land — `configure_langfuse_callback`
  is wired at lifespan startup (main.py:275-289). Pre-cutover, the
  callback registers but no spans land because no caller hits LiteLLM.
  Rolling back to `ollama_native` everywhere makes Langfuse silent
  again until a future cutover.

## Pre-flight checklist (re-cutover after rollback)

When the underlying issue is fixed and you want to re-attempt the
cutover:

1. Confirm the parity test still passes against live Ollama:

   ```powershell
   $env:OLLAMA_URL = "http://localhost:11434"
   $env:INTEGRATION_TESTS = "1"
   $env:REAL_SERVICES_TESTS = "1"
   poetry run pytest tests/integration/test_litellm_cost_parity.py -v
   ```
2. Verify `litellm` is installed (`poetry run pip show litellm`) — it
   wasn't a declared dependency before #372, the cutover PR adds it.
3. Re-apply the migration: `poindexter migrations up`.
4. Restart the worker.
5. Trigger one dev_diary post end-to-end and confirm `cost_logs`
   rows look as expected.

## Related issues

- Glad-Labs/poindexter#199 — cost-lookup migration (already merged).
- Glad-Labs/poindexter#373 — Langfuse callback wiring (already merged).
- Glad-Labs/poindexter#376 — `get_all_llm_providers()` registry merge
  (already merged; required for the LiteLLM provider to be discoverable
  from the dispatcher).
- Glad-Labs/poindexter#378 — migration number collisions (0158/0159
  doublewide); 0160 deliberately picks the next free number.
