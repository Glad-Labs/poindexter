"""TemplateRunner.run binds the ambient task id around the whole run (#902)."""

from __future__ import annotations

from typing import Any

import pytest

from services.task_context import current_task_id
from services.template_runner import TemplateRunner

pytestmark = pytest.mark.asyncio


async def test_run_binds_task_id_around_impl_and_resets(monkeypatch):
    captured: list[str | None] = []

    async def _fake_impl(self: Any, slug: str, state: dict, **_kwargs: Any) -> str:
        captured.append(current_task_id())
        return "summary"

    monkeypatch.setattr(TemplateRunner, "_run_impl", _fake_impl)
    runner = TemplateRunner.__new__(TemplateRunner)  # run() touches no ctor state

    out = await runner.run("canonical_blog", {"task_id": "task-xyz"})

    assert out == "summary"
    assert captured == ["task-xyz"]
    # The binding must not leak past the run.
    assert current_task_id() is None


async def test_run_resets_binding_even_when_impl_raises(monkeypatch):
    async def _boom(self: Any, *_a: Any, **_k: Any) -> None:
        raise RuntimeError("node exploded")

    monkeypatch.setattr(TemplateRunner, "_run_impl", _boom)
    runner = TemplateRunner.__new__(TemplateRunner)

    with pytest.raises(RuntimeError):
        await runner.run("canonical_blog", {"task_id": "task-abc"})
    assert current_task_id() is None


async def test_run_without_task_id_binds_none(monkeypatch):
    captured: list[str | None] = []

    async def _fake_impl(self: Any, *_a: Any, **_k: Any) -> str:
        captured.append(current_task_id())
        return "ok"

    monkeypatch.setattr(TemplateRunner, "_run_impl", _fake_impl)
    runner = TemplateRunner.__new__(TemplateRunner)

    await runner.run("dev_diary", {})
    assert captured == [None]
