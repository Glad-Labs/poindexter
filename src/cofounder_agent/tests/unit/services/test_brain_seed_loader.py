"""
Unit tests for brain/seed_loader.py.

brain/ is standalone (stdlib + asyncpg). All DB I/O is mocked.

Covers the idempotency guarantee from Gitea #236:
- Running on an existing DB still inserts any seed key that's missing
  (regression guard — the old fast-path skipped everything when the
  REQUIRED_KEYS set was already populated).
- Existing non-empty values are NOT overwritten.
- Empty values ARE refilled from seed (protects against accidental
  blanks on boot-critical keys).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from brain import seed_loader as sl


def _make_conn():
    """Build an AsyncMock conn that behaves like asyncpg.Connection."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock()
    conn.fetch = AsyncMock()
    return conn


@pytest.fixture
def sample_seed(monkeypatch):
    """Patch load_seed_file to return a small in-memory seed list."""
    seed = [
        {"key": "site_name", "value": "Glad Labs", "category": "general",
         "description": "Brand name"},
        {"key": "site_url", "value": "https://gladlabs.io",
         "category": "general", "description": ""},
        # A non-required key — this is the one the old fast-path would skip.
        {"key": "prometheus.threshold.postgres_p99_latency_seconds",
         "value": "0.1", "category": "alerting", "description": ""},
    ]
    monkeypatch.setattr(sl, "load_seed_file", lambda: seed)
    return seed


@pytest.mark.unit
@pytest.mark.asyncio
class TestSeedAppSettings:
    async def test_empty_db_inserts_full_seed(self, sample_seed):
        conn = _make_conn()
        conn.fetchval.side_effect = [
            0,  # _settings_rows_present → 0
            # Each subsequent fetchval is the "created_at = updated_at"
            # check inside the loop. Since we're on empty DB, every row
            # is new, so True for all three.
            True, True, True,
        ]
        conn.fetch.return_value = []  # _missing_required_keys
        conn.execute.return_value = "INSERT 0 1"

        result = await sl.seed_app_settings(conn)

        assert result["inserted"] == 3
        assert result["refilled"] == 0
        assert result["skipped_existing"] == 0
        assert result["total_seed"] == 3
        # Three per-row INSERTs plus one CREATE TABLE = 4 execute calls.
        assert conn.execute.await_count == 4

    async def test_new_seed_key_on_populated_db_gets_inserted(self, sample_seed):
        """Gitea #236 regression guard: a fresh key added to the JSON
        file must land in app_settings on next boot even when the
        REQUIRED_KEYS set is already satisfied."""
        conn = _make_conn()
        conn.fetchval.side_effect = [
            # _settings_rows_present → 10 (populated)
            10,
            # For the two already-present rows (site_name, site_url):
            # the upsert reports INSERT 0 0 because the WHERE suppressed
            # the update (value wasn't empty). We won't hit the disambig
            # fetchval for those.
            # For the new prometheus.threshold key: INSERT 0 1,
            # was_present = True → counted as inserted.
            True,
        ]
        # Required keys all present.
        conn.fetch.return_value = [
            {"key": k, "value": "set"} for k in sl.REQUIRED_KEYS
        ]
        conn.execute.side_effect = [
            "CREATE TABLE",  # _ensure_app_settings_table
            "INSERT 0 0",    # site_name (already populated)
            "INSERT 0 0",    # site_url (already populated)
            "INSERT 0 1",    # prometheus.threshold.* (new)
        ]

        result = await sl.seed_app_settings(conn)

        # The new key got inserted despite the DB being populated.
        assert result["inserted"] == 1
        assert result["skipped_existing"] == 2

    async def test_existing_non_empty_values_preserved(self, sample_seed):
        """Operator edits must not be clobbered by the seed."""
        conn = _make_conn()
        conn.fetchval.side_effect = [10]  # rows_present → 10
        conn.fetch.return_value = [
            {"key": k, "value": "set"} for k in sl.REQUIRED_KEYS
        ]
        # Every row already has a non-empty value, so ON CONFLICT DO
        # UPDATE's WHERE suppresses every update. All three INSERT 0 0.
        conn.execute.side_effect = [
            "CREATE TABLE",
            "INSERT 0 0",
            "INSERT 0 0",
            "INSERT 0 0",
        ]

        result = await sl.seed_app_settings(conn)

        assert result["inserted"] == 0
        assert result["refilled"] == 0
        assert result["skipped_existing"] == 3

    async def test_empty_value_refills_from_seed(self, sample_seed):
        """Boot-critical key accidentally blanked should be recovered
        by the ``WHERE value = ''`` clause in the ON CONFLICT branch."""
        conn = _make_conn()
        conn.fetchval.side_effect = [
            10,  # rows_present
            # Disambig for the row that got updated: was_present=False
            # means it existed but was refilled (created_at != updated_at).
            False,
            # The other two were present + populated, so no disambig
            # fetchval is called for them.
        ]
        conn.fetch.return_value = [
            {"key": k, "value": "set"} for k in sl.REQUIRED_KEYS
        ]
        conn.execute.side_effect = [
            "CREATE TABLE",
            "INSERT 0 1",    # site_name — existed but was empty, refilled
            "INSERT 0 0",    # site_url — already populated, left alone
            "INSERT 0 0",    # prometheus key — already populated
        ]

        result = await sl.seed_app_settings(conn)

        assert result["inserted"] == 0
        assert result["refilled"] == 1
        assert result["skipped_existing"] == 2

    async def test_seed_runs_when_required_key_missing(self, sample_seed):
        """If a boot-critical key is somehow missing, the log message
        highlights that but the INSERT loop still runs normally."""
        conn = _make_conn()
        conn.fetchval.side_effect = [10, True, True, True]
        # Only site_name is present — every other REQUIRED_KEY is missing.
        conn.fetch.return_value = [{"key": "site_name", "value": "set"}]
        conn.execute.return_value = "INSERT 0 1"

        result = await sl.seed_app_settings(conn)

        assert result["total_seed"] == 3


