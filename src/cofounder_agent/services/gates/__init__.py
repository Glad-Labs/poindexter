"""Generic per-medium HITL approval gate engine.

See :mod:`services.gates.post_approval_gates` for the public API.
"""

from services.gates.post_approval_gates import (
    CANONICAL_GATE_NAMES,
    GATE_STATE_APPROVED,
    GATE_STATE_PENDING,
    GATE_STATE_REJECTED,
    GATE_STATE_REVISING,
    GATE_STATE_SKIPPED,
    GateCascadeRequiredError,
    GateNotFoundError,
    GateServiceError,
    GateStateError,
    advance_workflow,
    approve_gate,
    create_gates_for_post,
    get_gates_for_post,
    get_next_pending_gate,
    notify_gate_pending,
    record_media_failure,
    reject_gate,
    reopen_gate,
    reset_gate_to_pending,
    revise_gate,
)

__all__ = [
    "CANONICAL_GATE_NAMES",
    "GATE_STATE_APPROVED",
    "GATE_STATE_PENDING",
    "GATE_STATE_REJECTED",
    "GATE_STATE_REVISING",
    "GATE_STATE_SKIPPED",
    "GateCascadeRequiredError",
    "GateNotFoundError",
    "GateServiceError",
    "GateStateError",
    "advance_workflow",
    "approve_gate",
    "create_gates_for_post",
    "get_gates_for_post",
    "get_next_pending_gate",
    "notify_gate_pending",
    "record_media_failure",
    "reject_gate",
    "reopen_gate",
    "reset_gate_to_pending",
    "revise_gate",
]
