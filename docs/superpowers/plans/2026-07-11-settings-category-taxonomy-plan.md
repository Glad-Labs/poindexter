# Settings Category Taxonomy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the `app_settings` category mess (698/1,172 keys in `general`, 62 ad-hoc categories) into a deterministic, self-healing flat 13-category taxonomy driven by one resolver.

**Architecture:** A pure `resolve_category(key)` in a new `services/settings_categories.py` becomes the single authority. The boot seeder + `admin_db.set_setting` defer to it, a boot-time reconcile re-stamps existing rows, and the console renders the 13 curated labels. Category is display-only (verified), so re-stamping is safe.

**Tech Stack:** Python 3.12 / asyncpg / FastAPI (backend), vanilla JS + `node --test` (console), pytest (backend tests).

**Spec:** [`docs/superpowers/specs/2026-07-11-settings-category-taxonomy-design.md`](../specs/2026-07-11-settings-category-taxonomy-design.md)

## Global Constraints

- **13 canonical category ids** (order = sidebar order): `identity`, `pipeline`, `content`, `quality`, `models`, `media`, `distribution`, `cost`, `observability`, `self_healing`, `plugins`, `integrations`, `infrastructure`. Plus `general` as the catch-all fallback (not a "real" category — kept ≤ 20 keys by test).
- **Secrets are NOT special-cased.** `resolve_category` takes only `key`; it never reads `is_secret`. Credentials route to their subsystem by prefix.
- **Category is display-only** — never gate behavior on it; only value/description are user-editable, so re-stamping is safe.
- **Branch:** `claude/settings-category-taxonomy` (already exists, off `origin/main`, draft PR #2320). Commit each task; do NOT open a new branch.
- **Backend tests** run from `src/cofounder_agent` via `poetry run pytest`. In a fresh worktree with no venv, use the main checkout's venv python with `-o addopts=""` (skips `--forked`) per repo convention.
- **Console tests:** `npm run test:console` (node --test, pure JS, no DOM).
- New `app_settings` keys, if any, go in `settings_defaults.py` — but this plan adds **no** new settings keys.

## File Structure

- **Create:** `src/cofounder_agent/services/settings_categories.py` — the canonical taxonomy + `resolve_category` (one responsibility: key → category id).
- **Create:** `src/cofounder_agent/tests/unit/services/test_settings_categories.py` — resolver anchors + determinism + completeness gate.
- **Create:** `src/cofounder_agent/tests/unit/services/fixtures/app_settings_keyspace.json` — snapshot of the live keyspace for the completeness gate.
- **Create:** `src/cofounder_agent/console/js/__tests__/api.categories.test.js` — `deriveCategories` ordering + drift guard.
- **Modify:** `src/cofounder_agent/services/settings_defaults.py` — seeder rewire (2 INSERTs) + boot reconcile pass.
- **Modify:** `src/cofounder_agent/services/admin_db.py` — `set_setting` resolves category when `None`.
- **Modify:** `src/cofounder_agent/console/js/settings-data.js` — 13 canonical `CATEGORIES`, mock rows re-stamped.
- **Modify:** `src/cofounder_agent/console/js/api.js` — `deriveCategories` emits canonical order.
- **Modify:** `src/cofounder_agent/schemas/settings_schemas.py` + `src/cofounder_agent/routes/settings_routes.py` — retire `SettingCategoryEnum`, validate create against `CATEGORY_IDS`.
- **Modify (maybe):** `src/cofounder_agent/tests/integration_db/test_settings_defaults_integration.py` — reconcile behavior with a real DB.

---

### Task 1: Canonical taxonomy module + resolver + unit tests

**Files:**

- Create: `src/cofounder_agent/services/settings_categories.py`
- Create: `src/cofounder_agent/tests/unit/services/test_settings_categories.py`
- Create: `src/cofounder_agent/tests/unit/services/fixtures/app_settings_keyspace.json`

**Interfaces:**

- Produces: `CATEGORIES: list[dict]` (`{"id","label","order"}`), `CATEGORY_IDS: frozenset[str]`, `resolve_category(key: str) -> str`. Consumed by Tasks 2, 3, 5.

- [ ] **Step 1: Generate the keyspace fixture**

Run against the live DB (mcp postgres or psql) and write the sorted key list:

```sql
SELECT key FROM app_settings ORDER BY key;
```

Write the result as a JSON array to `src/cofounder_agent/tests/unit/services/fixtures/app_settings_keyspace.json`, e.g. `["approval_ttl_days", "auto_embed_watch_enabled", ...]`. This snapshot lets the completeness gate run hermetically in CI. (Regenerate when the migration baseline is re-squashed.)

- [ ] **Step 2: Write the failing resolver test**

Create `src/cofounder_agent/tests/unit/services/test_settings_categories.py`:

```python
"""Unit tests for the app_settings category resolver."""
import json
from pathlib import Path

import pytest

from services.settings_categories import (
    CATEGORIES,
    CATEGORY_IDS,
    resolve_category,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "app_settings_keyspace.json"


def test_thirteen_canonical_categories():
    ids = [c["id"] for c in CATEGORIES]
    assert len(ids) == 13
    assert len(set(ids)) == 13  # no dupes
    assert "general" not in ids  # general is the fallback, not a category
    # order is contiguous 1..13
    assert sorted(c["order"] for c in CATEGORIES) == list(range(1, 14))


@pytest.mark.parametrize(
    "key,expected",
    [
        ("findings_alert_route_watermark", "observability"),
        ("qa_overall_score_threshold", "quality"),
        ("self_consistency_threshold", "quality"),
        ("plugin.llm_provider.primary.free", "plugins"),
        ("compose_drift_auto_recover_enabled", "infrastructure"),
        ("daily_spend_limit_usd", "cost"),
        ("daily_post_limit", "pipeline"),
        ("auto_publish_threshold", "pipeline"),
        ("auto_embed_watch_enabled", "content"),
        ("mercury_api_key", "cost"),            # secret → subsystem
        ("resend_api_key", "integrations"),     # secret → subsystem
        ("anthropic_api_key", "models"),        # secret → subsystem
        ("grafana_admin_password", "observability"),
        ("mcp_http_probe_enabled", "self_healing"),
        ("mcp_oauth_client_id", "integrations"),
        ("vision_alt_model", "media"),
        ("site_name", "identity"),
    ],
)
def test_anchor_mappings(key, expected):
    assert resolve_category(key) == expected


def test_determinism():
    assert resolve_category("qa_x") == resolve_category("qa_x")


def test_every_live_key_resolves_to_a_canonical_id():
    keys = json.loads(_FIXTURE.read_text())
    for k in keys:
        assert resolve_category(k) in (CATEGORY_IDS | {"general"})


def test_general_residual_is_small():
    keys = json.loads(_FIXTURE.read_text())
    residual = [k for k in keys if resolve_category(k) == "general"]
    assert len(residual) <= 20, f"{len(residual)} keys fell to general: {residual}"
```

- [ ] **Step 3: Run it to confirm it fails** — `ModuleNotFoundError: services.settings_categories`.

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_categories.py -q`
Expected: FAIL (import error).

- [ ] **Step 4: Implement the resolver module**

Create `src/cofounder_agent/services/settings_categories.py`:

```python
"""Canonical app_settings category taxonomy + deterministic resolver.

Single source of truth for which category a settings key belongs to. Every
writer (the boot seeder, admin_db.set_setting, the boot reconcile) defers to
resolve_category() so the category column has exactly one computed answer and
cannot drift back into the 62-category / 698-in-'general' mess.

Category is display-only (drives the console sidebar); nothing branches on it.
Secrets are NOT special-cased — a credential routes to its subsystem by prefix
like any other key; the is_secret DB flag remains the credential signal.
"""
from __future__ import annotations

# id → label, in sidebar order. `general` is the fallback, deliberately NOT here.
CATEGORIES: list[dict[str, str | int]] = [
    {"id": "identity", "label": "Identity & Site", "order": 1},
    {"id": "pipeline", "label": "Pipeline", "order": 2},
    {"id": "content", "label": "Content & Knowledge", "order": 3},
    {"id": "quality", "label": "Quality / QA", "order": 4},
    {"id": "models", "label": "Models & LLM", "order": 5},
    {"id": "media", "label": "Media", "order": 6},
    {"id": "distribution", "label": "Distribution", "order": 7},
    {"id": "cost", "label": "Cost & Finance", "order": 8},
    {"id": "observability", "label": "Observability", "order": 9},
    {"id": "self_healing", "label": "Brain & Self-Healing", "order": 10},
    {"id": "plugins", "label": "Plugins", "order": 11},
    {"id": "integrations", "label": "Integrations", "order": 12},
    {"id": "infrastructure", "label": "Infrastructure", "order": 13},
]

CATEGORY_IDS: frozenset[str] = frozenset(c["id"] for c in CATEGORIES)

# Exact-key overrides for names whose prefix misleads. Checked first.
_OVERRIDES: dict[str, str] = {
    "default_media_to_generate": "media",
    "default_ollama_model": "content",
    "default_template_slug": "pipeline",
    "default_model_tier": "pipeline",
    "default_workflow_gates": "pipeline",
}

# (prefix, category). Longest-prefix-first (sorted at module load), first match wins.
_PREFIX_RULES_RAW: list[tuple[str, str]] = [
    # identity
    ("identity", "identity"), ("site", "identity"), ("company", "identity"),
    ("owner", "identity"), ("support", "identity"), ("brand", "identity"),
    ("legal", "identity"),
    # pipeline
    ("pipeline", "pipeline"), ("prefect", "pipeline"), ("auto_publish", "pipeline"),
    ("staging", "pipeline"), ("require_human", "pipeline"), ("publish_spacing", "pipeline"),
    ("approval", "pipeline"), ("stale_task", "pipeline"), ("max_posts", "pipeline"),
    ("max_task", "pipeline"), ("max_approval", "pipeline"), ("experiment", "pipeline"),
    ("orchestr", "pipeline"), ("scheduling", "pipeline"), ("daily_post", "pipeline"),
    ("daily_budget", "pipeline"),
    # content & knowledge
    ("content", "content"), ("writer", "content"), ("rag", "content"),
    ("seo", "content"), ("topic", "content"), ("niche", "content"),
    ("citation", "content"), ("embedding", "content"), ("auto_embed", "content"),
    ("memory", "content"), ("newsletter", "content"), ("title", "content"),
    ("code_density", "content"), ("auto_append", "content"), ("internal_link", "content"),
    ("serper", "content"), ("igdb", "content"), ("research", "content"),
    # quality
    ("qa", "quality"), ("quality", "quality"), ("deepeval", "quality"),
    ("ragas", "quality"), ("self_consistency", "quality"), ("unlinked", "quality"),
    ("min_curation", "quality"),
    # models & llm
    ("model", "models"), ("cloud_api", "models"), ("llm", "models"),
    ("anthropic", "models"), ("openai", "models"), ("openrouter", "models"),
    ("gemini", "models"), ("groq", "models"),
    # media
    ("media", "media"), ("image", "media"), ("video", "media"),
    ("podcast", "media"), ("voice", "media"), ("tts", "media"),
    ("vision", "media"), ("stable", "media"), ("sdxl", "media"),
    ("caption", "media"), ("livekit", "media"), ("elevenlabs", "media"),
    ("pexels", "media"), ("unsplash", "media"),
    # distribution
    ("social", "distribution"), ("publishing", "distribution"), ("postiz", "distribution"),
    ("offsite", "distribution"), ("devto", "distribution"), ("reddit", "distribution"),
    ("bluesky", "distribution"), ("linkedin", "distribution"), ("indexnow", "distribution"),
    # cost & finance
    ("cost", "cost"), ("finance", "cost"), ("mercury", "cost"),
    ("lemon", "cost"), ("stripe", "cost"), ("electricity", "cost"),
    ("daily_spend", "cost"), ("monthly_spend", "cost"),
    # observability
    ("findings", "observability"), ("grafana", "observability"), ("prometheus", "observability"),
    ("langfuse", "observability"), ("glitchtip", "observability"), ("sentry", "observability"),
    ("uptime", "observability"), ("alertmanager", "observability"), ("monitoring", "observability"),
    ("observability", "observability"), ("logging", "observability"), ("tracing", "observability"),
    ("otel", "observability"), ("pyroscope", "observability"), ("data_fabric", "observability"),
    ("data_freshness", "observability"), ("beacon", "observability"), ("live_activity", "observability"),
    ("smart_monitor", "observability"), ("branch_drift", "observability"), ("pr_staleness", "observability"),
    ("morning_brief", "observability"), ("page_view", "observability"), ("analytics", "observability"),
    ("performance", "observability"),
    # self-healing
    ("brain", "self_healing"), ("ops_firefighter", "self_healing"), ("ops_triage", "self_healing"),
    ("firefighter", "self_healing"), ("self_heal", "self_healing"), ("mcp_http_probe", "self_healing"),
    ("alert", "self_healing"), ("notification", "self_healing"), ("anticipation", "self_healing"),
    # plugins
    ("plugin", "plugins"),
    # integrations
    ("integration", "integrations"), ("mcp", "integrations"), ("webhook", "integrations"),
    ("cloudflare", "integrations"), ("cloudinary", "integrations"), ("storage", "integrations"),
    ("resend", "integrations"), ("smtp", "integrations"), ("google", "integrations"),
    ("tap", "integrations"), ("external_tap", "integrations"), ("discord", "integrations"),
    ("telegram", "integrations"), ("notion", "integrations"), ("openclaw", "integrations"),
    ("gh", "integrations"), ("github", "integrations"),
    # infrastructure (incl. app auth/access config)
    ("infrastructure", "infrastructure"), ("system", "infrastructure"), ("gpu", "infrastructure"),
    ("ollama", "infrastructure"), ("docker", "infrastructure"), ("compose", "infrastructure"),
    ("restore", "infrastructure"), ("backup", "infrastructure"), ("migration", "infrastructure"),
    ("clock", "infrastructure"), ("rate", "infrastructure"), ("cli", "infrastructure"),
    ("shared_http", "infrastructure"), ("redis", "infrastructure"), ("scripts", "infrastructure"),
    ("revalidate", "infrastructure"), ("settings", "infrastructure"), ("operator", "infrastructure"),
    ("auth", "infrastructure"), ("oauth", "infrastructure"), ("cors", "infrastructure"),
    ("jwt", "infrastructure"), ("secret", "infrastructure"), ("api_token", "infrastructure"),
    ("api_key", "infrastructure"),
]

# Longest prefix first so `daily_spend` beats `daily`, `mcp_http_probe` beats `mcp`.
_PREFIX_RULES: list[tuple[str, str]] = sorted(
    _PREFIX_RULES_RAW, key=lambda r: len(r[0]), reverse=True
)


def resolve_category(key: str) -> str:
    """Deterministic key → canonical category id. Pure, no I/O.

    Order: exact override → longest-matching prefix → 'general' fallback.
    """
    if key in _OVERRIDES:
        return _OVERRIDES[key]
    for prefix, category in _PREFIX_RULES:
        if key.startswith(prefix):
            return category
    return "general"
```

- [ ] **Step 5: Run the tests; iterate rules to green**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_categories.py -q`
Expected: anchors + determinism + `test_every_live_key_resolves` PASS. If `test_general_residual_is_small` FAILS, it prints the keys that fell to `general` — add a prefix rule or `_OVERRIDES` entry for each cluster and re-run until the residual is ≤ 20. This iterative loop is the intended forcing function.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/settings_categories.py \
        src/cofounder_agent/tests/unit/services/test_settings_categories.py \
        src/cofounder_agent/tests/unit/services/fixtures/app_settings_keyspace.json
git commit -m "feat(settings): canonical 13-category taxonomy + deterministic resolver"
```

---

### Task 2: Seeder rewire — seed INSERTs + `set_setting` defer to the resolver

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py:2246-2258` (main seed INSERT) and `:2354-2368` (operator-overlay INSERT)
- Modify: `src/cofounder_agent/services/admin_db.py:475-511` (`set_setting`)
- Modify: `src/cofounder_agent/tests/unit/services/test_settings_defaults.py`

**Interfaces:**

- Consumes: `resolve_category` from Task 1.

- [ ] **Step 1: Write the failing test** (extend `test_settings_defaults.py`):

```python
def test_seed_insert_uses_resolved_category(monkeypatch):
    """The seed INSERT must bind resolve_category(key), never a literal 'general'."""
    import services.settings_defaults as sd

    captured = {}

    class _FakeConn:
        async def execute(self, sql, *args):
            if "INSERT INTO app_settings" in sql and "category" in sql:
                captured["sql"] = sql
                captured["args"] = args
            return "INSERT 0 1"
        # fetch/executemany stubbed so this test survives Task 3's reconcile
        # pass (which runs on the same connection).
        async def fetch(self, sql, *args): return []
        async def executemany(self, sql, args): return None
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _FakeAcquire:
        async def __aenter__(self): return _FakeConn()
        async def __aexit__(self, *a): return False

    class _FakePool:
        def acquire(self): return _FakeAcquire()

    import asyncio
    # single-key DEFAULTS so we assert one insert
    monkeypatch.setattr(sd, "DEFAULTS", {"findings_alert_route_watermark": "0.5"})
    monkeypatch.setattr(sd, "METADATA", {})
    asyncio.run(sd.seed_all_defaults(_FakePool()))
    # 'general' must NOT be a hardcoded SQL literal; the resolved category rides as a bind param
    assert "'general'" not in captured["sql"]
    assert "observability" in captured["args"]
```

- [ ] **Step 2: Run it, confirm it fails** — the current SQL has the `'general'` literal.

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py::test_seed_insert_uses_resolved_category -q`
Expected: FAIL (`'general'` in sql).

- [ ] **Step 3: Rewire the main seed INSERT** (`settings_defaults.py` ~line 2246). Add the import at the top of the file (`from services.settings_categories import resolve_category`) and change the INSERT:

```python
            status = await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active, updated_at)
                VALUES
                    ($1, $2, $3,
                     'Auto-seeded by services.settings_defaults (#379)',
                     FALSE, TRUE, NOW())
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                value,
                resolve_category(key),
            )
