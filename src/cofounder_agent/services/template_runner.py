"""TemplateRunner — LangGraph-based orchestration backbone (v1 POC).

The Phase 1 lift from the dynamic-pipeline-composition spec
(``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``).

What this is in v1 (POC):

- A thin wrapper around LangGraph's ``StateGraph.compile().ainvoke()``.
- Identity-adapter for existing Stage instances → LangGraph nodes (the
  12 canonical stages run unchanged when wrapped via :func:`make_stage_node`).
- Lookup of named template factories from
  ``services.pipeline_templates.TEMPLATES`` (stub for now; templates
  land in their own module).

What this is NOT in v1 (deferred to Phase 2-5):

- No atom granularity refactor — existing stages stay coarse.
- No capability-tier abstraction — model_router stays as-is.
- No streaming events to operator (silent multi-minute waits remain).
- No Postgres checkpointer (uses MemorySaver; durability is Phase 2).
- No LLM-architect composition (templates are hand-coded Python).
- No outcome-feedback loop on the router (Phase 2).

Coexists with the legacy :class:`plugins.stage_runner.StageRunner`. Tasks
opt into the LangGraph path by setting ``pipeline_tasks.template_slug``
(non-NULL); tasks without that field continue to flow through StageRunner.

Implements: Glad-Labs/poindexter#356.
"""

from __future__ import annotations

import asyncio
import logging
import operator
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Progress streaming — Phase 2 of the dynamic-pipeline-composition spec.
#
# Emit one notification per node start / completion / failure so the
# operator's Discord chat shows live progress instead of silent
# multi-minute waits.
#
# History: this used to also INSERT into ``pipeline_events`` for "future
# EventBus listeners + Grafana panels for replay/audit," but the 2026-05-04
# audit (poindexter#366) found no consumer ever materialized — Grafana
# panels read audit_log instead, no listener subscribed to NOTIFY,
# nothing replayed the rows. Phase 4 of the split-migration drops the
# write here; Langfuse traces are the future replacement when we wire
# full per-run tracing.
#
# Discord routing: gated by ``template_runner_progress_streaming``
# setting; default ON because Discord is meant to be noisy. NEVER
# routes to Telegram — Matt's phone is for critical alerts only
# (worker offline, GPU temp, cost overrun).
# ---------------------------------------------------------------------------


async def _emit_progress(
    pool: Any,
    *,
    event_type: str,
    payload: dict[str, Any],
    notify_operator_message: str | None = None,
) -> None:
    """Fan progress events out to Discord (when enabled).

    The ``pool`` and ``event_type`` / ``payload`` parameters are kept
    on the signature so the dozen call sites don't churn — they're now
    unused at this layer. A future Langfuse-tracing wire-up will read
    ``event_type`` + ``payload`` to populate span attributes.
    """
    del pool, event_type, payload  # see docstring — kept for source-compat
    if not notify_operator_message:
        return
    try:
        from services.integrations.operator_notify import notify_operator
        from services.site_config import site_config
        stream_on = bool(
            site_config.get_bool(
                "template_runner_progress_streaming", True,
            )
        )
        if stream_on:
            # critical=False routes to Discord — the spam channel.
            # Never bump to critical=True for routine progress.
            await notify_operator(notify_operator_message, critical=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[template_runner] operator notify failed: %s", exc)


# ---------------------------------------------------------------------------
# State shape
# ---------------------------------------------------------------------------


class PipelineState(TypedDict, total=False):
    """Permissive state for v1 POC.

    Mirrors the loose dict shape that ``content_router_service`` /
    ``StageRunner`` already use as ``context``. Keys are added by stages
    as they run; nothing is required up-front other than ``task_id``.

    A future iteration (Phase 2) tightens this with pydantic-validated
    sub-models per atom, but for the v1 lift we just want the existing
    coarse stages running unchanged.
    """

    # Always present at task entry:
    task_id: str
    topic: str

    # Filled by various stages:
    content: str
    title: str
    excerpt: str
    quality_score: float
    qa_final_score: float
    seo_title: str
    seo_description: str
    seo_keywords: list[str]
    featured_image_url: str
    featured_image_data: dict
    style: str
    tone: str
    target_length: int
    target_audience: str
    primary_keyword: str
    tags: list[str]
    research_context: dict
    research_results: list
    quality_result: object  # services.quality_service.QualityResult
    quality_passing: bool
    quality_details_initial: dict
    # qa_reviews uses operator.add as its reducer so parallel critic
    # atoms (architect-composed fan-out: narrate -> [critic_1, critic_2]
    # -> aggregate) can both append a Review on the same step. Without
    # the reducer, LangGraph's default last-value channel rejects
    # concurrent writes with InvalidUpdateError. Each critic returns
    # its review wrapped in a one-element list; the reducer concats
    # them. The aggregator atom reads the merged list as a normal
    # state value.
    qa_reviews: Annotated[list, operator.add]
    qa_rewrite_attempts: int
    stages: dict
    generate_metrics: dict
    cost_log: dict
    model_used: str
    models_used_by_phase: dict
    model_selection_log: dict

    # Plumbing the runner needs but doesn't constrain:
    database_service: object
    settings_service: object
    image_service: object
    image_style: str
    image_style_tracker: object
    experiment_assignment: object
    quality_preference: str
    title_originality: dict
    generate_featured_image: bool
    inline_images_replaced: list
    featured_image_plan: dict
    category: str
    content_task_id: int
    content_length: int
    models_by_phase: dict


# ---------------------------------------------------------------------------
# Run record types — mirror StageRunRecord/StageRunSummary so callers that
# already integrate with StageRunner can swap in TemplateRunner with minimal
# refactor.
# ---------------------------------------------------------------------------


@dataclass
class TemplateRunRecord:
    """One node's execution result, mirroring StageRunRecord shape."""

    name: str
    ok: bool
    detail: str = ""
    halted: bool = False
    skipped: bool = False
    elapsed_ms: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class TemplateRunSummary:
    """Aggregate return from :meth:`TemplateRunner.run`."""

    ok: bool
    template_slug: str
    halted_at: str | None = None
    records: list[TemplateRunRecord] = field(default_factory=list)
    final_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "template_slug": self.template_slug,
            "halted_at": self.halted_at,
            "records": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "detail": r.detail,
                    "halted": r.halted,
                    "skipped": r.skipped,
                    "elapsed_ms": r.elapsed_ms,
                    "metrics": r.metrics,
                }
                for r in self.records
            ],
        }


