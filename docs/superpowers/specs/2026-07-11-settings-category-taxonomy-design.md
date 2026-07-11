# Settings Category Taxonomy — Design Spec

**Date:** 2026-07-11
**Status:** Approved design, pending plan
**Author:** Claude (with Matt)
**Area:** operator console — `app_settings` categorization

## Problem

The operator console's **App Settings** tab groups keys by their
`app_settings.category` column. That column is a mess:

- **60% of keys (698 of 1,172) sit in `general`** — clicking any real
  category shows a handful of keys while `general` is an unnavigable dump.
- **62 distinct categories**, many semantic duplicates:
  `integration`+`integrations`; `monitoring`+`observability`+`prometheus`+
  `grafana`+`logging`; `qa`+`quality`+`content_qa`+`gates`;
  `secrets`+`api_keys`+`tokens`+`external_apis`; `media`+`image`+`podcast`+
  `voice`+`voice_agent`; `cost`+`finance`; `social`+`publishing`.
- **11 singleton categories** (1 key each) fragmenting the sidebar.

### Root cause

The `category` column has **no single writer and no shared vocabulary**.
Three writers stamp it independently:

1. **`settings_defaults.py::seed_all_defaults`** — the boot seeder that
   applies `DEFAULTS` every startup — **hardcodes `category = 'general'`**
   on insert ([settings_defaults.py:2251](../../../src/cofounder_agent/services/settings_defaults.py) and `:2358`).
   Since the CLAUDE.md convention routes _every_ new key through
   `settings_defaults.py`, `general` grows monotonically forever.
2. **The baseline `seeds.sql`** — stamps real categories for the keys it
   seeds (the ~474 non-`general` keys).
3. **Ad-hoc `set_setting()` calls** — stamp whatever string a developer typed.

Three writers, no shared vocabulary → 62 ad-hoc categories, most work
piling into `general`.

### Verified constraints

- **`app_settings.category` is display/filter-only.** A full grep for
  functional branches (`WHERE category`, `category ==`, `category IN`,
  `get_all_settings(category=)`) found only: the settings-route sidebar
  filter (intended), and `alertmanager_webhook_routes.py:304` which reads
  an **Alertmanager alert label** named `category`, unrelated to
  `app_settings`. Nothing branches on an `app_settings.category` _value_
  for behavior → **re-stamping every row is behaviorally safe**.
- The console **already derives the sidebar dynamically** from whatever
  distinct categories are present in the returned rows
  ([api.js `deriveCategories`](../../../src/cofounder_agent/console/js/api.js)), reusing
  the mock's curated labels where ids match, else Title-Casing the raw id.
  So a clean DB → a clean sidebar automatically; the console change is
  small (labels + order).
- The `SettingCategoryEnum` (8 abstract values: `general/api/database/
security/feature_flags/performance/logging/integration`) is **dead** —
  the list route explicitly bypasses it to accept raw strings
  ([settings_routes.py:69-74](../../../src/cofounder_agent/routes/settings_routes.py)).

## Goals

- Collapse 62 categories → **13 canonical categories**, no duplicates.
- Drain `general` from 698 keys to a tiny residual (**≤ 20 keys**, tracked
  by a test).
- Make categorization **deterministic and self-healing** so the mess
  cannot recur: one resolver every writer defers to.
- Keep the console read/update UX exactly as Matt likes it; only the
  category grouping improves.

## Non-goals (explicitly out of scope)

- **Two-level (category → subcategory) sidebar.** Matt chose the flat set.
  Sub-grouping large buckets is a possible future phase.
- Backend-sourced category _labels_ (a `/api/settings/categories`
  endpoint). The console keeps owning presentation (labels + order) for
  now; revisit only if label drift becomes a problem.
- Changing any setting **value**, `is_secret` flag, or the read/write API
  contract. This is purely a re-grouping.
- **No dedicated secrets/security bucket.** Credentials route to their
  subsystem by prefix like any other key; the `is_secret` DB flag (which
  the console already honors by masking) remains the sole
  "this-is-a-credential" signal. See Design §1 and Resolved Decisions.

## Design

Four parts, one source of truth.

### 1. `services/settings_categories.py` (new) — the single authority

```python
# The canonical taxonomy: ordered. `order` drives console sidebar order.
CATEGORIES: list[dict[str, str | int]]  # {id, label, order}

def resolve_category(key: str) -> str:
    """Deterministic key → canonical category id. Pure function, no I/O."""

CATEGORY_IDS: frozenset[str]  # {c["id"] for c in CATEGORIES}, for validation
```

