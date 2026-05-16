"""Plugin registry — discover plugins via setuptools entry_points.

No custom registry, no decorators, no pkgutil auto-imports. We use
``importlib.metadata.entry_points()`` — the same mechanism pytest,
click, and flask use.

As of 2026-05-13, **20 entry-point groups** are wired here. 19 are
capability plugins (Tap, Stage, Reviewer, Adapter, Provider, …) and
the 20th is ``modules`` — the Module v1 group (Glad-Labs/poindexter#490),
the unit of business-function composition. See ``plugins/module.py``
and ``docs/architecture/module-v1.md`` for the Module Protocol; this
file's ``get_modules()`` accessor is the registry side.

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
import re
from collections.abc import Iterable
from functools import cache
from importlib.metadata import EntryPoint, entry_points
from typing import Any

logger = logging.getLogger(__name__)


_MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
"""Module names must be a lowercase slug — used as a Grafana folder
name, MCP namespace prefix, DB-migration prefix, and HTTP route
prefix. Same constraints as Python package names but stricter
(no uppercase, no dashes).
"""


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
    "audio_gen_providers": "poindexter.audio_gen_providers",
    "video_providers": "poindexter.video_providers",
    "tts_providers": "poindexter.tts_providers",
    "caption_providers": "poindexter.caption_providers",
    "publish_adapters": "poindexter.publish_adapters",
    "media_compositors": "poindexter.media_compositors",
    # Module v1 (Glad-Labs/poindexter#490) — bundles the lower-level
    # plugin contributions into installable, versioned business
    # functions. See docs/architecture/module-v1.md.
    "modules": "poindexter.modules",
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
            logger.exception(
                "plugin discovery: failed to load %s entry_point %r: %s",
                group, ep.name, e,
            )
            continue
        try:
            instance = target() if callable(target) else target
        except Exception as e:
            logger.exception(
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


def _merge_with_core_samples(group_key: str, ep_group: str) -> list[Any]:
    """Merge entry_point-discovered plugins with imperatively-loaded
    core samples.

    Background: this project's poetry packaging doesn't (yet) install
    the backend as an editable distribution inside the worker image, so
    the entry_points the pyproject declares are not actually visible to
    ``importlib.metadata.entry_points()`` at runtime. The fallback is
    the imperative ``get_core_samples()`` list. Without merging here,
    every consumer that calls a typed getter (``get_llm_providers()``,
    ``get_topic_sources()``, etc.) silently sees an empty list — which
    is how niche topic discovery has been quietly broken.

    De-dup is by ``.name`` attribute: an entry_point provider with the
    same name as a core sample wins, so third-party overrides still
    take precedence when packaging is fixed.
    """
    ep_instances = list(_cached(ep_group))
    samples = get_core_samples().get(group_key, [])
    by_name: dict[Any, Any] = {}
    for inst in samples:
        by_name[getattr(inst, "name", id(inst))] = inst
    for inst in ep_instances:
        by_name[getattr(inst, "name", id(inst))] = inst
    return list(by_name.values())


def get_taps() -> list[Any]:
    """Return all registered Tap instances."""
    return _merge_with_core_samples("taps", ENTRY_POINT_GROUPS["taps"])


def get_probes() -> list[Any]:
    """Return all registered Probe instances."""
    return _merge_with_core_samples("probes", ENTRY_POINT_GROUPS["probes"])


def get_jobs() -> list[Any]:
    """Return all registered Job instances."""
    return _merge_with_core_samples("jobs", ENTRY_POINT_GROUPS["jobs"])


def get_stages() -> list[Any]:
    """Return all registered Stage instances (excluding specializations)."""
    return _merge_with_core_samples("stages", ENTRY_POINT_GROUPS["stages"])


def get_reviewers() -> list[Any]:
    """Return all registered Reviewer instances."""
    return _merge_with_core_samples("reviewers", ENTRY_POINT_GROUPS["reviewers"])


def get_adapters() -> list[Any]:
    """Return all registered Adapter instances."""
    return _merge_with_core_samples("adapters", ENTRY_POINT_GROUPS["adapters"])


def get_providers() -> list[Any]:
    """Return all registered Provider instances."""
    return _merge_with_core_samples("providers", ENTRY_POINT_GROUPS["providers"])


def get_packs() -> list[Any]:
    """Return all registered Pack instances."""
    return _merge_with_core_samples("packs", ENTRY_POINT_GROUPS["packs"])


def get_llm_providers() -> list[Any]:
    """Return every registered LLMProvider — entry_points + core samples.

    Merges entry_points-discovered plugins with the imperatively-loaded
    core samples (``ollama_native``, ``openai_compat``, ``litellm``,
    etc.). Without the merge this returned ``[]`` in every development
    checkout because ``poetry install`` is never run on the package
    itself, leaving ``importlib.metadata.entry_points`` empty — which
    silently broke embedding writes, niche topic discovery, cross-model
    QA, and image semantic queries.

    On name conflict the entry_points instance wins, so installed
    plugin distributions still override the in-tree default.
    """
    return _merge_with_core_samples("llm_providers", ENTRY_POINT_GROUPS["llm_providers"])


def get_all_llm_providers() -> list[Any]:
    """Backward-compat alias for :func:`get_llm_providers`.

    Predates the registry-merge fix (poindexter#220) — when
    ``get_llm_providers`` returned entry_points-only and silently
    produced empty lists, this function was added as the merged view
    that production should use. The merge now lives inside
    ``get_llm_providers`` itself, so the two return the same list.
    Kept as an alias so any test or third-party code calling this
    name keeps working.
    """
    return get_llm_providers()


def get_topic_sources() -> list[Any]:
    """Return all registered TopicSource instances."""
    return _merge_with_core_samples("topic_sources", ENTRY_POINT_GROUPS["topic_sources"])


def get_image_providers() -> list[Any]:
    """Return all registered ImageProvider instances."""
    return _merge_with_core_samples("image_providers", ENTRY_POINT_GROUPS["image_providers"])


def get_audio_gen_providers() -> list[Any]:
    """Return all registered AudioGenProvider instances."""
    return _merge_with_core_samples(
        "audio_gen_providers", ENTRY_POINT_GROUPS["audio_gen_providers"],
    )


def get_video_providers() -> list[Any]:
    """Return all registered VideoProvider instances."""
    return _merge_with_core_samples("video_providers", ENTRY_POINT_GROUPS["video_providers"])


def get_tts_providers() -> list[Any]:
    """Return all registered TTSProvider instances."""
    return _merge_with_core_samples("tts_providers", ENTRY_POINT_GROUPS["tts_providers"])


def get_caption_providers() -> list[Any]:
    """Return all registered CaptionProvider instances."""
    return _merge_with_core_samples(
        "caption_providers", ENTRY_POINT_GROUPS["caption_providers"],
    )


def get_publish_adapters() -> list[Any]:
    """Return all registered PublishAdapter instances."""
    return _merge_with_core_samples(
        "publish_adapters", ENTRY_POINT_GROUPS["publish_adapters"],
    )


def get_media_compositors() -> list[Any]:
    """Return all registered MediaCompositor instances."""
    return _merge_with_core_samples(
        "media_compositors", ENTRY_POINT_GROUPS["media_compositors"],
    )


def _validate_modules(modules: list[Any]) -> list[Any]:
    """Drop modules whose manifest is malformed. Logs a warning per
    drop so a typo in a downstream package's manifest doesn't silently
    disappear from ``poindexter modules list``.

    Validation rules (Phase 1):
    - ``manifest()`` callable + returns a ``ModuleManifest``
    - ``manifest().name`` matches ``_MODULE_NAME_RE``
    - No two surviving modules share a name (first-discovered wins;
      collisions log a warning)
    """
    from plugins.module import ModuleManifest

    valid: list[Any] = []
    seen_names: set[str] = set()
    for mod in modules:
        manifest_fn = getattr(mod, "manifest", None)
        if not callable(manifest_fn):
            logger.warning(
                "plugins.registry: dropping module %r — no callable "
                "manifest() method",
                mod,
            )
            continue
        try:
            m = manifest_fn()
        except Exception as exc:
            logger.warning(
                "plugins.registry: dropping module %r — manifest() "
                "raised %s: %s",
                mod, type(exc).__name__, exc,
            )
            continue
        if not isinstance(m, ModuleManifest):
            logger.warning(
                "plugins.registry: dropping module %r — manifest() "
                "returned %s, expected ModuleManifest",
                mod, type(m).__name__,
            )
            continue
        if not _MODULE_NAME_RE.match(m.name):
            logger.warning(
                "plugins.registry: dropping module %r — manifest name "
                "%r does not match %s",
                mod, m.name, _MODULE_NAME_RE.pattern,
            )
            continue
        if m.name in seen_names:
            logger.warning(
                "plugins.registry: dropping duplicate module %r — "
                "name %r already registered (first-discovered wins)",
                mod, m.name,
            )
            continue
        seen_names.add(m.name)
        valid.append(mod)
    return valid


def get_modules() -> list[Any]:
    """Return all ``Module`` instances discovered via the
    ``poindexter.modules`` entry-point group, filtered to those with
    valid manifests.

    Merges entry_points-discovered modules with core-sample modules
    (registered imperatively in ``get_core_samples()``). The merge
    matches the pattern every other ``get_*`` accessor uses — see
    ``_merge_with_core_samples``. Without the merge, in-tree modules
    that haven't gone through ``pip install`` are invisible to the
    boot loader.

    Validation drops modules whose manifest is malformed; see
    ``_validate_modules`` for the rules.

    Results are cached for the process lifetime. Call
    ``clear_registry_cache`` after a ``pip install`` if you need to
    re-discover.

    See ``docs/architecture/module-v1.md``.
    """
    return _validate_modules(
        _merge_with_core_samples("modules", ENTRY_POINT_GROUPS["modules"])
    )


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
        # Module v1 Phase 3-lite — ContentModule is the first concrete
        # business module. Lives in-tree at cofounder_agent.modules.content
        # while we prove the shape; extracts to its own top-level package
        # when 2+ modules give us a comparison point (see Phase 3.5).
        ("modules", "modules.content", "ContentModule"),
        # FinanceModule F1 (2026-05-13) — Mercury read-only banking
        # integration. visibility=private (Matt's operator overlay).
        ("modules", "modules.finance", "FinanceModule"),
        # FinanceModule F2 polling job — pulls accounts + transactions
        # from Mercury hourly. Gated by mercury_enabled in app_settings.
        ("jobs", "modules.finance.jobs.poll_mercury", "PollMercuryJob"),
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
        # GiteaIssuesTap retired 2026-05-08 — Gitea was decommissioned
        # 2026-04-30; the corresponding settings.taps.gitea_issues row
        # is harmless and is left in app_settings for historical reference.
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
        ("jobs", "services.jobs.fix_uncategorized_posts", "FixUncategorizedPostsJob"),
        ("jobs", "services.jobs.tune_publish_threshold", "TunePublishThresholdJob"),
        ("jobs", "services.jobs.verify_published_posts", "VerifyPublishedPostsJob"),
        # Static-export reconciliation — 15-min DB ↔ R2 drift watchdog. The
        # public site reads R2 static/posts/index.json as source of truth;
        # publish_service used to fire export_post as a fire-and-forget asyncio
        # task that died silently when cancelled (Prefect teardown / worker
        # restart), freezing the bucket for days. This job rebuilds the index
        # whenever count or latest-published-at drift between DB and R2.
        (
            "jobs",
            "services.jobs.static_export_reconciliation",
            "StaticExportReconciliationJob",
        ),
        # Media-generation reconciliation — sibling watchdog for podcast +
        # video MP3/MP4 assets on R2. Catches the same fire-and-forget
        # anti-pattern that froze the static index, but for media files:
        # the 2026-04-29 → 2026-05-11 silent media outage motivated this
        # job. Self-heals by regenerating missing files, capped per cycle
        # so the GPU/disk don't pile up under backlog.
        (
            "jobs",
            "services.jobs.media_reconciliation",
            "MediaReconciliationJob",
        ),
        ("jobs", "services.jobs.crosspost_to_devto", "CrosspostToDevtoJob"),
        ("jobs", "services.jobs.update_utility_rates", "UpdateUtilityRatesJob"),
        ("jobs", "services.jobs.auto_embed_posts", "AutoEmbedPostsJob"),
        ("jobs", "services.jobs.rollup_post_performance", "RollupPostPerformanceJob"),
        # One-shot backfill — patches google_* columns on existing
        # post_performance snapshots from external_metrics. Runs every
        # 30d as a maintenance pass; the rollup job above keeps new
        # rows accurate. Glad-Labs/poindexter#27.
        (
            "jobs",
            "services.jobs.backfill_post_performance_gsc",
            "BackfillPostPerformanceGscJob",
        ),
        # ReloadSiteConfigJob — every-minute refresh of the in-memory
        # site_config cache so SQL/UI edits to app_settings take effect
        # without a container restart (gitea#280).
        ("jobs", "services.jobs.reload_site_config", "ReloadSiteConfigJob"),
        ("jobs", "services.jobs.analyze_topic_gaps", "AnalyzeTopicGapsJob"),
        ("jobs", "services.jobs.sync_newsletter_subscribers", "SyncNewsletterSubscribersJob"),
        # Niche topic-discovery sweep — calls TopicBatchService.run_sweep
        # per active niche on a 30-min cadence. Per-niche cadence floor
        # (niches.discovery_cadence_minute_floor) gates the actual work.
        # Layer 1 of the topic-UX rollout (niche pivot).
        ("jobs", "services.jobs.run_niche_topic_sweep", "RunNicheTopicSweepJob"),
        # Daily dev_diary auto-post (PR #160). Cron 0 13 * * * UTC = 9am EDT.
        # The pyproject.toml entry-point is also registered but isn't read
        # at runtime per the imperative-load pattern this list enforces.
        ("jobs", "services.jobs.run_dev_diary_post", "RunDevDiaryPostJob"),
        # Daily morning brief (cron 0 7 * * * — local container time).
        # Posts a consolidated 24h digest to Discord ops and only pings
        # Telegram when overnight criticals appear, so the operator wakes
        # up to one summary instead of 50+ individual Captain Hook pings.
        ("jobs", "services.jobs.morning_brief", "MorningBriefJob"),
        # Topic auto-resolve (every 2h). Closes the gap when the operator
        # is not running ``poindexter topics rank-batch / resolve-batch``
        # manually. Scans open topic_batches, applies LLM-rank as
        # operator-rank, promotes the rank-1 candidate to a
        # canonical_blog content_task. Master switch is
        # ``topic_auto_resolve_enabled`` (default false). See
        # services/jobs/topic_auto_resolve.py docstring for rails.
        ("jobs", "services.jobs.topic_auto_resolve", "TopicAutoResolveJob"),
        # Bridge: ``audit_log`` findings -> ``alert_events`` so the brain's
        # existing alert_dispatcher (with its dedup matrix) actually pages
        # operators on severity>=warn findings. Closes the "audit_log row
        # IS the finding" silent-route gap noted in utils/findings.py's
        # docstring. Captured 2026-05-15: 108 critical findings written
        # to audit_log in 7 days, zero reached the operator. Runs every
        # 60s; uses ``app_settings.findings_alert_route_watermark`` to
        # track progress.
        ("jobs", "services.jobs.findings_alert_router", "FindingsAlertRouterJob"),
        # Integrations runners — wrap tap_runner / retention_runner as
        # scheduled Jobs. Pre-2026-05-09 these only ran via the
        # poindexter CLI, so external_taps + retention_policies had been
        # dark since 2026-05-01. RunTapsJob fires hourly (matches the
        # hackernews tap's "every 1 hour" floor); RunRetentionJob fires
        # every 6 hours (retention is a sweep-everything operation).
        ("jobs", "services.jobs.run_taps", "RunTapsJob"),
        ("jobs", "services.jobs.run_retention", "RunRetentionJob"),
        # Memory + embedding hygiene — registered 2026-05-09 after the
        # deletion-candidates audit found these had pyproject.toml
        # entry_points but were missing from this in-process discovery
        # path. All 4 are idempotent sweeps with valid schedule attrs.
        # CheckMemoryStaleness alerts when a pgvector writer goes silent
        # (every 30m). PruneOrphan/PruneStale handle embedding cleanup
        # (cron 03:23 / 03:17). RegenerateStockImages replaces Pexels
        # stock with SDXL on already-published posts (every 6h, GPU-cap).
        ("jobs", "services.jobs.check_memory_staleness", "CheckMemoryStalenessJob"),
        ("jobs", "services.jobs.prune_orphan_embeddings", "PruneOrphanEmbeddingsJob"),
        ("jobs", "services.jobs.prune_stale_embeddings", "PruneStaleEmbeddingsJob"),
        ("jobs", "services.jobs.regenerate_stock_images", "RegenerateStockImagesJob"),
        # Anomaly detection — z-score outlier detection across failure
        # rate, quality, cost, and error-log rate (every 4h). Emits a
        # finding via utils.findings (routes through notify_operator
        # to Discord + Telegram) when 2+ metrics breach 2-sigma. The
        # docstring's "files a Gitea issue" was stale — actual code
        # uses emit_finding (post-Gitea-retirement path). Doc updated
        # 2026-05-09.
        ("jobs", "services.jobs.detect_anomalies", "DetectAnomaliesJob"),
        # Core TopicSources — Phase F migration. HackerNews + Dev.to first;
        # pgvector-knowledge / codebase-scan / web-search migrate later.
        ("topic_sources", "services.topic_sources.hackernews", "HackerNewsSource"),
        ("topic_sources", "services.topic_sources.devto", "DevtoSource"),
        ("topic_sources", "services.topic_sources.web_search", "WebSearchSource"),
        ("topic_sources", "services.topic_sources.knowledge", "KnowledgeSource"),
        ("topic_sources", "services.topic_sources.codebase", "CodebaseSource"),
        # Dev_diary topic source — pulls 24h of PRs/commits/decisions for
        # the daily build-in-public post (PR #160).
        ("topic_sources", "services.topic_sources.dev_diary_source", "DevDiarySource"),
        # Core ImageProviders — Phase G migration. Pexels first (search);
        # SDXL generation provider lands in a follow-up slice.
        ("image_providers", "services.image_providers.pexels", "PexelsProvider"),
        ("image_providers", "services.image_providers.sdxl", "SdxlProvider"),
        ("image_providers", "services.image_providers.ai_generation", "AIGenerationProvider"),
        # FLUX.1-schnell — second-generation text-to-image alternative to
        # SDXL Lightning. Apache-2.0 licensed (the non-commercial flux_dev
        # variant is intentionally NOT registered). GH#123.
        ("image_providers", "services.image_providers.flux_schnell", "FluxSchnellProvider"),
        # Core VideoProviders. Imperative load until the packaging issue
        # (entry_points discovery in Docker) is resolved — same pattern
        # as the image_providers above.
        ("video_providers", "services.video_providers.wan2_1", "Wan21Provider"),
        ("video_providers", "services.video_providers.ken_burns_slideshow", "KenBurnsSlideshowProvider"),
        # Core AudioGenProviders. Stable Audio Open 1.0 — text-to-music/SFX
        # via dedicated inference server (Stability AI Community license,
        # free <$1M ARR). GH-Glad-Labs/poindexter#125.
        (
            "audio_gen_providers",
            "services.audio_gen_providers.stable_audio_open",
            "StableAudioOpenProvider",
        ),
        # Core LLM providers.
        ("llm_providers", "services.llm_providers.ollama_native", "OllamaNativeProvider"),
        ("llm_providers", "services.llm_providers.openai_compat", "OpenAICompatProvider"),
        ("llm_providers", "services.llm_providers.litellm_provider", "LiteLLMProvider"),
        # Plugin-namespaced LLM providers (paid-vendor SDKs, opt-in via
        # ``app_settings.plugin.llm_provider.<name>.enabled``). Each ships
        # disabled by default so the core install stays free + self-hostable.
        ("llm_providers", "plugins.llm_providers.gemini", "GeminiProvider"),
        # Core Stages (Phase E migration — one per file, unblocks tearing
        # down content_router_service.py over a handful of commits).
        ("stages", "services.stages.verify_task", "VerifyTaskStage"),
        ("stages", "services.stages.generate_content", "GenerateContentStage"),
        ("stages", "services.stages.writer_self_review", "WriterSelfReviewStage"),
        # Resolve ``[posts/<slug>]`` placeholders before validation. The
        # writer LLM emits these as hints to internal posts but NO code
        # ever resolved them; the validator (added 2026-05-12) catches
        # them as ``critical`` and vetoes the multi_model_qa gate. ~95%
        # canonical_blog rejection rate traced to this leak on 2026-05-15.
        # Stage looks slugs/ids up in ``posts`` and rewrites to real
        # markdown links, or strips unknown placeholders.
        (
            "stages",
            "services.stages.resolve_internal_link_placeholders",
            "ResolveInternalLinkPlaceholdersStage",
        ),
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
            logger.exception(
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
