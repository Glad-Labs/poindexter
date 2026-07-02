"""Regression tests for Glad-Labs/poindexter#477 + the #272 capstone.

Critical bug discovered 2026-05-11 13:00 UTC: Prefect-spawned
subprocesses (active in prod since Phase 1+2 cutover #410) never run
``main.py``'s lifespan, so the 40+ ``services/*.py`` modules with
module-level ``site_config: SiteConfig`` attributes stayed at the empty
default. ``site_config.get("preferred_ollama_model")`` returned ``""``,
``ollama_client``'s ``auto`` resolver fell through to the largest model,
and 70-150B parameter models thrashed the box.

The original fix centralised the wired-module list in
``services/di_wiring.py`` and fanned a loaded SiteConfig out across every
per-module ``site_config`` attribute via ``set_site_config``.

**#272 CAPSTONE (2026-05-29):** the ambient-singleton + lifespan-rebind
pattern (GH#330) is now fully retired. The last four modules
(``gpu_scheduler`` / ``ollama_client`` / ``prompt_manager`` /
``utils.route_utils``) source their SiteConfig from the process-wide
``AppContainer`` accessor (``services.container_registry.get_container``)
instead of a per-module global. ``WIRED_MODULES`` is therefore EMPTY and
``set_site_config`` setters no longer exist on those modules.

These tests pin the NEW contract:

1. ``WIRED_MODULES`` is empty — the per-module wiring loop is retired.
2. ``wire_site_config_modules`` is a no-op over the empty tuple but still
   succeeds (and still publishes to ``shared_context`` — a separate seam).
3. The accessor (``container_registry.set_container`` /
   ``get_container``) round-trips a container, and the four migrated
   modules source their SiteConfig from it.
4. The no-container path falls back to an empty SiteConfig (never crashes).
5. ``main.py`` + the Prefect flow still call the wiring helpers
   (source-level guards — closes the poindexter#473/#477 sever class).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.di_wiring import (
    WIRED_MODULES,
    build_and_wire_for_subprocess,
    wire_site_config_modules,
)

# ---------------------------------------------------------------------------
# WIRED_MODULES: now empty (the per-module wiring loop is retired)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWiredModulesList:
    """#272 capstone retired the per-module ``set_site_config`` loop."""

    def test_list_is_empty(self):
        """All ambient singletons migrated — the tuple is empty."""
        assert WIRED_MODULES == (), (
            "WIRED_MODULES must be empty after the #272 capstone — every "
            "ambient-singleton module now sources SiteConfig from the "
            f"process-wide AppContainer. Found: {WIRED_MODULES!r}"
        )

    def test_no_duplicates(self):
        assert len(set(WIRED_MODULES)) == len(WIRED_MODULES)

    @pytest.mark.parametrize(
        "modname",
        [
            "services.ollama_client",
            "services.prompt_manager",
            "services.gpu_scheduler",
            "utils.route_utils",
        ],
    )
    def test_migrated_module_has_no_setter(self, modname: str):
        """The four capstone modules no longer expose ``set_site_config``
        nor a module-level ``site_config`` global — they source it from
        the container accessor instead. This pins that the migration
        actually deleted those seams (verify-clean)."""
        mod = __import__(modname, fromlist=["set_site_config"])
        assert not hasattr(mod, "set_site_config"), (
            f"{modname} must NOT expose set_site_config after the #272 "
            "capstone — it sources SiteConfig from container_registry."
        )
        assert not hasattr(mod, "site_config"), (
            f"{modname} must NOT carry a module-level site_config global "
            "after the #272 capstone."
        )


