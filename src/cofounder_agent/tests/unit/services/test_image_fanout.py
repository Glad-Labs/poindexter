"""image_fanout — graphs, winner selection, judge fail-soft, outcome rows.

Everything HTTP/GPU is mocked: candidate renders patch ``_render_via_comfy``,
the judge patches ``_score_candidate``, and the gpu unload rung is stubbed —
on the operator-box CI runner a real :8188/:9836 is reachable, so an
unmocked path here would silently exercise live services (the known
silent-test-network-hazard shape).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services import image_fanout
from services.image_fanout import (
    FanoutCandidate,
    _parse_score,
    _pick_winner,
    fanout_enabled,
    qwen_graph,
    run_featured_fanout,
    schnell_graph,
)


class _FakeSiteConfig:
    def __init__(self, values: dict | None = None) -> None:
        self._values = values or {}

    def get(self, key, default=None):
        val = self._values.get(key)
        return default if val in (None, "") else val


class TestGraphs:
    def test_schnell_shape(self):
        g = schnell_graph(
            prompt="a console", seed=7, width=1024, height=1024,
            ckpt="s.safetensors", steps=4, cfg=1.0,
        )
        assert g["1"]["inputs"]["ckpt_name"] == "s.safetensors"
        # Positive prompt carries the textless clause; negative is empty
        # (schnell ignores negative guidance at cfg 1).
        assert "a console" in g["2"]["inputs"]["text"]
        assert "no text" in g["2"]["inputs"]["text"]
        assert g["3"]["inputs"]["text"] == ""
        assert g["5"]["inputs"]["steps"] == 4
        assert g["5"]["inputs"]["cfg"] == 1.0
        # VAE comes from the checkpoint bundle, not a separate loader.
        assert g["6"]["inputs"]["vae"] == ["1", 2]

    def test_qwen_shape(self):
        g = qwen_graph(
            prompt="a console", negative="ugly", seed=7,
            width=1024, height=1024, steps=20, cfg=2.5, shift=3.1,
        )
        assert g["4"]["class_type"] == "ModelSamplingAuraFlow"
        assert g["4"]["inputs"]["shift"] == 3.1
        # The KSampler must read the shift-wrapped model, not the bare UNET.
        assert g["8"]["inputs"]["model"] == ["4", 0]
        assert g["6"]["inputs"]["text"] == "ugly"
        assert g["2"]["inputs"]["type"] == "qwen_image"


class TestParseScore:
    def test_plain_json(self):
        assert _parse_score('{"score": 82, "reason": "clean"}') == (82.0, "clean")

    def test_fenced_json(self):
        s, r = _parse_score('```json\n{"score": 40, "reason": "text"}\n```')
        assert s == 40.0

    def test_garbage_is_none(self):
        s, r = _parse_score("I think this image is nice")
        assert s is None


class TestPickWinner:
    def _c(self, name, score):
        return FanoutCandidate(name=name, path=f"/tmp/{name}.png", score=score)

    def test_argmax_wins(self):
        cands = [self._c("zimage", 60.0), self._c("qwen", 85.0)]
        assert _pick_winner(cands, ["zimage", "schnell", "qwen"]).name == "qwen"

    def test_tie_resolves_by_priority(self):
        cands = [self._c("qwen", 80.0), self._c("zimage", 80.0)]
        assert _pick_winner(cands, ["zimage", "schnell", "qwen"]).name == "zimage"

    def test_all_none_falls_open_to_priority(self):
        # Judge down → today's production model ships, exactly as before.
        cands = [self._c("qwen", None), self._c("zimage", None)]
        assert _pick_winner(cands, ["zimage", "schnell", "qwen"]).name == "zimage"

    def test_scored_beats_unscored(self):
        cands = [self._c("zimage", None), self._c("schnell", 30.0)]
        assert _pick_winner(cands, ["zimage", "schnell", "qwen"]).name == "schnell"

    def test_unknown_name_ranks_last(self):
        cands = [self._c("mystery", None), self._c("zimage", None)]
        assert _pick_winner(cands, ["zimage"]).name == "zimage"


class TestFanoutEnabled:
    def test_default_off(self):
        assert fanout_enabled(_FakeSiteConfig()) is False

    def test_string_true(self):
        assert fanout_enabled(
            _FakeSiteConfig({"image_fanout_enabled": "true"})) is True

    def test_none_site_config_off(self):
        assert fanout_enabled(None) is False


def _pool():
    pool = AsyncMock()
    pool.execute = AsyncMock()
    return pool


def _sc(**over):
    base = {"image_fanout_enabled": "true"}
    base.update(over)
    return _FakeSiteConfig(base)


@pytest.fixture
def zimage_file(tmp_path):
    f = tmp_path / "zimage.png"
    f.write_bytes(b"PNG")
    return str(f)


@pytest.fixture
def no_gpu_unload():
    with patch("services.gpu_scheduler.gpu._unload_image_gen", AsyncMock()) as m:
        yield m


class TestRunFeaturedFanout:
    @pytest.mark.asyncio
    async def test_no_candidates_returns_none(self, no_gpu_unload):
        async def no_render(name, graph, **kw):
            return None, {"failure": "down"}

        with patch.object(image_fanout, "_render_via_comfy", no_render):
            out = await run_featured_fanout(
                prompt="p", negative="n", zimage_path=None, zimage_meta=None,
                site_config=_sc(), pool=_pool(), task_id="t1",
            )
        assert out is None

    @pytest.mark.asyncio
    async def test_zimage_only_wins_without_judging(
        self, zimage_file, no_gpu_unload,
    ):
        """Comfy candidates all fail → single candidate → no judge calls,
        zimage ships (current behaviour preserved under partial outage)."""
        async def no_render(name, graph, **kw):
            return None, {"failure": "down"}

        judge = AsyncMock()
        pool = _pool()
        with patch.object(image_fanout, "_render_via_comfy", no_render), \
             patch.object(image_fanout, "_score_candidate", judge):
            out = await run_featured_fanout(
                prompt="p", negative="n", zimage_path=zimage_file,
                zimage_meta={"model": "z_image_turbo"},
                site_config=_sc(), pool=pool, task_id="t1",
            )
        assert out is not None
        path, meta = out
        assert path == zimage_file
        assert meta["fanout"]["winner"] == "zimage"
        judge.assert_not_awaited()
        pool.execute.assert_awaited_once()  # outcome row still recorded

    @pytest.mark.asyncio
    async def test_judged_winner_ships_and_outcome_row_written(
        self, zimage_file, tmp_path, no_gpu_unload,
    ):
        qwen_file = tmp_path / "qwen.png"
        qwen_file.write_bytes(b"PNG2")

        async def render(name, graph, **kw):
            if name == "qwen":
                return str(qwen_file), {"model": "qwen", "elapsed_s": 24.0}
            return None, {"failure": "skip"}

        async def score(candidate, **kw):
            candidate.score = 90.0 if candidate.name == "qwen" else 55.0
            candidate.reason = "judged"

        pool = _pool()
        with patch.object(image_fanout, "_render_via_comfy", render), \
             patch.object(image_fanout, "_score_candidate", score):
            out = await run_featured_fanout(
                prompt="brief text", negative="n", zimage_path=zimage_file,
                zimage_meta={}, site_config=_sc(), pool=pool, task_id="t1",
            )
        assert out is not None
        path, meta = out
        assert path == str(qwen_file)
        assert meta["fanout"]["winner"] == "qwen"
        assert meta["fanout"]["judge_ran"] is True
        assert meta["fanout"]["scores"]["zimage"] == 55.0

        row = pool.execute.await_args
        assert row.args[1] == "image_fanout_judged"
        details = json.loads(row.args[4])
        assert details["winner"] == "qwen"
        assert {c["name"] for c in details["candidates"]} == {"zimage", "qwen"}

    @pytest.mark.asyncio
    async def test_judge_down_falls_open_to_priority(
        self, zimage_file, tmp_path, no_gpu_unload,
    ):
        schnell_file = tmp_path / "schnell.png"
        schnell_file.write_bytes(b"PNG3")

        async def render(name, graph, **kw):
            if name == "schnell":
                return str(schnell_file), {"model": "schnell"}
            return None, {"failure": "skip"}

        async def score(candidate, **kw):
            candidate.reason = "judge call failed"  # score stays None

        with patch.object(image_fanout, "_render_via_comfy", render), \
             patch.object(image_fanout, "_score_candidate", score):
            out = await run_featured_fanout(
                prompt="p", negative="n", zimage_path=zimage_file,
                zimage_meta={}, site_config=_sc(), pool=_pool(), task_id="t1",
            )
        assert out is not None
        _, meta = out
        # Priority default is zimage-first: a downed judge reproduces
        # today's single-model behaviour.
        assert meta["fanout"]["winner"] == "zimage"
        assert meta["fanout"]["judge_ran"] is False

    @pytest.mark.asyncio
    async def test_image_gen_unloaded_before_comfy_candidates(
        self, zimage_file, no_gpu_unload,
    ):
        async def no_render(name, graph, **kw):
            return None, {"failure": "down"}

        with patch.object(image_fanout, "_render_via_comfy", no_render):
            await run_featured_fanout(
                prompt="p", negative="n", zimage_path=zimage_file,
                zimage_meta={}, site_config=_sc(), pool=_pool(), task_id="t1",
            )
        no_gpu_unload.assert_awaited_once_with(hard=True)

    @pytest.mark.asyncio
    async def test_candidates_setting_filters(self, zimage_file, no_gpu_unload):
        """candidates='zimage' → no comfy renders, no unload needed."""
        render = AsyncMock()
        with patch.object(image_fanout, "_render_via_comfy", render):
            out = await run_featured_fanout(
                prompt="p", negative="n", zimage_path=zimage_file,
                zimage_meta={},
                site_config=_sc(image_fanout_candidates="zimage"),
                pool=_pool(), task_id="t1",
            )
        assert out is not None
        render.assert_not_awaited()
        no_gpu_unload.assert_not_awaited()


class TestAuditSchema:
    def test_registered_and_validates_producer_shape(self):
        from services.audit_event_schemas import (
            EVENT_SCHEMAS,
            validate_event_details,
        )

        assert "image_fanout_judged" in EVENT_SCHEMAS
        out = validate_event_details("image_fanout_judged", {
            "winner": "qwen",
            "judge_ran": True,
            "brief": "b",
            "candidates": [
                {"name": "qwen", "score": 90.0, "reason": "r", "elapsed_s": 24.0},
                {"name": "zimage", "score": None, "reason": "", "elapsed_s": None},
            ],
        })
        assert out["winner"] == "qwen"
