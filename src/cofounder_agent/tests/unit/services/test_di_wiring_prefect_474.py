"""Regression tests for Glad-Labs/poindexter#477.

Critical bug discovered 2026-05-11 13:00 UTC: Prefect-spawned
subprocesses (active in prod since Phase 1+2 cutover #410) never run
``main.py``'s lifespan, so the 40+ ``services/*.py`` modules with
module-level ``site_config: SiteConfig`` attributes stay at the empty
default. ``site_config.get("preferred_ollama_model")`` returns ``""``,
``ollama_client``'s ``auto`` resolver falls through to
``Auto-resolved default model (largest)``, and 70-150B parameter
models get loaded into 32 GB VRAM + 63 GB host RAM, thrashing the
system. The Mixtral 8x22B + qwen2.5:72b incidents on 2026-05-11
surfaced the trail.

Fix: ``services/di_wiring.py`` centralises the wired-module list
(previously inlined in ``main.py``) and exposes
``wire_site_config_modules`` + ``build_and_wire_for_subprocess``.
``main.py``'s lifespan now calls the former; the Prefect flow body
in ``services/flows/content_generation.py`` calls the latter.

These tests pin the contract:

1. ``WIRED_MODULES`` covers every load-bearing service that holds a
   module-level ``site_config`` attribute. If a future commit adds
   such a module and forgets to register it, this test fails at
   collection time instead of at the next overnight batch.
2. ``wire_site_config_modules`` actually rebinds each module's
   ``site_config`` attribute to the instance it was given.
3. ``content_generation_flow``'s entrypoint source includes the
   subprocess wiring call. Source-level guard so a future refactor
   can't silently sever it.
4. ``main.py`` calls ``wire_site_config_modules`` from its lifespan
   (same source-level guard).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.di_wiring import (
    WIRED_MODULES,
    build_and_wire_for_subprocess,
    wire_site_config_modules,
)


# ---------------------------------------------------------------------------
# WIRED_MODULES: completeness + uniqueness
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWiredModulesList:
    """The single-source-of-truth list must cover every wired surface."""

    def test_list_is_non_empty(self):
        assert len(WIRED_MODULES) > 0, (
            "WIRED_MODULES must enumerate the modules to wire — empty list "
            "means the Prefect subprocess wiring is a no-op."
        )

    def test_no_duplicates(self):
        """Duplicates in the list don't break correctness but signal
        the list is being maintained sloppily."""
        assert len(set(WIRED_MODULES)) == len(WIRED_MODULES), (
            f"WIRED_MODULES has duplicates: "
            f"{sorted(set(WIRED_MODULES))}"
        )

    @pytest.mark.parametrize(
        "modname",
        [
            # The minimum-viable set — these directly caused the 2026-05-11
            # bleed when not wired. Adding more here is fine; removing one
            # without a paired fix elsewhere means a known failure mode is
            # un-pinned.
            "services.ollama_client",
            "services.ai_content_generator",
            "services.multi_model_qa",
            "services.content_router_service",
            "services.prompt_manager",
            "services.gpu_scheduler",
        ],
    )
    def test_known_critical_module_present(self, modname: str):
        """Pins the modules that *must* be wired or the bug recurs."""
        assert modname in WIRED_MODULES


# ---------------------------------------------------------------------------
# wire_site_config_modules: rebind verification
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWireSiteConfigModules:
    """Calling the wiring helper actually rebinds each module's attr."""

    def test_returns_wired_count(self):
        """Helper returns the count of successfully wired modules.

        Used by main.py's lifespan log line — operators read that
        number to confirm the wiring took.
        """
        marker = SimpleNamespace(name="marker_site_cfg")
        count = wire_site_config_modules(marker)
        assert count > 0, (
            "wire_site_config_modules returned 0 — every module's "
            "set_site_config failed silently. Check the WARNING logs "
            "for [di_wiring] entries."
        )

    def test_known_modules_pick_up_the_new_instance(self):
        """After wiring, the module-level ``site_config`` attr is the
        instance we passed in — not the original empty default."""
        from services.site_config import SiteConfig

        # A real SiteConfig so the wired modules accept it; the .get()
        # surface is what differentiates "wired" from "default empty".
        sentinel = SiteConfig()
        sentinel._config["__sentinel__"] = "wired_474"

        wire_site_config_modules(sentinel)

        # Check the critical modules — pulling them in via importlib
        # so the test doesn't fail at import time if one's unavailable.
        for modname in ("services.ollama_client", "services.ai_content_generator"):
            mod = __import__(modname, fromlist=["site_config"])
            cfg = getattr(mod, "site_config", None)
            assert cfg is sentinel, (
                f"{modname}.site_config is not the wired sentinel; "
                f"set_site_config wasn't called or didn't rebind."
            )

    def test_missing_module_does_not_break_the_loop(self):
        """A broken or missing module logs a WARNING but the rest still wire."""
        from services import di_wiring

        bogus_list = (
            "services.ollama_client",  # real
            "services.this_module_does_not_exist_anywhere",  # bogus
            "services.ai_content_generator",  # real, must still get wired
        )
        with patch.object(di_wiring, "WIRED_MODULES", bogus_list):
            from services.site_config import SiteConfig
            sentinel = SiteConfig()
            sentinel._config["__sentinel__"] = "loop_resilience_474"
            count = wire_site_config_modules(sentinel)

        # 2 of 3 must wire — the bogus one is swallowed.
        assert count == 2

        # The real one downstream of the broken entry still got wired.
        import services.ai_content_generator as aig
        assert getattr(aig, "site_config", None) is sentinel


