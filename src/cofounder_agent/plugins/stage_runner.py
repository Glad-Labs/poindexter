"""StageRunner — execute registered Stages in the order defined in app_settings.

Phase E replacement for the content_router_service.py god-file's inline
pipeline dispatch. Each `_stage_X` function in the old file becomes a
standalone Stage plugin (Protocol in ``plugins/stage.py``), and this
runner walks the DB-configured order list to invoke them.

## Order source of truth

``app_settings.pipeline.stages.order`` holds the ordered JSON list of
stage names the orchestrator runs, e.g.:

.. code:: json

    ["verify_task", "generate_content", "quality_evaluation",
     "replace_inline_images", "source_featured_image",
     "generate_seo_metadata", "generate_media_scripts",
     "capture_training_data", "finalize_task"]

Operators can reorder, disable-by-omission, or inject new stages at
runtime without a code deploy. Stages that are registered but not in
the order list are simply never executed.

## Per-stage config

Each stage's ``plugin.stage.<name>`` entry in app_settings holds
config (via :class:`PluginConfig`). Special fields the runner honors:

- ``enabled`` (default True) — if False, stage is skipped and logged.
- ``timeout_seconds`` (default from stage's ``timeout_seconds`` attr,
  fallback 120) — per-invocation deadline.
- ``halts_on_failure`` (default from stage's ``halts_on_failure`` attr,
  fallback True) — if False, a failing stage logs an error and the
  runner continues to the next stage instead of raising.

## Context

The context is a ``dict[str, Any]`` threaded through every stage.
Stages receive it as their first argument and can mutate it directly
(matches the existing ``services/phases/base_phase.py`` contract).
``StageResult.context_updates`` is merged on top of the context after
each stage for stages that prefer immutable-return style.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .config import PluginConfig
from .stage import StageResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RunSummary — what the runner returns (used for audit + brain handoff).
# ---------------------------------------------------------------------------


@dataclass
class StageRunRecord:
    """Per-stage record of what happened during a single run."""

    name: str
    ok: bool
    detail: str
    skipped: bool = False
    halted: bool = False
    elapsed_ms: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageRunSummary:
    """Aggregate return from :meth:`StageRunner.run_all`."""

    ok: bool
    halted_at: str | None = None
    records: list[StageRunRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for audit logging."""
        return {
            "ok": self.ok,
            "halted_at": self.halted_at,
            "stages": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "detail": r.detail,
                    "skipped": r.skipped,
                    "halted": r.halted,
                    "elapsed_ms": r.elapsed_ms,
                    "metrics": r.metrics,
                }
                for r in self.records
            ],
        }


# ---------------------------------------------------------------------------
# Loading — order list + stage lookup.
# ---------------------------------------------------------------------------


ORDER_KEY = "pipeline.stages.order"


async def load_stage_order(pool_or_conn: Any) -> list[str]:
    """Read ``pipeline.stages.order`` from app_settings.

    Returns the default order if the key is missing — a fresh install
    should still have a working pipeline without manual seeding.
    """
    raw = await pool_or_conn.fetchval(
        "SELECT value FROM app_settings WHERE key = $1", ORDER_KEY
    )
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("%s has malformed JSON; falling back to defaults", ORDER_KEY)
            parsed = None
        if isinstance(parsed, list) and all(isinstance(s, str) for s in parsed):
            return parsed
        if parsed is not None:
            logger.warning("%s is not a list of strings; falling back to defaults", ORDER_KEY)

    return list(DEFAULT_STAGE_ORDER)


