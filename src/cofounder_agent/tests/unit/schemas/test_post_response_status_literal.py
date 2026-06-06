"""Contract test for ``PostResponse.status`` Literal coverage.

Pins the 2026-05-26 follow-up to PR #595: the model's status Literal
only listed ``draft``/``published``/``archived``, which crashed
``publish_post_from_task(stage_only=True)`` when it tried to return
a posts row at ``status='approved'`` for the schedule-batch staging
pool.

This test pins the full lifecycle so a future "let's clean up the
model" refactor that drops the new states fails here instead of
silently breaking the staging or scheduling flows.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from schemas.database_response_models import PostResponse

# The full state machine the posts table actually carries — every value
# here must round-trip through PostResponse validation without raising.
_REQUIRED_STATUSES = [
    "draft",                # publish_post_from_task(draft_mode=True)
    "approved",             # publish_post_from_task(stage_only=True) — schedule-batch staging
    "awaiting_approval",    # scheduling_service.schedule_batch eligibility input
    "awaiting_gates",       # publish_post_from_task with planned gates
    "scheduled",            # scheduling_service.schedule_batch output → scheduled_publisher input
    "published",            # final state
    "archived",             # retired
]


def _make_minimal_kwargs(status: str) -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": "Test",
        "slug": "test",
        "content": "Body.",
        "status": status,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


@pytest.mark.parametrize("status", _REQUIRED_STATUSES)
def test_post_response_accepts_status(status: str) -> None:
    """Every status the pipeline produces must validate. Missing one
    here means a downstream stage will crash with a pydantic
    Literal error the moment it tries to return a row in that state."""
    model = PostResponse(**_make_minimal_kwargs(status))
    assert model.status == status


def test_post_response_rejects_unknown_status() -> None:
    """The Literal is the source of truth — new states require a code
    change here. A typo-state shouldn't quietly slip through."""
    with pytest.raises(Exception) as exc_info:
        PostResponse(**_make_minimal_kwargs("not_a_real_state"))
    assert "literal_error" in str(exc_info.value) or "Input should be" in str(exc_info.value)


def test_post_response_default_is_draft() -> None:
    """Default status must be ``draft``. A new post being created
    without an explicit status shouldn't accidentally land in a
    later-stage state (e.g. ``published``)."""
    kwargs = _make_minimal_kwargs("draft")
    del kwargs["status"]
    model = PostResponse(**kwargs)
    assert model.status == "draft"
