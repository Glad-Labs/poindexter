# Prompt Manager

**File:** `src/cofounder_agent/services/prompt_manager.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_prompt_manager.py`
**Last reviewed:** 2026-07-04

## What it does

`UnifiedPromptManager` is the resolution chain for every LLM prompt the worker uses. After phase 2 of poindexter#47 retired the `prompt_templates` DB layer, and poindexter#825 made the shipped defaults authoritative, the chain is:

1. **`SKILL.md` pack defaults** — the authoritative production prompts, loaded at process start from `skills/<pack>/<skill>/SKILL.md` (the legacy `prompts/*.yaml` loader still runs first but the repo ships no YAML files). Edited in the repo via PR; mirrored into Langfuse for review by `SyncPromptCatalogToLangfuseJob`.
2. **Langfuse production label** — consulted FIRST only when `langfuse_prompt_overrides_enabled='true'` (default `false`). Legacy override layer for deliberate live experiments; leaving it on re-introduces the masking trap (stale Langfuse copies shadowing SKILL.md edits).

When the override layer is enabled but Langfuse isn't configured (no host + key in `app_settings`) or the lookup fails (network/auth/missing prompt name), the call falls through to the pack defaults — operators without a Langfuse install always get working prompts.

## Key methods

- **`get_prompt(key, **kwargs) -> str`** — sync. Resolves and `.format()`s the template. Raises `KeyError` if the key isn't in any source.
- **`load_from_db(pool, *, site_config=None) -> int`** — async, called once at worker startup. Despite the name, this no longer loads from `prompt_templates` (that table was dropped — phase 2 of poindexter#47). Today it captures `site_config` on the instance + pre-fetches the encrypted `langfuse_secret_key` while the async event loop is available, so the sync Langfuse-init path inside `get_prompt` has the secret in hand. Returns `0` (no rows loaded). The `pool` arg is kept on the signature so the dozen callers don't churn.
- **`_fetch_from_langfuse(key)` / `_init_langfuse_client()`** — internal. Lazy-initializes the Langfuse client on first `get_prompt`. Reads `langfuse_host`, `langfuse_public_key`, `langfuse_secret_key` from `app_settings` via the injected SiteConfig. When any setting is missing or empty, returns `None` and logs a single info message — fallback to YAML is the documented OSS path.

## DI seams

The module-level `services.site_config.site_config` singleton and the per-module `set_site_config` fan-out are both retired (#788 capstone). `UnifiedPromptManager` receives its `SiteConfig` as a constructor arg (DI) — the `AppContainer` composition root builds the manager with the process-wide `SiteConfig`. The actual loaded SiteConfig is captured on `self._site_config` and used by the lazy Langfuse init.

The pre-fetched secret cache (`self._langfuse_secret_key`) exists because `_init_langfuse_client` runs from the sync `get_prompt` path and can't `await site_config.get_secret(...)`. Pre-fetching during the async `load_from_db` step puts the value in hand by the time the lazy init runs.

## Reads from / writes to

- **Reads:**
  - YAML files under `prompts/` (loaded once at `__init__` via `_initialize_prompts`).
  - `app_settings` via SiteConfig: `langfuse_host`, `langfuse_public_key`, `langfuse_secret_key`.
- **Writes:** nothing. Pure read path. Operator edits land in Langfuse via the UI, not through this module.
- **External APIs:** Langfuse via the SDK (`langfuse.Langfuse(host, public_key, secret_key)` + `client.get_prompt(name=key, label='production')`).

## Failure modes

- **Prompt key not in any source** — `KeyError(f"Prompt 'X' not found. Available: ...")`. Lists available keys in the message so the typo is obvious.
- **Format kwargs missing** — `KeyError(f"Prompt 'X' missing required variable: Y")`. Tells the caller exactly which kwarg the template needed.
- **Langfuse unreachable** — debug-level log + fall through to YAML. No operator alert; the YAML version is the documented fallback.
- **Langfuse `auth_check` fails** — same as above. The operator sees the "Langfuse not configured" info log on the next worker restart and can fix the keys via the `activate_langfuse.py` one-shot.

## Activation flow (operator)

For an OSS install standing up Langfuse for the first time:

```bash
# 1. Boot the Langfuse Docker stack (provisions org/project/keys via LANGFUSE_INIT_*)
docker compose up -d langfuse-web langfuse-worker langfuse-redis langfuse-clickhouse langfuse-minio

# 2. Lift the auto-provisioned keys into app_settings
poetry run python scripts/activate_langfuse.py

# 3. Nothing else — SyncPromptCatalogToLangfuseJob populates the prompt
#    mirror on its next cycle (<=6h), and traces flow once the worker
#    restarts with the credentials in place.
docker restart poindexter-worker
```

(The old step 3 — `scripts/import_prompts_to_langfuse.py`, a manual bulk import — was retired by poindexter#825; the mirror job supersedes it and treats the script's leftover `imported_by` versions as its own.)

## See also

- [`docs/architecture/prompt-management.md`](../prompt-management.md) — the full architecture: SKILL.md packs, the read-only Langfuse mirror, and the masking-trap history.
- `feedback_prompts_must_be_db_configurable` (operator design note) — why every prompt routes through this manager.
- `feedback_module_singleton_gotcha` (operator design note) — DI gotcha that bit phase 1 of #47.
- `scripts/activate_langfuse.py` — operator activation tool (credentials only).
- `services/migrations/0000_baseline.py` — seeds placeholder rows for the three Langfuse credentials (originally migration 0153, folded into the baseline by the 2026-06-22 squash).
