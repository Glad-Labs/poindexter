# Jank-audit + stress-test — 2026-05-19 → 2026-05-20

**Date:** 2026-05-19 evening → 2026-05-20 morning UTC.
**Goal:** Squeeze every observable misbehavior out of the prod stack before
the next big push.
**Author:** Claude (Opus 4.7) on Matt's request.

---

## TL;DR

**10 distinct issues found and fixed in one overnight sweep.** Six were
"true positives" already on the operator radar (Telegram noise, content
quality drift); four were _latent_ — services declared in pyproject.toml
or seeded in app_settings that had never actually run in production.

Highlights by category:

| Category                       | Issue                                                      | Symptom                                                                     | Resolution                                                                                                   |
| ------------------------------ | ---------------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **Telegram noise**             | severity routing didn't gate Telegram on `error`+          | every probe warning phone-pinged                                            | PR #496 (`_TELEGRAM_SEVERITIES = {error, critical}`)                                                         |
| **Telegram noise**             | URL probe double-paged MCP + voice surfaces                | 102 pages / 12h                                                             | PR #498 (skip-keys extension)                                                                                |
| **Telegram noise**             | Grafana webhook 401 every 5 min                            | 4 × 401 / 20 min                                                            | PR #497 (OAuth-JWT via `settings.authorization_credentials`)                                                 |
| **Silent failures**            | 5 plugins never registered in `_SAMPLES`                   | dead schedules — `writer=collapse_job` silent 23+ days, backfills never ran | PR #502 (`fix(plugins): register 5 never-loaded plugins`)                                                    |
| **Writer quality**             | LLM-tell vocabulary slipped through validator              | "delve / tapestry / multifaceted" in drafts                                 | PR #494 (`buzzword_density` rule) + PR #493 (prompt-side ban)                                                |
| **Writer quality**             | empty `[]` brackets from citation-aware writer             | `…on user devices [].` shape                                                | PR #493 (`_strip_empty_brackets`)                                                                            |
| **Data integrity**             | `posts.featured_image_data` JSONB never populated          | column was a `{}` dead seam since GH#161                                    | PR #495 (`source_featured_image` now stamps SDXL provenance)                                                 |
| **Compose drift**              | nested `${VAR:-default}` interpolation half-applied        | 573 false-positive drift warnings / 24h on `backup-daily` + `backup-hourly` | resolved by 2026-05-16 patch (`_INNERMOST_BRACED_RE`); audit confirmed 0 false positives since brain restart |
| **Niche policy**               | dev_diary podcasts spawning despite `media_to_generate=[]` | slug-hack `NOT LIKE 'what-we-shipped%'` was the previous bandage            | PR #482 (`niches.default_media_to_generate` array)                                                           |
| **Severity routing follow-on** | `smart_monitor` warned on every brain restart              | smartctl-not-installed pinged Telegram                                      | absorbed by PR #496 — same warning now Discord-only                                                          |

All 10 are fully resolved or have an open PR; the **registry-drift class** is now guarded by a parametrized regression test (`test_pyproject_entry_points_are_registered_in_samples`).

---

## The five never-loaded plugins (finding #189)

The most surprising finding of the night. Worker container bind-mounts
the source tree rather than `pip install`-ing it, so
`importlib.metadata.entry_points(group='poindexter.*')` returns **zero**
at runtime. The imperative `_SAMPLES` list in
`plugins/registry.py:get_core_samples()` is the sole in-process load
path.

Cross-checking pyproject.toml's `[tool.poetry.plugins."poindexter.*"]`
sections against `_SAMPLES` surfaced five classes declared but never
registered:

| Plugin                     | Group         | Schedule / Behavior | Time dead                                                             |
| -------------------------- | ------------- | ------------------- | --------------------------------------------------------------------- |
| `CollapseOldEmbeddingsJob` | jobs          | every 7 days        | ≥23 days (per `embeddings.writer='collapse_job'` last row 2026-04-27) |
| `BackfillPodcastsJob`      | jobs          | every 4 hours       | since PR #482 (filter fix landed but never executed)                  |
| `BackfillVideosJob`        | jobs          | every 6 hours       | same                                                                  |
| `OpenClawSQLiteTap`        | taps          | tap_runner          | unknown — settings seeded but never read                              |
| `IGDBSource`               | topic_sources | discovery           | since migration `20260512_182304`                                     |

**Symptom that caught it:** `CheckMemoryStaleness` was correctly paging
on `writer=collapse_job` every cycle. Following the alert all the way
back to the registry surfaced the systemic gap.

**Regression test:** new parametrized test in
`tests/unit/plugins/test_registry_completeness.py` walks every
`poindexter.*` group in pyproject.toml and fails if any declared class
is missing from `_SAMPLES`. A negative-test confirmed it correctly
flags 29 missing classes when `_SAMPLES` is reduced to a stub.

---

## Severity-routing precondition (finding #187)

PR #485 hydrated `TELEGRAM_*` + `DISCORD_*_WEBHOOK_URL` from
`app_settings` into `os.environ` so the env-only `operator_notifier`
could read them. That hydration also unmasked a latent bug: every
`notify_operator(severity=…)` call was attempting Telegram regardless
of severity, training the operator to ignore phone-pings on routine
probe noise.

PR #496 gates `_try_telegram` on `severity in {"error", "critical"}`
and routes everything else to Discord-only. Routing matrix:

| severity | Telegram | Discord | alerts.log |
| -------- | -------- | ------- | ---------- |
| critical | ✓        | ✓       | ✓          |
| error    | ✓        | ✓       | ✓          |
| warning  | skip     | ✓       | ✓          |
| info     | skip     | ✓       | ✓          |

`error` still pings because it represents an operator-actionable
failure. The boundary is at the warning/error line — warnings are
signal-grade noise (probe drift, recurring failures), errors are
anomalies that need eyes.

Verified live in prod: `smart_monitor` page from 01:04:38 UTC shows
`{"telegram": "skipped (severity below error)", "discord": "discord"}`.

---

## Bug-chain pattern

PR #485 (hydration) → exposed PR #496 (severity routing) → exposed
the operator_url_probe double-paging (#188) → exposed the staleness
probe surfacing `writer=collapse_job` (#189). One fix surfaces the
next bug down the chain. Pattern worth watching for after every
infra-layer fix.

---

## Followups (out of scope)

- **`KNOWN_UNREGISTERED` audit** — `plugins.llm_providers.anthropic`
  is the one remaining provider-side gap, tracked via #398.
- **Container-mode entry_points** — packaging the worker so
  `pip install .` actually exposes the entry_points groups would
  let us delete the entire `_SAMPLES` list. Long-tail cleanup.
- **Module sub-group drift** — the cross-check skips the `modules`
  group because its TOML shape is structurally different. Module
  manifest validation is covered separately by
  `tests/unit/plugins/test_module_registry.py`.
