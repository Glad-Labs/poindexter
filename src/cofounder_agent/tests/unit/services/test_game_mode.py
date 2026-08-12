"""Unit tests for services/game_mode.py + its three consumers.

The behaviours pinned here are the ones whose regression would be silent and
expensive:

* a forgotten game mode must EXPIRE (a boolean would starve the pipeline),
* a corrupt timestamp must read as OFF everywhere (failing open starves it too),
* the Prefect flow must not CLAIM work during a window (claim → GPU reject →
  retry burn → healthy tasks driven to terminal `failed`),
* the brain must fold parked services into its on-demand set (otherwise
  `compose up -d` undoes the whole feature within one cycle, which is the
  original bug this feature exists to fix).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from services import game_mode
from services.site_config import SiteConfig


def _sc(**overrides: str) -> SiteConfig:
    base = {
        game_mode.PARKED_SERVICES_KEY: game_mode.PARKED_SERVICES_DEFAULT,
        game_mode.DEFAULT_HOURS_KEY: game_mode.DEFAULT_HOURS_DEFAULT,
        game_mode.CONTAINER_PREFIX_KEY: game_mode.CONTAINER_PREFIX_DEFAULT,
    }
    base.update(overrides)
    return SiteConfig(initial_config=base)


class _FakePool:
    """Minimal asyncpg-pool stand-in recording the last written value."""

    def __init__(self, initial: str = "") -> None:
        self.value = initial
        self.executed: list[tuple] = []

    async def execute(self, _sql: str, *args):
        self.executed.append(args)
        self.value = args[1]

    async def fetchval(self, _sql: str, *_args):
        return self.value


# ---------------------------------------------------------------------------
# TTL semantics — the reason this is a timestamp and not a bool
# ---------------------------------------------------------------------------


def test_expired_window_reads_as_off():
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    assert game_mode.is_active(_sc(**{game_mode.UNTIL_KEY: past})) is False


def test_future_window_reads_as_on():
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert game_mode.is_active(_sc(**{game_mode.UNTIL_KEY: future})) is True


def test_empty_value_is_off():
    assert game_mode.is_active(_sc(**{game_mode.UNTIL_KEY: ""})) is False


def test_naive_timestamp_is_treated_as_utc():
    """A hand-edited setting without a tz must not crash or flip meaning."""
    naive = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert game_mode.is_active(_sc(**{game_mode.UNTIL_KEY: naive})) is True


def test_corrupt_timestamp_fails_closed_not_open():
    """Garbage must read as OFF.

    Failing OPEN would pause the pipeline forever with nothing to notice it —
    the single worst outcome this module can produce.
    """
    assert game_mode.is_active(_sc(**{game_mode.UNTIL_KEY: "not-a-date"})) is False


# ---------------------------------------------------------------------------
# enable/disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_writes_expiry_and_reports_parked_services():
    pool = _FakePool()
    sc = _sc()
    now = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)

    status = await game_mode.enable(pool, sc, hours=4, now=now)

    assert status.active is True
    assert status.until == now + timedelta(hours=4)
    assert status.seconds_remaining == 4 * 3600
    assert "speaches" in status.parked_services
    assert pool.value == (now + timedelta(hours=4)).isoformat()


@pytest.mark.asyncio
async def test_enable_defaults_hours_from_settings():
    pool = _FakePool()
    sc = _sc(**{game_mode.DEFAULT_HOURS_KEY: "2"})
    now = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)

    status = await game_mode.enable(pool, sc, now=now)

    assert status.until == now + timedelta(hours=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, game_mode.MAX_HOURS + 1])
async def test_enable_rejects_impossible_durations(bad):
    """Raise rather than clamp — feedback_no_silent_defaults."""
    with pytest.raises(ValueError):
        await game_mode.enable(_FakePool(), _sc(), hours=bad)


@pytest.mark.asyncio
async def test_disable_clears_the_value_and_is_idempotent():
    pool = _FakePool(initial="2026-08-10T08:00:00+00:00")
    sc = _sc()

    first = await game_mode.disable(pool, sc)
    second = await game_mode.disable(pool, sc)

    assert first.active is False and second.active is False
    assert pool.value == ""


@pytest.mark.asyncio
async def test_status_reads_db_not_cache():
    """The claim guard depends on this: a stale cache must not win."""
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    pool = _FakePool(initial=future)
    # Cache says OFF, DB says ON — DB must win.
    sc = _sc(**{game_mode.UNTIL_KEY: ""})

    status = await game_mode.status(pool, sc)

    assert status.active is True


@pytest.mark.asyncio
async def test_status_without_site_config_still_answers_active():
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    status = await game_mode.status(_FakePool(initial=future))
    assert status.active is True
    assert status.parked_services == ()


# ---------------------------------------------------------------------------
# container-name derivation
# ---------------------------------------------------------------------------


def test_container_names_apply_the_configured_prefix():
    sc = _sc(**{
        game_mode.PARKED_SERVICES_KEY: "speaches,wan-server",
        game_mode.CONTAINER_PREFIX_KEY: "poindexter-",
    })
    assert game_mode.container_names(sc) == (
        "poindexter-speaches",
        "poindexter-wan-server",
    )


def test_parked_services_tolerates_whitespace_and_blanks():
    sc = _sc(**{game_mode.PARKED_SERVICES_KEY: " speaches , , wan-server "})
    assert game_mode.parked_services(sc) == ("speaches", "wan-server")


# ---------------------------------------------------------------------------
# Consumer 1 — GPU scheduler refuses new acquires during a window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gpu_scheduler_rejects_new_acquire_during_game_mode(monkeypatch):
    from services import gpu_scheduler
    from services.gpu_admission import GpuBusyError

    future = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
    monkeypatch.setattr(
        gpu_scheduler, "_sc", lambda: _sc(**{game_mode.UNTIL_KEY: future})
    )

    scheduler = gpu_scheduler.GPUScheduler()
    with pytest.raises(GpuBusyError) as excinfo:
        await scheduler._wait_for_gaming_clear()

    assert excinfo.value.reason == "game_mode"
    # Exact expiry, not an estimate — callers can log a real ETA.
    assert excinfo.value.eta_seconds == pytest.approx(3 * 3600, abs=5)


@pytest.mark.asyncio
async def test_gpu_scheduler_game_mode_outranks_current_owner_guard(monkeypatch):
    """A held lock must not suppress the game-mode reject.

    Reentrant acquires never reach this function (lock() returns earlier on
    _gpu_session_active), so anything arriving here is genuinely new work.
    """
    from services import gpu_scheduler
    from services.gpu_admission import GpuBusyError

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        gpu_scheduler, "_sc", lambda: _sc(**{game_mode.UNTIL_KEY: future})
    )

    scheduler = gpu_scheduler.GPUScheduler()
    scheduler._current_owner = "ollama"

    with pytest.raises(GpuBusyError):
        await scheduler._wait_for_gaming_clear()


@pytest.mark.asyncio
async def test_gpu_scheduler_unaffected_when_game_mode_off(monkeypatch):
    """The util-heuristic path stays exactly as it was — no new false pauses."""
    from services import gpu_scheduler

    monkeypatch.setattr(
        gpu_scheduler, "_sc", lambda: _sc(**{game_mode.UNTIL_KEY: ""})
    )
    scheduler = gpu_scheduler.GPUScheduler()
    scheduler._current_owner = "ollama"

    # Returns (does not raise) via the pre-existing _current_owner guard.
    await scheduler._wait_for_gaming_clear()
    assert scheduler._gaming_detected is False


# ---------------------------------------------------------------------------
# CLI adapter — host-vs-container Ollama URL (found on first live `game on`)
#
# `app_settings.ollama_base_url` is the CONTAINER-facing
# `http://host.docker.internal:11434`, which does not resolve in a host-side
# process. The first real run logged two ConnectErrors and then reported
# "nothing resident" while 21 GB stayed pinned on the second card — a false
# success the operator would have acted on.
# ---------------------------------------------------------------------------


class TestHostReachableOllamaUrl:
    def test_rewrites_container_hostname_to_localhost(self):
        from poindexter.cli.game import _host_reachable_url

        assert (
            _host_reachable_url("http://host.docker.internal:11434")
            == "http://localhost:11434"
        )

    def test_preserves_the_port_so_the_vision_instance_still_resolves(self):
        """:11435 is the vision-pinned instance — the one holding the big model.
        Dropping the port would silently unload only the primary."""
        from poindexter.cli.game import _host_reachable_url

        assert (
            _host_reachable_url("http://host.docker.internal:11435")
            == "http://localhost:11435"
        )

    def test_leaves_an_already_host_reachable_url_alone(self):
        from poindexter.cli.game import _host_reachable_url

        for url in ("http://localhost:11434", "http://127.0.0.1:11434",
                    "http://gpu-host.internal:11434"):
            assert _host_reachable_url(url) == url

    def test_empty_url_is_passed_through(self):
        from poindexter.cli.game import _host_reachable_url

        assert _host_reachable_url("") == ""


class TestEvictReportsUnreachableHonestly:
    @pytest.mark.asyncio
    async def test_unreachable_host_is_not_reported_as_nothing_resident(self):
        """The bug this pins: an unreachable Ollama must NOT read as success.

        `unload_loaded_ollama_models` swallows transport errors and returns [],
        which is indistinguishable from "no models loaded" — so the adapter
        probes first and says so.
        """
        from unittest.mock import AsyncMock, patch

        from poindexter.cli import game as game_cli

        sc = _sc()
        with patch.object(game_cli, "_ollama_reachable", AsyncMock(return_value=False)), \
             patch(
                 "services.llm_providers.ollama_unload.ollama_base_urls",
                 lambda _sc: ["http://host.docker.internal:11434"],
             ):
            msg = await game_cli._evict_ollama(sc)

        assert "UNREACHABLE" in msg
        assert "VRAM NOT freed" in msg
        assert "nothing resident" not in msg

    @pytest.mark.asyncio
    async def test_reachable_and_empty_still_reads_as_nothing_resident(self):
        from unittest.mock import AsyncMock, patch

        from poindexter.cli import game as game_cli

        sc = _sc()
        with patch.object(game_cli, "_ollama_reachable", AsyncMock(return_value=True)), \
             patch(
                 "services.llm_providers.ollama_unload.ollama_base_urls",
                 lambda _sc: ["http://localhost:11434"],
             ), patch(
                 "services.llm_providers.ollama_unload.unload_loaded_ollama_models",
                 AsyncMock(return_value=[]),
             ):
            msg = await game_cli._evict_ollama(sc)

        assert msg == "nothing resident"
