"""Migration 20260928_174820: preview_base_url comes off the URL probe's mute list.

The mute dates from the probe's false-positive cleanup in early May 2026. A
mute is blind: on the Pop!_OS host it hid the dead retired-node IP in
``preview_base_url`` for about ten weeks. The probe now resolves tailnet names
through the tailnet's resolver, so even a MagicDNS link is checkable and the
mute comes off. The fresh-install seed must not bring it back either.
"""

from __future__ import annotations

import importlib.util
import re
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_MIGRATIONS_DIR = _REPO / "src" / "cofounder_agent" / "poindexter" / "services" / "migrations"
_MIGRATION = (
    _MIGRATIONS_DIR / "20260928_174820_stop_muting_preview_base_url_in_the_operator_url_probe.py"
)
_BASELINE_SEEDS = _MIGRATIONS_DIR / "0000_baseline.seeds.sql"
_KEY = "operator_url_probe_skip_keys"


def _load():
    spec = importlib.util.spec_from_file_location("_mig_stop_muting_preview", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Table:
    """A one-table stand-in for app_settings behind pool.acquire()."""

    def __init__(self, value: str | None):
        self.rows = {} if value is None else {_KEY: value}
        self.writes = 0

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetchval(self, sql, key):
        assert "SELECT value FROM app_settings" in sql
        return self.rows.get(key)

    async def execute(self, sql, key, value):
        assert sql.startswith("UPDATE app_settings SET value = $2")
        self.rows[key] = value
        self.writes += 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestStopMutingPreviewBaseUrl:
    async def test_removes_only_that_entry_and_keeps_order(self):
        table = _Table("gitea_url, openclaw_gateway_url,preview_base_url,r2_public_url")
        await _load().up(table)
        assert table.rows[_KEY] == "gitea_url,openclaw_gateway_url,r2_public_url"

    async def test_is_idempotent(self):
        table = _Table("gitea_url,r2_public_url")
        await _load().up(table)
        assert table.writes == 0
        assert table.rows[_KEY] == "gitea_url,r2_public_url"

    async def test_missing_row_is_a_no_op(self):
        table = _Table(None)
        await _load().up(table)
        assert table.rows == {}

    async def test_down_mutes_it_again_once(self):
        mig = _load()
        table = _Table("gitea_url")
        await mig.down(table)
        await mig.down(table)
        assert table.rows[_KEY] == "gitea_url,preview_base_url"
        assert table.writes == 1


@pytest.mark.unit
def test_fresh_install_seed_does_not_mute_preview_base_url():
    seeds = _BASELINE_SEEDS.read_text(encoding="utf-8")
    match = re.search(
        r"VALUES \('operator_url_probe_skip_keys', '([^']*)'", seeds,
    )
    assert match, "operator_url_probe_skip_keys is no longer seeded by the baseline"
    entries = [e.strip() for e in match.group(1).split(",")]
    assert "preview_base_url" not in entries
    # The rest of the operator's mute list is untouched.
    assert "oauth_issuer_url" in entries
    assert "social_x_url" in entries
