# Affiliate-link injection + `/go` click tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the removed affiliate-link feature as a curated, keyword-matched injection atom in the `canonical_blog` pipeline, with a `/go/<code>` Cloudflare-Worker redirect that tracks clicks and a conditional FTC disclosure banner.

**Architecture:** A deterministic content atom rewrites the first prose mention of each seeded keyword into a `[text](/go/<code>)` markdown link (first-mention, per-post cap, skips code/headings/existing links), placed in the writer block before the QA rails so links are vetted. A standalone Cloudflare Worker resolves `code→url` from a static `affiliate-links.json` on R2, 302-redirects, and writes the click to Analytics Engine; a 5-minute sync job pulls those into `affiliate_link_clicks`. The frontend renders a disclosure banner when the exported post JSON carries `has_affiliate_links`.

**Tech Stack:** Python 3 / asyncpg / FastAPI / LangGraph atoms, Click (CLI), Cloudflare Workers (TypeScript + vitest), Next.js 16 (TSX + Jest), Grafana.

## Global Constraints

- **Real referral URLs are DB-only.** Never commit a real referral URL/code to source (`settings_defaults.py`, `baseline.seeds.sql`, this repo). Tables ship empty; rows are added via the `poindexter affiliate` CLI at rollout. (Origin: the March-2026 fabricated-code leak; `.shared-context/decisions/DECISION-LOG.md`.)
- **Config in `app_settings`, not code.** Every tunable is a DB setting with a default in `settings_defaults.py`. `''` is the unset sentinel — never seed a settings row as NULL.
- **New `app_settings` keys go in `settings_defaults.py`**, never in a migration file. Migration files are DDL + non-settings data only.
- **New migrations:** `python scripts/new-migration.py "<slug>"` → `YYYYMMDD_HHMMSS_<slug>.py`, `async def up(pool)` / `down(pool)`, idempotent (`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`).
- **Atoms auto-register** by living in `modules/content/atoms/*.py` (not `_`-prefixed) with a module-level `ATOM_META: AtomMeta` and a callable `async def run(state)`. No registry edit needed.
- **Config reads must never break the pipeline** — wrap `site_config` reads in try/except and fall back to defaults (fail-open), matching `internal_link_coherence` / `content.reconcile_citations`.
- **Test invocation (fresh worktree has no venv):** run pytest via the **main checkout's** poetry venv python with `-o addopts=""` (skips `--forked`), e.g. `python -m pytest <path> -o addopts="" -q`. In the main checkout, `cd src/cofounder_agent && poetry run pytest <path> -q` works normally.
- **All changes via PR; linear history (squash/rebase).** Commit frequently per task.

---

## File Structure

**Created:**

- `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_add_affiliate_links_tables.py` — DDL for `affiliate_links` + `affiliate_link_clicks`.
- `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_reseed_canonical_blog_affiliate_node.py` — re-seed `pipeline_templates.graph_def` for `canonical_blog`.
- `src/cofounder_agent/modules/content/affiliate_links.py` — service: the pure matcher + DB CRUD + click rollup.
- `src/cofounder_agent/modules/content/atoms/content_inject_affiliate_links.py` — the injection atom.
- `src/cofounder_agent/poindexter/cli/affiliate.py` — `poindexter affiliate` CLI group.
- `src/cofounder_agent/services/jobs/sync_affiliate_clicks.py` — AE → `affiliate_link_clicks` sync job.
- `infrastructure/cloudflare/affiliate-redirect/{src/index.ts,src/index.test.ts,wrangler.toml,package.json,tsconfig.json,README.md}` — the `/go` Worker.
- `web/public-site/components/AffiliateDisclosure.tsx` — disclosure banner component.
- `docs/operations/affiliate-links.md` — operator runbook.
- Test files under `src/cofounder_agent/tests/unit/...` mirroring each module.

**Modified:**

- `src/cofounder_agent/services/settings_defaults.py` — new `affiliate_*` + `sync_affiliate_clicks` defaults.
- `src/cofounder_agent/services/canonical_blog_spec.py` — new node + edges.
- `src/cofounder_agent/services/static_export_service.py` — `has_affiliate_links` + disclosure text on post JSON, `affiliate-links.json` export.
- `src/cofounder_agent/poindexter/cli/app.py` — register the `affiliate` group.
- `web/public-site/lib/posts.ts` — `Post` interface gains `has_affiliate_links` + `affiliate_disclosure_text`.
- `web/public-site/app/posts/[slug]/page.tsx` — render the banner.
- A Grafana dashboard JSON under `infrastructure/grafana/dashboards/` (Cost & Analytics) — click panels.

---

## Task 1: Schema — `affiliate_links` + `affiliate_link_clicks` tables

**Files:**

- Create: `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_add_affiliate_links_tables.py`
- Test: `src/cofounder_agent/tests/integration_db/test_affiliate_links_schema.py`

**Interfaces:**

- Produces: tables `affiliate_links(id, code UNIQUE, keyword UNIQUE, url, display_text, program, is_active, clicks, created_at, updated_at)` and `affiliate_link_clicks(id, code, post_slug, referrer, country, user_agent, created_at)`.

- [ ] **Step 1: Generate the migration file**

Run: `python scripts/new-migration.py "add affiliate links tables"`
This creates `src/cofounder_agent/services/migrations/<timestamp>_add_affiliate_links_tables.py` with the `up(pool)`/`down(pool)` scaffold.

- [ ] **Step 2: Write `up()` and `down()`**

Replace the scaffold body with:

```python
async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS affiliate_links (
                id            serial PRIMARY KEY,
                code          varchar(64)  NOT NULL UNIQUE,
                keyword       varchar(100) NOT NULL UNIQUE,
                url           text         NOT NULL,
                display_text  varchar(200),
                program       varchar(100),
                is_active     boolean      NOT NULL DEFAULT true,
                clicks        integer      NOT NULL DEFAULT 0,
                created_at    timestamptz  NOT NULL DEFAULT now(),
                updated_at    timestamptz  NOT NULL DEFAULT now()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_affiliate_links_active "
            "ON affiliate_links (is_active)"
        )
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS affiliate_link_clicks (
                id          bigserial PRIMARY KEY,
                code        varchar(64) NOT NULL,
                post_slug   text,
                referrer    text,
                country     varchar(8),
                user_agent  text,
                created_at  timestamptz NOT NULL DEFAULT now()
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_affiliate_link_clicks_code "
            "ON affiliate_link_clicks (code)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_affiliate_link_clicks_created "
            "ON affiliate_link_clicks (created_at)"
        )
    logger.info("add_affiliate_links_tables up: affiliate_links + affiliate_link_clicks ready")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS affiliate_link_clicks")
        await conn.execute("DROP TABLE IF EXISTS affiliate_links")
```

