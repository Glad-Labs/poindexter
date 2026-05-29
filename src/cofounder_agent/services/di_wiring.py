"""DI-wiring helper for the module-level ``site_config`` singletons.

Background — the singleton-DI-with-lifespan-rebind pattern (GH#330):

Most ``services/*.py`` modules expose a module-level ``site_config``
attribute that defaults to an empty ``SiteConfig()`` at import time
and a ``set_site_config(...)`` setter that rebinds it to a real,
DB-loaded instance carried by the FastAPI app's lifespan. ``main.py``
loops over every wired module at lifespan startup and calls
``set_site_config(loaded_instance)`` so that all per-module attrs
point at the SAME SiteConfig that ``app.state.site_config`` exposes
to route handlers via ``Depends()``.

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


# Single source of truth for "every module that exposes
# ``set_site_config()``". Adding a new wired module is a one-line edit
# here; both ``main.py``'s lifespan and the Prefect flow pick it up
# automatically.
#
# Order is informational only — ``__import__`` is idempotent. Grouped
# by lifecycle for readability:
WIRED_MODULES: tuple[str, ...] = (
    # Core infra: scheduler, decorators, HTTP/Ollama clients
    # ``services.decorators`` migrated to AppContainer (PR 6, 2026-05-28
    # — see ``docs/architecture/2026-05-28-site-config-di-migration.md``).
    "services.gpu_scheduler",
    "services.ollama_client",
    # ``services.url_validator`` + ``services.url_scraper`` migrated to
    # constructor DI 2026-05-29 (#272 leaf batch 1). Reach them via
    # ``container.url_validator`` / ``container.url_scraper``, or build a
    # per-call instance from a lifespan-bound SiteConfig (caller-bridge).
    # ``services.web_research`` migrated to constructor DI 2026-05-29
    # (#272 leaf batch 2). Reach it via ``container.web_research`` or build
    # ``WebResearcher(site_config=...)`` per call (caller-bridge).
    # ``services.revalidation_service`` migrated to constructor DI 2026-05-29
    # (#272 leaf batch 3). Reach it via ``container.revalidation_service`` or
    # build a per-call instance from a lifespan-bound SiteConfig
    # (caller-bridge); the back-compat free-function wrappers now require an
    # explicit ``site_config=`` kwarg.
    "services.static_export_service",
    # ``services.telegram_config`` migrated to constructor DI 2026-05-28
    # (SiteConfig DI migration PR 3). Reach it via
    # ``container.telegram_config`` instead of importing free functions.
    "services.video_service",
    # ``services.webhook_delivery_service`` migrated to constructor DI
    # 2026-05-29 (#272 leaf batch 4). ``main.py``'s lifespan constructs
    # ``WebhookDeliveryService(pool, site_config=...)`` from the
    # lifespan-bound SiteConfig (caller-bridge); no container build-time
    # property because the runtime pool can't be supplied at build time.
    # Content pipeline + QA
    "services.publish_service",
    # ``services.citation_verifier`` + ``services.seed_url_fetcher`` +
    # ``services.title_originality_external`` migrated to constructor DI
    # 2026-05-29 (#272 leaf batch 2). Reach them via
    # ``container.citation_verifier`` / ``container.seed_url_fetcher`` /
    # ``container.title_originality_external``, or build a per-call instance
    # from a lifespan-bound SiteConfig (caller-bridge). NOTE:
    # ``citation_verifier`` still exposes ``set_http_client`` and stays in
    # ``http_client.WIRED_HTTP_CLIENT_MODULES`` — that's the separate shared
    # httpx-client plumbing, not the SiteConfig seam.
    "services.newsletter_service",
    "services.podcast_service",
    "services.multi_model_qa",
    "services.image_decision_agent",
    "services.content_validator",
    "services.research_service",
    # ``services.research_quality_service`` migrated to constructor DI
    # 2026-05-29 (#272 leaf batch 4). Reach it via
    # ``container.research_quality_service`` or build a per-call instance
    # from a lifespan-bound SiteConfig (caller-bridge).
    "services.self_review",
    "services.title_generation",
    # ``services.internal_rag_source`` migrated to constructor DI 2026-05-29
    # (#272 leaf batch 5). It takes a runtime ``pool`` the container can't
    # supply at build time, so there's no container build-time property;
    # ``topic_batch_service`` constructs
    # ``InternalRagSource(pool, site_config=...)`` from its own lifespan-bound
    # SiteConfig (caller-bridge).
    "services.scheduled_publisher",
    "services.topic_ranking",
    "services.database_service",
    "services.quality_scorers",
    "services.quality_models",
    "services.quality_service",
    "services.validator_config",
    "services.template_runner",
    "services.topic_batch_service",
    "services.pipeline_architect",
    "services.prompt_manager",
    # ``services.retention_janitor`` migrated to constructor DI 2026-05-29
    # (#272 leaf batch 3). Reach it via ``container.retention_janitor`` or
    # build a per-call instance from a lifespan-bound SiteConfig
    # (caller-bridge); ``startup_manager`` constructs ``RetentionJanitor``.
    "services.ai_content_generator",
    "services.image_service",
    "services.content_router_service",
    # ``services.seo_content_generator`` migrated to constructor DI 2026-05-29
    # (#272 leaf batch 5). Reach the SiteConfig-bearing
    # ``ContentMetadataGenerator`` via ``container.seo_content_generator``; the
    # public ``SEOOptimizedContentGenerator`` needs a runtime
    # ``ai_content_generator`` so the ``generate_seo_metadata`` stage builds it
    # from the context SiteConfig (caller-bridge).
    "services.social_poster",
    # Cross-cutting
    "utils.route_utils",
)


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
    logger.info(
        "[di_wiring] subprocess AppContainer wired: %d settings from DB, "
        "%d modules rebound (container service count: 0 — services "
        "migrate one per PR)",
        len(site_cfg._config),  # noqa: SLF001 — observability only
        wired,
    )
    return site_cfg, container
