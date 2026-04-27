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

---

## Sweep 3 — brain daemon

Continuation of the audit. Scope: `brain/` (the standalone Brainstem
daemon — independent of FastAPI, only depends on Python stdlib + asyncpg).
The daemon writes its own log via `logging.getLogger("brain")` (and
sub-loggers `brain.probes`, `brain.alert_sync`, `brain.business_probes`)
— it does NOT use `services.logger_config`, by design (the brain has
to keep working when the rest of the stack is broken).

### 1. Silent `except: pass` swallows fixed

| File                       | Line(s)   | What was swallowed                                           | Now logs                                                      |
| -------------------------- | --------- | ------------------------------------------------------------ | ------------------------------------------------------------- |
| `brain/brain_daemon.py`    | 119–132   | `_setting_int` — DB read OR `int()` parse failure            | WARNING                                                       |
| `brain/brain_daemon.py`    | 789–791   | `digest.last_sent` date string parse                         | DEBUG                                                         |
| `brain/brain_daemon.py`    | 918–921   | nvidia-smi-exporter probe (control flow — exporter optional) | DEBUG                                                         |
| `brain/brain_daemon.py`    | 933–940   | `electricity_rate_kwh` app_settings read                     | WARNING                                                       |
| `brain/brain_daemon.py`    | 993–996   | PSU watchdog `brain_knowledge` write                         | DEBUG                                                         |
| `brain/health_probes.py`   | 550–557   | `approval_queue_alert_threshold` app_settings read           | WARNING                                                       |
| `brain/health_probes.py`   | 1058–1066 | `gpu_temperature_high_threshold_c` app_settings read         | WARNING                                                       |
| `brain/business_probes.py` | 81        | API health check (already records DOWN; preserve why)        | DEBUG                                                         |
| `brain/business_probes.py` | 89        | site health check                                            | DEBUG                                                         |
| `brain/business_probes.py` | 97        | OpenClaw health check                                        | DEBUG                                                         |
| `brain/bootstrap.py`       | 207–215   | bootstrap.toml chmod (Windows always raises)                 | WARNING on POSIX, silent on Windows (stdlib-only — no logger) |

Total: **11 sites converted**, across 4 files.