**The 13 canonical categories** (id → label, in sidebar order):

| #   | id               | label                |
| --- | ---------------- | -------------------- |
| 1   | `identity`       | Identity & Site      |
| 2   | `pipeline`       | Pipeline             |
| 3   | `content`        | Content & Knowledge  |
| 4   | `quality`        | Quality / QA         |
| 5   | `models`         | Models & LLM         |
| 6   | `media`          | Media                |
| 7   | `distribution`   | Distribution         |
| 8   | `cost`           | Cost & Finance       |
| 9   | `observability`  | Observability        |
| 10  | `self_healing`   | Brain & Self-Healing |
| 11  | `plugins`        | Plugins              |
| 12  | `integrations`   | Integrations         |
| 13  | `infrastructure` | Infrastructure       |

**Resolver algorithm** — first match wins, in this order:

1. **Explicit key overrides** — an exact-key `dict[str, str]` for true
   one-offs whose name misleads (e.g. `default_media_to_generate` → `media`).
2. **Ordered prefix rules** — a list of `(prefix, category)` matched
   **longest-prefix-first** so specific beats general (`daily_spend` before
   `daily`, `mcp_http_probe` before `mcp`, `auto_publish` before `auto`).
3. **Fallback → `general`** (kept as an honest catch-all; the completeness
   test keeps it ≤ 20).

**Secrets are not special-cased.** A credential routes to its subsystem by
prefix like any other key — `mercury_api_key` → `cost`, `resend_api_key` →
`integrations`, `anthropic_api_key` → `models`, `grafana_*` secret →
`observability`. The `is_secret` DB flag stays the sole credential signal
(the console already masks on it), so no consolidation bucket is needed.
`resolve_category` therefore takes only `key` — it never reads `is_secret`.

**Grounded prefix rules** (starter set, from the live keyspace — the
implementer completes the tail against the completeness test):

| prefix(es)                                                                                                                                                                                                                                                                                                                                        | → category       | notes                        |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | ---------------------------- |
| `identity`, `site`, `company`, `owner`, `support`, `brand`, `legal`                                                                                                                                                                                                                                                                               | `identity`       |                              |
| `pipeline`, `prefect`, `auto_publish`, `default_template`, `default_model_tier`, `default_workflow`, `staging`, `require_human`, `publish_spacing`, `approval`, `stale_task`, `max_posts`, `max_task`, `max_approval`, `experiment`, `orchestr`, `scheduling`                                                                                     | `pipeline`       |                              |
| `content`, `writer`, `writer_rag`, `rag`, `seo`, `topic`, `niche`, `citation`, `embedding`, `auto_embed`, `memory`, `newsletter`, `title`, `code_density`, `auto_append`, `internal_link`, `default_ollama_model`, `serper`, `igdb`, `research`                                                                                                   | `content`        |                              |
| `qa`, `quality`, `deepeval`, `ragas`, `self_consistency`, `unlinked`, `min_curation`                                                                                                                                                                                                                                                              | `quality`        |                              |
| `model` (`model_role*`, `model_eval*`), `models`, `cloud_api`, `llm`, `anthropic`, `openai`, `openrouter`, `gemini`, `groq`                                                                                                                                                                                                                       | `models`         | incl. provider API keys      |
| `media`, `image`, `video`, `podcast`, `voice`, `tts`, `vision`, `stable`, `sdxl`, `caption`, `default_media`, `livekit`, `elevenlabs`, `pexels`, `unsplash`                                                                                                                                                                                       | `media`          |                              |
| `social`, `publishing`, `postiz`, `offsite`, `devto`, `reddit`, `bluesky`, `linkedin`, `indexnow`                                                                                                                                                                                                                                                 | `distribution`   |                              |
| `cost`, `finance`, `mercury`, `lemon`, `stripe`, `electricity`, `daily_spend`, `daily_budget`, `monthly_spend`                                                                                                                                                                                                                                    | `cost`           |                              |
| `findings`, `grafana`, `prometheus`, `langfuse`, `glitchtip`, `sentry`, `uptime`, `alertmanager`, `monitoring`, `observability`, `logging`, `tracing`, `otel`, `pyroscope`, `data_fabric`, `data_freshness`, `beacon`, `live_activity`, `smart_monitor`, `branch_drift`, `pr_staleness`, `morning_brief`, `page_view`, `analytics`, `performance` | `observability`  | probes/telemetry/digests     |
| `brain`, `ops_firefighter`, `ops_triage`, `firefighter`, `self_heal`, `mcp_http_probe`, `alert`, `notification`, `anticipation`                                                                                                                                                                                                                   | `self_healing`   |                              |
| `plugin` (incl. `plugin.*`)                                                                                                                                                                                                                                                                                                                       | `plugins`        | whole plugin namespace       |
| `integration`, `mcp`, `webhook`, `cloudflare`, `cloudinary`, `storage`, `resend`, `smtp`, `google`, `tap`, `external_tap`, `discord`, `telegram`, `notion`, `openclaw`, `gh`, `github`                                                                                                                                                            | `integrations`   |                              |
| `infrastructure`, `system`, `gpu`, `ollama`, `docker`, `compose`, `restore`, `backup`, `migration`, `clock`, `rate`, `cli`, `shared_http`, `prefect_worker`, `redis`, `scripts`, `revalidate`, `settings`, `operator`, `auth`, `oauth`, `cors`, `jwt`, `secret`, `api_token`, `api_key`                                                           | `infrastructure` | incl. app auth/access config |

