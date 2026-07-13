# Community Draft Assistant (WS2 · PR1: Reddit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (subagent delegation is disabled in this project — execute inline, sequentially). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the on-demand, CLI-first Reddit draft-assistant — a `poindexter community` command group that generates native, per-subreddit founder-voice value-posts from published blog posts, stores them for manual review/posting, and lets the operator curate subreddit profiles (CRUD + CSV round-trip). No auto-posting.

**Architecture:** Two new tables (`subreddit_profiles`, `community_post_drafts`) behind a single service module (`services/community_drafts.py`) plus a CSV importer (`services/subreddit_import.py`), a SKILL.md prompt pack, and a thin Click adapter group. Generation uses the local writer model for prose; the blog-link decision and all advisory warnings are computed **deterministically** in the service (not left to the LLM), which keeps them unit-testable. IndieHackers generation is deliberately deferred to PR2 — this PR builds the shared substrate + the Reddit generator.

**Tech Stack:** Python 3.13, asyncpg (Postgres), Click (CLI), the project's `ollama_chat_text` LLM seam, SKILL.md auto-discovered prompt packs, pytest (unit + `integration_db`).

**Spec:** [`docs/superpowers/specs/2026-07-13-community-draft-assistant-design.md`](../specs/2026-07-13-community-draft-assistant-design.md)

## Global Constraints

Every task's requirements implicitly include this section.

- **Migrations are DDL-only.** New tables/columns/indexes go in a `YYYYMMDD_HHMMSS_<slug>.py` migration with `async def up(pool)` / `async def down(pool)`. **Never** seed rows or `app_settings` in a migration — settings go in `services/settings_defaults.py` (`DEFAULTS` dict, applied every boot via `seed_all_defaults`).
- **No silent defaults.** Required config that is missing fails loud (raise / `emit_finding`), never a silent fallback. `resolve_local_model` already raises `ValueError` when `pipeline_writer_model` is unset — do not catch-and-default it.
- **Prose uses the writer model; JSON uses the structured model.** Founder-voice drafts are prose → `resolve_local_model(pin, site_config=...)` (reads `pipeline_writer_model`). Never `resolve_structured_model` here (this is not JSON).
- **Prompts live in SKILL.md packs**, category `social_media` (a valid `_CATEGORY_MAP` key — an invalid category silently drops the whole pack), `output_format: text`. Resolve via `get_prompt_manager().get_prompt(key, **kwargs)`. `template.format(**kwargs)` runs on the body, so any literal `{`/`}` in the prompt must be doubled (`{{`/`}}`).
- **CLI is a thin adapter.** No business logic or inline SQL in `poindexter/cli/community.py` — it delegates to `services/community_drafts.py` + `services/subreddit_import.py`. (`scripts/ci/adapter_purity_lint.py` enforces no net-new inline SQL in `poindexter/cli/`.)
- **Local LLM default.** No paid-API calls. `community_draft_model` empty → writer model.
- **Docs + tests ship with the code** (`feedback_docs_and_tests_default`). Architecture doc is **product-generic** — no operator identity, no real subreddit names, no internal issue refs (`feedback_no_operator_info_to_public_repo`, `feedback_no_internal_tracker_refs`). Real operator subreddit seeds land only in the stripped operator overlay, never OSS baseline (this PR seeds **zero** profiles).
- **No dummy data.** Tables ship empty; tests insert their own rows and roll them back.
- **Linear history / all changes via PR.** Squash-merge, no merge commits. Push to the existing draft PR #2449 on branch `claude/community-draft-assistant`.
- **The plan/spec docs commit with `[skip-public-sync]`** in the message. Code/test/settings/migration commits sync normally (the mirror filter strips `docs/superpowers/` regardless, so only the doc commits need the tag; but tag them for belt-and-suspenders).

### Test run command (fresh worktree has no venv)

A fresh worktree has no `.venv`. Use the **main checkout's** poetry venv interpreter, set `PYTHONPATH`, and pass `-o addopts=""` (the repo's default addopts include `--forked`, which needs a plugin the ad-hoc invocation lacks):

```bash
VENV="C:/Users/mattm/AppData/Local/pypoetry/Cache/virtualenvs/poindexter-backend-YHugfB---py3.13/Scripts/python.exe"
SRC="C:/Users/mattm/glad-labs-website/.claude/worktrees/indiehackers-devto-posting-27bb1a/src/cofounder_agent"
PYTHONPATH="$SRC" "$VENV" -m pytest "$SRC/tests/unit/services/test_community_drafts_crud.py" -o addopts="" -q
```

`integration_db` tests need a live Postgres; they self-skip when none is reachable. Run them the same way (the `test_pool` fixture provisions a disposable DB from the full migration tree).

---

## Service surface (defined once — every task consumes these exact signatures)

`services/community_drafts.py`:

```python
@dataclass
class SubredditProfile:
    subreddit: str
    enabled: bool = True
    content_types: list[str] = field(default_factory=list)   # classifier labels this sub accepts
    post_type: str = "text"          # 'text' | 'link' | 'either'
    self_promo: str = "strict"       # 'strict' | 'moderate' | 'ok'
    flair: str | None = None
    min_karma: int | None = None
    min_account_age_days: int | None = None
    rules_summary: str = ""
    tone_notes: str = ""
    cadence_cap_days: int | None = None

@dataclass
class CommunityDraft:
    id: int
    target: str                       # 'reddit:<sub>' | 'indiehackers'
    title: str | None
    body: str
    post_type: str
    source_post_id: str | None        # uuid as str
    warnings: list[str]
    status: str                       # 'draft' | 'posted' | 'discarded'
    posted_url: str | None
    model: str | None

# profiles
async def list_profiles(pool, *, enabled_only: bool = False) -> list[SubredditProfile]
async def get_profile(pool, subreddit: str) -> SubredditProfile | None
async def add_profile(pool, profile: SubredditProfile) -> bool          # INSERT ON CONFLICT DO NOTHING; True=created
async def update_profile(pool, profile: SubredditProfile) -> bool       # full-row UPDATE; True=matched
async def edit_profile(pool, subreddit: str, **changes) -> SubredditProfile  # load+merge non-None+update; raises KeyError
async def set_profile_enabled(pool, subreddit: str, enabled: bool) -> bool
async def remove_profile(pool, subreddit: str) -> bool

# content-type match
async def suggest_subreddits_for_post(pool, post_id: str) -> list[str]

# drafts
async def create_draft(pool, *, target: str, body: str, title: str | None = None,
                       post_type: str = "text", source_post_id: str | None = None,
                       warnings: list[str] | None = None, model: str | None = None) -> int
async def list_drafts(pool, *, status: str | None = None) -> list[CommunityDraft]
async def get_draft(pool, draft_id: int) -> CommunityDraft | None
async def edit_draft(pool, draft_id: int, *, title: str | None = None, body: str | None = None) -> bool
async def mark_posted(pool, draft_id: int, *, url: str) -> bool
async def discard_draft(pool, draft_id: int) -> bool

# reddit generation
def compute_warnings(profile: SubredditProfile) -> list[str]            # pure
def maybe_append_blog_link(body, *, post_type, slug, site_config) -> str  # pure
async def generate_reddit_draft(pool, *, post_id: str, subreddit: str, site_config) -> CommunityDraft
```

`services/subreddit_import.py`:

```python
@dataclass
class ImportRowResult:
    subreddit: str
    status: str                       # 'created' | 'updated' | 'skipped_exists' | 'error'
    detail: str = ""

@dataclass
class ImportReport:
    rows: list[ImportRowResult]
    # .created / .updated / .skipped / .errors properties

def parse_content_types_cell(raw: str) -> list[str]                     # ';'-delimited → list
def row_to_profile(row: dict) -> SubredditProfile                       # pure CSV-row → profile
def profiles_to_csv(profiles: list[SubredditProfile]) -> str           # pure
async def import_csv(pool, csv_path: str, *, force: bool = False) -> ImportReport
async def export_csv(pool) -> str
```

CSV column order (canonical — used by both import and export):
`subreddit, enabled, content_types, post_type, self_promo, flair, min_karma, min_account_age_days, rules_summary, tone_notes, cadence_cap_days`

---

## Task 1: Schema migration (both tables) + settings

**Files:**

- Create: `src/cofounder_agent/services/migrations/20260713_010000_add_community_draft_tables.py`
- Modify: `src/cofounder_agent/services/settings_defaults.py` (add 2 keys)
- Test: `src/cofounder_agent/tests/unit/services/test_community_settings_defaults.py`
- Test: `src/cofounder_agent/tests/integration_db/test_community_draft_tables.py`

**Interfaces:**

- Produces: the `subreddit_profiles` and `community_post_drafts` tables (columns per the spec DDL); settings `community_draft_model` (default `''`) and `community_draft_timeout_seconds` (default `'180'`).

