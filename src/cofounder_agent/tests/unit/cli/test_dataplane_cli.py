"""Behavior + purity tests for the de-SQL'd data-plane CLI groups (#1522).

Each group (taps / retention / webhooks / qa-gates / publishers) now delegates
to ``services.declarative_config_service`` through the shared ``_dataplane``
helper. These tests pin that the commands still behave AND that no raw SQL /
asyncpg connection remains in the module source (the epic #1340 contract).
"""

from __future__ import annotations

import asyncio
import pathlib
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner


def _fake_run_service(factory):
    """Stand-in for ``_dataplane.run_service``: run the factory with a dummy
    pool (the service functions it calls are patched, so the pool is unused)."""
    return asyncio.run(factory(object()))


def _assert_no_raw_sql(module) -> None:
    # Match actual usage, not prose: a docstring may *mention* asyncpg/SQL.
    src = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    forbidden = (
        "import asyncpg", "asyncpg.", "conn.fetch", "conn.execute", "_connect(",
        "SELECT ", "INSERT ", "UPDATE ", "DELETE ",
    )
    for token in forbidden:
        assert token not in src, f"{token!r} still present in {module.__name__}"


# --- taps ---------------------------------------------------------------


def test_taps_source_has_no_raw_sql():
    import poindexter.cli.taps as taps

    _assert_no_raw_sql(taps)


def test_taps_list_renders_rows():
    from poindexter.cli.taps import taps_group

    rows = [{
        "name": "rss", "handler_name": "h", "tap_type": "singer",
        "schedule": "@daily", "enabled": True, "last_run_at": None,
        "total_runs": 3, "total_records": 10, "last_error": None,
    }]
    with patch("poindexter.cli.taps.run_service", _fake_run_service), patch(
        "services.declarative_config_service.list_rows",
        new=AsyncMock(return_value=rows),
    ):
        result = CliRunner().invoke(taps_group, ["list"])
    assert result.exit_code == 0
    assert "rss" in result.output


def test_taps_enable_missing_reports_and_exits():
    from poindexter.cli.taps import taps_group

    with patch("poindexter.cli.taps.run_service", _fake_run_service), patch(
        "services.declarative_config_service.get_row",
        new=AsyncMock(return_value=None),
    ):
        result = CliRunner().invoke(taps_group, ["enable", "ghost"])
    assert result.exit_code == 1
    assert "no tap named" in result.output


def test_taps_enable_existing_upserts_enabled_true():
    from poindexter.cli.taps import taps_group

    captured: dict = {}

    async def _fake_upsert(pool, surface, payload):
        captured.update(payload)
        return payload

    existing = {"name": "rss", "enabled": False, "config": {}, "metadata": {}}
    with patch("poindexter.cli.taps.run_service", _fake_run_service), patch(
        "services.declarative_config_service.get_row",
        new=AsyncMock(return_value=existing),
    ), patch("services.declarative_config_service.upsert_row", new=_fake_upsert):
        result = CliRunner().invoke(taps_group, ["enable", "rss"])
    assert result.exit_code == 0
    assert captured["name"] == "rss"
    assert captured["enabled"] is True


# --- retention ----------------------------------------------------------


def test_retention_source_has_no_raw_sql():
    import poindexter.cli.retention as retention

    _assert_no_raw_sql(retention)


def test_retention_list_renders_rows():
    from poindexter.cli.retention import retention_group

    rows = [{
        "name": "prune-sessions", "handler_name": "delete", "table_name": "claude_sessions",
        "ttl_days": 30, "enabled": True, "last_run_at": None, "last_error": None,
        "total_runs": 5, "total_deleted": 100,
    }]
    with patch("poindexter.cli.retention.run_service", _fake_run_service), patch(
        "services.declarative_config_service.list_rows",
        new=AsyncMock(return_value=rows),
    ):
        result = CliRunner().invoke(retention_group, ["list"])
    assert result.exit_code == 0
    assert "prune-sessions" in result.output


def test_retention_enable_missing_reports_and_exits():
    from poindexter.cli.retention import retention_group

    with patch("poindexter.cli.retention.run_service", _fake_run_service), patch(
        "services.declarative_config_service.get_row",
        new=AsyncMock(return_value=None),
    ):
        result = CliRunner().invoke(retention_group, ["enable", "ghost"])
    assert result.exit_code == 1
    assert "no policy named" in result.output


# --- webhooks -----------------------------------------------------------


def test_webhooks_source_has_no_raw_sql():
    import poindexter.cli.webhooks as webhooks

    _assert_no_raw_sql(webhooks)


def test_webhooks_list_renders_rows():
    from poindexter.cli.webhooks import webhooks_group

    rows = [{
        "name": "ls-hook", "direction": "inbound", "handler_name": "lemonsqueezy",
        "signing_algorithm": "hmac-sha256", "enabled": True, "secret_key_ref": "wh_ls",
        "last_success_at": None, "total_success": 9, "total_failure": 0, "last_error": None,
    }]
    with patch("poindexter.cli.webhooks.run_service", _fake_run_service), patch(
        "services.declarative_config_service.list_rows",
        new=AsyncMock(return_value=rows),
    ):
        result = CliRunner().invoke(webhooks_group, ["list"])
    assert result.exit_code == 0
    assert "ls-hook" in result.output


def test_webhooks_set_secret_without_ref_errors():
    from poindexter.cli.webhooks import webhooks_group

    row = {"name": "ls-hook", "secret_key_ref": None}
    with patch("poindexter.cli.webhooks.run_service", _fake_run_service), patch(
        "services.declarative_config_service.get_row",
        new=AsyncMock(return_value=row),
    ):
        result = CliRunner().invoke(
            webhooks_group, ["set-secret", "ls-hook", "--value", "s3cr3t"]
        )
    assert result.exit_code == 1
    assert "secret_key_ref" in result.output


