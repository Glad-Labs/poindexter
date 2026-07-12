# Affiliate Referrals Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a public `/referrals` page listing every active affiliate link with a real name, description, and service/product grouping, sourced from a new DB-driven JSON export, with the operator CLI auto-republishing on every change.

**Architecture:** Two new columns (`description`, `category`) on the existing `affiliate_links` table feed a new `static/affiliate-referrals.json` R2 export (parallel to the existing `affiliate-links.json`, which stays URL-only). The `poindexter affiliate` CLI republishes both exports + busts the `referrals` ISR tag after every add/enable/disable/rm. A new Next.js route renders two `Card`-grid sections (service/product) from that JSON, CTA-linking through the existing `/go/<code>` redirect — no raw merchant URL ever reaches the frontend.

**Tech Stack:** Python 3.11+ / asyncpg / FastAPI backend (`src/cofounder_agent/`), Next.js 16 App Router frontend (`web/public-site/`), Jest + Testing Library for frontend tests, pytest for backend tests.

## Global Constraints

- Real referral URLs are DB-only, never in source — this plan touches only the empty-shipping schema/code; the actual 4 rows are written via CLI commands (Task 7), using Matt's already-verified real URLs (already live in the DB — Task 7 just adds the two new columns to each existing row).
- `category` is a closed two-value field (`service` | `product`) enforced by a DB CHECK constraint — never a free-form tag system.
- The CLI's `--category` and `--description` flags on `affiliate add` are **required**, not defaulted — no silent CLI-layer default (the DB column default exists only as a schema-level safety net for pre-existing rows).
- A republish/revalidate failure must never fail the CLI write that already landed in the DB — always fail-open with a warning, matching the existing revalidation-service philosophy (`services/revalidation_service.py` never raises).
- The Worker's existing `affiliate-links.json` (code→url map) is untouched in shape — the new `affiliate-referrals.json` is a separate R2 object so the redirect hot path's payload never grows because a description got longer.
- Page CTAs always link through `/go/<code>` (relative, same-origin) — never the raw merchant URL.
- This repo's git worktrees have no poetry venv of their own. If `poetry run pytest` fails to resolve inside `src/cofounder_agent` in a fresh worktree, use the main checkout's venv directly, e.g.:
  `& (poetry -C C:\Users\mattm\glad-labs-website\src\cofounder_agent env info --path)\Scripts\python.exe -m pytest <path> -q -o addopts=""`
  (the `-o addopts=""` clears a `--forked` default that only works from the main checkout).
- Follow existing house patterns exactly rather than inventing new ones — every code block in this plan was copied from a real, currently-existing file in this repo, not guessed.

---

### Task 1: Schema — `description` + `category` columns on `affiliate_links`

**Files:**

- Create: `src/cofounder_agent/services/migrations/20260712_140000_add_category_and_description_to_affiliate_links.py`
- Modify (test): `src/cofounder_agent/tests/integration_db/test_affiliate_links_schema.py`

**Interfaces:**

- Produces: `affiliate_links.description` (`text NOT NULL DEFAULT ''`), `affiliate_links.category` (`varchar(20) NOT NULL DEFAULT 'product'`, CHECK `IN ('service','product')`) — Task 2's `add_link()` and Task 3's export query both depend on these two column names existing with these exact types.

- [ ] **Step 1: Write the failing schema tests**

Add these two test functions to the end of `src/cofounder_agent/tests/integration_db/test_affiliate_links_schema.py` (the file already defines `_columns(pool, table)` above — reuse it, no new helper needed):

```python
async def test_affiliate_links_new_columns(test_pool):
    cols = await _columns(test_pool, "affiliate_links")
    assert {"description", "category"} <= cols


async def test_affiliate_links_category_check_constraint(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(Exception):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO affiliate_links (code, keyword, url, category) "
                    "VALUES ('bad-cat-test', 'BadCat', 'https://x', 'bogus')"
                )
```

