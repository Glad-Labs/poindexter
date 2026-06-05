"""Contract tests for ``modules.content.auto_publish_gate.evaluate``.

Pins the 2026-05-27 niche-leak fix. Before the fix, the gate read a
HARDCODED ``dev_diary_auto_publish_threshold`` regardless of the
caller's ``niche_slug``. Result: a glad-labs niche post scoring 92
("Claude Is Not Your Architect. Stop.") auto-published 2026-05-26
13:45 UTC without operator approval, because the operator's
dev_diary opt-in (``threshold=70``, ``dry_run=false``) cross-
pollinated to every other niche.

The fix reads niche-prefixed keys
(``{niche_slug}_auto_publish_{threshold|dry_run|min_clean_runs|max_edit_distance}``)
and returns ``disabled`` when ``niche_slug`` is missing — every
niche must opt in via its own keys, per
``feedback_no_silent_defaults``.

These tests pin the contract so a future refactor can't silently
re-introduce the cross-niche leak.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_platform(settings: dict[str, Any]) -> Any:
    """Minimal platform handle double — only ``config.get(key, default)`` is read.

    Seam 1 Wave 3e (#667): auto_publish_gate now reads config via
    ``platform.config.get`` instead of ``site_config.get``.
    """
    p = MagicMock()
    p.config.get = MagicMock(
        side_effect=lambda key, default=None: settings.get(key, default)
    )
    return p


def _make_pool(rows: list[dict[str, Any]] | None = None) -> Any:
    """asyncpg pool double — supplies ``conn.fetch`` with caller-controlled rows."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows or [])

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


# ---------------------------------------------------------------------------
# Niche-leak regression — THE bug 2026-05-27 fix prevents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dev_diary_opt_in_does_not_leak_to_other_niches() -> None:
    """The exact 2026-05-26 production bug. The operator had configured
    ``dev_diary_auto_publish_threshold=70`` + ``dev_diary_auto_publish_dry_run=false``
    to opt in dev_diary. A canonical_blog/glad-labs post scoring 92 must
    NOT inherit that opt-in — glad-labs has no explicit opt-in keys,
    so the gate must return ``disabled``."""
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({
        "dev_diary_auto_publish_threshold": "70",
        "dev_diary_auto_publish_dry_run": "false",
        # NOTE: zero glad-labs_* keys — glad-labs has not opted in.
    })

    decision = await evaluate(
        _make_pool(),
        task_id="t1",
        niche_slug="glad-labs",
        category="technology",
        quality_score=92.0,
        platform=site_config,
    )

    assert decision.would_fire is False, (
        f"dev_diary opt-in leaked to glad-labs niche — gate would_fire={decision.would_fire}, "
        f"gate_state={decision.gate_state}, reason={decision.reason}. "
        "This is the exact 2026-05-26 production incident."
    )
    assert decision.gate_state == "disabled"


# ---------------------------------------------------------------------------
# Each-niche-opts-in-separately contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dev_diary_opt_in_still_works_for_dev_diary_niche() -> None:
    """Backward-compat: dev_diary's existing keys still control dev_diary
    posts. The niche-leak fix doesn't break the niche it was named after."""
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({
        "dev_diary_auto_publish_threshold": "70",
        "dev_diary_auto_publish_dry_run": "false",
        "dev_diary_auto_publish_min_clean_runs": "3",
        "dev_diary_auto_publish_max_edit_distance": "50",
    })

    # 3 clean historical approves (char_diff < 50) → would_fire=True
    pool = _make_pool([
        {"char_diff_count": 10},
        {"char_diff_count": 20},
        {"char_diff_count": 5},
    ])

    decision = await evaluate(
        pool,
        task_id="t1",
        niche_slug="dev_diary",
        category="dev",
        quality_score=92.0,
        platform=site_config,
    )

    assert decision.would_fire is True, (
        f"dev_diary niche own opt-in broken — gate_state={decision.gate_state}, "
        f"reason={decision.reason}"
    )
    assert decision.dry_run is False
    assert decision.gate_state == "pass"


@pytest.mark.asyncio
async def test_glad_labs_opts_in_via_its_own_keys() -> None:
    """When the operator explicitly opts in glad-labs via
    ``glad-labs_auto_publish_threshold=70`` + ``glad-labs_auto_publish_dry_run=false``,
    the gate fires for glad-labs (independent of dev_diary)."""
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({
        "glad-labs_auto_publish_threshold": "70",
        "glad-labs_auto_publish_dry_run": "false",
        "glad-labs_auto_publish_min_clean_runs": "3",
        "glad-labs_auto_publish_max_edit_distance": "50",
        # dev_diary deliberately NOT set — proves independence.
    })

    pool = _make_pool([
        {"char_diff_count": 10},
        {"char_diff_count": 20},
        {"char_diff_count": 5},
    ])

    decision = await evaluate(
        pool,
        task_id="t1",
        niche_slug="glad-labs",
        category="technology",
        quality_score=92.0,
        platform=site_config,
    )

    assert decision.would_fire is True
    assert decision.gate_state == "pass"


# ---------------------------------------------------------------------------
# Disabled-by-default paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_niche_slug_returns_disabled() -> None:
    """``feedback_no_silent_defaults``: a task without a niche cannot
    auto-publish. The gate must NOT pick an arbitrary fallback niche."""
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({
        "dev_diary_auto_publish_threshold": "70",
        "dev_diary_auto_publish_dry_run": "false",
    })

    decision = await evaluate(
        _make_pool(),
        task_id="t1",
        niche_slug=None,
        category="technology",
        quality_score=92.0,
        platform=site_config,
    )

    assert decision.would_fire is False
    assert decision.gate_state == "disabled"
    assert "niche_slug missing" in decision.reason


@pytest.mark.asyncio
async def test_empty_niche_slug_returns_disabled() -> None:
    """Whitespace-only niche slug is treated the same as None."""
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({})

    decision = await evaluate(
        _make_pool(),
        task_id="t1",
        niche_slug="   ",
        category="x",
        quality_score=99.0,
        platform=site_config,
    )

    assert decision.would_fire is False
    assert decision.gate_state == "disabled"


@pytest.mark.asyncio
async def test_no_platform_returns_disabled() -> None:
    """Stages running without a platform handle (e.g. legacy callers) must not
    auto-publish — they have no operator-tuned settings to read."""
    from modules.content.auto_publish_gate import evaluate

    decision = await evaluate(
        _make_pool(),
        task_id="t1",
        niche_slug="dev_diary",
        category="dev",
        quality_score=99.0,
        platform=None,
    )

    assert decision.would_fire is False
    assert decision.gate_state == "disabled"


@pytest.mark.asyncio
async def test_threshold_negative_returns_disabled() -> None:
    """The default ``threshold=-1`` opts OUT — the gate must not fire
    even on a perfect score."""
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({
        "dev_diary_auto_publish_threshold": "-1",
        "dev_diary_auto_publish_dry_run": "false",
    })

    decision = await evaluate(
        _make_pool(),
        task_id="t1",
        niche_slug="dev_diary",
        category="dev",
        quality_score=100.0,
        platform=site_config,
    )

    assert decision.would_fire is False
    assert decision.gate_state == "disabled"
    assert "< 0" in decision.reason


# ---------------------------------------------------------------------------
# Score / clean-run gate paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_below_threshold_returns_block_threshold() -> None:
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({
        "dev_diary_auto_publish_threshold": "80",
        "dev_diary_auto_publish_dry_run": "false",
    })

    decision = await evaluate(
        _make_pool(),
        task_id="t1",
        niche_slug="dev_diary",
        category="dev",
        quality_score=70.0,
        platform=site_config,
    )

    assert decision.would_fire is False
    assert decision.gate_state == "block_threshold"


@pytest.mark.asyncio
async def test_insufficient_history_returns_no_history() -> None:
    """Until N historical approves exist, the gate can't establish the
    clean-run baseline — return ``no_history``."""
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({
        "dev_diary_auto_publish_threshold": "70",
        "dev_diary_auto_publish_dry_run": "false",
        "dev_diary_auto_publish_min_clean_runs": "3",
    })

    # Only 1 historical approve, need 3.
    pool = _make_pool([{"char_diff_count": 5}])

    decision = await evaluate(
        pool,
        task_id="t1",
        niche_slug="dev_diary",
        category="dev",
        quality_score=90.0,
        platform=site_config,
    )

    assert decision.would_fire is False
    assert decision.gate_state == "no_history"


@pytest.mark.asyncio
async def test_unclean_history_returns_block_unclean() -> None:
    """Enough history, but too many were heavily edited — block."""
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({
        "dev_diary_auto_publish_threshold": "70",
        "dev_diary_auto_publish_dry_run": "false",
        "dev_diary_auto_publish_min_clean_runs": "3",
        "dev_diary_auto_publish_max_edit_distance": "50",
    })

    # 3 rows, only 1 is "clean" (< 50 edits) — need 3 clean.
    pool = _make_pool([
        {"char_diff_count": 200},
        {"char_diff_count": 300},
        {"char_diff_count": 10},
    ])

    decision = await evaluate(
        pool,
        task_id="t1",
        niche_slug="dev_diary",
        category="dev",
        quality_score=90.0,
        platform=site_config,
    )

    assert decision.would_fire is False
    assert decision.gate_state == "block_unclean"
    assert decision.trailing_clean_runs == 1


# ---------------------------------------------------------------------------
# Dry-run signal preserved on pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_true_marks_decision_dry_run_even_when_would_fire() -> None:
    """``dry_run=true`` is the observe-only mode — the gate still
    computes ``would_fire=True`` so dashboards can show it, but the
    caller must NOT approve the task."""
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({
        "dev_diary_auto_publish_threshold": "70",
        "dev_diary_auto_publish_dry_run": "true",  # observe-only
        "dev_diary_auto_publish_min_clean_runs": "3",
        "dev_diary_auto_publish_max_edit_distance": "50",
    })

    pool = _make_pool([
        {"char_diff_count": 5},
        {"char_diff_count": 10},
        {"char_diff_count": 15},
    ])

    decision = await evaluate(
        pool,
        task_id="t1",
        niche_slug="dev_diary",
        category="dev",
        quality_score=92.0,
        platform=site_config,
    )

    assert decision.would_fire is True
    assert decision.dry_run is True
    assert decision.gate_state == "pass"


# ---------------------------------------------------------------------------
# History-query shape — the niche-OR-category filter (#647)
# ---------------------------------------------------------------------------


def _make_capturing_pool(rows: list[dict[str, Any]] | None = None):
    """asyncpg pool double that captures the SQL + bound args of the
    history ``conn.fetch`` so the query shape can be asserted."""
    captured: dict[str, Any] = {}

    async def _fetch(sql, *args):
        captured["sql"] = sql
        captured["args"] = args
        return rows or []

    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=_fetch)

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, captured