Update the docstring `ISSUE:` line and the "Background" paragraph to describe the affiliate rebuild.

- [ ] **Step 3: Write the failing test**

```python
# tests/integration_db/test_affiliate_links_schema.py
import pytest

pytestmark = pytest.mark.integration_db


async def test_affiliate_tables_exist(schema_loaded_pool):
    async with schema_loaded_pool.acquire() as conn:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'affiliate_links'"
        )
        names = {r["column_name"] for r in cols}
    assert {"code", "keyword", "url", "is_active", "clicks"} <= names
```

Use the existing `integration_db` pool/schema fixture (see a sibling test in `tests/integration_db/` for the exact fixture name; `schema_loaded_pool` is a placeholder for whatever that conftest exposes).

- [ ] **Step 4: Run the migration smoke test**

Run: `python scripts/ci/migrations_smoke.py`
Expected: applies cleanly against a fresh DB, no errors; the two tables get created.

- [ ] **Step 5: Lint + commit**

```bash
python scripts/ci/migrations_lint.py
git add src/cofounder_agent/services/migrations/*_add_affiliate_links_tables.py src/cofounder_agent/tests/integration_db/test_affiliate_links_schema.py
git commit -m "feat(affiliate): schema for affiliate_links + affiliate_link_clicks"
```

---

## Task 2: Config defaults

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py` (add to the `DEFAULTS` dict)
- Test: `src/cofounder_agent/tests/unit/services/test_affiliate_settings_defaults.py`

**Interfaces:**

- Produces settings: `affiliate_injection_enabled` (`'false'`), `affiliate_max_links_per_post` (`'3'`), `affiliate_redirect_base_url` (`'/go'`), `affiliate_disclosure_text` (default sentence), `plugin.job.sync_affiliate_clicks.enabled` (`'true'`), `plugin.job.sync_affiliate_clicks.interval_seconds` (`'300'`).

- [ ] **Step 1: Add the defaults**

In `DEFAULTS`, next to the other content/job settings, add:

```python
    # --- Affiliate-link injection (rebuild 2026-07-11) -----------------------
    # Dark-launched: the atom no-ops until this is flipped true. Real referral
    # rows are added out-of-band via `poindexter affiliate add` (DB-only).
    'affiliate_injection_enabled': 'false',
    # Max affiliate links injected per post (first-mention only, one per keyword).
    'affiliate_max_links_per_post': '3',
    # URL prefix the injected link points at: `{base}/{code}`. Default is the
    # relative `/go` path (a CF Worker route on the site origin); set to an
    # absolute origin (e.g. https://go.gladlabs.io) if hosting on a subdomain.
    'affiliate_redirect_base_url': '/go',
    # FTC disclosure banner copy (rendered near the top of any post that
    # contains an affiliate link). Empty string → frontend uses its built-in
    # default.
    'affiliate_disclosure_text': (
        'Some links in this article are affiliate links — if you sign up '
        'through them, Glad Labs may earn a commission at no extra cost to you.'
    ),
    # Click-ingest job: pull the affiliate-click Analytics Engine dataset into
    # affiliate_link_clicks every 5 minutes (mirrors sync_cloudflare_analytics).
    'plugin.job.sync_affiliate_clicks.enabled': 'true',
    'plugin.job.sync_affiliate_clicks.interval_seconds': '300',
```

- [ ] **Step 2: Write the test**

```python
# tests/unit/services/test_affiliate_settings_defaults.py
from services.settings_defaults import DEFAULTS


def test_affiliate_defaults_present_and_dark_launched():
    assert DEFAULTS["affiliate_injection_enabled"] == "false"
    assert DEFAULTS["affiliate_max_links_per_post"] == "3"
    assert DEFAULTS["affiliate_redirect_base_url"] == "/go"
    assert DEFAULTS["affiliate_disclosure_text"].strip() != ""
    assert DEFAULTS["plugin.job.sync_affiliate_clicks.enabled"] == "true"
```

- [ ] **Step 3: Run + commit**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_affiliate_settings_defaults.py -q`
Expected: PASS.

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/tests/unit/services/test_affiliate_settings_defaults.py
git commit -m "feat(affiliate): settings defaults (dark-launched)"
```

---

## Task 3: Service — matcher + DB CRUD

**Files:**

- Create: `src/cofounder_agent/modules/content/affiliate_links.py`
- Test: `src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_service.py`

**Interfaces:**

- Produces:
  - `@dataclass AffiliateLink(code: str, keyword: str, url: str, display_text: str = "")`
  - `inject_affiliate_links(markdown: str, links: list[AffiliateLink], *, base: str = "/go", cap: int = 3) -> tuple[str, list[str]]` — returns `(new_markdown, injected_codes)`.
  - `async def list_active(pool) -> list[AffiliateLink]`
  - `async def add_link(pool, *, code, keyword, url, display_text="", program="") -> None`
  - `async def set_active(pool, code: str, active: bool) -> bool`
  - `async def remove_link(pool, code: str) -> bool`
  - `async def rollup_clicks(pool) -> int` — recompute `affiliate_links.clicks` from `affiliate_link_clicks`.

- [ ] **Step 1: Write the failing matcher tests**

````python
# tests/unit/modules/content/test_affiliate_links_service.py
from modules.content.affiliate_links import AffiliateLink, inject_affiliate_links

M = AffiliateLink(code="mercury", keyword="Mercury", url="https://x", display_text="Mercury")


def test_injects_first_mention_only():
    md = "We use Mercury for banking. Mercury is great."
    out, injected = inject_affiliate_links(md, [M], base="/go", cap=3)
    assert injected == ["mercury"]
    assert out.count("[Mercury](/go/mercury)") == 1
    assert out.endswith("Mercury is great.")  # 2nd mention untouched


def test_respects_cap():
    a = AffiliateLink(code="a", keyword="Alpha", url="u", display_text="Alpha")
    b = AffiliateLink(code="b", keyword="Bravo", url="u", display_text="Bravo")
    out, injected = inject_affiliate_links("Alpha and Bravo.", [a, b], cap=1)
    assert injected == ["a"]


def test_skips_fenced_code_block():
    md = "```\nMercury\n```\nUse Mercury here."
    out, injected = inject_affiliate_links(md, [M])
    assert "```\nMercury\n```" in out          # code block untouched
    assert out.count("[Mercury](/go/mercury)") == 1


def test_skips_inline_code():
    md = "Set `Mercury` env then use Mercury."
    out, _ = inject_affiliate_links(md, [M])
    assert "`Mercury`" in out
    assert "[Mercury](/go/mercury)" in out


def test_skips_headings():
    md = "## Mercury\nWe use Mercury daily."
    out, _ = inject_affiliate_links(md, [M])
    assert out.startswith("## Mercury\n")       # heading untouched
    assert "[Mercury](/go/mercury)" in out


def test_skips_existing_link_and_is_idempotent():
    md = "See [Mercury](/go/mercury) here."
    out, injected = inject_affiliate_links(md, [M])
    assert injected == []
    assert out == md
````

- [ ] **Step 2: Run to verify failure**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/test_affiliate_links_service.py -q`
Expected: FAIL with `ModuleNotFoundError: modules.content.affiliate_links`.

