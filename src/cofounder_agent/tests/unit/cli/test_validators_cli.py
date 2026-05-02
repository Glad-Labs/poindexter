"""Unit tests for ``poindexter validators`` CLI (Validators CRUD V1).

Mirrors the test pattern in ``tests/unit/services/test_stores_cli.py``:
mock the asyncpg ``_connect`` boundary so we don't need a live Postgres
and exercise the click command surface end-to-end.

Subcommands covered:

* ``list`` -- empty + populated, --state filter
* ``show`` -- found + not-found
* ``enable`` / ``disable`` -- found + not-found
* ``set-severity`` -- happy path + bad level (click rejects)
* ``set-threshold`` -- JSON merge + null-value-deletes + bad input
* ``set-niches`` -- list + --all + mutual-exclusion error
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


def _mock_conn(
    *,
    fetch_rows: list[dict] | None = None,
    fetchrow_value: dict | None = None,
    fetchrow_side_effect: list[dict | None] | None = None,
    execute_status: str = "UPDATE 1",
) -> MagicMock:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_rows or [])
    if fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_value)
    conn.execute = AsyncMock(return_value=execute_status)
    conn.close = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def cli_runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _ensure_dsn(monkeypatch):
    """All CLI commands require a DSN env var to construct a connection."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")


def _patch_connect(conn: MagicMock):
    """Patch the validators module's ``_connect`` to return ``conn``."""
    return patch(
        "poindexter.cli.validators._connect",
        new=AsyncMock(return_value=conn),
    )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidatorsList:
    def test_empty(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(fetch_rows=[])
        with _patch_connect(conn):
            result = cli_runner.invoke(validators_group, ["list"])
        assert result.exit_code == 0
        assert "no content_validator_rules" in result.output.lower()

    def test_with_rows_renders_state_and_niches(self, cli_runner):
        from poindexter.cli.validators import validators_group
        rows = [
            {
                "name": "fake_stat",
                "enabled": True,
                "severity": "error",
                "applies_to_niches": None,
                "threshold": {},
                "description": "Detects fabricated stats",
            },
            {
                "name": "first_person_claims",
                "enabled": False,
                "severity": "warning",
                "applies_to_niches": ["dev_diary"],
                "threshold": {"penalty_per": 1.0},
                "description": "First person voice penalty",
            },
        ]
        conn = _mock_conn(fetch_rows=rows)
        with _patch_connect(conn):
            result = cli_runner.invoke(validators_group, ["list"])
        assert result.exit_code == 0, result.output
        assert "fake_stat" in result.output
        assert "first_person_claims" in result.output
        assert "enabled" in result.output
        assert "disabled" in result.output
        # NULL niches collapse to "(all)"; specific list renders comma-joined.
        assert "(all)" in result.output
        assert "dev_diary" in result.output

    def test_state_filter_passes_through(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(fetch_rows=[])
        with _patch_connect(conn):
            result = cli_runner.invoke(validators_group, ["list", "--state", "enabled"])
        assert result.exit_code == 0
        # The fetch SQL should have been called once with WHERE enabled = TRUE.
        sql = conn.fetch.await_args.args[0]
        assert "enabled = TRUE" in sql


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidatorsShow:
    def test_existing_rule(self, cli_runner):
        from poindexter.cli.validators import validators_group
        row = {
            "name": "code_block_density",
            "enabled": True,
            "severity": "warning",
            "threshold": {"min_blocks_per_700w": 2},
            "applies_to_niches": ["ai_ml"],
            "description": "Tech-post code density check",
            "created_at": None,
            "updated_at": None,
        }
        conn = _mock_conn(fetchrow_value=row)
        with _patch_connect(conn):
            result = cli_runner.invoke(validators_group, ["show", "code_block_density"])
        assert result.exit_code == 0, result.output
        assert "code_block_density" in result.output
        assert "ai_ml" in result.output
        assert "min_blocks_per_700w" in result.output

    def test_missing_rule_exits_nonzero(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(fetchrow_value=None)
        with _patch_connect(conn):
            result = cli_runner.invoke(validators_group, ["show", "ghost"])
        assert result.exit_code != 0
        assert "ghost" in result.output


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidatorsEnableDisable:
    def test_enable_existing(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(execute_status="UPDATE 1")
        with _patch_connect(conn):
            result = cli_runner.invoke(validators_group, ["enable", "fake_stat"])
        assert result.exit_code == 0, result.output
        assert "enabled" in result.output

    def test_disable_existing(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(execute_status="UPDATE 1")
        with _patch_connect(conn):
            result = cli_runner.invoke(validators_group, ["disable", "fake_stat"])
        assert result.exit_code == 0, result.output
        assert "disabled" in result.output

    def test_enable_missing_exits_nonzero(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(execute_status="UPDATE 0")
        with _patch_connect(conn):
            result = cli_runner.invoke(validators_group, ["enable", "ghost"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# set-severity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidatorsSetSeverity:
    def test_happy_path(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(execute_status="UPDATE 1")
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                ["set-severity", "code_block_density", "error"],
            )
        assert result.exit_code == 0, result.output
        assert "severity = error" in result.output
        # SQL should pass severity as the second positional arg.
        call = conn.execute.await_args
        assert "severity = $2" in call.args[0]
        assert call.args[1] == "code_block_density"
        assert call.args[2] == "error"

    def test_invalid_level_rejected_by_click(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn()
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                ["set-severity", "code_block_density", "critical"],
            )
        # 'critical' is not in the choice set (info|warning|error)
        assert result.exit_code != 0
        # Click rejected before any DB call was made.
        conn.execute.assert_not_awaited()

    def test_missing_rule_exits_nonzero(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(execute_status="UPDATE 0")
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group, ["set-severity", "ghost", "info"],
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# set-threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidatorsSetThreshold:
    def test_merges_with_existing_threshold(self, cli_runner):
        from poindexter.cli.validators import validators_group
        # Existing row already has min_line_ratio_pct; we add min_blocks_per_700w.
        existing_row = {"threshold": {"min_line_ratio_pct": 20}}
        conn = _mock_conn(fetchrow_value=existing_row, execute_status="UPDATE 1")
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                ["set-threshold", "code_block_density", "min_blocks_per_700w=2"],
            )
        assert result.exit_code == 0, result.output
        # The merged dict should contain BOTH keys.
        call = conn.execute.await_args
        merged = json.loads(call.args[2])
        assert merged == {"min_line_ratio_pct": 20, "min_blocks_per_700w": 2}

    def test_null_value_deletes_key(self, cli_runner):
        from poindexter.cli.validators import validators_group
        existing_row = {"threshold": {"max_penalty": 3.0, "penalty_per": 1.0}}
        conn = _mock_conn(fetchrow_value=existing_row, execute_status="UPDATE 1")
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                ["set-threshold", "first_person_claims", "max_penalty=null"],
            )
        assert result.exit_code == 0, result.output
        merged = json.loads(conn.execute.await_args.args[2])
        assert merged == {"penalty_per": 1.0}

    def test_multiple_entries_in_one_call(self, cli_runner):
        from poindexter.cli.validators import validators_group
        existing_row = {"threshold": {}}
        conn = _mock_conn(fetchrow_value=existing_row, execute_status="UPDATE 1")
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                [
                    "set-threshold", "code_block_density",
                    "min_blocks_per_700w=2",
                    "min_line_ratio_pct=25",
                ],
            )
        assert result.exit_code == 0, result.output
        merged = json.loads(conn.execute.await_args.args[2])
        assert merged == {"min_blocks_per_700w": 2, "min_line_ratio_pct": 25}

    def test_bad_entry_rejected(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn()
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                ["set-threshold", "x", "no_equals_sign"],
            )
        assert result.exit_code != 0

    def test_missing_rule_exits_nonzero(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(fetchrow_value=None)
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                ["set-threshold", "ghost", "x=1"],
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# set-niches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidatorsSetNiches:
    def test_pin_to_list(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(execute_status="UPDATE 1")
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                ["set-niches", "first_person_claims", "dev_diary,gaming"],
            )
        assert result.exit_code == 0, result.output
        call = conn.execute.await_args
        # niches param is the second positional after $1
        niches = call.args[2]
        assert niches == ["dev_diary", "gaming"]

    def test_all_clears_niches(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(execute_status="UPDATE 1")
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                ["set-niches", "first_person_claims", "--all"],
            )
        assert result.exit_code == 0, result.output
        call = conn.execute.await_args
        assert call.args[2] is None
        assert "(all niches)" in result.output

    def test_mutually_exclusive_args_rejected(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn()
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                ["set-niches", "x", "--all", "dev_diary"],
            )
        assert result.exit_code != 0

    def test_no_args_rejected(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn()
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group, ["set-niches", "x"],
            )
        assert result.exit_code != 0

    def test_missing_rule_exits_nonzero(self, cli_runner):
        from poindexter.cli.validators import validators_group
        conn = _mock_conn(execute_status="UPDATE 0")
        with _patch_connect(conn):
            result = cli_runner.invoke(
                validators_group,
                ["set-niches", "ghost", "dev_diary"],
            )
        assert result.exit_code != 0
