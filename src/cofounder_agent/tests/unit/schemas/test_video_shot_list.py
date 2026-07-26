"""Tests for ``schemas.video_shot_list`` — director output contract.

Pins the validation rules that the director MUST satisfy. A failing
test here = the director can produce invalid output the renderer
will choke on later.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from schemas.video_shot_list import Shot, VideoShotList, scan_for_human_tokens


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _valid_shot(idx: int = 0, source: str = "pexels", **overrides) -> dict:
    base = {
        "idx": idx,
        "duration_s": 5.0,
        "intent": "test shot",
        "source": source,
        "narration_offset_s": float(idx) * 5.0,
    }
    if source == "pexels":
        base["query"] = "data center"
    elif source in (
        "image_gen", "image_kenburns", "wan21", "generative",
        "sdxl", "sdxl_kenburns",  # pre-#1947 aliases, normalized on parse
    ):
        base["prompt"] = "test prompt"
    base.update(overrides)
    return base


def _valid_shot_list(**overrides) -> dict:
    base = {
        "version": 1,
        "total_duration_s": 15.0,
        "shots": [
            _valid_shot(0, "pexels"),
            _valid_shot(1, "image_kenburns"),
            _valid_shot(2, "pexels"),
        ],
        "director_model": "test-model",
        "director_prompt_version": "v1",
        "director_decided_at": _now().isoformat(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Shot — per-source input validation
# ---------------------------------------------------------------------------


def test_image_gen_requires_prompt() -> None:
    with pytest.raises(ValidationError, match="requires a non-empty"):
        Shot.model_validate(_valid_shot(0, "image_gen", prompt=None))


def test_wan21_requires_prompt() -> None:
    with pytest.raises(ValidationError, match="requires a non-empty"):
        Shot.model_validate(_valid_shot(0, "wan21", prompt=None))


def test_generative_requires_prompt() -> None:
    # ``generative`` is the i2v hero source — the prompt is the conditioning
    # text alongside the image_gen still, so it's mandatory just like wan21/image_gen.
    with pytest.raises(ValidationError, match="requires a non-empty"):
        Shot.model_validate(_valid_shot(0, "generative", prompt=None))


def test_generative_accepted_with_prompt() -> None:
    shot = Shot.model_validate(_valid_shot(0, "generative"))
    assert shot.source == "generative"
    assert shot.prompt == "test prompt"


def test_wan21_accepted_with_prompt_backcompat() -> None:
    # wan21 stays a valid source (legacy shot lists + in-flight tasks carry it).
    shot = Shot.model_validate(_valid_shot(0, "wan21"))
    assert shot.source == "wan21"


def test_image_kenburns_requires_prompt() -> None:
    with pytest.raises(ValidationError, match="requires a non-empty"):
        Shot.model_validate(_valid_shot(0, "image_kenburns", prompt=None))


# ---------------------------------------------------------------------------
# Deprecated ``source`` aliases (issue #874)
#
# Shot lists are frozen into ``pipeline_versions`` at Stage 1 and re-read at
# render time weeks later, so renaming a source without a shim strands the
# frozen rows. #1947 renamed sdxl -> image_gen / sdxl_kenburns ->
# image_kenburns with no alias and left task 511012cc unrenderable: its list
# froze 2026-06-25, the last day ``sdxl`` was legal.
# ---------------------------------------------------------------------------


def test_sdxl_normalized_to_image_gen() -> None:
    shot = Shot.model_validate(_valid_shot(0, "sdxl"))
    assert shot.source == "image_gen"


def test_sdxl_kenburns_normalized_to_image_kenburns() -> None:
    shot = Shot.model_validate(_valid_shot(0, "sdxl_kenburns"))
    assert shot.source == "image_kenburns"


def test_sdxl_still_requires_prompt() -> None:
    # The alias must not smuggle a shot past the per-source input rules.
    # Widening ``ShotSource`` instead would let sdxl fall through every branch
    # of _validate_source_inputs' if/elif chain — no prompt check at all.
    with pytest.raises(ValidationError, match="requires a non-empty"):
        Shot.model_validate(_valid_shot(0, "sdxl", prompt=None))


def test_sdxl_kenburns_still_requires_prompt() -> None:
    with pytest.raises(ValidationError, match="requires a non-empty"):
        Shot.model_validate(_valid_shot(0, "sdxl_kenburns", prompt=None))


def test_sdxl_kenburns_still_validates_kenburns_zoom() -> None:
    # The zoom check keys off source == "image_kenburns", so it only fires for
    # a normalized sdxl_kenburns if normalization happens before it runs.
    with pytest.raises(ValidationError, match="kenburns_zoom values must be positive"):
        Shot.model_validate(_valid_shot(0, "sdxl_kenburns", kenburns_zoom=(0.0, 1.2)))


def test_sdxl_prompt_still_scanned_for_human_tokens(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        Shot.model_validate(_valid_shot(0, "sdxl", prompt="a developer at a desk"))
    assert "human-indicator" in caplog.text


def test_unknown_source_still_rejected() -> None:
    # Normalization is a fixed alias map, not a wildcard — a genuinely bogus
    # source must still fail loudly.
    with pytest.raises(ValidationError):
        Shot.model_validate(_valid_shot(0, "midjourney", prompt="x"))


def test_frozen_pre_rename_shot_list_validates() -> None:
    """A full list shaped like production task 511012cc's frozen long lane.

    Mixed old/new vocabulary is impossible — #1947 was a pure rename, so
    ``image_gen`` did not exist before it and ``sdxl`` did not after — which is
    why normalizing can never fabricate a consecutive-source streak.
    """
    shot_list = VideoShotList.model_validate(
        _valid_shot_list(
            total_duration_s=20.0,
            shots=[
                _valid_shot(0, "pexels"),
                _valid_shot(1, "sdxl_kenburns"),
                _valid_shot(2, "generative"),
                _valid_shot(3, "sdxl"),
            ],
        ),
    )
    assert [s.source for s in shot_list.shots] == [
        "pexels", "image_kenburns", "generative", "image_gen",
    ]


def test_pexels_requires_query() -> None:
    with pytest.raises(ValidationError, match="requires a non-empty"):
        Shot.model_validate(_valid_shot(0, "pexels", query=None))


def test_holdover_forbids_prompt_and_query() -> None:
    """Holdover shots are pure transitions; surfacing prompt/query
    means the director got confused about source semantics."""
    bad_with_prompt = {
        "idx": 0, "duration_s": 1.0, "intent": "fade", "source": "holdover",
        "narration_offset_s": 0.0, "prompt": "this is wrong",
    }
    with pytest.raises(ValidationError, match="must not have"):
        Shot.model_validate(bad_with_prompt)


def test_unknown_source_rejected() -> None:
    """Discriminator typo must fail — no fall-through to default source."""
    with pytest.raises(ValidationError):
        Shot.model_validate(_valid_shot(0, "midjourney"))


def test_negative_duration_rejected() -> None:
    with pytest.raises(ValidationError):
        Shot.model_validate(_valid_shot(0, "pexels", duration_s=-1.0))


def test_zero_duration_rejected() -> None:
    """Zero duration shots are nonsensical — the renderer would skip them
    silently. Reject up front."""
    with pytest.raises(ValidationError):
        Shot.model_validate(_valid_shot(0, "pexels", duration_s=0.0))


def test_kenburns_zoom_positive() -> None:
    with pytest.raises(ValidationError, match="kenburns_zoom"):
        Shot.model_validate(
            _valid_shot(0, "image_kenburns", kenburns_zoom=(0.0, 1.2)),
        )


# ---------------------------------------------------------------------------
# VideoShotList — pacing + arithmetic rules
# ---------------------------------------------------------------------------


def test_minimal_valid_shot_list() -> None:
    """Happy path — three diverse shots, durations sum correctly."""
    shot_list = VideoShotList.model_validate(_valid_shot_list())
    assert len(shot_list.shots) == 3
    assert shot_list.total_duration_s == 15.0


def test_duration_sum_must_match_total() -> None:
    """A director that off-by-ones the arithmetic gets caught."""
    bad = _valid_shot_list(total_duration_s=60.0)  # shots sum to 15
    with pytest.raises(ValidationError, match="disagrees with sum"):
        VideoShotList.model_validate(bad)


def test_duration_sum_tolerates_small_drift() -> None:
    """±0.5s drift is acceptable — float math, narration alignment slop."""
    bad = _valid_shot_list(total_duration_s=15.3)  # 0.3s drift
    shot_list = VideoShotList.model_validate(bad)
    assert shot_list.total_duration_s == 15.3


def test_shot_idx_must_be_contiguous() -> None:
    """Skipping idx values would break renderer ordering."""
    bad = _valid_shot_list(shots=[
        _valid_shot(0, "pexels"),
        _valid_shot(2, "image_kenburns"),  # skipped 1
        _valid_shot(3, "pexels"),
    ])
    bad["total_duration_s"] = 15.0
    with pytest.raises(ValidationError, match="contiguous"):
        VideoShotList.model_validate(bad)


def test_no_more_than_2_consecutive_same_source() -> None:
    """The pacing rule — 3 Wan2.1 shots in a row reads as AI slop."""
    bad = _valid_shot_list(shots=[
        _valid_shot(0, "wan21"),
        _valid_shot(1, "wan21"),
        _valid_shot(2, "wan21"),  # 3rd in a row
    ])
    bad["total_duration_s"] = 15.0
    with pytest.raises(ValidationError, match="consecutive shots"):
        VideoShotList.model_validate(bad)


def test_two_consecutive_same_source_allowed() -> None:
    """Two in a row is fine — three is the line."""
    ok = _valid_shot_list(shots=[
        _valid_shot(0, "image_kenburns"),
        _valid_shot(1, "image_kenburns"),
        _valid_shot(2, "pexels"),
    ])
    ok["total_duration_s"] = 15.0
    shot_list = VideoShotList.model_validate(ok)
    assert shot_list.shots[0].source == shot_list.shots[1].source == "image_kenburns"


def test_holdover_does_not_count_in_streak() -> None:
    """Holdover transitions break the streak (they're not content shots)."""
    ok = _valid_shot_list(shots=[
        _valid_shot(0, "wan21"),
        _valid_shot(1, "wan21"),
        {
            "idx": 2, "duration_s": 5.0, "intent": "transition",
            "source": "holdover", "narration_offset_s": 10.0,
        },
        _valid_shot(3, "wan21"),  # would be 3rd wan21 without holdover
    ])
    ok["total_duration_s"] = 20.0
    shot_list = VideoShotList.model_validate(ok)
    assert len(shot_list.shots) == 4


def test_empty_shots_rejected() -> None:
    bad = _valid_shot_list(shots=[])
    bad["total_duration_s"] = 1.0
    with pytest.raises(ValidationError):
        VideoShotList.model_validate(bad)


# ---------------------------------------------------------------------------
# Human-subject soft validator — warns but does NOT reject
# ---------------------------------------------------------------------------


def test_scan_for_human_tokens_finds_obvious_humans() -> None:
    assert "developer" in scan_for_human_tokens("a developer at a desk")
    assert "person" in scan_for_human_tokens("a Person standing alone")


def test_scan_for_human_tokens_dedupes_and_lowercases() -> None:
    tokens = scan_for_human_tokens("Developer, developer, Engineer.")
    assert tokens == ["developer", "engineer"]


def test_scan_for_human_tokens_silhouette_escape_hatch() -> None:
    """'faceless silhouette' framing satisfies the convention — no warning."""
    assert scan_for_human_tokens("a faceless silhouette of a developer") == []
    assert scan_for_human_tokens("silhouette of a person backlit") == []


def test_scan_for_human_tokens_handles_empty() -> None:
    assert scan_for_human_tokens("") == []
    assert scan_for_human_tokens(None) == []  # type: ignore[arg-type]


def test_ai_source_human_prompt_warns_but_accepts(caplog: pytest.LogCaptureFixture) -> None:
    """Soft warning — director output isn't rejected, but the log captures
    the slop risk so we can tune the director prompt later."""
    with caplog.at_level(logging.WARNING, logger="schemas.video_shot_list"):
        shot = Shot.model_validate(
            _valid_shot(0, "image_kenburns", prompt="a developer typing at a keyboard"),
        )
    assert shot.source == "image_kenburns"  # NOT rejected
    assert "developer" in caplog.text
    assert "human-indicator" in caplog.text


def test_pexels_human_query_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """Pexels is the human-friendly lane — real footage has no AI tell."""
    with caplog.at_level(logging.WARNING, logger="schemas.video_shot_list"):
        Shot.model_validate(_valid_shot(0, "pexels", query="people working at desk"))
    assert "human-indicator" not in caplog.text


def test_ai_source_silhouette_prompt_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """The escape hatch — explicit silhouette framing satisfies the rule."""
    with caplog.at_level(logging.WARNING, logger="schemas.video_shot_list"):
        Shot.model_validate(
            _valid_shot(0, "wan21", prompt="faceless silhouette of a figure walking"),
        )
    assert "human-indicator" not in caplog.text


def test_aspect_defaults_to_16x9() -> None:
    sl = VideoShotList.model_validate(_valid_shot_list())
    assert sl.aspect == "16:9"


def test_aspect_accepts_9x16() -> None:
    sl = VideoShotList.model_validate(_valid_shot_list(aspect="9:16"))
    assert sl.aspect == "9:16"


def test_aspect_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        VideoShotList.model_validate(_valid_shot_list(aspect="4:3"))


# ---------------------------------------------------------------------------
# motion — image-to-video direction for generative shots
# ---------------------------------------------------------------------------


def test_generative_motion_roundtrips() -> None:
    shot = Shot.model_validate(_valid_shot(
        0, "generative",
        motion="slow push-in; particles drift upward with gentle parallax",
    ))
    assert shot.motion == (
        "slow push-in; particles drift upward with gentle parallax"
    )


def test_motion_is_optional_for_backcompat() -> None:
    """Every shot list frozen before the field existed lacks ``motion`` —
    the renderer substitutes a default direction, so the schema must not
    reject its absence."""
    shot = Shot.model_validate(_valid_shot(0, "generative"))
    assert shot.motion is None


def test_motion_inert_on_non_generative_sources() -> None:
    """A stray ``motion`` on a non-hero source parses fine (it's simply
    ignored by the renderer) — directors slip fields across sources and a
    hard reject would throw away an otherwise-usable list."""
    shot = Shot.model_validate(_valid_shot(0, "pexels", motion="drift"))
    assert shot.motion == "drift"


def test_motion_scanned_for_human_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The motion text drives generation just like the prompt — a human noun
    there is the same AI-tell risk (soft warning, not a reject)."""
    with caplog.at_level(logging.WARNING):
        Shot.model_validate(_valid_shot(
            0, "generative", motion="a person waves at the camera",
        ))
    assert any("human-indicator" in r.message for r in caplog.records)


def test_motion_over_length_rejected() -> None:
    with pytest.raises(ValidationError):
        Shot.model_validate(_valid_shot(0, "generative", motion="x" * 501))
