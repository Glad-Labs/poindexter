"""Behavior + purity tests for ``poindexter firefighter rule ...``.

The firefighter CLI is a thin adapter over
``services.remediation_rules_service`` — it holds no SQL (epic #1340), threads
its options straight through to the service, and turns service/selector errors
into a clean non-zero exit. These tests pin that wiring; the SQL-owning logic is
tested in ``tests/unit/services/test_remediation_rules_service.py``.
"""

from __future__ import annotations

import asyncio
import pathlib
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from poindexter.cli import firefighter as ff
from poindexter.cli.firefighter import firefighter_group


def _fake_run_service(factory):
    """Stand-in for ``_dataplane.run_service``: run the factory with a dummy
    pool (the service functions it calls are patched, so the pool is unused)."""
    return asyncio.run(factory(object()))


def _rule_row(**over):
    row = {
        "id": 1, "alertname": "PyroscopeDown", "match_regex": None,
        "action_name": "restart_container",
        "params": {"container": "poindexter-pyroscope"},
        "enabled": True, "max_attempts_per_window": None, "window_minutes": None,
        "verify_after_seconds": None, "description": "restart the profiler",
    }
    row.update(over)
    return row


# --- purity + registration --------------------------------------------------


def test_firefighter_source_has_no_raw_sql():
    """The adapter must not carry SQL / an asyncpg connection (epic #1340)."""
    src = pathlib.Path(ff.__file__).read_text(encoding="utf-8")
    forbidden = (
        "import asyncpg", "asyncpg.", "conn.fetch", "conn.execute", "_connect(",
        "SELECT ", "INSERT ", "UPDATE ", "DELETE ",
    )
    for token in forbidden:
        assert token not in src, f"{token!r} still present in {ff.__name__}"


def test_registration_has_rule_subgroup_with_all_verbs():
    assert "rule" in firefighter_group.commands
    rule = firefighter_group.commands["rule"]
    assert {"list", "show", "add", "rm", "enable", "disable"} <= set(rule.commands)


def test_firefighter_registered_on_main_cli_app():
    """`poindexter firefighter rule ...` must be reachable through the main CLI
    — guards against the import-without-add_command regression that has bitten
    other groups (see test_schedule_registration.py)."""
    import click

    from poindexter.cli.app import main

    assert isinstance(main, click.Group)
    assert "firefighter" in main.commands, (
        "firefighter group not registered on main CLI app — the "
        "import-without-add_command regression has re-appeared"
    )
    ff_group = main.commands["firefighter"]
    assert isinstance(ff_group, click.Group)
    assert "rule" in ff_group.commands


# --- list -------------------------------------------------------------------


def test_rule_list_renders_rows():
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.list_rules",
        new=AsyncMock(return_value=[_rule_row()]),
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "list"])
    assert result.exit_code == 0, result.output
    assert "PyroscopeDown" in result.output


def test_rule_list_empty_shows_inert_hint():
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.list_rules",
        new=AsyncMock(return_value=[]),
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "list"])
    assert result.exit_code == 0, result.output
    assert "inert" in result.output


def test_rule_list_state_filter_threads_enabled_bool():
    mock = AsyncMock(return_value=[])
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.list_rules", new=mock,
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "list", "--state", "disabled"])
    assert result.exit_code == 0, result.output
    assert mock.await_args.kwargs.get("enabled") is False


def test_rule_list_no_state_passes_none():
    mock = AsyncMock(return_value=[])
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.list_rules", new=mock,
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "list"])
    assert result.exit_code == 0, result.output
    assert mock.await_args.kwargs.get("enabled") is None


# --- add --------------------------------------------------------------------


def test_rule_add_threads_options_to_service():
    mock = AsyncMock(return_value=_rule_row(id=7))
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.add_rule", new=mock,
    ):
        result = CliRunner().invoke(firefighter_group, [
            "rule", "add",
            "--action", "restart_container",
            "--alert", "PyroscopeDown",
            "--param", "container=poindexter-pyroscope",
            "--description", "restart the profiler",
        ])
    assert result.exit_code == 0, result.output
    kw = mock.await_args.kwargs
    assert kw["action_name"] == "restart_container"
    assert kw["alertname"] == "PyroscopeDown"
    assert kw["match_regex"] is None
    assert kw["params"] == {"container": "poindexter-pyroscope"}
    assert kw["enabled"] is True
    assert "added rule #7" in result.output


def test_rule_add_disabled_flag_sets_enabled_false():
    mock = AsyncMock(return_value=_rule_row(enabled=False))
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.add_rule", new=mock,
    ):
        result = CliRunner().invoke(firefighter_group, [
            "rule", "add", "--action", "run_auto_remediate",
            "--match", "topic.batch.stuck", "--disabled",
        ])
    assert result.exit_code == 0, result.output
    kw = mock.await_args.kwargs
    assert kw["enabled"] is False
    assert kw["match_regex"] == "topic.batch.stuck"
    assert kw["alertname"] is None


