# Affiliate Links: Multi-Keyword Matching, Platform Column, CSV Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one affiliate link match on multiple keyword phrases (shared
across links where useful), add a free-text `platform` tracking column, and
ship a permanent `poindexter affiliate import-csv` command.

**Architecture:** A new `affiliate_link_keywords` child table replaces the
old singular `affiliate_links.keyword` column; `list_active`/`list_all`
aggregate it back into an `AffiliateLink.keywords` list. The matcher groups
candidates by keyword string across the whole catalog (not per-link),
resolving ties between links that share a keyword via a co-occurrence check
first, then least-recently-injected rotation (mirrors the existing
image-style LRU pattern) — no LLM in the matcher itself. A new
`affiliate_import.py` module drives CSV bulk-import: deterministic code
slugging, a local-LLM-assisted `display_text`/keyword proposal per new row
(SKILL.md-cataloged prompt, fails open), and insert-only-by-default writes
via the same `add_link()`/`set_active()` used everywhere else.

**Tech Stack:** Python 3.13 / asyncpg / Click / pytest-asyncio. No new
dependencies — `csv` is stdlib, the LLM call reuses `services.llm_text` and
`services.prompt_manager`.

## Global Constraints

- Strict TDD every step: write the failing test, run it, confirm it fails
  for the right reason, implement, run again, confirm it passes, commit.
- No inline SQL outside `modules/content/affiliate_links.py` /
  `modules/content/affiliate_import.py` — the CLI stays a thin adapter
  (transport-adapter-contract ADR); route everything through those two
  service modules.
- New `app_settings` defaults go in `services/settings_defaults.py`'s
  `DEFAULTS` dict, never in a migration file.
- New/changed prompt templates are cataloged in a `skills/*/*/SKILL.md`
  pack (never Langfuse-only) with a matching inline `_PROMPT_FALLBACK` and
  a case added to `tests/unit/services/test_prompt_fallback_drift.py`.
- A brand-new bare `except Exception: pass` (or log-only, or
  sentinel-return-only) needs a `# silent-ok: <reason>` plain comment or CI's
  `lint_silent_excepts.py` ratchet fails the build.
- Test fixtures use placeholder URLs (`https://x`) or the existing 4 real
  codes already referenced in tests (`mercury`, etc.) — never fabricate a
  real-looking referral URL.
- Run backend tests via the **main checkout's** poetry-venv python (this
  worktree has no venv of its own):
  `"C:\Users\mattm\glad-labs-website\src\cofounder_agent\.venv\Scripts\python.exe" -m pytest <path> -o addopts=""`
  run from `src/cofounder_agent`. If that venv path doesn't exist, locate it
  with `poetry env info --path` from the main checkout.

---

### Task 1: Schema migration — `affiliate_link_keywords` + `platform`

**Files:**

- Create: `src/cofounder_agent/services/migrations/<timestamp>_multi_keyword_and_platform_for_affiliate_links.py` (generate the filename via the command in Step 1 — do not hand-invent the timestamp)
- Modify: `src/cofounder_agent/tests/integration_db/test_affiliate_links_schema.py`

**Interfaces:**

- Produces: `affiliate_link_keywords` table (`id, link_id, keyword, created_at`, `UNIQUE(link_id, keyword)`, `ON DELETE CASCADE` from `affiliate_links`); `affiliate_links.platform VARCHAR(50) NOT NULL DEFAULT ''`; `affiliate_links.keyword` column is **dropped**.

- [ ] **Step 1: Generate the migration file**

Run: `python scripts/new-migration.py "multi keyword and platform for affiliate links"`

This creates `src/cofounder_agent/services/migrations/YYYYMMDD_HHMMSS_multi_keyword_and_platform_for_affiliate_links.py` with an empty `up`/`down` stub. Note the exact generated filename for the remaining steps.

- [ ] **Step 2: Write the failing schema tests**

Edit `src/cofounder_agent/tests/integration_db/test_affiliate_links_schema.py`. Replace the existing `test_affiliate_links_columns` and add new tests:

```python
async def test_affiliate_links_columns(test_pool):
    cols = await _columns(test_pool, "affiliate_links")
    assert {
        "id", "code", "url", "display_text", "program", "is_active",
        "clicks", "created_at", "updated_at", "description", "category",
        "platform",
    } <= cols
    assert "keyword" not in cols


async def test_affiliate_link_keywords_columns(test_pool):
    cols = await _columns(test_pool, "affiliate_link_keywords")
    assert {"id", "link_id", "keyword", "created_at"} <= cols


async def test_affiliate_link_keywords_allows_shared_keyword_across_links(test_pool):
    """Different links CAN share a keyword — uniqueness is scoped
    UNIQUE(link_id, keyword), not global. See the design spec's "Shared
    keywords" section."""
    async with test_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="rollback-for-test-isolation"):
            async with conn.transaction():
                link_a = await conn.fetchval(
                    "INSERT INTO affiliate_links (code, url) VALUES ($1, $2) RETURNING id",
                    "shared-kw-test-a", "https://x",
                )
                link_b = await conn.fetchval(
                    "INSERT INTO affiliate_links (code, url) VALUES ($1, $2) RETURNING id",
                    "shared-kw-test-b", "https://y",
                )
                await conn.execute(
                    "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                    link_a, "SharedKeyword",
                )
                await conn.execute(
                    "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                    link_b, "SharedKeyword",
                )
                # Force rollback so this test data never persists in the shared pool.
                raise RuntimeError("rollback-for-test-isolation")


async def test_affiliate_link_keywords_rejects_duplicate_within_same_link(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(Exception):
            async with conn.transaction():
                link_id = await conn.fetchval(
                    "INSERT INTO affiliate_links (code, url) VALUES ($1, $2) RETURNING id",
                    "dup-kw-test", "https://x",
                )
                await conn.execute(
                    "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                    link_id, "DupKeyword",
                )
                await conn.execute(
                    "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                    link_id, "DupKeyword",
                )
```

- [ ] **Step 3: Run the tests to verify they fail**

Run (from `src/cofounder_agent`):
`<venv-python> -m pytest tests/integration_db/test_affiliate_links_schema.py -o addopts="" -v`