# ---------------------------------------------------------------------------
# Stage → LangGraph node identity adapter
# ---------------------------------------------------------------------------


def make_stage_node(
    stage: Any,
    pool: Any,
    *,
    record_sink: list[TemplateRunRecord] | None = None,
) -> Callable[[PipelineState], Awaitable[dict[str, Any]]]:
    """Wrap an existing Stage instance as a LangGraph node.

    The returned coroutine reads the stage's plugin config from
    ``app_settings`` (same as StageRunner does), enforces the stage's
    timeout, calls ``stage.execute(context, cfg.config)``, and merges
    the resulting ``StageResult.context_updates`` back into the state.

    On failure with halts_on_failure=True (the stage default), the node
    raises so LangGraph stops the run. ``record_sink`` (if provided)
    receives a per-node :class:`TemplateRunRecord` for downstream
    summary aggregation — analogous to StageRunSummary.records.

    The wrapped stage code is NOT modified — Phase 1 keeps coarse stages
    coarse.

    Implements: Glad-Labs/poindexter#357.
    """

    from plugins.config import PluginConfig
    from plugins.stage import StageResult  # type only, but useful for clarity

    name = getattr(stage, "name", stage.__class__.__name__)

    async def node(state: PipelineState) -> dict[str, Any]:
        # Treat the LangGraph state dict as the legacy ``context`` dict.
        # In v1 they're the same shape; Phase 3 will tighten with atom
        # I/O contracts.
        context: dict[str, Any] = dict(state)
        task_id = context.get("task_id")

        cfg = await PluginConfig.load(pool, "stage", name)
        if not cfg.enabled:
            logger.info("template_runner: stage %r disabled — skipping", name)
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name=name, ok=True, detail="disabled", skipped=True,
                    )
                )
            await _emit_progress(
                pool,
                event_type="template.node_skipped",
                payload={"task_id": str(task_id or ""), "node": name},
            )
            return {}

        # Best-effort: stamp pipeline_tasks.stage so observability
        # surfaces show in-flight progress (mirrors StageRunner._mark_stage,
        # see Glad-Labs/poindexter#350).
        await _mark_stage_column(pool, context.get("task_id"), name)
        await _emit_progress(
            pool,
            event_type="template.node_started",
            payload={"task_id": str(task_id or ""), "node": name},
            notify_operator_message=f"⚙️ {name}… (task {str(task_id or '')[:8]})",
        )

        timeout = int(
            cfg.get("timeout_seconds", getattr(stage, "timeout_seconds", 120))
        )
        halts = bool(
            cfg.get("halts_on_failure", getattr(stage, "halts_on_failure", True))
        )

        t0 = time.time()
        try:
            result: StageResult = await asyncio.wait_for(
                stage.execute(context, cfg.config),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            elapsed = int((time.time() - t0) * 1000)
            logger.exception("template_runner: stage %r timed out after %ds", name, timeout)
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name=name, ok=False,
                        detail=f"timed out after {timeout}s",
                        halted=halts, elapsed_ms=elapsed,
                    )
                )
            await _emit_progress(
                pool,
                event_type="template.node_failed",
                payload={
                    "task_id": str(task_id or ""), "node": name,
                    "reason": f"timeout {timeout}s", "elapsed_ms": elapsed,
                },
                notify_operator_message=(
                    f"❌ {name} timed out after {timeout}s "
                    f"(task {str(task_id or '')[:8]})"
                ),
            )
            if halts:
                raise RuntimeError(f"stage {name!r} timed out after {timeout}s")
            return {}
        except Exception as exc:
            elapsed = int((time.time() - t0) * 1000)
            logger.exception("template_runner: stage %r raised: %s", name, exc)
            if record_sink is not None:
                record_sink.append(
                    TemplateRunRecord(
                        name=name, ok=False,
                        detail=f"raised {type(exc).__name__}: {exc}",
                        halted=halts, elapsed_ms=elapsed,
                    )
                )
            await _emit_progress(
                pool,
                event_type="template.node_failed",
                payload={
                    "task_id": str(task_id or ""), "node": name,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "elapsed_ms": elapsed,
                },
                notify_operator_message=(
                    f"❌ {name} raised {type(exc).__name__} "
                    f"(task {str(task_id or '')[:8]})"
                ),
            )
            if halts:
                raise
            return {}

        elapsed = int((time.time() - t0) * 1000)
        updates = dict(result.context_updates or {})

        # Halt-on-success: a stage may set continue_workflow=False (e.g.
        # cross_model_qa rejecting a post). LangGraph doesn't have a
        # "halt the graph" primitive at the node level; we surface this
        # via the record + a state flag the template can read on its
        # conditional edges.
        halted = (not result.ok and halts) or (not result.continue_workflow)
        if halted:
            updates["_halt"] = True
            updates["_halt_reason"] = result.detail or "stage requested halt"

        if record_sink is not None:
            record_sink.append(
                TemplateRunRecord(
                    name=name, ok=result.ok, detail=result.detail,
                    halted=halted, elapsed_ms=elapsed,
                    metrics=dict(result.metrics or {}),
                )
            )

        if halted:
            await _emit_progress(
                pool,
                event_type="template.node_halted",
                payload={
                    "task_id": str(task_id or ""), "node": name,
                    "reason": result.detail or "stage requested halt",
                    "elapsed_ms": elapsed,
                },
                notify_operator_message=(
                    f"⛔ {name} halted: {(result.detail or 'requested halt')[:80]} "
                    f"(task {str(task_id or '')[:8]})"
                ),
            )
        else:
            await _emit_progress(
                pool,
                event_type="template.node_completed",
                payload={
                    "task_id": str(task_id or ""), "node": name,
                    "ok": bool(result.ok), "elapsed_ms": elapsed,
                },
                notify_operator_message=(
                    f"✓ {name} ({elapsed}ms) "
                    f"(task {str(task_id or '')[:8]})"
                ),
            )

        return updates

    node.__name__ = f"stage_node_{name}"
    return node


