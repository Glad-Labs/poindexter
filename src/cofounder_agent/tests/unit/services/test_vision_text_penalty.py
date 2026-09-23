"""Rendered-text penalty on the qa.vision image-relevance rail.

The rail scores SUBJECT relevance and was structurally blind to rendered-text
garbage. Concrete case — task ``243f3123-711387d7`` (2026-09-23, hero for
"Beyond Arrival: Why Shipping Isn't the Same as Impact"): the composition was
on-concept, but roughly the top 40% of the frame was a nonsense headline
reading ``TÝMENEITUR`` plus four blocks of fake-text noise. The rail scored it
**95**, and its feedback cited the gibberish as ``its title 'TÝMENEITUR'`` —
the judge read unintended lettering as evidence of good composition.

That is systematic, not a one-off: the image negative prompt already bans
``text, words, letters, numbers, watermark, signature, logo``, so ANY legible
(or legible-shaped) mark in a generated illustration is something the generator
was told not to draw, and garbled pseudo-text is the dominant failure mode of
the local diffusion models.

These tests pin the fix: the prompt asks for a per-image ``text_coverage``
estimate, and ``text_coverage_penalty`` converts it into a deduction that
scales with how much of the frame the text eats — so a banner headline sinks
the image while a stray glyph in a corner does not.

The rail is advisory; this is a scoring-accuracy fix, not a gating change. The
fail-open contract is unchanged: a degraded judge still returns ``None`` (no
fabricated verdict), which ``qa.vision`` turns into a finding.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from poindexter.modules.content import multi_model_qa as mmqa
from poindexter.modules.content.multi_model_qa import (
    MultiModelQA,
    normalize_text_coverage,
    text_coverage_penalty,
)
from poindexter.services.site_config import SiteConfig

# Defaults mirrored from settings_defaults.py — the tests assert against the
# shipped config, so a change to either has to be a deliberate one.
MAX_PENALTY = 60
IGNORE_PCT = 5
FULL_PCT = 40
PASS_THRESHOLD = 60


@pytest.mark.unit
class TestTextCoveragePenalty:
    """The pure ramp. No model, no network — just the arithmetic."""

    def test_clean_frame_costs_nothing(self):
        assert text_coverage_penalty(0) == 0.0

    def test_corner_artifact_below_the_ignore_floor_costs_nothing(self):
        """Requirement 2: a small artifact must not tank a good image."""
        assert text_coverage_penalty(IGNORE_PCT) == 0.0
        assert text_coverage_penalty(IGNORE_PCT - 1) == 0.0

    def test_penalty_is_proportional_between_the_thresholds(self):
        """Halfway up the ramp costs half the maximum."""
        midpoint = IGNORE_PCT + (FULL_PCT - IGNORE_PCT) / 2
        assert text_coverage_penalty(midpoint) == pytest.approx(MAX_PENALTY / 2)

    def test_penalty_rises_monotonically(self):
        seen = [text_coverage_penalty(p) for p in range(0, 101, 5)]
        assert seen == sorted(seen)

    def test_full_penalty_at_and_above_the_ceiling(self):
        assert text_coverage_penalty(FULL_PCT) == pytest.approx(MAX_PENALTY)
        assert text_coverage_penalty(100) == pytest.approx(MAX_PENALTY)

    def test_thresholds_are_operator_tunable(self):
        assert text_coverage_penalty(
            10, max_penalty=100, ignore_pct=0, full_pct=10
        ) == pytest.approx(100)

    def test_zero_ignore_floor_penalises_any_text(self):
        """0 is a meaningful operator choice, not "unset"."""
        assert text_coverage_penalty(1, ignore_pct=0, full_pct=40) > 0

    @pytest.mark.parametrize("bad", [None, "lots", "", float("nan"), object()])
    def test_unusable_estimate_invents_no_penalty(self, bad):
        """The estimate comes from an LLM — a bad reading deducts nothing
        rather than guessing a number the model never gave."""
        assert text_coverage_penalty(bad) == 0.0

    def test_negative_and_oversized_estimates_are_clamped(self):
        assert text_coverage_penalty(-20) == 0.0
        assert text_coverage_penalty(5000) == pytest.approx(MAX_PENALTY)

    def test_percent_suffixed_estimate_is_not_lost(self):
        """Models answer "40" and "40%" about equally often."""
        assert text_coverage_penalty("40%") == pytest.approx(MAX_PENALTY)
        assert text_coverage_penalty(" 40 ") == pytest.approx(MAX_PENALTY)

    def test_inverted_thresholds_degrade_to_a_step_not_a_zero_divide(self):
        assert text_coverage_penalty(
            50, ignore_pct=40, full_pct=40
        ) == pytest.approx(MAX_PENALTY)
        assert text_coverage_penalty(10, ignore_pct=40, full_pct=40) == 0.0


@pytest.mark.unit
class TestNormalizeTextCoverage:
    """None means "no usable estimate", and must never be confused with 0."""

    @pytest.mark.parametrize(
        "raw,expected",
        [(0, 0.0), (40, 40.0), ("40", 40.0), ("40%", 40.0), (" 40 ", 40.0),
         (-5, 0.0), (500, 100.0)],
    )
    def test_readable_values(self, raw, expected):
        assert normalize_text_coverage(raw) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "raw", [None, "", "lots of text", float("nan"), True, False, {}, []],
    )
    def test_unreadable_values_are_none_not_zero(self, raw):
        assert normalize_text_coverage(raw) is None


# ---------------------------------------------------------------------------
# Whole-rail tests: stubbed vision response -> ReviewerResult
# ---------------------------------------------------------------------------

_SETTINGS = {
    "qa_vision_check_enabled": "true",
    "qa_vision_model": "ollama/qwen3-vl:30b-a3b-instruct",
    "qa_vision_max_images": "3",
    "qa_vision_pass_threshold": str(PASS_THRESHOLD),
    "qa_vision_num_predict": "1024",
    "qa_vision_text_penalty_max": str(MAX_PENALTY),
    "qa_vision_text_ignore_coverage_pct": str(IGNORE_PCT),
    "qa_vision_text_full_penalty_pct": str(FULL_PCT),
}

CONTENT = (
    "Shipping is not impact. "
    '<img src="https://r2.example.dev/images/featured/243f3123-711387d7.png" alt="hero"/>\n'
    "The rest of the article argues that arrival is the start, not the finish."
)


def _settings_service(**overrides):
    values = dict(_SETTINGS)
    values.update(overrides)
    svc = MagicMock()

    async def _get(key):
        return values.get(key)

    svc.get = AsyncMock(side_effect=_get)
    return svc


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 40, 90)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def _stub_image_download(monkeypatch):
    """Serve every image URL a decodable PNG, without touching the network."""
    client = MagicMock()
    client.get = AsyncMock(
        return_value=SimpleNamespace(status_code=200, content=_png_bytes())
    )
    monkeypatch.setattr(mmqa, "http_client", client)
    return client


@pytest.fixture
def _no_thinking_bump(monkeypatch):
    """Skip the thinking-budget lookup — it reads its own setting and is not
    what these tests are about."""
    async def _base(self, model, base):
        return base

    monkeypatch.setattr(MultiModelQA, "_maybe_bump_vision_thinking_budget", _base)


def _qa(monkeypatch, response: dict | str, **setting_overrides) -> MultiModelQA:
    qa = MultiModelQA(
        pool=None,
        settings_service=_settings_service(**setting_overrides),
        site_config=SiteConfig(),
    )
    text = response if isinstance(response, str) else json.dumps(response)

    async def _vision(self, **kwargs):
        return text

    monkeypatch.setattr(MultiModelQA, "_vision_complete", _vision)
    monkeypatch.setattr(
        mmqa, "get_prompt_manager", lambda: SimpleNamespace(get_prompt=lambda *a, **k: "PROMPT")
    )
    return qa


@pytest.mark.unit
@pytest.mark.usefixtures("_stub_image_download", "_no_thinking_bump")
class TestImageRelevanceTextPenalty:
    async def test_prominent_rendered_text_fails_the_rail(self, monkeypatch):
        """The 243f3123 case, verbatim: a judge that loves the composition AND
        reads the gibberish as a title must not ship a 95."""
        qa = _qa(monkeypatch, {
            "scores": [95],
            "text_coverage": [40],
            "reasons": [
                "Strong on-concept composition: a figure on a broken bridge "
                "with a trophy across the gap, and its title 'TÝMENEITUR' "
                "reinforces the theme."
            ],
            "overall": 95,
        })

        review = await qa._check_image_relevance(
            "Beyond Arrival", "shipping vs impact", CONTENT,
        )

        assert review is not None
        assert review.reviewer == "image_relevance"
        assert review.score < PASS_THRESHOLD, review.feedback
        assert review.approved is False
        # 95 - 60 = 35: the deduction is visible to the approver, not silent.
        assert review.score == pytest.approx(35.0)
        assert "text 40% of frame" in review.feedback

    async def test_small_corner_artifact_still_passes(self, monkeypatch):
        """Requirement 2: proportionality. A 3%-of-frame smudge on an otherwise
        excellent image must not cost it the gate."""
        qa = _qa(monkeypatch, {
            "scores": [92],
            "text_coverage": [3],
            "reasons": ["directly illustrates the deploy pipeline"],
            "overall": 92,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.approved is True
        assert review.score == pytest.approx(92.0)

    async def test_partial_coverage_deducts_without_failing_a_strong_image(
        self, monkeypatch
    ):
        qa = _qa(monkeypatch, {
            "scores": [95],
            "text_coverage": [12],
            "reasons": ["on concept"],
            "overall": 95,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        # 60 * (12-5)/(40-5) = 12 points off.
        assert review.score == pytest.approx(83.0)
        assert review.approved is True

    async def test_clean_image_is_unchanged_by_the_new_signal(self, monkeypatch):
        qa = _qa(monkeypatch, {
            "scores": [88],
            "text_coverage": [0],
            "reasons": ["on concept"],
            "overall": 88,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(88.0)
        assert review.approved is True
        assert "% of frame" not in review.feedback

    async def test_penalty_is_per_image_not_smeared_across_the_set(
        self, monkeypatch
    ):
        """Two inline images, only the second carrying a headline. The average
        must fall by half the deduction, and the note must name image 2."""
        content = (
            '<img src="https://r2.example.dev/a.png"/>\n'
            '<img src="https://r2.example.dev/b.png"/>'
        )
        qa = _qa(monkeypatch, {
            "scores": [90, 90],
            "text_coverage": [0, 40],
            "reasons": ["clean", "banner headline across the top"],
            "overall": 90,
        })

        review = await qa._check_image_relevance("t", "topic", content)

        assert review is not None
        # (90 + 30) / 2 = 60 — exactly at the threshold, so still a pass, but
        # the clean image is not dragged to 30 nor the dirty one lifted to 90.
        assert review.score == pytest.approx(60.0)
        assert "b.png" in review.feedback
        assert "-60 text 40% of frame" in review.feedback

    async def test_model_overall_cannot_rescue_a_penalised_image(
        self, monkeypatch
    ):
        """`overall` is formed from the same text-blind reading, so it must not
        carry a penalised image back over the line."""
        qa = _qa(monkeypatch, {
            "scores": [95],
            "text_coverage": [40],
            "reasons": ["great headline"],
            "overall": 99,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(35.0)

    async def test_harsher_model_overall_is_kept(self, monkeypatch):
        """The deduction can only lower the number — a model that was already
        stricter than the penalised average keeps its own verdict."""
        qa = _qa(monkeypatch, {
            "scores": [95],
            "text_coverage": [12],
            "reasons": ["on concept but busy"],
            "overall": 55,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(55.0)

    async def test_missing_text_coverage_deducts_nothing_and_emits_a_finding(
        self, monkeypatch
    ):
        """A model that stops answering the new question must not silently
        revert the rail to relevance-only behind a green score."""
        emitted: list[dict] = []
        import poindexter.utils.findings as findings

        monkeypatch.setattr(
            findings, "emit_finding", lambda **kw: emitted.append(kw)
        )

        qa = _qa(monkeypatch, {
            "scores": [95],
            "reasons": ["its title reinforces the theme"],
            "overall": 95,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(95.0)  # no penalty invented
        assert [f["kind"] for f in emitted] == ["vision_text_signal_missing"]
        assert "qwen3-vl" in emitted[0]["dedup_key"]

    async def test_coverage_shorter_than_scores_penalises_only_what_it_covers(
        self, monkeypatch
    ):
        """A ragged array must not shift penalties onto the wrong picture."""
        content = (
            '<img src="https://r2.example.dev/a.png"/>\n'
            '<img src="https://r2.example.dev/b.png"/>'
        )
        qa = _qa(monkeypatch, {
            "scores": [80, 80],
            "text_coverage": [40],
            "reasons": ["banner", "clean"],
            "overall": 80,
        })

        review = await qa._check_image_relevance("t", "topic", content)

        assert review is not None
        # image 1 penalised to 20, image 2 untouched at 80 -> avg 50.
        assert review.score == pytest.approx(50.0)
        assert review.approved is False

    async def test_bare_number_accepted_for_a_single_image_post(
        self, monkeypatch
    ):
        """A one-image post often comes back with a scalar instead of a
        one-element array. Unambiguous — take the signal, don't page."""
        emitted: list[dict] = []
        import poindexter.utils.findings as findings

        monkeypatch.setattr(
            findings, "emit_finding", lambda **kw: emitted.append(kw)
        )

        qa = _qa(monkeypatch, {
            "scores": [95],
            "text_coverage": 40,
            "reasons": ["banner headline"],
            "overall": 95,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(35.0)
        assert review.approved is False
        assert emitted == []

    async def test_unreadable_scalar_coverage_still_pages(self, monkeypatch):
        """A scalar only counts when it is READABLE — otherwise the signal is
        absent and the finding must still fire."""
        emitted: list[dict] = []
        import poindexter.utils.findings as findings

        monkeypatch.setattr(
            findings, "emit_finding", lambda **kw: emitted.append(kw)
        )

        qa = _qa(monkeypatch, {
            "scores": [95],
            "text_coverage": "could not tell",
            "reasons": ["x"],
            "overall": 95,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(95.0)
        assert [f["kind"] for f in emitted] == ["vision_text_signal_missing"]

    async def test_degraded_judge_still_returns_none_not_a_fake_verdict(
        self, monkeypatch
    ):
        """Fail-open contract unchanged: unparseable model output yields no
        review at all (qa.vision turns that into the finding), never a
        fabricated pass or a fabricated text penalty."""
        qa = _qa(monkeypatch, "the model rambled and emitted no JSON")

        assert await qa._check_image_relevance("t", "topic", CONTENT) is None

    async def test_penalty_thresholds_come_from_app_settings(self, monkeypatch):
        """Every tunable is DB-backed — a SaaS operator can retune the ramp
        without a code change."""
        qa = _qa(
            monkeypatch,
            {"scores": [95], "text_coverage": [10], "reasons": ["x"], "overall": 95},
            qa_vision_text_penalty_max="90",
            qa_vision_text_ignore_coverage_pct="0",
            qa_vision_text_full_penalty_pct="10",
        )

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(5.0)
        assert review.approved is False
