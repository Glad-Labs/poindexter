"""Unit tests for services.qa_gates_db.

The qa_gates_db module had no dedicated test file before this PR, so
load_qa_gate_chain was effectively 0% covered. This file exercises:
- Happy path — DB returns a list of rows -> ordered list of QAGateSpec
- pool=None -> empty list (graceful test fallback)
- DB error (table missing on fresh checkout) -> empty list, logged at debug
- only_enabled=False -> WHERE clause omits the enabled filter
- jsonb config returned as a string (some asyncpg versions/typecodecs)
- jsonb config returned as malformed JSON string -> fallback to {}
- applies_to_style filtering
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.qa_gates_db import QAGateSpec, load_qa_gate_chain


# ---------------------------------------------------------------------------
# QAGateSpec.applies_to_style
# ---------------------------------------------------------------------------


class TestAppliesToStyle:
    def test_empty_styles_applies_to_all(self):
        spec = QAGateSpec(
            name="x", stage_name="qa", execution_order=1,
            reviewer="ollama", required_to_pass=True, enabled=True,
            config={},
        )
        assert spec.applies_to_style("any-style") is True
        assert spec.applies_to_style(None) is True

    def test_explicit_styles_filter_includes(self):
        spec = QAGateSpec(
            name="x", stage_name="qa", execution_order=1,
            reviewer="ollama", required_to_pass=True, enabled=True,
            config={"applies_to_styles": ["dev", "tutorial"]},
        )
        assert spec.applies_to_style("dev") is True
        assert spec.applies_to_style("tutorial") is True

    def test_explicit_styles_filter_excludes_unlisted(self):
        spec = QAGateSpec(
            name="x", stage_name="qa", execution_order=1,
            reviewer="ollama", required_to_pass=True, enabled=True,
            config={"applies_to_styles": ["dev"]},
        )
        assert spec.applies_to_style("essay") is False

    def test_filter_with_none_writing_style(self):
        """Empty writing_style_id with restrictive list -> excluded."""
        spec = QAGateSpec(
            name="x", stage_name="qa", execution_order=1,
            reviewer="ollama", required_to_pass=True, enabled=True,
            config={"applies_to_styles": ["dev"]},
        )
        assert spec.applies_to_style(None) is False
        assert spec.applies_to_style("") is False

    def test_styles_compared_as_strings(self):
        """ID values may be ints in the DB but apply_to should still match."""
        spec = QAGateSpec(
            name="x", stage_name="qa", execution_order=1,
            reviewer="ollama", required_to_pass=True, enabled=True,
            config={"applies_to_styles": [1, 2, 3]},
        )
        assert spec.applies_to_style("1") is True
        assert spec.applies_to_style("4") is False

    def test_frozen_dataclass_is_immutable(self):
        spec = QAGateSpec(
            name="x", stage_name="qa", execution_order=1,
            reviewer="ollama", required_to_pass=True, enabled=True,
        )
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            spec.name = "y"


# ---------------------------------------------------------------------------
# load_qa_gate_chain
# ---------------------------------------------------------------------------


def _make_pool_with_rows(rows):
    """Build a mock pool whose acquire().__aenter__ yields a conn returning rows."""
    pool = MagicMock()
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)

    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


def _row(name, order=1, reviewer="ollama", required=True, enabled=True,
         stage_name="qa", config=None):
    return {
        "name": name,
        "stage_name": stage_name,
        "execution_order": order,
        "reviewer": reviewer,
        "required_to_pass": required,
        "enabled": enabled,
        "config": config if config is not None else {},
    }


class TestLoadQAGateChain:
    @pytest.mark.asyncio
    async def test_returns_empty_list_for_none_pool(self):
        result = await load_qa_gate_chain(None)
        assert result == []

    @pytest.mark.asyncio
    async def test_happy_path_returns_specs(self):
        rows = [
            _row("topic_delivery", order=1),
            _row("internal_consistency", order=2),
        ]
        pool, conn = _make_pool_with_rows(rows)
        chain = await load_qa_gate_chain(pool)
        assert len(chain) == 2
        assert chain[0].name == "topic_delivery"
        assert chain[0].execution_order == 1
        assert isinstance(chain[0], QAGateSpec)

    @pytest.mark.asyncio
    async def test_db_exception_returns_empty_list(self):
        """Missing table on fresh checkout falls back to legacy chain."""
        pool = MagicMock()
        acquire_ctx = MagicMock()
        acquire_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("relation qa_gates does not exist"))
        acquire_ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=acquire_ctx)
        result = await load_qa_gate_chain(pool)
        assert result == []

    @pytest.mark.asyncio
    async def test_only_enabled_true_includes_filter(self):
        rows = [_row("g", enabled=True)]
        pool, conn = _make_pool_with_rows(rows)
        await load_qa_gate_chain(pool, only_enabled=True)
        sql = conn.fetch.await_args.args[0]
        assert "enabled = TRUE" in sql

    @pytest.mark.asyncio
    async def test_only_enabled_false_omits_filter(self):
        """CLI list-disabled mode passes only_enabled=False."""
        rows = [_row("disabled_gate", enabled=False)]
        pool, conn = _make_pool_with_rows(rows)
        await load_qa_gate_chain(pool, only_enabled=False)
        sql = conn.fetch.await_args.args[0]
        assert "enabled = TRUE" not in sql

    @pytest.mark.asyncio
    async def test_stage_name_passed_as_param(self):
        rows = []
        pool, conn = _make_pool_with_rows(rows)
        await load_qa_gate_chain(pool, stage_name="post_publish")
        # First positional arg after SQL is the stage_name binding
        args = conn.fetch.await_args.args
        assert args[1] == "post_publish"

    @pytest.mark.asyncio
    async def test_config_as_json_string_parsed(self):
        """Some asyncpg versions return jsonb as text."""
        rows = [_row("g", config='{"applies_to_styles": ["dev"]}')]
        pool, conn = _make_pool_with_rows(rows)
        chain = await load_qa_gate_chain(pool)
        assert chain[0].config == {"applies_to_styles": ["dev"]}

    @pytest.mark.asyncio
    async def test_config_malformed_json_string_falls_back_to_empty(self):
        rows = [_row("g", config="not-json{{")]
        pool, conn = _make_pool_with_rows(rows)
        chain = await load_qa_gate_chain(pool)
        assert chain[0].config == {}

    @pytest.mark.asyncio
    async def test_config_none_becomes_empty_dict(self):
        rows = [_row("g", config=None)]
        pool, conn = _make_pool_with_rows(rows)
        chain = await load_qa_gate_chain(pool)
        assert chain[0].config == {}

    @pytest.mark.asyncio
    async def test_int_coercion_on_execution_order(self):
        """A weird DB type (e.g. Decimal) should still coerce cleanly."""
        rows = [_row("g", order="5")]  # string-typed
        pool, conn = _make_pool_with_rows(rows)
        chain = await load_qa_gate_chain(pool)
        assert chain[0].execution_order == 5
        assert isinstance(chain[0].execution_order, int)

    @pytest.mark.asyncio
    async def test_required_and_enabled_coerced_to_bool(self):
        rows = [_row("g", required=1, enabled=0)]  # int truthy/falsy
        pool, conn = _make_pool_with_rows(rows)
        chain = await load_qa_gate_chain(pool)
        assert chain[0].required_to_pass is True
        assert chain[0].enabled is False

    @pytest.mark.asyncio
    async def test_empty_result_set(self):
        pool, conn = _make_pool_with_rows([])
        chain = await load_qa_gate_chain(pool)
        assert chain == []
