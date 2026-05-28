"""Pin the ``niche_slug`` field roundtrip through ``ModelConverter.to_task_response``.

2026-05-27 incident: ``publish_service.publish_post_from_task`` reads
``task.get("niche_slug")`` to look up the niche's
``default_media_to_generate`` policy. Tasks come from
``database_service.get_task(task_id)`` which converts the DB row
through ``ModelConverter.to_task_response`` → Pydantic
``TaskResponse(**data)``.

Pydantic's default ``model_config`` ignores unknown fields — so when
``niche_slug`` was missing from ``TaskResponse``, the field was silently
stripped on every conversion. Every glad-labs post published from
2026-05-19 (the niche-policy migration date) onward got
``media_to_generate=[]``, and ``backfill_podcasts`` /
``backfill_videos`` found zero eligible candidates for ~8 days.

This test pins the roundtrip — adding it as a contract test catches
any future Pydantic schema change that drops the field again. If
``database_response_models.TaskResponse`` ever loses the field, this
test fails and the silent regression doesn't reach production.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from schemas.database_response_models import TaskResponse
from schemas.model_converter import ModelConverter


def _make_row(**overrides) -> dict:
    """Build a dict-shaped row that ``ModelConverter._normalize_row_data``
    accepts directly. asyncpg.Record exposes ``.keys()`` so the converter
    dict-converts it via ``dict(row)`` in production; passing a dict
    exercises the same code path."""
    base = {
        "task_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "id": 1,
        "task_name": "Test post for niche_slug roundtrip",
        "title": "Test post for niche_slug roundtrip",
        "topic": "Some topic",
        "status": "approved",
        "niche_slug": "glad-labs",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "task_metadata": {},
        "metadata": {},
    }
    base.update(overrides)
    return base


def test_task_response_schema_declares_niche_slug() -> None:
    """The field must exist on the schema. Removing this declaration
    re-introduces the silent-strip bug — Pydantic's
    ``extra='ignore'`` default discards unknown fields without
    warning."""
    fields = TaskResponse.model_fields
    assert "niche_slug" in fields, (
        "TaskResponse must declare niche_slug — without it, Pydantic "
        "silently drops the value on get_task() and "
        "publish_service can't look up the niche's media policy."
    )


def test_niche_slug_roundtrips_through_to_task_response() -> None:
    """The end-to-end conversion preserves niche_slug from row → TaskResponse → dict.
    This is the exact path ``DatabaseService.get_task`` uses to feed
    ``publish_service.publish_post_from_task``."""
    row = _make_row(niche_slug="glad-labs")
    task_response = ModelConverter.to_task_response(row)
    assert task_response.niche_slug == "glad-labs"

    as_dict = ModelConverter.to_dict(task_response)
    assert as_dict.get("niche_slug") == "glad-labs", (
        "to_dict must include niche_slug — publish_service reads it as "
        "``task.get('niche_slug')`` on the dict form."
    )


@pytest.mark.parametrize(
    "value",
    ["glad-labs", "dev_diary", None, ""],
    ids=["glad-labs", "dev_diary", "null", "empty"],
)
def test_niche_slug_handles_all_realistic_values(value: str | None) -> None:
    """Every niche slug shape that actually shows up in production —
    real slugs, NULL (legacy rows from before the column was added),
    empty string (untriaged tasks)."""
    row = _make_row(niche_slug=value)
    task_response = ModelConverter.to_task_response(row)
    # None and "" both surface as None on the model (Pydantic coerces
    # empty string → None for Optional[str] fields). The publish path
    # checks ``if niche_slug:`` so both are equivalent.
    assert task_response.niche_slug == value or (
        not value and not task_response.niche_slug
    )