- [ ] **Step 3: Implement the module**

````python
"""Affiliate-link service — the pure keyword matcher + DB CRUD + click rollup.

Rebuild of the removed services/affiliate_linker.py, adapted to markdown (the
pipeline content is markdown at injection time) and to /go/<code> redirect URLs
resolved by the affiliate-redirect Worker. Real referral rows live in the DB
only (never source) — see docs/operations/affiliate-links.md.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^\s*```")


@dataclass
class AffiliateLink:
    code: str
    keyword: str
    url: str
    display_text: str = ""


def _inside_inline_code(line: str, idx: int) -> bool:
    """True when position ``idx`` falls inside a `...` inline-code span."""
    return line.count("`", 0, idx) % 2 == 1


def _link_first_mention(line: str, link: AffiliateLink, base: str) -> tuple[str, int]:
    """Link the first eligible whole-word keyword occurrence in ``line``.

    Eligible = not preceded/followed by a word char, ``/``, ``[`` or ``]`` (so
    it won't fire inside an existing ``[kw](url)`` or a ``/go/kw`` path) and not
    inside an inline-code span. Returns (new_line, n_injected in {0,1}).
    """
    pattern = re.compile(
        r"(?<![\w`/\[\]])(" + re.escape(link.keyword) + r")(?![\w`\]])"
    )
    for m in pattern.finditer(line):
        if _inside_inline_code(line, m.start()):
            continue
        text = link.display_text or link.keyword
        url = f"{base.rstrip('/')}/{link.code}"
        return line[: m.start()] + f"[{text}]({url})" + line[m.end() :], 1
    return line, 0


def inject_affiliate_links(
    markdown: str,
    links: list[AffiliateLink],
    *,
    base: str = "/go",
    cap: int = 3,
) -> tuple[str, list[str]]:
    """Inject up to ``cap`` affiliate links, one per keyword, first-mention only.

    Skips fenced code blocks, heading lines, inline code, and existing links.
    Deterministic and idempotent (a re-run finds the keyword already inside a
    link and skips it). Returns (new_markdown, injected_codes).
    """
    if not markdown or not links or cap <= 0:
        return markdown, []
    used: set[str] = set()
    injected: list[str] = []
    out_lines: list[str] = []
    in_fence = False
    for line in markdown.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence or line.lstrip().startswith("#") or len(injected) >= cap:
            out_lines.append(line)
            continue
        for link in links:
            if link.code in used or len(injected) >= cap:
                continue
            new_line, n = _link_first_mention(line, link, base)
            if n:
                line = new_line
                used.add(link.code)
                injected.append(link.code)
        out_lines.append(line)
    return "\n".join(out_lines), injected


async def list_active(pool: Any) -> list[AffiliateLink]:
    rows = await pool.fetch(
        "SELECT code, keyword, url, COALESCE(display_text, '') AS display_text "
        "FROM affiliate_links WHERE is_active = true ORDER BY id"
    )
    return [
        AffiliateLink(
            code=r["code"], keyword=r["keyword"], url=r["url"],
            display_text=r["display_text"],
        )
        for r in rows
    ]


async def add_link(
    pool: Any, *, code: str, keyword: str, url: str,
    display_text: str = "", program: str = "",
) -> None:
    await pool.execute(
        "INSERT INTO affiliate_links (code, keyword, url, display_text, program) "
        "VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT (code) DO UPDATE SET keyword = EXCLUDED.keyword, "
        "url = EXCLUDED.url, display_text = EXCLUDED.display_text, "
        "program = EXCLUDED.program, updated_at = now()",
        code, keyword, url, display_text or None, program or None,
    )


async def set_active(pool: Any, code: str, active: bool) -> bool:
    tag = await pool.execute(
        "UPDATE affiliate_links SET is_active = $2, updated_at = now() WHERE code = $1",
        code, active,
    )
    return tag.endswith("1")


async def remove_link(pool: Any, code: str) -> bool:
    tag = await pool.execute("DELETE FROM affiliate_links WHERE code = $1", code)
    return tag.endswith("1")


async def rollup_clicks(pool: Any) -> int:
    """Recompute affiliate_links.clicks from the click-event table. Returns rows updated."""
    tag = await pool.execute(
        "UPDATE affiliate_links a SET clicks = c.n, updated_at = now() "
        "FROM (SELECT code, COUNT(*) AS n FROM affiliate_link_clicks GROUP BY code) c "
        "WHERE a.code = c.code AND a.clicks <> c.n"
    )
    try:
        return int(tag.rsplit(" ", 1)[1])
    except (ValueError, IndexError):
        return 0


__all__ = [
    "AffiliateLink", "inject_affiliate_links", "list_active",
    "add_link", "set_active", "remove_link", "rollup_clicks",
]
````

- [ ] **Step 4: Run tests to verify pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/modules/content/test_affiliate_links_service.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/affiliate_links.py src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_service.py
git commit -m "feat(affiliate): keyword matcher + DB CRUD service"
```

---

## Task 4: Injection atom

**Files:**

- Create: `src/cofounder_agent/modules/content/atoms/content_inject_affiliate_links.py`
- Test: `src/cofounder_agent/tests/unit/services/atoms/test_content_inject_affiliate_links.py`

**Interfaces:**

- Consumes: `modules.content.affiliate_links.{list_active, inject_affiliate_links}`; reads `state["content"]`, `state["site_config"]`, and a DB pool from `state["database_service"].pool` or `state["pool"]`.
- Produces: atom named `content.inject_affiliate_links`; returns `{"content": <new>}` when ≥1 link injected, else `{}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/services/atoms/test_content_inject_affiliate_links.py
import pytest
from modules.content.atoms import content_inject_affiliate_links as atom


class _Cfg:
    def __init__(self, on=True):
        self._on = on
    def get_bool(self, k, d=False):
        return self._on if k == "affiliate_injection_enabled" else d
    def get(self, k, d=""):
        return "/go" if k == "affiliate_redirect_base_url" else d
    def get_int(self, k, d=0):
        return 3 if k == "affiliate_max_links_per_post" else d


class _Pool:
    def __init__(self, rows):
        self._rows = rows
    async def fetch(self, *a, **k):
        return self._rows


def _pool(rows):
    class _DB:  # mimics state["database_service"].pool
        pool = _Pool(rows)
    return _DB()


async def test_noop_when_disabled():
    state = {"content": "We use Mercury.", "site_config": _Cfg(on=False),
             "database_service": _pool([])}
    assert await atom.run(state) == {}


async def test_injects_when_enabled():
    rows = [{"code": "mercury", "keyword": "Mercury", "url": "https://x", "display_text": "Mercury"}]
    state = {"content": "We use Mercury for banking.", "site_config": _Cfg(),
             "database_service": _pool(rows)}
    out = await atom.run(state)
    assert out["content"] == "We use [Mercury](/go/mercury) for banking."


async def test_noop_when_no_pool():
    state = {"content": "We use Mercury.", "site_config": _Cfg()}
    assert await atom.run(state) == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_content_inject_affiliate_links.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the atom**

```python
"""content.inject_affiliate_links — inject curated affiliate links into the draft.

Keyword-matched, first-mention-only, capped per post. Placed in the writer
block BEFORE the QA rails so injected links flow through url_validation /
qa.citations like any other link. No-op (returns {}) when
affiliate_injection_enabled=false, there's no DB pool, no active links, or
nothing matched. Real referral rows are DB-only (poindexter affiliate add).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.inject_affiliate_links",
    type="atom",
    version="1.0.0",
    description=(
        "Inject curated affiliate links (keyword match, first-mention, capped) "
        "as [text](/go/<code>) markdown links. No-op when "
        "affiliate_injection_enabled=false. Deterministic — no LLM."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="draft body to link"),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="body with affiliate links"),
    ),
    requires=("content",),
    produces=("content",),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(),
    parallelizable=True,
)


