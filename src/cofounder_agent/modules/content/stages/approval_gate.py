"""ApprovalGateStage — generic HITL pause-and-wait Stage (#145).

Drop this Stage at any boundary in any pipeline to require a human
review before continuing. Configurable per-gate so the operator can
toggle each gate independently:

- ``pipeline_gate_topic_decision = on|off``
- ``pipeline_gate_preview_approval = on|off``
- ``pipeline_gate_final_media = on``  (existing behaviour, eventual rename)

Contract
--------

The Stage's ``config`` dict drives behavior:

- ``gate_name``  (required) — stable slug, becomes
  ``content_tasks.awaiting_gate``. Examples: ``"topic_decision"``,
  ``"preview_approval"``.
- ``artifact_fn`` (required) — ``Callable[[context], dict]``. Returns
  the JSON-serializable thing the operator will review. Keep it lean
  — large artifacts go in object storage with a URL in the dict.
- ``skip_if_setting`` (optional) — name of an app_settings flag that,
  when truthy, skips this gate even if its enable flag is on. Lets
  one Stage participate in multiple gate-enable hierarchies (e.g.
  "skip every HITL gate for tasks tagged automated_test").
- ``halt_status`` (optional) — what to set ``content_tasks.status``
  to when the gate trips. Default is ``"in_progress"`` so the
  pipeline runner doesn't think the task is done. The Stage Runner
  halts via ``continue_workflow=False``; the runner's status
  bookkeeping is what observes ``halt_status``.

Behavior
--------

1. Look up ``pipeline_gate_<gate_name>`` in site_config. If unset or
   ``off``, return ``StageResult(ok=True)`` with no changes — the
   Stage is a passthrough.
2. Check ``skip_if_setting`` (when configured) — same passthrough if
   it's truthy.
3. Build the artifact via ``artifact_fn(context)``.
4. Call :func:`services.approval_service.pause_at_gate` to write
   ``awaiting_gate``, ``gate_artifact``, ``gate_paused_at`` and fire
   the operator notification.
5. Return ``StageResult(ok=True, continue_workflow=False)`` so the
   runner halts. Approve / reject re-queues the task.

Halts on failure: yes — if pause_at_gate raises (DB write failed) we
shouldn't silently continue past a gate that was supposed to block.

This Stage is generic. Adding a new gate is adding a row to whichever
declarative pipeline registry your worker uses (or hard-coding it
into a content_router_service phase chain) and pointing this Stage's
``config`` at it. No subclassing required.
"""

from __future__ import annotations

from typing import Any, Callable

from plugins.stage import StageResult
from services.approval_service import is_gate_enabled, pause_at_gate
from services.logger_config import get_logger

logger = get_logger(__name__)