The earlier `_setting_int` swallow was particularly concerning — it
caught `(ValueError, TypeError, Exception)` (where `Exception` shadows
the others) and silently fell back to the default for ANY error. A
DB outage during the brain's reasoning queue, auto-remediation
sweepers, or digest cadence reads would have shown up as "everything
quietly using defaults" rather than as a logged warning. Now split
into two distinct paths: DB error → WARNING ("real outage worth
seeing"), parse error → WARNING ("real misconfig worth seeing").

### 2. Heartbeat path — gaps found and fixed

The brain daemon writes a heartbeat file (`~/.poindexter/heartbeat`

- `/tmp/brain_heartbeat` in Docker) after each cycle for the OS-level
  watchdog. CLAUDE.md describes a separate `brain_decisions` write at
  the end of each cycle. Three gaps surfaced:

**Gap 1 — heartbeat fields were always 0/True regardless of cycle health.**
The `_touch_heartbeat()` helper accepted `cycle_issues` and
`probe_failures` parameters, but the call sites never passed them
in. Result: the heartbeat JSON always reported `cycle_ok=true` even
when the cycle had logged dozens of issues. **Fixed:** `run_cycle`
now returns a stats dict (`issues`, `probe_failures`, `step_failed`)
and the main loop feeds it into `_touch_heartbeat`. A watchdog reading
the JSON can now decide whether stale-but-failing warrants action.

**Gap 2 — when `run_cycle` raised, no heartbeat was written.**
The `try/except` in `main()` logged the error but skipped the
heartbeat update. The OS watchdog would eventually restart on
file-age, but the heartbeat itself never reflected the crash so an
operator scraping the JSON couldn't distinguish "brain is alive but
crashing every cycle" from "brain is genuinely dead". **Fixed:** the
exception path now calls `_touch_heartbeat(cycle_crashed=...)` with
the exception type + message so a Grafana panel reading the JSON
sees `cycle_ok=false` immediately.

**Gap 3 — a sub-step crash skipped the brain_decisions log.**
`run_cycle` was a flat sequence of `await monitor_services(pool)`
through `await run_business_probes(pool)` followed by the
`brain_decisions` INSERT. If `auto_remediate` (or any other step)
raised, control jumped to `main()`'s outer except and the
`brain_decisions` row never got written — losing the audit trail
for that cycle. **Fixed:** every step now runs through a `_step()`
wrapper that catches+logs at WARNING and keeps going. The first
step name to crash gets recorded in `step_failed` and lands in
both the `brain_decisions` row (with `confidence=0.5` so a query
can filter degraded cycles) AND the heartbeat JSON.

The "next-action" / queue-handler decision logging path is intact
— `process_queue` already logs each handler invocation at INFO and
the `_QUEUE_HANDLERS` dispatch updates the `brain_queue.result`
column. No change needed there.

### 3. Telegram alert path — verified

- **Wiring.** `send_telegram` hits the Telegram Bot API directly
  (no OpenClaw dependency). This is intentional and correct: the
  brain daemon is the watchdog FOR OpenClaw, so it can't depend on
  OpenClaw to deliver alerts. The `feedback_telegram_ownership`
  rule says "OpenClaw handles polling; backends SEND via webhook"
  — the brain daemon is sending, just via the bot API rather than
  an OpenClaw webhook. Both are write-only; the rule is preserved.
- **Rate-limit / dedup.** Three independent rate-limiters exist:
  1. `monitor_services` consults `alert_actions.cooldown_minutes`
     before notifying, with `consecutive_failures` and
     `escalate_after_failures` for tiered escalation.
  2. `monitor_external_services` only notifies on indicator
     **transitions** (prev != current), so a 1-hour Vercel outage
     fires at most one alert + one recovery.
  3. `run_health_probes` notifies once when `_failure_counts[name] ==
ALERT_AFTER_FAILURES` (3) — strictly equal, so subsequent
     failures past the threshold do not re-spam.
- **No additional dedup needed.** The audit specifically checked
  whether a 1-hour outage produces 12 identical alerts. It does not:
  monitor_services would emit at most 1 (cooldown), external
  services 1 (transition), probes 1 (`==` not `>=`).

No fixes needed in this area — the path is sound.

### 4. Auto-restart logic — gap found and fixed

**Gap 4 — `restart_service` had no backoff.**
`monitor_services` calls `restart_service(name)` every cycle (every
5 minutes) for as long as the worker / OpenClaw is detected DOWN.
For a transient blip that's fine; for a configuration error
preventing startup it would respawn the process every 5 minutes
forever. The Docker path in `restart_service` already used
`docker restart` + `notify(...)` per call so the Telegram channel
would also fill with restart messages. There WAS a separate
`REMEDIATION_COOLDOWN = 900` for probe-driven restarts in
`health_probes.py`, but the direct `monitor_services → restart_service`
path bypassed it entirely.

**Fixed.** Added a module-level `_RESTART_COOLDOWN_SECONDS = 15 * 60`

- `_last_restart_attempt` dict at the top of `restart_service`. First
  restart attempt logs + runs; further attempts inside the cooldown
  window log at INFO ("suppressed — cooldown Xs remaining") and bail.
  15 minutes was chosen to match `REMEDIATION_COOLDOWN` so the two
  restart-paths share the same envelope. Tunable via app_settings
  would be nice but adding a DB read here means the cooldown itself
  fails when the DB is broken — and "DB broken" is exactly when this
  code runs. Hardcoded floor is the safer choice for a watchdog.

**"Am I on Matt's PC vs cloud" detection.** Uses
`IS_DOCKER = bool(os.getenv("IN_DOCKER"))` (set in
`docker-compose.local.yml`). Sane: when running via the local
Docker stack, restart paths use `docker restart <container>` against
the mounted Docker socket; when running as a host-side process on
Matt's PC (the documented mode in CLAUDE.md), restart paths use
`subprocess.Popen` against `start-worker.ps1` / `openclaw gateway
restart`. The Windows-host path hardcodes the absolute PowerShell
script path (`C:\Users\mattm\glad-labs-website\scripts\start-worker.ps1`)
which is fine for Matt's setup but would need to come from
app_settings for a SaaS / multi-operator world. **Noted, not fixed**
— the brain daemon's whole purpose is "run on Matt's PC, manage Matt's
PC", and a DB-driven path here would re-introduce the
"DB broken at the moment we want to restart" failure mode the
hardcoded cooldown above avoids.

### Sweep 3 totals

- 11 swallow sites converted, across 4 files (8 WARNING, 3 DEBUG)
- 4 substantive gaps fixed (heartbeat fields not populated, no
  heartbeat on crash, sub-step crashes skipped audit log, no restart
  backoff)
- 1 gap noted but not fixed (hardcoded PS1 path for Windows-host
  worker restart) — reasoning above

### Out-of-scope sites NOT touched

- `brain/brain_daemon.py:1137–1138` — `asyncio.TimeoutError: pass` on
  the cycle interval timer. Pure control flow (the timeout is the
  signal that no shutdown was requested and the next cycle should
  start). DEBUG logging here would add a line every 5 minutes to no
  benefit.
- `brain/brain_daemon.py:1089–1091` — `(NotImplementedError,
AttributeError): pass` for the Windows signal-handler fallback.
  Documented + Windows-only control flow.
- `brain/health_probes.py:452–453` — `ValueError: pass` parsing
  schtasks `Last Result` ints. Control flow — non-int rows are just
  skipped.
- `brain/health_probes.py:482–483` — `(FileNotFoundError, OSError):
pass` while enumerating Windows drive letters C through H.
  Control flow — drive doesn't exist.
- `brain/health_probes.py:692–698` — `Exception: pass` on the
  `cost_logs.cost_type` query, with a fallback query for installs
  where the migration hasn't run yet. Control flow — schema-version
  fallback.
- `brain/alert_sync.py` — already audit-grade (every swallow already
  logs at WARNING/DEBUG with rationale). Reviewed, no changes.

The net effect: the brain daemon now (a) tells operators when its
own internal config reads fail instead of sitting on stale defaults,
(b) writes a heartbeat that actually reflects cycle health,
(c) preserves the audit trail in `brain_decisions` even when one
sub-step crashes, and (d) backs off restart attempts so a wedged
service doesn't get respawned every 5 minutes.
