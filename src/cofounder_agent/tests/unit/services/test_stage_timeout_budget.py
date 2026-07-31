"""A non-halting stage that times out must not be able to lose work silently.

RCA 2026-07-31 — the "5000-minutes-a-day" post published with no hero image.
``source_featured_image`` carried a hardcoded ``timeout_seconds = 300`` while
its render budget is settings-driven (``image_gen_render_attempts`` x
``image_render_timeout_seconds`` + backoff = 483s on prod), and the GPU-lock
wait sits inside the node timeout but outside the per-request httpx timeout.
The render completed at 302s; the node was killed at 300s. Because the stage is
``halts_on_failure=False``, three layers each did the locally-safe thing and the
failure disappeared:

1. ``make_stage_node`` swallowed the ``TimeoutError`` and returned ``{}``.
2. The stage's own downgrade finding lives INSIDE ``execute``, which had been
   cancelled — so it never fired.
3. ``_wrap_atom`` hardcoded ``ok=True`` on any non-raising return, so
   ``atom_runs`` recorded ``status=ok`` / ``output_keys=[]`` for 301s of work.

The image had already rendered and uploaded to R2. It was simply orphaned.
These tests pin each layer.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from plugins.stage import StageResult
from services.atom_registry import _make_stage_runner
from services.pipeline_architect import _wrap_atom
from services.template_runner import _resolve_node_timeout, make_stage_node

pytestmark = pytest.mark.unit


class _FakeSiteConfig:
    """Minimal SiteConfig stand-in exposing the typed getters used here."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values = values or {}

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self._values.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self._values.get(key, default))


def _stage_node_patches() -> tuple:
    """PluginConfig.load runs before execute(); DB-touching helpers stubbed."""
    enabled_cfg = SimpleNamespace(enabled=True, config={}, get=lambda k, d=None: d)
    return (
        patch("plugins.config.PluginConfig.load", AsyncMock(return_value=enabled_cfg)),
        patch("services.template_runner._mark_stage_column", AsyncMock()),
        patch("services.template_runner._emit_progress", AsyncMock()),
    )


# ---------------------------------------------------------------------------
# 1. The budget invariant itself
# ---------------------------------------------------------------------------


def test_featured_image_timeout_contains_its_own_retry_budget() -> None:
    """The regression, stated directly: node timeout >= render budget.

    Prod settings at the time of the incident. The old hardcoded 300 could not
    contain 2 x 240 + 3, so the second attempt could never even start.
    """
    from modules.content.stages.source_featured_image import (
        SourceFeaturedImageStage,
        resolve_stage_timeout_seconds,
    )

    site_config = _FakeSiteConfig({
        "image_gen_render_attempts": 2,
        "image_render_timeout_seconds": 240,
        "image_gen_retry_backoff_seconds": 3,
        "image_featured_stage_overhead_seconds": 120,
    })
    resolved = resolve_stage_timeout_seconds(site_config)
    raw_render_budget = 2 * 240 + 3

    assert resolved >= raw_render_budget, (
        "node timeout must contain the full retry budget or a render is killed "
        "mid-flight and its result discarded"
    )
    assert resolved == raw_render_budget + 120
    # The value the incident ran with must now be rejected by the floor.
    assert resolved > SourceFeaturedImageStage.timeout_seconds == 300
    # And the stage exposes it through the hook make_stage_node consults.
    assert SourceFeaturedImageStage().resolve_timeout_seconds(site_config) == resolved


def test_featured_image_budget_scales_with_attempts() -> None:
    """Raising the retry count must raise the node timeout with it."""
    from modules.content.stages.source_featured_image import (
        resolve_stage_timeout_seconds,
    )

    base = {
        "image_render_timeout_seconds": 240,
        "image_gen_retry_backoff_seconds": 3,
        "image_featured_stage_overhead_seconds": 120,
    }
    one = resolve_stage_timeout_seconds(
        _FakeSiteConfig({**base, "image_gen_render_attempts": 1}),
    )
    three = resolve_stage_timeout_seconds(
        _FakeSiteConfig({**base, "image_gen_render_attempts": 3}),
    )
    assert one == 240 + 120
    assert three == 3 * 240 + 2 * 3 + 120


# ---------------------------------------------------------------------------
# 2. _resolve_node_timeout — the hook is a floor, never a ceiling
# ---------------------------------------------------------------------------


class _HookStage:
    name = "hooked"
    timeout_seconds = 300

    def __init__(self, required: int) -> None:
        self._required = required

    def resolve_timeout_seconds(self, site_config: Any) -> int:
        return self._required


def _cfg(explicit: int | None = None) -> Any:
    return SimpleNamespace(
        get=lambda k, d=None: explicit if (k == "timeout_seconds" and explicit) else d,
    )


def test_hook_raises_timeout_above_static_attribute() -> None:
    assert _resolve_node_timeout(_HookStage(603), _cfg(), None, "hooked") == 603


