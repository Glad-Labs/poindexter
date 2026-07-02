"""DI-wiring helper for the module-level ``site_config`` singletons.

Background — the singleton-DI-with-lifespan-rebind pattern (GH#330):

Most ``services/*.py`` modules expose a module-level ``site_config``
attribute that defaults to an empty ``SiteConfig()`` at import time
and a ``set_site_config(...)`` setter that rebinds it to a real,
DB-loaded instance carried by the FastAPI app's lifespan. ``main.py``
loops over every wired module at lifespan startup and calls
``set_site_config(loaded_instance)`` so that all per-module attrs
point at the SAME SiteConfig the worker exposes to route handlers
via ``Depends(get_site_config_dependency)`` (today
``app.state.container.site_config``).

This works fine for processes that go through ``main.py``'s lifespan
(the FastAPI worker, the brain daemon). It does **not** work for
Prefect-spawned subprocesses (``services/flows/content_generation.py``
under the Phase 0 cutover #410), which start a fresh Python
interpreter, import the services tree, and never invoke ``main.py``'s
lifespan. Result: every per-module ``site_config`` stays the empty
default. Reads against ``site_config.get("preferred_ollama_model")``
return ``""``, and the ``auto``-resolution code path in
``services/ollama_client.py`` then falls through to "pick the
largest installed model by file size" — which on Matt's host means
loading 70-150B parameter models that don't fit in 32 GB VRAM, page-
thrashing the 63 GB host RAM, and freezing the system. (See the
2026-05-11 Mixtral / qwen2.5:72b incident for the symptom trail.)

This module centralises the wiring loop so ``main.py``'s lifespan and
``content_generation_flow``'s entrypoint share a single source of
truth for the module list. Mirrors the architectural mirror of the
poindexter#473 fix (Prefect cutover loses ``upsert_version`` calls,
same root cause: subprocess loses lifespan DI). See poindexter#477
for the tracking issue this code closes.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Single source of truth for "every module that still exposes a
# ``set_site_config()`` setter for the lifespan/subprocess wiring loop to
# call".
#
# #272 CAPSTONE (2026-05-29): this tuple is now EMPTY. The last four
# ambient-singleton modules — ``services.gpu_scheduler``,
# ``services.ollama_client``, ``services.prompt_manager``, and
# ``utils.route_utils`` — were migrated off their per-module
# ``site_config`` globals + ``set_site_config`` setters and onto the
# process-wide ``AppContainer`` accessor (``services.container_registry``
# .get_container()). ``bootstrap.build_container`` calls ``set_container``
# at the single chokepoint every entry point flows through, so each of
# the four sources its SiteConfig from the registered container (or a
# module-local empty ``_FALLBACK_SITE_CONFIG`` when none is registered —
# the no-container path that behaves exactly like the old empty global).
#
# All earlier batches (Phase-2a..2g + leaf batches 1-5) had already
# either migrated to constructor/required-keyword DI or moved to
# ``AppContainer`` cached_properties. With those four converted, the
# ambient-singleton + lifespan-rebind pattern (GH#330) is fully retired;
# ``wire_site_config_modules`` below becomes a no-op over the empty tuple
# (it still publishes to ``shared_context`` — a separate seam). The
# cleanup PR can retire this module + the wiring calls in ``main.py`` /
# the Prefect flow once we confirm nothing else depends on the
# ``shared_context`` publish side-effect.
WIRED_MODULES: tuple[str, ...] = ()


def wire_site_config_modules(site_cfg: Any) -> int:
    """Push ``site_cfg`` into every module's ``set_site_config()`` setter.

    Each per-module attribute is rebound to point at the SAME loaded
    SiteConfig instance, so a subsequent ``.reload(pool)`` on that
    instance propagates fresh DB values to every wired surface (which
    is how the scheduled ``reload_site_config`` job stays effective).

    Also publishes ``site_cfg`` to
    :mod:`services.integrations.shared_context` so the operator-notify
    helper (which doesn't have a SiteConfig in scope at most call sites)
    can resolve the lifespan-bound instance via
    ``shared_context.get_site_config()``. Without this, every call to
    :func:`services.integrations.operator_notify.notify_operator` from a
    caller that passes ``site_config=None`` ends up with the secret
    resolver short-circuiting on "no site_config in scope" and the
    ``discord_ops`` / ``telegram_ops`` handlers raising
    "no webhook URL" — the 2026-05-26 regression closed by this commit.

    Returns the count of modules successfully wired. Failures are
    logged at WARNING and the loop continues — a missing or broken
    module shouldn't take down the whole DI wiring.
    """
    # Publish to the integrations shared_context FIRST so the rest of
    # the wiring loop running below already sees a fully-resolved
    # framework state (in case any wired module's set_site_config()
    # eagerly fans out to a notify_operator call during wiring).
    try:
        from services.integrations.shared_context import set_site_config as _set_shared_site_config
        _set_shared_site_config(site_cfg)
    except Exception as exc:  # noqa: BLE001 — defensive: keep going
        logger.warning(
            "[di_wiring] shared_context.set_site_config wiring failed: %s",
            exc,
        )

    wired = 0
    for modname in WIRED_MODULES:
        try:
            mod = __import__(modname, fromlist=["set_site_config"])
            setter = getattr(mod, "set_site_config", None)
            if callable(setter):
                setter(site_cfg)
                wired += 1
        except Exception as exc:  # noqa: BLE001 — defensive: keep going
            logger.warning(
                "[di_wiring] set_site_config wiring failed for %s: %s",
                modname, exc,
            )
    return wired


async def build_and_wire_for_subprocess(pool: Any) -> Any:
    """Build a fresh SiteConfig, load it from the DB pool, wire all modules.

    The full bootstrap path for a Prefect-spawned subprocess (or any
    other lifespan-less context). Returns the loaded SiteConfig so the
    caller can hand it down to consumers that take an explicit
    ``site_config=`` kwarg (the DI seam introduced by glad-labs-stack#330).

    Logs the wiring summary so the per-flow-run boot is observable in
    Grafana / Loki — the absence of this log line at flow startup is
    the canary for a regression of this fix.
    """
    from services.site_config import SiteConfig

    site_cfg = SiteConfig()
    try:
        loaded_keys = await site_cfg.load(pool)
    except Exception as exc:
        logger.warning(
            "[di_wiring] SiteConfig.load() failed for subprocess; "
            "wiring env-fallback instance instead: %s",
            exc,
        )
        loaded_keys = 0

    wired = wire_site_config_modules(site_cfg)
    logger.info(
        "[di_wiring] subprocess SiteConfig wired: %d settings from DB, "
        "%d modules rebound", loaded_keys, wired,
    )
    return site_cfg


async def build_and_wire_subprocess_with_container(
    pool: Any,
) -> tuple[Any, Any]:
    """Subprocess bootstrap that ALSO constructs an ``AppContainer``.

    Sibling to :func:`build_and_wire_for_subprocess` for the SiteConfig
    constructor-DI migration (design doc:
    ``docs/architecture/2026-05-28-site-config-di-migration.md``).
    Returns ``(site_config, app_container)`` so the caller (a Prefect
    flow body, etc.) can keep handing the loaded SiteConfig down to
    legacy ``site_config=`` consumers AND start reaching for
    container-wired services as they migrate.

    Wiring order matters:

    1. ``build_container(pool)`` constructs a SiteConfig via the
       bootstrap helper and loads non-secret rows into it. This is the
       SAME SiteConfig instance the container holds.
    2. ``wire_site_config_modules(container.site_config)`` then fans
       the same instance out across every per-module ``site_config``
       attribute — exactly mirroring what ``main.py``'s lifespan does.

    Sharing the SiteConfig instance between the container and the
    per-module attrs is the key invariant: a ``.reload(pool)`` on the
    container's SiteConfig propagates to every wired module without a
    second wiring pass.

    Per ``feedback_no_silent_defaults``: ``build_container`` raises
    RuntimeError loudly on app_settings query failure. We propagate
    that (no try/except wrap here) — a Prefect flow that can't read
    its config shouldn't pretend it can.
    """
    from services.bootstrap import build_container

    container = await build_container(pool)
    site_cfg = container.site_config

    wired = wire_site_config_modules(site_cfg)

    # poindexter#815: this subprocess never runs main.py's lifespan, so the
    # prompt manager's async Langfuse-secret preload (``load_from_db``) never
    # happened — every flow run logged "Langfuse not configured
    # (secret_key=False)" and served YAML defaults, silently ignoring the
    # Langfuse ``production`` prompt versions the operator edits. Preload
    # here exactly like the lifespan does. Best-effort: a failed preload
    # falls through to YAML (the documented OSS path) and the prompt
    # manager's own configured-but-unusable finding stays loud.
    try:
        from services.prompt_manager import get_prompt_manager

        await get_prompt_manager().load_from_db(pool, site_config=site_cfg)
    except Exception:  # noqa: BLE001 — prompt preload must never block work
        logger.warning(
            "[di_wiring] prompt-manager Langfuse preload failed — "
            "this run will serve YAML default prompts",
            exc_info=True,
        )

    logger.info(
        "[di_wiring] subprocess AppContainer wired: %d settings from DB, "
        "%d modules rebound (container service count: 0 — services "
        "migrate one per PR)",
        len(site_cfg._config),  # noqa: SLF001 — observability only
        wired,
    )
    return site_cfg, container


def build_platform_for_subprocess(
    pool: Any, site_config: Any, *, module_name: str = "content"
) -> Any:
    """Build a Prefect-subprocess copy of a module's capability-scoped Platform.

    Wave 3c of Seam 1 (Glad-Labs/poindexter#667). The Prefect flow runs in a
    fresh subprocess that never executes ``main.py``'s lifespan, so the handle
    bound there (Wave 3b) is out of reach — the subprocess must build its own,
    exactly as it rebuilds ``site_config`` via
    :func:`build_and_wire_subprocess_with_container`.

    Constructs the full ``KernelPlatform`` from the subprocess's already-built
    ``site_config`` + ``pool`` + the LLM dispatch router + the global
    ``AuditLogger`` (init'd by the subprocess's ``DatabaseService``), then
    narrows it to ``module_name``'s declared capabilities via
    ``scope_for_module`` and returns that scoped handle.

    Best-effort by design: returns ``None`` (logged, never raised) if any
    dependency is missing or the build fails. The handle currently backs only
    best-effort audit telemetry, and a telemetry seam must never break content
    generation — mirroring ``audit_log_bg``'s own drop-and-continue posture.
    The migrated call sites treat a ``None`` handle as "drop this telemetry
    row." Imports are local so ``di_wiring`` stays import-cheap.
    """
    try:
        from plugins.kernel_platform import build_kernel_platform
        from plugins.platform import scope_for_module
        from plugins.registry import get_modules
        from services.audit_log import get_audit_logger
        from services.llm_providers.dispatcher import dispatch_complete

        audit_logger = get_audit_logger()
        if audit_logger is None:
            logger.warning(
                "[di_wiring] no global AuditLogger in subprocess — skipping "
                "Platform build (audit telemetry drops this run)",
            )
            return None

        module = next(
            (m for m in get_modules() if m.manifest().name == module_name), None
        )
        if module is None:
            logger.warning(
                "[di_wiring] module %r not discovered in subprocess — "
                "skipping Platform build",
                module_name,
            )
            return None

        full = build_kernel_platform(
            site_config=site_config,
            pool=pool,
            dispatch=dispatch_complete,
            audit_logger=audit_logger,
        )
        scoped = scope_for_module(module, full)
        logger.info(
            "[di_wiring] subprocess Platform built + scoped to module %r "
            "(capabilities: %s)",
            module_name,
            ", ".join(c.value for c in module.manifest().capabilities)
            or "(none)",
        )
        return scoped
    except Exception:
        logger.warning(
            "[di_wiring] subprocess Platform build failed — running without a "
            "handle (audit telemetry drops this run)",
            exc_info=True,
        )
        return None
