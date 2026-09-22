"""Brain probe URLs must track app_settings, not freeze at process start.

`_sync_config_from_db` ran exactly once per process (`if _config_synced:
return`), so every URL it resolves was immune to an app_settings change until
the daemon restarted. On 2026-09-20 `ollama_vision_base_url` was set on prod
and the vision probe kept reading an empty endpoint — the process was already
running the new code, so nothing about the deploy looked wrong.

The re-read machinery was always there: the function is called at the top of
every probe cycle and returned immediately. These tests pin the three
properties that make removing the guard safe.
"""

from __future__ import annotations

import logging

import pytest

from poindexter.brain import health_probes as hp


@pytest.fixture
def db(monkeypatch):
    """Fake resolver with env-wins-over-DB-wins-over-default semantics."""
    values: dict[str, str] = {}

    async def fake_resolve(pool, *keys, default="", env_var=None):
        for key in keys:
            if values.get(key):
                return values[key]
        return default

    monkeypatch.setattr(hp, "resolve_url", fake_resolve)
    monkeypatch.setattr(hp, "API_URL", "http://worker:8002")
    monkeypatch.setattr(hp, "LOCAL_OLLAMA", "http://host:11434")
    monkeypatch.setattr(hp, "VISION_OLLAMA", "")
    monkeypatch.setattr(hp, "ALERTMANAGER_URL", "http://am:9093")
    monkeypatch.setattr(
        hp,
        "_CONFIG_BASELINE",
        {
            "API_URL": "http://worker:8002",
            "LOCAL_OLLAMA": "http://host:11434",
            "VISION_OLLAMA": "",
            "ALERTMANAGER_URL": "http://am:9093",
        },
    )
    return values


@pytest.mark.asyncio
async def test_a_setting_written_after_startup_is_picked_up(db):
    """The bug itself: the daemon must not need a restart to see a new value."""
    await hp._sync_config_from_db(None)
    assert hp.VISION_OLLAMA == ""

    db["ollama_vision_base_url"] = "http://host:11435"
    await hp._sync_config_from_db(None)
    assert hp.VISION_OLLAMA == "http://host:11435"


@pytest.mark.asyncio
async def test_a_cleared_setting_does_not_stick(db):
    """Re-resolving from the LIVE global would make a cleared value permanent.

    `resolve_url` falls back to `default` when neither env nor DB has a value.
    Passing the current global as that default means an operator emptying the
    row keeps whatever was resolved an hour ago — a silent staleness that looks
    exactly like a working config. The baselines exist to prevent this.
    """
    db["ollama_vision_base_url"] = "http://host:11435"
    await hp._sync_config_from_db(None)
    assert hp.VISION_OLLAMA == "http://host:11435"

    db["ollama_vision_base_url"] = ""
    await hp._sync_config_from_db(None)
    assert hp.VISION_OLLAMA == "", "a cleared setting must clear the resolved value"


@pytest.mark.asyncio
async def test_logging_is_on_change_only(db, caplog):
    """Every cycle would be ~288 lines/day of 'nothing happened'; never was
    what hid the original bug. A line means a value actually moved."""
    with caplog.at_level(logging.INFO, logger=hp.logger.name):
        await hp._sync_config_from_db(None)  # first resolution — a change
        first = len([r for r in caplog.records if "Config resolved" in r.message])
        await hp._sync_config_from_db(None)  # identical — must stay silent
        await hp._sync_config_from_db(None)
        quiet = len([r for r in caplog.records if "Config resolved" in r.message])
        assert quiet == first, "an unchanged re-resolve must not log"

        db["ollama_vision_base_url"] = "http://host:11435"
        await hp._sync_config_from_db(None)
        after = len([r for r in caplog.records if "Config resolved" in r.message])
        assert after == first + 1, "a changed value must log exactly once"


@pytest.mark.asyncio
async def test_an_unset_url_is_named_rather_than_blank(db, caplog):
    """A probe reading a blank endpoint is indistinguishable in the log from one
    that is working — the failure this file exists to catch elsewhere.

    Something else has to move to produce a line at all: with every value
    already equal to what resolves, the first call is correctly NOT a change
    and stays silent. That is the intended behaviour, not a gap.
    """
    db["internal_api_base_url"] = "http://moved:8002"
    with caplog.at_level(logging.INFO, logger=hp.logger.name):
        await hp._sync_config_from_db(None)
    lines = [r.getMessage() for r in caplog.records if "Config resolved" in r.message]
    assert lines, "a changed value must produce exactly one line"
    assert "VisionOllama=(unset)" in lines[0]


@pytest.mark.asyncio
async def test_a_resolver_failure_leaves_the_previous_values(db, monkeypatch):
    """Best-effort: a DB blip must not blank every probe URL."""
    db["ollama_vision_base_url"] = "http://host:11435"
    await hp._sync_config_from_db(None)

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(hp, "resolve_url", boom)
    await hp._sync_config_from_db(None)
    assert hp.VISION_OLLAMA == "http://host:11435"


def test_the_one_shot_guard_is_gone():
    """Regression guard. Re-introducing `_config_synced` restores the exact bug:
    the function keeps being called every cycle and keeps doing nothing."""
    assert not hasattr(hp, "_config_synced"), (
        "_sync_config_from_db must re-resolve every cycle — a one-shot guard "
        "makes every URL here immune to app_settings until a restart"
    )
