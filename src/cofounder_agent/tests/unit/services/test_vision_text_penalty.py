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


def _qa(monkeypatch, response: dict | str | list, **setting_overrides) -> MultiModelQA:
    """``response`` is the judge's answer; a LIST gives one answer per call, in
    image order — the rail makes one call per image (poindexter#1078)."""
    qa = MultiModelQA(
        pool=None,
        settings_service=_settings_service(**setting_overrides),
        site_config=SiteConfig(),
    )
    answers = response if isinstance(response, list) else [response]
    texts = [a if isinstance(a, str) else json.dumps(a) for a in answers]
    calls = {"n": 0}

    async def _vision(self, **kwargs):
        assert len(kwargs["images_b64"]) == 1, "one image per judge call"
        text = texts[min(calls["n"], len(texts) - 1)]
        calls["n"] += 1
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
        qa = _qa(monkeypatch, [
            {"scores": [90], "text_coverage": [0], "reasons": ["clean"], "overall": 90},
            {"scores": [90], "text_coverage": [40],
             "reasons": ["banner headline across the top"], "overall": 90},
        ])

        review = await qa._check_image_relevance("t", "topic", content)

        assert review is not None
        # (90 + 30) / 2 = 60 — exactly at the threshold, so still a pass, but
        # the clean image is not dragged to 30 nor the dirty one lifted to 90.
        assert review.score == pytest.approx(60.0)
        assert "b.png" in review.feedback
        assert "-60 text 40% of frame" in review.feedback

    async def test_featured_image_keeps_its_own_score_and_reason(self, monkeypatch):
        """poindexter#1078: with every image in one call the judge's arrays drifted
        out of order and the featured image took the first inline image's
        reason and its 30. Each image is now its own call, so the featured
        verdict can only describe the featured image."""
        featured = "https://r2.example.dev/images/featured/hero.webp"
        content = '<img src="https://r2.example.dev/inline-1.png"/>'
        qa = _qa(monkeypatch, [
            {"scores": [90], "text_coverage": [0], "reasons": ["magnifying glass over a data grid"]},
            {"scores": [30], "text_coverage": [0], "reasons": ["person in front of an industrial fan"]},
        ])

        review = await qa._check_image_relevance(
            "t", "topic", content, featured_image_url=featured,
        )

        assert review is not None
        assert review.score == pytest.approx(60.0)
        # Each score and reason sits next to the image it was judged on
        # (feedback truncates the url to its last 40 chars).
        assert "[90] r2.example.dev/images/featured/hero.webp: magnifying glass" in review.feedback
        assert "[30] https://r2.example.dev/inline-1.png: person in front of an industrial fan" in review.feedback

    async def test_a_failed_call_for_one_image_leaves_the_others_on_their_own_index(
        self, monkeypatch,
    ):
        content = (
            '<img src="https://r2.example.dev/a.png"/>\n'
            '<img src="https://r2.example.dev/b.png"/>'
        )
        qa = _qa(monkeypatch, ["not json at all", {"scores": [80], "text_coverage": [0], "reasons": ["ok"]}])

        review = await qa._check_image_relevance("t", "topic", content)

        assert review is not None
        assert review.score == pytest.approx(80.0)

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
        qa = _qa(monkeypatch, [
            {"scores": [80], "text_coverage": [40], "reasons": ["banner"], "overall": 80},
            {"scores": [80], "reasons": ["clean"], "overall": 80},  # no coverage answer
        ])

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


# ---------------------------------------------------------------------------
# Measured coverage (2026-09-23)
#
# The ramp above is unchanged; only its INPUT moved. For an image whose kind
# forbids text, services/image_text_scan.py OCR-measures the share of the
# frame the text boxes cover, and that number replaces the judge's eyeballed
# estimate. The estimate stays the fallback when the scan is unavailable, and
# a kind whose text IS its content (chart / screenshot / brand hero) takes no
# text penalty at all.
# ---------------------------------------------------------------------------


def _stub_scan(monkeypatch, *, status="pass", coverage=0.0, calls=None):
    from poindexter.services import image_text_scan as its

    async def fake(image, *, kind, site_config=None, settings=None, backend=None):
        if calls is not None:
            calls.append(kind)
        measured = status in ("pass", "fail")
        return its.ImageTextScan(
            status=status, kind=kind, policy="forbidden",
            text_chars=20 if measured else None,
            coverage_pct=coverage if measured else None,
        )

    monkeypatch.setattr(its, "scan_image_text", fake)


CHART_CONTENT = (
    "Benchmarks below. "
    '<img src="https://r2.example.dev/images/charts/ab12cd34.png" alt="chart"/>'
)