Note the nesting: `pytest.raises` wraps the `async with conn.transaction():` block, not the other way around. This lets the exception propagate out of the transaction context manager so asyncpg issues a real `ROLLBACK` (a Postgres transaction is aborted server-side after a failed statement — nesting it the other way would let asyncpg attempt a `COMMIT` on an already-aborted transaction).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_affiliate_links_schema.py -q`
Expected: `test_affiliate_links_new_columns` FAILS (columns don't exist yet). `test_affiliate_links_category_check_constraint` FAILS (no CHECK constraint exists yet — the bogus insert succeeds instead of raising). If no live Postgres is reachable, the whole file skips cleanly instead — in that case, skip straight to Step 3 and verify later via `python scripts/ci/migrations_smoke.py` (Step 5).

- [ ] **Step 3: Write the migration**

Create `src/cofounder_agent/services/migrations/20260712_140000_add_category_and_description_to_affiliate_links.py`:

```python
"""Migration 20260712_140000: add category and description to affiliate_links

Adds the two columns the public /referrals page needs to render a name,
description, and service/product grouping from a DB-driven export instead
of a hand-maintained file. Existing rows land on the DEFAULT values
(category='product', description='') — the CLI's `poindexter affiliate add`
requires both flags explicitly for new/updated rows, so these defaults are
a schema-safety net, not the intended steady state.

See docs/superpowers/specs/2026-07-12-affiliate-referrals-page-design.md.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE affiliate_links
              ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '',
              ADD COLUMN IF NOT EXISTS category varchar(20) NOT NULL DEFAULT 'product'
            """
        )
        await conn.execute(
            "ALTER TABLE affiliate_links "
            "DROP CONSTRAINT IF EXISTS affiliate_links_category_check"
        )
        await conn.execute(
            "ALTER TABLE affiliate_links ADD CONSTRAINT affiliate_links_category_check "
            "CHECK (category IN ('service', 'product'))"
        )
    logger.info("Migration add_category_and_description_to_affiliate_links: applied")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE affiliate_links "
            "DROP CONSTRAINT IF EXISTS affiliate_links_category_check"
        )
        await conn.execute(
            "ALTER TABLE affiliate_links DROP COLUMN IF EXISTS category"
        )
        await conn.execute(
            "ALTER TABLE affiliate_links DROP COLUMN IF EXISTS description"
        )
    logger.info("Migration add_category_and_description_to_affiliate_links: reverted")
```

- [ ] **Step 4: Run the migrations smoke test**

Run: `cd src/cofounder_agent && python scripts/ci/migrations_smoke.py`
Expected: applies the full migration tree (including the new file) against a fresh DB with no errors.

- [ ] **Step 5: Run the schema tests again to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/integration_db/test_affiliate_links_schema.py -q`
Expected: PASS (all 5 tests — the 3 pre-existing + the 2 new ones).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/migrations/20260712_140000_add_category_and_description_to_affiliate_links.py src/cofounder_agent/tests/integration_db/test_affiliate_links_schema.py
git commit -m "feat(affiliate): add description + category columns to affiliate_links"
```

---

### Task 2: CRUD service — `add_link()` persists description + category

**Files:**

- Modify: `src/cofounder_agent/modules/content/affiliate_links.py:105-116`
- Create (test): `src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_crud.py`

**Interfaces:**

- Consumes: `affiliate_links.description`/`.category` columns from Task 1.
- Produces: `add_link(pool, *, code, keyword, url, display_text="", program="", description="", category="product") -> None` — Task 4's CLI wiring calls this with the new keyword args.

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_crud.py`:

```python
"""Unit tests for the affiliate-link CRUD helpers (fake pool, no real DB).

Complements test_affiliate_links_service.py (pure matcher tests, no DB at
all) and the integration_db schema guard — these assert the SQL shape +
parameter order add_link/set_active/remove_link send, using a capturing
fake pool double.
"""

from __future__ import annotations

from modules.content.affiliate_links import add_link, remove_link, set_active


class _FakePool:
    def __init__(self, execute_result: str = "UPDATE 1"):
        self.calls: list[tuple] = []
        self._execute_result = execute_result

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self._execute_result


async def test_add_link_persists_description_and_category():
    pool = _FakePool()
    await add_link(
        pool, code="mercury", keyword="Mercury", url="https://mercury.com/r/glad-labs",
        display_text="Mercury", program="Mercury Referral",
        description="Business banking we use daily.", category="service",
    )
    sql, args = pool.calls[0]
    assert "description" in sql
    assert "category" in sql
    assert args == (
        "mercury", "Mercury", "https://mercury.com/r/glad-labs", "Mercury",
        "Mercury Referral", "Business banking we use daily.", "service",
    )


async def test_add_link_defaults_category_to_product():
    pool = _FakePool()
    await add_link(
        pool, code="widget", keyword="Widget", url="https://x",
        description="A thing.",
    )
    _, args = pool.calls[0]
    assert args[-1] == "product"


async def test_set_active_returns_true_on_match():
    pool = _FakePool(execute_result="UPDATE 1")
    assert await set_active(pool, "mercury", False) is True


async def test_set_active_returns_false_when_no_match():
    pool = _FakePool(execute_result="UPDATE 0")
    assert await set_active(pool, "nope", False) is False


async def test_remove_link_returns_true_on_match():
    pool = _FakePool(execute_result="DELETE 1")
    assert await remove_link(pool, "mercury") is True


async def test_remove_link_returns_false_when_no_match():
    pool = _FakePool(execute_result="DELETE 0")
    assert await remove_link(pool, "nope") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/test_affiliate_links_crud.py -q`
Expected: `test_add_link_persists_description_and_category` and `test_add_link_defaults_category_to_product` FAIL with `TypeError: add_link() got an unexpected keyword argument 'description'`. The `set_active`/`remove_link` tests PASS already (those functions are unchanged) — that's fine, they're here as a regression net for Task 4's pool-vs-connection swap, not new behavior.

- [ ] **Step 3: Extend `add_link()`**

In `src/cofounder_agent/modules/content/affiliate_links.py`, replace the existing `add_link` function (lines 105-116):

