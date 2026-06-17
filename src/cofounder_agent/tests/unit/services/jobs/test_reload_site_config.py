"""Unit tests for ``services/jobs/reload_site_config.py`` + its propagation.

The headline test here is ``test_runtime_settings_change_reaches_route_dependency``
— the regression for the 2026-06-17 hot-reload gap: a runtime
``poindexter settings set`` (or any out-of-band ``app_settings`` UPDATE)
wrote to the DB and the 60s ``reload_site_config`` job logged
``site_config refreshed`` — yet HTTP route handlers kept reading the
pre-change value until a worker restart.

Root cause: the worker built TWO ``SiteConfig`` instances. ``main.py``
loaded ``_site_cfg`` and seeded it into the scheduler (so the reload job
refreshed *that* one), but ``build_container`` constructed a *separate*
fresh ``SiteConfig`` that ``app.state.container`` held — and
``get_site_config_dependency`` (the route seam) returns the container's
instance. Reloading one never touched the other.

The fix collapses the worker to ONE instance by having ``build_container``
reuse the caller's already-loaded ``SiteConfig`` (``main.py`` passes
``_site_cfg``). These tests pin that invariant: the instance the scheduler
reloads IS the instance routes read.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.bootstrap import build_container
from services.container_registry import set_container
from services.jobs.reload_site_config import ReloadSiteConfigJob
from services.site_config import SiteConfig
from utils.route_utils import get_site_config_dependency


@pytest.fixture(autouse=True)
def _reset_active_container():
    """``build_container`` registers a process-wide container — clear it
    before and after each test so identity assertions don't leak."""
    set_container(None)
    yield
    set_container(None)


def _rows(**settings: str) -> list[dict[str, str]]:
    return [{"key": k, "value": v} for k, v in settings.items()]


def _fake_request(*, container, site_config) -> SimpleNamespace:
    """Minimal stand-in for a FastAPI ``Request`` that
    ``get_site_config_dependency`` can resolve against."""
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(container=container, site_config=site_config)
        )
    )


# ---------------------------------------------------------------------------
# Job characterization — the reload job refreshes its seeded instance
# ---------------------------------------------------------------------------


class TestReloadSiteConfigJob:
    async def test_reload_refreshes_seeded_instance(self):
        sc = SiteConfig(initial_config={"k": "old"})
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=_rows(k="new"))

        result = await ReloadSiteConfigJob().run(pool, {"_site_config": sc})

        assert result.ok is True
        assert sc.get("k") == "new"

    async def test_reload_missing_site_config_returns_not_ok(self):
        pool = AsyncMock()
        result = await ReloadSiteConfigJob().run(pool, {})
        assert result.ok is False
        assert "site_config" in result.detail

    async def test_reload_none_pool_returns_not_ok(self):
        sc = SiteConfig(initial_config={"k": "v"})
        result = await ReloadSiteConfigJob().run(None, {"_site_config": sc})
        assert result.ok is False


# ---------------------------------------------------------------------------
# Propagation regression — the reloaded instance IS what routes read
# ---------------------------------------------------------------------------


class TestRuntimeReloadReachesRoutes:
    async def test_runtime_settings_change_reaches_route_dependency(self):
        """End-to-end: mutate app_settings + run the reload job → the
        route-facing dependency reflects the new value, no restart.

        Replicates the worker lifespan wiring: ``main.py`` loads
        ``_site_cfg``, builds the container reusing it, seeds the
        scheduler with it. The reload job (seeded with ``_site_cfg``)
        must propagate to ``get_site_config_dependency`` — which returns
        ``app.state.container.site_config``.
        """
        # --- worker boot wiring (mirrors main.py lifespan) ---
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=_rows(enforce_niche_allowlist="true"))

        _site_cfg = SiteConfig()
        await _site_cfg.load(pool)  # main.py:170
        # main.py passes the already-loaded instance so the container
        # holds the SAME object (the fix under test).
        container = await build_container(pool, site_config=_site_cfg)
        request = _fake_request(container=container, site_config=_site_cfg)

        # The scheduler seeds the job config with the lifespan instance
        # (plugins/scheduler.py: live_cfg.config["_site_config"]).
        job_config = {"_site_config": _site_cfg}

        # --- baseline: route reads the gate as enabled ---
        before = get_site_config_dependency(request)
        assert before.get_bool("enforce_niche_allowlist", True) is True

        # --- operator runs `poindexter settings set ... false` (DB UPDATE) ---
        pool.fetch = AsyncMock(return_value=_rows(enforce_niche_allowlist="false"))

        # --- the 60s periodic job fires ---
        result = await ReloadSiteConfigJob().run(pool, job_config)
        assert result.ok is True

        # --- route now reflects the new value WITHOUT a restart ---
        after = get_site_config_dependency(request)
        assert after is _site_cfg  # one instance, not a stale copy
        assert after.get_bool("enforce_niche_allowlist", True) is False
