# Silent Failures Audit — 2026-04-27

Background sweep of `except: pass` and `except Exception: pass` patterns
in `src/cofounder_agent/{services,plugins,poindexter}/`. Goal: every
swallow either logs at WARNING (so a real outage is visible in Grafana
Loki) or DEBUG (when the failure is normal control flow), and any
`raise NotImplementedError` is documented as gated-on-ticket vs real
bug.

Scope excludes `tests/`, `migrations/`, `__pycache__/`, and the
just-shipped video stages (`script_for_video.py`, `scene_visuals.py`,
`tts_for_video.py`, `stitch_long_form.py`, `stitch_short_form.py`,
`upload_to_platform.py`, `_video_stitch.py`, `caption_providers/whisper_local.py`,
`media_compositors/ffmpeg_local.py`, `publish_adapters/youtube.py`)
which the parent agent wants to review holistically.

---

## 1. Fixed in this PR

Each row was an `except […]: pass` that now logs. Severity is WARNING
unless the failure is normal-path (then DEBUG).

| File                                      | Line | What was swallowed                                        | Now logs                                     |
| ----------------------------------------- | ---- | --------------------------------------------------------- | -------------------------------------------- |
| `services/ai_content_generator.py`        | 732  | site_config read for `pipeline_writer_model` fallback     | WARNING                                      |
| `services/category_resolver.py`           | 72   | DB read for explicit-category lookup                      | WARNING (already on remote — `4b3f7528`)     |
| `services/devto_service.py`               | 198  | app_settings read for `devto_publish_immediately`         | WARNING                                      |
| `services/idle_worker.py`                 | 365  | `_get_setting` DB helper — generic app_settings read      | WARNING                                      |
| `services/jobs/check_memory_staleness.py` | 47   | `_get_setting` helper — generic app_settings read         | WARNING                                      |
| `services/multi_model_qa.py`              | 611  | site_config read for `site_domain` (citation filter)      | DEBUG                                        |
| `services/multi_model_qa.py`              | 789  | settings read for `qa_fallback_critic_model`              | WARNING                                      |
| `services/multi_model_qa.py`              | 1285 | settings read for `qa_vision_*` (vision QA gate)          | WARNING                                      |
| `services/multi_model_qa.py`              | 1497 | settings read for `qa_preview_*` (preview screenshot QA)  | WARNING                                      |
| `services/prompt_manager.py`              | 260  | live `premium_active` read from site_config               | WARNING                                      |
| `services/research_context.py`            | 76   | DB lookup for post slug/excerpt                           | WARNING                                      |
| `services/social_poster.py`               | 474  | prom counter `.inc()` failure                             | DEBUG (best-effort metric)                   |
| `services/stages/cross_model_qa.py`       | 122  | reading `qa_result.validation` for rejection summary      | DEBUG                                        |
| `services/stages/cross_model_qa.py`       | 409  | nested `audit_log_bg` failure (cost_log_write_failed)     | DEBUG                                        |
| `services/stages/cross_model_qa.py`       | 434  | settings read for `qa_consistency_veto_threshold`         | WARNING                                      |
| `services/stages/cross_model_qa.py`       | 523  | settings read for `qa_max_rewrites`                       | WARNING                                      |
| `services/stages/generate_content.py`     | 224  | building real-slug allowlist from `_internal_links_cache` | DEBUG                                        |
| `services/stages/generate_content.py`     | 326  | nested `audit_log_bg` failure (cost_log_write_failed)     | DEBUG                                        |
| `services/task_executor.py`               | 757  | settings read for `task_timeout_seconds`                  | WARNING                                      |
| `services/task_executor.py`               | 1259 | DB slug lookup in semantic-dedup rejection reason         | WARNING                                      |
| `services/task_executor.py`               | 1286 | DB read of `model_selections` for task                    | WARNING                                      |
| `services/taps/claude_code_sessions.py`   | 274  | site_config read for `claude_projects_dir`                | DEBUG                                        |
| `services/telemetry.py`                   | 235  | invalidating `app.middleware_stack` before instrument_app | DEBUG                                        |
| `plugins/llm_providers/gemini.py`         | 548  | walking response candidates/parts to extract text         | WARNING                                      |
| `poindexter/cli/setup.py`                 | 396  | `bootstrap.resolve_database_url()` failure                | now `click.secho yellow` (CLI has no logger) |

Total: **25 sites converted**, across 16 files.

The general rule applied:

- DB reads of `app_settings` / `site_config` → WARNING (a real DB
  outage that previously looked like "config defaults loaded silently"
  now shows up in logs as "couldn't read X, using default Y").
- Best-effort metric / audit-log emit failures → DEBUG (not load-bearing).
- Defensive `getattr` / attribute walks → DEBUG.
- Cleanup paths during teardown (already in error path) → DEBUG.

---

## 2. NotImplementedError stubs

All currently in-tree `raise NotImplementedError` calls in the audited
scope, with intent.

