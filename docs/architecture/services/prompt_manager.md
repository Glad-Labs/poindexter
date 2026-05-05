# Prompt Manager

**File:** `src/cofounder_agent/services/prompt_manager.py`
**Tested by:** `src/cofounder_agent/tests/unit/services/test_prompt_manager.py`
**Last reviewed:** 2026-05-04

## What it does

`UnifiedPromptManager` is the resolution chain for every LLM prompt the worker uses. After phase 2 of poindexter#47 retired the `prompt_templates` DB layer, the chain is two layers:

1. **Langfuse production label** — operator's edit surface. SDK caches in-process for ~60s; UI edits take effect on the next `get_prompt` call without a worker restart.
2. **YAML defaults** — the OSS distribution baseline. Files under `prompts/`, loaded at process start.

When Langfuse isn't configured (no host + key in `app_settings`) or the lookup fails (network/auth/missing prompt name), the call falls through to YAML. That's the OSS-distribution path — operators without a Langfuse install still get working prompts.

## Key methods

- **`get_prompt(key, **kwargs) -> str`** — sync. Resolves and `.format()`s the template. Raises `KeyError` if the key isn't in any source. Langfuse-first means Langfuse-cached calls are sub-millisecond after the first hit.
- **`load_from_db(pool, *, site_config=None) -> int`** — async, called once at worker startup. Despite the name, this no longer loads from `prompt_templates` (that table was dropped — phase 2 of poindexter#47). Today it captures `site_config` on the instance + pre-fetches the encrypted `langfuse_secret_key` while the async event loop is available, so the sync Langfuse-init path inside `get_prompt` has the secret in hand. Returns `0` (no rows loaded). The `pool` arg is kept on the signature so the dozen callers don't churn.
- **`_fetch_from_langfuse(key)` / `_init_langfuse_client()`** — internal. Lazy-initializes the Langfuse client on first `get_prompt`. Reads `langfuse_host`, `langfuse_public_key`, `langfuse_secret_key` from `app_settings` via the injected SiteConfig. When any setting is missing or empty, returns `None` and logs a single info message — fallback to YAML is the documented OSS path.

## DI seams

Per CLAUDE.md "Configuration" section, the manager doesn't read from the module-level `services.site_config.site_config` singleton (that singleton survives but is never `.load()`'d in process; calling its `.get()` returns "" — see `feedback_module_singleton_gotcha`). The actual loaded SiteConfig is the one passed to `load_from_db` at worker startup; it gets captured on `self._site_config` and used by the lazy Langfuse init.

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

# 3. Push current YAML prompts into Langfuse with the 'production' label
poetry run python -m scripts.import_prompts_to_langfuse

# 4. Restart the worker — the next get_prompt logs "Langfuse prompt management active"
docker restart poindexter-worker
```

After that, prompt edits in the Langfuse UI propagate to the worker on the next `get_prompt` call (60s SDK cache).

## See also

- `~/.claude/projects/C--Users-mattm/memory/feedback_prompts_must_be_db_configurable.md` — why every prompt routes through this manager.
- `~/.claude/projects/C--Users-mattm/memory/feedback_module_singleton_gotcha.md` — DI gotcha that bit phase 1 of #47.
- `scripts/activate_langfuse.py` + `scripts/import_prompts_to_langfuse.py` — operator activation tools.
- `services/migrations/0153_seed_langfuse_prompt_settings.py` — placeholder rows for the three Langfuse credentials.
