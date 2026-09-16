"""A still-executing ComfyUI prompt is progress.

2026-09-16 22:31Z, third presenter render: a single S2V clip runs 20+
minutes inside ONE graph node; the stuck-flow probe saw "No graph-node
progress for 21m (stall threshold 20m)" and cancelled a healthy render
mid-clip. The poll loop now heartbeats upstream every ~30 s while the
prompt executes, and the render atom stamps last_progress_at from it.
"""
from __future__ import annotations

import itertools
from unittest.mock import AsyncMock

import pytest

from poindexter.services.video_providers import comfyui as cf


class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload

    def json(self):
        return self._p


class _Client:
    """Empty history for `pending` polls, then a finished video."""

    def __init__(self, pending: int):
        self.calls = 0
        self.pending = pending

    async def get(self, url):
        self.calls += 1
        if self.calls <= self.pending:
            return _Resp({})
        return _Resp({"p1": {"status": {"status_str": "success"}, "outputs": {"9": {"video": [{"filename": "out.mp4"}]}}}})


@pytest.mark.asyncio
async def test_poll_heartbeats_every_thirty_seconds_while_the_prompt_runs(monkeypatch):
    monkeypatch.setattr(cf.asyncio, "sleep", AsyncMock())
    # each loop tick advances the clock 10 s: 6 pending ticks = 60 s → 2 beats
    clock = itertools.count(0.0, 10.0)
    monkeypatch.setattr(cf.time, "monotonic", lambda: next(clock))
    beat = AsyncMock()
    provider = cf.ComfyUIProvider()
    filename, reason = await provider._poll(_Client(pending=6), "http://c", "p1", 900.0, heartbeat_cb=beat)
    assert filename == "out.mp4" and reason == ""
    assert beat.await_count >= 2


@pytest.mark.asyncio
async def test_poll_without_heartbeat_is_unchanged(monkeypatch):
    monkeypatch.setattr(cf.asyncio, "sleep", AsyncMock())
    provider = cf.ComfyUIProvider()
    filename, reason = await provider._poll(_Client(pending=1), "http://c", "p1", 900.0)
    assert filename == "out.mp4"


@pytest.mark.asyncio
async def test_a_failing_heartbeat_never_disturbs_the_poll(monkeypatch):
    monkeypatch.setattr(cf.asyncio, "sleep", AsyncMock())
    clock = itertools.count(0.0, 40.0)
    monkeypatch.setattr(cf.time, "monotonic", lambda: next(clock))
    beat = AsyncMock(side_effect=RuntimeError("db gone"))
    provider = cf.ComfyUIProvider()
    filename, _ = await provider._poll(_Client(pending=2), "http://c", "p1", 900.0, heartbeat_cb=beat)
    assert filename == "out.mp4"
    assert beat.await_count >= 1


def test_render_shot_list_and_the_render_atom_carry_the_heartbeat():
    import inspect

    from poindexter.modules.content.atoms import _media_render
    from poindexter.services.video_renderers import shot_list_renderer as slr

    assert "heartbeat_cb" in inspect.signature(slr.render_shot_list).parameters
    assert "heartbeat_cb" in inspect.signature(slr._render_generative_clip).parameters
    assert "heartbeat_cb" in inspect.signature(slr._render_presenter_clip).parameters
    assert "heartbeat_cb" in inspect.signature(slr._animate_hero).parameters
    src = inspect.getsource(_media_render.render_from_state)
    assert "heartbeat_cb=_heartbeat" in src and "_mark_progress(pool, task_id)" in src


@pytest.mark.asyncio
async def test_generative_clip_hands_the_heartbeat_to_the_provider(monkeypatch):
    from poindexter.services.video_providers import wan2_1
    from poindexter.services.video_renderers import shot_list_renderer as slr

    seen: dict = {}

    class _Prov:
        last_error = ""

        def __init__(self, *a, **k): ...

        async def fetch(self, prompt, config):
            seen.update(config)
            return []

    monkeypatch.setattr(wan2_1, "Wan21Provider", _Prov)
    monkeypatch.setattr(slr, "_clear_image_gen_for_hero", AsyncMock())
    beat = AsyncMock()
    await slr._render_generative_clip(
        prompt="p", output_path="/tmp/x.mp4", image_path=None, duration_s=4,
        site_config=None, heartbeat_cb=beat,
    )
    assert seen.get("_heartbeat_cb") is beat