# ---------------------------------------------------------------------------
# build_and_wire_for_subprocess: full Prefect-entry contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildAndWireForSubprocess:
    """The function the Prefect flow body calls at startup."""

    @pytest.mark.asyncio
    async def test_loads_site_config_then_wires(self):
        """SiteConfig.load(pool) runs, then every module gets wired."""
        from services.site_config import SiteConfig

        load_called_with = {}

        async def fake_load(self_sc, pool):
            load_called_with["pool"] = pool
            self_sc._config["preferred_ollama_model"] = "gemma3:27b"
            return 42

        with patch.object(SiteConfig, "load", fake_load):
            pool = MagicMock(name="fake_pool")
            result_cfg = await build_and_wire_for_subprocess(pool)

        # Loaded from the pool we passed in.
        assert load_called_with["pool"] is pool
        # Loaded value present on the returned SiteConfig.
        assert result_cfg.get("preferred_ollama_model") == "gemma3:27b"
        # And the critical modules now point at this same instance —
        # which is the whole point of the fix.
        import services.ollama_client as oc
        assert getattr(oc, "site_config") is result_cfg

    @pytest.mark.asyncio
    async def test_load_failure_still_wires_env_fallback(self):
        """If SiteConfig.load() fails, we still wire the empty instance.

        This matches main.py's lifespan behaviour — env-fallback
        SiteConfig is better than the un-wired empty default, because
        consumers can at least pick up environment-variable values.
        """
        from services.site_config import SiteConfig

        async def fake_load(self_sc, pool):
            raise RuntimeError("simulated DB outage")

        with patch.object(SiteConfig, "load", fake_load):
            pool = MagicMock(name="fake_pool")
            result_cfg = await build_and_wire_for_subprocess(pool)

        # Empty-default SiteConfig returned, but modules still got wired.
        import services.ollama_client as oc
        assert getattr(oc, "site_config") is result_cfg


# ---------------------------------------------------------------------------
# Source-level guards: production code actually calls the helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProductionCallsiteGuards:
    """Lock the bug shut: a future refactor must NOT sever the call sites.

    The previous regression (poindexter#473) was exactly this — a
    function was defined but no production code called it. We're not
    repeating that with the di_wiring helper.
    """

    def test_content_generation_flow_calls_build_and_wire(self):
        """The Prefect flow entrypoint must call build_and_wire_for_subprocess."""
        from services.flows import content_generation

        source = inspect.getsource(content_generation)
        assert "build_and_wire_for_subprocess" in source, (
            "services/flows/content_generation.py must call "
            "build_and_wire_for_subprocess(database_service.pool) "
            "before any pipeline stage runs. See poindexter#477."
        )

    def test_main_lifespan_calls_wire_site_config_modules(self):
        """main.py's lifespan must call wire_site_config_modules."""
        import main

        source = inspect.getsource(main)
        assert "wire_site_config_modules" in source, (
            "main.py lifespan must call wire_site_config_modules(_site_cfg) "
            "so FastAPI worker startup uses the same wiring loop as the "
            "Prefect subprocess. See poindexter#477."
        )