def _resolve_pool(state: dict[str, Any]) -> Any:
    db = state.get("database_service")
    pool = getattr(db, "pool", None) if db is not None else None
    return pool if pool is not None else state.get("pool")


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = state.get("content") or ""
    if not content.strip():
        return {}

    sc = state.get("site_config")
    if sc is None:
        return {}
    try:
        if not sc.get_bool("affiliate_injection_enabled", False):
            return {}
        base = sc.get("affiliate_redirect_base_url", "/go") or "/go"
        cap = sc.get_int("affiliate_max_links_per_post", 3)
    except Exception:  # noqa: BLE001 — config read must never break the pipeline
        return {}

    pool = _resolve_pool(state)
    if pool is None:
        return {}

    from modules.content.affiliate_links import inject_affiliate_links, list_active

    try:
        links = await list_active(pool)
    except Exception as exc:  # noqa: BLE001 — DB blip → pass content through
        logger.debug("[content.inject_affiliate_links] list_active failed: %s", exc)
        return {}
    if not links:
        return {}

    new_content, injected = inject_affiliate_links(content, links, base=base, cap=cap)
    if not injected:
        return {}
    logger.info(
        "[content.inject_affiliate_links] injected %d link(s) (task=%s): %s",
        len(injected), str(state.get("task_id") or "?")[:8], ", ".join(injected),
    )
    return {"content": new_content}


__all__ = ["ATOM_META", "run"]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/atoms/test_content_inject_affiliate_links.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/content_inject_affiliate_links.py src/cofounder_agent/tests/unit/services/atoms/test_content_inject_affiliate_links.py
git commit -m "feat(affiliate): content.inject_affiliate_links atom"
```

---

## Task 5: Wire the atom into `canonical_blog` graph_def + reseed

**Files:**

- Modify: `src/cofounder_agent/services/canonical_blog_spec.py` (node list + edges in `CANONICAL_BLOG_GRAPH_DEF`)
- Create: `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_reseed_canonical_blog_affiliate_node.py`
- Test: `src/cofounder_agent/tests/unit/services/test_graph_def_contract_freshness.py` (existing — must stay green)

**Interfaces:**

- Consumes: the `content.inject_affiliate_links` atom from Task 4.
- Produces: `canonical_blog` graph_def with `inject_affiliate_links` between `llm_reconcile_citations` and `quality_evaluation`.

- [ ] **Step 1: Add the node**

In `CANONICAL_BLOG_GRAPH_DEF["nodes"]`, immediately after the `llm_reconcile_citations` node, add:

```python
        # Inject curated affiliate links (keyword match, first-mention, capped).
        # After citation repair, BEFORE quality_evaluation/url_validation so the
        # injected /go/<code> links flow through the dead-link + QA checks.
        {"id": "inject_affiliate_links", "atom": "content.inject_affiliate_links"},
```

- [ ] **Step 2: Rewire the edges**

In `CANONICAL_BLOG_GRAPH_DEF["edges"]`, replace:

```python
        {"from": "llm_reconcile_citations", "to": "quality_evaluation"},
```

with:

```python
        {"from": "llm_reconcile_citations", "to": "inject_affiliate_links"},
        {"from": "inject_affiliate_links", "to": "quality_evaluation"},
```

- [ ] **Step 3: Generate + write the reseed migration**

Run: `python scripts/new-migration.py "reseed canonical blog affiliate node"`
Then set the body (mirrors `20260623_035500_reseed_media_podcast_graph_defs.py` — write the RAW spec; the boot self-heal `ensure_active_graph_defs_stamped` re-stamps contract fingerprints on next boot):

```python
async def up(pool) -> None:
    import json
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    raw = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, updated_at = now() "
            "WHERE slug = 'canonical_blog'",
            raw,
        )
    logger.info("reseed_canonical_blog_affiliate_node up: %s", tag)


async def down(pool) -> None:
    # No-op: the boot self-heal owns stamping; a forward reseed is not reverted.
    logger.info("reseed_canonical_blog_affiliate_node down: no-op")
```

- [ ] **Step 4: Run the contract + seeded-graph tests**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_graph_def_contract_freshness.py tests/unit/services/test_seeded_graph_def_gate.py -q`
Expected: PASS. If the freshness test carries a committed snapshot/fingerprint file, regenerate it per that test's failure message (it prints the exact regen command), then re-run.

- [ ] **Step 5: Smoke the migration + commit**

Run: `python scripts/ci/migrations_smoke.py`
Expected: applies cleanly.

```bash
git add src/cofounder_agent/services/canonical_blog_spec.py src/cofounder_agent/services/migrations/*_reseed_canonical_blog_affiliate_node.py
git commit -m "feat(affiliate): wire inject_affiliate_links into canonical_blog graph_def"
```

---

## Task 6: `poindexter affiliate` CLI

**Files:**

- Create: `src/cofounder_agent/poindexter/cli/affiliate.py`
- Modify: `src/cofounder_agent/poindexter/cli/app.py`
- Test: `src/cofounder_agent/tests/unit/poindexter/cli/test_affiliate_cli.py`

**Interfaces:**

- Consumes: `modules.content.affiliate_links.{add_link, set_active, remove_link, list_active}`, `brain.bootstrap.resolve_database_url`.
- Produces: `poindexter affiliate {add,list,enable,disable,rm}` registered on `main`.

