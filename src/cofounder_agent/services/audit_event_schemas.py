"""Event-shape registry for ``audit_log.details`` (poindexter#758).

``audit_log.details`` is an untyped JSONB column functioning as the
system's universal event store, and two rails depend on specific shapes
staying stable: the QA Rails dashboard reads ``event_type='qa_pass_completed'``
rows, and the Findings dashboard + ``findings_alert_router`` read
``event_type='finding'`` rows. Without shape enforcement a producer can
rename a field and the consumer degrades silently (blank panel, dropped
filter) with no error anywhere.

This is deliberately minimal, not a platform build-out (per the issue):
only the two load-bearing types are registered. More are added
opportunistically as they're touched, not as a batch conversion sweep.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from services.logger_config import get_logger

logger = get_logger(__name__)


class QaReviewEntry(BaseModel):
    """One reviewer's verdict inside a ``qa_pass_completed`` row."""

    model_config = ConfigDict(extra="allow")

    reviewer: str | None = None
    provider: str | None = None
    approved: bool = False
    score: float = 0.0
    advisory: bool = False
    feedback_preview: str = ""


class QaPassCompletedDetails(BaseModel):
    """Powers the QA Rails dashboard (``qa_pass_completed`` rows).

    Two producers write this shape: ``modules/content/atoms/qa_aggregate.py``
    (the canonical_blog graph_def path, which adds ``terminal`` /
    ``qa_rewrite_attempts`` / ``rescued`` for the rescue-loop) and
    ``modules/content/multi_model_qa.py`` (dev_diary's legacy TEMPLATES
    path, which omits them) — so those three fields are optional rather
    than dropping either producer's shape.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    approved: bool
    final_score: float
    approval_threshold: float
    reviewer_count: int
    reviews: list[QaReviewEntry]
    terminal: bool | None = None
    qa_rewrite_attempts: int | None = None
    rescued: bool | None = None
    # Informational only (#2125) — the weighted mean over EVERY rail, advisory
    # included, whereas ``final_score`` counts only the rails that can gate.
    # The GAP between them is the operator-facing signal: it is what the gate
    # is choosing not to hear. Never compare this to ``approval_threshold``.
    #
    # Optional because the dev_diary producer (multi_model_qa.py) doesn't
    # compute it, and because rows written before it was persisted must keep
    # validating — a dashboard panel reading it uses a NULL-safe filter.
    qa_all_rail_score: float | None = None
    advisory_rail_count: int | None = None
    gating_rail_count: int | None = None


class FindingDetails(BaseModel):
    """Powers the Findings dashboard + ``findings_alert_router`` (``finding``
    rows). Single producer: ``utils/findings.py::emit_finding``.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    kind: str
    title: str
    body: str
    dedup_key: str | None = None
    extra: dict[str, Any] | None = None


class ChatToolCallDetails(BaseModel):
    """Powers the Cofounder chat panels (``chat_tool_call`` rows).

    Single producer: ``services/chat_agent.py`` — one row per tool
    execution in a chat turn, the reviewable trail of what the agent
    DID (poindexter#947). ``args_digest`` is a truncated JSON preview,
    never the full arguments (they may embed operator text).
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    conversation_id: str
    message_id: str | None = None
    tool: str
    tier: str
    ok: bool
    duration_ms: int = 0
    args_digest: str = ""
    error: str | None = None


class ChatTurnCompletedDetails(BaseModel):
    """Turn-level outcome rows (Cofounder chat panels — poindexter#949).

    Single producer: ``services/chat_agent.py`` (one row per finalized
    turn, written in the loop's ``finally``).
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    conversation_id: str
    message_id: str | None = None
    turn_status: str
    model: str = ""
    tool_calls: int = 0
    tool_errors: int = 0
    approvals_queued: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0


class ChatApprovalResolvedDetails(BaseModel):
    """Approval-card resolutions (poindexter#949 — the deny count feeds the
    Cofounder Grafana row). Single producer: ``services/chat_approvals.py``.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    approval_id: str
    conversation_id: str
    tool: str
    approved: bool
    executed_ok: bool | None = None


class ChatPlanRunDetails(BaseModel):
    """Architect plan-card runs (poindexter#950 — the Cofounder panel's
    plans-run count). Single producer: ``services/chat_plans.py``.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    plan_id: str
    conversation_id: str
    template_slug: str
    task_id: str


EVENT_SCHEMAS: dict[str, type[BaseModel]] = {
    "qa_pass_completed": QaPassCompletedDetails,
    "finding": FindingDetails,
    "chat_tool_call": ChatToolCallDetails,
    "chat_turn_completed": ChatTurnCompletedDetails,
    "chat_approval_resolved": ChatApprovalResolvedDetails,
    "chat_plan_run": ChatPlanRunDetails,
}


def validate_event_details(
    event_type: str, details: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Validate ``details`` against its registered schema, if any.

    Unregistered event_types pass through unchanged — this registry only
    covers the two types named in poindexter#758.

    On a shape mismatch: log loudly (visible in Loki/GlitchTip) and still
    return the original ``details`` unchanged. Audit writes are
    fire-and-forget telemetry across every call site (`write_bg` /
    `audit_log_bg`) precisely so they never block or corrupt the pipeline
    — a producer-side shape bug should surface as a loud log for a human
    to fix, not as a dropped observability row.
    """
    if details is None:
        return details
    model_cls = EVENT_SCHEMAS.get(event_type)
    if model_cls is None:
        return details
    try:
        validated = model_cls(**details)
    except ValidationError as exc:
        logger.error(
            "[audit_event_schemas] %s details failed shape validation "
            "(poindexter#758) — writing as-is: %s",
            event_type,
            exc,
        )
        return details
    return validated.model_dump(exclude_none=True)