# --- qa-gates -----------------------------------------------------------


def test_qa_gates_source_has_no_raw_sql():
    import poindexter.cli.qa_gates as qa_gates

    _assert_no_raw_sql(qa_gates)


def test_qa_gates_list_orders_by_execution_order():
    from poindexter.cli.qa_gates import qa_gates_group

    rows = [
        {"name": "b", "execution_order": 20, "reviewer": "critic",
         "required_to_pass": True, "enabled": True, "total_runs": 1,
         "total_rejections": 0, "last_error": None},
        {"name": "a", "execution_order": 10, "reviewer": "programmatic",
         "required_to_pass": True, "enabled": True, "total_runs": 1,
         "total_rejections": 0, "last_error": None},
    ]
    with patch("poindexter.cli.qa_gates.run_service", _fake_run_service), patch(
        "services.declarative_config_service.list_rows",
        new=AsyncMock(return_value=rows),
    ):
        result = CliRunner().invoke(qa_gates_group, ["list"])
    assert result.exit_code == 0
    # execution_order 10 row must render before the 20 row
    assert result.output.index("programmatic") < result.output.index("critic")


def test_qa_gates_reorder_upserts_execution_order():
    from poindexter.cli.qa_gates import qa_gates_group

    captured: dict = {}

    async def _fake_upsert(pool, surface, payload):
        captured.update(payload)
        return payload

    existing = {"name": "qa.critic", "execution_order": 10, "config": {}, "metadata": {}}
    with patch("poindexter.cli.qa_gates.run_service", _fake_run_service), patch(
        "services.declarative_config_service.get_row",
        new=AsyncMock(return_value=existing),
    ), patch("services.declarative_config_service.upsert_row", new=_fake_upsert):
        result = CliRunner().invoke(qa_gates_group, ["reorder", "qa.critic", "5"])
    assert result.exit_code == 0
    assert captured["execution_order"] == 5


def test_qa_gates_require_upserts_required_to_pass_true():
    from poindexter.cli.qa_gates import qa_gates_group

    captured: dict = {}

    async def _fake_upsert(pool, surface, payload):
        captured.update(payload)
        return payload

    # An advisory rail with live telemetry counters — graduating it must
    # flip required_to_pass without disturbing enabled/counters.
    existing = {
        "name": "qa.vision", "required_to_pass": False, "enabled": True,
        "total_runs": 7, "total_rejections": 2, "config": {}, "metadata": {},
    }
    with patch("poindexter.cli.qa_gates.run_service", _fake_run_service), patch(
        "services.declarative_config_service.get_row",
        new=AsyncMock(return_value=existing),
    ), patch("services.declarative_config_service.upsert_row", new=_fake_upsert):
        result = CliRunner().invoke(qa_gates_group, ["require", "qa.vision"])
    assert result.exit_code == 0
    assert captured["required_to_pass"] is True
    # The rest of the row rides along untouched (enabled stays true).
    assert captured["enabled"] is True
    assert "required_to_pass" in result.output


def test_qa_gates_advisory_upserts_required_to_pass_false():
    from poindexter.cli.qa_gates import qa_gates_group

    captured: dict = {}

    async def _fake_upsert(pool, surface, payload):
        captured.update(payload)
        return payload

    existing = {
        "name": "qa.vision", "required_to_pass": True, "enabled": True,
        "config": {}, "metadata": {},
    }
    with patch("poindexter.cli.qa_gates.run_service", _fake_run_service), patch(
        "services.declarative_config_service.get_row",
        new=AsyncMock(return_value=existing),
    ), patch("services.declarative_config_service.upsert_row", new=_fake_upsert):
        result = CliRunner().invoke(qa_gates_group, ["advisory", "qa.vision"])
    assert result.exit_code == 0
    assert captured["required_to_pass"] is False
    assert "advisory" in result.output


def test_qa_gates_require_missing_reports_and_exits():
    from poindexter.cli.qa_gates import qa_gates_group

    with patch("poindexter.cli.qa_gates.run_service", _fake_run_service), patch(
        "services.declarative_config_service.get_row",
        new=AsyncMock(return_value=None),
    ):
        result = CliRunner().invoke(qa_gates_group, ["require", "ghost"])
    assert result.exit_code == 1
    assert "no qa_gates row named" in result.output


# --- publishers ---------------------------------------------------------


def test_publishers_source_has_no_raw_sql():
    import poindexter.cli.publishers as publishers

    _assert_no_raw_sql(publishers)


def test_publishers_list_renders_rows():
    from poindexter.cli.publishers import publishers_group

    rows = [{
        "name": "bluesky_main", "platform": "bluesky", "handler_name": "bluesky",
        "enabled": True, "last_run_at": None, "total_runs": 4, "total_failures": 0,
        "last_error": None,
    }]
    with patch("poindexter.cli.publishers.run_service", _fake_run_service), patch(
        "services.declarative_config_service.list_rows",
        new=AsyncMock(return_value=rows),
    ):
        result = CliRunner().invoke(publishers_group, ["list"])
    assert result.exit_code == 0
    assert "bluesky_main" in result.output


def test_publishers_set_secret_prefix_mismatch_errors():
    from poindexter.cli.publishers import publishers_group

    row = {"name": "bluesky_main", "credentials_ref": "bluesky_"}
    with patch("poindexter.cli.publishers.run_service", _fake_run_service), patch(
        "services.declarative_config_service.get_row",
        new=AsyncMock(return_value=row),
    ):
        result = CliRunner().invoke(
            publishers_group,
            ["set-secret", "bluesky_main", "wrong_key", "--value", "x"],
        )
    assert result.exit_code == 1
    assert "prefix" in result.output
