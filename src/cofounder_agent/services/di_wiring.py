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
    "services.gpu_scheduler",
    "services.decorators",
    "services.ollama_client",
    "services.url_validator",
    "services.url_scraper",
    "services.web_research",
    "services.redis_cache",
    "services.r2_upload_service",
    "services.revalidation_service",
    "services.static_export_service",
    "services.telegram_config",
    "services.video_service",
    "services.webhook_delivery_service",
    # Content pipeline + QA
    "services.publish_service",
    "services.newsletter_service",
    "services.podcast_service",
    "services.multi_model_qa",
    "services.image_decision_agent",
    "services.content_validator",
    "services.research_service",
    "services.research_quality_service",
    "services.seed_url_fetcher",
    "services.self_review",
    "services.title_generation",
    "services.title_originality_external",
    "services.internal_rag_source",
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
    "services.retention_janitor",
    "services.ai_content_generator",
    "services.image_service",
    "services.content_router_service",
    "services.seo_content_generator",
    "services.social_poster",
    # Cross-cutting + admin
    "utils.route_utils",
    "admin",
)


def wire_site_config_modules(site_cfg: Any) -> int:
    """Push ``site_cfg`` into every module's ``set_site_config()`` setter.

    Each per-module attribute is rebound to point at the SAME loaded
    SiteConfig instance, so a subsequent ``.reload(pool)`` on that
    instance propagates fresh DB values to every wired surface (which
    is how the scheduled ``reload_site_config`` job stays effective).

    Returns the count of modules successfully wired. Failures are
    logged at WARNING and the loop continues — a missing or broken
    module shouldn't take down the whole DI wiring.
    """
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