> Note on settings: the spec lists `community_ih_lookback_days` too, but that is **only read by the PR2 IndieHackers generator**. Adding it now would create a zero-reader key (flagged by `ProbeZeroReaderSettingsJob`). Defer it to PR2, where it is first used. `community_draft_timeout_seconds` is added here (not in the spec's 2-row table) because `generate_reddit_draft` needs a DB-tunable LLM timeout rather than a hardcoded literal (`feedback_db_first_config`).

- [ ] **Step 1: Write the failing settings-defaults test**

Mirror `tests/unit/services/test_affiliate_settings_defaults.py`.

```python
"""The community-draft settings ship as app_settings defaults (not migration seeds)."""
from __future__ import annotations

from services.settings_defaults import DEFAULTS


def test_community_draft_model_defaults_empty():
    # Empty => generate_reddit_draft falls to pipeline_writer_model.
    assert DEFAULTS["community_draft_model"] == ""


def test_community_draft_timeout_default():
    assert DEFAULTS["community_draft_timeout_seconds"] == "180"
```

- [ ] **Step 2: Run it — verify it fails**

```bash
PYTHONPATH="$SRC" "$VENV" -m pytest "$SRC/tests/unit/services/test_community_settings_defaults.py" -o addopts="" -q
```

Expected: FAIL — `KeyError: 'community_draft_model'`.

- [ ] **Step 3: Add the settings defaults**

In `services/settings_defaults.py`, find the affiliate-settings block (search for `affiliate_import_llm_model`) and add, adjacent to it:

```python
    # Community draft assistant (WS2) — on-demand founder-voice drafts for
    # Reddit / IndieHackers. Empty model => the writer model (pipeline_writer_model);
    # prose, so writer-grade not structured-extraction.
    "community_draft_model": "",
    "community_draft_timeout_seconds": "180",
```

- [ ] **Step 4: Run it — verify it passes**

Expected: PASS (2 passed).

- [ ] **Step 5: Write the failing integration_db schema guard**

Mirror `tests/integration_db/test_affiliate_links_schema.py`.

```python
"""Schema guard for the community-draft tables (subreddit_profiles,
community_post_drafts). Runs against the disposable ``test_pool``; skips when
no live Postgres is available."""
from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _columns(pool, table: str) -> set[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = $1",
            table,
        )
    return {r["column_name"] for r in rows}


async def test_subreddit_profiles_columns(test_pool):
    cols = await _columns(test_pool, "subreddit_profiles")
    assert {
        "id", "subreddit", "enabled", "content_types", "post_type", "self_promo",
        "flair", "min_karma", "min_account_age_days", "rules_summary", "tone_notes",
        "cadence_cap_days", "created_at", "updated_at",
    } <= cols


async def test_community_post_drafts_columns(test_pool):
    cols = await _columns(test_pool, "community_post_drafts")
    assert {
        "id", "target", "title", "body", "post_type", "source_post_id",
        "warnings", "status", "posted_url", "model", "created_at", "updated_at",
    } <= cols


async def test_subreddit_profiles_ship_empty(test_pool):
    """Operator subreddit seeds live in the overlay, not OSS baseline."""
    async with test_pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM subreddit_profiles")
    assert n == 0


async def test_subreddit_unique(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(Exception):
            async with conn.transaction():
                await conn.execute("INSERT INTO subreddit_profiles (subreddit) VALUES ('dup-test')")
                await conn.execute("INSERT INTO subreddit_profiles (subreddit) VALUES ('dup-test')")
```

- [ ] **Step 6: Run it — verify it fails**

```bash
PYTHONPATH="$SRC" "$VENV" -m pytest "$SRC/tests/integration_db/test_community_draft_tables.py" -o addopts="" -q
```

Expected: FAIL — tables don't exist (or SKIP if no Postgres; if it skips, proceed and rely on the guard once the migration lands).

- [ ] **Step 7: Write the migration**

`services/migrations/20260713_010000_add_community_draft_tables.py` — mirror the shape of `20260713_000000_add_post_content_types.py` exactly (`async def up/down`, `pool.acquire()`, `IF NOT EXISTS`, module `logger`).

```python
"""Migration 20260713_010000: add community-draft tables

WS2 community draft assistant. Two tables:

- subreddit_profiles: per-community config (rules, tone, post-type norms,
  self-promo tolerance, advisory karma/age/cadence gates, and the content_types
  the sub accepts) so a generated draft fits the target subreddit.
- community_post_drafts: the draft store. Distinct lifecycle from
  social_post_drafts (which is welded to the Postiz publish-gate) — these are
  manual-post drafts, blog-post-optional (IndieHackers isn't blog-tied), with a
  mark-posted-with-URL state and no auto-post surface.

Ships EMPTY. Operator subreddit seeds live in the stripped operator overlay,
never OSS baseline. See
docs/superpowers/specs/2026-07-13-community-draft-assistant-design.md.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.subreddit_profiles (
                id                   bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                subreddit            text NOT NULL UNIQUE,
                enabled              boolean NOT NULL DEFAULT true,
                content_types        text[] NOT NULL DEFAULT '{}',
                post_type            text NOT NULL DEFAULT 'text',
                self_promo           text NOT NULL DEFAULT 'strict',
                flair                text,
                min_karma            integer,
                min_account_age_days integer,
                rules_summary        text NOT NULL DEFAULT '',
                tone_notes           text NOT NULL DEFAULT '',
                cadence_cap_days     integer,
                created_at           timestamptz NOT NULL DEFAULT now(),
                updated_at           timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subreddit_profiles_enabled "
            "ON subreddit_profiles (enabled)"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.community_post_drafts (
                id             bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                target         text NOT NULL,
                title          text,
                body           text NOT NULL,
                post_type      text NOT NULL DEFAULT 'text',
                source_post_id uuid REFERENCES posts(id) ON DELETE SET NULL,
                warnings       text[] NOT NULL DEFAULT '{}',
                status         text NOT NULL DEFAULT 'draft',
                posted_url     text,
                model          text,
                created_at     timestamptz NOT NULL DEFAULT now(),
                updated_at     timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_community_post_drafts_status "
            "ON community_post_drafts (status)"
        )
    logger.info("Migration add_community_draft_tables: applied")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS public.community_post_drafts")
        await conn.execute("DROP TABLE IF EXISTS public.subreddit_profiles")
    logger.info("Migration add_community_draft_tables: reverted")
```

- [ ] **Step 8: Lint the migration + run the schema guard**

```bash
PYTHONPATH="$SRC" "$VENV" "$SRC/../../scripts/ci/migrations_lint.py"
PYTHONPATH="$SRC" "$VENV" -m pytest "$SRC/tests/integration_db/test_community_draft_tables.py" -o addopts="" -q
```

Expected: lint clean; schema guard PASS (or clean SKIP if no Postgres).

- [ ] **Step 9: Commit**

```bash
git add src/cofounder_agent/services/migrations/20260713_010000_add_community_draft_tables.py \
        src/cofounder_agent/services/settings_defaults.py \
        src/cofounder_agent/tests/unit/services/test_community_settings_defaults.py \
        src/cofounder_agent/tests/integration_db/test_community_draft_tables.py
git commit -m "feat(community): add subreddit_profiles + community_post_drafts tables + settings"
```

---

## Task 2: `subreddit_profiles` CRUD service

**Files:**

- Create: `src/cofounder_agent/services/community_drafts.py` (dataclasses + profile CRUD)
- Test: `src/cofounder_agent/tests/unit/services/test_community_drafts_crud.py`

**Interfaces:**

- Consumes: the `subreddit_profiles` table (Task 1).
- Produces: `SubredditProfile`, `list_profiles`, `get_profile`, `add_profile`, `update_profile`, `edit_profile`, `set_profile_enabled`, `remove_profile` (signatures in the service-surface block above).

- [ ] **Step 1: Write the failing CRUD tests**

Use the fake-pool doubles from `tests/unit/modules/content/test_affiliate_links_crud.py` (copy the `_NullAsyncCtx` / `_FakeConn` / `_AcquireCtx` / `_FakePool` / `_ListPool` helpers into this test file — they are a small self-contained harness, and the affiliate copy is DRY-exempt as a test fixture).

```python
"""Unit tests for subreddit_profiles CRUD (fake pool, no real DB)."""
from __future__ import annotations

import pytest

from services.community_drafts import (
    SubredditProfile, add_profile, edit_profile, get_profile,
    list_profiles, remove_profile, set_profile_enabled, update_profile,
)

# --- fake pool doubles (copied from test_affiliate_links_crud.py) ---
class _NullAsyncCtx:
    async def __aenter__(self): return self
    async def __aexit__(self, *exc): return False

class _FakeConn:
    def __init__(self, fetchval_result=1, fetchrow_result=None):
        self.calls = []
        self._fetchval_result = fetchval_result
        self._fetchrow_result = fetchrow_result
    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args)); return self._fetchval_result
    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args)); return self._fetchrow_result
    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args)); return "INSERT 0 1"
    def transaction(self): return _NullAsyncCtx()

class _AcquireCtx:
    def __init__(self, conn): self._conn = conn
    async def __aenter__(self): return self._conn
    async def __aexit__(self, *exc): return False

class _FakePool:
    def __init__(self, *, execute_result="UPDATE 1", fetchrow_result=None, fetch_rows=None):
        self.conn = _FakeConn(fetchrow_result=fetchrow_result)
        self._execute_result = execute_result
        self._fetch_rows = fetch_rows or []
        self.fetch_sql = None
    def acquire(self): return _AcquireCtx(self.conn)
    async def execute(self, sql, *args):
        self.conn.calls.append(("pool.execute", sql, args)); return self._execute_result
    async def fetch(self, sql, *args):
        self.fetch_sql = sql; return self._fetch_rows
    async def fetchrow(self, sql, *args):
        self.conn.calls.append(("pool.fetchrow", sql, args)); return self.conn._fetchrow_result


def _row(**over):
    base = dict(
        subreddit="LocalLLaMA", enabled=True, content_types=["ai-ml"],
        post_type="text", self_promo="strict", flair=None, min_karma=None,
        min_account_age_days=None, rules_summary="", tone_notes="", cadence_cap_days=None,
    )
    base.update(over)
    return base


async def test_add_profile_returns_true_on_insert():
    pool = _FakePool(execute_result="INSERT 0 1")
    ok = await add_profile(pool, SubredditProfile(subreddit="LocalLLaMA", content_types=["ai-ml"]))
    assert ok is True
    _, sql, args = pool.conn.calls[0]
    assert "ON CONFLICT (subreddit) DO NOTHING" in sql
    assert args[0] == "LocalLLaMA"
    assert args[2] == ["ai-ml"]          # content_types passed as a list (text[])


async def test_add_profile_returns_false_on_conflict():
    pool = _FakePool(execute_result="INSERT 0 0")
    assert await add_profile(pool, SubredditProfile(subreddit="LocalLLaMA")) is False


async def test_list_profiles_enabled_only_filters():
    pool = _FakePool(fetch_rows=[_row()])
    out = await list_profiles(pool, enabled_only=True)
    assert "WHERE enabled" in pool.fetch_sql
    assert out[0].subreddit == "LocalLLaMA"
    assert out[0].content_types == ["ai-ml"]


async def test_list_profiles_all_has_no_filter():
    pool = _FakePool(fetch_rows=[])
    assert await list_profiles(pool) == []
    assert "WHERE enabled" not in pool.fetch_sql


async def test_get_profile_none_when_missing():
    pool = _FakePool(fetchrow_result=None)
    assert await get_profile(pool, "nope") is None


async def test_edit_profile_merges_only_provided_fields():
    pool = _FakePool(fetchrow_result=_row(flair=None, tone_notes="old"))
    merged = await edit_profile(pool, "LocalLLaMA", flair="Discussion", tone_notes=None)
    assert merged.flair == "Discussion"     # provided → applied
    assert merged.tone_notes == "old"       # None → left unchanged
    assert merged.content_types == ["ai-ml"]


async def test_edit_profile_raises_when_missing():
    pool = _FakePool(fetchrow_result=None)
    with pytest.raises(KeyError):
        await edit_profile(pool, "nope", flair="x")


async def test_set_profile_enabled_returns_true_on_match():
    pool = _FakePool(execute_result="UPDATE 1")
    assert await set_profile_enabled(pool, "LocalLLaMA", False) is True


async def test_remove_profile_returns_false_when_no_match():
    pool = _FakePool(execute_result="DELETE 0")
    assert await remove_profile(pool, "nope") is False
```

- [ ] **Step 2: Run — verify it fails**

```bash
PYTHONPATH="$SRC" "$VENV" -m pytest "$SRC/tests/unit/services/test_community_drafts_crud.py" -o addopts="" -q
```

Expected: FAIL — `ModuleNotFoundError: services.community_drafts`.

- [ ] **Step 3: Implement the module header + dataclasses + profile CRUD**

Create `services/community_drafts.py`:

```python
"""Community draft assistant — subreddit profiles + draft store + Reddit
value-post generation (WS2). On-demand, draft-only, no auto-posting.

Profiles carry each community's rules/tone/post-type norms and the classifier
content_types it accepts; drafts are native founder-voice posts the operator
reviews and posts manually. See
docs/superpowers/specs/2026-07-13-community-draft-assistant-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from services.llm_text import ollama_chat_text, resolve_local_model

_PROFILE_COLS = (
    "subreddit, enabled, content_types, post_type, self_promo, flair, "
    "min_karma, min_account_age_days, rules_summary, tone_notes, cadence_cap_days"
)


@dataclass
class SubredditProfile:
    subreddit: str
    enabled: bool = True
    content_types: list[str] = field(default_factory=list)
    post_type: str = "text"
    self_promo: str = "strict"
    flair: str | None = None
    min_karma: int | None = None
    min_account_age_days: int | None = None
    rules_summary: str = ""
    tone_notes: str = ""
    cadence_cap_days: int | None = None


@dataclass
class CommunityDraft:
    id: int
    target: str
    title: str | None
    body: str
    post_type: str
    source_post_id: str | None
    warnings: list[str]
    status: str
    posted_url: str | None
    model: str | None


def _row_to_profile(row: Any) -> SubredditProfile:
    return SubredditProfile(
        subreddit=row["subreddit"], enabled=row["enabled"],
        content_types=list(row["content_types"]), post_type=row["post_type"],
        self_promo=row["self_promo"], flair=row["flair"],
        min_karma=row["min_karma"], min_account_age_days=row["min_account_age_days"],
        rules_summary=row["rules_summary"], tone_notes=row["tone_notes"],
        cadence_cap_days=row["cadence_cap_days"],
    )


async def list_profiles(pool: Any, *, enabled_only: bool = False) -> list[SubredditProfile]:
    where = "WHERE enabled " if enabled_only else ""
    rows = await pool.fetch(
        f"SELECT {_PROFILE_COLS} FROM subreddit_profiles {where}ORDER BY subreddit"
    )
    return [_row_to_profile(r) for r in rows]


async def get_profile(pool: Any, subreddit: str) -> SubredditProfile | None:
    row = await pool.fetchrow(
        f"SELECT {_PROFILE_COLS} FROM subreddit_profiles WHERE subreddit = $1", subreddit
    )
    return _row_to_profile(row) if row else None


async def add_profile(pool: Any, profile: SubredditProfile) -> bool:
    tag = await pool.execute(
        "INSERT INTO subreddit_profiles "
        "(subreddit, enabled, content_types, post_type, self_promo, flair, "
        " min_karma, min_account_age_days, rules_summary, tone_notes, cadence_cap_days) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
        "ON CONFLICT (subreddit) DO NOTHING",
        profile.subreddit, profile.enabled, profile.content_types, profile.post_type,
        profile.self_promo, profile.flair, profile.min_karma,
        profile.min_account_age_days, profile.rules_summary, profile.tone_notes,
        profile.cadence_cap_days,
    )
    return tag.endswith("1")


async def update_profile(pool: Any, profile: SubredditProfile) -> bool:
    tag = await pool.execute(
        "UPDATE subreddit_profiles SET enabled=$2, content_types=$3, post_type=$4, "
        "self_promo=$5, flair=$6, min_karma=$7, min_account_age_days=$8, "
        "rules_summary=$9, tone_notes=$10, cadence_cap_days=$11, updated_at=now() "
        "WHERE subreddit=$1",
        profile.subreddit, profile.enabled, profile.content_types, profile.post_type,
        profile.self_promo, profile.flair, profile.min_karma,
        profile.min_account_age_days, profile.rules_summary, profile.tone_notes,
        profile.cadence_cap_days,
    )
    return tag.endswith("1")


async def edit_profile(pool: Any, subreddit: str, **changes: Any) -> SubredditProfile:
    existing = await get_profile(pool, subreddit)
    if existing is None:
        raise KeyError(subreddit)
    applied = {k: v for k, v in changes.items() if v is not None}
    merged = replace(existing, **applied)
    await update_profile(pool, merged)
    return merged


async def set_profile_enabled(pool: Any, subreddit: str, enabled: bool) -> bool:
    tag = await pool.execute(
        "UPDATE subreddit_profiles SET enabled=$2, updated_at=now() WHERE subreddit=$1",
        subreddit, enabled,
    )
    return tag.endswith("1")


async def remove_profile(pool: Any, subreddit: str) -> bool:
    tag = await pool.execute(
        "DELETE FROM subreddit_profiles WHERE subreddit=$1", subreddit
    )
    return tag.endswith("1")
```

- [ ] **Step 4: Run — verify it passes**

Expected: PASS (all profile CRUD tests green).

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/community_drafts.py \
        src/cofounder_agent/tests/unit/services/test_community_drafts_crud.py
git commit -m "feat(community): subreddit_profiles CRUD service"
```

---

## Task 3: Content-type match + draft CRUD

**Files:**

- Modify: `src/cofounder_agent/services/community_drafts.py` (append match + draft CRUD)
- Test: `src/cofounder_agent/tests/unit/services/test_community_drafts_drafts.py`

**Interfaces:**

- Consumes: `subreddit_profiles`, `community_post_drafts`, `post_content_types` (WS1.5).
- Produces: `suggest_subreddits_for_post`, `create_draft`, `list_drafts`, `get_draft`, `edit_draft`, `mark_posted`, `discard_draft`.

- [ ] **Step 1: Write the failing tests** (reuse the fake-pool doubles — import them, or re-declare the same minimal `_FakePool` in this file; re-declaring a tiny test double is acceptable).

```python
"""Unit tests for content-type match + draft CRUD (fake pool)."""
from __future__ import annotations

from services.community_drafts import (
    create_draft, discard_draft, edit_draft, get_draft, list_drafts,
    mark_posted, suggest_subreddits_for_post,
)

# (paste the same _FakeConn/_AcquireCtx/_FakePool doubles as Task 2, plus:)
class _FetchPool:
    def __init__(self, rows=None, fetchval=1, fetchrow=None):
        self.rows = rows or []; self._fetchval = fetchval; self._fetchrow = fetchrow
        self.fetch_sql = None; self.exec_calls = []
    async def fetch(self, sql, *args):
        self.fetch_sql = (sql, args); return self.rows
    async def fetchval(self, sql, *args):
        self.exec_calls.append((sql, args)); return self._fetchval
    async def fetchrow(self, sql, *args):
        return self._fetchrow
    async def execute(self, sql, *args):
        self.exec_calls.append((sql, args)); return "UPDATE 1"


def _draft_row(**over):
    base = dict(
        id=5, target="reddit:LocalLLaMA", title="T", body="B", post_type="text",
        source_post_id=None, warnings=["set flair: Discussion"], status="draft",
        posted_url=None, model="gemma",
    )
    base.update(over); return base


async def test_suggest_uses_array_overlap():
    pool = _FetchPool(rows=[{"subreddit": "LocalLLaMA"}, {"subreddit": "selfhosted"}])
    out = await suggest_subreddits_for_post(pool, "post-uuid")
    sql, args = pool.fetch_sql
    assert "content_types &&" in sql
    assert "post_content_types" in sql
    assert args == ("post-uuid",)
    assert out == ["LocalLLaMA", "selfhosted"]


async def test_create_draft_returns_id_and_defaults_warnings():
    pool = _FetchPool(fetchval=99)
    new_id = await create_draft(pool, target="reddit:X", body="hello")
    assert new_id == 99
    sql, args = pool.exec_calls[0]
    assert "INSERT INTO community_post_drafts" in sql
    assert args[5] == []          # warnings defaults to [] not None (text[] NOT NULL)


async def test_list_drafts_status_filter():
    pool = _FetchPool(rows=[_draft_row()])
    out = await list_drafts(pool, status="draft")
    sql, args = pool.fetch_sql
    assert "WHERE status" in sql and args == ("draft",)
    assert out[0].id == 5 and out[0].warnings == ["set flair: Discussion"]


async def test_get_draft_none_when_missing():
    pool = _FetchPool(fetchrow=None)
    assert await get_draft(pool, 123) is None


async def test_mark_posted_sets_status_and_url():
    pool = _FetchPool()
    assert await mark_posted(pool, 5, url="https://reddit.com/x") is True
    sql, args = pool.exec_calls[0]
    assert "status='posted'" in sql.replace(" ", "") or "status = 'posted'" in sql
    assert args == (5, "https://reddit.com/x")


async def test_discard_sets_status():
    pool = _FetchPool()
    assert await discard_draft(pool, 5) is True
    sql, _ = pool.exec_calls[0]
    assert "discarded" in sql


async def test_edit_draft_no_fields_returns_false():
    pool = _FetchPool()
    assert await edit_draft(pool, 5) is False
```

- [ ] **Step 2: Run — verify it fails** (`ImportError` on the new names).

- [ ] **Step 3: Append the implementation** to `services/community_drafts.py`:

```python
_DRAFT_COLS = (
    "id, target, title, body, post_type, source_post_id, warnings, status, posted_url, model"
)


def _row_to_draft(row: Any) -> CommunityDraft:
    src = row["source_post_id"]
    return CommunityDraft(
        id=row["id"], target=row["target"], title=row["title"], body=row["body"],
        post_type=row["post_type"], source_post_id=str(src) if src else None,
        warnings=list(row["warnings"]), status=row["status"],
        posted_url=row["posted_url"], model=row["model"],
    )


async def suggest_subreddits_for_post(pool: Any, post_id: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT subreddit FROM subreddit_profiles "
        "WHERE enabled AND content_types && ("
        "  SELECT COALESCE(array_agg(content_type), '{}') "
        "  FROM post_content_types WHERE post_id = $1"
        ") ORDER BY subreddit",
        post_id,
    )
    return [r["subreddit"] for r in rows]


async def create_draft(pool: Any, *, target: str, body: str, title: str | None = None,
                       post_type: str = "text", source_post_id: str | None = None,
                       warnings: list[str] | None = None, model: str | None = None) -> int:
    return await pool.fetchval(
        "INSERT INTO community_post_drafts "
        "(target, title, body, post_type, source_post_id, warnings, model) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id",
        target, title, body, post_type, source_post_id, warnings or [], model,
    )


async def list_drafts(pool: Any, *, status: str | None = None) -> list[CommunityDraft]:
    if status:
        rows = await pool.fetch(
            f"SELECT {_DRAFT_COLS} FROM community_post_drafts WHERE status = $1 ORDER BY id DESC",
            status,
        )
    else:
        rows = await pool.fetch(
            f"SELECT {_DRAFT_COLS} FROM community_post_drafts ORDER BY id DESC"
        )
    return [_row_to_draft(r) for r in rows]


async def get_draft(pool: Any, draft_id: int) -> CommunityDraft | None:
    row = await pool.fetchrow(
        f"SELECT {_DRAFT_COLS} FROM community_post_drafts WHERE id = $1", draft_id
    )
    return _row_to_draft(row) if row else None


async def edit_draft(pool: Any, draft_id: int, *, title: str | None = None,
                     body: str | None = None) -> bool:
    if title is None and body is None:
        return False
    tag = await pool.execute(
        "UPDATE community_post_drafts "
        "SET title = COALESCE($2, title), body = COALESCE($3, body), updated_at = now() "
        "WHERE id = $1",
        draft_id, title, body,
    )
    return tag.endswith("1")


async def mark_posted(pool: Any, draft_id: int, *, url: str) -> bool:
    tag = await pool.execute(
        "UPDATE community_post_drafts SET status = 'posted', posted_url = $2, "
        "updated_at = now() WHERE id = $1",
        draft_id, url,
    )
    return tag.endswith("1")


async def discard_draft(pool: Any, draft_id: int) -> bool:
    tag = await pool.execute(
        "UPDATE community_post_drafts SET status = 'discarded', updated_at = now() "
        "WHERE id = $1",
        draft_id,
    )
    return tag.endswith("1")
```

- [ ] **Step 4: Run — verify it passes.**

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/community_drafts.py \
        src/cofounder_agent/tests/unit/services/test_community_drafts_drafts.py
git commit -m "feat(community): content-type match + draft CRUD"
```

---

## Task 4: Reddit value-post generation (SKILL.md + generator)

**Files:**

- Create: `src/cofounder_agent/skills/community/reddit-value-post/SKILL.md`
- Modify: `src/cofounder_agent/services/community_drafts.py` (append `compute_warnings`, `maybe_append_blog_link`, `_resolve_reddit_prompt`, `generate_reddit_draft`)
- Test: `src/cofounder_agent/tests/unit/services/test_community_reddit_generate.py`
- Test: `src/cofounder_agent/tests/unit/services/test_community_reddit_skill.py`

**Interfaces:**

- Consumes: `get_profile`, `create_draft`, `get_draft` (Tasks 2-3); `resolve_local_model` + `ollama_chat_text` (`services.llm_text`); `get_prompt_manager().get_prompt`; the `posts` + `post_content_types` tables; `site_config.get`.
- Produces: `compute_warnings(profile) -> list[str]`, `maybe_append_blog_link(body, *, post_type, slug, site_config) -> str`, `generate_reddit_draft(pool, *, post_id, subreddit, site_config) -> CommunityDraft`.

- [ ] **Step 1: Write the SKILL.md-parses test** (mirror the WS1.5 `test_content_type_classifier_skill.py`).

```python
"""The reddit-value-post SKILL.md pack registers and its prompt renders."""
from __future__ import annotations

from services.prompt_manager import get_prompt_manager


def test_reddit_value_post_prompt_renders():
    pm = get_prompt_manager()
    out = pm.get_prompt(
        "community.reddit_value_post",
        title="RTX 5090 for local inference", content="body text",
        content_types="ai-ml, pc-hardware", rules_summary="No memes.",
        tone_notes="Technical, humble.", post_type="text",
        self_promo="strict", flair="Discussion",
    )
    assert "RTX 5090 for local inference" in out
    assert "Discussion" in out
    assert out.strip()
```

- [ ] **Step 2: Run — verify it fails** (prompt key not registered).

- [ ] **Step 3: Write the SKILL.md pack.**

`skills/community/reddit-value-post/SKILL.md` — category `social_media`, `output_format: text`. (Note: the prompt body is fed through `str.format(**kwargs)`, so it contains **no** literal `{`/`}` other than the named placeholders.)

```markdown
---
name: reddit-value-post
description: Generate a native, community-appropriate Reddit value-post from a published blog post
metadata:
  category: social_media
  prompts:
    - key: community.reddit_value_post
      output_format: text
      description: Native founder-voice Reddit post for a specific subreddit
---

# Reddit Value-Post

## community.reddit_value_post

You are the founder of a small AI-and-hardware software project, writing a post
to share in the subreddit described below. You are posting under your own
account, in your own first-person voice — not as a bot, not as marketing.

Reddit communities punish self-promotion and reward genuine, specific value.
Your job is to write a post that a longtime member of this subreddit would
upvote because it taught them something or started a good discussion — NOT a
"check out my blog" ad.

## The subreddit

- Content types it accepts: {content_types}
- Rules & culture: {rules_summary}
- How to write for it: {tone_notes}
- Post type allowed: {post_type}
- Self-promotion tolerance: {self_promo}
- Default flair: {flair}

## Source material (your own prior work — mine it for the genuine insight)

Title: {title}

Content:
{content}

## Write the post

- Lead with the concrete insight, result, or question — the value is in the
  first two lines, not buried under preamble.
- Write it as a standalone Reddit post. It must stand on its own even if nobody
  clicks any link.
- Be specific: real numbers, real trade-offs, real failure modes from the
  source material. Never invent a figure that is not in the source.
- Match the subreddit's tone. If self-promotion tolerance is strict, do NOT
  mention the blog or drop any link — share the value directly as text.
- Do NOT write a link with a URL yourself. If a link belongs here, the system
  appends it; you write only the body.
- No hype, no "game-changer", no marketing voice. Write like a practitioner
  talking to peers.
- Vary sentence length. Take a firm stance where the source material supports
  one. Avoid the words delve, tapestry, testament, realm, and "it's not just X,
  it's Y" constructions.

Output only the post body (and, if the subreddit expects one, a first line that
reads as the title). No preamble, no meta-commentary.
```

- [ ] **Step 4: Run the SKILL.md test — verify it passes.**

If it fails with the pack being skipped, confirm `category: social_media` is spelled exactly (an invalid category silently drops the pack — this is the known WS1.5 footgun).

- [ ] **Step 5: Write the generator tests.**

```python
"""Unit tests for Reddit draft generation: warnings, deterministic link-append,
and the generate_reddit_draft orchestration (stubbed LLM + fake pool)."""
from __future__ import annotations

import pytest

import services.community_drafts as cd
from services.community_drafts import (
    SubredditProfile, compute_warnings, maybe_append_blog_link,
)
from services.site_config import SiteConfig


def test_compute_warnings_strict_flair_karma():
    p = SubredditProfile(subreddit="X", self_promo="strict", flair="Discussion",
                         min_karma=100, cadence_cap_days=7)
    w = compute_warnings(p)
    assert any("strict" in x for x in w)
    assert any("Discussion" in x for x in w)
    assert any("100" in x for x in w)
    assert any("7" in x for x in w)


def test_compute_warnings_ok_promo_minimal():
    assert compute_warnings(SubredditProfile(subreddit="X", self_promo="ok")) == []


def test_link_omitted_for_text_posts():
    sc = SiteConfig(initial_config={"site_url": "https://example.test"})
    out = maybe_append_blog_link("body", post_type="text", slug="my-post", site_config=sc)
    assert out == "body"


def test_link_appended_for_link_posts():
    sc = SiteConfig(initial_config={"site_url": "https://example.test"})
    out = maybe_append_blog_link("body", post_type="either", slug="my-post", site_config=sc)
    assert "https://example.test/posts/my-post" in out


def test_link_never_broken_when_base_unset():
    sc = SiteConfig(initial_config={"site_url": ""})
    out = maybe_append_blog_link("body", post_type="link", slug="my-post", site_config=sc)
    assert out == "body"      # no base configured → never emit a broken link


# --- generate_reddit_draft orchestration (stub LLM + prompt + fake pool) ---
class _GenPool:
    def __init__(self, profile_row, post_row, ctype_rows):
        self._profile_row = profile_row; self._post_row = post_row
        self._ctype_rows = ctype_rows; self.created = {}
    async def fetchrow(self, sql, *args):
        if "subreddit_profiles" in sql:
            return self._profile_row
        if "FROM posts" in sql:
            return self._post_row
        if "community_post_drafts" in sql:      # get_draft after insert
            return {**self.created, "id": 1, "status": "draft", "posted_url": None}
        return None
    async def fetch(self, sql, *args):
        return self._ctype_rows
    async def fetchval(self, sql, *args):       # create_draft RETURNING id
        # args: target,title,body,post_type,source_post_id,warnings,model
        self.created = dict(
            target=args[0], title=args[1], body=args[2], post_type=args[3],
            source_post_id=args[4], warnings=list(args[5]), model=args[6],
        )
        return 1


def _profile_row(**over):
    base = dict(subreddit="LocalLLaMA", enabled=True, content_types=["ai-ml"],
                post_type="text", self_promo="strict", flair="Discussion",
                min_karma=None, min_account_age_days=None, rules_summary="No memes.",
                tone_notes="Technical.", cadence_cap_days=None)
    base.update(over); return base


async def test_generate_reddit_draft_stores_body_and_warnings(monkeypatch):
    monkeypatch.setattr(cd, "_resolve_reddit_prompt", lambda **k: "PROMPT")
    async def _fake_llm(prompt, **kw): return "  Native value post body.  "
    monkeypatch.setattr(cd, "ollama_chat_text", _fake_llm)

    pool = _GenPool(
        profile_row=_profile_row(),
        post_row={"title": "RTX 5090 inference", "body": "the source", "slug": "rtx-5090"},
        ctype_rows=[{"content_type": "ai-ml"}],
    )
    sc = SiteConfig(initial_config={"site_url": "https://example.test",
                                    "pipeline_writer_model": "gemma-4-31B"})
    draft = await cd.generate_reddit_draft(pool, post_id="p1", subreddit="LocalLLaMA", site_config=sc)

    assert draft.target == "reddit:LocalLLaMA"
    assert pool.created["body"] == "Native value post body."   # stripped, no link (text)
    assert "set flair: Discussion" in pool.created["warnings"]
    assert pool.created["model"] == "gemma-4-31B"
    assert pool.created["source_post_id"] == "p1"


async def test_generate_reddit_draft_appends_link_for_either(monkeypatch):
    monkeypatch.setattr(cd, "_resolve_reddit_prompt", lambda **k: "PROMPT")
    async def _fake_llm(prompt, **kw): return "Body."
    monkeypatch.setattr(cd, "ollama_chat_text", _fake_llm)

    pool = _GenPool(
        profile_row=_profile_row(post_type="either"),
        post_row={"title": "T", "body": "src", "slug": "rtx-5090"},
        ctype_rows=[{"content_type": "ai-ml"}],
    )
    sc = SiteConfig(initial_config={"site_url": "https://example.test",
                                    "pipeline_writer_model": "gemma-4-31B"})
    await cd.generate_reddit_draft(pool, post_id="p1", subreddit="LocalLLaMA", site_config=sc)
    assert "https://example.test/posts/rtx-5090" in pool.created["body"]


async def test_generate_reddit_draft_missing_profile_raises(monkeypatch):
    pool = _GenPool(profile_row=None, post_row=None, ctype_rows=[])
    sc = SiteConfig(initial_config={"pipeline_writer_model": "gemma"})
    with pytest.raises(KeyError):
        await cd.generate_reddit_draft(pool, post_id="p1", subreddit="nope", site_config=sc)
```

- [ ] **Step 6: Run — verify it fails** (`compute_warnings` etc. undefined).

- [ ] **Step 7: Append the generator implementation** to `services/community_drafts.py`:

```python
_REDDIT_PROMPT_KEY = "community.reddit_value_post"


def compute_warnings(profile: SubredditProfile) -> list[str]:
    """Deterministic advisory constraints surfaced to the operator (never
    enforced — the operator is the poster)."""
    out: list[str] = []
    if profile.self_promo == "strict":
        out.append("self-promo=strict: post as native text, no blog link")
    elif profile.self_promo == "moderate":
        out.append("self-promo=moderate: link only if it genuinely helps the reader")
    if profile.flair:
        out.append(f"set flair: {profile.flair}")
    if profile.min_karma is not None:
        out.append(f"min karma: {profile.min_karma}")
    if profile.min_account_age_days is not None:
        out.append(f"min account age: {profile.min_account_age_days} days")
    if profile.cadence_cap_days is not None:
        out.append(f"cadence: avoid posting here more than once per "
                   f"{profile.cadence_cap_days} days")
    return out


def _site_url(site_config: Any) -> str:
    return (site_config.get("site_url", "") or "").strip().rstrip("/")


def maybe_append_blog_link(body: str, *, post_type: str, slug: str,
                           site_config: Any) -> str:
    """Deterministically append the canonical blog link ONLY when the subreddit
    allows it (post_type link/either) AND a base URL is configured. Keeps the
    link decision out of the LLM's hands so it's testable and never a bare
    link-drop in a text-only sub."""
    if post_type not in ("link", "either"):
        return body
    base = _site_url(site_config)
    if not base:
        return body
    return f"{body}\n\n---\nFull write-up: {base}/posts/{slug}"


def _resolve_reddit_prompt(**kwargs: Any) -> str:
    from services.prompt_manager import get_prompt_manager
    return get_prompt_manager().get_prompt(_REDDIT_PROMPT_KEY, **kwargs)


async def generate_reddit_draft(pool: Any, *, post_id: str, subreddit: str,
                                site_config: Any) -> CommunityDraft:
    profile = await get_profile(pool, subreddit)
    if profile is None:
        raise KeyError(f"no subreddit profile for {subreddit!r}")
    post = await pool.fetchrow(
        "SELECT title, body, slug FROM posts WHERE id = $1 AND status = 'published'",
        post_id,
    )
    if post is None:
        raise ValueError(f"no published post {post_id!r}")
    ctype_rows = await pool.fetch(
        "SELECT content_type FROM post_content_types WHERE post_id = $1", post_id
    )
    content_types = ", ".join(r["content_type"] for r in ctype_rows) or "(unclassified)"

    prompt = _resolve_reddit_prompt(
        title=post["title"], content=post["body"], content_types=content_types,
        rules_summary=profile.rules_summary, tone_notes=profile.tone_notes,
        post_type=profile.post_type, self_promo=profile.self_promo,
        flair=profile.flair or "(none)",
    )
    pin = (site_config.get("community_draft_model", "") or "").strip() or None
    model = resolve_local_model(pin, site_config=site_config)
    raw = await ollama_chat_text(
        prompt, model=model, site_config=site_config, pool=pool, tier="standard",
        phase="community_reddit_draft",
        timeout_setting="community_draft_timeout_seconds", timeout_default=180.0,
        think=False,
    )
    body = maybe_append_blog_link(
        raw.strip(), post_type=profile.post_type, slug=post["slug"],
        site_config=site_config,
    )
    draft_id = await create_draft(
        pool, target=f"reddit:{subreddit}", body=body, title=post["title"],
        post_type=profile.post_type, source_post_id=post_id,
        warnings=compute_warnings(profile), model=model,
    )
    return await get_draft(pool, draft_id)
```

- [ ] **Step 8: Run — verify it passes.**

- [ ] **Step 9: Commit**

```bash
git add src/cofounder_agent/skills/community/reddit-value-post/SKILL.md \
        src/cofounder_agent/services/community_drafts.py \
        src/cofounder_agent/tests/unit/services/test_community_reddit_generate.py \
        src/cofounder_agent/tests/unit/services/test_community_reddit_skill.py
git commit -m "feat(community): reddit value-post SKILL.md + generator"
```

---

## Task 5: CSV import/export

**Files:**

- Create: `src/cofounder_agent/services/subreddit_import.py`
- Test: `src/cofounder_agent/tests/unit/services/test_subreddit_import.py`

**Interfaces:**

- Consumes: `SubredditProfile`, `add_profile`, `update_profile`, `get_profile`, `list_profiles` (Task 2).
- Produces: `ImportRowResult`, `ImportReport`, `parse_content_types_cell`, `row_to_profile`, `profiles_to_csv`, `import_csv`, `export_csv`.

- [ ] **Step 1: Write the failing tests** (pure helpers need no pool; `import_csv`/`export_csv` use a small fake pool + `tmp_path`).

```python
"""Unit tests for subreddit_profiles CSV import/export."""
from __future__ import annotations

from services.community_drafts import SubredditProfile
from services.subreddit_import import (
    ImportReport, export_csv, import_csv, parse_content_types_cell,
    profiles_to_csv, row_to_profile,
)

_HEADER = ("subreddit,enabled,content_types,post_type,self_promo,flair,"
           "min_karma,min_account_age_days,rules_summary,tone_notes,cadence_cap_days")


def test_parse_content_types_semicolon():
    assert parse_content_types_cell("ai-ml; pc-hardware ;") == ["ai-ml", "pc-hardware"]
    assert parse_content_types_cell("") == []


def test_row_to_profile_maps_all_columns():
    p = row_to_profile({
        "subreddit": "LocalLLaMA", "enabled": "true", "content_types": "ai-ml;pc-hardware",
        "post_type": "text", "self_promo": "strict", "flair": "Discussion",
        "min_karma": "100", "min_account_age_days": "30",
        "rules_summary": "No memes.", "tone_notes": "Technical.", "cadence_cap_days": "7",
    })
    assert p.subreddit == "LocalLLaMA" and p.enabled is True
    assert p.content_types == ["ai-ml", "pc-hardware"]
    assert p.min_karma == 100 and p.cadence_cap_days == 7


def test_row_to_profile_blank_ints_are_none():
    p = row_to_profile({"subreddit": "X", "min_karma": "", "cadence_cap_days": ""})
    assert p.min_karma is None and p.cadence_cap_days is None
    assert p.enabled is True                 # missing enabled defaults true


def test_profiles_to_csv_roundtrips_content_types():
    p = SubredditProfile(subreddit="X", content_types=["ai-ml", "gaming"], min_karma=50)
    text = profiles_to_csv([p])
    assert text.splitlines()[0] == _HEADER
    # content_types serialized ';'-joined so it survives a single CSV cell
    assert "ai-ml;gaming" in text
    reparsed = row_to_profile(dict(zip(_HEADER.split(","), text.splitlines()[1].split(","))))
    assert reparsed.content_types == ["ai-ml", "gaming"] and reparsed.min_karma == 50


# --- import_csv / export_csv against a fake pool ---
class _ImpPool:
    def __init__(self, existing=None):
        self.existing = set(existing or [])
        self.added = []; self.updated = []
    async def fetchval(self, sql, *a):        # existence check
        return 1 if a[0] in self.existing else None
    # add_profile / update_profile / get_profile / list_profiles are monkeypatched


async def test_import_creates_then_skips_without_force(tmp_path, monkeypatch):
    import services.subreddit_import as si
    added = []
    async def _add(pool, profile): added.append(profile.subreddit); return True
    monkeypatch.setattr(si, "add_profile", _add)

    csv_file = tmp_path / "subs.csv"
    csv_file.write_text(_HEADER + "\nLocalLLaMA,true,ai-ml,text,strict,,,,,,", encoding="utf-8")

    pool = _ImpPool(existing=set())
    rep = await import_csv(pool, str(csv_file))
    assert [r.status for r in rep.rows] == ["created"] and added == ["LocalLLaMA"]

    pool2 = _ImpPool(existing={"LocalLLaMA"})
    rep2 = await import_csv(pool2, str(csv_file))
    assert [r.status for r in rep2.rows] == ["skipped_exists"]


async def test_import_force_updates(tmp_path, monkeypatch):
    import services.subreddit_import as si
    updated = []
    async def _upd(pool, profile): updated.append(profile.subreddit); return True
    monkeypatch.setattr(si, "update_profile", _upd)

    csv_file = tmp_path / "subs.csv"
    csv_file.write_text(_HEADER + "\nLocalLLaMA,true,ai-ml,text,strict,,,,,,", encoding="utf-8")
    pool = _ImpPool(existing={"LocalLLaMA"})
    rep = await import_csv(pool, str(csv_file), force=True)
    assert [r.status for r in rep.rows] == ["updated"] and updated == ["LocalLLaMA"]


async def test_import_malformed_row_is_error_and_batch_continues(tmp_path, monkeypatch):
    import services.subreddit_import as si
    async def _add(pool, profile):
        if profile.subreddit == "bad":
            raise ValueError("boom")
        return True
    monkeypatch.setattr(si, "add_profile", _add)
    csv_file = tmp_path / "subs.csv"
    csv_file.write_text(
        _HEADER + "\nbad,true,ai-ml,text,strict,,,,,,\ngood,true,ai-ml,text,strict,,,,,,",
        encoding="utf-8",
    )
    rep = await import_csv(_ImpPool(), str(csv_file))
    statuses = {r.subreddit: r.status for r in rep.rows}
    assert statuses == {"bad": "error", "good": "created"}
    assert len(rep.errors) == 1
```

- [ ] **Step 2: Run — verify it fails** (`ModuleNotFoundError: services.subreddit_import`).

- [ ] **Step 3: Implement `services/subreddit_import.py`** (mirror `modules/content/affiliate_import.py` structure — dataclasses + insert-only-unless-force + per-row fail-open — minus the LLM derivation, since every column is a literal operator value).

```python
"""CSV bulk import/export for subreddit_profiles (poindexter community
profiles import-csv / export-csv).

Insert-only by default: a CSV row whose `subreddit` already exists is skipped
(never silently overwrites a hand-curated row) unless --force, which updates it
in place. Per-row fail-open: a malformed row becomes an `error` result and the
batch continues. No LLM — every column is a literal operator value (unlike the
affiliate importer's keyword derivation). Mirrors
modules/content/affiliate_import.py. See
docs/superpowers/specs/2026-07-13-community-draft-assistant-design.md.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Any

from services.community_drafts import (
    SubredditProfile, add_profile, list_profiles, update_profile,
)

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "subreddit", "enabled", "content_types", "post_type", "self_promo", "flair",
    "min_karma", "min_account_age_days", "rules_summary", "tone_notes", "cadence_cap_days",
]


@dataclass
class ImportRowResult:
    subreddit: str
    status: str            # 'created' | 'updated' | 'skipped_exists' | 'error'
    detail: str = ""


@dataclass
class ImportReport:
    rows: list[ImportRowResult] = field(default_factory=list)

    @property
    def created(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "created"]

    @property
    def updated(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "updated"]

    @property
    def skipped(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "skipped_exists"]

    @property
    def errors(self) -> list[ImportRowResult]:
        return [r for r in self.rows if r.status == "error"]


def parse_content_types_cell(raw: str) -> list[str]:
    """';'-delimited cell -> list (a comma-delimited cell would collide with the
    CSV separator, so content_types uses ';')."""
    return [p.strip() for p in (raw or "").split(";") if p.strip()]


def _to_int_or_none(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw else None


def _to_bool(raw: str, *, default: bool = True) -> bool:
    raw = (raw or "").strip().lower()
    if raw == "":
        return default
    return raw in ("true", "1", "yes", "y", "t")


def row_to_profile(row: dict) -> SubredditProfile:
    """Pure CSV-row -> SubredditProfile mapping. Missing optional columns take
    the dataclass defaults; blank int cells become None."""
    g = lambda k: (row.get(k) or "").strip()  # noqa: E731
    return SubredditProfile(
        subreddit=g("subreddit"),
        enabled=_to_bool(row.get("enabled", ""), default=True),
        content_types=parse_content_types_cell(row.get("content_types", "")),
        post_type=g("post_type") or "text",
        self_promo=g("self_promo") or "strict",
        flair=g("flair") or None,
        min_karma=_to_int_or_none(row.get("min_karma", "")),
        min_account_age_days=_to_int_or_none(row.get("min_account_age_days", "")),
        rules_summary=g("rules_summary"),
        tone_notes=g("tone_notes"),
        cadence_cap_days=_to_int_or_none(row.get("cadence_cap_days", "")),
    )


def profiles_to_csv(profiles: list[SubredditProfile]) -> str:
    """Pure export: profiles -> CSV text in CSV_COLUMNS order. content_types is
    ';'-joined so it survives one cell (symmetric with parse_content_types_cell)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for p in profiles:
        w.writerow([
            p.subreddit, str(p.enabled).lower(), ";".join(p.content_types),
            p.post_type, p.self_promo, p.flair or "",
            "" if p.min_karma is None else p.min_karma,
            "" if p.min_account_age_days is None else p.min_account_age_days,
            p.rules_summary, p.tone_notes,
            "" if p.cadence_cap_days is None else p.cadence_cap_days,
        ])
    return buf.getvalue()


async def import_csv(pool: Any, csv_path: str, *, force: bool = False) -> ImportReport:
    report = ImportReport()
    with open(csv_path, newline="", encoding="utf-8") as f:
        for raw_row in csv.DictReader(f):
            row = {(k or "").strip(): (v or "").strip() for k, v in raw_row.items()}
            subreddit = row.get("subreddit", "")
            if not subreddit:
                continue
            try:
                profile = row_to_profile(row)
                existing = await pool.fetchval(
                    "SELECT id FROM subreddit_profiles WHERE subreddit = $1", subreddit
                )
                if existing and not force:
                    report.rows.append(ImportRowResult(subreddit, "skipped_exists"))
                    continue
                if existing:
                    await update_profile(pool, profile)
                    report.rows.append(ImportRowResult(subreddit, "updated"))
                else:
                    await add_profile(pool, profile)
                    report.rows.append(ImportRowResult(subreddit, "created"))
            except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
                logger.warning("[subreddit_import] row %r failed: %s", subreddit, exc)
                report.rows.append(ImportRowResult(subreddit, "error", detail=str(exc)))
    return report


async def export_csv(pool: Any) -> str:
    return profiles_to_csv(await list_profiles(pool))


__all__ = [
    "ImportReport", "ImportRowResult", "CSV_COLUMNS", "parse_content_types_cell",
    "row_to_profile", "profiles_to_csv", "import_csv", "export_csv",
]
```

- [ ] **Step 4: Run — verify it passes.**

- [ ] **Step 5: Commit**

```bash
git add src/cofounder_agent/services/subreddit_import.py \
        src/cofounder_agent/tests/unit/services/test_subreddit_import.py
git commit -m "feat(community): subreddit_profiles CSV import/export"
```

---

## Task 6: `poindexter community` CLI group

**Files:**

- Create: `src/cofounder_agent/poindexter/cli/community.py`
- Modify: `src/cofounder_agent/poindexter/cli/app.py` (import + register)
- Test: `src/cofounder_agent/tests/unit/poindexter/cli/test_community_cli.py`

**Interfaces:**

- Consumes: every `services/community_drafts.py` + `services/subreddit_import.py` function above.
- Produces: `community_group` (Click group with `profiles`, `draft`, `drafts` subgroups), registered as `poindexter community`.

- [ ] **Step 1: Write the failing CLI tests** (mirror `tests/unit/poindexter/cli/test_affiliate_cli.py` — `CliRunner`, monkeypatch `_connect` to a fake pool and the service functions).

```python
"""Unit tests for the `poindexter community` CLI (mocked pool + services)."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

import poindexter.cli.community as com
from services.community_drafts import CommunityDraft, SubredditProfile


class _NoopPool:
    async def close(self): pass


@pytest.fixture
def runner(monkeypatch):
    async def _fake_connect(): return _NoopPool()
    monkeypatch.setattr(com, "_connect", _fake_connect)
    async def _fake_sc(pool): return object()
    monkeypatch.setattr(com, "_make_site_config", _fake_sc)
    return CliRunner()


def test_profiles_list_empty(runner, monkeypatch):
    async def _list(pool, *, enabled_only=False): return []
    monkeypatch.setattr(com, "list_profiles", _list)
    res = runner.invoke(com.community_group, ["profiles", "list"])
    assert res.exit_code == 0
    assert "No subreddit profiles" in res.output


def test_profiles_add_reports_created(runner, monkeypatch):
    async def _add(pool, profile): return True
    monkeypatch.setattr(com, "add_profile", _add)
    res = runner.invoke(com.community_group, [
        "profiles", "add", "LocalLLaMA", "--content-types", "ai-ml",
        "--post-type", "text", "--self-promo", "strict",
    ])
    assert res.exit_code == 0
    assert "LocalLLaMA" in res.output


def test_profiles_add_existing_warns(runner, monkeypatch):
    async def _add(pool, profile): return False
    monkeypatch.setattr(com, "add_profile", _add)
    res = runner.invoke(com.community_group, ["profiles", "add", "LocalLLaMA"])
    assert res.exit_code != 0
    assert "exists" in res.output.lower()


def test_draft_reddit_requires_subreddit_or_suggests(runner, monkeypatch):
    async def _suggest(pool, post_id): return ["LocalLLaMA", "selfhosted"]
    monkeypatch.setattr(com, "suggest_subreddits_for_post", _suggest)
    res = runner.invoke(com.community_group, ["draft", "reddit", "some-post"])
    assert res.exit_code != 0
    # no --subreddit → lists suggestions and stops (never auto-fans-out)
    assert "LocalLLaMA" in res.output and "selfhosted" in res.output


def test_draft_reddit_generates_with_subreddit(runner, monkeypatch):
    async def _gen(pool, *, post_id, subreddit, site_config):
        return CommunityDraft(id=7, target=f"reddit:{subreddit}", title="T", body="B",
                              post_type="text", source_post_id=post_id,
                              warnings=["set flair: Discussion"], status="draft",
                              posted_url=None, model="gemma")
    monkeypatch.setattr(com, "generate_reddit_draft", _gen)
    res = runner.invoke(com.community_group,
                        ["draft", "reddit", "some-post", "--subreddit", "LocalLLaMA"])
    assert res.exit_code == 0
    assert "#7" in res.output and "set flair: Discussion" in res.output


def test_drafts_mark_posted(runner, monkeypatch):
    async def _mp(pool, draft_id, *, url): return True
    monkeypatch.setattr(com, "mark_posted", _mp)
    res = runner.invoke(com.community_group,
                        ["drafts", "mark-posted", "7", "--url", "https://reddit.com/x"])
    assert res.exit_code == 0 and "7" in res.output


def test_profiles_import_csv(runner, monkeypatch, tmp_path):
    from services.subreddit_import import ImportReport, ImportRowResult
    async def _imp(pool, path, *, force=False):
        return ImportReport(rows=[ImportRowResult("LocalLLaMA", "created")])
    monkeypatch.setattr(com, "import_csv", _imp)
    f = tmp_path / "s.csv"; f.write_text("subreddit\nLocalLLaMA", encoding="utf-8")
    res = runner.invoke(com.community_group, ["profiles", "import-csv", str(f)])
    assert res.exit_code == 0 and "created" in res.output
```

- [ ] **Step 2: Run — verify it fails** (`ModuleNotFoundError: poindexter.cli.community`).

- [ ] **Step 3: Implement `poindexter/cli/community.py`** (thin adapter — `_connect`/`_make_site_config` copied from `affiliate.py`; every command wraps a service call in `asyncio.run(_go())`; no SQL here).

```python
"""`poindexter community` — Reddit/IndieHackers draft assistant.

Thin adapter over ``services.community_drafts`` + ``services.subreddit_import``
(no inline SQL, per the transport-adapter-contract ADR). On-demand, draft-only:
generates native founder-voice drafts the operator reviews and posts manually.
"""
from __future__ import annotations

import asyncio

import click

from services.community_drafts import (
    SubredditProfile, add_profile, discard_draft, edit_draft, edit_profile,
    generate_reddit_draft, get_draft, list_drafts, list_profiles, mark_posted,
    remove_profile, set_profile_enabled, suggest_subreddits_for_post,
)
from services.subreddit_import import export_csv, import_csv


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
        pass  # best-effort settings load; generation needs the writer model though
    return site_config


@click.group(name="community")
def community_group() -> None:
    """Reddit/IndieHackers draft assistant (profiles / draft / drafts)."""


# ------------------------------------------------------------------ profiles
@community_group.group(name="profiles")
def profiles_group() -> None:
    """Manage subreddit profiles (add/list/edit/enable/disable/rm/import-csv/export-csv)."""


@profiles_group.command(name="list")
@click.option("--enabled", "enabled_only", is_flag=True, help="Only enabled profiles.")
def profiles_list(enabled_only):
    async def _go():
        pool = await _connect()
        try:
            return await list_profiles(pool, enabled_only=enabled_only)
        finally:
            await pool.close()

    profiles = asyncio.run(_go())
    if not profiles:
        click.echo("No subreddit profiles.")
        return
    for p in profiles:
        state = "on " if p.enabled else "off"
        ct = ",".join(p.content_types)
        click.echo(f"  [{state}] {p.subreddit:20} {p.post_type:6} {p.self_promo:8} {ct}")


@profiles_group.command(name="show")
@click.argument("subreddit")
def profiles_show(subreddit):
    from services.community_drafts import get_profile

    async def _go():
        pool = await _connect()
        try:
            return await get_profile(pool, subreddit)
        finally:
            await pool.close()

    p = asyncio.run(_go())
    if p is None:
        raise click.ClickException(f"No profile for '{subreddit}'.")
    for f in ("subreddit", "enabled", "content_types", "post_type", "self_promo",
              "flair", "min_karma", "min_account_age_days", "rules_summary",
              "tone_notes", "cadence_cap_days"):
        click.echo(f"  {f:20} {getattr(p, f)}")


def _profile_options(f):
    f = click.option("--content-types", help="';'-delimited classifier labels this sub accepts.")(f)
    f = click.option("--post-type", type=click.Choice(["text", "link", "either"]))(f)
    f = click.option("--self-promo", type=click.Choice(["strict", "moderate", "ok"]))(f)
    f = click.option("--flair", help="Default flair to set.")(f)
    f = click.option("--min-karma", type=int)(f)
    f = click.option("--min-account-age-days", type=int)(f)
    f = click.option("--rules", "rules_summary", help="Rules + culture, fed to the LLM.")(f)
    f = click.option("--tone", "tone_notes", help="How to write for this sub.")(f)
    f = click.option("--cadence-cap-days", type=int)(f)
    return f


@profiles_group.command(name="add")
@click.argument("subreddit")
@_profile_options
def profiles_add(subreddit, content_types, post_type, self_promo, flair, min_karma,
                 min_account_age_days, rules_summary, tone_notes, cadence_cap_days):
    """Add a new subreddit profile (fails if it exists — use `edit`)."""
    profile = SubredditProfile(
        subreddit=subreddit,
        content_types=[c.strip() for c in (content_types or "").split(";") if c.strip()],
        post_type=post_type or "text", self_promo=self_promo or "strict",
        flair=flair, min_karma=min_karma, min_account_age_days=min_account_age_days,
        rules_summary=rules_summary or "", tone_notes=tone_notes or "",
        cadence_cap_days=cadence_cap_days,
    )

    async def _go():
        pool = await _connect()
        try:
            return await add_profile(pool, profile)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException(f"Profile '{subreddit}' already exists — use `edit`.")
    click.secho(f"Added subreddit profile '{subreddit}'.", fg="green")


@profiles_group.command(name="edit")
@click.argument("subreddit")
@_profile_options
def profiles_edit(subreddit, content_types, post_type, self_promo, flair, min_karma,
                  min_account_age_days, rules_summary, tone_notes, cadence_cap_days):
    """Edit an existing profile (only the flags you pass change)."""
    changes = dict(
        content_types=([c.strip() for c in content_types.split(";") if c.strip()]
                       if content_types is not None else None),
        post_type=post_type, self_promo=self_promo, flair=flair, min_karma=min_karma,
        min_account_age_days=min_account_age_days, rules_summary=rules_summary,
        tone_notes=tone_notes, cadence_cap_days=cadence_cap_days,
    )

    async def _go():
        pool = await _connect()
        try:
            return await edit_profile(pool, subreddit, **changes)
        finally:
            await pool.close()

    try:
        asyncio.run(_go())
    except KeyError:
        raise click.ClickException(f"No profile for '{subreddit}'.")
    click.secho(f"Updated '{subreddit}'.", fg="green")


@profiles_group.command(name="enable")
@click.argument("subreddit")
def profiles_enable(subreddit):
    _set_enabled(subreddit, True)


@profiles_group.command(name="disable")
@click.argument("subreddit")
def profiles_disable(subreddit):
    _set_enabled(subreddit, False)


def _set_enabled(subreddit, enabled):
    async def _go():
        pool = await _connect()
        try:
            return await set_profile_enabled(pool, subreddit, enabled)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException(f"No profile for '{subreddit}'.")
    click.secho(f"{'Enabled' if enabled else 'Disabled'} '{subreddit}'.", fg="green")


@profiles_group.command(name="rm")
@click.argument("subreddit")
def profiles_rm(subreddit):
    async def _go():
        pool = await _connect()
        try:
            return await remove_profile(pool, subreddit)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException(f"No profile for '{subreddit}'.")
    click.secho(f"Removed '{subreddit}'.", fg="green")


@profiles_group.command(name="import-csv")
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--force", is_flag=True, help="Update existing rows instead of skipping them.")
def profiles_import_csv(csv_path, force):
    """Bulk import subreddit profiles from a spreadsheet export."""
    async def _go():
        pool = await _connect()
        try:
            return await import_csv(pool, csv_path, force=force)
        finally:
            await pool.close()

    report = asyncio.run(_go())
    for r in report.rows:
        color = {"created": "green", "updated": "green",
                 "skipped_exists": "yellow", "error": "red"}.get(r.status, "white")
        extra = f" ({r.detail})" if r.detail else ""
        click.secho(f"  {r.status:15} {r.subreddit}{extra}", fg=color)
    click.echo(
        f"\n{len(report.created)} created, {len(report.updated)} updated, "
        f"{len(report.skipped)} skipped, {len(report.errors)} errors."
    )


@profiles_group.command(name="export-csv")
@click.argument("csv_path", type=click.Path(dir_okay=False), required=False)
def profiles_export_csv(csv_path):
    """Export every subreddit profile as CSV (to a file, or stdout)."""
    async def _go():
        pool = await _connect()
        try:
            return await export_csv(pool)
        finally:
            await pool.close()

    text = asyncio.run(_go())
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            f.write(text)
        click.secho(f"Wrote {csv_path}.", fg="green")
    else:
        click.echo(text)


# --------------------------------------------------------------------- draft
@community_group.group(name="draft")
def draft_group() -> None:
    """Generate a native founder-voice draft."""


@draft_group.command(name="reddit")
@click.argument("post")
@click.option("--subreddit", help="Target subreddit profile. Omit to see suggestions.")
def draft_reddit(post, subreddit):
    """Generate a Reddit value-post for a published POST (slug or id)."""
    if not subreddit:
        async def _suggest():
            pool = await _connect()
            try:
                return await suggest_subreddits_for_post(pool, post)
            finally:
                await pool.close()

        subs = asyncio.run(_suggest())
        if subs:
            click.echo("Matching subreddits (pass one via --subreddit):")
            for s in subs:
                click.echo(f"  {s}")
        else:
            click.echo("No matching subreddit profiles (post unclassified?). "
                       "Pass --subreddit explicitly.")
        raise click.ClickException("Choose a subreddit with --subreddit.")

    async def _go():
        pool = await _connect()
        try:
            site_config = await _make_site_config(pool)
            return await generate_reddit_draft(
                pool, post_id=post, subreddit=subreddit, site_config=site_config
            )
        finally:
            await pool.close()

    try:
        draft = asyncio.run(_go())
    except (KeyError, ValueError) as e:
        raise click.ClickException(str(e))
    click.secho(f"Draft #{draft.id} for {draft.target}:", fg="green")
    if draft.warnings:
        click.secho("  warnings:", fg="yellow")
        for w in draft.warnings:
            click.secho(f"    - {w}", fg="yellow")
    click.echo("\n" + draft.body)


# -------------------------------------------------------------------- drafts
@community_group.group(name="drafts")
def drafts_group() -> None:
    """List / review / mark-posted / discard community drafts."""


@drafts_group.command(name="list")
@click.option("--status", type=click.Choice(["draft", "posted", "discarded"]))
def drafts_list(status):
    async def _go():
        pool = await _connect()
        try:
            return await list_drafts(pool, status=status)
        finally:
            await pool.close()

    drafts = asyncio.run(_go())
    if not drafts:
        click.echo("No drafts.")
        return
    for d in drafts:
        click.echo(f"  #{d.id:<4} {d.status:9} {d.target:24} {(d.title or '')[:50]}")


@drafts_group.command(name="show")
@click.argument("draft_id", type=int)
def drafts_show(draft_id):
    async def _go():
        pool = await _connect()
        try:
            return await get_draft(pool, draft_id)
        finally:
            await pool.close()

    d = asyncio.run(_go())
    if d is None:
        raise click.ClickException(f"No draft #{draft_id}.")
    click.echo(f"#{d.id} {d.status} {d.target}")
    if d.warnings:
        click.secho("warnings: " + "; ".join(d.warnings), fg="yellow")
    if d.posted_url:
        click.echo(f"posted: {d.posted_url}")
    click.echo("\n" + d.body)


@drafts_group.command(name="edit")
@click.argument("draft_id", type=int)
@click.option("--title")
@click.option("--body-file", type=click.Path(exists=True, dir_okay=False),
              help="Read the new body from this file.")
def drafts_edit(draft_id, title, body_file):
    body = None
    if body_file:
        with open(body_file, encoding="utf-8") as f:
            body = f.read()

    async def _go():
        pool = await _connect()
        try:
            return await edit_draft(pool, draft_id, title=title, body=body)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException("Nothing changed (pass --title and/or --body-file).")
    click.secho(f"Updated draft #{draft_id}.", fg="green")


@drafts_group.command(name="mark-posted")
@click.argument("draft_id", type=int)
@click.option("--url", required=True, help="Permalink of the post you published.")
def drafts_mark_posted(draft_id, url):
    async def _go():
        pool = await _connect()
        try:
            return await mark_posted(pool, draft_id, url=url)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException(f"No draft #{draft_id}.")
    click.secho(f"Marked draft #{draft_id} posted → {url}", fg="green")


@drafts_group.command(name="discard")
@click.argument("draft_id", type=int)
def drafts_discard(draft_id):
    async def _go():
        pool = await _connect()
        try:
            return await discard_draft(pool, draft_id)
        finally:
            await pool.close()

    if not asyncio.run(_go()):
        raise click.ClickException(f"No draft #{draft_id}.")
    click.secho(f"Discarded draft #{draft_id}.", fg="green")


__all__ = ["community_group"]
```

- [ ] **Step 4: Register the group in `poindexter/cli/app.py`.**

Add the import next to the other group imports (near line 12, `from .affiliate import affiliate_group`):

```python
from .community import community_group
```

Add the registration in the `main.add_command(...)` block (after the `affiliate` line, ~line 118):

```python
main.add_command(community_group, name="community")
```

- [ ] **Step 5: Run — verify it passes.**

```bash
PYTHONPATH="$SRC" "$VENV" -m pytest "$SRC/tests/unit/poindexter/cli/test_community_cli.py" -o addopts="" -q
```

- [ ] **Step 6: Smoke the command tree loads** (catches registration/import errors):

```bash
PYTHONPATH="$SRC" "$VENV" -m poindexter.cli.app community --help
PYTHONPATH="$SRC" "$VENV" -m poindexter.cli.app community profiles --help
```

Expected: help text listing `profiles` / `draft` / `drafts` and the profiles subcommands. (If `-m poindexter.cli.app` isn't the entry module, use the same invocation the repo uses for `poindexter --help`; the point is to load the group without error.)

- [ ] **Step 7: Commit**

```bash
git add src/cofounder_agent/poindexter/cli/community.py \
        src/cofounder_agent/poindexter/cli/app.py \
        src/cofounder_agent/tests/unit/poindexter/cli/test_community_cli.py
git commit -m "feat(community): poindexter community CLI (profiles/draft/drafts)"
```

---

## Task 7: integration_db round-trip

**Files:**

- Create: `src/cofounder_agent/tests/integration_db/test_community_drafts_roundtrip.py`

**Interfaces:**

- Consumes: the services from Tasks 2-5 against real Postgres (`test_pool`).

- [ ] **Step 1: Write the round-trip tests.**

```python
"""End-to-end round-trips against real Postgres: profile CRUD, a draft
lifecycle, and CSV export→import (verifies text[] columns survive the trip).
Skips cleanly when no live Postgres is available."""
from __future__ import annotations

import pytest

from services.community_drafts import (
    SubredditProfile, add_profile, create_draft, get_draft, get_profile,
    list_drafts, mark_posted, suggest_subreddits_for_post,
)
from services.subreddit_import import export_csv, import_csv

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_profile_insert_and_read_back(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="rollback-for-test-isolation"):
            async with conn.transaction():
                await add_profile(conn, SubredditProfile(
                    subreddit="itest-localllama", content_types=["ai-ml", "pc-hardware"],
                    post_type="text", self_promo="strict", flair="Discussion", min_karma=100,
                ))
                p = await get_profile(conn, "itest-localllama")
                assert p is not None
                assert p.content_types == ["ai-ml", "pc-hardware"]   # text[] round-trip
                assert p.min_karma == 100 and p.flair == "Discussion"
                raise RuntimeError("rollback-for-test-isolation")


async def test_draft_lifecycle(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="rollback-for-test-isolation"):
            async with conn.transaction():
                did = await create_draft(
                    conn, target="reddit:itest", body="native body",
                    title="T", post_type="text",
                    warnings=["self-promo=strict: post as native text, no blog link"],
                    model="gemma",
                )
                d = await get_draft(conn, did)
                assert d.status == "draft" and d.warnings[0].startswith("self-promo=strict")
                assert await mark_posted(conn, did, url="https://reddit.com/r/itest/x") is True
                d2 = await get_draft(conn, did)
                assert d2.status == "posted" and d2.posted_url.endswith("/x")
                raise RuntimeError("rollback-for-test-isolation")


async def test_suggest_matches_on_content_type(test_pool):
    async with test_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="rollback-for-test-isolation"):
            async with conn.transaction():
                post_id = await conn.fetchval(
                    "INSERT INTO posts (title, slug, body, status) "
                    "VALUES ('t','itest-suggest-slug','b','published') RETURNING id"
                )
                await conn.execute(
                    "INSERT INTO post_content_types (post_id, content_type) VALUES ($1,'ai-ml')",
                    post_id,
                )
                await add_profile(conn, SubredditProfile(
                    subreddit="itest-match", content_types=["ai-ml"], enabled=True))
                await add_profile(conn, SubredditProfile(
                    subreddit="itest-nomatch", content_types=["gaming"], enabled=True))
                out = await suggest_subreddits_for_post(conn, str(post_id))
                assert "itest-match" in out and "itest-nomatch" not in out
                raise RuntimeError("rollback-for-test-isolation")


async def test_csv_export_import_roundtrip(test_pool, tmp_path):
    async with test_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="rollback-for-test-isolation"):
            async with conn.transaction():
                await add_profile(conn, SubredditProfile(
                    subreddit="itest-csv", content_types=["ai-ml", "gaming"],
                    post_type="either", self_promo="moderate", min_karma=50,
                    rules_summary="No memes.", cadence_cap_days=7))
                text = await export_csv(conn)
                assert "itest-csv" in text and "ai-ml;gaming" in text
                f = tmp_path / "out.csv"; f.write_text(text, encoding="utf-8")
                # re-import with --force updates in place (idempotent round-trip)
                rep = await import_csv(conn, str(f), force=True)
                assert any(r.subreddit == "itest-csv" and r.status == "updated"
                           for r in rep.rows)
                back = await get_profile(conn, "itest-csv")
                assert back.content_types == ["ai-ml", "gaming"]     # array survived CSV
                assert back.min_karma == 50 and back.cadence_cap_days == 7
                raise RuntimeError("rollback-for-test-isolation")
```

> The services take `pool.execute/fetch/...`; an acquired `conn` satisfies the same interface, so passing `conn` (inside the rollback transaction) works and keeps every test's writes isolated — the same idiom the affiliate export integration test uses.

- [ ] **Step 2: Run.**

```bash
PYTHONPATH="$SRC" "$VENV" -m pytest "$SRC/tests/integration_db/test_community_drafts_roundtrip.py" -o addopts="" -q
```

Expected: PASS (or clean SKIP with no Postgres). If a `posts` NOT NULL column beyond `(title, slug, body, status)` rejects the insert in `test_suggest_matches_on_content_type`, add the missing column(s) to that INSERT — inspect with `\d posts` or the baseline schema.

- [ ] **Step 3: Commit**

```bash
git add src/cofounder_agent/tests/integration_db/test_community_drafts_roundtrip.py
git commit -m "test(community): integration_db round-trips (profiles/drafts/CSV)"
```

---

## Task 8: Architecture doc + services.md regen

**Files:**

- Create: `docs/architecture/community-draft-assistant.md`
- Modify: `docs/reference/services.md` (regenerated)

**Interfaces:** none (docs only).

- [ ] **Step 1: Write `docs/architecture/community-draft-assistant.md`** — product-generic (no operator identity, no real subreddit names, no internal issue refs). Cover: the two tables, the on-demand draft-only lifecycle, the deterministic warnings + link-append, the content-type match seam (reuses `post_content_types`), the SKILL.md prompt pack, and the `poindexter community` CLI (profiles CRUD + CSV round-trip, `draft reddit`, `drafts`). State that IndieHackers generation is a planned follow-up. Keep it to the shape of `docs/architecture/content-type-classification.md`.

- [ ] **Step 2: Regenerate the services doc** (new service files must appear in the auto-generated reference, or the `regen-services-doc` CI drift check fails — this bit WS1.5 as PR #2415):

```bash
PYTHONPATH="$SRC" "$VENV" "$SRC/../../scripts/regen-services-doc.py"
```

- [ ] **Step 3: Verify the drift check is clean.**

```bash
git diff --stat docs/reference/services.md
```

Expected: `services.md` now lists `services/community_drafts.py` + `services/subreddit_import.py`.

- [ ] **Step 4: Commit**

```bash
git add docs/architecture/community-draft-assistant.md docs/reference/services.md
git commit -m "docs(community): architecture doc + services.md regen"
```

---

## Final verification (before finishing the branch)

- [ ] **Run the full community unit suite:**

```bash
PYTHONPATH="$SRC" "$VENV" -m pytest \
  "$SRC/tests/unit/services/test_community_settings_defaults.py" \
  "$SRC/tests/unit/services/test_community_drafts_crud.py" \
  "$SRC/tests/unit/services/test_community_drafts_drafts.py" \
  "$SRC/tests/unit/services/test_community_reddit_generate.py" \
  "$SRC/tests/unit/services/test_community_reddit_skill.py" \
  "$SRC/tests/unit/services/test_subreddit_import.py" \
  "$SRC/tests/unit/poindexter/cli/test_community_cli.py" \
  -o addopts="" -q
```

Expected: all green.

- [ ] **Run the integration_db suite** (or confirm clean skip):

```bash
PYTHONPATH="$SRC" "$VENV" -m pytest \
  "$SRC/tests/integration_db/test_community_draft_tables.py" \
  "$SRC/tests/integration_db/test_community_drafts_roundtrip.py" \
  -o addopts="" -q
```

- [ ] **Lint the new code** (ruff over the two service files + CLI; fix any finding — `feedback_fix_preexisting_issues`):

```bash
PYTHONPATH="$SRC" "$VENV" -m ruff check \
  "$SRC/services/community_drafts.py" "$SRC/services/subreddit_import.py" \
  "$SRC/poindexter/cli/community.py"
```

- [ ] **Adapter-purity lint** (guards the CLI has no net-new inline SQL):

```bash
PYTHONPATH="$SRC" "$VENV" "$SRC/../../scripts/ci/adapter_purity_lint.py"
```

- [ ] **Finish the branch** — REQUIRED SUB-SKILL: `superpowers:finishing-a-development-branch`. Push to the existing draft PR #2449 (branch `claude/community-draft-assistant`); once CI is green, self-merge (squash) per `feedback_ci_is_the_review_gate`. PR2 (IndieHackers) is a separate later plan.

---

## Self-review (completed by the plan author)

- **Spec coverage:** both tables (T1) ✓; settings (T1, `community_ih_lookback_days` explicitly deferred to PR2 with rationale) ✓; profiles CRUD (T2) ✓; content-type match + draft CRUD (T3) ✓; Reddit generation + SKILL.md + deterministic warnings/link (T4) ✓; CSV import/export mirroring affiliate_import (T5) ✓; full `poindexter community` CLI (T6) ✓; unit + integration_db (T2-T7) ✓; architecture doc (T8) ✓. Spec test items #1-4, #6, #7 map to T2/T3/T4/T5 tests; **#5 (IH generation) is PR2**, correctly out of scope.
- **Type consistency:** `SubredditProfile` / `CommunityDraft` field names are identical across T2-T7 and the CLI. Service signatures in the surface block match every call site (CLI, importer, tests). `content_types` is a `list[str]` everywhere in Python and `text[]` in SQL; the `;`-delimited form appears only inside CSV cells (parse/serialize symmetric in T5).
- **Placeholder scan:** every code step carries complete code; no TBD/TODO. The one deliberately-prose step is T8-Step-1 (the architecture doc), which specifies exact required sections + a reference file to match.
- **Deviations from spec (flagged):** (a) `community_ih_lookback_days` deferred to PR2 to avoid a zero-reader key; (b) `community_draft_timeout_seconds` added (not in the spec's 2-row table) so the LLM timeout is DB-tunable rather than a hardcoded literal; (c) the blog-link decision is deterministic in the service (spec §"Generation engines" left it prompt-guided) — this makes spec test #3 actually testable and honors `feedback_calculated_vs_generated`.
