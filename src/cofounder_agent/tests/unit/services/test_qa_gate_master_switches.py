"""A gate row must not be enabled while its rail's master switch is off.

poindexter#1065: ``ragas_enabled`` was flipped off but the ``ragas_eval`` gate
stayed enabled. A rail whose switch is off appends NO review, and
``missing_required_gates`` reads an absent required rail as a veto — so
graduating such a gate hard-rejects every post (the poindexter#1060 shape).
These tests pin the map, the pure conflict check, the service-layer refusal,
and that a fresh install ships consistent.
"""

from __future__ import annotations

import pathlib
import re
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.services.declarative_config_service import (
    SurfaceValidationError,
    upsert_row,
)
from poindexter.services.qa_gates_db import (
    RAIL_MASTER_SWITCHES,
    master_switch_conflicts,
    master_switch_is_on,
)
from poindexter.services.settings_defaults import DEFAULTS

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[3]  # src/cofounder_agent
_SEEDS = _ROOT / "poindexter" / "services" / "migrations" / "0000_baseline.seeds.sql"
_GATE_ROW = re.compile(
    r"INSERT INTO qa_gates .*? VALUES \('[^']*', '(?P<name>[^']+)', '[^']*', "
    r"\d+, '[^']*', (?P<required>true|false), (?P<enabled>true|false),"
)


def _baseline_gates() -> dict[str, dict]:
    gates = {}
    for m in _GATE_ROW.finditer(_SEEDS.read_text()):
        gates[m["name"]] = {
            "name": m["name"],
            "required_to_pass": m["required"] == "true",
            "enabled": m["enabled"] == "true",
        }
    return gates


def test_baseline_parse_found_the_gates():
    # Floor, so a reformatted seed file can't turn the checks below vacuous.
    assert len(_baseline_gates()) >= 15


def test_every_mapped_gate_is_a_real_gate():
    gates = _baseline_gates()
    missing = sorted(set(RAIL_MASTER_SWITCHES) - set(gates))
    assert not missing, f"RAIL_MASTER_SWITCHES names gates with no seed row: {missing}"


_SETTING_ROW = re.compile(
    r"INSERT INTO app_settings \(key, value[^)]*\) VALUES \('(?P<key>[^']+)', '(?P<value>[^']*)'"
)


def _fresh_install_settings() -> dict[str, str]:
    """Baseline seeds + settings_defaults. The two agree on overlapping keys
    (settings_seed_value_drift_lint), so the union is what a fresh DB holds."""
    seeded = {m["key"]: m["value"] for m in _SETTING_ROW.finditer(_SEEDS.read_text())}
    assert len(seeded) >= 500, "baseline app_settings parse went blind"
    return {**DEFAULTS, **seeded}


def test_every_mapped_switch_is_a_real_setting():
    missing = sorted(
        {k for k, _ in RAIL_MASTER_SWITCHES.values()} - set(_fresh_install_settings())
    )
    assert not missing, f"master-switch keys seeded nowhere: {missing}"


def test_fresh_install_never_requires_a_rail_whose_switch_is_off():
    """The landmine itself. Opt-in rails (switch off by default, gate enabled
    but advisory) are inert and are what `qa-gates list` warns about; a
    REQUIRED one would reject every post on a fresh install."""
    settings = _fresh_install_settings()
    required = [g for g in _baseline_gates().values() if g["required_to_pass"]]
    assert required, "no required gates parsed"
    assert master_switch_conflicts(required, settings) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("1", True), ("On", True), ("false", False), ("", False)],
)
def test_switch_values(value, expected):
    assert master_switch_is_on("ragas_eval", {"ragas_enabled": value}) is expected


def test_absent_key_uses_the_readers_default():
    assert master_switch_is_on("ragas_eval", {}) is False
    assert master_switch_is_on("numeric_fidelity", {}) is True


def test_unmapped_gate_is_always_on():
    assert master_switch_is_on("llm_critic", {"ragas_enabled": "false"}) is True


def test_conflicts_only_report_enabled_gates():
    gates = [
        {"name": "ragas_eval", "enabled": True},
        {"name": "guardrails_brand", "enabled": False},
        {"name": "llm_critic", "enabled": True},
    ]
    settings = {"ragas_enabled": "false", "guardrails_enabled": "false"}
    assert master_switch_conflicts(gates, settings) == [("ragas_eval", "ragas_enabled")]


def _pool(settings_rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=settings_rows)
    conn.fetchrow = AsyncMock(return_value={"name": "ragas_eval"})

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    return pool, conn


@pytest.mark.asyncio
async def test_upsert_refuses_to_require_a_gate_whose_switch_is_off():
    pool, conn = _pool([{"key": "ragas_enabled", "value": "false"}])
    with pytest.raises(SurfaceValidationError, match="ragas_enabled"):
        await upsert_row(
            pool, "qa-gates",
            {"name": "ragas_eval", "required_to_pass": True, "enabled": True},
        )
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_allows_require_when_switch_is_on():
    pool, conn = _pool([{"key": "ragas_enabled", "value": "true"}])
    await upsert_row(
        pool, "qa-gates",
        {"name": "ragas_eval", "required_to_pass": True, "enabled": True},
    )
    conn.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_allows_advisory_and_disabled_writes_with_switch_off():
    pool, conn = _pool([{"key": "ragas_enabled", "value": "false"}])
    await upsert_row(pool, "qa-gates", {"name": "ragas_eval", "required_to_pass": False})
    await upsert_row(
        pool, "qa-gates",
        {"name": "ragas_eval", "required_to_pass": True, "enabled": False},
    )
    assert conn.fetchrow.await_count == 2