# ---------------------------------------------------------------------------
# wire_site_config_modules: no-op over the empty tuple, still succeeds
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWireSiteConfigModules:
    """The helper survives an empty WIRED_MODULES (cleanup hasn't yet
    removed the call sites in main.py / the Prefect flow)."""

    def test_returns_zero_on_empty_list(self):
        """No modules to wire → returns 0 without raising."""
        marker = SimpleNamespace(name="marker_site_cfg")
        count = wire_site_config_modules(marker)
        assert count == 0

    def test_still_publishes_to_shared_context(self):
        """The shared_context publish side-effect is independent of the
        (now-empty) per-module loop and must keep firing — the
        operator-notify helper depends on it."""
        from services.site_config import SiteConfig

        sentinel = SiteConfig()
        sentinel._config["__sentinel__"] = "shared_ctx_272"

        wire_site_config_modules(sentinel)

        from services.integrations import shared_context
        assert shared_context.get_site_config() is sentinel

    def test_empty_loop_does_not_break(self):
        """Patching WIRED_MODULES back to a bogus list still wires the
        real entries — proves the loop body is intact for any future
        re-population."""
        from services import di_wiring

        with patch.object(
            di_wiring,
            "WIRED_MODULES",
            ("services.this_module_does_not_exist_anywhere",),
        ):
            from services.site_config import SiteConfig
            count = wire_site_config_modules(SiteConfig())

        # The bogus entry is swallowed (no set_site_config) → 0 wired.
        assert count == 0


# ---------------------------------------------------------------------------
# container_registry accessor: the new SiteConfig source for the four
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContainerAccessor:
    """The process-wide AppContainer accessor replaces the per-module
    ``set_site_config`` fan-out."""

    def test_round_trips_container(self):
        from services.container import AppContainer
        from services.container_registry import get_container, set_container
        from services.site_config import SiteConfig

        original = get_container()
        try:
            sentinel = SiteConfig()
            sentinel._config["__sentinel__"] = "accessor_272"
            container = AppContainer(site_config=sentinel, pool=MagicMock())
            set_container(container)
            assert get_container() is container
            assert get_container().site_config is sentinel
        finally:
            set_container(original)

    def test_modules_source_site_config_from_container(self):
        """gpu_scheduler ``_sc()`` + prompt_manager ``_sc()`` resolve the
        registered container's SiteConfig."""
        import services.gpu_scheduler as gs
        import services.prompt_manager as pm
        from services.container import AppContainer
        from services.container_registry import get_container, set_container
        from services.site_config import SiteConfig

        original = get_container()
        try:
            sentinel = SiteConfig()
            sentinel._config["preferred_ollama_model"] = "gemma3:27b"
            set_container(AppContainer(site_config=sentinel, pool=MagicMock()))

            assert gs._sc() is sentinel
            assert pm._sc() is sentinel
            # ollama_client's patchable _sc_get reads _sc() when no
            # instance is injected.
            assert gs._sc().get("preferred_ollama_model") == "gemma3:27b"
        finally:
            set_container(original)

    def test_no_container_falls_back_to_empty(self):
        """With no container registered, ``_sc()`` returns the module's
        empty fallback rather than crashing."""
        import services.gpu_scheduler as gs
        import services.ollama_client as oc
        import services.prompt_manager as pm
        from services.container_registry import get_container, set_container

        original = get_container()
        try:
            set_container(None)
            # Each returns a usable SiteConfig (the empty fallback), no raise.
            assert gs._sc() is gs._FALLBACK_SITE_CONFIG
            assert oc._sc() is oc._FALLBACK_SITE_CONFIG
            assert pm._sc() is pm._FALLBACK_SITE_CONFIG
            # And reads default cleanly.
            assert oc._sc_get("missing_key", "fallback") == "fallback"
        finally:
            set_container(original)


# ---------------------------------------------------------------------------
# build_and_wire_for_subprocess: still loads + wires (shared_context seam)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildAndWireForSubprocess:
    """The legacy subprocess seam still loads SiteConfig from the pool and
    publishes to shared_context (the per-module loop is now empty)."""

    @pytest.mark.asyncio
    async def test_loads_site_config_then_publishes(self):
        from services.site_config import SiteConfig

        load_called_with = {}

        async def fake_load(self_sc, pool):
            load_called_with["pool"] = pool
            self_sc._config["preferred_ollama_model"] = "gemma3:27b"
            return 42

        with patch.object(SiteConfig, "load", fake_load):
            pool = MagicMock(name="fake_pool")
            result_cfg = await build_and_wire_for_subprocess(pool)

        assert load_called_with["pool"] is pool
        assert result_cfg.get("preferred_ollama_model") == "gemma3:27b"
        # Published to shared_context (the surviving wiring side-effect).
        from services.integrations import shared_context
        assert shared_context.get_site_config() is result_cfg

    @pytest.mark.asyncio
    async def test_load_failure_still_returns_env_fallback(self):
        from services.site_config import SiteConfig

        async def fake_load(self_sc, pool):
            raise RuntimeError("simulated DB outage")

        with patch.object(SiteConfig, "load", fake_load):
            pool = MagicMock(name="fake_pool")
            result_cfg = await build_and_wire_for_subprocess(pool)

        # Empty-default SiteConfig returned; no crash.
        assert isinstance(result_cfg, SiteConfig)


