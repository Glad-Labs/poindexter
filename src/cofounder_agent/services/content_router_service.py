"""Unified Content Router Service — LangGraph TemplateRunner dispatcher.

What this file is
-----------------

The single public entry point :func:`process_content_generation_task` is
called by the Prefect flow (``services/flows/content_generation.py``) to
run one ``pipeline_tasks`` row through the content pipeline. It is a
thin dispatcher: it builds the shared pipeline context dict (image
service, settings, style tracker, site_config, models_by_phase,
experiment assignment) and hands it to
:class:`services.template_runner.TemplateRunner` keyed on the task's
``template_slug`` column.

History (why the file is now this small)
----------------------------------------

Until 2026-05-16 this module ALSO contained the legacy chunked
``StageRunner.run_all`` orchestration — five sequential calls to
``_runner.run_all([...])`` that drove the 12-stage pipeline in-process.
That path was the production default until the Lane C cutover
(``Glad-Labs/poindexter#355`` / ``#450``) shipped the
``canonical_blog`` LangGraph template and prod flipped
``app_settings.default_template_slug='canonical_blog'`` on 2026-05-10.

After 7+ clean days on TemplateRunner with zero ``template_slug IS
NULL`` tasks rolling through, the legacy chunked block was deleted in
the cleanup sweep (Lane C Stage 4, 2026-05-16). What remains is the
shared-context construction + the TemplateRunner dispatch + the
post-run experiment outcome attribution. The 12-stage flow itself
lives in ``services/pipeline_templates/__init__.py:_CANONICAL_BLOG_ORDER``;
new stages go there, NOT here.

Dependencies
------------

Reads:
    - ``services.container.get_service("settings")`` — DI seam, may be None outside lifespan
    - ``services.image_style_rotation.ImageStyleTracker``
    - ``services.image_service.get_image_service``
    - ``services.site_config.site_config`` (per-module SiteConfig attr)
    - ``services.pipeline_experiment_hook`` (best-effort)
    - ``services.template_runner.TemplateRunner``
    - ``pipeline_tasks.template_slug`` (per-row, set at task creation)

Writes (via TemplateRunner → stages):
    - ``content_tasks`` (status, error_message, task_metadata) via the
      ``finalize_task`` stage and the failure branch below
    - ``audit_log`` — ``task_started`` here, plus per-stage events
      emitted from inside TemplateRunner / the stages themselves
    - ``webhook_events`` indirectly via ``emit_webhook_event`` on the
      failure path

Failure modes
-------------

- **Missing ``template_slug`` on the task row** — per
  ``feedback_no_silent_defaults`` we fail loud rather than running an
  implicit legacy path. ``tasks_db.add_task`` consults
  ``app_settings.default_template_slug`` at task creation, so a NULL
  here means either the setting was empty when the task was queued
  (stale config) or the row was inserted by a foreign writer that
  bypassed ``tasks_db``. Both deserve operator attention; we mark the
  task ``failed`` with a diagnostic ``error_message`` and return.
- **TemplateRunner raises** — caught, task marked ``failed``, partial
  context preserved in ``task_metadata`` so the operator can review
  whatever generated before the crash.

See also
--------

- ``services/template_runner.py`` — the LangGraph engine
- ``services/pipeline_templates/__init__.py`` — where new stages go
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from services.logger_config import get_logger
from services.site_config import SiteConfig

from .audit_log import audit_log_bg
from .database_service import DatabaseService
from .image_service import get_image_service
from .webhook_delivery_service import emit_webhook_event

# SiteConfig is now injected exclusively (#272 Phase-2f). The
# module-level ``site_config`` global + ``set_site_config`` setter were
# deleted; ``process_content_generation_task`` requires a ``site_config=``
# kwarg. The Prefect flow (``services/flows/content_generation.py``)
# threads the subprocess-wired instance from
# ``build_and_wire_subprocess_with_container``.

logger = get_logger(__name__)


async def _record_experiment_outcome(
    *,
    result: dict[str, Any],
    task_id: Any,
    database_service: Any,
    site_config: Any,
    ok: bool,
) -> None:
    """Attribute a completed pipeline run to its experiment-assignment row
    (Glad-Labs/poindexter#27; no-op when no experiment is active).

    Best-effort — **never raises**, so it can't poison a successful run — but a
    failure is surfaced as a non-paging ``info`` finding so a persistent break
    doesn't silently starve the A/B Lab's ``(variant -> outcome)`` data
    (router-learning class, batches 10-12). Lifted out of
    ``process_content_generation_task`` for testability.
    """
    try:
        from services.pipeline_experiment_hook import record_pipeline_outcome

        await record_pipeline_outcome(
            assignment=result.get("experiment_assignment") or {},
            task_id=task_id,
            database_service=database_service,
            site_config=site_config,
            metrics={
                "quality_score": float(result.get("quality_score") or 0.0),
                "qa_final_score": float(result.get("qa_final_score") or 0.0),
                "status": str(result.get("status", "unknown")),
                "model_used": str(result.get("model_used", "")),
                "outcome": "success" if ok else "halted",
            },
        )
    except Exception as _exc:  # noqa: BLE001 — attribution is best-effort; never poison the run
        logger.debug("[BG-TASK] experiment record_outcome failed: %s", _exc)
        from utils.findings import emit_finding

        emit_finding(
            source="services.content_router_service",
            kind="experiment_outcome_record_failed",
            title="Experiment outcome attribution failed",
            body=(
                f"record_pipeline_outcome raised {type(_exc).__name__}: {_exc} for "
                f"task {task_id}. The pipeline run itself succeeded, but its outcome "
                f"was not attributed to the experiment-assignment row — a persistent "
                f"failure silently starves the A/B Lab's (variant -> outcome) data."
            ),
            severity="info",
            dedup_key=f"experiment_outcome_record_failed:{type(_exc).__name__}",
            extra={"error_type": type(_exc).__name__, "task_id": str(task_id)},
        )


async def _load_template_slug(database_service: DatabaseService, task_id: str) -> str | None:
    """Read ``pipeline_tasks.template_slug`` for ``task_id``.

    Returns the trimmed slug, or ``None`` if the row has no slug / the
    lookup fails. The caller treats ``None`` as a hard error — see the
    module docstring "Missing template_slug" note.
    """
    try:
        async with database_service.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT template_slug FROM pipeline_tasks WHERE task_id = $1",
                str(task_id),
            )
    except Exception as exc:
        logger.warning(
            "[BG-TASK] template_slug lookup failed for task %s: %s",
            task_id, exc,
        )
        return None
    # Tight isinstance check — test fixtures bind ``db.pool`` as a
    # MagicMock that auto-generates AsyncMocks for attribute access,
    # so ``fetchval`` can return a truthy AsyncMock object rather than
    # a string. Without the isinstance gate, a non-string slug flows
    # into TemplateRunner.run and KeyErrors out.
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


async def _load_niche_slug(
    database_service: DatabaseService, task_id: str,
) -> str | None:
    """Read ``pipeline_tasks.niche_slug`` for ``task_id``.

    Phase 0 lab observability seam (2026-05-28). The niche_slug is the
    durable routing seam for the lab — every learnings digest /
    bandit / variant experiment slices outcomes per niche. Seeding it
    on the context dict means every downstream stage + atom +
    capability_outcomes row gets it for free.

    Returns the trimmed slug, or ``None`` if the row has no niche /
    the lookup fails. None is the correct value for legacy / manual /
    dev_diary-infra tasks (not all pipeline_tasks rows carry a niche).
    """
    try:
        async with database_service.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT niche_slug FROM pipeline_tasks WHERE task_id = $1",
                str(task_id),
            )
    except Exception as exc:
        logger.warning(
            "[BG-TASK] niche_slug lookup failed for task %s: %s",
            task_id, exc,
        )
        return None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _graph_hydration_keys(
    graph_def: dict[str, Any] | None,
    get_meta: Callable[[str], Any],
) -> set[str]:
    """Keys the active graph must receive from initial state (``task_metadata``).

    A hydration key is a declared ``PipelineState`` channel that some atom
    *consumes* (via ``requires`` or a declared input) BEFORE any atom
    *produces* it — walking the nodes in topological order. Produced-then-
    consumed channels (``content`` / ``quality_score`` / ``seo_title`` / …) are
    NOT hydration keys; excluding them is what stops a ``canonical_blog``
    rejected→rewrite re-run — whose ``task_metadata`` carries the prior draft's
    body and scores — from leaking them onto the fresh run's initial state.

    Order-sensitive so an entry atom that both consumes and re-emits an
    optional input — e.g. ``image_rebuild``'s ``allow_stock`` (read at entry,
    normalised, re-produced) — is still recognised as needing to arrive from
    metadata. An order-insensitive ``requires - produces`` would silently drop
    it, ignoring the operator's opt-in.

    ``get_meta`` maps an atom name to its ``AtomMeta`` (``.requires`` /
    ``.produces`` / ``.inputs``); injected so the walk is unit-testable without
    the atom registry.
    """
    if not isinstance(graph_def, dict):
        return set()
    nodes = [
        n for n in (graph_def.get("nodes") or [])
        if isinstance(n, dict) and isinstance(n.get("id"), str)
    ]
    if not nodes:
        return set()
    node_by_id = {n["id"]: n for n in nodes}
    ids = set(node_by_id)

    # Topological order via Kahn's algorithm, mirroring pipeline_architect's
    # build-time walk: skip designated rescue back-edges (``loop``) so the
    # loopback target's indegree can still reach 0, and ignore edges to END.
    indeg = dict.fromkeys(ids, 0)
    adj: dict[str, list[str]] = {nid: [] for nid in ids}
    for e in graph_def.get("edges") or []:
        if not isinstance(e, dict) or e.get("loop"):
            continue
        src, dst = e.get("from"), e.get("to")
        if isinstance(src, str) and isinstance(dst, str) and dst != "END" and src in ids and dst in ids:
            adj[src].append(dst)
            indeg[dst] += 1
    ready = [nid for nid in ids if indeg[nid] == 0]
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    # Any nodes a cycle left out (a valid DAG has none): append them so their
    # contracts still count — conservative, never surfaces fewer keys.
    order.extend(nid for nid in ids if nid not in order)

    from services.template_runner import PipelineState  # lazy: import cycle

    declared = set(PipelineState.__annotations__)
    produced: set[str] = set()
    hydration: set[str] = set()
    for nid in order:
        meta = get_meta(node_by_id[nid].get("atom", ""))
        if meta is None:
            continue
        consumed = set(meta.requires) | {f.name for f in meta.inputs}
        hydration |= (consumed & declared) - produced
        produced |= set(meta.produces)
    return hydration


async def _load_task_metadata(
    database_service: DatabaseService,
    task_id: str,
    template_slug: str | None,
    *,
    graph_loader: Callable[..., Any] | None = None,
    get_meta: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Surface per-task hydration metadata for graph templates that start from
    an existing row instead of a topic (``seo_refresh`` / ``image_rebuild`` /
    future kin).

    Reads ``pipeline_versions.stage_data -> 'task_metadata'`` — where
    ``tasks_db.add_task`` persists a task's metadata at version 1 — and returns
    only the keys the *active graph* actually needs from initial state (see
    :func:`_graph_hydration_keys`, which derives them from the atoms'
    ``requires`` / ``produces`` contracts rather than a hardcoded per-template
    allowlist). Returns ``{}`` on miss, and for ``canonical_blog`` / ``dev_diary``
    (no external hydration inputs), so those paths are unaffected.

    ``graph_loader`` / ``get_meta`` are injected for testability; production
    resolves the real ``load_active_graph_def`` / ``get_atom_meta``.
    """
    try:
        async with database_service.pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT stage_data FROM pipeline_versions "
                "WHERE task_id = $1 ORDER BY version DESC LIMIT 1",
                str(task_id),
            )
    except Exception as exc:
        logger.warning(
            "[BG-TASK] task metadata lookup failed for %s: %s", task_id, exc,
        )
        return {}
    # asyncpg returns a JSONB column as a str unless a codec is registered;
    # tolerate both shapes.
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    data = raw if isinstance(raw, dict) else {}
    meta = data.get("task_metadata")
    if not isinstance(meta, dict) or not meta:
        return {}

    if graph_loader is None:
        from services.pipeline_templates import load_active_graph_def
        graph_loader = load_active_graph_def
    if get_meta is None:
        from services.atom_registry import get_atom_meta
        get_meta = get_atom_meta

    graph_def = await graph_loader(getattr(database_service, "pool", None), template_slug or "")
    keys = _graph_hydration_keys(graph_def, get_meta)
    # Narrow the candidate channels to those actually present in this task's
    # metadata. Injected-service / context channels (database_service,
    # image_service, …) are declared channels the router seeds directly, so
    # they appear in ``keys`` but never in ``meta`` — this filter drops them.
    return {k: meta[k] for k in keys if meta.get(k) not in (None, "")}


