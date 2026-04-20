"""Plugin registry — discover plugins via setuptools entry_points.

No custom registry, no decorators, no pkgutil auto-imports. We use
``importlib.metadata.entry_points()`` — the same mechanism pytest,
click, and flask use.

Each plugin package declares its contributions in its ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.taps"]
    gitea = "poindexter_tap_gitea:GiteaTap"

At runtime Poindexter discovers them:

.. code:: python

    from plugins.registry import get_taps
    for tap in get_taps():
        ...

Each ``get_*`` function returns *instances*, not classes — the entry_point
target is called with no arguments to instantiate the plugin. Plugins that
need more than parameterless construction should expose a factory function
as their entry_point target.

Results are cached for the lifetime of the process; call
:func:`clear_registry_cache` in tests or after a ``pip install`` if you
need to re-discover.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from functools import cache
from importlib.metadata import EntryPoint, entry_points
from typing import Any

logger = logging.getLogger(__name__)


# Canonical entry_point group names. Kept as module-level constants so
# plugin authors can reference them and stay in sync.
ENTRY_POINT_GROUPS: dict[str, str] = {
    "taps": "poindexter.taps",
    "probes": "poindexter.probes",
    "jobs": "poindexter.jobs",
    "stages": "poindexter.stages",
    "reviewers": "poindexter.reviewers",
    "adapters": "poindexter.adapters",
    "providers": "poindexter.providers",
    "packs": "poindexter.packs",
    "llm_providers": "poindexter.llm_providers",
    "topic_sources": "poindexter.topic_sources",
    "image_providers": "poindexter.image_providers",
}


def _load_group(group: str) -> list[Any]:
    """Load every entry_point in ``group``, instantiate each, and return
    the resulting objects.

    Entry_points that fail to import or instantiate are logged and
    skipped — one broken plugin must not block discovery of the others.
    """
    try:
        # Python 3.10+: entry_points() accepts a group kwarg.
        eps: Iterable[EntryPoint] = entry_points(group=group)
    except TypeError:
        # Fallback for older 3.9 shape, just in case.
        all_eps = entry_points()
        eps = all_eps.get(group, []) if isinstance(all_eps, dict) else []

    instances: list[Any] = []
    for ep in eps:
        try:
            target = ep.load()
        except Exception as e:
            logger.error(
                "plugin discovery: failed to load %s entry_point %r: %s",
                group, ep.name, e,
            )
            continue
        try:
            instance = target() if callable(target) else target
        except Exception as e:
            logger.error(
                "plugin discovery: failed to instantiate %s plugin %r: %s",
                group, ep.name, e,
            )
            continue
        instances.append(instance)
    return instances


@cache
def _cached(group: str) -> tuple[Any, ...]:
    """Cached tuple wrapper around ``_load_group``.

    Tuple return so ``lru_cache`` works (lists are unhashable — though
    that doesn't affect the cache key here, keeping it a tuple means
    callers can't mutate the cached list by accident).
    """
    return tuple(_load_group(group))


def get_taps() -> list[Any]:
    """Return all registered Tap instances."""
    return list(_cached(ENTRY_POINT_GROUPS["taps"]))


def get_probes() -> list[Any]:
    """Return all registered Probe instances."""
    return list(_cached(ENTRY_POINT_GROUPS["probes"]))


def get_jobs() -> list[Any]:
    """Return all registered Job instances."""
    return list(_cached(ENTRY_POINT_GROUPS["jobs"]))


def get_stages() -> list[Any]:
    """Return all registered Stage instances (excluding specializations)."""
    return list(_cached(ENTRY_POINT_GROUPS["stages"]))


def get_reviewers() -> list[Any]:
    """Return all registered Reviewer instances."""
    return list(_cached(ENTRY_POINT_GROUPS["reviewers"]))


def get_adapters() -> list[Any]:
    """Return all registered Adapter instances."""
    return list(_cached(ENTRY_POINT_GROUPS["adapters"]))


def get_providers() -> list[Any]:
    """Return all registered Provider instances."""
    return list(_cached(ENTRY_POINT_GROUPS["providers"]))


def get_packs() -> list[Any]:
    """Return all registered Pack instances."""
    return list(_cached(ENTRY_POINT_GROUPS["packs"]))


def get_llm_providers() -> list[Any]:
    """Return all registered LLMProvider instances."""
    return list(_cached(ENTRY_POINT_GROUPS["llm_providers"]))


def get_topic_sources() -> list[Any]:
    """Return all registered TopicSource instances."""
    return list(_cached(ENTRY_POINT_GROUPS["topic_sources"]))


def get_image_providers() -> list[Any]:
    """Return all registered ImageProvider instances."""
    return list(_cached(ENTRY_POINT_GROUPS["image_providers"]))


# ---------------------------------------------------------------------------
# Core sample plugins — registered imperatively as a workaround for this
# project's poetry packaging config (see pyproject.toml note). Third-party
# community plugins use entry_points as documented; core samples are
# imported directly until the packaging issue is resolved.
# ---------------------------------------------------------------------------


def get_core_samples() -> dict[str, list[Any]]:
    """Discover sample plugins shipped under ``plugins.samples.*``.

    Imports each sample module + instantiates its plugin class. Returns
    a dict keyed by plugin type (``"taps"`` / ``"probes"`` / ``"jobs"`` /
    etc.) so callers that want to merge core samples with entry_point-
    discovered third-party plugins can do so cleanly.

    Import failures are logged + skipped per the same policy as
    ``_load_group()``.
    """
    samples: dict[str, list[Any]] = {k: [] for k in ENTRY_POINT_GROUPS}

    _SAMPLES: list[tuple[str, str, str]] = [
        # (plugin_type, module_path, class_name)
        ("taps", "plugins.samples.hello_tap", "HelloTap"),
        ("probes", "plugins.samples.database_probe", "DatabaseProbe"),
        ("jobs", "plugins.samples.noop_job", "NoopJob"),
        # Core Taps — same imperative load path as samples. Keeps them
        # discoverable in-container without relying on a `pip install .`
        # of poindexter-backend itself (tracked as packaging follow-up).
        ("taps", "services.taps.memory", "MemoryFilesTap"),
        ("taps", "services.taps.published_posts", "PostsTap"),
        ("taps", "services.taps.audit", "AuditTap"),
        ("taps", "services.taps.brain_knowledge", "BrainKnowledgeTap"),
        ("taps", "services.taps.brain_decisions", "BrainDecisionsTap"),
        ("taps", "services.taps.gitea_issues", "GiteaIssuesTap"),
        ("taps", "services.taps.claude_code_sessions", "ClaudeCodeSessionsTap"),
        # Core Jobs — apscheduler-driven housekeeping. Ship as imperative
        # loads until the poetry packaging issue is resolved.
        ("jobs", "services.jobs.sync_page_views", "SyncPageViewsJob"),
        ("jobs", "services.jobs.expire_stale_approvals", "ExpireStaleApprovalsJob"),
        ("jobs", "services.jobs.db_backup", "DbBackupJob"),
        ("jobs", "services.jobs.render_prometheus_rules", "RenderPrometheusRulesJob"),
        ("jobs", "services.jobs.postgres_vacuum", "PostgresVacuumJob"),
        ("jobs", "services.jobs.check_published_links", "CheckPublishedLinksJob"),
        ("jobs", "services.jobs.flag_missing_seo", "FlagMissingSeoJob"),
        ("jobs", "services.jobs.detect_duplicate_posts", "DetectDuplicatePostsJob"),
        ("jobs", "services.jobs.audit_published_quality", "AuditPublishedQualityJob"),
        ("jobs", "services.jobs.fix_broken_internal_links", "FixBrokenInternalLinksJob"),
        ("jobs", "services.jobs.fix_broken_external_links", "FixBrokenExternalLinksJob"),
        # Core TopicSources — Phase F migration. HackerNews + Dev.to first;
        # pgvector-knowledge / codebase-scan / web-search migrate later.
        ("topic_sources", "services.topic_sources.hackernews", "HackerNewsSource"),
        ("topic_sources", "services.topic_sources.devto", "DevtoSource"),
        ("topic_sources", "services.topic_sources.web_search", "WebSearchSource"),
        ("topic_sources", "services.topic_sources.knowledge", "KnowledgeSource"),
        ("topic_sources", "services.topic_sources.codebase", "CodebaseSource"),
        # Core ImageProviders — Phase G migration. Pexels first (search);
        # SDXL generation provider lands in a follow-up slice.
        ("image_providers", "services.image_providers.pexels", "PexelsProvider"),
        # Core LLM providers.
        ("llm_providers", "services.llm_providers.ollama_native", "OllamaNativeProvider"),
        ("llm_providers", "services.llm_providers.openai_compat", "OpenAICompatProvider"),
        # Core Stages (Phase E migration — one per file, unblocks tearing
        # down content_router_service.py over a handful of commits).
        ("stages", "services.stages.verify_task", "VerifyTaskStage"),
        ("stages", "services.stages.generate_content", "GenerateContentStage"),
        ("stages", "services.stages.writer_self_review", "WriterSelfReviewStage"),
        ("stages", "services.stages.quality_evaluation", "QualityEvaluationStage"),
        ("stages", "services.stages.url_validation", "UrlValidationStage"),
        ("stages", "services.stages.replace_inline_images", "ReplaceInlineImagesStage"),
        ("stages", "services.stages.source_featured_image", "SourceFeaturedImageStage"),
        ("stages", "services.stages.generate_seo_metadata", "GenerateSeoMetadataStage"),
        ("stages", "services.stages.generate_media_scripts", "GenerateMediaScriptsStage"),
        ("stages", "services.stages.capture_training_data", "CaptureTrainingDataStage"),
        ("stages", "services.stages.finalize_task", "FinalizeTaskStage"),
        ("stages", "services.stages.cross_model_qa", "CrossModelQAStage"),
    ]

    for plugin_type, module_path, class_name in _SAMPLES:
        try:
            import importlib
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            samples[plugin_type].append(cls())
        except Exception as e:
            logger.error(
                "core sample load failed: %s.%s: %s",
                module_path, class_name, e,
            )

    return samples


def clear_registry_cache() -> None:
    """Invalidate the discovery cache.

    Useful in tests, after a ``pip install`` during a running session,
    or when a new plugin has been dynamically loaded. Production code
    should rarely need this — the cache lives for the process lifetime
    and container restarts pick up newly-installed plugins.
    """
    _cached.cache_clear()