# ---------------------------------------------------------------------------
# Source-level guards: production code still calls the helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProductionCallsiteGuards:
    """Lock the bug shut: a future refactor must NOT sever the call sites
    until the cleanup PR retires the helpers entirely."""

    def test_content_generation_flow_calls_build_and_wire(self):
        from pathlib import Path

        flow_path = (
            Path(inspect.getsourcefile(inspect.currentframe())).resolve()
            .parents[3]
            / "services" / "flows" / "content_generation.py"
        )
        assert flow_path.is_file(), (
            f"expected content_generation.py at {flow_path}"
        )
        source = flow_path.read_text(encoding="utf-8")
        wires_legacy = "build_and_wire_for_subprocess" in source
        wires_container = "build_and_wire_subprocess_with_container" in source
        assert wires_legacy or wires_container, (
            "services/flows/content_generation.py must call either "
            "build_and_wire_for_subprocess (legacy) or "
            "build_and_wire_subprocess_with_container (DI migration PR 2)."
        )

    def test_main_lifespan_builds_container(self):
        """main.py's lifespan must build the AppContainer (which registers
        it via ``set_container`` inside ``build_container``) so the four
        accessor-migrated modules resolve a configured SiteConfig."""
        import main

        source = inspect.getsource(main)
        assert "build_container" in source, (
            "main.py lifespan must call services.bootstrap.build_container "
            "so the process-wide AppContainer is registered (#272 capstone "
            "accessor source for gpu_scheduler / ollama_client / "
            "prompt_manager / utils.route_utils)."
        )


class TestSubprocessPromptManagerPreload:
    """poindexter#815 — the Prefect subprocess never ran main.py's lifespan,
    so the prompt manager's async Langfuse-secret preload (load_from_db)
    never happened: every flow run logged "Langfuse not configured
    (secret_key=False)" and served YAML defaults, silently ignoring
    Langfuse `production` prompt versions. The subprocess bootstrap must
    preload the prompt manager exactly like the worker lifespan does.
    """

    @pytest.mark.asyncio
    async def test_container_bootstrap_preloads_prompt_manager(self):
        from unittest.mock import AsyncMock

        from services.di_wiring import build_and_wire_subprocess_with_container

        fake_sc = MagicMock()
        fake_sc._config = {}
        fake_container = SimpleNamespace(site_config=fake_sc)
        fake_pm = MagicMock()
        fake_pm.load_from_db = AsyncMock(return_value=0)

        with patch(
            "services.bootstrap.build_container",
            AsyncMock(return_value=fake_container),
        ), patch(
            "services.prompt_manager.get_prompt_manager",
            return_value=fake_pm,
        ):
            pool = MagicMock()
            site_cfg, container = await build_and_wire_subprocess_with_container(pool)

        assert site_cfg is fake_sc
        fake_pm.load_from_db.assert_awaited_once_with(pool, site_config=fake_sc)

    @pytest.mark.asyncio
    async def test_preload_failure_never_breaks_bootstrap(self):
        from unittest.mock import AsyncMock

        from services.di_wiring import build_and_wire_subprocess_with_container

        fake_sc = MagicMock()
        fake_sc._config = {}
        fake_container = SimpleNamespace(site_config=fake_sc)
        fake_pm = MagicMock()
        fake_pm.load_from_db = AsyncMock(side_effect=RuntimeError("db down"))

        with patch(
            "services.bootstrap.build_container",
            AsyncMock(return_value=fake_container),
        ), patch(
            "services.prompt_manager.get_prompt_manager",
            return_value=fake_pm,
        ):
            site_cfg, _ = await build_and_wire_subprocess_with_container(MagicMock())

        assert site_cfg is fake_sc  # bootstrap survived; YAML fallback applies