_(Ambiguity resolutions verified against live keys: `compose_*` is
docker-compose → `infrastructure` (NOT media); `mcp_http_probe_*` →
`self_healing` while `mcp_oauth_*` (the MCP server's own OAuth creds) →
`integrations`; `vision_alt_*` → `media`; `self_consistency_*` →
`quality`; `data_fabric_*` → `observability`. The 68 `is_secret` keys
scatter to their subsystems by these same prefixes.)_

### 2. Seeder rewire

Replace the hardcoded `'general'` in both `settings_defaults.py` INSERTs
with `resolve_category(key)`. New keys self-categorize at seed time,
forever.

Additionally, make **`admin_db.set_setting(...)`** default its category to
`resolve_category(key)` when a caller passes none, closing the third writer
(the API create path + any ad-hoc setter).

### 3. Boot reconcile (converge existing rows, self-heal drift)

After the `DEFAULTS` seed loop in `seed_all_defaults(pool)`, add a reconcile
pass:

```python
rows = await conn.fetch("SELECT key, category FROM app_settings")
updates = [
    (resolve_category(r["key"]), r["key"])
    for r in rows
    if r["category"] != resolve_category(r["key"])
]
if updates:
    await conn.executemany(
        "UPDATE app_settings SET category = $1 WHERE key = $2", updates
    )
```

- Converges today's prod (698-in-`general` → clean) on the next worker boot.
- Self-heals: steady-state writes 0 rows; any future drift re-corrects each
  boot.
- **No migration needed** — this is a boot-time reconcile, not schema DDL.
  It also honors "seed data belongs in the seeder, not migration files": the
  reconcile lives beside the seeder and runs every boot, so it never goes
  stale.
- **No periodic job needed** — new keys only enter via `seed_all_defaults`
  (boot-coupled), and reconcile runs in the same pass, so boot cadence is
  sufficient.

### 4. Console reconcile

- Replace the 12-entry `CATEGORIES` array in
  [`console/js/settings-data.js`](../../../src/cofounder_agent/console/js/settings-data.js)
  with the 13 canonical `{id, label}` in canonical order.
- Make the live sidebar render **in canonical order**. `deriveCategories`
  currently emits categories in first-seen row order; sort its output by
  each id's index in `PX_SETTINGS.categories` (known ids first in defined
  order, any unknown appended). Small, contained change.
- Re-stamp the mock `S` rows' `category` fields to canonical ids so
  offline-dev fidelity matches live.
- **Retire `SettingCategoryEnum`**: it's dead. Replace its members with the
  13 canonical ids (so `SettingCreate.category` validates against the real
  taxonomy) OR drop it and validate `create_setting` against
  `settings_categories.CATEGORY_IDS`. Prefer the latter (single source of
  truth in one Python module; no enum duplication). Keep `SettingResponse`'s
  free-string `category` override as-is (backcompat during the reconcile
  window).

## How drift stops recurring

```
                    ┌────────────────────────┐
   every writer  →  │  resolve_category(key)  │  → one answer
                    └────────────────────────┘
   seed_all_defaults INSERT ──┐
   admin_db.set_setting()  ───┼──► defer to resolver (was: 'general' / typed)
   boot reconcile pass    ────┘
```