@pytest.mark.unit
class TestLoadSeedFile:
    def test_raises_when_file_missing(self, tmp_path, monkeypatch):
        # Force _seed_path to look somewhere empty.
        def _fake_path():
            raise FileNotFoundError("not found")
        monkeypatch.setattr(sl, "_seed_path", _fake_path)
        with pytest.raises(FileNotFoundError):
            sl.load_seed_file()

    def test_raises_on_row_missing_key_or_value(self, tmp_path, monkeypatch):
        path = tmp_path / "seed.json"
        path.write_text('{"settings": [{"key": "x"}]}', encoding="utf-8")
        monkeypatch.setattr(sl, "_seed_path", lambda: path)
        with pytest.raises(ValueError, match="key/value"):
            sl.load_seed_file()

    def test_raises_when_settings_not_a_list(self, tmp_path, monkeypatch):
        path = tmp_path / "seed.json"
        path.write_text('{"settings": {"not": "a list"}}', encoding="utf-8")
        monkeypatch.setattr(sl, "_seed_path", lambda: path)
        with pytest.raises(ValueError, match="no 'settings' list"):
            sl.load_seed_file()


@pytest.mark.unit
@pytest.mark.asyncio
class TestEnsureAppSettingsTable:
    async def test_issues_create_table_if_not_exists(self):
        conn = _make_conn()
        await sl._ensure_app_settings_table(conn)
        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args.args[0]
        assert "CREATE TABLE IF NOT EXISTS app_settings" in sql


@pytest.mark.unit
@pytest.mark.asyncio
class TestSettingsRowsPresent:
    async def test_returns_count_when_table_exists(self):
        conn = _make_conn()
        conn.fetchval.return_value = 42
        assert await sl._settings_rows_present(conn) == 42

    async def test_returns_zero_when_table_missing(self):
        conn = _make_conn()
        conn.fetchval.side_effect = asyncpg.exceptions.UndefinedTableError(
            "relation does not exist",
        )
        assert await sl._settings_rows_present(conn) == 0


@pytest.mark.unit
@pytest.mark.asyncio
class TestMissingRequiredKeys:
    async def test_returns_all_required_when_table_missing(self):
        conn = _make_conn()
        conn.fetch.side_effect = asyncpg.exceptions.UndefinedTableError("nope")
        missing = await sl._missing_required_keys(conn)
        assert missing == set(sl.REQUIRED_KEYS)

    async def test_empty_value_counts_as_missing(self):
        conn = _make_conn()
        conn.fetch.return_value = [
            {"key": k, "value": ""} for k in sl.REQUIRED_KEYS
        ]
        # All "present" but empty — should be treated as missing.
        missing = await sl._missing_required_keys(conn)
        assert missing == set(sl.REQUIRED_KEYS)

    async def test_non_empty_counts_as_present(self):
        conn = _make_conn()
        conn.fetch.return_value = [
            {"key": k, "value": "set"} for k in sl.REQUIRED_KEYS
        ]
        missing = await sl._missing_required_keys(conn)
        assert missing == set()