def _truthy(value: Any) -> bool:
    """Same truthy semantics site_config.get() values use elsewhere."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "on", "1", "yes")


class ApprovalGateStage:
    """Generic, config-driven HITL gate Stage.

    The same instance can be reused across pipelines — gate identity
    comes from ``config["gate_name"]`` at execute-time, not at
    construction. A workflow author wires up multiple gates by
    registering this Stage multiple times in the chain with different
    config dicts.
    """

    name = "approval_gate"
    description = "Pause the pipeline pending human approval at a named gate"
    timeout_seconds = 30
    halts_on_failure = True

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        gate_name = (config or {}).get("gate_name")
        if not gate_name:
            return StageResult(
                ok=False,
                detail="approval_gate Stage missing required config['gate_name']",
                continue_workflow=False,
            )

        artifact_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = (
            (config or {}).get("artifact_fn")
        )

        site_config = context.get("site_config")
        task_id = context.get("task_id")
        if not task_id:
            return StageResult(
                ok=False,
                detail="approval_gate Stage missing context['task_id']",
                continue_workflow=False,
            )

        # 1. Master enable check. Default-off — adding the Stage to the
        # chain doesn't accidentally start blocking on humans until the
        # operator explicitly flips the setting.
        if not is_gate_enabled(gate_name, site_config):
            logger.info(
                "[approval_gate:%s] gate disabled — passthrough", gate_name,
            )
            return StageResult(
                ok=True,
                detail=f"gate {gate_name!r} disabled, passthrough",
                metrics={"gate_name": gate_name, "skipped": True, "reason": "disabled"},
            )

        # 2. Per-task / per-pipeline opt-out. Lets a single Stage row
        # participate in multiple skip hierarchies — e.g. "skip every
        # HITL gate for tasks tagged automated_test".
        skip_setting = (config or {}).get("skip_if_setting")
        if skip_setting:
            skip_value = (
                site_config.get(skip_setting, "") if site_config is not None else ""
            )
            if _truthy(skip_value):
                logger.info(
                    "[approval_gate:%s] skip_if_setting %s truthy — passthrough",
                    gate_name, skip_setting,
                )
                return StageResult(
                    ok=True,
                    detail=(
                        f"gate {gate_name!r} skipped — "
                        f"skip_if_setting={skip_setting} truthy"
                    ),
                    metrics={
                        "gate_name": gate_name,
                        "skipped": True,
                        "reason": f"skip_if_setting:{skip_setting}",
                    },
                )

        # 3. Build the artifact dict — the thing the operator reviews.
        # The artifact_fn is called with the full pipeline context so
        # it can pull whatever it needs (topic, image_url, video URL,
        # etc.) without coupling this Stage to a particular shape.
        artifact: dict[str, Any] = {}
        if artifact_fn is not None:
            try:
                artifact = artifact_fn(context) or {}
            except Exception as exc:
                logger.exception(
                    "[approval_gate:%s] artifact_fn raised: %s",
                    gate_name, exc,
                )
                return StageResult(
                    ok=False,
                    detail=(
                        f"approval_gate {gate_name!r} artifact_fn raised: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    continue_workflow=False,
                )
        else:
            # No artifact_fn — fall back to a minimal context summary
            # so the operator at least knows which task they're being
            # asked about. Stage authors should always supply one.
            artifact = {
                "topic": context.get("topic", ""),
                "title": context.get("title", ""),
                "task_id": str(task_id),
            }

        # 4. Persist + notify. The DB pool comes from the
        # database_service handle the runner seeds on the context.
        database_service = context.get("database_service")
        pool = getattr(database_service, "pool", None) if database_service else None
        if pool is None:
            return StageResult(
                ok=False,
                detail=(
                    f"approval_gate {gate_name!r}: no DB pool on context "
                    "(context['database_service'].pool is None)"
                ),
                continue_workflow=False,
            )

        try:
            pause_result = await pause_at_gate(
                task_id=str(task_id),
                gate_name=gate_name,
                artifact=artifact,
                site_config=site_config,
                pool=pool,
                notify=True,
            )
        except Exception as exc:
            logger.exception(
                "[approval_gate:%s] pause_at_gate raised: %s",
                gate_name, exc,
            )
            return StageResult(
                ok=False,
                detail=(
                    f"approval_gate {gate_name!r} pause_at_gate failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                continue_workflow=False,
            )

        # 5. Halt the workflow. The runner stops here. Resume is
        # operator-driven via `poindexter approve <task_id>` (CLI),
        # MCP `approve`, or the future REST endpoint — all of which
        # call services.approval_service.approve(), which clears the
        # gate columns + inserts a pipeline_events row that wakes the
        # runner to pick up the next Stage.
        halt_status = (config or {}).get("halt_status", "in_progress")
        return StageResult(
            ok=True,
            detail=f"awaiting human approval at gate {gate_name!r}",
            continue_workflow=False,
            context_updates={
                "awaiting_gate": gate_name,
                "gate_artifact": artifact,
                # Status hint — runner reads this when bookkeeping the
                # halted task. Not authoritative; the DB row written
                # by pause_at_gate is.
                "status": halt_status,
            },
            metrics={
                "gate_name": gate_name,
                "skipped": False,
                "paused_at": pause_result.get("paused_at"),
                "notify_sent": (pause_result.get("notify") or {}).get("sent", False),
            },
        )


__all__ = ["ApprovalGateStage"]
