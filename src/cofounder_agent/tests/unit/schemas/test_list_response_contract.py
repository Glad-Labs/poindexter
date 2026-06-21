"""Conformance ratchet for the canonical list envelope (poindexter#745).

ADR ``docs/architecture/2026-06-20-api-response-contracts.md``: every list
response is ``{items, total, limit, offset}`` — offset-based, snake_case. This
test locks the canonical generic's shape so it can't drift back to the
page-based form, and is the seam the per-endpoint envelope assertions grow into
as the typed-schema sweep lands (steps 2–N).
"""

from __future__ import annotations

import pytest

from schemas import ListResponse, PaginatedResponse
from schemas.media_schemas import PodcastEpisodeListResponse
from schemas.settings_schemas import SettingListResponse
from schemas.task_schemas import (
    GateListResponse,
    GatePausedListResponse,
    MediaApprovalListResponse,
    PendingApprovalListResponse,
    PostApprovalListResponse,
    TaskListResponse,
)

pytestmark = pytest.mark.unit

# Per-endpoint list-response models, asserted to descend from the canonical
# generic. The typed-schema sweep (#745 steps 2–N) appends each converted
# endpoint's envelope here, so one ratchet covers every list surface and a
# regression to a bespoke page-based body fails loudly.
ENDPOINT_LIST_MODELS = [
    SettingListResponse,  # step 3 — GET /api/settings/
    TaskListResponse,  # step 4 — GET /api/tasks
    PendingApprovalListResponse,  # step 5 — GET /api/tasks/pending-approval
    MediaApprovalListResponse,  # step 6 — GET /api/media-approval/pending
    GatePausedListResponse,  # step 7 — GET /api/gates/pending
    GateListResponse,  # step 8 — GET /api/gates
    PostApprovalListResponse,  # step 9 — GET /api/posts-approval/pending
    PodcastEpisodeListResponse,  # step 10 — GET /api/podcast/episodes
]


def test_list_response_is_canonical_offset_shape() -> None:
    fields = set(ListResponse.model_fields)
    assert fields == {"items", "total", "limit", "offset"}, (
        "ListResponse must be the canonical {items, total, limit, offset} "
        f"envelope (#745); got {sorted(fields)}"
    )


def test_list_response_dropped_page_based_fields() -> None:
    """The #745 reconciliation resolves the page-vs-offset split toward offset."""
    for banned in ("page", "per_page", "pages"):
        assert banned not in ListResponse.model_fields, (
            f"{banned!r} is the page-based shape the ADR replaced with offset"
        )


def test_paginated_response_is_deprecated_alias_of_list_response() -> None:
    """Backcompat: older code imports ``PaginatedResponse``; keep it as an alias
    to the one canonical ``ListResponse`` rather than a second divergent type."""
    assert PaginatedResponse is ListResponse


def test_items_defaults_to_empty_list_not_null() -> None:
    resp = ListResponse[str](total=0, limit=20, offset=0)
    assert resp.items == [], "items must default to [] (never null) per the ADR"


def test_round_trips_an_offset_window() -> None:
    resp = ListResponse[str](
        items=["a", "b"], total=100, limit=20, offset=40
    )
    assert resp.total == 100
    assert resp.limit == 20
    assert resp.offset == 40
    assert resp.items == ["a", "b"]


@pytest.mark.parametrize("model", ENDPOINT_LIST_MODELS)
def test_endpoint_model_descends_from_canonical_envelope(model: type) -> None:
    """Each converted endpoint's list model IS a ``ListResponse`` — so it
    inherits the canonical ``{items, total, limit, offset}`` shape and can't
    reintroduce a bespoke page-based body."""
    assert issubclass(model, ListResponse)
    assert set(model.model_fields) == {"items", "total", "limit", "offset"}, (
        f"{model.__name__} drifted from the canonical envelope; got {sorted(model.model_fields)}"
    )