Expected: `test_affiliate_links_columns` fails (no `platform` column / `keyword` still present), `test_affiliate_link_keywords_*` fail with `UndefinedTableError` (table doesn't exist yet).

- [ ] **Step 4: Write the migration**

`src/cofounder_agent/services/migrations/<timestamp>_multi_keyword_and_platform_for_affiliate_links.py`:

```python
"""Multi-keyword matching + platform tracking for affiliate_links.

Widens affiliate-link matching from one keyword per link to many (a new
child table), and adds a free-text `platform` column. See
docs/superpowers/specs/2026-07-12-affiliate-multi-keyword-platform-design.md.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS affiliate_link_keywords (
              id SERIAL PRIMARY KEY,
              link_id INTEGER NOT NULL REFERENCES affiliate_links(id) ON DELETE CASCADE,
              keyword TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE (link_id, keyword)
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_affiliate_link_keywords_link_id "
            "ON affiliate_link_keywords(link_id)"
        )
        await conn.execute(
            "ALTER TABLE affiliate_links "
            "ADD COLUMN IF NOT EXISTS platform VARCHAR(50) NOT NULL DEFAULT ''"
        )
        # Backfill: each existing row's single `keyword` becomes its first alias.
        await conn.execute(
            "INSERT INTO affiliate_link_keywords (link_id, keyword) "
            "SELECT id, keyword FROM affiliate_links "
            "ON CONFLICT (link_id, keyword) DO NOTHING"
        )
        await conn.execute("ALTER TABLE affiliate_links DROP COLUMN IF EXISTS keyword")
    logger.info("Migration multi_keyword_and_platform_for_affiliate_links: applied")


async def down(pool) -> None:
    """Revert the migration.

    Restores `keyword` as a plain (non-UNIQUE) column: post-migration data
    may legitimately have shared keywords across links, so re-adding the old
    global UNIQUE constraint here could fail against real data. Rolling back
    is a best-effort safety net, not a byte-for-byte restore.
    """
    async with pool.acquire() as conn:
        await conn.execute("ALTER TABLE affiliate_links ADD COLUMN IF NOT EXISTS keyword TEXT")
        await conn.execute(
            """
            UPDATE affiliate_links al SET keyword = sub.keyword
            FROM (
              SELECT DISTINCT ON (link_id) link_id, keyword
              FROM affiliate_link_keywords
              ORDER BY link_id, id
            ) sub
            WHERE al.id = sub.link_id
            """
        )
        await conn.execute("ALTER TABLE affiliate_links DROP COLUMN IF EXISTS platform")
        await conn.execute("DROP TABLE IF EXISTS affiliate_link_keywords")
    logger.info("Migration multi_keyword_and_platform_for_affiliate_links: reverted")
```

- [ ] **Step 5: Run the migrations-smoke check + tests to verify they pass**

Run: `python scripts/ci/migrations_smoke.py` (confirms the migration applies cleanly)

Then: `<venv-python> -m pytest tests/integration_db/test_affiliate_links_schema.py -o addopts="" -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/services/migrations/*_multi_keyword_and_platform_for_affiliate_links.py src/cofounder_agent/tests/integration_db/test_affiliate_links_schema.py
git commit -m "feat(affiliate): add affiliate_link_keywords table + platform column"
```

---

### Task 2: `AffiliateLink` dataclass + CRUD (`list_active`/`list_all`/`add_link`)

**Files:**

- Modify: `src/cofounder_agent/modules/content/affiliate_links.py` (dataclass, `list_active`, new `list_all`, `add_link`)
- Modify: `src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_crud.py`

**Interfaces:**

- Consumes: Task 1's `affiliate_link_keywords` table + `affiliate_links.platform`.
- Produces: `AffiliateLink(code, keywords: list[str], url, display_text, platform)`; `async def list_active(pool) -> list[AffiliateLink]`; `async def list_all(pool) -> list[AffiliateLink]`; `async def add_link(pool, *, code, keywords: list[str], url, display_text="", program="", description="", category="product", platform="") -> None` (raises `ValueError` if `keywords` is empty).

- [ ] **Step 1: Write the failing CRUD tests**

Replace `src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_crud.py` in full:

```python
"""Unit tests for the affiliate-link CRUD helpers (fake pool, no real DB).

Complements test_affiliate_links_service.py (pure matcher tests, no DB at
all) and the integration_db schema guard — these assert the SQL shape +
parameter order add_link/list_active/list_all/set_active/remove_link send,
using capturing fake pool/connection doubles.
"""

from __future__ import annotations

import pytest

from modules.content.affiliate_links import (
    add_link, list_active, list_all, remove_link, set_active,
)


class _NullAsyncCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, fetchval_result=1):
        self.calls: list[tuple] = []
        self._fetchval_result = fetchval_result

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self._fetchval_result

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "INSERT 0 1"

    def transaction(self):
        return _NullAsyncCtx()


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, fetchval_result=1, execute_result: str = "UPDATE 1"):
        self.conn = _FakeConn(fetchval_result=fetchval_result)
        self._execute_result = execute_result

    def acquire(self):
        return _AcquireCtx(self.conn)

    async def execute(self, sql, *args):
        self.conn.calls.append(("pool.execute", sql, args))
        return self._execute_result


async def test_add_link_persists_description_category_platform_and_keywords():
    pool = _FakePool(fetchval_result=42)
    await add_link(
        pool, code="mercury", keywords=["Mercury", "Mercury Bank"],
        url="https://mercury.com/r/glad-labs", display_text="Mercury",
        program="Mercury Referral", description="Business banking we use daily.",
        category="service", platform="direct",
    )
    calls = pool.conn.calls
    kind, sql, args = calls[0]
    assert kind == "fetchval"
    assert "platform" in sql
    assert "keyword" not in sql  # keyword rows are separate inserts, not this row
    assert args == (
        "mercury", "https://mercury.com/r/glad-labs", "Mercury",
        "Mercury Referral", "Business banking we use daily.", "service", "direct",
    )
    delete_kind, delete_sql, delete_args = calls[1]
    assert delete_kind == "execute"
    assert "DELETE FROM affiliate_link_keywords" in delete_sql
    assert delete_args == (42,)
    kw_calls = calls[2:]
    assert [c[2] for c in kw_calls] == [(42, "Mercury"), (42, "Mercury Bank")]


async def test_add_link_dedupes_repeated_keywords_preserving_order():
    pool = _FakePool(fetchval_result=7)
    await add_link(pool, code="x", keywords=["A", "A", "B"], url="https://x")
    kw_calls = pool.conn.calls[2:]
    assert [c[2] for c in kw_calls] == [(7, "A"), (7, "B")]


async def test_add_link_requires_at_least_one_keyword():
    pool = _FakePool()
    with pytest.raises(ValueError):
        await add_link(pool, code="x", keywords=[], url="https://x")


async def test_add_link_defaults_category_to_product():
    pool = _FakePool(fetchval_result=1)
    await add_link(
        pool, code="widget", keywords=["Widget"], url="https://x",
        description="A thing.",
    )
    _, _, args = pool.conn.calls[0]
    assert args[-2] == "product"  # (..., category, platform) — category is 2nd-to-last


class _ListPool:
    def __init__(self, rows):
        self._rows = rows
        self.fetch_sql = None

    async def fetch(self, sql):
        self.fetch_sql = sql
        return self._rows


async def test_list_active_aggregates_keywords_and_platform():
    pool = _ListPool([{
        "code": "mercury", "url": "https://x", "display_text": "Mercury",
        "platform": "direct", "keywords": ["Mercury", "Mercury Bank"],
    }])
    links = await list_active(pool)
    assert "affiliate_link_keywords" in pool.fetch_sql
    assert "is_active = true" in pool.fetch_sql
    assert len(links) == 1
    assert links[0].code == "mercury"
    assert links[0].keywords == ["Mercury", "Mercury Bank"]
    assert links[0].platform == "direct"


async def test_list_all_has_no_active_filter():
    pool = _ListPool([])
    assert await list_all(pool) == []
    assert "is_active" not in pool.fetch_sql


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

Run: `<venv-python> -m pytest tests/unit/modules/content/test_affiliate_links_crud.py -o addopts="" -v`

Expected: FAIL — `add_link()` still takes `keyword: str`, no `list_all`, old `list_active` query shape.

- [ ] **Step 3: Rewrite the dataclass + CRUD functions**

In `src/cofounder_agent/modules/content/affiliate_links.py`, replace the `AffiliateLink` dataclass, `list_active`, and `add_link`; add `list_all`:

```python
from dataclasses import dataclass, field
```

(add `field` to the existing `from dataclasses import dataclass` import line)

```python
@dataclass
class AffiliateLink:
    code: str
    keywords: list[str] = field(default_factory=list)
    url: str = ""
    display_text: str = ""
    platform: str = ""
```

```python
async def _fetch_links(pool: Any, *, active_only: bool) -> list[AffiliateLink]:
    where = "WHERE al.is_active = true " if active_only else ""
    rows = await pool.fetch(
        "SELECT al.code, al.url, COALESCE(al.display_text, '') AS display_text, "
        "al.platform, "
        "COALESCE(array_agg(alk.keyword ORDER BY alk.id) "
        "  FILTER (WHERE alk.keyword IS NOT NULL), '{}') AS keywords "
        "FROM affiliate_links al "
        "LEFT JOIN affiliate_link_keywords alk ON alk.link_id = al.id "
        + where + "GROUP BY al.id ORDER BY al.id"
    )
    return [
        AffiliateLink(code=r["code"], url=r["url"], display_text=r["display_text"],
                      platform=r["platform"], keywords=list(r["keywords"]))
        for r in rows
    ]


async def list_active(pool: Any) -> list[AffiliateLink]:
    return await _fetch_links(pool, active_only=True)


async def list_all(pool: Any) -> list[AffiliateLink]:
    return await _fetch_links(pool, active_only=False)


async def add_link(
    pool: Any, *, code: str, keywords: list[str], url: str,
    display_text: str = "", program: str = "", description: str = "",
    category: str = "product", platform: str = "",
) -> None:
    if not keywords:
        raise ValueError("add_link requires at least one keyword")
    deduped = list(dict.fromkeys(keywords))
    async with pool.acquire() as conn, conn.transaction():
        link_id = await conn.fetchval(
            "INSERT INTO affiliate_links "
            "(code, url, display_text, program, description, category, platform) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (code) DO UPDATE SET url = EXCLUDED.url, "
            "display_text = EXCLUDED.display_text, program = EXCLUDED.program, "
            "description = EXCLUDED.description, category = EXCLUDED.category, "
            "platform = EXCLUDED.platform, updated_at = now() RETURNING id",
            code, url, display_text or None, program or None,
            description, category, platform,
        )
        await conn.execute("DELETE FROM affiliate_link_keywords WHERE link_id = $1", link_id)
        for kw in deduped:
            await conn.execute(
                "INSERT INTO affiliate_link_keywords (link_id, keyword) VALUES ($1, $2)",
                link_id, kw,
            )
```

Update `__all__` to add `"list_all"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `<venv-python> -m pytest tests/unit/modules/content/test_affiliate_links_crud.py -o addopts="" -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/affiliate_links.py src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_crud.py
git commit -m "feat(affiliate): multi-keyword CRUD — list_all + child-table-backed add_link"
```

---

### Task 3: Matcher rewrite — shared-keyword resolution + `load_link_last_used`

**Files:**

- Modify: `src/cofounder_agent/modules/content/affiliate_links.py` (`inject_affiliate_links`, `_link_first_mention` → `_find_eligible_span`, new `_resolve_link`, new `load_link_last_used`)
- Modify: `src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_service.py`

**Interfaces:**

- Consumes: `AffiliateLink.keywords` (Task 2).
- Produces: `inject_affiliate_links(markdown, links, *, base="/go", cap=3, last_used: dict[str,str] | None = None, rng: Any = random) -> tuple[str, list[str]]`; `async def load_link_last_used(pool) -> dict[str, str]`.

- [ ] **Step 1: Write the failing matcher tests**

Replace `src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_service.py` in full:

````python
"""Unit tests for the affiliate-link matcher (pure, no DB)."""

from modules.content.affiliate_links import AffiliateLink, inject_affiliate_links

M = AffiliateLink(code="mercury", keywords=["Mercury"], url="https://x", display_text="Mercury")


def test_injects_first_mention_only():
    md = "We use Mercury for banking. Mercury is great."
    out, injected = inject_affiliate_links(md, [M], base="/go", cap=3)
    assert injected == ["mercury"]
    assert out.count("[Mercury](/go/mercury)") == 1
    assert out.endswith("Mercury is great.")  # 2nd mention untouched


def test_respects_cap():
    a = AffiliateLink(code="a", keywords=["Alpha"], url="u", display_text="Alpha")
    b = AffiliateLink(code="b", keywords=["Bravo"], url="u", display_text="Bravo")
    out, injected = inject_affiliate_links("Alpha and Bravo.", [a, b], cap=1)
    assert injected == ["a"]


def test_skips_fenced_code_block():
    md = "```\nMercury\n```\nUse Mercury here."
    out, injected = inject_affiliate_links(md, [M])
    assert "```\nMercury\n```" in out
    assert out.count("[Mercury](/go/mercury)") == 1


def test_skips_inline_code():
    md = "Set `Mercury` env then use Mercury."
    out, _ = inject_affiliate_links(md, [M])
    assert "`Mercury`" in out
    assert "[Mercury](/go/mercury)" in out


def test_skips_headings():
    md = "## Mercury\nWe use Mercury daily."
    out, _ = inject_affiliate_links(md, [M])
    assert out.startswith("## Mercury\n")
    assert "[Mercury](/go/mercury)" in out


def test_skips_existing_link_and_is_idempotent():
    md = "See [Mercury](/go/mercury) here."
    out, injected = inject_affiliate_links(md, [M])
    assert injected == []
    assert out == md


def test_display_text_defaults_to_matched_keyword():
    link = AffiliateLink(code="c", keywords=["Cloudflare"], url="u")  # no display_text
    out, _ = inject_affiliate_links("We run Cloudflare here.", [link])
    assert "[Cloudflare](/go/c)" in out


def test_noop_on_empty_inputs():
    assert inject_affiliate_links("", [M]) == ("", [])
    assert inject_affiliate_links("text", []) == ("text", [])


def test_longer_keyword_phrase_preferred_over_shorter_substring():
    """"RTX 5090" and "5090" are DIFFERENT links' keywords (not shared) — the
    longer phrase must be tried first so it isn't half-consumed by the
    shorter one first."""
    generic = AffiliateLink(code="generic", keywords=["5090"], url="u1", display_text="Generic")
    gpu = AffiliateLink(code="gpu", keywords=["RTX 5090"], url="u2", display_text="GPU")
    out, injected = inject_affiliate_links("The RTX 5090 is fast.", [gpu, generic], cap=3)
    assert injected == ["gpu"]
    assert "[GPU](/go/gpu)" in out
    assert "generic" not in injected


def test_shared_keyword_resolves_via_cooccurrence():
    psu = AffiliateLink(code="psu", keywords=["Corsair", "HX1500i"], url="u1", display_text="PSU")
    headset = AffiliateLink(code="headset", keywords=["Corsair", "Virtuoso MAX"], url="u2", display_text="Headset")
    md = "I love my Corsair gear, especially the HX1500i."
    out, injected = inject_affiliate_links(md, [psu, headset], cap=3)
    assert injected == ["psu"]
    assert "/go/psu" in out
    assert "/go/headset" not in out


def test_shared_keyword_resolves_via_lru_when_no_cooccurrence():
    a = AffiliateLink(code="a", keywords=["Corsair"], url="u1", display_text="A")
    b = AffiliateLink(code="b", keywords=["Corsair"], url="u2", display_text="B")
    # "a" was used more recently than "b" -> "b" is least-recently-used -> wins
    last_used = {"a": "2026-07-10T00:00:00", "b": "2026-01-01T00:00:00"}
    out, injected = inject_affiliate_links("I love my Corsair gear.", [a, b], last_used=last_used)
    assert injected == ["b"]
    assert "[B](/go/b)" in out


def test_shared_keyword_lru_tie_breaks_via_injected_rng_when_both_unused():
    a = AffiliateLink(code="a", keywords=["Corsair"], url="u1")
    b = AffiliateLink(code="b", keywords=["Corsair"], url="u2")

    class _FixedRng:
        def choice(self, seq):
            return seq[-1]

    out, injected = inject_affiliate_links(
        "Corsair gear.", [a, b], last_used={}, rng=_FixedRng(),
    )
    assert injected == ["b"]
````

- [ ] **Step 2: Run the tests to verify they fail**

Run: `<venv-python> -m pytest tests/unit/modules/content/test_affiliate_links_service.py -o addopts="" -v`

Expected: FAIL — `AffiliateLink(keywords=...)` doesn't match the current `keyword=` field name; new shared-keyword tests fail/error.

- [ ] **Step 3: Rewrite the matcher**

In `src/cofounder_agent/modules/content/affiliate_links.py`, replace `_link_first_mention` and `inject_affiliate_links`, and add `_resolve_link` + `load_link_last_used`. Add `import random` to the top-level imports.

```python
def _find_eligible_span(line: str, keyword: str) -> tuple[int, int] | None:
    """First eligible whole-word occurrence of ``keyword`` in ``line``.

    Eligible = not preceded/followed by a word char, ``/``, ``[`` or ``]``
    (so it won't fire inside an existing ``[kw](url)`` or a ``/go/kw`` path)
    and not inside an inline-code span.
    """
    pattern = re.compile(r"(?<![\w`/\[\]])(" + re.escape(keyword) + r")(?![\w`\]])")
    for m in pattern.finditer(line):
        if _inside_inline_code(line, m.start()):
            continue
        return m.start(), m.end()
    return None


def _resolve_link(
    candidates: list[AffiliateLink], keyword: str, markdown: str,
    last_used: dict[str, str], rng: Any,
) -> AffiliateLink:
    """Pick which link wins a keyword shared by 2+ active links.

    Tier 1 (co-occurrence): does exactly one candidate have another of ITS
    OWN keywords also present elsewhere in the post? Tier 2 (LRU fallback):
    least-recently-injected into a published post, ties broken randomly.
    Mirrors modules/content/stages/source_featured_image.py's
    _select_style_lru.
    """
    if len(candidates) == 1:
        return candidates[0]
    corroborated = [
        c for c in candidates
        if any(kw in markdown for kw in c.keywords if kw != keyword)
    ]
    if len(corroborated) == 1:
        return corroborated[0]
    oldest = min(last_used.get(c.code, "") for c in candidates)
    tied = [c for c in candidates if last_used.get(c.code, "") == oldest]
    return rng.choice(tied)


def inject_affiliate_links(
    markdown: str,
    links: list[AffiliateLink],
    *,
    base: str = "/go",
    cap: int = 3,
    last_used: dict[str, str] | None = None,
    rng: Any = random,
) -> tuple[str, list[str]]:
    """Inject up to ``cap`` affiliate links, first-mention only per link.

    Keywords are matched across the WHOLE active-link catalog (not per-link)
    and tried longest-first, so a more specific phrase wins over a shorter
    one that would otherwise match the same text. When a matched keyword is
    shared by 2+ links, ``_resolve_link`` picks the winner. Skips fenced code
    blocks, heading lines, inline code, and existing links. Deterministic
    given a fixed ``last_used``/``rng`` (idempotent: a re-run finds the
    keyword already inside a link and skips it).
    """
    if not markdown or not links or cap <= 0:
        return markdown, []
    last_used = last_used or {}

    keyword_to_links: dict[str, list[AffiliateLink]] = {}
    for link in links:
        for kw in link.keywords:
            keyword_to_links.setdefault(kw, []).append(link)
    ordered_keywords = sorted(keyword_to_links, key=len, reverse=True)

    used: set[str] = set()
    injected: list[str] = []
    consumed_keywords: set[str] = set()
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
        for kw in ordered_keywords:
            if kw in consumed_keywords or len(injected) >= cap:
                continue
            candidates = [l for l in keyword_to_links[kw] if l.code not in used]
            if not candidates:
                continue
            span = _find_eligible_span(line, kw)
            if span is None:
                continue
            link = _resolve_link(candidates, kw, markdown, last_used, rng)
            start, end = span
            text = link.display_text or kw
            url = f"{base.rstrip('/')}/{link.code}"
            line = line[:start] + f"[{text}]({url})" + line[end:]
            used.add(link.code)
            injected.append(link.code)
            consumed_keywords.add(kw)
        out_lines.append(line)
    return "\n".join(out_lines), injected


async def load_link_last_used(pool: Any) -> dict[str, str]:
    """Map affiliate code -> ISO timestamp of its most recent PUBLISHED-post
    mention, for LRU tie-breaking when multiple links share a keyword.
    Mirrors _load_style_last_used in
    modules/content/stages/source_featured_image.py (scans existing posts,
    no new tracking column)."""
    rows = await pool.fetch(
        "SELECT al.code, MAX(p.published_at) AS last_used "
        "FROM affiliate_links al "
        "LEFT JOIN posts p "
        "  ON p.body LIKE '%/go/' || al.code || '%' AND p.status = 'published' "
        "GROUP BY al.code"
    )
    return {r["code"]: (r["last_used"].isoformat() if r["last_used"] else "") for r in rows}
```

Remove the old `_link_first_mention` function entirely (replaced by
`_find_eligible_span` + `_resolve_link`). Add `"load_link_last_used"` to
`__all__`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `<venv-python> -m pytest tests/unit/modules/content/test_affiliate_links_service.py -o addopts="" -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/affiliate_links.py src/cofounder_agent/tests/unit/modules/content/test_affiliate_links_service.py
git commit -m "feat(affiliate): shared-keyword matching via co-occurrence + LRU tie-break"
```

---

### Task 4: Atom wiring — `content_inject_affiliate_links.py` computes `last_used`

**Files:**

- Modify: `src/cofounder_agent/modules/content/atoms/content_inject_affiliate_links.py`
- Modify: `src/cofounder_agent/tests/unit/services/atoms/test_content_inject_affiliate_links.py`

**Interfaces:**

- Consumes: `list_active`, `load_link_last_used`, `inject_affiliate_links` (Tasks 2-3).
- Produces: no change to the atom's public contract (`run(state) -> {"content": ...} | {}`).

- [ ] **Step 1: Write the failing atom tests**

Replace `src/cofounder_agent/tests/unit/services/atoms/test_content_inject_affiliate_links.py` in full:

```python
"""Unit tests for the content.inject_affiliate_links atom."""

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
    def __init__(self, rows, last_used_rows=None):
        self._rows = rows
        self._last_used_rows = last_used_rows or []

    async def fetch(self, sql, *a, **k):
        if "posts" in sql:
            return self._last_used_rows
        return self._rows


def _db(rows, last_used_rows=None):
    class _DB:  # mimics state["database_service"].pool
        pool = _Pool(rows, last_used_rows)

    return _DB()


async def test_noop_when_disabled():
    state = {"content": "We use Mercury.", "site_config": _Cfg(on=False),
             "database_service": _db([])}
    assert await atom.run(state) == {}


async def test_injects_when_enabled():
    rows = [{"code": "mercury", "url": "https://x", "display_text": "Mercury",
             "platform": "", "keywords": ["Mercury"]}]
    state = {"content": "We use Mercury for banking.", "site_config": _Cfg(),
             "database_service": _db(rows)}
    out = await atom.run(state)
    assert out["content"] == "We use [Mercury](/go/mercury) for banking."


async def test_noop_when_no_pool():
    state = {"content": "We use Mercury.", "site_config": _Cfg()}
    assert await atom.run(state) == {}


async def test_noop_when_no_links():
    state = {"content": "We use Mercury.", "site_config": _Cfg(),
             "database_service": _db([])}
    assert await atom.run(state) == {}


async def test_computes_last_used_when_two_or_more_active_links():
    rows = [
        {"code": "a", "url": "https://x", "display_text": "A", "platform": "", "keywords": ["Corsair"]},
        {"code": "b", "url": "https://y", "display_text": "B", "platform": "", "keywords": ["Corsair"]},
    ]
    last_used_rows = [{"code": "a", "last_used": None}, {"code": "b", "last_used": None}]
    state = {"content": "I love my Corsair gear.", "site_config": _Cfg(),
             "database_service": _db(rows, last_used_rows)}
    out = await atom.run(state)
    assert out["content"] in (
        "I love my [A](/go/a) gear.", "I love my [B](/go/b) gear.",
    )  # either is valid — both never-used, tie breaks randomly


async def test_skips_last_used_query_for_single_active_link():
    rows = [{"code": "mercury", "url": "https://x", "display_text": "Mercury",
             "platform": "", "keywords": ["Mercury"]}]

    class _NoLastUsedPool:
        async def fetch(self, sql, *a, **k):
            assert "posts" not in sql  # must never run the LRU query for 1 link
            return rows

    class _DB:
        pool = _NoLastUsedPool()

    state = {"content": "We use Mercury.", "site_config": _Cfg(), "database_service": _DB()}
    out = await atom.run(state)
    assert out["content"] == "We use [Mercury](/go/mercury)."


def test_atom_metadata():
    assert atom.ATOM_META.name == "content.inject_affiliate_links"
    assert atom.ATOM_META.requires == ("content",)
    assert atom.ATOM_META.produces == ("content",)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `<venv-python> -m pytest tests/unit/services/atoms/test_content_inject_affiliate_links.py -o addopts="" -v`

Expected: FAIL — `test_injects_when_enabled` fails on the old row shape (`keyword` vs `keywords`); the two new tests fail/error (atom doesn't call `load_link_last_used` yet).

- [ ] **Step 3: Wire the atom**

In `src/cofounder_agent/modules/content/atoms/content_inject_affiliate_links.py`, replace the body of `run()` from the `list_active` import line onward:

```python
    from modules.content.affiliate_links import (
        inject_affiliate_links, list_active, load_link_last_used,
    )

    try:
        links = await list_active(pool)
    except Exception as exc:  # noqa: BLE001 — DB blip → pass content through
        logger.debug("[content.inject_affiliate_links] list_active failed: %s", exc)
        return {}
    if not links:
        return {}

    last_used: dict[str, str] = {}
    if len(links) >= 2:
        try:
            last_used = await load_link_last_used(pool)
        except Exception as exc:  # noqa: BLE001 — LRU signal is best-effort
            logger.debug("[content.inject_affiliate_links] load_link_last_used failed: %s", exc)

    new_content, injected = inject_affiliate_links(
        content, links, base=base, cap=cap, last_used=last_used,
    )
    if not injected:
        return {}
    logger.info(
        "[content.inject_affiliate_links] injected %d link(s) (task=%s): %s",
        len(injected), str(state.get("task_id") or "?")[:8], ", ".join(injected),
    )
    return {"content": new_content}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `<venv-python> -m pytest tests/unit/services/atoms/test_content_inject_affiliate_links.py -o addopts="" -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/modules/content/atoms/content_inject_affiliate_links.py src/cofounder_agent/tests/unit/services/atoms/test_content_inject_affiliate_links.py
git commit -m "feat(affiliate): wire LRU last-used lookup into the injection atom"
```

---

### Task 5: Settings defaults + prompt catalog entry for keyword derivation

**Files:**

- Modify: `src/cofounder_agent/services/settings_defaults.py`
- Modify: `src/cofounder_agent/skills/content/utility/SKILL.md`
- Modify: `src/cofounder_agent/tests/unit/services/test_prompt_fallback_drift.py`

**Interfaces:**

- Produces: `app_settings` keys `affiliate_import_llm_model` (default `''`) and `affiliate_import_llm_timeout_seconds` (default `'60'`); prompt catalog key `task.affiliate_derive_keywords`.

- [ ] **Step 1: Add the settings defaults**

In `src/cofounder_agent/services/settings_defaults.py`, insert immediately after the existing `'plugin.job.sync_affiliate_clicks.interval_seconds': '300',` line:

```python
    # Model pin for the CSV-import keyword/display-text derivation
    # (task.affiliate_derive_keywords). why: empty -> structured_extraction_model
    # (a JSON-reliable instruct model). Local by default.
    'affiliate_import_llm_model': '',
    # why: per-row timeout for the derivation call (keep short — one row at a time).
    'affiliate_import_llm_timeout_seconds': '60',
```

- [ ] **Step 2: Add the SKILL.md prompt entry**

In `src/cofounder_agent/skills/content/utility/SKILL.md`, add to the YAML frontmatter `prompts:` list (after the existing `task.utility_json_conversion` entry):

```yaml
- key: task.affiliate_derive_keywords
  output_format: json
  description: 'Derive a short display name + keyword aliases for an affiliate-catalog product from its long marketing title + description -> {display_text, keywords} JSON. Used by `poindexter affiliate import-csv`.'
```

Add to the markdown body (after the existing `## task.utility_json_conversion` section):

````markdown
## task.affiliate_derive_keywords

```text
You are naming and tagging a product for an affiliate-link catalog.

Given the product title and description below, respond with ONLY a JSON
object: {{"display_text": "<short human name, 2-6 words>", "keywords":
["<alias 1>", "<alias 2>", ...]}}

- display_text: a short, natural name a reader would recognize (not the
  full marketing title).
- keywords: 3 to 6 short phrases (1-4 words each) that might plausibly
  appear in prose referring to this product - brand names, model numbers,
  common nicknames. Avoid generic single words that could describe many
  products.

Title: {title}
Description: {description}
```
````

- [ ] **Step 3: Write the failing drift-guard test case**

This test references `modules.content.affiliate_import`, which doesn't exist
yet (Task 6) — so this step's test will fail with an `ImportError` until
Task 6 lands. That's expected: add the case now so Task 6 is TDD'd against
it, and re-run this specific test at the end of Task 6 to confirm it passes.

In `src/cofounder_agent/tests/unit/services/test_prompt_fallback_drift.py`,
add a resolver function (near the other `_*()` helpers):

```python
def _affiliate_derive_keywords():
    from modules.content import affiliate_import as m

    return m._resolve_prompt(title="Widget Pro 9000", description="A great widget.")
```

Add to `_CASES` (append to the list):

```python
    ("affiliate_derive_keywords", "task.affiliate_derive_keywords", _affiliate_derive_keywords),
```

- [ ] **Step 4: Run the new case to verify it currently fails on the missing module**

Run: `<venv-python> -m pytest tests/unit/services/test_prompt_fallback_drift.py -o addopts="" -k affiliate_derive_keywords -v`

Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'modules.content.affiliate_import'` (fixed in Task 6).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/settings_defaults.py src/cofounder_agent/skills/content/utility/SKILL.md src/cofounder_agent/tests/unit/services/test_prompt_fallback_drift.py
git commit -m "feat(affiliate): add settings defaults + prompt catalog entry for keyword derivation"
```

---

### Task 6: CSV import module (`affiliate_import.py`)

**Files:**

- Create: `src/cofounder_agent/modules/content/affiliate_import.py`
- Create: `src/cofounder_agent/tests/unit/modules/content/test_affiliate_import.py`

**Interfaces:**

- Consumes: `add_link`, `set_active` (Task 2); `resolve_structured_model`, `ollama_chat_text` (`services.llm_text`, pre-existing); `task.affiliate_derive_keywords` prompt (Task 5).
- Produces: `slugify_code(product_name, *, max_words=6, max_len=60) -> str`; `async def import_csv(pool, csv_path, *, site_config, force=False) -> ImportReport`; `ImportReport` (with `.created`/`.skipped`/`.errors` properties over `list[ImportRowResult]`); `ImportRowResult(code, product_name, status, display_text="", keywords=[], detail="")`.

- [ ] **Step 1: Write the failing tests**

Create `src/cofounder_agent/tests/unit/modules/content/test_affiliate_import.py`:

```python
"""Unit tests for CSV bulk-import (no real DB, no real LLM)."""

from __future__ import annotations

import csv
from unittest.mock import AsyncMock, patch

import pytest

from modules.content.affiliate_import import (
    _derive_display_and_keywords, _map_category, _map_is_active, import_csv, slugify_code,
)


def test_slugify_code_is_deterministic():
    name = "ASUS ROG Astral NVIDIA GeForce RTX 5090 32GB GDDR7 OC Edition"
    assert slugify_code(name) == slugify_code(name)
    assert slugify_code(name) == "asus-rog-astral-nvidia-geforce-rtx"


def test_slugify_code_strips_punctuation_and_truncates():
    assert slugify_code("Corsair HX1500i (2025) Fully Modular!!") == "corsair-hx1500i-2025-fully-modular"


@pytest.mark.parametrize("status,expected", [
    ("Active", True), ("active", True), ("ACTIVE", True),
    ("Inactive", False), ("Paused", False), ("", False),
])
def test_map_is_active(status, expected):
    assert _map_is_active(status) is expected


@pytest.mark.parametrize("category,expected", [
    ("Service", "service"), ("service", "service"),
    ("Hardware", "product"), ("", "product"), ("Anything Else", "product"),
])
def test_map_category(category, expected):
    assert _map_category(category) == expected


class _RaisingSiteConfig:
    def get(self, key, default=""):
        return default

    def get_int(self, key, default=0):
        return default


async def test_derive_display_and_keywords_fails_open_on_llm_error():
    with patch(
        "modules.content.affiliate_import.ollama_chat_text",
        AsyncMock(side_effect=RuntimeError("ollama unreachable")),
    ):
        display_text, keywords = await _derive_display_and_keywords(
            title="Widget Pro 9000 Long Marketing Title Here",
            description="x", site_config=_RaisingSiteConfig(), pool=object(),
        )
    assert display_text == "Widget Pro 9000 Long Marketing Title Here"[:60]
    assert keywords == []


class _FakePool:
    def __init__(self, existing_codes=()):
        self._existing = set(existing_codes)

    async def fetchval(self, sql, code):
        return 1 if code in self._existing else None


def _write_csv(tmp_path, rows):
    path = tmp_path / "sheet.csv"
    fieldnames = [
        "Status", "Product Name", "Category", "Platform",
        "Commission Rate", "Affiliate Link", "Description", "Promo Code",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{k: "" for k in fieldnames}, **row})
    return str(path)


async def test_import_creates_new_row_with_llm_keywords(tmp_path):
    csv_path = _write_csv(tmp_path, [{
        "Status": "Active", "Product Name": "Widget Pro 9000",
        "Category": "Hardware", "Platform": "Amazon",
        "Affiliate Link": "https://amazon.com/x", "Description": "A great widget.",
    }])
    pool = _FakePool()
    calls = {}

    async def _fake_add_link(pool, **kwargs):
        calls["add_link"] = kwargs

    async def _fake_set_active(pool, code, active):
        calls["set_active"] = (code, active)
        return True

    with patch(
        "modules.content.affiliate_import._derive_display_and_keywords",
        AsyncMock(return_value=("Widget Pro", ["Widget Pro", "Pro 9000"])),
    ), patch("modules.content.affiliate_links.add_link", _fake_add_link), \
       patch("modules.content.affiliate_links.set_active", _fake_set_active):
        report = await import_csv(pool, csv_path, site_config=object())

    assert len(report.created) == 1
    assert calls["add_link"]["keywords"] == ["Widget Pro", "Pro 9000"]
    assert calls["add_link"]["platform"] == "Amazon"
    assert calls["add_link"]["category"] == "product"
    assert calls["set_active"] == ("widget-pro-9000", True)


async def test_import_skips_existing_code_by_default(tmp_path):
    csv_path = _write_csv(tmp_path, [{
        "Status": "Active", "Product Name": "Widget Pro 9000", "Description": "",
    }])
    pool = _FakePool(existing_codes=["widget-pro-9000"])
    report = await import_csv(pool, csv_path, site_config=object())
    assert len(report.skipped) == 1
    assert report.created == []


async def test_import_force_overwrites_existing_code(tmp_path):
    csv_path = _write_csv(tmp_path, [{
        "Status": "Active", "Product Name": "Widget Pro 9000", "Description": "Updated.",
    }])
    pool = _FakePool(existing_codes=["widget-pro-9000"])
    calls = {}

    async def _fake_add_link(pool, **kwargs):
        calls["add_link"] = kwargs

    async def _fake_set_active(pool, code, active):
        return True

    with patch(
        "modules.content.affiliate_import._derive_display_and_keywords",
        AsyncMock(return_value=("Widget Pro", ["Widget"])),
    ), patch("modules.content.affiliate_links.add_link", _fake_add_link), \
       patch("modules.content.affiliate_links.set_active", _fake_set_active):
        report = await import_csv(pool, csv_path, site_config=object(), force=True)

    assert len(report.created) == 1
    assert calls["add_link"] is not None


async def test_import_uses_fallback_keyword_when_derivation_returns_none(tmp_path):
    csv_path = _write_csv(tmp_path, [{
        "Status": "Active", "Product Name": "Widget Pro 9000", "Description": "x",
    }])
    pool = _FakePool()
    calls = {}

    async def _fake_add_link(pool, **kwargs):
        calls["add_link"] = kwargs

    async def _fake_set_active(pool, code, active):
        return True

    with patch(
        "modules.content.affiliate_import._derive_display_and_keywords",
        AsyncMock(return_value=("Widget Pro 9000", [])),
    ), patch("modules.content.affiliate_links.add_link", _fake_add_link), \
       patch("modules.content.affiliate_links.set_active", _fake_set_active):
        report = await import_csv(pool, csv_path, site_config=object())

    assert len(report.created) == 1
    assert calls["add_link"]["keywords"] == ["Widget Pro 9000"]


async def test_import_add_link_failure_produces_error_row_not_batch_abort(tmp_path):
    csv_path = _write_csv(tmp_path, [
        {"Status": "Active", "Product Name": "Widget One", "Description": "x"},
        {"Status": "Active", "Product Name": "Widget Two", "Description": "y"},
    ])
    pool = _FakePool()

    async def _failing_add_link(pool, **kwargs):
        if kwargs["code"] == "widget-one":
            raise RuntimeError("db blip")

    async def _fake_set_active(pool, code, active):
        return True

    with patch(
        "modules.content.affiliate_import._derive_display_and_keywords",
        AsyncMock(return_value=("Name", ["kw"])),
    ), patch("modules.content.affiliate_links.add_link", _failing_add_link), \
       patch("modules.content.affiliate_links.set_active", _fake_set_active):
        report = await import_csv(pool, csv_path, site_config=object())

    assert len(report.errors) == 1
    assert report.errors[0].code == "widget-one"
    assert len(report.created) == 1
    assert report.created[0].code == "widget-two"


async def test_import_skips_blank_product_name(tmp_path):
    csv_path = _write_csv(tmp_path, [{"Status": "Active", "Product Name": "", "Description": "x"}])
    report = await import_csv(_FakePool(), csv_path, site_config=object())
    assert report.rows == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `<venv-python> -m pytest tests/unit/modules/content/test_affiliate_import.py -o addopts="" -v`

Expected: FAIL/ERROR — `modules.content.affiliate_import` doesn't exist yet.

- [ ] **Step 3: Write the module**

Create `src/cofounder_agent/modules/content/affiliate_import.py`:

```python
"""CSV bulk-import for affiliate_links (poindexter affiliate import-csv).

Insert-only by default: a CSV row whose derived `code` already exists in the
DB is skipped (never silently overwrites a hand-curated row) unless --force.
`is_active` is set directly from the CSV's own Status column. A local LLM
proposes display_text + keywords from title+description; failures fall
open per-row (the row still gets created, using the product name itself as
a fallback keyword) so one bad row never aborts the whole import. See
docs/superpowers/specs/2026-07-12-affiliate-multi-keyword-platform-design.md.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from services.llm_text import ollama_chat_text, resolve_structured_model

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_PROMPT_KEY = "task.affiliate_derive_keywords"
_PROMPT_FALLBACK = """\
You are naming and tagging a product for an affiliate-link catalog.

Given the product title and description below, respond with ONLY a JSON
object: {{"display_text": "<short human name, 2-6 words>", "keywords":
["<alias 1>", "<alias 2>", ...]}}

- display_text: a short, natural name a reader would recognize (not the
  full marketing title).
- keywords: 3 to 6 short phrases (1-4 words each) that might plausibly
  appear in prose referring to this product - brand names, model numbers,
  common nicknames. Avoid generic single words that could describe many
  products.

Title: {title}
Description: {description}
"""


def slugify_code(product_name: str, *, max_words: int = 6, max_len: int = 60) -> str:
    """Deterministic, non-LLM code derivation -- stable across re-imports."""
    words = product_name.strip().split()[:max_words]
    slug = _SLUG_RE.sub("-", " ".join(words).lower()).strip("-")
    return slug[:max_len].rstrip("-")


def _map_category(csv_category: str) -> str:
    return "service" if csv_category.strip().lower() == "service" else "product"


def _map_is_active(csv_status: str) -> bool:
    return csv_status.strip().lower() == "active"


@dataclass
class ImportRowResult:
    code: str
    product_name: str
    status: str  # "created" | "skipped_exists" | "error"
    display_text: str = ""
    keywords: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class ImportReport:
    rows: list[ImportRowResult] = field(default_factory=list)

    @property
    def created(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "created"]

    @property
    def skipped(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "skipped_exists"]

    @property
    def errors(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "error"]


def _resolve_prompt(*, title: str, description: str) -> str:
    try:
        from services.prompt_manager import get_prompt_manager
        return get_prompt_manager().get_prompt(
            _PROMPT_KEY, title=title, description=description,
        )
    except Exception as exc:  # noqa: BLE001 — registry unreachable (bootstrap/test)
        logger.error("[affiliate_import] prompt lookup failed (%s) — inline fallback", exc)
        return _PROMPT_FALLBACK.format(title=title, description=description)


def _parse_llm_json(raw: str) -> dict:
    if not raw:
        return {}
    s = raw.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(s[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


async def _derive_display_and_keywords(
    *, title: str, description: str, site_config: Any, pool: Any,
) -> tuple[str, list[str]]:
    """LLM-assisted; fails open to (title[:60], []) on any error so one bad
    row never aborts the batch."""
    try:
        pin = (site_config.get("affiliate_import_llm_model", "") or "").strip() or None
        model = resolve_structured_model(pin, site_config=site_config)
        prompt = _resolve_prompt(title=title, description=description)
        timeout = site_config.get_int("affiliate_import_llm_timeout_seconds", 60)
        raw = await ollama_chat_text(
            prompt, model=model, site_config=site_config, pool=pool,
            tier="budget", timeout_setting="affiliate_import_llm_timeout_seconds",
            timeout_default=float(timeout), phase="affiliate_import_derive_keywords",
            think=False,
        )
        parsed = _parse_llm_json(raw)
        display_text = str(parsed.get("display_text") or "").strip() or title[:60]
        keywords = [str(k).strip() for k in (parsed.get("keywords") or []) if str(k).strip()]
        return display_text, keywords
    except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
        logger.warning("[affiliate_import] LLM derivation failed for %r: %s", title[:60], exc)
        return title[:60], []


async def import_csv(
    pool: Any, csv_path: str, *, site_config: Any, force: bool = False,
) -> ImportReport:
    report = ImportReport()
    from modules.content.affiliate_links import add_link, set_active

    with open(csv_path, newline="", encoding="utf-8") as f:
        for raw_row in csv.DictReader(f):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw_row.items()}
            product_name = row.get("Product Name", "")
            if not product_name:
                continue
            code = slugify_code(product_name)

            existing = await pool.fetchval("SELECT id FROM affiliate_links WHERE code = $1", code)
            if existing and not force:
                report.rows.append(
                    ImportRowResult(code=code, product_name=product_name, status="skipped_exists")
                )
                continue

            description = row.get("Description", "")
            display_text, keywords = await _derive_display_and_keywords(
                title=product_name, description=description,
                site_config=site_config, pool=pool,
            )
            try:
                await add_link(
                    pool, code=code, keywords=keywords or [product_name[:60]],
                    url=row.get("Affiliate Link", ""), display_text=display_text,
                    description=description,
                    category=_map_category(row.get("Category", "")),
                    platform=row.get("Platform", ""),
                )
                await set_active(pool, code, _map_is_active(row.get("Status", "")))
                report.rows.append(
                    ImportRowResult(
                        code=code, product_name=product_name, status="created",
                        display_text=display_text, keywords=keywords or [product_name[:60]],
                    )
                )
            except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
                report.rows.append(
                    ImportRowResult(code=code, product_name=product_name, status="error", detail=str(exc))
                )
    return report


__all__ = ["ImportReport", "ImportRowResult", "slugify_code", "import_csv"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `<venv-python> -m pytest tests/unit/modules/content/test_affiliate_import.py -o addopts="" -v`

Expected: all PASS.

- [ ] **Step 5: Re-run Task 5's drift-guard case to confirm it now passes**

Run: `<venv-python> -m pytest tests/unit/services/test_prompt_fallback_drift.py -o addopts="" -k affiliate_derive_keywords -v`

Expected: all 3 parametrized tests (`test_prompt_key_registered_from_skill`, `test_skill_default_matches_inline_fallback`, `test_fallback_logs_loud_when_registry_down`) PASS. If `test_skill_default_matches_inline_fallback` fails, the SKILL.md fenced-block body and `_PROMPT_FALLBACK` have drifted (usually a trailing-newline mismatch — see Task 5 Step 2's fenced block; the closing `\`\`\``must immediately follow`Description: {description}`with no blank line, matching`\_PROMPT_FALLBACK`'s closing `"""` on its own line).

- [ ] **Step 6: Commit**

```bash
git add src/cofounder_agent/modules/content/affiliate_import.py src/cofounder_agent/tests/unit/modules/content/test_affiliate_import.py
git commit -m "feat(affiliate): add CSV bulk-import with LLM-assisted keyword derivation"
```

---

### Task 7: CLI updates — repeatable `--keyword`, `--platform`, `list --all`, `enable --all`, `import-csv`

**Files:**

- Modify: `src/cofounder_agent/poindexter/cli/affiliate.py` (full rewrite)
- Modify: `src/cofounder_agent/tests/unit/poindexter/cli/test_affiliate_cli.py`

**Interfaces:**

- Consumes: `add_link`, `list_active`, `list_all`, `set_active`, `remove_link` (Task 2); `import_csv` (Task 6).

- [ ] **Step 1: Write the failing CLI tests**

Replace `src/cofounder_agent/tests/unit/poindexter/cli/test_affiliate_cli.py` in full:

```python
"""Guards that the `poindexter affiliate` group is registered + wired."""

import click
import pytest

from poindexter.cli.app import main


def test_affiliate_group_registered():
    assert "affiliate" in main.commands


def test_affiliate_subcommands():
    from poindexter.cli.affiliate import affiliate_group

    assert {"add", "list", "enable", "disable", "rm", "import-csv"} <= set(affiliate_group.commands)


def test_add_requires_category_and_description():
    from poindexter.cli.affiliate import add_cmd

    param_names = {p.name for p in add_cmd.params}
    assert {"category", "description"} <= param_names

    category_param = next(p for p in add_cmd.params if p.name == "category")
    assert category_param.required is True
    assert set(category_param.type.choices) == {"service", "product"}

    description_param = next(p for p in add_cmd.params if p.name == "description")
    assert description_param.required is True


def test_add_keyword_is_repeatable_and_required():
    from poindexter.cli.affiliate import add_cmd

    keyword_param = next(p for p in add_cmd.params if p.name == "keywords")
    assert keyword_param.multiple is True
    assert keyword_param.required is True


def test_add_has_optional_platform():
    from poindexter.cli.affiliate import add_cmd

    platform_param = next(p for p in add_cmd.params if p.name == "platform")
    assert platform_param.required is False
    assert platform_param.default == ""


def test_list_has_all_flag():
    from poindexter.cli.affiliate import list_cmd

    all_param = next(p for p in list_cmd.params if p.name == "show_all")
    assert all_param.is_flag is True


def test_enable_has_all_flag_and_optional_code():
    from poindexter.cli.affiliate import enable_cmd

    code_param = next(p for p in enable_cmd.params if p.name == "code")
    assert code_param.required is False
    all_param = next(p for p in enable_cmd.params if p.name == "enable_all")
    assert all_param.is_flag is True


def test_enable_validate_args_rejects_neither():
    from poindexter.cli.affiliate import _validate_enable_args

    with pytest.raises(click.UsageError):
        _validate_enable_args(None, False)


def test_enable_validate_args_rejects_both():
    from poindexter.cli.affiliate import _validate_enable_args

    with pytest.raises(click.UsageError):
        _validate_enable_args("mercury", True)


def test_enable_validate_args_accepts_code_only():
    from poindexter.cli.affiliate import _validate_enable_args

    _validate_enable_args("mercury", False)  # must not raise


def test_enable_validate_args_accepts_all_only():
    from poindexter.cli.affiliate import _validate_enable_args

    _validate_enable_args(None, True)  # must not raise


def test_import_csv_requires_existing_path():
    from poindexter.cli.affiliate import import_csv_cmd

    path_param = next(p for p in import_csv_cmd.params if p.name == "csv_path")
    assert path_param.type.exists is True


def test_import_csv_has_force_flag():
    from poindexter.cli.affiliate import import_csv_cmd

    force_param = next(p for p in import_csv_cmd.params if p.name == "force")
    assert force_param.is_flag is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `<venv-python> -m pytest tests/unit/poindexter/cli/test_affiliate_cli.py -o addopts="" -v`

Expected: FAIL — `add_cmd` has no `keywords`/`platform` params yet, no `_validate_enable_args`, no `import_csv_cmd`.

- [ ] **Step 3: Rewrite the CLI module**

Replace `src/cofounder_agent/poindexter/cli/affiliate.py` in full:

```python
"""`poindexter affiliate` — manage affiliate-link rows (DB-only real URLs).

Thin adapter over ``modules.content.affiliate_links`` / ``affiliate_import``
(no inline SQL here, per the transport-adapter-contract ADR). Real referral
URLs never live in source; operators add them here.
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


async def _make_site_config(pool):
    from services.site_config import SiteConfig

    site_config = SiteConfig(pool=pool)
    try:
        await site_config.load(pool)
    except Exception:
        # silent-ok: best-effort settings load — callers still work pool-only
        # (just without DB-loaded settings) if this fails.
        pass
    return site_config


async def _republish(pool) -> None:
    """Republish both affiliate JSON exports + bust the referrals ISR tag.

    Best-effort: a republish failure must not fail the CLI command that
    already wrote the DB row (the row is the source of truth; a stale
    export self-heals on the next publish or on-demand rebuild).
    """
    from services.revalidation_service import trigger_nextjs_revalidation
    from services.static_export_service import republish_affiliate_exports

    try:
        site_config = await _make_site_config(pool)
        await republish_affiliate_exports(pool, site_config=site_config)
        await trigger_nextjs_revalidation(tags=["referrals"], site_config=site_config)
    except Exception as e:  # noqa: BLE001 — never fail the CLI write over a republish hiccup
        click.secho(f"  (warning: republish failed: {e})", fg="yellow")


@click.group(name="affiliate")
def affiliate_group() -> None:
    """Manage affiliate links (add/list/enable/disable/rm/import-csv)."""


@affiliate_group.command(name="add")
@click.option("--code", required=True, help="Stable slug for /go/<code> (e.g. mercury).")
@click.option(
    "--keyword", "keywords", required=True, multiple=True,
    help="Body phrase to match (repeatable — pass multiple times for aliases).",
)
@click.option("--url", required=True, help="Real merchant/referral URL.")
@click.option("--display-text", default="", help="Link text (defaults to the matched keyword).")
@click.option("--program", default="", help="Program label (e.g. 'Mercury Referral').")
@click.option(
    "--category", required=True, type=click.Choice(["service", "product"]),
    help="Section this link appears in on /referrals.",
)
@click.option("--description", required=True, help="Reader-facing description shown on /referrals.")
@click.option("--platform", default="", help="Free-text tracking label (e.g. Amazon, direct).")
def add_cmd(code, keywords, url, display_text, program, category, description, platform):
    """Add or update an affiliate link."""
    from modules.content.affiliate_links import add_link

    async def _go():
        pool = await _connect()
        try:
            await add_link(
                pool, code=code, keywords=list(keywords), url=url,
                display_text=display_text, program=program,
                description=description, category=category, platform=platform,
            )
            await _republish(pool)
        finally:
            await pool.close()

    asyncio.run(_go())
    click.secho(f"Added/updated affiliate link '{code}'.", fg="green")


@affiliate_group.command(name="list")
@click.option("--all", "show_all", is_flag=True, help="Include inactive links.")
def list_cmd(show_all):
    """List affiliate links (active only, unless --all)."""
    from modules.content.affiliate_links import list_active, list_all

    async def _go():
        pool = await _connect()
        try:
            return await (list_all(pool) if show_all else list_active(pool))
        finally:
            await pool.close()

    links = asyncio.run(_go())
    if not links:
        click.echo("No affiliate links." if show_all else "No active affiliate links.")
        return
    for lk in links:
        kw = ", ".join(lk.keywords)
        platform = f" [{lk.platform}]" if lk.platform else ""
        click.echo(f"  {lk.code:16} {kw:40}{platform} → {lk.url}")


def _validate_enable_args(code, enable_all) -> None:
    if enable_all == bool(code):
        raise click.UsageError("Provide exactly one of CODE or --all.")


@affiliate_group.command(name="enable")
@click.argument("code", required=False)
@click.option("--all", "enable_all", is_flag=True, help="Enable every currently-inactive link.")
def enable_cmd(code, enable_all):
    """Enable (activate) one link, or every inactive link with --all."""
    _validate_enable_args(code, enable_all)
    if enable_all:
        _set_active_all(True)
    else:
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


def _set_active_all(active: bool):
    from modules.content.affiliate_links import list_all, set_active

    async def _go():
        pool = await _connect()
        try:
            targets = await list_all(pool)
            changed = []
            for lk in targets:
                if await set_active(pool, lk.code, active):
                    changed.append(lk.code)
            if changed:
                await _republish(pool)
            return changed
        finally:
            await pool.close()

    changed = asyncio.run(_go())
    click.secho(
        f"{'Enabled' if active else 'Disabled'} {len(changed)} link(s): "
        f"{', '.join(changed) or '(none)'}",
        fg="green",
    )


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


@affiliate_group.command(name="import-csv")
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--force", is_flag=True, help="Overwrite existing rows instead of skipping them.")
def import_csv_cmd(csv_path, force):
    """Bulk-import affiliate links from a spreadsheet export."""
    from modules.content.affiliate_import import import_csv

    async def _go():
        pool = await _connect()
        try:
            site_config = await _make_site_config(pool)
            report = await import_csv(pool, csv_path, site_config=site_config, force=force)
            if report.created:
                await _republish(pool)
            return report
        finally:
            await pool.close()

    report = asyncio.run(_go())
    for r in report.rows:
        if r.status == "created":
            kw = ", ".join(r.keywords)
            click.secho(f"  created  {r.code:24} {r.display_text} [{kw}]", fg="green")
        elif r.status == "skipped_exists":
            click.secho(f"  skipped  {r.code:24} (already exists — use --force to overwrite)", fg="yellow")
        else:
            click.secho(f"  error    {r.code:24} {r.detail}", fg="red")
    click.echo(
        f"\n{len(report.created)} created, {len(report.skipped)} skipped, "
        f"{len(report.errors)} errors."
    )


__all__ = ["affiliate_group"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `<venv-python> -m pytest tests/unit/poindexter/cli/test_affiliate_cli.py -o addopts="" -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/affiliate.py src/cofounder_agent/tests/unit/poindexter/cli/test_affiliate_cli.py
git commit -m "feat(affiliate): CLI repeatable --keyword/--platform, list/enable --all, import-csv"
```

---

### Task 8: Docs update (`docs/operations/affiliate-links.md`)

**Files:**

- Modify: `docs/operations/affiliate-links.md`

**Interfaces:**

- Consumes: nothing (documentation only) — reflects Tasks 1-7's final shape.

- [ ] **Step 1: Update the quick-start example**

Replace the `poindexter affiliate add` example in the "Quick start" section:

```bash
# 1. Seed a link (real URL stays in the DB, never in git):
poindexter affiliate add \
  --code mercury \
  --keyword Mercury \
  --url "https://mercury.com/r/<your-referral>" \
  --program "Mercury Referral" \
  --category service \
  --description "Business banking we use daily."

# Pass --keyword multiple times for aliases (e.g. a product with several names):
#   --keyword "RTX 5090" --keyword "ASUS ROG Astral" --keyword "5090 GPU"

# 2. Review what's live:
poindexter affiliate list

# 3. Turn injection on (off by default):
poindexter settings set affiliate_injection_enabled true
```

- [ ] **Step 2: Update the CLI reference table**

Replace the "CLI reference" table:

```markdown
### CLI reference

| Command                                                                                                                  | Effect                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `poindexter affiliate add --code --keyword ... --url --category --description [--display-text] [--program] [--platform]` | Add or update a link (idempotent on `code`). `--keyword` is repeatable — pass it multiple times for aliases. |
| `poindexter affiliate list [--all]`                                                                                      | List links (active only by default; `--all` includes inactive).                                              |
| `poindexter affiliate enable <code> \| --all`                                                                            | Re-activate one link, or every inactive link at once.                                                        |
| `poindexter affiliate disable <code>`                                                                                    | Deactivate without deleting (stops new injections + drops it from the published map).                        |
| `poindexter affiliate rm <code>`                                                                                         | Delete a link.                                                                                               |
| `poindexter affiliate import-csv <path> [--force]`                                                                       | Bulk-import from a spreadsheet export (see "Bulk import" below).                                             |

`--keyword` is a phrase matched in body prose (case-sensitive on the first
letter as written) — pass it multiple times on one `add` call to give a
link several aliases; different links can share a keyword (see "How
injection works" below for how that's resolved). `--code` is the stable
slug used in `/go/<code>` and as the click-tracking key — keep it short and
URL-safe. `--platform` is a free-text label (e.g. `Amazon`, `direct`) for
your own tracking, not used by the matcher.
```

- [ ] **Step 3: Update "How injection works"**

Append after the existing bullet list in that section (before "It is
deterministic (no LLM) and **fails open**..."):

```markdown
When a link's keyword is shared with another active link (e.g. "Corsair" on
several different Corsair products), the matcher resolves which one wins a
given mention in two steps, both free and deterministic: first, does one of
the tied candidates have another of _its own_ keywords also present
elsewhere in the same post (e.g. the post also says "HX1500i" — that
candidate wins)? If that doesn't disambiguate, it rotates to whichever
candidate was least-recently linked into a published post, so exposure
spreads across your catalog instead of one product always winning a generic
mention.
```

- [ ] **Step 4: Update the data model section**

Replace:

```markdown
## Data model

- `affiliate_links` — `id, code (UNIQUE), url, display_text, program,
is_active, clicks, created_at, updated_at, description, category, platform`.
  The published map and the injector both read `WHERE is_active = true`.
- `affiliate_link_keywords` — `id, link_id (FK -> affiliate_links, ON DELETE
CASCADE), keyword, created_at`, `UNIQUE(link_id, keyword)` — one row per
  keyword/alias a link matches on. Uniqueness is scoped per-link, not
  global: different links CAN share a keyword string.
- `affiliate_link_clicks` — `id, code, post_slug, referrer, country, user_agent,
created_at`. One row per synced click; `post_slug` is derived from the
  referrer.
```

- [ ] **Step 5: Add a "Bulk import from a spreadsheet" section**

Insert a new section after "Rollout checklist" (or after "Settings", your
choice of a natural spot — before "Rollout checklist" reads best since it's
day-to-day usage, not one-time setup):

````markdown
## Bulk import from a spreadsheet

`poindexter affiliate import-csv <path> [--force]` reads a CSV export with
these columns: `Status, Product Name, Category, Platform, Commission Rate,
Affiliate Link, Description, Promo Code` (`Commission Rate` and `Promo Code`
are read but not stored).

For each row:

- `code` is derived deterministically from `Product Name` (never
  LLM-touched, so re-running the import on an unchanged sheet always maps
  to the same code).
- If that `code` already exists, the row is **skipped** unless `--force` is
  passed — this protects hand-curated rows (e.g. a real description you
  wrote) from being blanked out by a re-import.
- `display_text` and a keyword list are proposed by a local LLM from the
  title + description (`task.affiliate_derive_keywords`). On any LLM/JSON
  error the row still gets created, falling back to the product name itself
  as both the display text and sole keyword — nothing is silently dropped.
- `is_active` is set directly from the CSV's own `Status` column
  (`"Active"`, case-insensitive → active; anything else → inactive).
- `Category` maps to `service`/`product` (`"Service"` → `service`,
  everything else, including `"Hardware"` → `product`). `Platform` is
  copied verbatim into the new `platform` column.

Review the printed summary, then approve what you're happy with:

```bash
poindexter affiliate list --all       # review anything that landed inactive
poindexter affiliate enable --all     # approve everything at once, or...
poindexter affiliate enable <code>    # ...one at a time
```
````

````

- [ ] **Step 6: Commit**

```bash
git add docs/operations/affiliate-links.md
git commit -m "docs(affiliate): document multi-keyword, platform column, CSV import"
````

---

## Self-Review Notes

- **Spec coverage:** every section of
  `docs/superpowers/specs/2026-07-12-affiliate-multi-keyword-platform-design.md`
  maps to a task — schema (Task 1), CRUD (Task 2), matcher (Task 3), atom
  wiring (Task 4), settings/prompt (Task 5), CSV import (Task 6), CLI (Task
  7), docs (Task 8).
- **Resolved gap vs. the spec:** the spec's CSV-import flow said a row with
  an empty derived keyword list "still gets created" but didn't reconcile
  that `add_link()` requires at least one keyword. Task 6 resolves it with
  an explicit fallback (`keywords or [product_name[:60]]`) — the product
  name itself becomes the sole keyword when the LLM derivation comes back
  empty, satisfying both constraints. Covered by
  `test_import_uses_fallback_keyword_when_derivation_returns_none`.
- **Type/name consistency checked:** `AffiliateLink.keywords` (Task 2) is
  the field name used consistently through the matcher (Task 3), the atom
  (Task 4), and the importer (Task 6) — no `keyword`/`keywords` naming
  drift. `list_all` (Task 2) is used identically by `enable --all` (Task 7)
  and by the docs' `affiliate list --all` (Task 8).
- **Prompt drift guard sequencing:** Task 5 adds the `_CASES` entry before
  Task 6 creates the module it imports — deliberately left red in between
  (Step 4 of Task 5 documents the expected failure) so Task 6's Step 5
  closes the loop under TDD rather than the case being added retroactively
  untested.
