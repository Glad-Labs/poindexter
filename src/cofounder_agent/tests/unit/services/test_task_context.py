"""services.task_context — the ambient per-run task-id binding (poindexter#902)."""

from __future__ import annotations

import asyncio

import pytest

from services.task_context import bind_task_id, current_task_id, reset_task_id


def test_default_is_none():
    assert current_task_id() is None


def test_bind_read_reset_roundtrip():
    token = bind_task_id("task-123")
    try:
        assert current_task_id() == "task-123"
    finally:
        reset_task_id(token)
    assert current_task_id() is None


def test_falsy_binds_none_and_still_resets():
    outer = bind_task_id("outer-task")
    try:
        inner = bind_task_id(None)
        assert current_task_id() is None
        reset_task_id(inner)
        assert current_task_id() == "outer-task"
    finally:
        reset_task_id(outer)


def test_non_string_ids_coerce_to_str():
    token = bind_task_id(42)  # type: ignore[arg-type] — legacy int ids
    try:
        assert current_task_id() == "42"
    finally:
        reset_task_id(token)


@pytest.mark.asyncio
async def test_concurrent_tasks_see_their_own_binding():
    """asyncio.create_task copies the context — two concurrent runs must not
    bleed ids into each other (the whole point of a ContextVar over a global)."""
    seen: dict[str, str | None] = {}

    async def run(task_id: str) -> None:
        token = bind_task_id(task_id)
        try:
            await asyncio.sleep(0.01)  # interleave with the sibling
            seen[task_id] = current_task_id()
        finally:
            reset_task_id(token)

    await asyncio.gather(run("task-a"), run("task-b"))
    assert seen == {"task-a": "task-a", "task-b": "task-b"}
    assert current_task_id() is None