- [ ] **Step 1: Implement the CLI group (thin adapter → service)**

```python
"""`poindexter affiliate` — manage affiliate-link rows (DB-only real URLs)."""
from __future__ import annotations

import asyncio

import click


async def _connect():
    import asyncpg
    from brain import bootstrap
    dsn = bootstrap.resolve_database_url()
    if not dsn:
        raise click.ClickException("No database_url — run `poindexter setup` first.")
    return await asyncpg.connect(dsn, timeout=8)


@click.group(name="affiliate")
def affiliate_group() -> None:
    """Manage affiliate links (add/list/enable/disable/rm)."""


@affiliate_group.command(name="add")
@click.option("--code", required=True, help="Stable slug for /go/<code> (e.g. mercury).")
@click.option("--keyword", required=True, help="Body keyword to match (e.g. Mercury).")
@click.option("--url", required=True, help="Real merchant/referral URL.")
@click.option("--display-text", default="", help="Link text (defaults to keyword).")
@click.option("--program", default="", help="Program label (e.g. 'Mercury Referral').")
def add_cmd(code, keyword, url, display_text, program):
    from modules.content.affiliate_links import add_link

    async def _go():
        conn = await _connect()
        try:
            await add_link(conn, code=code, keyword=keyword, url=url,
                           display_text=display_text, program=program)
        finally:
            await conn.close()
    asyncio.run(_go())
    click.secho(f"Added/updated affiliate link '{code}'.", fg="green")


@affiliate_group.command(name="list")
def list_cmd():
    from modules.content.affiliate_links import list_active

    async def _go():
        conn = await _connect()
        try:
            return await list_active(conn)
        finally:
            await conn.close()
    links = asyncio.run(_go())
    if not links:
        click.echo("No active affiliate links.")
        return
    for lk in links:
        click.echo(f"  {lk.code:16} {lk.keyword:16} → {lk.url}")


@affiliate_group.command(name="enable")
@click.argument("code")
def enable_cmd(code):
    _set_active(code, True)


@affiliate_group.command(name="disable")
@click.argument("code")
def disable_cmd(code):
    _set_active(code, False)


def _set_active(code, active):
    from modules.content.affiliate_links import set_active

    async def _go():
        conn = await _connect()
        try:
            return await set_active(conn, code, active)
        finally:
            await conn.close()
    ok = asyncio.run(_go())
    if not ok:
        raise click.ClickException(f"No affiliate link with code '{code}'.")
    click.secho(f"{'Enabled' if active else 'Disabled'} '{code}'.", fg="green")


@affiliate_group.command(name="rm")
@click.argument("code")
def rm_cmd(code):
    from modules.content.affiliate_links import remove_link

    async def _go():
        conn = await _connect()
        try:
            return await remove_link(conn, code)
        finally:
            await conn.close()
    ok = asyncio.run(_go())
    if not ok:
        raise click.ClickException(f"No affiliate link with code '{code}'.")
    click.secho(f"Removed '{code}'.", fg="green")
```

- [ ] **Step 2: Register in `app.py`**

Add near the other imports:

```python
from .affiliate import affiliate_group
```

Add next to the other `main.add_command(...)` lines:

```python
main.add_command(affiliate_group, name="affiliate")
```

- [ ] **Step 3: Write the registration test**

```python
# tests/unit/poindexter/cli/test_affiliate_cli.py
from poindexter.cli.app import main


def test_affiliate_group_registered():
    assert "affiliate" in main.commands


def test_affiliate_subcommands():
    from poindexter.cli.affiliate import affiliate_group
    assert {"add", "list", "enable", "disable", "rm"} <= set(affiliate_group.commands)
```

- [ ] **Step 4: Run + commit**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/poindexter/cli/test_affiliate_cli.py -q`
Expected: PASS.

```bash
git add src/cofounder_agent/poindexter/cli/affiliate.py src/cofounder_agent/poindexter/cli/app.py src/cofounder_agent/tests/unit/poindexter/cli/test_affiliate_cli.py
git commit -m "feat(affiliate): poindexter affiliate CLI (add/list/enable/disable/rm)"
```

---

## Task 7: Static export — post flag + `affiliate-links.json`

**Files:**

- Modify: `src/cofounder_agent/services/static_export_service.py`
- Test: `src/cofounder_agent/tests/unit/services/test_static_export_affiliate.py`

**Interfaces:**

- Consumes: `site_config.get("affiliate_redirect_base_url")`, `site_config.get("affiliate_disclosure_text")`.
- Produces: per-post JSON gains `has_affiliate_links: bool` and (when true + text set) `affiliate_disclosure_text: str`; a global `affiliate-links.json` (`{code: {url}}`) uploaded on full rebuild.

- [ ] **Step 1: Write the failing test for the derivation helper**

```python
# tests/unit/services/test_static_export_affiliate.py
from services.static_export_service import _post_full


def test_post_full_flags_affiliate_content():
    post = {"id": "1", "title": "T", "slug": "s",
            "content": "Use [Mercury](/go/mercury) today."}
    out = _post_full(post, affiliate_base="/go",
                     disclosure_text="Affiliate note.")
    assert out["has_affiliate_links"] is True
    assert out["affiliate_disclosure_text"] == "Affiliate note."


def test_post_full_no_affiliate_no_text():
    post = {"id": "1", "title": "T", "slug": "s", "content": "Plain body."}
    out = _post_full(post, affiliate_base="/go", disclosure_text="Affiliate note.")
    assert out["has_affiliate_links"] is False
    assert "affiliate_disclosure_text" not in out
```

- [ ] **Step 2: Update `_post_full`**

Replace the current `_post_full` with (keeps the existing summary+content behavior, adds the two fields):

```python
def _post_full(post: dict, *, affiliate_base: str = "/go", disclosure_text: str = "") -> dict:
    """Full post dict including content (converted to HTML) + affiliate flag."""
    summary = _post_summary(post)
    content_html = _markdown_to_html(post.get("content", ""))
    summary["content"] = content_html
    marker = f"{affiliate_base.rstrip('/')}/"
    has_aff = marker in content_html
    summary["has_affiliate_links"] = has_aff
    if has_aff and disclosure_text:
        summary["affiliate_disclosure_text"] = disclosure_text
    return summary
