"""atom_registry.sync_to_db prunes catalogue rows for atoms that are gone.

poindexter#1066: the sync only upserted, so ``pipeline_atoms`` kept a row for
every atom ever discovered — 13 on prod whose files had been deleted, back to
May. Pruning is by grace window (``pipeline_atoms_prune_after_days``), not by
absence from one process's discovery, so an entry point that fails to import
an atom cannot delete a row the others still serve.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.services import atom_registry

pytestmark = pytest.mark.unit


def _pool(*, setting, deleted=()):
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=setting)
    conn.fetch = AsyncMock(return_value=[{"name": n} for n in deleted])

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    return pool, conn


@pytest.mark.asyncio
async def test_sync_prunes_with_the_configured_window_and_spares_discovered():
    pool, conn = _pool(setting="7", deleted=["qa.guardrails"])
    n = await atom_registry.sync_to_db(pool)
    assert n > 0
    sql, days, names = conn.fetch.await_args.args
    assert "DELETE FROM pipeline_atoms" in sql
    assert days == 7
    # Every atom this process discovered is protected from the delete.
    assert set(names) == {m.name for m in atom_registry.list_atoms()}
    assert "content.generate_draft" in names


@pytest.mark.asyncio
async def test_zero_disables_pruning():
    pool, conn = _pool(setting="0")
    await atom_registry.sync_to_db(pool)
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "", "seven"])
async def test_missing_or_bad_setting_falls_back_to_default(raw):
    pool, conn = _pool(setting=raw)
    await atom_registry.sync_to_db(pool)
    assert conn.fetch.await_args.args[1] == atom_registry._PRUNE_DEFAULT_DAYS


def test_default_matches_settings_defaults():
    from poindexter.services.settings_defaults import DEFAULTS

    assert int(DEFAULTS[atom_registry._PRUNE_SETTING]) == atom_registry._PRUNE_DEFAULT_DAYS