| File                                      | Function                     | Status               | Gating                                                                                                                                                            |
| ----------------------------------------- | ---------------------------- | -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `services/social_adapters/youtube.py:33`  | `upload_to_youtube()`        | **Intentional stub** | GH-40 (Google OAuth not set up). Module docstring lists the 5-step setup checklist.                                                                               |
| `services/social_adapters/reddit.py:32`   | `post_to_reddit()`           | **Intentional stub** | GH-40 (Reddit OAuth not set up). Module docstring lists the 3-step setup checklist.                                                                               |
| `services/social_adapters/linkedin.py:32` | `post_to_linkedin()`         | **Intentional stub** | GH-40 (LinkedIn Marketing Developer Platform OAuth not set up). Module docstring lists the 5-step setup checklist.                                                |
| `plugins/llm_providers/anthropic.py:599`  | `AnthropicProvider.stream()` | **Intentional stub** | Issue #133. `supports_streaming=False` Protocol flag routes callers to `complete()`; pipeline doesn't stream. Both docstring and error message say so.            |
| `plugins/llm_providers/anthropic.py:615`  | `AnthropicProvider.embed()`  | **Intentional stub** | Anthropic doesn't sell embeddings. `supports_embeddings=False` Protocol flag routes callers to Ollama-native embeddings. Both docstring and error message say so. |

All 5 stubs were inspected and their docstrings + error messages already
clearly identify the gating ticket / Protocol flag / config-key path. **No
issues need to be filed.** The social-adapter stubs are particularly clean
— they `logger.warning(...)` first so the operator sees the call attempt,
then raise so callers in `social_poster._distribute_to_adapters` know to
skip rather than treat the post as delivered. This is the GH-40 design
pattern Matt asked for — "test for it in social_poster, GH-40 unblocks
real implementations."

---

## 3. Actionable TODO/FIXME

A grep for `# TODO|FIXME|XXX|HACK` over the audited scope returned a
single match:

| File                                                       | Line | Comment                                            | Verdict                                                                                                                                                                                                                                                                                                                                                                                                               |
| ---------------------------------------------------------- | ---- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `services/migrations/0073_alert_rules_and_grafana_sync.py` | 146  | `"",  # TODO: operator-provided (see description)` | **Stale / not actionable.** This is a deliberate empty default for the `grafana_api_token` seed row, with the description column telling the operator to populate it via the admin UI. The TODO is a hint to humans reading the migration source, not work. Could rephrase to `# operator-provided via admin UI` to remove the TODO marker if we want to keep the codebase TODO-free, but it's not blocking anything. |

The audited scope (`services/`, `plugins/`, `poindexter/`) is otherwise
clean of TODO/FIXME/XXX/HACK comments. Either we've been disciplined
about closing them or they live in `tests/` / `web/` / `brain/` which
this audit didn't touch.

---

## 4. Re-raise patterns inspected

Searched for the `except SomeError: raise SomeError(...)` anti-pattern
that loses the original traceback. The handful of `raise X(...)` blocks
inside `except` clauses across `routes/`, `plugins/`, and `services/`
all use `raise X(...) from e` (or are translating to FastAPI's
`HTTPException` with `from exc`), preserving the chain. **No traceback-
losing re-raises found.**

---

## 5. Out-of-scope sites NOT touched

These swallow patterns were intentionally left as-is because the
exception is part of normal control flow (file-cleanup races, parsing
fallbacks, optional-import probes, cancellation cleanup):

- `services/audio_gen_providers/stable_audio_open.py:165` — `os.remove` cleanup, file may already be gone (`OSError`)
- `services/image_providers/flux_schnell.py:133` — same pattern
- `services/image_providers/sdxl.py:175` — same pattern
- `services/video_providers/wan2_1.py:162, 169` — `os.remove` cleanup + `getsize` race (`OSError`)
- `services/jobs/check_memory_staleness.py:94, 139` — int / fromisoformat parsing fallbacks (control flow)
- `services/jobs/collapse_old_embeddings.py:587` — int parsing of asyncpg `DELETE N` reply (control flow)
- `services/integrations/handlers/tap_singer_subprocess.py:205, 241, 263` — stderr decode noise / state-file fallback / cancellation cleanup
- `services/revalidation_service.py:71` — `ImportError` probe for the optional declarative-webhook framework (control flow)
- `plugins/llm_providers/anthropic.py:298, 333` — int parsing for per-call timeout (control flow)
- `plugins/llm_providers/gemini.py:159` — int parsing for per-call timeout (control flow)
- `poindexter/cli/setup.py:123, 145` — pool close during teardown / urlparse fallback (cleanup + control flow)

These could each accept a DEBUG log too if we ever want them visible,
but they don't represent silent-outage risk — the DB / config / API
calls _would_ surface, while cleanup of an already-gone file or a
"is this a docker URL?" probe would not.

The video / publish stages from tonight's ship are entirely out of
scope per the task constraints.

---

## Summary

- 25 swallow sites converted, across 16 files
- 5 NotImplementedError stubs, all intentional + well-documented
- 1 TODO comment (stale, low priority)
- 0 traceback-losing re-raises
- 11 control-flow swallow sites left intentionally as-is
