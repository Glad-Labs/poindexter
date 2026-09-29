"""What an image costs the vision judge, and how many tiles its context holds.

The judge holds one context (``pinned_llm_endpoint_num_ctx``) and every image in
a request spends part of it. The cost is decided by the server: Ollama 0.32.1
serves qwen3-vl through llama.cpp, which rounds an image to a 32 px grid and
pulls it inside 1024-4096 tokens (its load log says ``image_min_pixels:
1048576`` and ``image_max_pixels: 4194304``). ``estimate_image_tokens``
reproduces that rule.

The expected numbers below are not derived from the function under test. They
are what the judge itself reported for real requests on 2026-09-28: the prompt
of a 1280x13141 draft screenshot was 4140 tokens and a 1280x2738 one 3671, and
subtracting the ~230-245 tokens of prompt text leaves the image cost. The
synthetic-size calibration against the live judge is in
``docs/architecture/preview-links.md`` ("Calibrating the estimate").
"""

from __future__ import annotations

import pytest

from poindexter.modules.content import multi_model_qa
from poindexter.services import vision_image_budget as budget
from poindexter.services.settings_defaults import DEFAULTS, default_int

pytestmark = pytest.mark.unit


class TestEstimateImageTokens:
    def test_a_viewport_tile_costs_one_token_per_32px_block(self):
        # 1280x1024 = 40 x 32 blocks, plus the two vision markers
        assert budget.estimate_image_tokens(1280, 1024) == 40 * 32 + 2

    def test_the_evidence_screenshot_was_squeezed_to_the_4096_token_ceiling(self):
        """1280x13141 -> (608, 6560): about half scale, which is what turned 16 px text into 8 px."""
        assert budget.resized_dimensions(1280, 13141) == (608, 6560)
        assert budget.estimate_image_tokens(1280, 13141) == 19 * 205 + 2
        # measured prompt was 4140 tokens; the rest is the ~240-token text prompt
        assert 4140 - budget.estimate_image_tokens(1280, 13141) == pytest.approx(243, abs=15)

    def test_it_matches_what_the_judge_reported_for_real_sizes(self):
        """Calibrated 2026-09-28 against the live judge's own ``prompt_eval_count``
        (minus the 15-token text-only prompt), through :11435 at num_ctx 16384.
        The estimate was exact on every one of these sizes."""
        measured = {
            (320, 240): 1038, (640, 640): 1026, (800, 600): 1038, (1024, 1024): 1026,
            (1280, 1024): 1282, (1011, 1298): 1314, (1280, 1643): 2042, (1280, 2048): 2562,
            (1280, 3200): 4002, (1280, 4096): 3992, (1280, 13141): 3897, (2200, 1024): 2210,
            (3000, 3000): 4098, (700, 1024): 1055,
            # 2000 px is 62.5 blocks: the judge rounds half UP (63), Python's round() to even (62)
            (2000, 655): 1262,
        }
        for (width, height), tokens in measured.items():
            assert budget.estimate_image_tokens(width, height) == tokens, (width, height)

    def test_half_a_block_rounds_up_like_the_judge_not_to_even(self):
        # 2000 / 32 = 62.5 -> 63 blocks (2016 px); 80 / 32 = 2.5 -> 3 blocks (96 px)
        assert budget.resized_dimensions(2000, 655)[0] == 2016
        assert budget._resized_side(80) == 96
        assert budget._resized_side(48) == 64  # 1.5 -> 2
        assert budget._resized_side(16) == 32  # 0.5 -> 1, and never below one block

    def test_a_short_page_is_not_resized(self):
        assert budget.resized_dimensions(1280, 2738) == (1280, 2752)
        assert 3671 - budget.estimate_image_tokens(1280, 2738) == pytest.approx(230, abs=15)

    def test_a_thumbnail_costs_as_much_as_a_megapixel_image(self):
        """The server scales small images UP to the 1024-token floor.

        A square lands on it exactly; an odd aspect ratio rounds both scaled
        sides UP to the 32 px grid (llama.cpp's ceil_by_factor), so it can sit a
        few blocks above: 320x240 becomes 1184x896 = 1036 blocks.
        """
        floor = budget.IMAGE_MIN_TOKENS + budget.IMAGE_MARKER_TOKENS
        assert budget.estimate_image_tokens(64, 64) == floor
        assert budget.estimate_image_tokens(1024, 1024) == floor
        assert budget.resized_dimensions(320, 240) == (1184, 896)
        assert floor <= budget.estimate_image_tokens(320, 240) <= floor * 1.03

    def test_a_huge_image_stops_at_the_4096_token_ceiling(self):
        ceiling = budget.IMAGE_MAX_TOKENS + budget.IMAGE_MARKER_TOKENS
        assert budget.estimate_image_tokens(4000, 4000) <= ceiling
        assert budget.estimate_image_tokens(1280, 40000) <= ceiling

    def test_it_is_symmetric_in_width_and_height(self):
        for w, h in [(1280, 2048), (700, 3000), (2000, 655)]:
            assert budget.estimate_image_tokens(w, h) == budget.estimate_image_tokens(h, w)

    def test_dimensions_must_be_positive(self):
        for bad in [(0, 100), (100, 0), (-5, 100)]:
            with pytest.raises(ValueError):
                budget.estimate_image_tokens(*bad)

    @pytest.mark.parametrize("width", [1, 33, 320, 800, 1280, 1500, 2000, 4096])
    @pytest.mark.parametrize("height", [1, 40, 600, 1024, 2048, 6653, 13141, 40000])
    def test_the_resized_image_is_on_the_grid_and_inside_the_token_range(self, width, height):
        w, h = budget.resized_dimensions(width, height)
        assert w % budget.IMAGE_ALIGN_PX == 0 and h % budget.IMAGE_ALIGN_PX == 0
        blocks = (w // budget.IMAGE_ALIGN_PX) * (h // budget.IMAGE_ALIGN_PX)
        assert blocks <= budget.IMAGE_MAX_TOKENS
        # the floor applies except where the 32 px grid itself forces a tiny image
        # (a 1 px wide image is still 32 px wide) or the aspect is too extreme to reach it
        if width >= 256 and height >= 256:
            assert blocks >= budget.IMAGE_MIN_TOKENS - (w // budget.IMAGE_ALIGN_PX) - (h // budget.IMAGE_ALIGN_PX)


class TestTilesThatFit:
    def test_the_pinned_judge_holds_ten_viewport_tiles_with_the_answer_reserved(self):
        assert budget.tiles_that_fit(16384, 3072, 1280, 1024) == 10

    def test_a_smaller_context_holds_fewer(self):
        assert budget.tiles_that_fit(8192, 3072, 1280, 1024) == 3

    def test_bigger_tiles_mean_fewer_of_them(self):
        assert budget.tiles_that_fit(16384, 3072, 1280, 2048) < budget.tiles_that_fit(16384, 3072, 1280, 1024)

    def test_it_never_reaches_zero(self):
        """A tiny context degrades to one tile, not to no verdict."""
        assert budget.tiles_that_fit(2048, 3072, 1280, 1024) == 1


class TestTheDefaultsAgree:
    """The tile cap is a context budget, so the seeded defaults must fit the
    seeded context. Both come from settings_defaults, so nobody can raise one
    without meeting the other here (the same pairing as
    tests/unit/scripts/test_ollama_vision_context_pin.py)."""

    def test_the_default_tile_count_fits_the_pinned_judge_context(self):
        fits = budget.tiles_that_fit(
            default_int("pinned_llm_endpoint_num_ctx"),
            multi_model_qa._PREVIEW_CONTEXT_RESERVE_TOKENS,
            default_int("qa_preview_viewport_width"),
            default_int("qa_preview_viewport_height"),
        )
        assert default_int("qa_preview_max_tiles") <= fits, (
            "qa_preview_max_tiles would be clamped at every call: it needs more "
            "context than pinned_llm_endpoint_num_ctx leaves after the reserve. "
            "Lower the cap, or raise the context after checking the judge GPU's VRAM headroom."
        )

    def test_multi_model_qa_uses_the_seeded_defaults(self):
        assert multi_model_qa._PREVIEW_DEFAULT_MAX_TILES == default_int("qa_preview_max_tiles")
        assert multi_model_qa._PREVIEW_DEFAULT_MIN_SCALE == float(DEFAULTS["qa_preview_min_scale"])

    def test_the_reserve_covers_the_prompt_and_the_answer(self):
        """~500 tokens of prompt and up to ~450 of answer, with room to spare."""
        assert multi_model_qa._PREVIEW_CONTEXT_RESERVE_TOKENS >= 2 * (500 + 450)