@pytest.mark.unit
@pytest.mark.usefixtures("_stub_image_download", "_no_thinking_bump")
class TestMeasuredTextCoverage:
    async def test_measured_coverage_replaces_a_judge_that_missed_the_text(
        self, monkeypatch,
    ):
        """The judge says 0% text; the boxes say 40%. The boxes win."""
        _stub_scan(monkeypatch, coverage=40.0)
        qa = _qa(monkeypatch, {
            "scores": [95], "text_coverage": [0], "reasons": ["x"], "overall": 95,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(35.0)
        assert "text 40% of frame, measured" in review.feedback

    async def test_measured_clean_frame_overrides_a_judged_estimate(
        self, monkeypatch,
    ):
        _stub_scan(monkeypatch, coverage=0.0)
        qa = _qa(monkeypatch, {
            "scores": [90], "text_coverage": [40], "reasons": ["x"], "overall": 90,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(90.0)

    async def test_unavailable_scan_falls_back_to_the_judge_estimate(
        self, monkeypatch,
    ):
        """'Could not verify' must never read as a measured clean 0."""
        _stub_scan(monkeypatch, status="unavailable")
        qa = _qa(monkeypatch, {
            "scores": [95], "text_coverage": [40], "reasons": ["x"], "overall": 95,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(35.0)
        assert "judged" in review.feedback

    async def test_chart_text_is_content_not_a_defect(self, monkeypatch):
        calls: list = []
        _stub_scan(monkeypatch, coverage=60.0, calls=calls)
        qa = _qa(monkeypatch, {
            "scores": [88], "text_coverage": [45], "reasons": ["axis labels"],
            "overall": 88,
        })

        review = await qa._check_image_relevance("t", "topic", CHART_CONTENT)

        assert review is not None
        assert review.score == pytest.approx(88.0)
        assert calls == []  # an expected-text kind is never sent to the scanner

    async def test_missing_judge_array_does_not_page_when_every_image_is_measured(
        self, monkeypatch,
    ):
        emitted: list[dict] = []
        import poindexter.utils.findings as findings

        monkeypatch.setattr(findings, "emit_finding", lambda **kw: emitted.append(kw))
        _stub_scan(monkeypatch, coverage=40.0)
        qa = _qa(monkeypatch, {"scores": [95], "reasons": ["x"], "overall": 95})

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(35.0)  # the measurement still bites
        assert emitted == []

    async def test_unknown_url_kind_keeps_the_judged_path(self, monkeypatch):
        """An image the URL can't classify is not scanned — exactly the #3973
        behaviour, so an unrecognised host changes nothing."""
        calls: list = []
        _stub_scan(monkeypatch, coverage=0.0, calls=calls)
        qa = _qa(monkeypatch, {
            "scores": [95], "text_coverage": [40], "reasons": ["x"], "overall": 95,
        })
        content = '<img src="https://r2.example.dev/a.png"/>'

        review = await qa._check_image_relevance("t", "topic", content)

        assert review is not None
        assert review.score == pytest.approx(35.0)
        assert calls == []

    async def test_scanner_down_is_asked_once_per_rail_call(self, monkeypatch):
        calls: list = []
        _stub_scan(monkeypatch, status="unavailable", calls=calls)
        qa = _qa(monkeypatch, {
            "scores": [90, 90], "text_coverage": [0, 0], "reasons": ["a", "b"],
            "overall": 90,
        })
        content = (
            '<img src="https://r2.example.dev/images/inline/aaaaaaaaaaaa.png"/>\n'
            '<img src="https://r2.example.dev/images/inline/bbbbbbbbbbbb.png"/>'
        )

        await qa._check_image_relevance("t", "topic", content)

        assert calls == ["generate"]

    async def test_real_default_scan_path_degrades_to_judged(self, monkeypatch):
        """No stub: the conftest-isolated backend reports unavailable and the
        rail behaves exactly as it did before measurement existed."""
        qa = _qa(monkeypatch, {
            "scores": [95], "text_coverage": [40], "reasons": ["x"], "overall": 95,
        })

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.score == pytest.approx(35.0)


@pytest.mark.unit
@pytest.mark.usefixtures("_stub_image_download", "_no_thinking_bump")
class TestPassThresholdSetting:
    async def test_deliberate_zero_threshold_is_honoured(self, monkeypatch):
        """`or 60` used to turn a configured 0 into 60."""
        qa = _qa(
            monkeypatch,
            {"scores": [20], "text_coverage": [0], "reasons": ["x"], "overall": 20},
            qa_vision_pass_threshold="0",
        )

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.approved is True

    async def test_blank_threshold_uses_the_default(self, monkeypatch):
        qa = _qa(
            monkeypatch,
            {"scores": [59], "text_coverage": [0], "reasons": ["x"], "overall": 59},
            qa_vision_pass_threshold="",
        )

        review = await qa._check_image_relevance("t", "topic", CONTENT)

        assert review is not None
        assert review.approved is False

    def test_threshold_is_seeded(self):
        from poindexter.services.settings_defaults import DEFAULTS, METADATA

        assert DEFAULTS["qa_vision_pass_threshold"] == str(PASS_THRESHOLD)
        assert METADATA["qa_vision_pass_threshold"]["value_type"] == "integer"
