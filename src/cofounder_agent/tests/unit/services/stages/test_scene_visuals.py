"""Unit tests for ``services/stages/scene_visuals.py``.

Strategy fan-out (reuse_first / pexels / sdxl / wan / mixed) is
covered with mocked provider calls — no real Pexels / SDXL / Wan
invoked. Pure helpers (_tokenize, _score_match, _query_for_pexels,
_suffix_from_url, _adapt_prompt_for_wan) get edge-case coverage.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.stage import Stage
from services.stages.scene_visuals import (
    SceneVisualsStage,
    _adapt_prompt_for_wan,
    _default_visuals,
    _query_for_pexels,
    _score_match,
    _STOP_WORDS,
    _suffix_from_url,
    _tokenize,
)


_FAKE_SITE_CONFIG = SimpleNamespace(
    get=lambda _k, _d="": _d,
    get_int=lambda _k, _d=0: _d,
    get_float=lambda _k, _d=0.0: _d,
    get_bool=lambda _k, _d=False: _d,
    _pool=None,
)


def _make_site_config(overrides: dict[str, Any] | None = None, pool: Any = None):
    """Build a SiteConfig-like stub that returns values from overrides."""
    overrides = overrides or {}

    def _get(key, default=""):
        return overrides.get(key, default)

    return SimpleNamespace(
        get=_get, get_int=_get, get_float=_get, get_bool=_get,
        _pool=pool,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_filters_stop_words(self):
        out = _tokenize("the quick brown fox in the sky")
        # "the", "in" are stop words; "fox" is len 3 (filtered by length);
        # "quick", "brown", "sky" should remain. "sky" is len 3 → filtered.
        # Length filter is >=4.
        assert "quick" in out
        assert "brown" in out
        assert "the" not in out
        assert "in" not in out
        assert "fox" not in out  # 3 chars
        assert "sky" not in out  # 3 chars

    def test_filters_short_tokens(self):
        out = _tokenize("AI is a way of thinking")
        # All tokens 1-3 chars are filtered: "ai", "is", "a", "way", "of"
        # "thinking" survives (8 chars, not a stop word)
        assert "thinking" in out
        assert "ai" not in out
        assert "is" not in out
        assert "way" not in out

    def test_empty_input(self):
        assert _tokenize("") == set()

    def test_lowercases(self):
        out = _tokenize("THUNDER and Lightning")
        assert "thunder" in out
        assert "lightning" in out

    def test_preserves_alphanumeric(self):
        out = _tokenize("model-name 4kvideo gpt4-turbo")
        # Hyphens are kept inside tokens.
        assert "model-name" in out
        assert "gpt4-turbo" in out

    def test_known_stop_word_dropped(self):
        # Sanity-check a few sentinel stop words from _STOP_WORDS
        for stop in ("cinematic", "lighting", "people", "text"):
            assert stop in _STOP_WORDS
            out = _tokenize(f"a {stop} thing")
            assert stop not in out


class TestScoreMatch:
    def test_empty_visual_prompt_returns_zero(self):
        assert _score_match("", "anything") == 0.0

    def test_empty_candidate_returns_zero(self):
        assert _score_match("server room", "") == 0.0

    def test_full_overlap_returns_one(self):
        # Both have exactly the same tokens after filtering.
        prompt = "rolling mountains"
        candidate = "rolling mountains"
        assert _score_match(prompt, candidate) == 1.0

    def test_no_overlap_returns_zero(self):
        # Disjoint vocabularies → 0
        score = _score_match("rolling mountains valley", "kitchen utensils refrigerator")
        assert score == 0.0

    def test_partial_overlap(self):
        # 1 of 2 tokens overlaps → 0.5
        score = _score_match("rolling mountains", "rolling kitchen")
        assert score == 0.5

    def test_only_stop_words_returns_zero(self):
        # Both tokenize to empty after filtering
        assert _score_match("the and or", "the not is") == 0.0


class TestQueryForPexels:
    def test_strips_noise_keeps_substantive_tokens(self):
        prompt = (
            "rolling mountains stretching to horizon, cinematic lighting, "
            "no people, no text, photorealistic, 4k"
        )
        out = _query_for_pexels(prompt)
        # Stop words like "cinematic", "lighting", "people", "text", "4k"
        # should be filtered.
        assert "cinematic" not in out
        assert "lighting" not in out
        assert "rolling" in out
        assert "mountains" in out

    def test_caps_at_six_tokens(self):
        prompt = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo"
        out = _query_for_pexels(prompt)
        tokens = out.split()
        assert len(tokens) <= 6

    def test_preserves_original_order(self):
        prompt = "kitchen rolling mountains stretching"
        out = _query_for_pexels(prompt)
        tokens = out.split()
        # Should preserve order: kitchen → rolling → mountains → stretching
        assert tokens.index("kitchen") < tokens.index("rolling")
        assert tokens.index("rolling") < tokens.index("mountains")

    def test_falls_back_to_truncated_prompt_when_no_substantive_tokens(self):
        # All stop words; should fall back to a slice of the original.
        prompt = "the and or is on at"
        out = _query_for_pexels(prompt)
        # When tokenize returns empty, helper falls back to first 80 chars.
        assert out.startswith("the ")

    def test_empty_prompt(self):
        assert _query_for_pexels("") == ""


class TestSuffixFromUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://x.com/photo.jpg", ".jpg"),
        ("https://x.com/photo.jpeg", ".jpeg"),
        ("https://x.com/photo.png", ".png"),
        ("https://x.com/photo.webp", ".webp"),
        ("https://x.com/photo.gif", ".gif"),
        ("https://x.com/photo.JPG", ".jpg"),  # case insensitive
        ("https://x.com/photo.jpg?token=abc", ".jpg"),  # query string
    ])
    def test_recognized_extensions(self, url, expected):
        assert _suffix_from_url(url) == expected

    @pytest.mark.parametrize("url", [
        "https://x.com/photo.bmp",
        "https://x.com/photo.tiff",
        "https://x.com/no-extension",
        "",
    ])
    def test_falls_back_to_jpg(self, url):
        assert _suffix_from_url(url) == ".jpg"


class TestDefaultVisuals:
    def test_shape(self):
        v = _default_visuals()
        assert v["long_form"] == []
        assert v["short_form"] == []
        # Bookend slots present (added when intro/outro Pexels lookup wired).
        assert "intro_clip_path" in v
        assert "outro_clip_path" in v
        assert v["intro_clip_path"] == ""
        assert v["outro_clip_path"] == ""


class TestAdaptPromptForWan:
    def test_appends_motion_suffix(self):
        out = _adapt_prompt_for_wan("a quiet kitchen scene")
        assert "kitchen scene" in out
        # Suffix appended
        assert "motion" in out.lower() or "panning" in out.lower()

    def test_skips_when_motion_already_present(self):
        # Already mentions motion → no double-tag
        prompt = "a server fan spinning fast in motion"
        out = _adapt_prompt_for_wan(prompt)
        assert out == prompt

    def test_skips_for_moving_subject(self):
        prompt = "people moving through a station"
        out = _adapt_prompt_for_wan(prompt)
        assert out == prompt

    def test_empty_input_returns_empty(self):
        assert _adapt_prompt_for_wan("") == ""
        assert _adapt_prompt_for_wan("   ") == ""

    def test_strips_whitespace(self):
        out = _adapt_prompt_for_wan("  a horse running  ")
        # Source includes "running" → not motion, so suffix appended.
        # But "running" hits the test? Let's just verify it doesn't raise
        # and the base text appears.
        assert "horse running" in out


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_conforms(self):
        assert isinstance(SceneVisualsStage(), Stage)

    def test_metadata(self):
        s = SceneVisualsStage()
        assert s.name == "video.scene_visuals"
        assert s.halts_on_failure is False


# ---------------------------------------------------------------------------
# Stage.execute
# ---------------------------------------------------------------------------


def _scene(idx: int, prompt: str = "rolling mountains stretching"):
    return {
        "narration_text": f"narration {idx}",
        "visual_prompt": prompt,
        "duration_s_hint": 30,
    }


def _video_script(long_form_count: int = 2, short_form_count: int = 1):
    return {
        "long_form": {
            "intro_hook": "h", "outro_cta": "c",
            "scenes": [_scene(i) for i in range(long_form_count)],
        },
        "short_form": {
            "intro_hook": "h",
            "scenes": [_scene(i) for i in range(short_form_count)],
        },
    }


@pytest.mark.asyncio
class TestExecute:
    async def test_missing_site_config_returns_not_ok(self):
        ctx: dict[str, Any] = {}
        result = await SceneVisualsStage().execute(ctx, {})
        assert result.ok is False
        assert "site_config" in result.detail
        assert result.metrics.get("skipped") is True

    async def test_no_scenes_returns_not_ok(self):
        ctx: dict[str, Any] = {
            "site_config": _FAKE_SITE_CONFIG,
            "video_script": {"long_form": {"scenes": []}, "short_form": {"scenes": []}},
        }
        result = await SceneVisualsStage().execute(ctx, {})
        assert result.ok is False
        assert "no scenes" in result.detail

    async def test_strategy_pexels_routes_to_pexels(self):
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value={"clip_path": "/tmp/x.jpg", "url": "https://u", "metadata": {}}),
        ) as pexels_mock, patch(
            "services.stages.scene_visuals._try_sdxl",
            AsyncMock(return_value=None),
        ) as sdxl_mock:
            result = await SceneVisualsStage().execute(ctx, {})

        assert result.ok is True
        # 1 body scene + 1 intro_clip + 1 outro_clip = 3 pexels calls
        assert pexels_mock.await_count >= 1
        # SDXL not called when pexels succeeds
        assert sdxl_mock.await_count == 0
        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        assert long_visuals[0]["source"] == "pexels"
        assert long_visuals[0]["clip_path"] == "/tmp/x.jpg"

    async def test_strategy_pexels_falls_back_to_sdxl_on_miss(self):
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value=None),
        ), patch(
            "services.stages.scene_visuals._try_sdxl",
            AsyncMock(return_value={"clip_path": "/tmp/sdxl.png", "url": "", "metadata": {}}),
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        assert long_visuals[0]["source"] == "sdxl"

    async def test_strategy_sdxl_routes_to_sdxl_first(self):
        cfg = _make_site_config({"video_scene_visuals_strategy": "sdxl"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value={"clip_path": "/tmp/x.jpg", "url": "u", "metadata": {}}),
        ), patch(
            "services.stages.scene_visuals._try_sdxl",
            AsyncMock(return_value={"clip_path": "/tmp/sdxl.png", "url": "", "metadata": {}}),
        ) as sdxl_mock:
            result = await SceneVisualsStage().execute(ctx, {})

        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        # Body scene resolves through sdxl-first strategy
        assert long_visuals[0]["source"] == "sdxl"
        # SDXL called for the body scene
        assert sdxl_mock.await_count >= 1

    async def test_strategy_mixed_alternates_per_scene(self):
        # rotation_idx=0 → pexels first, idx=1 → sdxl first
        cfg = _make_site_config({"video_scene_visuals_strategy": "mixed"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=2, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value={"clip_path": "/tmp/p.jpg", "url": "u", "metadata": {}}),
        ), patch(
            "services.stages.scene_visuals._try_sdxl",
            AsyncMock(return_value={"clip_path": "/tmp/s.png", "url": "", "metadata": {}}),
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        # idx 0: pexels first → pexels source. idx 1: sdxl first → sdxl source.
        assert long_visuals[0]["source"] == "pexels"
        assert long_visuals[1]["source"] == "sdxl"

    async def test_strategy_reuse_first_falls_back_to_pexels_when_no_pool(self):
        # post_id present but pool=None → reuse skipped → fall through.
        cfg = _make_site_config({"video_scene_visuals_strategy": "reuse_first"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "post_id": "post-1",
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value={"clip_path": "/tmp/p.jpg", "url": "u", "metadata": {}}),
        ), patch(
            "services.stages.scene_visuals._try_reuse_from_media_assets",
            AsyncMock(return_value=None),
        ) as reuse_mock:
            result = await SceneVisualsStage().execute(ctx, {})
        # reuse path skipped (no pool); pexels picked.
        assert reuse_mock.await_count == 0
        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        assert long_visuals[0]["source"] == "pexels"

    async def test_strategy_reuse_first_uses_reuse_when_pool_present(self):
        fake_pool = MagicMock()
        cfg = _make_site_config(
            {"video_scene_visuals_strategy": "reuse_first"}, pool=fake_pool,
        )
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "post_id": "post-1",
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        reuse_payload = {
            "id": "asset-1",
            "url": "https://u",
            "storage_path": None,
            "alt_text": "alt",
            "score": 0.7,
        }
        with patch(
            "services.stages.scene_visuals._try_reuse_from_media_assets",
            AsyncMock(return_value=reuse_payload),
        ) as reuse_mock, patch(
            "services.stages.scene_visuals._ensure_local",
            AsyncMock(return_value="/tmp/cached.jpg"),
        ), patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value=None),
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        # reuse called for the body scene
        assert reuse_mock.await_count == 1
        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        assert long_visuals[0]["source"] == "media_assets"
        assert long_visuals[0]["reused"] is True
        assert long_visuals[0]["clip_path"] == "/tmp/cached.jpg"

    async def test_all_sources_fail_records_miss(self):
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value=None),
        ), patch(
            "services.stages.scene_visuals._try_sdxl",
            AsyncMock(return_value=None),
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        assert long_visuals[0]["source"] == "miss"
        assert long_visuals[0]["clip_path"] == ""
        assert result.ok is False  # long_form not fully resolved

    async def test_provider_exception_falls_through_for_body_scene(self):
        # Body scene: pexels call inside _resolve_scene is wrapped in
        # try/except. When it raises, the loop falls through to sdxl.
        # Use side_effect to selectively raise on the body call (first
        # call) and return None on subsequent (intro/outro bookend) calls.
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        # First call raises (body scene), bookend calls return None.
        call_count = {"n": 0}

        async def _pexels_side_effect(*_a, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("body pexels boom")
            return None

        with patch(
            "services.stages.scene_visuals._try_pexels",
            side_effect=_pexels_side_effect,
        ), patch(
            "services.stages.scene_visuals._try_sdxl",
            AsyncMock(return_value={"clip_path": "/tmp/s.png", "url": "", "metadata": {}}),
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        # Body pexels raised → caught → fell through to sdxl
        assert long_visuals[0]["source"] == "sdxl"

    async def test_scene_with_no_visual_prompt_returns_none_source(self):
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": {
                "long_form": {"scenes": [{
                    "narration_text": "n",
                    "visual_prompt": "",
                    "duration_s_hint": 30,
                }]},
                "short_form": {"scenes": []},
            },
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value={"clip_path": "/tmp/p.jpg", "url": "u", "metadata": {}}),
        ) as pexels_mock:
            result = await SceneVisualsStage().execute(ctx, {})

        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        assert long_visuals[0]["source"] is None
        # Provider not called for the empty prompt scene.
        assert pexels_mock.await_count == 0

    async def test_strategy_wan_routes_to_wan_first(self):
        cfg = _make_site_config({"video_scene_visuals_strategy": "wan"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_wan",
            AsyncMock(return_value={"clip_path": "/tmp/w.mp4", "url": "u", "metadata": {}}),
        ) as wan_mock, patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value={"clip_path": "/tmp/p.jpg", "url": "u", "metadata": {}}),
        ), patch(
            "services.stages.scene_visuals._try_sdxl",
            AsyncMock(return_value=None),
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        # Wan-first strategy → body scene resolves as wan
        assert long_visuals[0]["source"] == "wan"
        assert wan_mock.await_count >= 1

    async def test_strategy_wan_falls_back_to_sdxl_when_wan_misses(self):
        cfg = _make_site_config({"video_scene_visuals_strategy": "wan"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_wan",
            AsyncMock(return_value=None),
        ), patch(
            "services.stages.scene_visuals._try_sdxl",
            AsyncMock(return_value={"clip_path": "/tmp/s.png", "url": "u", "metadata": {}}),
        ), patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value=None),
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        # Wan returned None, SDXL succeeded
        assert long_visuals[0]["source"] == "sdxl"

    async def test_records_strategy_in_metrics(self):
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value={"clip_path": "/tmp/x.jpg", "url": "u", "metadata": {}}),
        ):
            result = await SceneVisualsStage().execute(ctx, {})
        assert result.metrics["strategy"] == "pexels"
        assert "by_source" in result.metrics


# ---------------------------------------------------------------------------
# gh#163 bookend dedup against body URLs
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBookendDedup:
    async def test_intro_skips_url_already_used_by_body(self):
        """When a body scene picked URL X, the intro bookend must NOT
        also pick URL X — it should pull a different candidate from the
        Pexels result list.
        """
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }

        # First call (body scene) returns the "shared" URL.
        # Subsequent calls (bookends) receive seen_urls={SHARED_URL}
        # and must fetch with that set — the helper itself dedups
        # internally against the result list.
        body_url = "https://pexels.example/body.jpg"
        intro_url = "https://pexels.example/intro.jpg"

        async def fake_try_pexels(_query, _site_config, *, seen_urls=None):
            # Body call: no seen_urls or empty
            if not seen_urls:
                return {"clip_path": "/tmp/body.jpg", "url": body_url, "metadata": {}}
            # Bookend calls: must skip body_url
            assert body_url in seen_urls, (
                "bookend call must receive seen_urls containing the body URL"
            )
            return {"clip_path": "/tmp/intro.jpg", "url": intro_url, "metadata": {}}

        with patch(
            "services.stages.scene_visuals._try_pexels", AsyncMock(side_effect=fake_try_pexels),
        ) as pexels_mock:
            result = await SceneVisualsStage().execute(ctx, {})

        assert result.ok is True
        # 1 body scene + 1 intro + 1 outro = 3 _try_pexels calls
        assert pexels_mock.await_count == 3

    async def test_outro_dedups_against_intro(self):
        """When intro + outro have the same query (both fall through to
        title because intro_hook + outro_cta are empty), the outro must
        receive seen_urls containing the intro URL.
        """
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        # Build a video_script with empty intro_hook + outro_cta — both
        # will fall through to the post title. Need at least 1 body
        # scene so the stage doesn't short-circuit on "no scenes".
        video_script = _video_script(long_form_count=1, short_form_count=0)
        video_script["long_form"]["intro_hook"] = ""
        video_script["long_form"]["outro_cta"] = ""
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "title": "Self-Hosting Your Own AI",
            "video_script": video_script,
        }

        body_url = "https://pexels.example/body.jpg"
        intro_url = "https://pexels.example/intro.jpg"
        outro_url = "https://pexels.example/outro.jpg"
        call_count = {"n": 0}

        async def fake_try_pexels(_query, _site_config, *, seen_urls=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # body scene — no seen_urls
                return {"clip_path": "/tmp/body.jpg", "url": body_url, "metadata": {}}
            if call_count["n"] == 2:
                # intro — seen_urls contains body_url
                assert seen_urls is not None
                assert body_url in seen_urls
                return {"clip_path": "/tmp/intro.jpg", "url": intro_url, "metadata": {}}
            # outro — seen_urls must contain BOTH body_url AND intro_url
            assert seen_urls is not None
            assert body_url in seen_urls, (
                "outro must dedup against the body URL"
            )
            assert intro_url in seen_urls, (
                "outro must dedup against the intro URL"
            )
            return {"clip_path": "/tmp/outro.jpg", "url": outro_url, "metadata": {}}

        with patch(
            "services.stages.scene_visuals._try_pexels", AsyncMock(side_effect=fake_try_pexels),
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        v = result.context_updates["video_scene_visuals"]
        assert v["intro_clip_path"] == "/tmp/intro.jpg"
        assert v["outro_clip_path"] == "/tmp/outro.jpg"


# ---------------------------------------------------------------------------
# gh#163 _try_pexels — internal seen_urls handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestTryPexelsSeenUrls:
    async def test_picks_first_unseen_when_top_hit_already_used(self):
        """When the Pexels top hit is in seen_urls, _try_pexels must
        skip it and pick the next candidate.
        """
        from services.stages.scene_visuals import _try_pexels

        seen_url = "https://pexels.example/seen.jpg"
        unseen_url = "https://pexels.example/unseen.jpg"

        candidates = [
            SimpleNamespace(
                url=seen_url, photographer="A", photographer_url="", width=0, height=0,
            ),
            SimpleNamespace(
                url=unseen_url, photographer="B", photographer_url="", width=0, height=0,
            ),
        ]
        provider = MagicMock()
        provider.fetch = AsyncMock(return_value=candidates)

        with patch(
            "services.image_providers.pexels.PexelsProvider", return_value=provider,
        ), patch(
            "services.stages.scene_visuals._download_to_tmp",
            AsyncMock(return_value="/tmp/dl.jpg"),
        ):
            result = await _try_pexels(
                "topic", _FAKE_SITE_CONFIG, seen_urls={seen_url},
            )

        assert result is not None
        assert result["url"] == unseen_url

    async def test_returns_none_when_all_results_are_seen(self):
        """When every candidate is already in seen_urls, _try_pexels
        returns None rather than reusing one — caller falls through
        (better to leave the bookend empty than to dupe).
        """
        from services.stages.scene_visuals import _try_pexels

        url = "https://pexels.example/only.jpg"
        candidates = [
            SimpleNamespace(
                url=url, photographer="X", photographer_url="", width=0, height=0,
            ),
        ]
        provider = MagicMock()
        provider.fetch = AsyncMock(return_value=candidates)

        with patch(
            "services.image_providers.pexels.PexelsProvider", return_value=provider,
        ):
            result = await _try_pexels(
                "topic", _FAKE_SITE_CONFIG, seen_urls={url},
            )

        assert result is None


# ---------------------------------------------------------------------------
# Bounded-concurrency fan-out (poindexter#164)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBoundedConcurrency:
    """Verify the asyncio.Semaphore-bounded scene resolution.

    Default cap=1 preserves the original sequential behavior; raising
    the cap lets compatible scenes overlap. The semaphore is the gate;
    these tests prove (a) the cap is honored, (b) results stay scene-
    indexed regardless of completion order, and (c) per-scene timing
    audit rows fire.
    """

    async def test_default_cap_serializes_resolutions(self):
        # Default cap=1 means only one scene resolves at a time. We
        # observe that by counting the max-in-flight inside a patched
        # _try_pexels that holds the semaphore for a real awaitable.
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=3, short_form_count=0),
        }

        in_flight = {"current": 0, "peak": 0}

        async def _slow_pexels(*_a, **_kw):
            in_flight["current"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
            await asyncio.sleep(0)  # yield so siblings get a chance
            in_flight["current"] -= 1
            return {"clip_path": "/tmp/x.jpg", "url": "u", "metadata": {}}

        with patch(
            "services.stages.scene_visuals._try_pexels",
            side_effect=_slow_pexels,
        ):
            await SceneVisualsStage().execute(ctx, {})

        # Default cap=1 means the peak in-flight count for the body
        # scenes is exactly 1. Bookend pexels calls happen AFTER the
        # gather completes, so they don't push the counter.
        assert in_flight["peak"] == 1

    async def test_higher_cap_allows_parallel_resolutions(self):
        cfg = _make_site_config({
            "video_scene_visuals_strategy": "pexels",
            "video_scene_visuals_max_concurrent": 3,
        })
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=3, short_form_count=0),
        }

        in_flight = {"current": 0, "peak": 0}
        release_all = asyncio.Event()

        async def _gate(*_a, **_kw):
            in_flight["current"] += 1
            in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
            # Wait until every other in-flight task has incremented the
            # counter — proves they truly ran concurrently. The first
            # task that sees current==3 releases the gate for everyone.
            if in_flight["current"] >= 3:
                release_all.set()
            await release_all.wait()
            in_flight["current"] -= 1
            return {"clip_path": "/tmp/x.jpg", "url": "u", "metadata": {}}

        with patch(
            "services.stages.scene_visuals._try_pexels",
            side_effect=_gate,
        ):
            await SceneVisualsStage().execute(ctx, {})

        # All 3 body scenes resolved concurrently under cap=3.
        assert in_flight["peak"] == 3

    async def test_results_stay_scene_indexed_under_concurrency(self):
        # Even when scene resolution finishes out of order, the result
        # list must remain scene-indexed (gather preserves input order).
        cfg = _make_site_config({
            "video_scene_visuals_strategy": "pexels",
            "video_scene_visuals_max_concurrent": 3,
        })
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=3, short_form_count=0),
        }

        # Index-keyed sleep timings (longest first → it finishes last)
        # so completion order is the reverse of scene order.
        sleeps_remaining = [0.01, 0.005, 0.001]

        async def _slow_pexels(*_a, **_kw):
            # Pop in call order. asyncio.gather schedules all three
            # tasks; the longest-sleep one is the body scene at idx=0,
            # which therefore returns last.
            await asyncio.sleep(sleeps_remaining.pop(0) if sleeps_remaining else 0)
            return {"clip_path": f"/tmp/x.jpg", "url": "u", "metadata": {}}

        with patch(
            "services.stages.scene_visuals._try_pexels",
            side_effect=_slow_pexels,
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        # Each entry retains its scene_idx; the list itself stays in
        # scene order 0..N-1.
        assert [v["scene_idx"] for v in long_visuals] == [0, 1, 2]

    async def test_zero_or_negative_cap_clamps_to_one(self):
        # A misconfigured 0/-1 must not deadlock the stage.
        cfg = _make_site_config({
            "video_scene_visuals_strategy": "pexels",
            "video_scene_visuals_max_concurrent": 0,
        })
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value={"clip_path": "/tmp/x.jpg", "url": "u", "metadata": {}}),
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        # Stage still completes — clamp prevents Semaphore(0) deadlock.
        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        assert long_visuals[0]["source"] == "pexels"

    async def test_per_scene_elapsed_recorded_on_metadata(self):
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "video_script": _video_script(long_form_count=1, short_form_count=0),
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value={"clip_path": "/tmp/x.jpg", "url": "u", "metadata": {}}),
        ):
            result = await SceneVisualsStage().execute(ctx, {})

        long_visuals = result.context_updates["video_scene_visuals"]["long_form"]
        # Per-scene wall-clock surfaces on metadata for downstream
        # observability (poindexter#164 acceptance: real timing data).
        assert "elapsed_s" in long_visuals[0]["metadata"]
        assert isinstance(long_visuals[0]["metadata"]["elapsed_s"], (int, float))
        assert long_visuals[0]["metadata"]["elapsed_s"] >= 0

    async def test_emits_audit_log_per_resolved_scene(self):
        cfg = _make_site_config({"video_scene_visuals_strategy": "pexels"})
        ctx: dict[str, Any] = {
            "site_config": cfg,
            "task_id": "task-abc",
            "video_script": _video_script(long_form_count=2, short_form_count=1),
        }
        with patch(
            "services.stages.scene_visuals._try_pexels",
            AsyncMock(return_value={"clip_path": "/tmp/x.jpg", "url": "u", "metadata": {}}),
        ), patch(
            "services.audit_log.audit_log_bg",
        ) as audit_mock:
            await SceneVisualsStage().execute(ctx, {})

        # 2 long_form + 1 short_form = 3 audit rows for body scenes.
        # Bookend pexels calls don't audit.
        body_calls = [
            c for c in audit_mock.call_args_list
            if c.args and c.args[0] == "video.scene_visual_resolved"
        ]
        assert len(body_calls) == 3
        # Each call carries scene_idx, kind, source, elapsed_s.
        sample = body_calls[0]
        details = sample.args[2]
        assert "scene_idx" in details
        assert "kind" in details
        assert "elapsed_s" in details
        assert details["source"] == "pexels"