```

- [ ] **Step 4: Rewire the operator-overlay INSERT** (`apply_operator_overrides`, ~line 2354):

```python
            applied_key = await conn.fetchval(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active, updated_at)
                VALUES ($1, $2, $5, $3, FALSE, TRUE, NOW())
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
                    WHERE app_settings.value = $4
                RETURNING key
                """,
                key,
                operator_value,
                _OPERATOR_OVERLAY_DESC,
                oss_default,
                resolve_category(key),
            )
```

- [ ] **Step 5: Rewire `admin_db.set_setting`** (~line 492). Add `from services.settings_categories import resolve_category` at the top, then before the `async with`:

```python
            value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            if category is None:
                category = resolve_category(key)
```

(The existing `COALESCE($3, 'general')` becomes a harmless no-op since `category` is now never `None`; leave the SQL as-is.)

- [ ] **Step 6: Run the tests to green**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_defaults.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py \
        src/cofounder_agent/services/admin_db.py \
        src/cofounder_agent/tests/unit/services/test_settings_defaults.py
git commit -m "feat(settings): seeder + set_setting defer to resolve_category (no more hardcoded 'general')"
```

---

### Task 3: Boot reconcile — converge existing rows on startup

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (`seed_all_defaults`, after the METADATA block, ~line 2293)
- Modify: `src/cofounder_agent/tests/integration_db/test_settings_defaults_integration.py`

**Interfaces:**

- Consumes: `resolve_category` from Task 1.

- [ ] **Step 1: Write the failing integration_db test** (real DB). Add to `test_settings_defaults_integration.py`:

```python
async def test_boot_reconcile_restamps_wrong_categories(db_pool):
    """seed_all_defaults re-stamps a row whose category != resolve_category(key)."""
    from services.settings_defaults import seed_all_defaults

    async with db_pool.acquire() as conn:
        # Plant a key in the wrong bucket.
        await conn.execute(
            "INSERT INTO app_settings (key, value, category, is_active) "
            "VALUES ('findings_reconcile_probe', 'x', 'general', true) "
            "ON CONFLICT (key) DO UPDATE SET category='general'"
        )

    await seed_all_defaults(db_pool)

    async with db_pool.acquire() as conn:
        cat = await conn.fetchval(
            "SELECT category FROM app_settings WHERE key='findings_reconcile_probe'"
        )
    assert cat == "observability"  # findings_* → observability
    # cleanup
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM app_settings WHERE key='findings_reconcile_probe'")
```

- [ ] **Step 2: Run it, confirm it fails** — no reconcile yet, category stays `general`.

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_settings_defaults_integration.py::test_boot_reconcile_restamps_wrong_categories -q`
Expected: FAIL (`assert 'general' == 'observability'`).

- [ ] **Step 3: Add the reconcile pass** in `seed_all_defaults`, inside the `async with pool.acquire() as conn:` block, after the METADATA `try/except` and before `return inserted`:

```python
        # Category reconcile: converge every row's category onto the resolver's
        # canonical answer. Converges prod's 698-in-'general' pile on first boot
        # and self-heals any future drift (steady-state writes 0 rows). Category
        # is display-only, so overwriting is safe. See settings_categories.py.
        cat_rows = await conn.fetch("SELECT key, category FROM app_settings")
        cat_updates = [
            (resolve_category(r["key"]), r["key"])
            for r in cat_rows
            if r["category"] != resolve_category(r["key"])
        ]
        if cat_updates:
            await conn.executemany(
                "UPDATE app_settings SET category = $1 WHERE key = $2", cat_updates
            )
```

- [ ] **Step 4: Run the test to green**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_settings_defaults_integration.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py \
        src/cofounder_agent/tests/integration_db/test_settings_defaults_integration.py
git commit -m "feat(settings): boot reconcile re-stamps app_settings.category onto the resolver"
```

---

### Task 4: Console reconcile — 13 labels, canonical order, mock re-stamp

**Files:**

- Modify: `src/cofounder_agent/console/js/settings-data.js` (`CATEGORIES` array + mock `S` rows)
- Modify: `src/cofounder_agent/console/js/api.js` (`deriveCategories`, ~line 440)
- Create: `src/cofounder_agent/console/js/__tests__/api.categories.test.js`

**Interfaces:**

- Consumes: the 13 canonical `{id,label}` (mirror of Task 1's `CATEGORIES`, in JS).

- [ ] **Step 1: Write the failing node test**

Create `src/cofounder_agent/console/js/__tests__/api.categories.test.js`:

```js
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Canonical ids (mirror of services/settings_categories.py CATEGORIES).
const CANON = [
  'identity',
  'pipeline',
  'content',
  'quality',
  'models',
  'media',
  'distribution',
  'cost',
  'observability',
  'self_healing',
  'plugins',
  'integrations',
  'infrastructure',
];

function loadSettingsData() {
  const p = path.join(__dirname, '..', 'settings-data.js');
  const code = fs.readFileSync(p, 'utf8');
  const sandbox = { window: {} };
  vm.runInNewContext(code, sandbox);
  return sandbox.window.PX_SETTINGS;
}

test('settings-data CATEGORIES ids are all canonical (drift guard)', () => {
  const px = loadSettingsData();
  const ids = px.categories.map((c) => c.id);
  for (const id of ids)
    assert.ok(CANON.includes(id), `non-canonical id: ${id}`);
});

test('settings-data CATEGORIES are in canonical order', () => {
  const px = loadSettingsData();
  const ids = px.categories.map((c) => c.id);
  const expected = CANON.filter((id) => ids.includes(id));
  assert.deepStrictEqual(ids, expected);
});
```

Run: `npm run test:console`
Expected: FAIL — current `settings-data.js` has 12 non-canonical ids (`model_roles`, `image`, `prometheus`, …).

- [ ] **Step 2: Replace `CATEGORIES` in `settings-data.js`** with the 13 canonical entries in order:

```js
const CATEGORIES = [
  { id: 'identity', label: 'Identity & Site' },
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'content', label: 'Content & Knowledge' },
  { id: 'quality', label: 'Quality / QA' },
  { id: 'models', label: 'Models & LLM' },
  { id: 'media', label: 'Media' },
  { id: 'distribution', label: 'Distribution' },
  { id: 'cost', label: 'Cost & Finance' },
  { id: 'observability', label: 'Observability' },
  { id: 'self_healing', label: 'Brain & Self-Healing' },
  { id: 'plugins', label: 'Plugins' },
  { id: 'integrations', label: 'Integrations' },
  { id: 'infrastructure', label: 'Infrastructure' },
];
```

- [ ] **Step 3: Re-stamp the mock `S` rows' `category` fields** to canonical ids so offline-dev matches live. Map each existing mock row's category to its canonical id (`models`/`model_roles` → `models`, `image` → `media`, `prometheus`/`observability` → `observability`, `cost` → `cost`, `features` → its subsystem or `infrastructure`, etc.). Keep every row pointing at one of the 13 ids.

- [ ] **Step 4: Make `deriveCategories` emit canonical order** (`api.js`, ~line 440). After building `out`, sort by canonical index (known ids first in defined order, unknown appended):

```js
const order = ((window.PX_SETTINGS && window.PX_SETTINGS.categories) || []).map(
  (c) => c.id
);
out.sort((a, b) => {
  const ia = order.indexOf(a.id);
  const ib = order.indexOf(b.id);
  return (ia < 0 ? 1e9 : ia) - (ib < 0 ? 1e9 : ib);
});
return out;
```

- [ ] **Step 5: Run console tests to green**

Run: `npm run test:console`
Expected: PASS (new test + all existing console tests).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/console/js/settings-data.js \
        src/cofounder_agent/console/js/api.js \
        src/cofounder_agent/console/js/__tests__/api.categories.test.js
git commit -m "feat(console): render the 13 canonical settings categories in order"
```

---

### Task 5: Retire the dead `SettingCategoryEnum`; validate create against `CATEGORY_IDS`

**Files:**

- Modify: `src/cofounder_agent/schemas/settings_schemas.py`
- Modify: `src/cofounder_agent/routes/settings_routes.py` (`create_setting`)
- Modify: `src/cofounder_agent/tests/unit/services/test_settings_schemas.py`

**Interfaces:**

- Consumes: `CATEGORY_IDS` from Task 1.

- [ ] **Step 1: Write the failing test** (`test_settings_schemas.py`): a `SettingCreate` with `category="observability"` is accepted, and a bogus `category="nonsense"` is rejected at the route's validation layer.

```python
def test_setting_create_accepts_canonical_category():
    from schemas.settings_schemas import SettingCreate
    s = SettingCreate(key="x_probe", value="1", category="observability")
    assert s.category == "observability"
```

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_schemas.py -q`
Expected: FAIL — `SettingCreate.category` is typed `SettingCategoryEnum` and rejects `"observability"`.

- [ ] **Step 2: Loosen `SettingCreate.category` to a validated string.** In `settings_schemas.py`, change `SettingCreate.category` from `SettingCategoryEnum | None` to `str | None`, and drop `SettingCategoryEnum` (or leave it deprecated-unused). `SettingBase.category` (used only by response models) similarly becomes `str`. Keep `SettingResponse`'s existing free-string override.

- [ ] **Step 3: Validate in `create_setting`** (`settings_routes.py`). Import `CATEGORY_IDS`; when a category is supplied, reject unknown values with 400; when omitted, let the service resolve it:

```python
from services.settings_categories import CATEGORY_IDS
...
    if setting_data.category and setting_data.category not in CATEGORY_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown category '{setting_data.category}'. Valid: {sorted(CATEGORY_IDS)}",
        )
```

Pass `category=setting_data.category` (may be `None`) through to `db_service.set_setting`, which now resolves `None` via the resolver (Task 2).

- [ ] **Step 4: Fix any references** to `SettingCategoryEnum` the removal broke (grep `SettingCategoryEnum` across the tree; update `settings_routes.py` defaulting lines that referenced `SettingCategoryEnum.DATABASE` / `.GENERAL` to plain strings `"general"` or the resolver).

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_settings_schemas.py tests/unit/ -k settings -q`
Expected: PASS.

- [ ] **Step 5: Update the contract snapshot if needed.** If the OpenAPI snapshot test flags a diff for the settings create schema, regenerate it per the repo's snapshot-update procedure and verify the diff is only the category-type loosening.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/schemas/settings_schemas.py \
        src/cofounder_agent/routes/settings_routes.py \
        src/cofounder_agent/tests/unit/services/test_settings_schemas.py
git commit -m "feat(settings): validate create-category against canonical ids; retire dead SettingCategoryEnum"
```

---

## Final verification (after all tasks)

- [ ] **Full backend settings tests:** `cd src/cofounder_agent && poetry run pytest tests/unit/ -k "settings or categor" -q` → all green.
- [ ] **Console tests:** `npm run test:console` → all green.
- [ ] **Lint/format:** `npm run lint:python` and `npm run format` (CSS/JS/MD prettier is a pre-commit hook; run once to be safe).
- [ ] **Mark PR #2320 ready for review** (`gh pr ready 2320`), let CI run, then merge per the CI-green-is-the-gate rule.
- [ ] **Post-merge prod check** (after the worker restart the deploy performs):
      `SELECT category, count(*) FROM app_settings GROUP BY 1 ORDER BY 2 DESC;`
      → ≤ 13 categories + a `general` residual ≤ 20, no duplicates. Open the console App Settings tab and confirm the 13 curated labels render in order with `general` no longer dominating.

## Self-review notes (author)

- **Spec coverage:** resolver (T1) · seeder rewire + set_setting (T2) · boot reconcile (T3) · console labels/order + mock (T4) · enum retirement + create validation (T5) · tests in every task · rollout in Final Verification. All spec sections covered.
- **Type consistency:** `resolve_category(key: str) -> str`, `CATEGORY_IDS: frozenset[str]`, `CATEGORIES: list[dict]` used identically across T1/T2/T3/T5.
- **Task ordering:** T1 is the dependency for T2/T3/T5; T4 is independent (JS) but mirrors T1's ids. Linear execution is safe.
