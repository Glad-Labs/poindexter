"""Pin the route-registration order that prevents path shadowing.

poindexter#752 item 3: ``/api/tasks/pending-approval`` (served by
``approval_router``) resolves to the approval handler only because
``approval_router`` is registered BEFORE ``task_router``. FastAPI matches
routes in include order, so the concrete ``/pending-approval`` path must be
mounted before ``task_router``'s wildcard ``/{task_id}`` — otherwise
``pending-approval`` is silently captured as a task-id lookup.

``register_all_routes`` walks ``_WORKER_ROUTES`` in order and calls
``app.include_router`` once per entry, so manifest order IS resolution
order. This test pins that order so a future manifest reshuffle fails loud
instead of quietly breaking the endpoint.
"""

from __future__ import annotations

import pytest

from utils.route_registration import _WORKER_ROUTES

pytestmark = pytest.mark.unit


def _status_key_index(status_key: str) -> int:
    """Index of the manifest entry with this status_key (4-tuple:
    module_path, router_attr, status_key, description)."""
    for i, entry in enumerate(_WORKER_ROUTES):
        if entry[2] == status_key:
            return i
    raise AssertionError(
        f"{status_key!r} not found in _WORKER_ROUTES — the route manifest "
        "changed; update this guard if the router was renamed or removed."
    )


def test_approval_router_registered_before_task_router():
    """approval_router must precede task_router so the concrete
    /api/tasks/pending-approval path matches before task_router's wildcard
    /api/tasks/{task_id} (poindexter#752 item 3)."""
    approval_idx = _status_key_index("approval_router")
    task_idx = _status_key_index("task_router")
    assert approval_idx < task_idx, (
        "approval_router must register before task_router, or "
        "/api/tasks/pending-approval is shadowed by /api/tasks/{task_id}"
    )
