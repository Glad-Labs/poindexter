"""Tests for the Glad Labs operator model overlay.

``apply_operator_model_overrides`` re-pins the operator's custom local models
over the public OSS defaults on a fresh install / settings reset, but only when
a key still holds the OSS default (so live ``poindexter settings set`` tuning
survives a reboot). On OSS installs the private ``services.operator_overrides``
module is absent and the overlay is a no-op.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.settings_defaults import DEFAULTS, apply_operator_model_overrides


def _mock_pool(fetchval_side_effect):
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


@pytest.mark.asyncio
async def test_apply_is_noop_when_overlay_absent(monkeypatch):
    """OSS install: importing the private overlay fails -> no DB writes."""
    monkeypatch.setitem(sys.modules, "services.operator_overrides", None)
    pool, conn = _mock_pool(fetchval_side_effect=lambda *a, **k: None)

    applied = await apply_operator_model_overrides(pool)

    assert applied == 0
    conn.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_is_noop_for_none_pool():
    assert await apply_operator_model_overrides(None) == 0


@pytest.mark.asyncio
async def test_apply_compares_against_oss_default_and_counts_changes():
    """Overlay present: each pin issues a conditional UPSERT keyed on the OSS
    default, and only rows that actually changed are counted."""
    oo = pytest.importorskip("services.operator_overrides")

    calls = []

    def _fetchval(query, key, value, desc, oss_default):
        calls.append((key, value, oss_default))
        # Pretend every other row was already operator-tuned (UPSERT WHERE
        # missed -> RETURNING yields nothing -> None).
        return key if len(calls) % 2 else None

    pool, _conn = _mock_pool(fetchval_side_effect=_fetchval)
    applied = await apply_operator_model_overrides(pool)

    assert len(calls) == len(oo.OPERATOR_MODEL_PINS)
    for key, value, oss_default in calls:
        # The overlay only overwrites the public OSS default for that key...
        assert oss_default == DEFAULTS.get(key)
        # ...with the operator's custom model.
        assert value == oo.OPERATOR_MODEL_PINS[key]
    assert applied == sum(1 for i in range(1, len(calls) + 1) if i % 2)


def test_every_pin_key_has_a_public_oss_default():
    """The overlay skips a key whose OSS default it can't see, so every pinned
    key must exist in DEFAULTS (else the operator silently loses that pin)."""
    oo = pytest.importorskip("services.operator_overrides")

    missing = [k for k in oo.OPERATOR_MODEL_PINS if k not in DEFAULTS]
    assert not missing, (
        f"operator overlay pins {missing} have no OSS default in "
        "settings_defaults.DEFAULTS — apply_operator_model_overrides would skip "
        "them, so a fresh operator install would NOT get those models."
    )


def test_pins_are_operator_private_not_public_defaults():
    """An overlay entry only makes sense for a CUSTOM model — if a pin equals
    the public OSS default it's dead weight (and a sign the seed drifted)."""
    oo = pytest.importorskip("services.operator_overrides")

    redundant = {
        k: v for k, v in oo.OPERATOR_MODEL_PINS.items() if v == DEFAULTS.get(k)
    }
    assert not redundant, (
        f"operator overlay pins {redundant} equal the public OSS default — "
        "drop them from operator_overrides or fix the seed drift."
    )