async def process_content_generation_task(
    topic: str,
    style: str,
    tone: str,
    target_length: int,
    tags: list[str] | None = None,
    generate_featured_image: bool = True,
    database_service: DatabaseService | None = None,
    task_id: str | None = None,
    models_by_phase: dict[str, str] | None = None,
    quality_preference: str | None = None,
    category: str | None = None,
    target_audience: str | None = None,
    *,
    site_config: SiteConfig,
    platform: Any = None,
) -> dict[str, Any]:
    """Dispatch one ``pipeline_tasks`` row through its LangGraph template.

    Builds the shared pipeline context (service handles + per-task
    inputs + experiment assignment) and hands it to
    :class:`services.template_runner.TemplateRunner` keyed on
    ``pipeline_tasks.template_slug``. The TemplateRunner drives the
    12-node ``canonical_blog`` graph (or whichever template the row
    declares) to completion; this function returns the final state
    dict.

    Per ``feedback_no_silent_defaults``: a row without a
    ``template_slug`` is a configuration bug, not a fallback case. We
    mark the task ``failed`` with a diagnostic message instead of
    silently running an undefined pipeline.
    """
    from uuid import uuid4

    # SiteConfig is injected (#272 Phase-2f — module global deleted).
    # Bind to ``_sc`` so the existing reads below stay unchanged.
    _sc = site_config

    if not task_id:
        task_id = str(uuid4())

    if not database_service:
        logger.error("DatabaseService not provided - cannot persist content")
        raise ValueError("DatabaseService is required for content_tasks persistence")

    logger.info("=" * 80)
    logger.info("CONTENT GENERATION PIPELINE")
    logger.info("=" * 80)
    logger.info("   Task ID: %s", task_id)
    logger.info("   Topic: %s", topic)
    logger.info("   Style: %s | Tone: %s", style, tone)
    logger.info("   Target Length: %s words", target_length)
    logger.info("   Tags: %s", ', '.join(tags) if tags else 'none')
    logger.info("   Image Search: %s", generate_featured_image)
    logger.info("=" * 80)

    # Build the shared pipeline context.
    #
    # TemplateRunner extracts service handles from this dict via
    # ``_KNOWN_SERVICE_KEYS`` ({database_service, image_service,
    # settings_service, image_style_tracker, site_config, platform}). Stages
    # read inputs (topic / style / tone / target_length / tags /
    # generate_featured_image / models_by_phase / category /
    # target_audience) and accumulate outputs (content, quality_result,
    # featured_image_url, seo_*, status, ...) on the same dict.
    #
    # Pull the lifespan-loaded SiteConfig and thread it into the
    # ImageService ctor so the Pexels secret lookup goes through the
    # canonical Phase H DI seam (poindexter#381).
    image_service = get_image_service(site_config=_sc)

    # Settings + style tracker — pulled from the container/app.state
    # during DI transition (#242). Falls back to fresh instances when
    # invoked outside the lifespan-wired context (tests, ad-hoc CLI).
    try:
        from services.container import get_service as _get_service
        _settings_service = _get_service("settings")
    except Exception:
        _settings_service = None
    if _settings_service is None:
        # The Prefect flow subprocess never runs main.py's lifespan, so the
        # module-level ServiceContainer has no "settings" registration and
        # get_service returns None (it doesn't raise). A None settings_service
        # silently disables every settings-gated QA leg downstream — qa.vision's
        # image-relevance check read qa_vision_check_enabled as false and passed
        # open on 100% of posts since the Prefect cutover. Build one from the
        # task's own pool instead.
        _pool = getattr(database_service, "pool", None)
        if _pool is not None:
            from services.settings_service import SettingsService as _SettingsService
            _settings_service = _SettingsService(_pool)
            logger.info(
                "[CONTENT_ROUTER] no 'settings' registration in the service "
                "container — built SettingsService from the database pool "
                "(Prefect subprocess path)",
            )
        else:
            logger.warning(
                "[CONTENT_ROUTER] settings_service unavailable and "
                "database_service has no .pool — settings-gated QA legs "
                "(qa.vision image relevance, preview screenshot) will "
                "read their enable flags as false this run",
            )
    from services.image_style_rotation import ImageStyleTracker as _IST
    _style_tracker = _IST(
        history_size=_sc.get_int("image_style_history_size", 10),
        ttl_seconds=_sc.get_int("image_style_history_ttl_seconds", 3600),
    )

    # Mutable copy — the experiment hook may set ``models_by_phase["writer"]``
    # below if an A/B experiment is active. Always seed the dict before
    # the hook runs so the merge is in-place + observable to downstream
    # stages.
    _models_by_phase: dict[str, str] = dict(models_by_phase or {})

    # Glad-Labs/poindexter#27: assign this task to a variant of the
    # active pipeline experiment (if any). Best-effort — failure
    # returns a no-op assignment and the pipeline runs with default
    # config. The assignment dict is threaded through so finalize can
    # ``record_outcome`` on the same row.
    try:
        from services.pipeline_experiment_hook import assign_pipeline_variant
        _experiment_assignment = await assign_pipeline_variant(
            task_id=task_id,
            database_service=database_service,
            site_config=_sc,
            models_by_phase=_models_by_phase,
        )
    except Exception as _exc:
        # assign_pipeline_variant is itself wrapped in try/except, but
        # if the import fails for some bizarre reason we still want
        # the pipeline to run.
        logger.debug("[BG-TASK] experiment hook unavailable: %s", _exc)
        _experiment_assignment = {"experiment_key": None, "variant_key": None}

    result: dict[str, Any] = {
        "task_id": task_id,
        "topic": topic,
        "status": "pending",
        "stages": {},
        # Seed media-artifact channels at graph construction time so LangGraph
        # registers them in channel_versions at step 0.  Without this, channels
        # absent from the initial state dict are silently dropped when a node
        # returns them (#674 variant) — even though they ARE declared in
        # PipelineState — because LangGraph 1.1.10 does not promote untracked
        # channels from node return values unless the channel already has a
        # registered version.  Seeding empty defaults here ensures that when
        # generate_media_scripts writes podcast_script/video_scenes/etc. the
        # updates propagate correctly to generate_video_shot_list and Stage-2
        # dispatch.  (Discovered 2026-06-08 via checkpoint_blobs audit.)
        "podcast_script": "",
        "video_scenes": [],
        "short_summary_script": "",
        "video_shot_list": {},
        "short_shot_list": {},
        "video_ambient_audio_path": "",
        "podcast_audio_path": "",
        "podcast_intro_audio_path": "",
        "category": category or "technology",
        # Orchestrator inputs — stages read these directly.
        "style": style,
        "tone": tone,
        "target_length": target_length,
        "tags": tags or [],
        "generate_featured_image": generate_featured_image,
        "database_service": database_service,
        "image_service": image_service,
        # Phase H DI seam — every stage can pull ``site_config`` from
        # context.get('site_config') and forward it into services that
        # need DB-backed settings or secrets (poindexter#381).
        "site_config": _sc,
        # Seam 1 Wave 3c (Glad-Labs/poindexter#667) — content's
        # capability-scoped kernel handle. Stages/atoms reach the kernel
        # through ``context.get('platform')`` (e.g. ``platform.audit.write_bg``)
        # instead of importing kernel internals. ``None`` when the caller
        # didn't build one (tests / ad-hoc CLI) — sites treat that as "skip
        # this best-effort telemetry," mirroring ``site_config``'s None-tolerant
        # seam.
        "platform": platform,
        "models_by_phase": _models_by_phase,
        "quality_preference": quality_preference,
        "target_audience": target_audience,
        # Shared services threaded via context (replaces singletons).
        "settings_service": _settings_service,
        "image_style_tracker": _style_tracker,
        # Experiment context — present for the duration of the run so
        # finalize can ``record_outcome`` on the same assignment row.
        "experiment_assignment": _experiment_assignment,
    }

    # Resolve the template slug for this task. Per Lane C cutover
    # (poindexter#355), ``tasks_db.add_task`` reads
    # ``app_settings.default_template_slug`` at task creation and stores
    # the resolved slug on the ``pipeline_tasks`` row. Reading it back
    # here gives us per-task pipeline selection (e.g. ``dev_diary`` cron
    # tasks pass their own slug; everything else gets the operator's
    # global default).
    template_slug = await _load_template_slug(database_service, task_id)

    # Phase 0 lab observability (2026-05-28). niche_slug is the durable
    # routing seam — stamped on the context dict so every downstream
    # stage / atom can read it, and ultimately propagated to
    # capability_outcomes.niche_slug via record_run's state-level
    # fallback. None for legacy / manual / dev_diary-infra tasks.
    niche_slug = await _load_niche_slug(database_service, task_id)
    if niche_slug:
        result["niche_slug"] = niche_slug

    # Graph templates that hydrate from an existing row (seo_refresh's post_id,
    # image_rebuild's target_task_id/allow_stock, …) carry their inputs in the
    # task's metadata rather than as topic/style args. Surface exactly the keys
    # the active graph needs from initial state (derived from atom contracts —
    # see _load_task_metadata) so the entry atom can read them. ``setdefault``
    # so metadata never clobbers a freshly-seeded context value; empty for
    # canonical_blog / dev_diary, so those paths are unaffected.
    for _k, _v in (
        await _load_task_metadata(database_service, task_id, template_slug)
    ).items():
        result.setdefault(_k, _v)

    # Per ``feedback_no_silent_defaults``: a missing slug is a config
    # error, not a fallback. The legacy chunked StageRunner flow was
    # deleted in the 2026-05-16 sweep (see module docstring); there is
    # no implicit pipeline to run. Mark the task failed with a
    # diagnostic so the operator notices the misconfiguration instead
    # of silently dropping the task on the floor.
    if not template_slug:
        msg = (
            f"pipeline_tasks.template_slug is NULL for task {task_id} — "
            "set app_settings.default_template_slug or pass template_slug "
            "at task creation. The legacy chunked StageRunner path was "
            "deleted 2026-05-16."
        )
        logger.error("[BG-TASK] %s", msg)
        audit_log_bg(
            "missing_template_slug", "content_router",
            {"task_id": task_id, "topic": topic[:100]},
            task_id=task_id, severity="error",
        )
        try:
            await database_service.update_task(
                task_id, {"status": "failed", "error_message": msg[:500]},
            )
        except Exception as _exc:
            logger.error("[BG-TASK] failed to mark task failed: %s", _exc)
        result["status"] = "failed"
        result["error"] = msg
        return result

    logger.info(
        "[BG-TASK] template_slug=%r — dispatching via TemplateRunner (LangGraph)",
        template_slug,
    )
    audit_log_bg(
        "task_started", "content_router",
        {"topic": topic[:100], "template_slug": template_slug},
        task_id=task_id,
    )

    try:
        from services.template_runner import TemplateRunner
        _tmpl_runner = TemplateRunner(database_service.pool, site_config=_sc)
        # Build the default progress-streaming callback per the
        # pipeline_streaming_channel setting (#361 part 2). Returns None for
        # discord/off (Discord is driven by _emit_progress; off = silent) and
        # a Telegram edit-streaming callback when opted in. Best-effort — a
        # callback-build failure must never block the run.
        _on_event = None
        try:
            from services.pipeline_streaming import make_streaming_callback
            _on_event = await make_streaming_callback(
                database_service.pool, _sc, str(task_id),
                template_slug=template_slug,
            )
        except Exception as _stream_exc:  # noqa: BLE001 — silent-ok: progress-streaming callback build is best-effort UX; a failure just means no live progress updates and the run continues normally
            logger.debug(
                "[BG-TASK] streaming callback build failed (%s) — continuing "
                "without on_event streaming", _stream_exc,
            )
        _tmpl_summary = await _tmpl_runner.run(
            template_slug, result, thread_id=str(task_id),
            on_event=_on_event,
        )
        # Mirror the stage-summary shape expected by callers — task
        # routes through ``finalize_task`` inside the template, which
        # already updates the row to ``awaiting_approval`` (or auto-
        # publishes when the score clears the gate).
        result.update(_tmpl_summary.final_state)
        audit_log_bg(
            "template_completed", "content_router",
            {
                "template": template_slug,
                "ok": _tmpl_summary.ok,
                "halted_at": _tmpl_summary.halted_at,
                "records": [r.name for r in _tmpl_summary.records],
            },
            task_id=task_id,
        )

        # Glad-Labs/poindexter#27: attribute pipeline outcome to the
        # experiment assignment row (no-op when no experiment active).
        # Best-effort — failure here must not poison a successful run.
        await _record_experiment_outcome(
            result=result,
            task_id=task_id,
            database_service=database_service,
            site_config=_sc,
            ok=_tmpl_summary.ok,
        )

        logger.info("=" * 80)
        logger.info("CONTENT GENERATION PIPELINE FINISHED")
        logger.info("=" * 80)
        logger.info("   Task ID: %s", task_id)
        logger.info("   Post ID: %s", result.get('post_id', 'NOT_YET_CREATED'))
        logger.info("   Status: %s", result.get('status', 'unknown'))
        logger.info("   Template: %s", template_slug)
        logger.info("=" * 80)
        return result

    except Exception as exc:
        # TemplateRunner raised. Log loud, preserve partial context in
        # task_metadata so the operator can still review what generated
        # before the crash, and emit a task.failed webhook so OpenClaw
        # / Discord notifies downstream consumers.
        logger.exception(
            "[BG-TASK] TemplateRunner raised for task %s template=%r: %s",
            task_id, template_slug, exc,
        )

        # Per poindexter#260: when pipeline_dry_run_mode is on, the
        # writer chain short-circuits with AllModelsFailedError ("no
        # attempts recorded") because dry-run intentionally suppresses
        # model calls. That's expected behaviour, NOT a real failure —
        # logging it as severity='error' was drowning the 24h error
        # count (277/277 in one window were dry-run noise) and hiding
        # actual ollama/db errors. Demote to severity='info' with a
        # filterable event_type so dashboards/alerts can ignore it.
        _is_dry_run_halt = False
        try:
            _dry_raw = _sc.get("pipeline_dry_run_mode", "")
            _is_dry_run = str(_dry_raw).strip().lower() in ("true", "1", "yes", "on")
            _err_text = str(exc)
            _is_dry_run_halt = _is_dry_run and (
                "no attempts recorded" in _err_text
                or "AllModelsFailedError" in _err_text
            )
        except Exception as _dry_exc:  # noqa: BLE001 — silent-ok: dry-run classification is fail-safe; on failure _is_dry_run_halt stays False so the error is treated as a real error (the conservative direction)
            logger.debug("[BG-TASK] dry-run severity-demote check failed: %s", _dry_exc)

        # poindexter#846: content.load_draft_for_image_rebuild raises this
        # exact marker when the target draft was approved/rejected/published
        # in the queue gap before the rebuild task claimed it — an expected,
        # non-actionable race (the operator already made the call), not a
        # bug. Same demotion shape as _is_dry_run_halt above, but this case
        # also gets a terminal status='cancelled' below (the task can never
        # succeed on retry — the target will never return to
        # awaiting_approval) instead of 'failed' (which reads as "needs
        # investigation" and would otherwise sit in the failed-tasks queue
        # forever for something no retry can fix).
        _is_image_rebuild_target_moved_on = "target draft moved on" in str(exc)

        if _is_image_rebuild_target_moved_on:
            audit_log_bg(
                "image_rebuild_target_moved_on", "content_router",
                {
                    "error": str(exc)[:500],
                    "reason": "target draft was approved/rejected/published before the rebuild task claimed it",
                },
                task_id=task_id, severity="info",
            )
        elif _is_dry_run_halt:
            audit_log_bg(
                "dry_run_halt", "content_router",
                {
                    "error": str(exc)[:500],
                    "stages_completed": list(result.get("stages", {}).keys()),
                    "reason": "pipeline_dry_run_mode short-circuited the writer chain",
                },
                task_id=task_id, severity="info",
            )
        else:
            audit_log_bg(
                "error", "content_router",
                {
                    "error": str(exc)[:500],
                    "stages_completed": list(result.get("stages", {}).keys()),
                    "template": template_slug,
                },
                task_id=task_id, severity="error",
            )

        # Preserve all partially-generated data (content, image,
        # metadata) so it's available for review/approval workflow.
        try:
            failure_metadata = {
                "content": result.get("content"),
                "featured_image_url": result.get("featured_image_url"),
                "featured_image_alt": result.get("featured_image_alt"),
                "featured_image_width": result.get("featured_image_width"),
                "featured_image_height": result.get("featured_image_height"),
                "featured_image_photographer": result.get("featured_image_photographer"),
                "featured_image_source": result.get("featured_image_source"),
                "seo_title": result.get("seo_title"),
                "seo_description": result.get("seo_description"),
                "seo_keywords": result.get("seo_keywords"),
                "topic": topic,
                "style": style,
                "tone": tone,
                "quality_score": result.get("quality_score"),
                "error_stage": str(exc)[:200],
                "error_message": str(exc),
                "stages_completed": result.get("stages", {}),
                "template_slug": template_slug,
            }
            failure_metadata = {k: v for k, v in failure_metadata.items() if v is not None}

            _final_status = "cancelled" if _is_image_rebuild_target_moved_on else "failed"
            await database_service.update_task(
                task_id=task_id,
                updates={
                    "status": _final_status,
                    "error_message": str(exc),
                    "task_metadata": failure_metadata,
                },
            )

            # A cancelled-due-to-race task isn't a failure — skip the
            # task.failed webhook so OpenClaw/Discord don't page on
            # something no retry could ever fix.
            if not _is_image_rebuild_target_moved_on:
                try:
                    await emit_webhook_event(database_service.pool, "task.failed", {
                        "task_id": task_id, "topic": topic, "error": str(exc)[:200],
                    })
                except Exception:
                    logger.warning(
                        "[WEBHOOK] Failed to emit task.failed event from pipeline",
                        exc_info=True,
                    )
        except Exception as db_error:
            logger.error(
                "[BG-TASK] Failed to update task status: %s", db_error, exc_info=True,
            )

        result["status"] = "cancelled" if _is_image_rebuild_target_moved_on else "failed"
        result["error"] = str(exc)
        return result