```python
async def add_link(
    pool: Any, *, code: str, keyword: str, url: str,
    display_text: str = "", program: str = "",
    description: str = "", category: str = "product",
) -> None:
    await pool.execute(
        "INSERT INTO affiliate_links "
        "(code, keyword, url, display_text, program, description, category) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "ON CONFLICT (code) DO UPDATE SET keyword = EXCLUDED.keyword, "
        "url = EXCLUDED.url, display_text = EXCLUDED.display_text, "
        "program = EXCLUDED.program, description = EXCLUDED.description, "
        "category = EXCLUDED.category, updated_at = now()",
        code, keyword, url, display_text or None, program or None,
        description, category,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/test_affiliate_links_crud.py -q`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/affiliate_links.py src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_crud.py
git commit -m "feat(affiliate): add_link persists description + category"
```

---

### Task 3: Static export — `affiliate-referrals.json`

**Files:**

- Modify: `src/cofounder_agent/services/static_export_service.py` (insert after the existing `_export_affiliate_links` function, currently ending at line 256)
- Modify (test): `src/cofounder_agent/tests/unit/services/test_static_export_affiliate.py`

**Interfaces:**

- Consumes: `affiliate_links.description`/`.category` columns from Task 1; existing `_upload_json(key, data, content_type="application/json", *, site_config)` and `_to_json(data)` helpers already in this file.
- Produces: `_export_affiliate_referrals(pool, *, site_config: SiteConfig) -> None` and `republish_affiliate_exports(pool, *, site_config: SiteConfig) -> None` — Task 4's CLI `_republish()` helper calls `republish_affiliate_exports`.

- [ ] **Step 1: Write the failing tests**

Append to `src/cofounder_agent/tests/unit/services/test_static_export_affiliate.py` (the file already defines `_FakePool` above — reuse it):

```python
async def test_export_affiliate_referrals_uploads_display_fields(monkeypatch):
    captured: dict = {}

    async def _fake_upload(key, data, content_type="application/json", *, site_config=None):
        captured["key"] = key
        captured["data"] = data
        return f"https://cdn.example/{key}"

    monkeypatch.setattr(ses, "_upload_json", _fake_upload)

    pool = _FakePool(rows=[
        {"code": "mercury", "name": "Mercury", "description": "Business banking.", "category": "service"},
        {"code": "prime-gaming", "name": "Prime Gaming", "description": "Free games monthly.", "category": "product"},
    ])
    await ses._export_affiliate_referrals(pool, site_config=SiteConfig(initial_config={}))

    assert captured["key"] == "affiliate-referrals.json"
    referrals = json.loads(captured["data"])
    assert referrals == [
        {"code": "mercury", "name": "Mercury", "description": "Business banking.", "category": "service"},
        {"code": "prime-gaming", "name": "Prime Gaming", "description": "Free games monthly.", "category": "product"},
    ]


async def test_export_affiliate_referrals_fails_open_on_db_error(monkeypatch):
    uploads: list = []

    async def _fake_upload(key, data, content_type="application/json", *, site_config=None):
        uploads.append(key)
        return "x"

    monkeypatch.setattr(ses, "_upload_json", _fake_upload)

    pool = _FakePool(raise_exc=RuntimeError("affiliate_links table missing"))
    # Must not raise — the export continues even if the referrals data fails.
    await ses._export_affiliate_referrals(pool, site_config=SiteConfig(initial_config={}))
    assert uploads == []