def test_hook_never_lowers_a_larger_configured_timeout() -> None:
    """One-directional floor: an operator may raise it, not cut it below."""
    assert _resolve_node_timeout(_HookStage(120), _cfg(900), None, "hooked") == 900


def test_stage_without_hook_keeps_configured_value() -> None:
    plain = SimpleNamespace(name="plain", timeout_seconds=42)
    assert _resolve_node_timeout(plain, _cfg(), None, "plain") == 42


def test_raising_hook_falls_back_instead_of_breaking_the_run() -> None:
    class _Bad:
        name = "bad"
        timeout_seconds = 55

        def resolve_timeout_seconds(self, site_config: Any) -> int:
            raise RuntimeError("settings unavailable")

    assert _resolve_node_timeout(_Bad(), _cfg(), None, "bad") == 55


# ---------------------------------------------------------------------------
# 3. A swallowed timeout must emit a durable finding
# ---------------------------------------------------------------------------


class _SlowStage:
    name = "slow_stage"
    timeout_seconds = 1
    halts_on_failure = False

    async def execute(self, context: Any, config: Any) -> StageResult:
        await asyncio.sleep(5)
        return StageResult(ok=True, detail="never", context_updates={"content": "x"})


async def test_swallowed_timeout_emits_finding() -> None:
    node = make_stage_node(_SlowStage(), pool=None, record_sink=[])
    p1, p2, p3 = _stage_node_patches()
    with p1, p2, p3, patch("utils.findings.emit_finding") as emit:
        out = await node({"task_id": "t-1"})

    assert out == {}, "non-halting timeout still yields no state updates"
    assert emit.call_count == 1
    kwargs = emit.call_args.kwargs
    assert kwargs["kind"] == "stage_failure_swallowed"
    assert kwargs["severity"] == "warn"
    assert kwargs["dedup_key"] == "stage-swallowed:slow_stage"
    assert kwargs["extra"]["stage"] == "slow_stage"
    assert kwargs["extra"]["task_id"] == "t-1"


async def test_clean_stage_emits_no_finding() -> None:
    class _Fine:
        name = "fine"
        timeout_seconds = 5
        halts_on_failure = False

        async def execute(self, context: Any, config: Any) -> StageResult:
            return StageResult(ok=True, detail="ok", context_updates={"content": "x"})

    node = make_stage_node(_Fine(), pool=None, record_sink=[])
    p1, p2, p3 = _stage_node_patches()
    with p1, p2, p3, patch("utils.findings.emit_finding") as emit:
        out = await node({"task_id": "t-2"})

    assert out["content"] == "x"
    emit.assert_not_called()


# ---------------------------------------------------------------------------
# 4 + 5. The outcome seam — atom_runs must stop recording the loss as "ok"
# ---------------------------------------------------------------------------


async def test_stage_runner_lifts_failure_onto_atom_outcome() -> None:
    runner = _make_stage_runner(_SlowStage(), fallback_pool=None)
    p1, p2, p3 = _stage_node_patches()
    with p1, p2, p3, patch("utils.findings.emit_finding"):
        out = await runner({"task_id": "t-3"})

    assert out["_atom_outcome"]["ok"] is False
    assert "timed out" in out["_atom_outcome"]["detail"]


async def test_wrap_atom_records_swallowed_failure_as_error() -> None:
    """The exact row shape from the incident, now recorded honestly.

    ``output_keys`` must stay empty (the reserved key is popped, so digests are
    unchanged for atoms that never set it) while ``ok`` flips to False.
    """
    async def _run_fn(_state: dict[str, Any]) -> dict[str, Any]:
        return {"_atom_outcome": {"ok": False, "detail": "timed out after 300s"}}

    sink: list = []
    node = _wrap_atom(_run_fn, "stage.source_featured_image", "sfi", sink)
    out = await node({"task_id": "t-4"}, None)

    assert out == {}, "reserved key must not leak into LangGraph state"
    assert len(sink) == 1
    record = sink[0]
    assert record.ok is False
    assert record.detail == "timed out after 300s"
    assert record.metrics["output_keys"] == []

    # atom_runs must now call this what it is.
    from services.atom_runs import _status_of

    assert _status_of(record) == "error"

    # But halts_on_failure=False still means the RUN continues: run-level ok is
    # `not any(r.halted for r in records)`, so an honest ok=False must not
    # start halting pipelines that previously completed.
    assert record.halted is False


async def test_wrap_atom_still_reports_ok_without_the_reserved_key() -> None:
    """Back-compat: atoms that never set _atom_outcome are unchanged."""
    async def _run_fn(_state: dict[str, Any]) -> dict[str, Any]:
        return {"content": "body"}

    sink: list = []
    node = _wrap_atom(_run_fn, "content.demo", "demo", sink)
    out = await node({"task_id": "t-5"}, None)

    assert out == {"content": "body"}
    assert sink[0].ok is True
    assert sink[0].metrics["output_keys"] == ["content"]
