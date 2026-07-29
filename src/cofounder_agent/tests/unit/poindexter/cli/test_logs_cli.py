"""Tests for ``poindexter logs`` — the read-only Loki tail.

Two properties carry weight beyond the usual adapter checks:

* the command must stay **read-only**, because ``services/demo_clips.py``
  allowlists it for unattended demo recording against live production;
* its level colours must follow the shared colourblind-safe roles, since
  level is the one field where colour is the signal.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from poindexter.cli import _status_style as style
from poindexter.cli import logs as logs_mod
from poindexter.cli.logs import _LEVEL_COLOR, logs_command

SAMPLE = {
    "lines": [
        {"ts": "2026-07-29T04:25:41Z", "service": "worker", "level": "info", "line": "claimed task"},
        {"ts": "2026-07-29T04:25:39Z", "service": "worker", "level": "error", "line": "boom"},
    ],
    "stats": {"count": 2, "query": '{service="worker"}'},
}


@pytest.fixture
def captured(monkeypatch):
    """Stub the API hop; record the params the command asked for."""
    seen: dict = {}

    async def _fake_fetch(params):
        seen.update(params)
        return SAMPLE

    monkeypatch.setattr(logs_mod, "_fetch", _fake_fetch)
    return seen


# ---------------------------------------------------------------------------
# Palette — level is the one field where colour carries meaning
# ---------------------------------------------------------------------------


def test_level_colors_use_no_green() -> None:
    """Same constraint as the CLI status maps (poindexter#938)."""
    greens = {lvl: c for lvl, c in _LEVEL_COLOR.items() if "green" in c}
    assert not greens, f"level colours must avoid green, got {greens}"


def test_level_colors_are_declared_roles() -> None:
    roles = {
        style.SUCCESS, style.LIVE, style.ACTIVE,
        style.ATTENTION, style.FAILURE, style.NEUTRAL, style.INACTIVE,
    }
    assert set(_LEVEL_COLOR.values()) <= roles


def test_error_and_info_are_distinguishable() -> None:
    """The distinction an operator scans for must survive CVD."""
    assert _LEVEL_COLOR["error"] != _LEVEL_COLOR["info"]
    assert _LEVEL_COLOR["warn"] != _LEVEL_COLOR["error"]
    assert _LEVEL_COLOR["warn"] != _LEVEL_COLOR["info"]


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_renders_lines_oldest_first(captured) -> None:
    """The API returns newest-first; a terminal tail reads oldest-at-top."""
    result = CliRunner().invoke(logs_command, ["--limit", "2"])
    assert result.exit_code == 0
    assert result.output.index("boom") < result.output.index("claimed task")


def test_filters_are_forwarded(captured) -> None:
    result = CliRunner().invoke(
        logs_command, ["--service", "worker", "--level", "error", "--since", "6h"]
    )
    assert result.exit_code == 0
    assert captured["service"] == "worker"
    assert captured["level"] == "error"
    assert captured["since"] == "6h"


def test_empty_filters_are_not_sent(captured) -> None:
    """An empty --service must not become an empty Loki matcher."""
    CliRunner().invoke(logs_command, [])
    assert "service" not in captured
    assert "level" not in captured
    assert "query" not in captured


def test_json_output_is_the_raw_payload(captured) -> None:
    result = CliRunner().invoke(logs_command, ["--json"])
    assert json.loads(result.output) == SAMPLE


def test_empty_result_says_so(monkeypatch) -> None:
    async def _empty(params):
        return {"lines": [], "stats": {"count": 0}}

    monkeypatch.setattr(logs_mod, "_fetch", _empty)
    result = CliRunner().invoke(logs_command, ["--since", "45m"])
    assert result.exit_code == 0
    assert "no log lines" in result.output
    assert "45m" in result.output


def test_api_failure_exits_nonzero(monkeypatch) -> None:
    async def _boom(params):
        raise RuntimeError("worker unreachable")

    monkeypatch.setattr(logs_mod, "_fetch", _boom)
    result = CliRunner().invoke(logs_command, [])
    assert result.exit_code == 1
    assert "worker unreachable" in result.output


def test_service_column_hidden_when_filtering_to_one(captured) -> None:
    """Repeating the service on every line wastes width in a demo clip."""
    with_filter = CliRunner().invoke(logs_command, ["--service", "worker"]).output
    without = CliRunner().invoke(logs_command, []).output
    assert without.count("worker") > with_filter.count("worker")


# ---------------------------------------------------------------------------
# The demo-recording contract
# ---------------------------------------------------------------------------


def test_logs_is_allowlisted_for_demo_tapes() -> None:
    """Recording relies on this command being read-only by construction."""
    from services.demo_clips import READ_ONLY_VERBS

    assert "logs" in READ_ONLY_VERBS
