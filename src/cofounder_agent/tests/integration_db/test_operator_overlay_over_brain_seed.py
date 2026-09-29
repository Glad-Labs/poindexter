"""The operator overlay restores its values over the brain daemon's seed (real Postgres).

``apply_operator_overrides`` overwrites a row only while it still holds a seeded
value. The brain daemon boots before the worker, so when the overlay runs a key
like ``site_name`` holds the brain's free-tier placeholder rather than the empty
OSS default. Either the brain wrote first (an empty DB) or it refilled the
empty value ``poindexter setup`` left (``seed_loader``'s refill-empty rule).
Until 2026-09-28 the guard accepted only the OSS default, so those keys kept
the placeholders.

The unit tests pin the values the guard binds. This test runs the real brain
seed, the real ``seed_all_defaults`` and the real overlay SQL in one rolled-back
transaction, then checks what the rows hold. It needs the private operator
overlay, which is stripped from the public mirror, so the module skips there.
"""
from __future__ import annotations

import pytest

from poindexter.brain.seed_loader import load_seed_file, seed_app_settings
from poindexter.services.settings_defaults import (
    DEFAULTS,
    apply_operator_overrides,
    seed_all_defaults,
)

oo = pytest.importorskip("poindexter.services.operator_overrides")

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

OVERRIDES = {**oo.OPERATOR_MODEL_PINS, **oo.OPERATOR_SETTING_OVERRIDES}


class _TxnPool:
    """Adapt the rolled-back ``test_txn`` connection to the ``pool.acquire()``
    seam the seeder and the overlay take."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_a):
                return False

        return _Ctx()


async def _brain_boot(conn, _pool) -> None:
    await seed_app_settings(conn)


async def _setup_seed(_conn, pool) -> None:
    # The harness already ran the migrations; seed_all_defaults is the rest of
    # what `poindexter setup` (and every worker boot) does before the overlay.
    await seed_all_defaults(pool)


async def _overlaid_rows(conn) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT key, value FROM app_settings WHERE key = ANY($1::text[])",
        list(OVERRIDES),
    )
    return {r["key"]: r["value"] for r in rows}


def _not_holding_operator_value(rows: dict[str, str]) -> list[str]:
    """Key names only: overlay values are private, so keep them out of CI logs."""
    return sorted(k for k, v in OVERRIDES.items() if rows.get(k) != v)


@pytest.mark.parametrize(
    "boot_order",
    [
        # `poindexter setup`, then `docker compose up`: the brain container boots
        # before the worker and refills the empty values setup left.
        pytest.param((_setup_seed, _brain_boot), id="setup_then_compose_up"),
        # `docker compose up` on an empty DB: the brain is the first writer.
        pytest.param((_brain_boot, _setup_seed), id="brain_writes_first"),
    ],
)
async def test_overlay_restores_every_value_whichever_seeder_wrote_first(
    test_txn, boot_order
):
    pool = _TxnPool(test_txn)
    # A fresh install for the overlaid rows: no seeder has written them yet.
    await test_txn.execute(
        "DELETE FROM app_settings WHERE key = ANY($1::text[])", list(OVERRIDES)
    )
    for boot in boot_order:
        await boot(test_txn, pool)

    # The hazard is live. An overlaid key whose OSS default is empty and which
    # the brain seeds with a placeholder holds that placeholder when the worker
    # boots, on both paths: the brain wrote it first, or it refilled the empty
    # value.
    brain = {row["key"]: str(row["value"]) for row in load_seed_file()}
    placeholders = {
        k: brain[k] for k in OVERRIDES if brain.get(k) and not DEFAULTS.get(k)
    }
    before = await _overlaid_rows(test_txn)
    assert {k: before.get(k) for k in placeholders} == placeholders

    # Worker boot: seed_all_defaults, then the overlay.
    await seed_all_defaults(pool)
    await apply_operator_overrides(pool)

    missed = _not_holding_operator_value(await _overlaid_rows(test_txn))
    assert not missed, f"the overlay left these keys holding a seeded value: {missed}"


async def test_runtime_tuned_values_survive_a_full_restart(test_txn):
    """Widening the guard to accept the brain's placeholder must not widen it
    to anything else. A value tuned at runtime survives a brain boot plus a
    worker boot."""
    pool = _TxnPool(test_txn)
    await test_txn.execute(
        "DELETE FROM app_settings WHERE key = ANY($1::text[])", list(OVERRIDES)
    )
    await seed_all_defaults(pool)
    await seed_app_settings(test_txn)
    await apply_operator_overrides(pool)

    tuned = {k: f"tuned-at-runtime:{k}" for k in OVERRIDES}
    await test_txn.executemany(
        "UPDATE app_settings SET value = $2 WHERE key = $1", list(tuned.items())
    )
    await seed_app_settings(test_txn)
    await seed_all_defaults(pool)
    await apply_operator_overrides(pool)

    rows = await _overlaid_rows(test_txn)
    clobbered = sorted(k for k, v in tuned.items() if rows.get(k) != v)
    assert not clobbered, f"the overlay overwrote runtime-tuned values: {clobbered}"