async def test_republish_affiliate_exports_uploads_both_files(monkeypatch):
    uploaded_keys: list = []

    async def _fake_upload(key, data, content_type="application/json", *, site_config=None):
        uploaded_keys.append(key)
        return f"https://cdn.example/{key}"

    monkeypatch.setattr(ses, "_upload_json", _fake_upload)

    pool = _FakePool(rows=[
        {"code": "mercury", "url": "https://mercury.com/r/glad-labs",
         "name": "Mercury", "description": "Business banking.", "category": "service"},
    ])
    await ses.republish_affiliate_exports(pool, site_config=SiteConfig(initial_config={}))

    assert uploaded_keys == ["affiliate-links.json", "affiliate-referrals.json"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_static_export_affiliate.py -q`
Expected: the 3 new tests FAIL with `AttributeError: module 'services.static_export_service' has no attribute '_export_affiliate_referrals'` (and `republish_affiliate_exports`). The 4 pre-existing tests still PASS.

- [ ] **Step 3: Add the two new functions**

In `src/cofounder_agent/services/static_export_service.py`, insert immediately after the existing `_export_affiliate_links` function (after line 256, before `_build_json_feed`):

```python
async def _export_affiliate_referrals(pool, *, site_config: SiteConfig) -> None:
    """Upload static/affiliate-referrals.json — display fields for the /referrals page.

    Deliberately excludes the real merchant ``url`` — the page always links
    through ``/go/<code>``, so raw URLs never need to reach the frontend for
    this purpose. A separate R2 object from affiliate-links.json, so the
    Worker's redirect hot path never grows because a description got longer.
    """
    try:
        rows = await pool.fetch(
            "SELECT code, COALESCE(NULLIF(display_text, ''), keyword) AS name, "
            "description, category FROM affiliate_links "
            "WHERE is_active = true ORDER BY id"
        )
    except Exception as e:  # noqa: BLE001 — never fail the export on the referrals page data
        logger.warning("[STATIC_EXPORT] affiliate-referrals fetch failed: %s", e)
        return
    referrals = [
        {
            "code": r["code"],
            "name": r["name"],
            "description": r["description"],
            "category": r["category"],
        }
        for r in rows
    ]
    await _upload_json(
        "affiliate-referrals.json", _to_json(referrals), site_config=site_config
    )


async def republish_affiliate_exports(pool, *, site_config: SiteConfig) -> None:
    """Republish both affiliate JSON exports.

    Called by the ``poindexter affiliate`` CLI after add/enable/disable/rm so
    a DB change reaches R2 immediately, without paying for a full site
    rebuild (``export_full_rebuild``) just to refresh two small objects.
    """
    await _export_affiliate_links(pool, site_config=site_config)
    await _export_affiliate_referrals(pool, site_config=site_config)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_static_export_affiliate.py -q`
Expected: PASS (all 7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/static_export_service.py src/cofounder_agent/tests/unit/services/test_static_export_affiliate.py
git commit -m "feat(affiliate): export affiliate-referrals.json + republish helper"
```

---

### Task 4: CLI — required flags + auto-republish

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/affiliate.py` (full file rewrite below)
- Modify (test): `src/cofounder_agent/tests/unit/poindexter/cli/test_affiliate_cli.py`

**Interfaces:**

- Consumes: `add_link(..., description=, category=)` from Task 2; `republish_affiliate_exports(pool, *, site_config)` from Task 3; `services.site_config.SiteConfig(pool=...)` + `.load(pool)` (async, existing); `services.revalidation_service.trigger_nextjs_revalidation(paths=None, tags=None, *, site_config) -> bool` (existing).
- Produces: `add_cmd` gains required `--category {service,product}` and `--description TEXT` options; `add`/`enable`/`disable`/`rm` all republish after a successful DB write.

- [ ] **Step 1: Write the failing test**

Append to `src/cofounder_agent/tests/unit/poindexter/cli/test_affiliate_cli.py`:

```python
def test_add_requires_category_and_description():
    from poindexter.cli.affiliate import add_cmd

    param_names = {p.name for p in add_cmd.params}
    assert {"category", "description"} <= param_names

    category_param = next(p for p in add_cmd.params if p.name == "category")
    assert category_param.required is True
    assert set(category_param.type.choices) == {"service", "product"}

    description_param = next(p for p in add_cmd.params if p.name == "description")
    assert description_param.required is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/poindexter/cli/test_affiliate_cli.py -q`
Expected: FAIL — `category`/`description` are not (yet) in `add_cmd.params`.

- [ ] **Step 3: Rewrite the CLI module**

Replace the full contents of `src/cofounder_agent/poindexter/cli/affiliate.py`:

```python
"""`poindexter affiliate` — manage affiliate-link rows (DB-only real URLs).

Thin adapter over ``modules.content.affiliate_links`` (no inline SQL here, per
the transport-adapter-contract ADR). Real referral URLs never live in source;
operators add them here.
"""
from __future__ import annotations

import asyncio

import click


async def _connect():
    import asyncpg
    from brain import bootstrap

    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")
    return await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=8)


async def _republish(pool) -> None:
    """Republish both affiliate JSON exports + bust the referrals ISR tag.

    Best-effort: a republish failure must not fail the CLI command that
    already wrote the DB row (the row is the source of truth; a stale
    export self-heals on the next publish or on-demand rebuild).
    """
    from services.revalidation_service import trigger_nextjs_revalidation
    from services.site_config import SiteConfig
    from services.static_export_service import republish_affiliate_exports

    try:
        site_config = SiteConfig(pool=pool)
        try:
            await site_config.load(pool)
        except Exception:
            pass
        await republish_affiliate_exports(pool, site_config=site_config)
        await trigger_nextjs_revalidation(tags=["referrals"], site_config=site_config)
    except Exception as e:  # noqa: BLE001 — never fail the CLI write over a republish hiccup
        click.secho(f"  (warning: republish failed: {e})", fg="yellow")


@click.group(name="affiliate")
def affiliate_group() -> None:
    """Manage affiliate links (add/list/enable/disable/rm)."""


@affiliate_group.command(name="add")
@click.option("--code", required=True, help="Stable slug for /go/<code> (e.g. mercury).")
@click.option("--keyword", required=True, help="Body keyword to match (e.g. Mercury).")
@click.option("--url", required=True, help="Real merchant/referral URL.")
@click.option("--display-text", default="", help="Link text (defaults to keyword).")
@click.option("--program", default="", help="Program label (e.g. 'Mercury Referral').")
@click.option(
    "--category",
    required=True,
    type=click.Choice(["service", "product"]),
    help="Section this link appears in on /referrals.",
)
@click.option(
    "--description",
    required=True,
    help="Reader-facing description shown on /referrals.",
)
def add_cmd(code, keyword, url, display_text, program, category, description):
    """Add or update an affiliate link."""
    from modules.content.affiliate_links import add_link

    async def _go():
        pool = await _connect()
        try:
            await add_link(
                pool, code=code, keyword=keyword, url=url,
                display_text=display_text, program=program,
                description=description, category=category,
            )
            await _republish(pool)
        finally:
            await pool.close()

    asyncio.run(_go())
    click.secho(f"Added/updated affiliate link '{code}'.", fg="green")