```

- [ ] **Step 3: Thread config into the `_post_full` call sites**

In each function that calls `_post_full(post)` (`export_post` and `export_full_rebuild` — grep the file for `_post_full(`), resolve the two values from the keyword-only `site_config` already in scope and pass them:

```python
    _aff_base = site_config.get("affiliate_redirect_base_url", "/go") or "/go"
    _aff_text = site_config.get("affiliate_disclosure_text", "") or ""
    # ... then:
    full = _post_full(post, affiliate_base=_aff_base, disclosure_text=_aff_text)
```

- [ ] **Step 4: Add the `affiliate-links.json` export**

Add a helper and call it from `export_full_rebuild` (after the post/index uploads, using the same `pool` + `site_config` already in scope):

```python
async def _export_affiliate_links(pool, *, site_config: SiteConfig) -> None:
    """Upload static/affiliate-links.json — the code→url map the /go Worker resolves."""
    try:
        rows = await pool.fetch(
            "SELECT code, url FROM affiliate_links WHERE is_active = true"
        )
    except Exception as e:
        logger.warning("[STATIC_EXPORT] affiliate-links fetch failed: %s", e)
        return
    mapping = {r["code"]: {"url": r["url"]} for r in rows}
    await _upload_json("affiliate-links.json", _to_json(mapping),
                       "application/json", site_config=site_config)
```

Call it inside `export_full_rebuild` where the other `_upload_json` calls happen:

```python
    await _export_affiliate_links(pool, site_config=site_config)
```

- [ ] **Step 5: Run + commit**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/test_static_export_affiliate.py tests/unit/services/test_static_export_service.py -q`
Expected: PASS (new tests pass; existing export tests stay green).

```bash
git add src/cofounder_agent/services/static_export_service.py src/cofounder_agent/tests/unit/services/test_static_export_affiliate.py
git commit -m "feat(affiliate): export has_affiliate_links flag + affiliate-links.json"
```

---

## Task 8: `/go` redirect Cloudflare Worker

**Files:**

- Create: `infrastructure/cloudflare/affiliate-redirect/src/index.ts`, `src/index.test.ts`, `wrangler.toml`, `package.json`, `tsconfig.json`, `README.md`

**Interfaces:**

- Consumes: `affiliate-links.json` on R2 (from Task 7).
- Produces: `GET /go/<code>` (or `/<code>` on a subdomain) → 302 to the merchant URL + one Analytics Engine data point per click.

- [ ] **Step 1: Scaffold config (copy the beacon Worker's shape)**

`package.json`, `tsconfig.json`, and `wrangler.toml` mirror `infrastructure/cloudflare/page-views-beacon/` (same deps: `wrangler`, `vitest`, `@cloudflare/workers-types`). In `wrangler.toml`, declare the Analytics Engine binding and the config vars:

```toml
name = "affiliate-redirect"
main = "src/index.ts"
compatibility_date = "2024-11-01"

[[analytics_engine_datasets]]
binding = "ANALYTICS_ENGINE"
dataset = "affiliate_clicks"

[vars]
LINKS_URL = "https://<r2-public-host>/static/affiliate-links.json"
HOME_URL = "https://www.gladlabs.io"
```

- [ ] **Step 2: Write the failing test for the pure resolver**

```typescript
// src/index.test.ts
import { describe, it, expect } from 'vitest';
import { codeFromPath, resolveTarget } from './index';

describe('affiliate-redirect', () => {
  it('extracts the code from /go/<code> and /<code>', () => {
    expect(codeFromPath('/go/mercury')).toBe('mercury');
    expect(codeFromPath('/mercury')).toBe('mercury');
    expect(codeFromPath('/go/')).toBe('');
  });
  it('resolves a known code and misses an unknown one', () => {
    const map = { mercury: { url: 'https://mercury.com/r/x' } };
    expect(resolveTarget(map, 'mercury')).toBe('https://mercury.com/r/x');
    expect(resolveTarget(map, 'nope')).toBeNull();
  });
});
```

- [ ] **Step 3: Implement the Worker**

```typescript
// src/index.ts
export interface Env {
  ANALYTICS_ENGINE: AnalyticsEngineDataset;
  LINKS_URL: string;
  HOME_URL: string;
}

type LinkMap = Record<string, { url: string }>;

export function codeFromPath(pathname: string): string {
  return pathname.split('/').filter(Boolean).pop() || '';
}

export function resolveTarget(map: LinkMap, code: string): string | null {
  return map[code]?.url ?? null;
}

async function loadMap(env: Env): Promise<LinkMap> {
  // Cache the small JSON map at the edge for 5 minutes.
  const cache = caches.default;
  const cacheKey = new Request(env.LINKS_URL);
  let resp = await cache.match(cacheKey);
  if (!resp) {
    resp = await fetch(env.LINKS_URL, { cf: { cacheTtl: 300 } });
    if (resp.ok) {
      const cloned = new Response(resp.clone().body, resp);
      cloned.headers.set('Cache-Control', 'max-age=300');
      await cache.put(cacheKey, cloned);
    }
  }
  try {
    return (await resp.json()) as LinkMap;
  } catch {
    return {};
  }
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const home = env.HOME_URL || 'https://www.gladlabs.io';
    const code = codeFromPath(new URL(req.url).pathname);
    if (!code) return Response.redirect(home, 302);

    const map = await loadMap(env);
    const target = resolveTarget(map, code);
    if (!target) return Response.redirect(home, 302);

    const cf = (req.cf as Record<string, unknown> | undefined) ?? {};
    env.ANALYTICS_ENGINE.writeDataPoint({
      blobs: [
        code, // blob1: affiliate code
        (req.headers.get('Referer') || '').slice(0, 500), // blob2: source post URL
        ((cf.country as string) || '').slice(0, 8), // blob3: country
        (req.headers.get('user-agent') || '').slice(0, 200), // blob4: UA
      ],
      doubles: [],
      indexes: [code],
    });
    return Response.redirect(target, 302);
  },
};
```

- [ ] **Step 4: Run the Worker tests**

Run: `cd infrastructure/cloudflare/affiliate-redirect && npm install && npm test`
Expected: PASS (resolver tests).

- [ ] **Step 5: README + commit**

Write `README.md` documenting: the route (`/go/*` on the site zone, or `/<code>` on a `go.` subdomain — see the deploy note), the `affiliate_clicks` AE dataset, and the `wrangler deploy` command. Then:

```bash
git add infrastructure/cloudflare/affiliate-redirect
git commit -m "feat(affiliate): /go redirect Cloudflare Worker + AE click write"
```

---

## Task 9: Click sync job

**Files:**

- Create: `src/cofounder_agent/services/jobs/sync_affiliate_clicks.py`
- Test: `src/cofounder_agent/tests/unit/services/jobs/test_sync_affiliate_clicks.py`
- Modify: wherever `sync_cloudflare_analytics` is registered as a job (grep `SyncCloudflareAnalyticsJob` in `plugins/registry.py` / the jobs registry) — register `SyncAffiliateClicksJob` the same way.

**Interfaces:**

- Consumes: the `affiliate_clicks` AE dataset (via the CF SQL HTTP API), reusing `cloudflare_account_id` + `cloudflare_analytics_api_token`.
- Produces: rows in `affiliate_link_clicks` + updated `affiliate_links.clicks`; high-water mark `affiliate_clicks_last_sync`.

- [ ] **Step 1: Write the failing test for the row-mapper**

```python
# tests/unit/services/jobs/test_sync_affiliate_clicks.py
from services.jobs.sync_affiliate_clicks import _row_to_click


def test_row_to_click_maps_fields():
    raw = {"code": "mercury", "referrer": "https://gladlabs.io/posts/x",
           "country": "US", "user_agent": "UA", "created_at": "2026-07-11 10:00:00"}
    c = _row_to_click(raw)
    assert c["code"] == "mercury"
    assert c["post_slug"] == "x"          # derived from the referrer path
    assert c["country"] == "US"


def test_row_to_click_skips_codeless():
    assert _row_to_click({"code": ""}) is None
```

- [ ] **Step 2: Implement the job** (clone `SyncCloudflareAnalyticsJob`; swap dataset, query columns, target table, watermark key, and add the post-slug derivation + rollup)

```python
"""SyncAffiliateClicksJob — pull affiliate-click rows from CF Analytics Engine.

Mirrors SyncCloudflareAnalyticsJob (services/jobs/sync_cloudflare_analytics.py):
the affiliate-redirect Worker writes one AE data point per /go click; this job
pulls them via the CF SQL HTTP API every 5 min into affiliate_link_clicks, then
rolls counts up into affiliate_links.clicks. High-water mark in app_settings.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)

_SQL_API_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/analytics_engine/sql"
_QUERY = (
    "SELECT blob1 AS code, blob2 AS referrer, blob3 AS country, "
    "blob4 AS user_agent, timestamp AS created_at "
    "FROM affiliate_clicks "
    "WHERE timestamp > toDateTime('{since}', 'UTC') "
    "ORDER BY created_at ASC LIMIT {limit} FORMAT JSON"
)
_WATERMARK_KEY = "affiliate_clicks_last_sync"
_SLUG_RE = re.compile(r"/posts/([^/?#]+)")


def _row_to_click(raw: dict[str, Any]) -> dict[str, Any] | None:
    code = (raw.get("code") or "").strip()
    if not code:
        return None
    referrer = (raw.get("referrer") or "")[:1000]
    m = _SLUG_RE.search(referrer)
    return {
        "code": code[:64],
        "post_slug": (m.group(1) if m else None),
        "referrer": referrer or None,
        "country": (raw.get("country") or "")[:8] or None,
        "user_agent": (raw.get("user_agent") or "")[:500] or None,
        "created_at_raw": raw.get("created_at") or "",
    }


class SyncAffiliateClicksJob:
    name = "sync_affiliate_clicks"
    description = "Pull affiliate /go clicks from CF Analytics Engine into affiliate_link_clicks."
    schedule = "every 5 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no _site_config — skipping", changes_made=0)
        account_id = (sc.get("cloudflare_account_id", "") or "").strip()
        if not account_id:
            return JobResult(ok=True, detail="cloudflare_account_id unset — skipping", changes_made=0)
        try:
            api_token = (await sc.get_secret("cloudflare_analytics_api_token", "")).strip()
        except Exception as e:  # noqa: BLE001
            return JobResult(ok=False, detail=f"get_secret failed: {e}", changes_made=0)
        if not api_token:
            return JobResult(ok=True, detail="cloudflare_analytics_api_token unset — skipping", changes_made=0)

        batch_size = int(config.get("batch_size", 5000))
        lookback_hours = int(config.get("lookback_hours", 24))
        import httpx

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings WHERE key = $1", _WATERMARK_KEY
            )
        last = (row["value"] if row and row["value"] else "").strip()
        try:
            since = datetime.fromisoformat(last.replace("Z", "+00:00")) if last else None
        except ValueError:
            since = None
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        since_str = since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        sql = _QUERY.format(since=since_str, limit=batch_size)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    _SQL_API_URL.format(account_id=account_id),
                    headers={"Authorization": f"Bearer {api_token}", "Content-Type": "text/plain"},
                    content=sql,
                )
        except Exception as e:  # noqa: BLE001
            return JobResult(ok=False, detail=f"cf api request failed: {e}", changes_made=0)
        if resp.status_code != 200:
            return JobResult(ok=False, detail=f"cf api returned {resp.status_code}", changes_made=0)

        rows = (resp.json() or {}).get("data") or []
        inserted = 0
        max_ts: datetime | None = None
        async with pool.acquire() as conn:
            async with conn.transaction():
                for raw in rows:
                    c = _row_to_click(raw)
                    if c is None:
                        continue
                    try:
                        ts = datetime.strptime(c["created_at_raw"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
                    dup = await conn.fetchval(
                        "SELECT 1 FROM affiliate_link_clicks "
                        "WHERE code = $1 AND created_at = $2 "
                        "AND user_agent IS NOT DISTINCT FROM $3 LIMIT 1",
                        c["code"], ts, c["user_agent"],
                    )
                    if dup:
                        continue
                    await conn.execute(
                        "INSERT INTO affiliate_link_clicks "
                        "(code, post_slug, referrer, country, user_agent, created_at) "
                        "VALUES ($1,$2,$3,$4,$5,$6)",
                        c["code"], c["post_slug"], c["referrer"], c["country"], c["user_agent"], ts,
                    )
                    inserted += 1
                    if max_ts is None or ts > max_ts:
                        max_ts = ts

        if inserted:
            from modules.content.affiliate_links import rollup_clicks
            await rollup_clicks(pool)
        if max_ts is not None:
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO app_settings (key, value, category, is_active, is_secret) "
                    "VALUES ($1, $2, 'cloudflare', true, false) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    _WATERMARK_KEY, max_ts.isoformat(),
                )
        return JobResult(ok=True, detail=f"synced {inserted} affiliate clicks", changes_made=inserted)


__all__ = ["SyncAffiliateClicksJob", "_row_to_click"]
```

- [ ] **Step 3: Register the job**

Grep for how `SyncCloudflareAnalyticsJob` is registered (`grep -rn SyncCloudflareAnalyticsJob src/cofounder_agent/plugins src/cofounder_agent/services/jobs/__init__.py`) and add `SyncAffiliateClicksJob` the identical way (same registry list / entry-point / `_SAMPLES` jobs section).

- [ ] **Step 4: Run + commit**

Run: `cd src/cofounder_agent && poetry run pytest tests/unit/services/jobs/test_sync_affiliate_clicks.py -q`
Expected: PASS (2 tests).

```bash
git add src/cofounder_agent/services/jobs/sync_affiliate_clicks.py src/cofounder_agent/tests/unit/services/jobs/test_sync_affiliate_clicks.py src/cofounder_agent/plugins/registry.py
git commit -m "feat(affiliate): sync_affiliate_clicks job (AE → affiliate_link_clicks)"
```

---

## Task 10: Disclosure banner (frontend)

**Files:**

- Modify: `web/public-site/lib/posts.ts` (add fields to `Post`)
- Create: `web/public-site/components/AffiliateDisclosure.tsx`
- Modify: `web/public-site/app/posts/[slug]/page.tsx`
- Test: `web/public-site/components/AffiliateDisclosure.test.tsx`

**Interfaces:**

- Consumes: `post.has_affiliate_links`, `post.affiliate_disclosure_text` from the exported JSON (Task 7).
- Produces: a banner rendered near the top of the article body when `has_affiliate_links` is true.

- [ ] **Step 1: Extend the `Post` interface**

In `web/public-site/lib/posts.ts`, add to the `Post` interface:

```typescript
  has_affiliate_links?: boolean;
  affiliate_disclosure_text?: string;
```

- [ ] **Step 2: Write the failing component test**

```tsx
// components/AffiliateDisclosure.test.tsx
import { render, screen } from '@testing-library/react';
import { AffiliateDisclosure } from './AffiliateDisclosure';

test('renders provided text', () => {
  render(<AffiliateDisclosure text="Custom affiliate note." />);
  expect(screen.getByText(/Custom affiliate note\./)).toBeInTheDocument();
});

test('falls back to default when text empty', () => {
  render(<AffiliateDisclosure />);
  expect(screen.getByText(/affiliate link/i)).toBeInTheDocument();
});
```

- [ ] **Step 3: Implement the component**

```tsx
// components/AffiliateDisclosure.tsx
const DEFAULT_TEXT =
  'Some links in this article are affiliate links — if you sign up through them, Glad Labs may earn a commission at no extra cost to you.';

export function AffiliateDisclosure({ text }: { text?: string }) {
  return (
    <div
      className="gl-log gl-mono--upper mb-8 rounded border border-[color:var(--gl-hairline)] px-4 py-3 text-xs"
      role="note"
    >
      {text && text.trim() ? text : DEFAULT_TEXT}
    </div>
  );
}
```

- [ ] **Step 4: Render it in the post page**

In `web/public-site/app/posts/[slug]/page.tsx`, import it:

```tsx
import { AffiliateDisclosure } from '../../../components/AffiliateDisclosure';
```

Then inside the "Article body" container, immediately after the opening `<div className="container mx-auto max-w-4xl">` (before the Table of Contents block, so the disclosure appears before any affiliate links), add:

```tsx
{
  post.has_affiliate_links && (
    <AffiliateDisclosure text={post.affiliate_disclosure_text} />
  );
}
```

- [ ] **Step 5: Run + commit**

Run: `cd web/public-site && npm test -- AffiliateDisclosure`
Expected: PASS (2 tests).

```bash
git add web/public-site/lib/posts.ts web/public-site/components/AffiliateDisclosure.tsx web/public-site/components/AffiliateDisclosure.test.tsx "web/public-site/app/posts/[slug]/page.tsx"
git commit -m "feat(affiliate): conditional FTC disclosure banner on posts"
```

---

## Task 11: Grafana click panels

**Files:**

- Modify: the Cost & Analytics dashboard JSON under `infrastructure/grafana/dashboards/` (grep for the file whose `title` is the Cost & Analytics board).

**Interfaces:**

- Consumes: `affiliate_link_clicks` (Postgres datasource).

- [ ] **Step 1: Add three panels**

Following an existing table/timeseries panel in the same dashboard file (copy its structure — `datasource`, `gridPos`, `fieldConfig` — and change `id`, `title`, and `targets[].rawSql`), add:

1. **Timeseries "Affiliate clicks / day":**
   `SELECT date_trunc('day', created_at) AS time, COUNT(*) AS clicks FROM affiliate_link_clicks GROUP BY 1 ORDER BY 1`
2. **Table "Clicks by link":**
   `SELECT code, COUNT(*) AS clicks FROM affiliate_link_clicks GROUP BY code ORDER BY clicks DESC`
3. **Table "Clicks by post":**
   `SELECT post_slug, COUNT(*) AS clicks FROM affiliate_link_clicks WHERE post_slug IS NOT NULL GROUP BY post_slug ORDER BY clicks DESC LIMIT 25`

Give each a unique `id` (max existing `id` + 1, 2, 3) and place them in a new row at the bottom of the board.

- [ ] **Step 2: Validate JSON + commit**

Run: `python -c "import json,sys; json.load(open(sys.argv[1]))" <dashboard.json>`
Expected: no error (valid JSON).

```bash
git add infrastructure/grafana/dashboards/<dashboard>.json
git commit -m "feat(affiliate): Grafana click panels on Cost & Analytics"
```

---

## Task 12: Operator runbook

**Files:**

- Create: `docs/operations/affiliate-links.md`

- [ ] **Step 1: Write the runbook**

Cover: the DB-only rule; `poindexter affiliate add --code <c> --keyword <k> --url <u> [--display-text ..] [--program ..]`; enabling with `poindexter settings set affiliate_injection_enabled true`; how injection works (keyword, first-mention, cap `affiliate_max_links_per_post`); the `/go` Worker + `affiliate-links.json` export + `affiliate_clicks` AE dataset + `sync_affiliate_clicks` job; the disclosure banner + `affiliate_disclosure_text`; and the deploy note that the Worker routes at `www.gladlabs.io/go/*` (zone route → relative `/go` base) **or** `go.gladlabs.io/*` (subdomain → set `affiliate_redirect_base_url` to that origin).

- [ ] **Step 2: Commit**

```bash
git add docs/operations/affiliate-links.md
git commit -m "docs(affiliate): operator runbook"
```

---

## Rollout (after all tasks merge)

1. Deploy the Worker (`wrangler deploy` in `infrastructure/cloudflare/affiliate-redirect/`); confirm the zone route or `go.` subdomain resolves.
2. Trigger a full static rebuild so `affiliate-links.json` publishes to R2.
3. Load real rows (DB-only): `poindexter affiliate add --code mercury --keyword Mercury --url <mercury referral> --program "Mercury Referral"`; same for the Google Workspace link.
4. Confirm `/go/mercury` 302s to the merchant and a row lands in `affiliate_link_clicks` within ~5 min.
5. Flip `poindexter settings set affiliate_injection_enabled true`; watch the next generated post get a vetted link + disclosure banner, and the Grafana panel populate.

## Deviations from the spec (noted)

- The spec suggested threading a `has_affiliate_links` flag through `PipelineState` → `content.compile_meta` → post JSON. This plan instead **derives** the flag at export time from the post content (presence of the `{base}/` redirect path). Same observable behavior, one source of truth (the content itself), and no `PipelineState`/persist/`posts`-schema changes. The atom's only job is to inject the links.