def test_rule_add_threads_optional_caps():
    mock = AsyncMock(return_value=_rule_row())
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.add_rule", new=mock,
    ):
        result = CliRunner().invoke(firefighter_group, [
            "rule", "add", "--action", "restart_container", "--alert", "X",
            "--param", "container=c",
            "--max-attempts", "5", "--window-minutes", "30", "--verify-after", "90",
        ])
    assert result.exit_code == 0, result.output
    kw = mock.await_args.kwargs
    assert kw["max_attempts_per_window"] == 5
    assert kw["window_minutes"] == 30
    assert kw["verify_after_seconds"] == 90


def test_rule_add_bad_param_exits_before_service():
    mock = AsyncMock(return_value=_rule_row())
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.add_rule", new=mock,
    ):
        result = CliRunner().invoke(firefighter_group, [
            "rule", "add", "--action", "restart_container",
            "--alert", "X", "--param", "noequals",
        ])
    assert result.exit_code == 1
    assert "KEY=VALUE" in result.output
    mock.assert_not_awaited()  # never reached the DB


def test_rule_add_service_error_exits_1():
    err = ff.svc.RemediationRuleError("unknown action_name 'rm_rf'")
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.add_rule",
        new=AsyncMock(side_effect=err),
    ):
        result = CliRunner().invoke(firefighter_group, [
            "rule", "add", "--action", "rm_rf", "--alert", "X",
        ])
    assert result.exit_code == 1
    assert "unknown action_name" in result.output


# --- show / selector --------------------------------------------------------


def test_rule_show_requires_a_selector():
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.get_rule",
        new=AsyncMock(return_value=None),
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "show"])
    assert result.exit_code == 1
    assert "exactly one" in result.output


def test_rule_show_by_id_dumps_row():
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.get_rule",
        new=AsyncMock(return_value=_rule_row()),
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "PyroscopeDown" in result.output


def test_rule_show_missing_exits_1():
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.get_rule",
        new=AsyncMock(return_value=None),
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "show", "--alert", "Ghost"])
    assert result.exit_code == 1


# --- rm ---------------------------------------------------------------------


def test_rule_rm_missing_reports_and_exits():
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.remove_rule",
        new=AsyncMock(return_value=None),
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "rm", "5"])
    assert result.exit_code == 1
    assert "no matching" in result.output


def test_rule_rm_by_alert_confirms():
    mock = AsyncMock(return_value=_rule_row())
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.remove_rule", new=mock,
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "rm", "--alert", "PyroscopeDown"])
    assert result.exit_code == 0, result.output
    assert mock.await_args.kwargs.get("alertname") == "PyroscopeDown"
    assert "removed rule #1" in result.output


# --- enable / disable -------------------------------------------------------


def test_rule_enable_by_id_threads_true():
    mock = AsyncMock(return_value=_rule_row(enabled=True))
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.set_rule_enabled", new=mock,
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "enable", "3"])
    assert result.exit_code == 0, result.output
    kw = mock.await_args.kwargs
    assert kw["enabled"] is True
    assert kw["rule_id"] == 3


def test_rule_disable_by_alert_threads_false():
    mock = AsyncMock(return_value=_rule_row(enabled=False))
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.set_rule_enabled", new=mock,
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "disable", "--alert", "PromtailDown"])
    assert result.exit_code == 0, result.output
    kw = mock.await_args.kwargs
    assert kw["enabled"] is False
    assert kw["alertname"] == "PromtailDown"


def test_rule_enable_missing_exits_1():
    with patch("poindexter.cli.firefighter.run_service", _fake_run_service), patch(
        "services.remediation_rules_service.set_rule_enabled",
        new=AsyncMock(return_value=None),
    ):
        result = CliRunner().invoke(firefighter_group, ["rule", "enable", "99"])
    assert result.exit_code == 1
    assert "no matching" in result.output


# --- helpers ----------------------------------------------------------------


def test_parse_params_good_pairs():
    assert ff._parse_params(("container=poindexter-pyroscope",)) == {
        "container": "poindexter-pyroscope"
    }
    assert ff._parse_params(("a=1", "b=2")) == {"a": "1", "b": "2"}
    # value may itself contain '=' — only the first splits
    assert ff._parse_params(("url=http://x?a=b",)) == {"url": "http://x?a=b"}


def test_parse_params_rejects_missing_equals():
    with pytest.raises(ValueError, match="KEY=VALUE"):
        ff._parse_params(("noequals",))


def test_parse_params_rejects_empty_key():
    with pytest.raises(ValueError, match="non-empty key"):
        ff._parse_params(("=value",))


def test_selector_happy_paths():
    assert ff._selector(5, "") == {"rule_id": 5}
    assert ff._selector(None, "PyroscopeDown") == {"alertname": "PyroscopeDown"}


def test_selector_rejects_both_and_neither():
    with pytest.raises(SystemExit):
        ff._selector(None, "")  # neither
    with pytest.raises(SystemExit):
        ff._selector(5, "X")  # both
