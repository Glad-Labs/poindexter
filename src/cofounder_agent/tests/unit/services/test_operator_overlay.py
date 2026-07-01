"""Tests for the Glad Labs operator overlay.

``apply_operator_overrides`` re-pins the operator's custom local models AND
personal settings over the public OSS defaults on a fresh install / settings
reset, but only when a key still holds the OSS default (so live
``poindexter settings set`` tuning survives a reboot). On OSS installs the
private ``services.operator_overrides`` module is absent and the overlay is a
no-op.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import settings_defaults as _sd
from services.settings_defaults import DEFAULTS, apply_operator_overrides

_BASELINE_SEEDS = (
    Path(_sd.__file__).resolve().parent / "migrations" / "0000_baseline.seeds.sql"
)
_SEED_KV_RE = re.compile(r"VALUES\s*\(\s*'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'")


def _baseline_seed_map() -> dict[str, str]:
    """key -> value for every app_settings seed row (SQL '' unescaped to ')."""
    text = _BASELINE_SEEDS.read_text(encoding="utf-8")
    return {
        k.replace("''", "'"): v.replace("''", "'")
        for k, v in _SEED_KV_RE.findall(text)
    }


def _mock_pool(fetchval_side_effect):
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def _all_overrides(oo):
    return {**oo.OPERATOR_MODEL_PINS, **oo.OPERATOR_SETTING_OVERRIDES}


@pytest.mark.asyncio
async def test_apply_is_noop_when_overlay_absent(monkeypatch):
    """OSS install: importing the private overlay fails -> no DB writes."""
    monkeypatch.setitem(sys.modules, "services.operator_overrides", None)
    pool, conn = _mock_pool(fetchval_side_effect=lambda *a, **k: None)

    applied = await apply_operator_overrides(pool)

    assert applied == 0
    conn.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_is_noop_for_none_pool():
    assert await apply_operator_overrides(None) == 0


@pytest.mark.asyncio
async def test_apply_compares_against_oss_default_and_counts_changes():
    """Overlay present: each override issues a conditional UPSERT keyed on the
    OSS default, and only rows that actually changed are counted."""
    oo = pytest.importorskip("services.operator_overrides")
    overrides = _all_overrides(oo)

    calls = []

    def _fetchval(query, key, value, desc, oss_default):
        calls.append((key, value, oss_default))
        # Pretend every other row was already operator-tuned (UPSERT WHERE
        # missed -> RETURNING yields nothing -> None).
        return key if len(calls) % 2 else None

    pool, _conn = _mock_pool(fetchval_side_effect=_fetchval)
    applied = await apply_operator_overrides(pool)

    assert len(calls) == len(overrides)
    for key, value, oss_default in calls:
        # The overlay only overwrites the public OSS default for that key...
        assert oss_default == DEFAULTS.get(key)
        # ...with the operator's private value.
        assert value == overrides[key]
    assert applied == sum(1 for i in range(1, len(calls) + 1) if i % 2)


def test_every_override_key_has_a_public_oss_default():
    """The overlay skips a key whose OSS default it can't see, so every override
    key must exist in DEFAULTS (else the operator silently loses that value)."""
    oo = pytest.importorskip("services.operator_overrides")

    missing = [k for k in _all_overrides(oo) if k not in DEFAULTS]
    assert not missing, (
        f"operator overlay keys {missing} have no OSS default in "
        "settings_defaults.DEFAULTS — apply_operator_overrides would skip them, "
        "so a fresh operator install would NOT get those values."
    )


def test_overrides_differ_from_oss_default():
    """An overlay entry only makes sense when it differs from the public OSS
    default — an equal value is dead weight (and a sign the seed drifted)."""
    oo = pytest.importorskip("services.operator_overrides")

    redundant = {k: v for k, v in _all_overrides(oo).items() if v == DEFAULTS.get(k)}
    assert not redundant, (
        f"operator overlay entries {redundant} equal the public OSS default — "
        "drop them from operator_overrides or fix the seed drift."
    )


def test_overlaid_keys_seed_matches_defaults():
    """The overlay overwrites a row only WHERE it still equals ``DEFAULTS[key]``,
    so the value ``0000_baseline.seeds.sql`` actually seeds must equal
    ``DEFAULTS[key]`` — otherwise the overlay silently no-ops on a fresh operator
    install and the operator loses that value. Guards hand-transcribed seeds like
    the multi-line voice prompt against DEFAULTS/baseline drift."""
    oo = pytest.importorskip("services.operator_overrides")
    seeds = _baseline_seed_map()
    drift = {
        key: {"baseline": seeds[key], "defaults": DEFAULTS.get(key)}
        for key in _all_overrides(oo)
        if key in seeds and seeds[key] != DEFAULTS.get(key)
    }
    assert not drift, (
        "baseline.seeds.sql and settings_defaults.DEFAULTS disagree on the OSS "
        f"default for overlaid keys, so the overlay won't re-apply on reset: {drift}"
    )