async def _mark_stage_column(pool: Any, task_id: Any, stage_name: str) -> None:
    """Stamp ``pipeline_tasks.stage`` for observability. Best-effort."""
    if pool is None or not task_id:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE pipeline_tasks SET stage = $1, updated_at = NOW() "
                "WHERE task_id::text = $2",
                stage_name, str(task_id),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("template_runner: stage column UPDATE failed: %s", exc)


# ---------------------------------------------------------------------------
# Halt-aware conditional edge helper
# ---------------------------------------------------------------------------


def halt_or_continue(next_node: str) -> Callable[[PipelineState], str]:
    """Return a LangGraph conditional-edge router that respects ``_halt``.

    Use this between every pair of nodes in v1 templates so a stage that
    returns ``continue_workflow=False`` (or fails with halts_on_failure)
    short-circuits the rest of the graph the same way StageRunner does.
    """

    def _route(state: PipelineState) -> str:
        if state.get("_halt"):
            return END
        return next_node

    _route.__name__ = f"route_to_{next_node}_or_end"
    return _route


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class TemplateRunner:
    """Execute a named LangGraph template against a starting state.

    Construction takes the DB pool (so adapters can read plugin config +
    stamp the stage column). ``run`` accepts a template slug and an
    initial state, looks up the template factory from
    ``services.pipeline_templates.TEMPLATES``, compiles the StateGraph
    (with MemorySaver for v1 — Postgres checkpointer is Phase 2), and
    invokes it.

    The returned :class:`TemplateRunSummary` mirrors
    :class:`plugins.stage_runner.StageRunSummary` shape so existing call
    sites (content_router_service, task_executor) can swap minimally.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def run(
        self,
        template_slug: str,
        initial_state: dict[str, Any],
        *,
        thread_id: str | None = None,
    ) -> TemplateRunSummary:
        """Run a template by slug.

        ``thread_id`` is the LangGraph checkpointer thread ID. Defaults
        to the task_id from initial_state, falling back to the slug
        itself for ad-hoc runs.
        """
        # Lazy import to avoid module-load cycle: pipeline_templates.__init__
        # imports adapters from here, here imports from there → cycle if
        # done at top level.
        from services.pipeline_templates import TEMPLATES

        factory = TEMPLATES.get(template_slug)
        if factory is None:
            raise KeyError(
                f"unknown template_slug={template_slug!r}; "
                f"registered={sorted(TEMPLATES)}"
            )

        records: list[TemplateRunRecord] = []
        graph: StateGraph = factory(pool=self._pool, record_sink=records)
        # No checkpointer in v1 — the pipeline state contains live
        # service objects (DatabaseService, ImageService, settings_service)
        # that aren't msgpack-serializable, and v1 doesn't need
        # durability. Phase 2 introduces a Postgres checkpointer with
        # custom serializers that strip the non-pickleable fields and
        # re-inject them on resume.
        compiled = graph.compile()

        thread_id = (
            thread_id
            or str(initial_state.get("task_id") or "")
            or template_slug
        )

        logger.info(
            "[template_runner] running template=%r thread_id=%r initial_keys=%s",
            template_slug, thread_id, sorted(initial_state.keys()),
        )

        await _emit_progress(
            self._pool,
            event_type="template.run_started",
            payload={
                "task_id": str(initial_state.get("task_id") or ""),
                "template_slug": template_slug,
                "thread_id": thread_id,
            },
            notify_operator_message=(
                f"▶️ Pipeline {template_slug} starting "
                f"(task {str(initial_state.get('task_id') or '')[:8]})"
            ),
        )

        try:
            final_state = await compiled.ainvoke(initial_state)
        except Exception as exc:
            logger.exception(
                "[template_runner] template %r raised: %s", template_slug, exc,
            )
            await _emit_progress(
                self._pool,
                event_type="template.run_failed",
                payload={
                    "task_id": str(initial_state.get("task_id") or ""),
                    "template_slug": template_slug,
                    "reason": f"{type(exc).__name__}: {exc}",
                },
                notify_operator_message=(
                    f"💥 Pipeline {template_slug} crashed: {type(exc).__name__} "
                    f"(task {str(initial_state.get('task_id') or '')[:8]})"
                ),
            )
            return TemplateRunSummary(
                ok=False,
                template_slug=template_slug,
                halted_at=records[-1].name if records else None,
                records=records,
                final_state=dict(initial_state),
            )

        ok = not any(r.halted for r in records)
        halted_at = next((r.name for r in records if r.halted), None)

        await _emit_progress(
            self._pool,
            event_type=("template.run_completed" if ok else "template.run_halted"),
            payload={
                "task_id": str(initial_state.get("task_id") or ""),
                "template_slug": template_slug,
                "ok": ok,
                "halted_at": halted_at,
                "node_count": len(records),
            },
            notify_operator_message=(
                f"🏁 Pipeline {template_slug} {'complete' if ok else f'halted at {halted_at}'} "
                f"(task {str(initial_state.get('task_id') or '')[:8]}, {len(records)} nodes)"
            ),
        )

        # Outcome feedback loop — write one capability_outcomes row per
        # node so the router can score (atom, model, tier) combinations
        # in production. Best-effort; logging failures don't fail the run.
        try:
            from services.capability_outcomes import record_run as _record_run
            interim = TemplateRunSummary(
                ok=ok,
                template_slug=template_slug,
                halted_at=halted_at,
                records=records,
                final_state=dict(final_state) if isinstance(final_state, dict) else {},
            )
            written = await _record_run(self._pool, interim, initial_state)
            logger.debug(
                "[template_runner] capability_outcomes wrote %d row(s)", written,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[template_runner] outcome write failed: %s", exc)

        return TemplateRunSummary(
            ok=ok,
            template_slug=template_slug,
            halted_at=halted_at,
            records=records,
            final_state=dict(final_state) if isinstance(final_state, dict) else {},
        )
