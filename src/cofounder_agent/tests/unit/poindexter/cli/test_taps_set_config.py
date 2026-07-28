"""Tests for `poindexter taps set-config` (#925).

The web_search source now fails loud when a tap has neither `seed_queries`
nor `categories`, and its error message tells the operator to set one. There
was no CLI able to do that — `taps` had only list/show/enable/disable/run —
so the remediation was unactionable. This command closes that gap
(``feedback_cli_first``).
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

import poindexter.cli.taps as taps


@pytest.fixture
def runner():
    return CliRunner()


def _patch_service(monkeypatch, row, captured):
    """Stub the declarative-config service; capture what upsert receives."""
    async def _get_row(pool, surface, name):
        return dict(row) if row is not None else None

    async def _upsert_row(pool, surface, payload):
        captured.append(payload)

    # run_service drives the coroutine without a real pool.
    def _run_service(factory):
        import asyncio
        return asyncio.run(factory(None))

    monkeypatch.setattr(taps.dcs, "get_row", _get_row)
    monkeypatch.setattr(taps.dcs, "upsert_row", _upsert_row)
    monkeypatch.setattr(taps, "run_service", _run_service)


def test_registered_as_subcommand():
    assert "set-config" in taps.taps_group.commands


def test_merges_into_existing_config(runner, monkeypatch):
    """Unrelated keys (weight_pct) must survive — the live glad-labs tap
    carries one, and clobbering it would silently re-weight discovery."""
    captured: list[dict] = []
    _patch_service(
        monkeypatch,
        {"name": "glad-labs_web_search", "config": {"weight_pct": 10}},
        captured,
    )

    res = runner.invoke(taps.taps_group, [
        "set-config", "glad-labs_web_search",
        '{"categories": ["technology", "engineering"]}',
    ])

    assert res.exit_code == 0, res.output
    assert captured[0]["config"] == {
        "weight_pct": 10,
        "categories": ["technology", "engineering"],
    }


def test_replace_flag_overwrites_wholesale(runner, monkeypatch):
    captured: list[dict] = []
    _patch_service(
        monkeypatch,
        {"name": "t", "config": {"weight_pct": 10, "stale": "x"}},
        captured,
    )

    res = runner.invoke(taps.taps_group, [
        "set-config", "t", '{"categories": ["technology"]}', "--replace",
    ])

    assert res.exit_code == 0, res.output
    assert captured[0]["config"] == {"categories": ["technology"]}


def test_config_stored_as_json_string_is_parsed(runner, monkeypatch):
    """Rows written by an older path can carry config as a JSON string."""
    captured: list[dict] = []
    _patch_service(
        monkeypatch,
        {"name": "t", "config": json.dumps({"weight_pct": 5})},
        captured,
    )

    res = runner.invoke(taps.taps_group, ["set-config", "t", '{"categories": ["ai"]}'])

    assert res.exit_code == 0, res.output
    assert captured[0]["config"] == {"weight_pct": 5, "categories": ["ai"]}


def test_rejects_invalid_json(runner, monkeypatch):
    captured: list[dict] = []
    _patch_service(monkeypatch, {"name": "t", "config": {}}, captured)

    res = runner.invoke(taps.taps_group, ["set-config", "t", "{not json"])

    assert res.exit_code == 1
    assert "not valid JSON" in res.output
    assert not captured


def test_rejects_non_object_json(runner, monkeypatch):
    captured: list[dict] = []
    _patch_service(monkeypatch, {"name": "t", "config": {}}, captured)

    res = runner.invoke(taps.taps_group, ["set-config", "t", '["a", "b"]'])

    assert res.exit_code == 1
    assert "must be a JSON object" in res.output
    assert not captured


def test_unknown_tap_exits_nonzero(runner, monkeypatch):
    captured: list[dict] = []
    _patch_service(monkeypatch, None, captured)

    res = runner.invoke(taps.taps_group, ["set-config", "nope", "{}"])

    assert res.exit_code == 1
    assert "no tap named" in res.output
    assert not captured