@affiliate_group.command(name="list")
def list_cmd():
    """List active affiliate links."""
    from modules.content.affiliate_links import list_active

    async def _go():
        pool = await _connect()
        try:
            return await list_active(pool)
        finally:
            await pool.close()

    links = asyncio.run(_go())
    if not links:
        click.echo("No active affiliate links.")
        return
    for lk in links:
        click.echo(f"  {lk.code:16} {lk.keyword:16} → {lk.url}")


@affiliate_group.command(name="enable")
@click.argument("code")
def enable_cmd(code):
    """Enable (activate) an affiliate link."""
    _set_active(code, True)


@affiliate_group.command(name="disable")
@click.argument("code")
def disable_cmd(code):
    """Disable (deactivate) an affiliate link."""
    _set_active(code, False)


def _set_active(code, active):
    from modules.content.affiliate_links import set_active

    async def _go():
        pool = await _connect()
        try:
            ok = await set_active(pool, code, active)
            if ok:
                await _republish(pool)
            return ok
        finally:
            await pool.close()

    ok = asyncio.run(_go())
    if not ok:
        raise click.ClickException(f"No affiliate link with code '{code}'.")
    click.secho(f"{'Enabled' if active else 'Disabled'} '{code}'.", fg="green")


@affiliate_group.command(name="rm")
@click.argument("code")
def rm_cmd(code):
    """Remove an affiliate link."""
    from modules.content.affiliate_links import remove_link

    async def _go():
        pool = await _connect()
        try:
            ok = await remove_link(pool, code)
            if ok:
                await _republish(pool)
            return ok
        finally:
            await pool.close()

    ok = asyncio.run(_go())
    if not ok:
        raise click.ClickException(f"No affiliate link with code '{code}'.")
    click.secho(f"Removed '{code}'.", fg="green")


