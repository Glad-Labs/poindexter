"""image_fanout — graphs, winner selection, judge fail-soft, outcome rows.

Everything HTTP/GPU is mocked: candidate renders patch ``_render_via_comfy``,
the judge patches ``_score_candidate``, and the gpu unload rung is stubbed —
on the operator-box CI runner a real :8188/:9836 is reachable, so an
unmocked path here would silently exercise live services (the known
silent-test-network-hazard shape).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import image_fanout
from services.image_fanout import (
    _COMFY_CANDIDATES,
    FanoutCandidate,
    _build_candidate_graph,
    _parse_score,
    _pick_winner,
    fanout_enabled,
    klein_graph,
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

    def test_klein_shape(self):
        g = klein_graph(
            prompt="a console", seed=7, width=1024, height=1024,
            model="k.safetensors", text_encoder="q3.safetensors",
            vae="v.safetensors", steps=4, cfg=1.0,
        )
        assert g["1"]["inputs"]["unet_name"] == "k.safetensors"
        # FLUX.2's text encoder is its own file loaded as type "flux2" —
        # not the checkpoint-bundled CLIP schnell uses.
        assert g["2"]["inputs"]["type"] == "flux2"
        assert g["2"]["inputs"]["clip_name"] == "q3.safetensors"
        assert "a console" in g["4"]["inputs"]["text"]
        assert "no text" in g["4"]["inputs"]["text"]
        # Negative = zeroed positive, NOT a second (8GB-encoder) text pass.
        assert g["5"]["class_type"] == "ConditioningZeroOut"
        assert g["5"]["inputs"]["conditioning"] == ["4", 0]
        assert g["6"]["inputs"]["negative"] == ["5", 0]
        assert g["6"]["inputs"]["cfg"] == 1.0
        # Sampling is the SamplerCustomAdvanced path: Flux2Scheduler emits
        # the sigmas, so steps/resolution live there and NOT on a KSampler.
        assert g["8"]["class_type"] == "Flux2Scheduler"
        assert g["8"]["inputs"]["steps"] == 4
        assert g["8"]["inputs"]["width"] == 1024
        assert g["11"]["inputs"]["sigmas"] == ["8", 0]
        assert g["11"]["inputs"]["guider"] == ["6", 0]
        assert g["10"]["class_type"] == "EmptyFlux2LatentImage"
        # Decode reads the sampler's slot 0, and the VAE is its own loader.
        assert g["12"]["inputs"]["samples"] == ["11", 0]
        assert g["12"]["inputs"]["vae"] == ["3", 0]
        assert not any(
            n["class_type"] == "KSampler" for n in g.values()
        ), "FLUX.2 cannot sample through KSampler — it needs SIGMAS"

    def test_every_comfy_candidate_builds_a_graph(self):
        """The wanted-filter and the graph dispatch are two places that must
        agree. A name in ``_COMFY_CANDIDATES`` with no builder branch would
        be silently skipped at render time (``graph is None`` → ``continue``),
        so the candidate would just never appear — no error, no audit row."""
        for name in _COMFY_CANDIDATES:
            g = _build_candidate_graph(
                name, prompt="p", negative="n", seed=1,
                site_config=_FakeSiteConfig(),
            )
            assert g is not None, f"{name} has no graph builder"
            assert any(
                n["class_type"] == "SaveImage" for n in g.values()
            ), f"{name} graph never saves an image"

    def test_default_candidates_are_all_renderable(self):
        """Every name in the shipped default list is either the
        stage-rendered zimage or has a ComfyUI builder — a typo'd default
        would drop a candidate silently."""
        from services.image_fanout import _DEFAULT_CANDIDATES, _DEFAULT_PRIORITY

        wanted = [n.strip() for n in _DEFAULT_CANDIDATES.split(",")]
        assert set(wanted) <= {"zimage", *_COMFY_CANDIDATES}
        # Priority must cover every candidate, or an unlisted one ranks last
        # on ties and in the judge-down fall-open.
        assert set(wanted) == {n.strip() for n in _DEFAULT_PRIORITY.split(",")}
        assert wanted[0] == "zimage", "judge-down must reproduce production"


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
    # Stubs BOTH gpu rungs the fan-out touches. The post-fanout comfyui free
    # (2026-08-24 fix) otherwise fires a REAL GET /queue at the live sidecar
    # on the operator-box runner — the egress guard (poindexter#1011) caught
    # exactly that when this fixture only stubbed image-gen. Tests asserting
    # either rung re-patch locally; the innermost patch wins.
    with patch("services.gpu_scheduler.gpu._unload_image_gen", AsyncMock()) as m, \
         patch("services.gpu_scheduler.gpu._unload_comfyui", AsyncMock()):
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
    async def test_klein_renders_and_can_win(
        self, zimage_file, tmp_path, no_gpu_unload,
    ):
        """klein is reachable end-to-end on the default candidate list, and
        its render order sits between schnell and qwen (ascending VRAM)."""
        klein_file = tmp_path / "klein.png"
        klein_file.write_bytes(b"PNG4")
        rendered: list[str] = []

        async def render(name, graph, **kw):
            rendered.append(name)
            if name == "klein":
                return str(klein_file), {"model": "klein", "elapsed_s": 1.0}
            return None, {"failure": "skip"}

        async def score(candidate, **kw):
            candidate.score = 95.0 if candidate.name == "klein" else 50.0

        pool = _pool()
        with patch.object(image_fanout, "_render_via_comfy", render), \
             patch.object(image_fanout, "_score_candidate", score):
            out = await run_featured_fanout(
                prompt="p", negative="n", zimage_path=zimage_file,
                zimage_meta={}, site_config=_sc(), pool=pool, task_id="t1",
            )
        assert out is not None
        path, meta = out
        assert path == str(klein_file)
        assert meta["fanout"]["winner"] == "klein"
        assert rendered == ["schnell", "klein", "qwen"]
        details = json.loads(pool.execute.await_args.args[4])
        assert {c["name"] for c in details["candidates"]} == {"zimage", "klein"}

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


class TestVramFixAndDatasetCompleteness:
    """2026-08-24 fixes: post-fanout ComfyUI free (the 8-day silent-503
    starvation), judge token budget as a setting, and zimage-absence reasons
    in the audit row (24/32 early rows recorded absence with no why)."""

    @pytest.mark.asyncio
    async def test_comfy_freed_after_fanout_with_comfy_candidates(
        self, zimage_file, tmp_path, no_gpu_unload,
    ):
        qwen_file = tmp_path / "q.png"
        qwen_file.write_bytes(b"P")

        async def render(name, graph, **kw):
            return (str(qwen_file), {"model": name}) if name == "qwen" \
                else (None, {"failure": "skip"})

        async def score(candidate, **kw):
            candidate.score = 50.0

        free = AsyncMock()
        with patch.object(image_fanout, "_render_via_comfy", render), \
             patch.object(image_fanout, "_score_candidate", score), \
             patch("services.gpu_scheduler.gpu._unload_comfyui", free):
            out = await run_featured_fanout(
                prompt="p", negative="n", zimage_path=zimage_file,
                zimage_meta={}, site_config=_sc(), pool=_pool(), task_id="t",
            )
        assert out is not None
        free.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_comfy_candidates_no_free(self, zimage_file, no_gpu_unload):
        free = AsyncMock()
        with patch("services.gpu_scheduler.gpu._unload_comfyui", free):
            await run_featured_fanout(
                prompt="p", negative="n", zimage_path=zimage_file,
                zimage_meta={},
                site_config=_sc(image_fanout_candidates="zimage"),
                pool=_pool(), task_id="t",
            )
        free.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_zimage_absence_reason_recorded(
        self, tmp_path, no_gpu_unload,
    ):
        schnell_file = tmp_path / "s.png"
        schnell_file.write_bytes(b"P")

        async def render(name, graph, **kw):
            return (str(schnell_file), {"model": name}) if name == "schnell" \
                else (None, {"failure": "skip"})

        pool = _pool()
        with patch.object(image_fanout, "_render_via_comfy", render), \
             patch("services.gpu_scheduler.gpu._unload_comfyui", AsyncMock()):
            await run_featured_fanout(
                prompt="p", negative="n", zimage_path=None,
                zimage_meta={"transient": True, "failure": "HTTP 503"},
                site_config=_sc(), pool=pool, task_id="t",
            )
        details = json.loads(pool.execute.await_args.args[4])
        assert details["zimage_absent_reason"] == "HTTP 503"

    @pytest.mark.asyncio
    async def test_judge_token_budget_reads_setting(self, tmp_path):
        cand = FanoutCandidate(name="qwen", path=str(tmp_path / "c.png"))
        (tmp_path / "c.png").write_bytes(b"P")
        captured = {}

        async def fake_dispatch(pool, messages, model, **kw):
            captured.update(kw)
            return MagicMock(text='{"score": 70, "reason": "ok"}')

        prompt_mgr = MagicMock()
        prompt_mgr.get_prompt = MagicMock(return_value="judge prompt")
        with patch("services.llm_providers.dispatcher.dispatch_complete",
                   fake_dispatch), \
             patch("services.prompt_manager.get_prompt_manager",
                   MagicMock(return_value=prompt_mgr)):
            await image_fanout._score_candidate(
                cand, brief="b",
                site_config=_FakeSiteConfig({
                    "qa_vision_model": "qwen3-vl:30b",
                    "image_fanout_judge_max_tokens": "3000",
                }),
                pool=MagicMock(),
            )
        assert captured["max_tokens"] == 3000
        assert cand.score == 70.0
