# RFC: Declarative Data Plane — Integrations as DB Rows, Not Code

**Date:** 2026-04-24
**Status:** Approved 2026-04-24 — decisions recorded in Section 10. Phase 0 + Phase 1 in flight.
**Authors:** Claude (audit + synthesis), Matt (direction)
**Supersedes / reframes:** ad-hoc plans for [GH-103](https://github.com/Glad-Labs/poindexter/issues/103), [GH-110](https://github.com/Glad-Labs/poindexter/issues/110), [GH-111](https://github.com/Glad-Labs/poindexter/issues/111)
**Covers:** a project-wide pattern that should be applied to ~12 integration surfaces

---

## 1. Big picture

Every time Poindexter needs to talk to the outside world — pull data in, push data out, remember something, forget something, notify somebody — there is a choice:

- **Code path:** write a Python module, thread its config through `site_config`, register a scheduled job, ship it in a release, deploy. New integration = PR.
- **Data path:** insert a row into a table, point it at a named handler, flip `enabled=true`. New integration = SQL.

Poindexter **has already chosen the data path** for some subsystems (taps, topic sources, jobs, stages, image providers, LLM providers — all via `plugins/registry.py` + `PluginConfig`). The rest of the codebase hasn't caught up. This RFC proposes finishing the job so every integration point in the system uses the same declarative shape.

The payoff:

- New external integrations without a deploy
- Operator CRUD via CLI / DB / dashboard — not by editing Python
- One inspection surface for "what is this system connected to?" (Grafana panel of every integration's health)
- Encrypted secrets go through one audited path — closes the [GH-107](https://github.com/Glad-Labs/poindexter/issues/107) raw-get bug class permanently
- `enabled=false` is always the safest failure mode — every activation is deliberate

The short version: **Poindexter has a plugin architecture. Extend it to cover everything.**

---

## 2. What already exists (current state audit)

### The plugin registry

`src/cofounder_agent/plugins/registry.py` exposes 11 entry_point groups:

```python
ENTRY_POINT_GROUPS = {
    "taps":            "poindexter.taps",
    "probes":          "poindexter.probes",
    "jobs":            "poindexter.jobs",
    "stages":          "poindexter.stages",
    "reviewers":       "poindexter.reviewers",
    "adapters":        "poindexter.adapters",
    "providers":       "poindexter.providers",
    "packs":           "poindexter.packs",
    "llm_providers":   "poindexter.llm_providers",
    "topic_sources":   "poindexter.topic_sources",
    "image_providers": "poindexter.image_providers",
}
```

Every registered plugin has a row in `app_settings` keyed `plugin.<type>.<name>` with the shape:

```json
{ "enabled": true, "interval_seconds": 3600, "config": { ... } }
```

The scheduler checks `enabled` at fire-time (not just registration-time), so an operator can disable a plugin with a single SQL UPDATE and the effect is immediate — no restart.

### Subsystems already on the plugin pattern

| Subsystem                | Entry point                  | Registry                           | Config key                     |
| ------------------------ | ---------------------------- | ---------------------------------- | ------------------------------ |
| Memory ingestion taps    | `poindexter.taps`            | `services/taps/runner.py`          | `plugin.tap.<name>`            |
| Topic sources (scrapers) | `poindexter.topic_sources`   | `services/topic_sources/runner.py` | `plugin.topic_source.<name>`   |
| Scheduled jobs           | `poindexter.jobs`            | `plugins/scheduler.py`             | `plugin.job.<name>`            |
| Pipeline stages          | `poindexter.stages`          | `plugins/stage_runner.py`          | `plugin.stage.<name>`          |
| LLM providers            | `poindexter.llm_providers`   | `plugins/llm_provider.py`          | `plugin.llm_provider.<name>`   |
| Image providers          | `poindexter.image_providers` | `plugins/image_provider.py`        | `plugin.image_provider.<name>` |

These are all healthy. New sources can be added without code changes to Poindexter core. **No work is needed here beyond documenting the pattern.**

### Subsystems NOT YET on the plugin pattern

| Subsystem                     | Current shape                                                                                       | Pain                                                                            |
| ----------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Inbound webhooks              | 3 hardcoded FastAPI routes (`routes/external_webhooks.py`, `routes/alertmanager_webhook_routes.py`) | New webhook = new route + new PR + new deploy                                   |
| Outbound webhooks             | Bespoke code in `task_executor.py`, `social_poster.py`, `revalidation_service.py`                   | New destination = new bespoke module                                            |
| Notification channels         | `_notify_discord`, `_notify_telegram` fan-out in `task_executor.py`                                 | New channel (Slack/Email/SMS) = hardcoded dispatch logic                        |
| Retention policies            | None — each prune job hand-rolled                                                                   | New retention rule = new Python file; no runtime toggle                         |
| Publishing targets            | `publish_service.py` + `social_adapters/{bluesky,linkedin,mastodon,reddit,youtube}.py`              | Adapters are plugin-shaped but aren't in the registry yet                       |
| Object stores                 | `r2_upload_service.py` with S3-compatible abstraction                                               | Provider-agnostic internally, but not declaratively registered                  |
| Cache invalidation            | `revalidation_service.py` hardcoded to Vercel ISR                                                   | SvelteKit/Nuxt/Astro/Cloudflare-purge would need new code                       |
| Content validators / QA gates | Hardcoded allowlists in `content_validator.py`, hardcoded gate chain in `cross_model_qa.py`         | New gate = code change                                                          |
| Secret hygiene                | `site_config.get()` vs `site_config.get_secret()` applied inconsistently                            | [GH-107](https://github.com/Glad-Labs/poindexter/issues/107) — latent bug class |

### Secret hygiene — the cross-cutting concern

Every integration that touches a signed secret (webhook HMAC, bot token, storage credentials, ISR shared-secret) has the same footgun: the secret is stored encrypted (`enc:v1:` prefix via pgcrypto), but `site_config.get()` returns the ciphertext verbatim. The correct path is `await site_config.get_secret(key)`, which decrypts. Three incidents in one day (2026-04-23) hit this same bug class — Alertmanager, Vercel revalidation, auto-Telegram post-publish.

Any RFC that unifies integrations must also close this loophole. The integration framework **is** the secret-hygiene enforcement point.

---

## 3. Proposed pattern

All new integration surfaces follow the same shape, which Poindexter's plugin system already uses for its half of the codebase.

### 3.1 Table conventions

Every integration surface gets its own table, but the column shape is consistent:

```sql
CREATE TABLE <surface_name> (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 text UNIQUE NOT NULL,          -- stable slug
    handler_name         text NOT NULL,                 -- registered function in handlers registry
    secret_key_ref       text,                          -- app_settings key of encrypted secret (or NULL)
    enabled              boolean NOT NULL DEFAULT false,-- every activation is a deliberate flip
    config               jsonb NOT NULL DEFAULT '{}',   -- surface-specific config
    metadata             jsonb NOT NULL DEFAULT '{}',   -- operator notes, tags
    last_success_at      timestamptz,
    last_failure_at      timestamptz,
    last_error           text,
    total_success        bigint NOT NULL DEFAULT 0,
    total_failure        bigint NOT NULL DEFAULT 0,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
-- plus surface-specific columns (path, url, schedule, event_filter, ttl_days, etc.)
```

### 3.2 Handler registry

`src/cofounder_agent/services/integrations/handlers.py`:

```python
_HANDLERS: dict[str, Callable] = {}

def register_handler(name: str):
    def decorator(fn):
        _HANDLERS[name] = fn
        return fn
    return decorator

async def dispatch(name: str, payload: Any, *, site_config, row: dict) -> Any:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"unknown handler: {name}")
    return await handler(payload, site_config=site_config, row=row)
```

Handlers are imported by name from DB rows, so adding a new integration with an existing shape requires zero code. Adding a new _shape_ of integration means one new handler function plus an insert.

### 3.3 Secret resolution contract

```python
async def resolve_secret(row: dict, site_config) -> str | None:
    ref = row.get("secret_key_ref")
    if not ref:
        return None
    return await site_config.get_secret(ref)  # always decrypts
```

Every integration fetches its secret through this single function. The raw `.get()` footgun is removed from every new code path, and old code migrates to it as each integration is ported.

### 3.4 CLI conventions

```
poindexter <surface> list [--enabled] [--disabled]
poindexter <surface> show <name>
poindexter <surface> add <name> --handler=<h> [surface-specific args]
poindexter <surface> set-secret <name>            # prompts, stores encrypted
poindexter <surface> rotate <name>
poindexter <surface> enable <name>
poindexter <surface> disable <name>
poindexter <surface> test-fire <name> [--payload=-]
poindexter <surface> remove <name>
```

Same syntax across webhooks, taps, notifications, publishers, retention policies, etc.

### 3.5 Grafana conventions

One panel per surface showing:

- Table of rows: name | enabled | secret-set | last-success-age | total-success | total-failure
- Queue backlog (for anything queued)
- Success-rate gauge per row
- Failed-run alert (auto-fire when `last_failure_at > last_success_at` for >N hours)

All panels share the same Grafana row → **"Integration Health"** replaces the hand-built `system-health.json` single row.

### 3.6 Unified enable/disable semantics

- `enabled=false` on a row → handler not invoked. No retries, no queue buildup, silent skip with an audit log entry noting "disabled, skipped".
- Flipping `enabled` is immediate (no restart).
- Adding a new row `enabled=false` means "configured but not active yet." Matt flips it on when ready.

---

## 4. Integration surfaces — what to migrate

Twelve surfaces. Ranked by leverage (how much new-integration friction disappears per surface moved).

### 4.1 Tier 1 — Ship first

#### 1. Inbound webhooks → [GH-111](https://github.com/Glad-Labs/poindexter/issues/111)

- **Table:** `webhook_endpoints`
- **Surface-specific columns:** `direction='inbound'`, `path`, `signing_algorithm`
- **Handlers:** `revenue_event_writer`, `subscriber_event_writer`, `audit_only`, `alertmanager_dispatch`
- **Migration:** seed three rows (lemon-squeezy, resend, alertmanager) matching current behavior; keep legacy routes as shims until flipped off.
- **Why first:** closes GH-107 bug class across all webhook surfaces; Lemon Squeezy + Resend are the first blockers to revenue/newsletter feedback-loop data (GH-27).

#### 2. Outbound webhooks / notifications / publishing / revalidation

- **Table:** `webhook_endpoints` (same table as 4.1.1)
- **Surface-specific columns:** `direction='outbound'`, `url`, `event_filter` (jsonb), `signing_algorithm`
- **Handlers:** `discord_post`, `telegram_post`, `vercel_isr`, `generic_webhook`
- **Migration:** seed rows for current destinations (discord_ops, telegram_ops, vercel_isr). Existing `webhook_events` queue + `WebhookDeliveryService` becomes the transport for everything.
- **Why bundled:** inbound and outbound share the signing-secret and handler-dispatch logic; two separate tables would be premature splitting.

#### 3. Retention policies → [GH-110](https://github.com/Glad-Labs/poindexter/issues/110)

- **Table:** `retention_policies`
- **Surface-specific columns:** `table_name`, `filter_sql`, `age_column`, `ttl_days`, `downsample_rule` (jsonb), `summarize_handler`
- **Handlers:** `ttl_prune`, `downsample`, `temporal_summarize`
- **Migration:** seed disabled rows for claude_sessions / audit / brain / gpu_metrics / audit_log / brain_decisions. Flip on one at a time.
- **Why first:** 13k embedding rows and 20k gpu_metrics rows have no retention today; every month that passes adds scope to the eventual cleanup.

### 4.2 Tier 2 — Ship next

#### 4. Singer-protocol taps → [GH-103](https://github.com/Glad-Labs/poindexter/issues/103)

- **Table:** `external_taps` (distinct from existing `services/taps/` memory-ingestion; Singer is external-data ingestion)
- **Surface-specific columns:** `tap_type` (singer-io-tap-stripe, custom-script, built-in), `config` (jsonb, encrypted), `state` (jsonb for incremental bookmarks), `target_table`, `record_handler`, `schedule`
- **Handlers:** `revenue_event_writer` (reused from webhooks), `subscriber_event_writer` (reused), `external_metrics_writer`
- **Migration:** seed built-in rows for hackernews / devto / web_search to match current topic-source behavior; migrate scraper plugins to be Singer-tap-shaped so new external data providers just drop in.
- **Why tier 2:** blocked on external_metrics table utility (no paying customers yet), and we need the webhook framework handlers reusable first.

#### 5. Social publishing adapters

- **Table:** `publishing_adapters`
- **Surface-specific columns:** `platform` (bluesky, linkedin, mastodon, reddit, youtube, devto, devto_crosspost), `credentials_ref`, `default_tags`, `rate_limit_per_day`
- **Handlers:** existing `social_adapters/*` modules become handlers keyed by platform name
- **Migration:** seed a row per `services/social_adapters/*.py` file with current defaults; existing `social_poster.py` becomes a thin dispatcher.
- **Why tier 2:** adapters are already almost plugin-shaped; bringing them into the registry + Grafana is mostly refactoring, not architectural.

#### 6. Object stores

- **Table:** `object_stores`
- **Surface-specific columns:** `provider` (cloudflare_r2, aws_s3, b2, minio, wasabi), `endpoint_url`, `bucket`, `public_url`, `credentials_ref`, `cache_busting_strategy`
- **Handlers:** `s3_compatible_uploader` (covers all of them via boto3)
- **Migration:** seed one row for current storage; the `storage_*` namespace migration (already done in `r2_upload_service.py`) becomes the declarative table.
- **Why tier 2:** not painful today because only one store is configured, but a clean landing zone for future "post media to customer's own S3" feature.

### 4.3 Tier 3 — Ship when the pain shows up

#### 7. Cache invalidation backends

- **Table:** `cache_invalidation_backends`
- **Handlers:** `vercel_isr`, `svelte_kit_revalidate`, `cloudflare_purge`, `generic_http_purge`
- **Why tier 3:** only Vercel today. Worth designing the column shape but don't ship rows until second backend emerges.

#### 8. Content validators / QA gates

- **Table:** `qa_gates`
- **Surface-specific columns:** `stage_name`, `reviewer` (programmatic_validator, llm_critic, url_verifier, consistency, web_factcheck, vision_gate), `enabled`, `required_to_pass`, `config` (gate-specific)
- **Handlers:** existing reviewers in `plugins/reviewers.py` — already entry-point-registered
- **Migration:** seed rows for current gate chain; refactor `cross_model_qa.py` to iterate rows instead of hardcoded order.
- **Why tier 3:** current gate chain works; value is unlocked if/when operators want to tune QA per-niche or experiment with gate order without code changes.

#### 9. MCP server integrations

- **Table:** `mcp_connections`
- **Handlers:** generic MCP client invocation
- **Why tier 3:** MCP is operator-side (runs inside Claude/IDE), not pipeline-side. Worth a row per server so operators can see "what tools is Poindexter offering?" but no urgency.

### 4.4 Tier 4 — Probably not worth it

#### 10. Hardcoded allowlists (tech-name whitelist, hallucination dictionary)

- These are **data already** — just stored in Python literals. Moving to DB rows would make them runtime-editable but there's no current pain.
- Leave in code unless an operator asks to tune them per-site.

#### 11. Pipeline stage ordering

- Already plugin-shaped via entry_points, and stage ordering is stable. Moving stage order into DB rows would turn a well-understood sequence into a config surface that could be broken.
- **No-op.**

#### 12. LLM provider routing rules

- LLM providers themselves are already on the plugin pattern. The ROUTING rules (which model for which stage) are hardcoded in stage plugins.
- Could become `plugin.stage.<name>.config.preferred_model` — trivial one-key config, probably already works. **No-op.**

---

## 5. Implementation sequencing

```
Phase 0 (no code, just scaffolding)
├─ services/integrations/ package
│   ├─ __init__.py
│   ├─ handlers.py              (registry + dispatch)
│   ├─ secret_resolver.py       (one audited path)
│   └─ cli.py                   (shared argparse base)
├─ services/integrations/tests/
├─ docs/architecture/integration-conventions.md  (link to this RFC)
└─ migrations/0083_integrations_scaffold.py

Phase 1 (webhooks — GH-111 umbrella)
├─ migrations/0084_create_webhook_endpoints_table.py
├─ services/integrations/webhook_dispatcher.py
├─ services/integrations/handlers/
│   ├─ revenue_event_writer.py
│   ├─ subscriber_event_writer.py
│   ├─ alertmanager_dispatch.py
│   └─ discord_post.py / telegram_post.py / vercel_isr.py
├─ routes/webhooks.py           (catch-all /api/webhooks/{name})
├─ poindexter webhooks CLI
├─ Grafana row: Webhook Health
└─ Migrate existing 3 inbound routes to shims → rows → delete shims

Phase 2 (retention — GH-110)
├─ migrations/0085_create_retention_policies_table.py
├─ services/integrations/retention_runner.py
├─ services/integrations/handlers/
│   ├─ ttl_prune.py
│   ├─ downsample.py
│   └─ temporal_summarize.py    (Phase 3 of the DB plan lives here)
├─ poindexter retention CLI
├─ Grafana row: Retention Health
└─ Seed rows for every append-only source (disabled); flip one at a time

Phase 3 (external taps — GH-103)
├─ migrations/0086_create_external_taps_table.py
├─ services/integrations/tap_runner.py
├─ Singer subprocess harness
├─ poindexter taps CLI
├─ Grafana row: Tap Health
└─ Seed existing topic_sources (hackernews/devto/web_search) as built-in tap rows

Phase 4 (everything else, as pain emerges)
├─ Publishing adapters → publishing_adapters table (Tier 2 #5)
├─ Object stores → object_stores table (Tier 2 #6)
├─ Cache invalidation backends (Tier 3 #7, when 2nd backend emerges)
├─ QA gates (Tier 3 #8, when QA tuning becomes operator-facing)
└─ MCP connections (Tier 3 #9, when customer MCP setup becomes a thing)
```

Each phase is independently shippable. Phases 1–3 are the big rocks; phase 4 items are opportunistic.

---

## 6. Risks and what we are explicitly NOT doing

**We are not** building a customer-facing "add your own integration" UI. Operator-only for now. If Poindexter ever ships as a hosted SaaS, the UI is a later feature on top of this framework.

**We are not** building a plugin marketplace or a hot-install mechanism. Plugins still arrive via `pip install`. The declarative table is for _configuration_, not _distribution_.

**We are not** refactoring the existing healthy plugin subsystems (taps for memory, topic sources, stages, LLM providers, image providers). They already follow the pattern. This RFC documents that and extends it.

**We are not** shipping a 12-surface migration in one release. Phases 1–3 are the non-negotiable ones; the rest wait for pain.

**Risk: secret-handling regressions.** Every migration from hardcoded `.get()` to the framework's `resolve_secret()` touches a signed integration. Mitigation: add a CI lint that flags `site_config.get("*_secret")` or `site_config.get("*_token")` outside the integrations package.

**Risk: framework overhead for one-off integrations.** If an integration is genuinely unique (weird protocol, weird schema), forcing it into the handler-registry shape adds ceremony. Mitigation: a `custom_handler` escape hatch where the row's `handler_name` points at a module path, allowing full Python flexibility while keeping the row-level toggles intact.

**Risk: handler name collisions across surfaces.** `revenue_event_writer` might make sense as both a webhook handler AND a tap handler. Mitigation: namespace handlers by surface in the registry (`webhook.revenue_event_writer`), but allow a handler to register to multiple surfaces.

---

## 7. Open questions

1. **Should all surfaces share one `integrations` table** (with `surface_type` discriminator) or one table per surface? Current proposal is one-per-surface because the surface-specific columns diverge (schedule vs url vs ttl_days), and the common columns are small enough that duplication is cheaper than union.

2. **Row-level vs surface-level enable flags.** Today's `plugin.<type>.<name>` config can disable an entire surface (e.g. all topic sources off). Should that stay, or is per-row sufficient?

3. **How operator-friendly does the CLI need to be in v1?** Minimal (list / enable / disable / set-secret) or full CRUD including payload validation and test-fire? Current proposal: minimal v1, full CRUD in v1.1.

4. **Runbook documentation.** Should each handler have an operator-facing "how to configure" doc in `docs/integrations/<handler>.md`, or is the inline `config` JSON schema sufficient? Current proposal: per-handler markdown in `docs/integrations/` — operators will ask "what are the fields?" and a table beats reading Python.

5. **Test-fire mechanism.** Synthetic payload per handler, or operator-provided payload via CLI flag? Current proposal: both — handlers declare a `synthetic_payload()` method for quick testing, and the CLI accepts `--payload=<file>` for custom.

---

## 8. What this unlocks

- **New webhook integration:** 5 minutes. Insert a row, flip `enabled=true`, share the URL with the third-party service.
- **New retention policy for a new tap:** 1 minute. Insert a row, flip `enabled=true`.
- **Disabling an integration that's misbehaving:** single SQL, immediate effect.
- **Rotating a secret:** `poindexter webhooks set-secret <name>` (prompts), rotated in place.
- **"Is this working?":** one Grafana dashboard row per surface shows every integration's health.
- **Operator onboarding:** the answer to "what does this system talk to?" is `SELECT * FROM webhook_endpoints UNION ALL SELECT * FROM retention_policies UNION ALL ...`.
- **Secret-hygiene bug class ([GH-107](https://github.com/Glad-Labs/poindexter/issues/107)):** closed permanently for every surface that migrates.

---

## 9. Decisions requested

1. Approve the overall pattern (Sections 3–5)? Yes / modify / defer.
2. Approve the tier ranking (Section 4)? Any surface moving up or down?
3. Any of the open questions (Section 7) that should be nailed down before Phase 0 kicks off?
4. Phase 0 scaffolding — do you want to see the scaffolding PR separately before any real migration work starts?

No code runs without a green light on each.

---

## 10. Decisions recorded (2026-04-24)

### Open questions — operator answers

| #   | Question                           | Answer                                                                                |
| --- | ---------------------------------- | ------------------------------------------------------------------------------------- |
| 1   | One table per surface vs shared?   | **One table per surface.** Surface-specific columns stay clean.                       |
| 2   | Row-level vs surface-level enable? | **Per-row.** No surface-wide kill switch; operator toggles individual rows.           |
| 3   | CLI v1 scope?                      | **Minimal** — list / enable / disable / set-secret in v1. Full CRUD in v1.1.          |
| 4   | Per-handler markdown docs?         | **Yes** — every handler gets a doc in `docs/integrations/<handler>.md`.               |
| 5   | Test-fire mechanism?               | **Both** — handlers declare `synthetic_payload()` AND CLI accepts `--payload=<file>`. |

### Decisions requested — operator answers

1. **Overall pattern approved.** Sections 3–5 locked in.
2. **Tier ranking approved with one caveat:** Matt wants Tiers 2–3 either executed now OR explicitly tracked as GH issues with design content for future work. Explicitly rejected items (Tier 4) stay in this doc as a "do not revisit" decision record. **Action:** file GH issues for the remaining Tier 2–3 surfaces not already tracked (social publishing adapters, object stores, cache invalidation backends, QA gates, MCP connections) before Phase 1 ships.
3. Open questions nailed down above.
4. **Phase 0 scaffolding ships with Phase 1** — no separate PR. Execute end-to-end.

### Execution marching orders

1. File GH issues for untracked Tier 2–3 surfaces (task 22).
2. Phase 0 scaffolding (`services/integrations/` package + handlers.py + secret_resolver.py + CLI base + migration 0083) (task 23).
3. Phase 1 webhook framework: `webhook_endpoints` table + catch-all dispatcher + handler migrations for the 3 inbound and 3 outbound destinations (task 24). Legacy routes stay as shims until rows are seeded and verified.
4. Per-handler markdown docs under `docs/integrations/` added alongside each handler.

No pause for review between phases. Report on Telegram when Phase 1 is live.

<!-- DOC-SYNC 2026-04-26: section 3.2 references `services/integrations/handlers.py`; current impl uses a `services/integrations/handlers/` package directory instead. RFC text still describes the originally proposed shape. -->

---

## 11. Post-implementation status (appended 2026-05-09)

All four Tier 1 / Tier 2 corner tables proposed in Section 4 are now live. The RFC pattern is the working pattern.

### Tables that landed

| Surface      | Table                                        | Migration                                                                                   | Issue / PR                                                                          |
| ------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `webhook`    | `webhook_endpoints` (`direction='inbound'`)  | seeded by `0000_baseline.py`                                                                | [`Glad-Labs/poindexter#111`](https://github.com/Glad-Labs/poindexter/issues/111) ✅ |
| `outbound`   | `webhook_endpoints` (`direction='outbound'`) | seeded by `0000_baseline.py`                                                                | bundled with #111 ✅                                                                |
| `tap`        | `external_taps`                              | seeded by `0000_baseline.py`                                                                | [`Glad-Labs/poindexter#103`](https://github.com/Glad-Labs/poindexter/issues/103) ✅ |
| `retention`  | `retention_policies`                         | seeded by `0000_baseline.py`                                                                | [`Glad-Labs/poindexter#110`](https://github.com/Glad-Labs/poindexter/issues/110) ✅ |
| `publishing` | `publishing_adapters`                        | `20260509_175447_add_publishing_adapters.py`                                                | [`Glad-Labs/poindexter#112`](https://github.com/Glad-Labs/poindexter/issues/112) ✅ |
| `qa_gate`    | `qa_gates`                                   | seeded by `0000_baseline.py` + `20260508_215727_seed_qa_gate_deepeval_brand_fabrication.py` | (Tier 3 #8 — landed early)                                                          |

The only Tier 1 table merged into a sibling rather than getting its own surface is **outbound webhooks** — they share `webhook_endpoints` with inbound, discriminated by the `direction` column, exactly as proposed in §4.1.2.

### Handlers actually registered

`src/cofounder_agent/services/integrations/handlers/` ships **14 handlers** across the 5 surfaces. The registry uses the namespacing-by-surface scheme proposed in §6 (Risk: handler name collisions): every handler is registered as `<surface>.<name>` so the same short name (`revenue_event_writer`, `discord_post`) can coexist where it makes sense.

| Surface      | Handler key                       | Module                                     |
| ------------ | --------------------------------- | ------------------------------------------ |
| `webhook`    | `webhook.revenue_event_writer`    | `handlers/revenue_event_writer.py`         |
| `webhook`    | `webhook.subscriber_event_writer` | `handlers/subscriber_event_writer.py`      |
| `webhook`    | `webhook.alertmanager_dispatch`   | `handlers/alertmanager_dispatch.py`        |
| `outbound`   | `outbound.discord_post`           | `handlers/discord_post.py`                 |
| `outbound`   | `outbound.telegram_post`          | `handlers/telegram_post.py`                |
| `outbound`   | `outbound.vercel_isr`             | `handlers/vercel_isr.py`                   |
| `tap`        | `tap.builtin_topic_source`        | `handlers/builtin_topic_source.py`         |
| `tap`        | `tap.external_metrics_writer`     | `handlers/external_metrics_writer.py`      |
| `tap`        | `tap.singer_subprocess`           | `handlers/singer_subprocess.py`            |
| `retention`  | `retention.ttl_prune`             | `handlers/retention_ttl_prune.py`          |
| `retention`  | `retention.downsample`            | `handlers/retention_downsample.py`         |
| `retention`  | `retention.summarize_to_table`    | `handlers/retention_summarize_to_table.py` |
| `publishing` | `publishing.bluesky`              | `handlers/publishing_bluesky.py`           |
| `publishing` | `publishing.mastodon`             | `handlers/publishing_mastodon.py`          |

### Where the RFC's proposals deviated from what shipped

1. **Handler registry is a package, not a module.** §3.2 proposed `services/integrations/handlers.py`. We ship `services/integrations/handlers/` as a package (one file per handler) with `handlers/__init__.py:load_all()` importing them at startup. Same registry, same `register_handler(surface, name)` decorator semantics — the file structure scales better than a single module.
2. **`@register_handler` takes two args, not one.** §3.2 proposed `register_handler(name)`. Actual: `register_handler(surface, name)` to enforce the `<surface>.<name>` key shape automatically.
3. **`secret_key_ref` became `credentials_ref` for publishing rows.** Publishing adapters often need multiple secrets (e.g. Bluesky needs handle + app-password; Mastodon needs instance URL + access token), so the column became a _prefix_ rather than a single key. Each row's `<credentials_ref>handle` / `<credentials_ref>app_password` etc. live in `app_settings`. §3.3's `resolve_secret(row, site_config)` contract still holds — it just runs once per logical secret.
4. **Legacy `routes/webhooks.py` shim was deleted, not kept.** §4.1.1 proposed keeping legacy routes as shims until flipped off. They were deleted entirely on 2026-05-09 once the seed rows + dispatcher + Grafana panels were live (commit `e7daca9`).
5. **CLI v1 ships closer to the v1.1 proposal in §10.3.** The minimal v1 (list / enable / disable / set-secret) shipped first, but the test-fire functionality landed alongside as `poindexter <surface> fire <name>` because operators were using SQL to test-fire and the CLI was already half-built.

### What's NOT yet on the pattern (deliberately)

- **Object stores** (Tier 2 #6) — still hardcoded to one S3-compatible bucket. No second store is configured. RFC §6 explicitly de-prioritized this without pain.
- **Cache invalidation backends** (Tier 3 #7) — only Vercel ISR. `outbound.vercel_isr` covers it adequately as a single outbound handler.
- **MCP connections** (Tier 3 #9) — no operator demand.
- **Hardcoded allowlists** (Tier 4) — confirmed no-op. The hallucination dictionary stays in Python.

### Cross-cutting wins beyond the RFC's stated payoff

- **Lane B's cost-tier API** (`docs/architecture/cost-tier-routing.md`) inherited the same "configuration is data, not code" muscle memory. The cost_tier rows feed `app_settings.cost_tier.<tier>.model` instead of a new table, but the operator workflow is identical: insert a row, no deploy.
- **Lane A's prompt management** (`docs/architecture/prompt-management.md`) is the prompt-surface analogue: YAML-on-disk default + Langfuse runtime override + the same `get_prompt(key)` shape across all callers.
- **Snapshot tests pinned every migrated body byte-for-byte** during Lane A. The integrations framework's handler tests follow the same shape: contract-pinned test per handler, ports cleanly across handler renames or schema tweaks.

### Pickup pointer for new readers

If you're trying to add a new external integration in 2026-05+, the answer is **almost always one of the 5 surfaces listed in `docs/integrations/README.md`**. Read the per-handler doc that matches the closest existing handler, copy the row shape, and write the handler module. No new RFC needed — this one already covers the pattern.