Assignment is owned by the backend resolver (deterministic); presentation
(labels + order) is owned by the console. "What category is this key?" has
exactly one computed answer.

## Testing strategy

Backend (`tests/unit/test_settings_categories.py`):

1. **Anchor mappings** — spot-check high-value keys resolve correctly:
   `findings_*` → `observability`, `qa.*`/`self_consistency_*` → `quality`,
   `plugin.llm_provider.*` → `plugins`, `compose_drift_*` →
   `infrastructure`, `daily_spend_limit_usd` → `cost`, `daily_post_limit`
   → `pipeline`, and secrets to their subsystem (`mercury_api_key` →
   `cost`, `resend_api_key` → `integrations`).
2. **Completeness / anti-drift gate** — the forcing function. Load the full
   live keyspace (fixture snapshot of all keys), assert:
   - every key resolves to a member of `CATEGORY_IDS`;
   - the `general` residual count is **≤ 20** (fails CI if the tail grows).
     This test is _how_ the implementer completes the prefix/override tail:
     run it, inspect what falls into `general`, add rules until green.
3. **Determinism** — `resolve_category` is pure (same input → same output,
   no I/O).
4. **Seeder rewire** — seeding a brand-new key stamps `resolve_category`,
   not `general`.
5. **Reconcile** — given rows with wrong categories, the reconcile computes
   the correct minimal update set and leaves already-correct rows untouched
   (0 writes in steady state).

Console (`console/js/__tests__/`, node --test — pure JS, no DOM):

6. `deriveCategories` emits known categories in canonical order.
7. Drift guard: every id in `settings-data.js` `CATEGORIES` ∈ the 13
   canonical ids (keeps the mock honest against the backend list —
   duplicated by necessity across the Python/JS boundary, guarded by test).

Docs/contract:

8. Update the openapi/contract snapshot if the create-path category
   validation changes.

## Rollout & verification

Console + backend both ship in one PR (branch off `origin/main`). Standard
flow: PR → CI green → merge.

**On prod after merge:**

- The worker restarts on deploy → `seed_all_defaults` runs → reconcile
  converges all rows in one pass.
- Verify:
  `SELECT category, count(*) FROM app_settings GROUP BY 1 ORDER BY 2 DESC;`
  → expect ≤ 13 categories (+ a `general` residual ≤ 20), no dupes.
- Open the console App Settings tab → sidebar shows the 13 curated labels
  in order, `general` no longer dominates.
- The console is bind-mount static (deploy-checkout sync, no restart needed
  for the JS/CSS half); the backend half needs the worker restart the
  deploy already performs.

## Risks & mitigations

| Risk                                             | Mitigation                                                                                                                                               |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Resolver misclassifies a key                     | Cosmetic only (sidebar grouping; never touches value). Anchor + completeness tests catch regressions; a wrong bucket is a one-line override fix.         |
| Reconcile fights a human-set category            | Categories are **not** user-editable in the console (only value/description). Category is purely system-derived → safe to overwrite.                     |
| `general` tail creeps back up                    | The ≤ 20 completeness test fails CI when a new key doesn't match any rule, forcing a rule/override at add-time.                                          |
| Python `CATEGORIES` vs JS mock drift             | Console drift-guard test (test 7) asserts JS ids ⊆ canonical.                                                                                            |
| A secret lands in an unexpected subsystem bucket | It's still masked (the `is_secret` flag is unchanged); grouping is cosmetic. Anchor tests pin the high-value credentials; misses are one-line overrides. |

## Resolved decisions

1. **No secrets/security bucket** _(Matt, 2026-07-11)_. The `is_secret` flag
   already marks credentials and the console masks on it, so a separate
   category is redundant and would split an integration's config from its
   key. Secrets route to their subsystem by prefix. Consequence: the
   would-be "Security & Secrets" bucket had exactly **one** non-secret key
   (`oauth_issuer_url`), so it's dropped — taxonomy is **13**, not 14 — and
   `oauth`/`cors`/`jwt`/generic-`secret` config folds into Infrastructure.
2. **`newsletter` → `content`** (authored content) rather than
   `distribution` (outbound channel). Reversible one-liner.
3. **`memory` + `embedding` → `content`** (folded into Content & Knowledge)
   rather than a standalone "Memory" bucket. Reversible.
