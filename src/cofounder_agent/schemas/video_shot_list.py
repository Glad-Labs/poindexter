"""Pydantic models for the video shot list — director output contract.

The shot list is the seam between the LLM director (decides what each
shot should be) and the renderer (assembles the final MP4 from
per-source clips). This module defines the canonical schema; both sides
import from here so they can't drift.

Design doc: ``docs/architecture/video-composition.md`` (issue #649)

Stored as JSONB on ``posts.video_shot_list``. The director stage
produces it; PR 2 in the sequenced plan will wire the renderer to
consume it. This PR only persists the data so the operator can review
director output for a few real posts before committing the renderer.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


# Discriminator values for ``Shot.source``. Adding a new source = add
# here + add a per-source plugin (PR 2). The director prompt YAML
# references this set explicitly, so the LLM knows what it's allowed
# to choose.
ShotSource = Literal[
    "image_gen",       # Static generated image, held for ``duration_s``
    "image_kenburns",  # Generated image + Ken Burns zoom/pan animation
    "pexels",          # Pexels stock video clip (real footage)
    "generative",      # Hero shot: animate the stylized generated still (Wan i2v)
    "wan21",           # DEPRECATED alias of ``generative`` (legacy shot lists)
    "holdover",        # Cross-fade transition from prior shot (no asset)
]


# Human-indicator nouns scanned in AI-source prompts. Faces, hands, and
# bodies are the worst AI-tell zones — Pexels stays human-friendly
# (real footage), AI sources should route around humans or use faceless
# silhouettes. See ``feedback_no_humans_in_ai_media``. Soft-warning
# only (not a rejection) until we see the false-positive rate.
_HUMAN_TOKENS: tuple[str, ...] = (
    "person", "people", "man", "woman", "men", "women", "boy", "girl",
    "child", "children", "human", "humans", "guy", "lady", "kid",
    "face", "faces", "hand", "hands", "finger", "fingers",
    "developer", "engineer", "programmer", "designer", "manager",
    "founder", "ceo", "team", "crowd", "audience",
)
_HUMAN_TOKEN_RE = re.compile(
    r"\b(" + "|".join(_HUMAN_TOKENS) + r")\b",
    re.IGNORECASE,
)
# ``silhouette`` / ``faceless`` are the escape hatch — if the director
# explicitly framed the human as a faceless silhouette, the rule is
# satisfied. Match the canonical phrasing from
# ``source_featured_image.py`` so the convention carries over.
_SILHOUETTE_RE = re.compile(r"\b(silhouette|faceless)\b", re.IGNORECASE)


def scan_for_human_tokens(text: str) -> list[str]:
    """Return human-indicator nouns found in ``text``.

    Case-insensitive, deduplicated, lowercase. If the text frames the
    figure as ``silhouette`` or ``faceless`` we treat the rule as
    satisfied and return ``[]`` — that's the established Glad Labs
    convention (see ``source_featured_image.py``: "faceless silhouettes
    OK but no identifiable faces").
    """
    if not text:
        return []
    if _SILHOUETTE_RE.search(text):
        return []
    return sorted({m.group(1).lower() for m in _HUMAN_TOKEN_RE.finditer(text)})


class Shot(BaseModel):
    """One shot in the composition.

    ``prompt`` is the generation prompt for image-gen / Wan2.1 sources;
    ``query`` is the stock-library search query for Pexels. Exactly
    one of the two is required for those sources (validated below).
    ``holdover`` shots need neither — they're pure transitions.

    ``narration_offset_s`` is where in the podcast audio this shot
    starts. The renderer slices the audio rather than re-narrating
    per shot — keeps the podcast as the single source of voice.
    """

    idx: int = Field(..., ge=0, description="Zero-based shot index")
    duration_s: float = Field(..., gt=0, le=30, description="Target shot duration in seconds")
    intent: str = Field(..., min_length=1, max_length=200, description="Director's note on why this shot exists")
    source: ShotSource = Field(..., description="Which plugin renders this shot")
    prompt: str | None = Field(None, max_length=2000, description="Generation prompt for image-gen/Wan2.1 sources")
    query: str | None = Field(None, max_length=200, description="Stock-library search query for Pexels source")
    kenburns_zoom: tuple[float, float] | None = Field(
        None,
        description="Start/end zoom for image_kenburns shots (e.g. (1.0, 1.2))",
    )
    narration_offset_s: float = Field(..., ge=0, description="Audio offset where this shot's narration starts")

    @model_validator(mode="after")
    def _validate_source_inputs(self) -> Shot:
        """Each source requires its specific input fields.

        Fail loud per ``feedback_no_silent_defaults`` — a director that
        returns an image-gen shot with no prompt would otherwise produce an
        empty clip silently.
        """
        if self.source in ("image_gen", "image_kenburns", "wan21", "generative"):
            if not self.prompt:
                raise ValueError(
                    f"source={self.source!r} requires a non-empty ``prompt``",
                )
        elif self.source == "pexels":
            if not self.query:
                raise ValueError(
                    "source='pexels' requires a non-empty ``query``",
                )
        elif self.source == "holdover":
            # Holdover is a pure transition — no asset, no prompt, no query.
            if self.prompt or self.query:
                raise ValueError(
                    "source='holdover' must not have a prompt or query",
                )

        if self.source == "image_kenburns" and self.kenburns_zoom is not None:
            start, end = self.kenburns_zoom
            if start <= 0 or end <= 0:
                raise ValueError("kenburns_zoom values must be positive")

        if self.source in ("image_gen", "image_kenburns", "wan21", "generative") and self.prompt:
            human_tokens = scan_for_human_tokens(self.prompt)
            if human_tokens:
                logger.warning(
                    "video shot idx=%s source=%s prompt has human-indicator "
                    "tokens %s — AI-generated humans are the worst AI-tell "
                    "zone. Route human shots through source='pexels' (real "
                    "footage) or rephrase as 'faceless silhouette'.",
                    self.idx, self.source, human_tokens,
                )

        return self


class VideoShotList(BaseModel):
    """The full shot list stored on ``posts.video_shot_list``.

    ``shots`` are ordered by ``idx``; the renderer concats in that
    order. ``total_duration_s`` MUST equal the sum of the shots'
    durations (validated below) — a director that produced a 47-second
    shot list but claimed 60 seconds total would otherwise leave the
    renderer guessing.

    ``director_*`` fields capture provenance for the feedback loop:
    next-gen director runs can read prior shot lists to learn what
    worked + what got rejected.
    """

    version: int = Field(1, description="Schema version; bump on breaking changes")
    aspect: Literal["16:9", "9:16"] = Field(
        "16:9",
        description="Output aspect ratio: 16:9 long-form, 9:16 short-form",
    )
    total_duration_s: float = Field(..., gt=0, description="Sum of shot durations; matches podcast length ±1s")
    shots: list[Shot] = Field(..., min_length=1, max_length=30)
    director_model: str = Field(..., description="LLM model that produced this shot list")
    director_prompt_version: str = Field(..., description="Prompt version (e.g. 'v1') for traceability")
    director_decided_at: datetime = Field(..., description="When the director produced this output")

    @model_validator(mode="after")
    def _validate_durations(self) -> VideoShotList:
        """Sum of shot durations must equal ``total_duration_s`` (±0.5s).

        The director sometimes off-by-one's the arithmetic; we tolerate
        small drift but fail loud on big mismatches. Also enforces shot
        idx is contiguous starting at 0 — the renderer relies on order.
        """
        shot_sum = sum(s.duration_s for s in self.shots)
        if abs(shot_sum - self.total_duration_s) > 0.5:
            raise ValueError(
                f"total_duration_s={self.total_duration_s} disagrees with "
                f"sum of shot durations ({shot_sum:.1f}); director should "
                f"reconcile before output",
            )

        expected_idx = list(range(len(self.shots)))
        actual_idx = [s.idx for s in self.shots]
        if actual_idx != expected_idx:
            raise ValueError(
                f"shots must be 0-indexed contiguous; got idx={actual_idx}",
            )

        # Pacing guard: no more than 2 consecutive shots from the same
        # non-holdover source. Holdovers don't count toward the streak
        # (they're transitions, not content). Matches the rule in the
        # director prompt — duplicate-source streaks visually drag.
        streak_src: str | None = None
        streak_count = 0
        for s in self.shots:
            if s.source == "holdover":
                streak_src = None
                streak_count = 0
                continue
            if s.source == streak_src:
                streak_count += 1
                if streak_count > 2:
                    raise ValueError(
                        f"more than 2 consecutive shots from source={s.source!r} "
                        f"at idx={s.idx} — director should diversify",
                    )
            else:
                streak_src = s.source
                streak_count = 1

        return self