# Default order mirrors the pre-refactor content_router_service.py pipeline.
# Keeping it explicit here (not in app_settings) means a fresh install runs
# the canonical pipeline out of the box; operators override by writing the
# ORDER_KEY row.
DEFAULT_STAGE_ORDER: list[str] = [
    "verify_task",
    "generate_content",
    "writer_self_review",
    "quality_evaluation",
    "url_validation",
    "replace_inline_images",
    "source_featured_image",
    "generate_seo_metadata",
    "generate_media_scripts",
    "capture_training_data",
    "finalize_task",
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class StageRunner:
    """Execute stages in configured order against a shared context.

    Construction takes every registered Stage instance (typically from
    :func:`plugins.registry.get_stages` + ``get_core_samples()["stages"]``)
    and a DB pool for config lookups. Call :meth:`run_all` once per
    pipeline invocation.
    """

    def __init__(self, pool: Any, stages: list[Any]):
        self._pool = pool
        self._by_name: dict[str, Any] = {s.name: s for s in stages}

    def registered_names(self) -> list[str]:
        return sorted(self._by_name)

    async def run_all(
        self,
        context: dict[str, Any],
        order: list[str] | None = None,
    ) -> StageRunSummary:
        """Execute every enabled stage in ``order``.

        If ``order`` is None, reads from ``pipeline.stages.order`` in
        app_settings (falling back to :data:`DEFAULT_STAGE_ORDER`).
        """
        if order is None:
            order = await load_stage_order(self._pool)

        summary = StageRunSummary(ok=True)
        for name in order:
            stage = self._by_name.get(name)
            if stage is None:
                # Order references a stage that isn't registered. Log + skip.
                # Not fatal — operators may have the order list ahead of
                # their installed plugins (e.g. they pulled repo without
                # installing a new stage package yet).
                logger.info("stage_runner: %r in order but not registered; skipping", name)
                summary.records.append(
                    StageRunRecord(name=name, ok=True, detail="not registered", skipped=True)
                )
                continue

            record = await self._run_one(stage, context)
            summary.records.append(record)

            if record.halted:
                summary.ok = False
                summary.halted_at = name
                return summary

        return summary

    async def _run_one(self, stage: Any, context: dict[str, Any]) -> StageRunRecord:
        """Invoke a single stage with config + timeout + halt semantics."""
        name = stage.name

        cfg = await PluginConfig.load(self._pool, "stage", name)
        if not cfg.enabled:
            logger.info("stage_runner: %r disabled in app_settings; skipping", name)
            return StageRunRecord(name=name, ok=True, detail="disabled", skipped=True)

        timeout = int(
            cfg.get("timeout_seconds", getattr(stage, "timeout_seconds", 120))
        )
        halts = bool(
            cfg.get("halts_on_failure", getattr(stage, "halts_on_failure", True))
        )

        import time
        t0 = time.time()
        try:
            result: StageResult = await asyncio.wait_for(
                stage.execute(context, cfg.config),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            elapsed = int((time.time() - t0) * 1000)
            logger.error("stage_runner: %r timed out after %ds", name, timeout)
            return StageRunRecord(
                name=name,
                ok=False,
                detail=f"timed out after {timeout}s",
                halted=halts,
                elapsed_ms=elapsed,
            )
        except Exception as e:  # noqa: BLE001 — stage crashed; don't let it propagate
            elapsed = int((time.time() - t0) * 1000)
            logger.exception("stage_runner: %r raised: %s", name, e)
            return StageRunRecord(
                name=name,
                ok=False,
                detail=f"raised {type(e).__name__}: {e}",
                halted=halts,
                elapsed_ms=elapsed,
            )

        elapsed = int((time.time() - t0) * 1000)

        # Merge any context_updates onto the shared context.
        if result.context_updates:
            context.update(result.context_updates)

        # Halt logic:
        # - Stage failed + runner is configured to halt on failure → halt.
        # - Stage explicitly set continue_workflow=False → halt (early-exit
        #   even on success — e.g. "quality too low, publish path is moot").
        halted = (not result.ok and halts) or (not result.continue_workflow)

        return StageRunRecord(
            name=name,
            ok=result.ok,
            detail=result.detail,
            halted=halted,
            elapsed_ms=elapsed,
            metrics=dict(result.metrics),
        )
