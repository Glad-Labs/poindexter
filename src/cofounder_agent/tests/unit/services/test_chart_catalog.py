"""Unit tests for ``services/chart_catalog.py``.

The catalog is the allowlist that lets a writer choose WHICH chart without ever
choosing what it says. The security-shaped properties are pinned hardest: an
unknown key resolves to nothing rather than something plausible, charts are
code-defined so no LLM-chosen key can reach operator-authored SQL, and every
failure mode collapses to "render no chart" because a wrong chart is worse than
none.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import chart_catalog

pytestmark = pytest.mark.unit


def _sc(keys: str = "") -> Any:
    return SimpleNamespace(get=lambda k, d="": keys if "enabled_keys" in k else d)


def _pool(rows: list[dict] | None = None):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows if rows is not None else _ROWS)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


_ROWS = [
    {"model": "phi4:14b", "calls": 243, "first_seen": "2026-08-26",
     "last_seen": "2026-09-01", "decode_tps": 124.7, "wall_tps": 25.3, "overhead_ms": 8872},
    {"model": "glm-4.7-5090:latest", "calls": 34, "first_seen": "2026-08-28",
     "last_seen": "2026-08-30", "decode_tps": 177.0, "wall_tps": 172.2, "overhead_ms": 591},
]


class TestAllowlist:
    def test_empty_setting_permits_the_whole_catalog(self):
        """Charts are code-defined and read-only, so 'available' is the safe
        default; the operator lever exists to NARROW the list."""
        assert chart_catalog.enabled_keys(_sc("")) == chart_catalog.catalog_keys()

    def test_an_explicit_list_narrows_it(self):
        assert chart_catalog.enabled_keys(_sc("nothing-real")) == []

    def test_a_missing_site_config_still_yields_the_catalog(self):
        assert chart_catalog.enabled_keys(None) == chart_catalog.catalog_keys()

    def test_prompt_description_enumerates_the_keys(self):
        text = chart_catalog.describe_for_prompt(None)
        assert "llm-decode-vs-delivered" in text

    def test_prompt_says_do_not_use_when_nothing_is_enabled(self):
        text = chart_catalog.describe_for_prompt(_sc("nothing-real"))
        assert "do not use" in text


class TestResolve:
    async def test_a_catalogued_key_builds_a_real_spec(self):
        spec = await chart_catalog.resolve(
            "llm-decode-vs-delivered", pool=_pool(), site_config=None,
        )
        assert spec is not None
        assert spec.form == "bar"
        assert spec.categories == ["glm-4.7-5090:latest", "phi4:14b"]
        assert [s.label for s in spec.series] == ["Raw decode", "Delivered to caller"]

    async def test_the_spec_always_states_its_provenance(self):
        """A published chart must say what produced it and over what sample."""
        spec = await chart_catalog.resolve(
            "llm-decode-vs-delivered", pool=_pool(), site_config=None,
        )
        assert "cost_logs" in spec.source
        assert "277" in spec.source  # 243 + 34

    async def test_an_unknown_key_resolves_to_nothing(self):
        """A hallucinated key must render no image, never something plausible."""
        assert await chart_catalog.resolve(
            "totally-made-up", pool=_pool(), site_config=None,
        ) is None

    async def test_a_disabled_key_resolves_to_nothing(self):
        assert await chart_catalog.resolve(
            "llm-decode-vs-delivered", pool=_pool(), site_config=_sc("something-else"),
        ) is None

    async def test_no_pool_resolves_to_nothing(self):
        assert await chart_catalog.resolve(
            "llm-decode-vs-delivered", pool=None, site_config=None,
        ) is None

    async def test_one_model_is_not_a_comparison(self):
        """Rendering a single bar would imply a contrast the data cannot
        support."""
        assert await chart_catalog.resolve(
            "llm-decode-vs-delivered", pool=_pool([_ROWS[0]]), site_config=None,
        ) is None

    async def test_a_query_failure_resolves_to_nothing_rather_than_raising(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=RuntimeError("db gone"))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=ctx)
        assert await chart_catalog.resolve(
            "llm-decode-vs-delivered", pool=pool, site_config=None,
        ) is None

    async def test_key_matching_is_case_and_whitespace_tolerant(self):
        spec = await chart_catalog.resolve(
            "  LLM-Decode-Vs-Delivered  ", pool=_pool(), site_config=None,
        )
        assert spec is not None


class TestNoQuerySurfaceIsReachableFromASetting:
    def test_charts_are_code_defined_only(self):
        """A settings-defined chart would mean operator-authored SQL reachable
        from an LLM-chosen key — the seam this design exists to avoid."""
        import inspect

        src = inspect.getsource(chart_catalog)
        # The only setting the catalog reads is the allowlist.
        assert src.count("site_config.get(") == 1
        assert "_ENABLED_KEYS_SETTING" in src