@pytest.mark.asyncio
async def test_history_query_filters_on_niche_OR_category() -> None:
    """Pin the trailing-clean-run history query (lines ~187-197).

    The ``WHERE COALESCE(niche_slug,'')=$1 OR COALESCE(category,'')=$2``
    filter is an OR — so a row matching EITHER the niche OR the category
    is counted. That OR-bleed is intentional (a niche with no history of
    its own can borrow its category's track record), but it MUST stay
    visible: a category shared across niches means one niche's edit
    history can influence another's gate. This test makes the OR
    explicit so a refactor to AND (or a hardcoded niche) is caught, and
    confirms BOTH niche_slug ($1) and category ($2) are bound."""
    from modules.content.auto_publish_gate import evaluate

    site_config = _make_platform({
        "dev_diary_auto_publish_threshold": "70",
        "dev_diary_auto_publish_dry_run": "false",
        "dev_diary_auto_publish_min_clean_runs": "3",
        "dev_diary_auto_publish_max_edit_distance": "50",
    })

    pool, captured = _make_capturing_pool([
        {"char_diff_count": 5},
        {"char_diff_count": 10},
        {"char_diff_count": 15},
    ])

    await evaluate(
        pool,
        task_id="t1",
        niche_slug="dev_diary",
        category="engineering",
        quality_score=92.0,
        platform=site_config,
    )

    sql = " ".join(captured["sql"].split())  # collapse whitespace
    assert "published_post_edit_metrics" in sql
    # The OR-bleed: niche_slug OR category. Guard both halves + the OR.
    assert "niche_slug" in sql
    assert "category" in sql
    assert " OR " in sql, (
        "history query must keep the niche-OR-category filter — "
        f"got: {sql}"
    )
    # Both filter values are bound: $1=niche_slug, $2=category, $3=limit.
    args = captured["args"]
    assert args[0] == "dev_diary"
    assert args[1] == "engineering"
    # LIMIT is max(min_clean, 1) = 3.
    assert args[2] == 3