__all__ = ["affiliate_group"]
```

Note what changed vs. the current file: `_connect()` now returns an `asyncpg.Pool` (via `create_pool`) instead of a single `asyncpg.Connection` — every existing call site already does `pool = await _connect(); ...; await pool.close()`, and `Pool.close()` exists just like `Connection.close()`, so this is a drop-in swap. The CRUD functions in `affiliate_links.py` already type-hint their first arg as `pool: Any` and only call `.fetch()`/`.execute()` (both of which `Pool` exposes directly as convenience methods), so no other file needs to change for this swap. The Pool (not a bare Connection) is required because `_republish()`'s `SiteConfig.get_secret()` internally calls `self._pool.acquire()`, which only exists on a real `Pool`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/poindexter/cli/test_affiliate_cli.py -q`
Expected: PASS (all 3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/affiliate.py src/cofounder_agent/tests/unit/poindexter/cli/test_affiliate_cli.py
git commit -m "feat(affiliate): CLI requires category/description + auto-republishes"
```

---

### Task 5: Frontend — `/referrals` page

**Files:**

- Create: `web/public-site/app/referrals/page.js`
- Create (test): `web/public-site/app/referrals/__tests__/page.test.js`

**Interfaces:**

- Consumes: `static/affiliate-referrals.json` (array of `{code, name, description, category}`, from Task 3); `@glad-labs/brand`'s `Button`, `Card` (+ `.Title`, `.Body`, `.Meta`), `Display` (+ `.Accent`), `Eyebrow`; `components/AffiliateDisclosure.tsx`'s `AffiliateDisclosure` (named export, no required props); `lib/site.config`'s `SITE_NAME`/`SITE_URL`.
- Produces: the `/referrals` route, consumed by Task 6's nav links.

- [ ] **Step 1: Write the failing test**

Create `web/public-site/app/referrals/__tests__/page.test.js`:

```javascript
import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('@sentry/nextjs', () => ({
  captureException: jest.fn(),
}));

const mockReferrals = [
  {
    code: 'mercury',
    name: 'Mercury',
    description: 'Business banking we use daily.',
    category: 'service',
  },
  {
    code: 'prime-gaming',
    name: 'Prime Gaming',
    description: 'Free games every month with Prime.',
    category: 'product',
  },
];

beforeEach(() => {
  global.fetch = jest.fn(() =>
    Promise.resolve({
      ok: true,
      json: () => Promise.resolve(mockReferrals),
    })
  );
});

afterEach(() => {
  jest.restoreAllMocks();
});

let ReferralsPage;
beforeAll(async () => {
  const mod = await import('../page');
  ReferralsPage = mod.default;
});

describe('Referrals Page (/referrals)', () => {
  const renderPage = async () => {
    const jsx = await ReferralsPage();
    return render(jsx);
  };

  test('renders page heading', async () => {
    await renderPage();
    const headings = screen.getAllByText(/referrals/i);
    expect(headings.length).toBeGreaterThan(0);
  });

  test('renders service and product section headings', async () => {
    await renderPage();
    expect(screen.getByText('Tools & Services')).toBeInTheDocument();
    expect(screen.getByText('Amazon Picks')).toBeInTheDocument();
  });

  test('renders each referral name and description', async () => {
    await renderPage();
    expect(screen.getByText('Mercury')).toBeInTheDocument();
    expect(
      screen.getByText('Business banking we use daily.')
    ).toBeInTheDocument();
    expect(screen.getByText('Prime Gaming')).toBeInTheDocument();
  });

  test('CTA links point at /go/<code>', async () => {
    await renderPage();
    const ctaLinks = screen
      .getAllByRole('link')
      .filter((a) => a.getAttribute('href')?.startsWith('/go/'));
    expect(ctaLinks.length).toBe(2);
    expect(ctaLinks.some((a) => a.getAttribute('href') === '/go/mercury')).toBe(
      true
    );
  });

  test('renders the affiliate disclosure', async () => {
    await renderPage();
    expect(
      screen.getByRole('note', { name: 'Affiliate disclosure' })
    ).toBeInTheDocument();
  });

  test('renders empty state when no referrals', async () => {
    global.fetch = jest.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve([]) })
    );
    await renderPage();
    expect(screen.getByText(/nothing published here yet/i)).toBeInTheDocument();
  });

  test('handles fetch failure gracefully', async () => {
    global.fetch = jest.fn(() => Promise.reject(new Error('Network error')));
    await renderPage();
    expect(screen.getByText(/nothing published here yet/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web/public-site && npx jest app/referrals/__tests__/page.test.js`
Expected: FAIL — `Cannot find module '../page'` (the page doesn't exist yet).

- [ ] **Step 3: Write the page**

Create `web/public-site/app/referrals/page.js`:

```jsx
import * as Sentry from '@sentry/nextjs';
import { Button, Card, Display, Eyebrow } from '@glad-labs/brand';
import { AffiliateDisclosure } from '../../components/AffiliateDisclosure';
import { SITE_NAME, SITE_URL } from '@/lib/site.config';

// Time-based ISR backstop (1h) — see app/page.js and app/archive/[page]/page.tsx.
// On-demand revalidateTag('referrals') from the poindexter CLI is primary;
// this floor self-heals if a CLI republish is ever skipped.
export const revalidate = 3600;

export const metadata = {
  title: `Referrals — ${SITE_NAME}`,
  description: `Tools, services, and products ${SITE_NAME} actually uses day to day — with full affiliate disclosure.`,
  alternates: {
    canonical: `${SITE_URL}/referrals`,
  },
};

const STATIC_URL =
  process.env.NEXT_PUBLIC_STATIC_URL ||
  'https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static';

async function fetchReferralsIndex() {
  const response = await fetch(`${STATIC_URL}/affiliate-referrals.json`, {
    // Tag-based cache: invalidated by revalidateTag('referrals') from the
    // poindexter CLI's add/enable/disable/rm commands.
    next: { tags: ['referrals'] },
  });

  if (response.status === 404) {
    // Not published yet — treat as empty, not an error.
    return [];
  }

  if (!response.ok) {
    const err = new Error(
      `fetchReferralsIndex: R2 returned ${response.status} ${response.statusText}`
    );
    Sentry.captureException(err);
    throw err;
  }

  return response.json();
}

async function getReferrals() {
  try {
    return await fetchReferralsIndex();
  } catch {
    return [];
  }
}

function ReferralCard({ referral, accent }) {
  return (
    <Card accent={accent}>
      <Card.Title>{referral.name}</Card.Title>
      <Card.Body>{referral.description}</Card.Body>
      <div className="mt-4">
        <Button
          as="a"
          href={`/go/${referral.code}`}
          target="_blank"
          rel="sponsored noopener"
          variant="secondary"
        >
          Check it out →
        </Button>
      </div>
    </Card>
  );
}

export default async function ReferralsPage() {
  const referrals = await getReferrals();
  const services = referrals.filter((r) => r.category === 'service');
  const products = referrals.filter((r) => r.category === 'product');

  return (
    <div className="gl-atmosphere min-h-screen">
      {/* Hero */}
      <section className="relative pt-20 pb-12 md:pt-32 md:pb-16 px-4 sm:px-6 lg:px-8">
        <div className="container mx-auto max-w-3xl">
          <Eyebrow>GLAD LABS · REFERRALS</Eyebrow>
          <Display xl>
            Tools we use. <Display.Accent>Gear we recommend.</Display.Accent>
          </Display>
          <p className="gl-body gl-body--lg mt-6">
            Everything below is something Glad Labs actually uses day to day —
            business tools that keep the pipeline running, and products worth
            recommending to readers.
          </p>
        </div>
      </section>

      {/* Disclosure — unconditional, this whole page is affiliate content */}
      <section className="px-4 sm:px-6 lg:px-8 pb-4">
        <div className="container mx-auto max-w-3xl">
          <AffiliateDisclosure />
        </div>
      </section>

      {referrals.length === 0 ? (
        <section className="px-4 sm:px-6 lg:px-8 pb-20">
          <div className="container mx-auto max-w-3xl text-center">
            <Card accent="amber" className="text-center py-12">
              <Card.Meta>NOTHING HERE YET</Card.Meta>
              <h2 className="gl-h2 mt-2">Nothing published here yet.</h2>
              <p className="gl-body mt-3 max-w-md mx-auto">
                Check back soon — this page fills in as links get added.
              </p>
            </Card>
          </div>
        </section>
      ) : (
        <>
          {services.length > 0 && (
            <section className="px-4 sm:px-6 lg:px-8 pb-12">
              <div className="container mx-auto max-w-3xl">
                <h2 className="gl-h2 mb-6">Tools &amp; Services</h2>
                <div className="grid sm:grid-cols-2 gap-6">
                  {services.map((r) => (
                    <ReferralCard key={r.code} referral={r} accent="cyan" />
                  ))}
                </div>
              </div>
            </section>
          )}

          {products.length > 0 && (
            <section className="px-4 sm:px-6 lg:px-8 pb-20">
              <div className="container mx-auto max-w-3xl">
                <h2 className="gl-h2 mb-6">Amazon Picks</h2>
                <div className="grid sm:grid-cols-2 gap-6">
                  {products.map((r) => (
                    <ReferralCard key={r.code} referral={r} accent="amber" />
                  ))}
                </div>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd web/public-site && npx jest app/referrals/__tests__/page.test.js`
Expected: PASS (all 7 tests).

- [ ] **Step 5: Commit**

```bash
git add web/public-site/app/referrals/page.js web/public-site/app/referrals/__tests__/page.test.js
git commit -m "feat(affiliate): add public /referrals page"
```

---

### Task 6: Nav + footer links

**Files:**

- Modify: `web/public-site/components/TopNav.js` (desktop nav block ~line 140, mobile nav block ~line 286)
- Modify: `web/public-site/components/Footer.js` (Explore nav block ~line 80)
- Modify (tests): `web/public-site/components/__tests__/TopNav.test.js`, `web/public-site/components/__tests__/Footer.test.js`

**Interfaces:**

- Consumes: the `/referrals` route from Task 5.
- Produces: nothing consumed by later tasks — this is the final wiring task.

- [ ] **Step 1: Write the failing tests**

Append to `web/public-site/components/__tests__/TopNav.test.js` (inside the first `describe('TopNavigation', ...)` block, alongside the other `it('renders ... link', ...)` tests):

```javascript
it('renders Referrals link', () => {
  render(<TopNavigation />);
  const referralsLinks = screen.getAllByRole('link', { name: 'Referrals' });
  expect(referralsLinks.length).toBeGreaterThan(0);
  referralsLinks.forEach((link) =>
    expect(link).toHaveAttribute('href', '/referrals')
  );
});
```

Append to `web/public-site/components/__tests__/Footer.test.js` (inside the first `describe('Footer Component', ...)` block):

```javascript
it('should display Referrals link', () => {
  render(<Footer />);
  const referralsLink = screen.getByRole('link', { name: /referrals/i });
  expect(referralsLink).toBeInTheDocument();
  expect(referralsLink).toHaveAttribute('href', '/referrals');
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web/public-site && npx jest components/__tests__/TopNav.test.js components/__tests__/Footer.test.js`
Expected: both new tests FAIL (`Unable to find a link with the name Referrals`).

- [ ] **Step 3: Add the nav links**

In `web/public-site/components/TopNav.js`, in the desktop nav block, replace:

```jsx
            <Link href="/about" className="gl-focus-ring gl-nav-link">
              About
            </Link>
            <a
              href="https://www.gladlabs.ai"
```

with:

```jsx
            <Link href="/about" className="gl-focus-ring gl-nav-link">
              About
            </Link>
            <Link href="/referrals" className="gl-focus-ring gl-nav-link">
              Referrals
            </Link>
            <a
              href="https://www.gladlabs.ai"
```

In the same file's mobile nav panel, replace:

```jsx
                <Link
                  href="/about"
                  className="gl-focus-ring gl-nav-link py-3"
                  style={{ borderBottom: '1px solid var(--gl-hairline)' }}
                >
                  About
                </Link>
                <a
                  href="https://www.gladlabs.ai"
```

with:

```jsx
                <Link
                  href="/about"
                  className="gl-focus-ring gl-nav-link py-3"
                  style={{ borderBottom: '1px solid var(--gl-hairline)' }}
                >
                  About
                </Link>
                <Link
                  href="/referrals"
                  className="gl-focus-ring gl-nav-link py-3"
                  style={{ borderBottom: '1px solid var(--gl-hairline)' }}
                >
                  Referrals
                </Link>
                <a
                  href="https://www.gladlabs.ai"
```

In `web/public-site/components/Footer.js`, in the Explore nav block, replace:

```jsx
              <Link href="/about" className={FOOTER_LINK_CLASS}>
                About
              </Link>
            </nav>
          </div>

          {/* Legal */}
```

with:

```jsx
              <Link href="/about" className={FOOTER_LINK_CLASS}>
                About
              </Link>
              <Link href="/referrals" className={FOOTER_LINK_CLASS}>
                Referrals
              </Link>
            </nav>
          </div>

          {/* Legal */}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web/public-site && npx jest components/__tests__/TopNav.test.js components/__tests__/Footer.test.js`
Expected: PASS (all tests in both files).

- [ ] **Step 5: Commit**

```bash
git add web/public-site/components/TopNav.js web/public-site/components/Footer.js web/public-site/components/__tests__/TopNav.test.js web/public-site/components/__tests__/Footer.test.js
git commit -m "feat(affiliate): link /referrals from top nav + footer"
```

---

### Task 7: Backfill description + category for the 4 existing rows

This is a data task, not a code task — no new test cycle. The 4 rows already exist and are live (seeded via earlier CLI calls); this re-runs the now-extended `add_link` upsert (Task 2 + Task 4) to fill in the two new columns using the exact same real, already-verified URLs already in the DB (see `docs/superpowers/specs/2026-07-12-affiliate-referrals-page-design.md` §4.1).

The descriptions below are a **draft** — get a quick confirm/edit from Matt before or after running these, per the spec's explicit instruction that the copy itself is his call.

- [ ] **Step 1: Run the backfill commands**

```bash
poindexter affiliate add --code mercury --keyword Mercury \
  --url "https://mercury.com/r/glad-labs" \
  --display-text "Mercury" --program "Mercury Referral" \
  --category service \
  --description "The business bank account Glad Labs actually runs on - API-first, no monthly fees, built for founders who'd rather automate banking than deal with a branch."

poindexter affiliate add --code google-workspace --keyword "Google Workspace" \
  --url "https://referworkspace.app.goo.gl/uUQH" \
  --display-text "Google Workspace" --program "Google Workspace Referral" \
  --category service \
  --description "Business email, docs, and storage under the Glad Labs domain - the Workspace plan every professional email and doc on this site runs through."

poindexter affiliate add --code prime-gaming --keyword "Prime Gaming" \
  --url "https://www.amazon.com/b?node=211738315011&linkCode=ll2&tag=gladlabsllc-20&linkId=3b5390a1bd8b3785ef770f9b475e9aa4&language=en_US&ref_=as_li_ss_tl" \
  --display-text "Prime Gaming" --program "Amazon Bounty - Prime Gaming" \
  --category product \
  --description "Free games and in-game loot every month, included with Prime - an easy one if you're already a Prime member."

poindexter affiliate add --code audible --keyword Audible \
  --url "https://www.amazon.com/hz/audible/arya/mlp?_encoding=UTF8&purchaseType=STANDARD_MTRIAL&SHOPPING_PORTAL_MODE=AUGMENTED&linkCode=ll2&tag=gladlabsllc-20&linkId=458cd1d65f29b3f3617c73c8b36fb241&language=en_US&ref_=as_li_ss_tl" \
  --display-text "Audible" --program "Amazon Bounty - Audible Standard Free Trial" \
  --category product \
  --description "Amazon's audiobook service - a free trial if you want to hear a book instead of reading it."
```

Each command's `_republish()` call will upload both JSON exports and revalidate the `referrals` tag automatically (Task 4) — no separate rebuild step needed.

- [ ] **Step 2: Verify the exports landed on R2**

```bash
curl -s https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev/static/affiliate-referrals.json
```

Expected: a JSON array of 4 objects, each with `code`, `name`, `description`, `category` — `mercury` and `google-workspace` with `"category": "service"`, `prime-gaming` and `audible` with `"category": "product"`. No `url` field present (deliberately excluded).

- [ ] **Step 3: Verify the live page**

Visit `https://www.gladlabs.io/referrals` and confirm all 4 items render under the correct section, each CTA button resolves through `/go/<code>` to the correct merchant URL.

- [ ] **Step 4: Confirm copy with Matt**

Ask Matt to review the 4 description strings above (already drafted, not placeholders) and edit/replace any he doesn't like via the same `affiliate add` command (idempotent upsert) — the CLI's auto-republish means an edit shows up live within the on-demand revalidate window, no manual rebuild needed.

---

## Plan self-review notes

- **Spec coverage:** every §2 decision (two sections, `is_active` reuse, no images, DB-driven export, `/go/<code>` click tracking, insertion-order sort) is implemented as specified. §4.1 schema, §4.2 CLI, §4.3 export, §4.4 frontend are each their own task. §6 rollout order (migration → export/CLI → frontend → verify) matches Task 1 → 2/3/4 → 5/6 → 7 ordering.
- **Placeholder scan:** no TBD/TODO in any code block; Task 7's descriptions are real drafted copy, explicitly flagged for confirmation rather than left as `"TODO: description"`.
- **Type consistency:** `add_link(..., description="", category="product")` (Task 2) matches the CLI's call in Task 4 exactly (`description=description, category=category`), matches the export's read shape in Task 3 (`description`, `category` columns), matches the frontend's expected JSON shape in Task 5 (`{code, name, description, category}`). `republish_affiliate_exports(pool, *, site_config)` (Task 3) matches the CLI's `_republish()` call in Task 4 exactly.
