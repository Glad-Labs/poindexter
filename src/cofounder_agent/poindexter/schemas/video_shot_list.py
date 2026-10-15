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

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

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
    "cli_demo",        # Pre-baked recording of the real CLI (see demo_clips)
    "presenter",       # Talking-head clip of the niche's persona speaking this shot's narration (S2V)
]


# Retired ``source`` spellings mapped onto their canonical replacements
# (issue #874). A shot list is frozen into ``pipeline_versions`` at Stage 1 and
# re-read by the renderer at Stage 2 — sometimes weeks later — so renaming a
# source without a shim strands every already-frozen list that still carries
# the old spelling. #1947 renamed sdxl/sdxl_kenburns with no alias and left
# task 511012cc permanently unrenderable (its list froze the last day ``sdxl``
# was legal); the watchdog then re-dispatched it ~31 times against the same
# failure.
#
# Normalized here rather than added to ``ShotSource`` on purpose. Widening the
# Literal would leave the retired spellings live in the vocabulary, so every
# downstream branch that matches on source (``_HERO_SOURCES``,
# ``_IMAGE_GEN_FAMILY``, the vision-QA retry set, ...) would have to list them
# too — the same N-site burden that made #1947 miss this. It would also let
# them fall through every branch of ``_validate_source_inputs`` below, skipping
# the prompt requirement entirely. Rewriting on the way in means the rest of
# the codebase never learns these names exist.
_DEPRECATED_SOURCES: dict[str, str] = {
    "sdxl": "image_gen",
    "sdxl_kenburns": "image_kenburns",
}


# Human-indicator nouns scanned in AI-source prompts. These exist ONLY to
# serve the ``human_subjects='none'`` branch of the per-niche media policy
# (services/media_subject_policy.py) — they are not a house rule.
#
# The blanket "no AI humans" rule was retired 2026-09-14: it was written for
# SDXL's melted faces, and the current stack renders people cleanly. Measured
# again 2026-09-20 across Wan 2.2 / LTX-2.5 / HunyuanVideo 1.5 — AI humans in
# hero shots came back clean on all three, while routing human shots to stock
# footage is what put an unrelated muralist into two published videos (#3884).
# The default policy is ``allow``; ``glad-labs`` additionally allows photoreal.
# Soft-warning only, and only when a caller passes a ``none`` policy context.
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

# ``demo_id`` resolves to a filename in the demo-clip directory, and its value
# is LLM-authored, so the character set is constrained rather than trusted.
# Matches the slugs ``services/demo_clips.py`` derives from tape filenames.
_DEMO_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,99}")

#: Persona slug shape — must match ``persona_service._SLUG_RE``, which a test
#: pins. Repeated here rather than imported so the schema stays dependency-free.
_PERSONA_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

#: Hard cap on shots in one list (poindexter#1135).
MAX_SHOTS = 120


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

    ``motion`` is the image-to-video direction for ``generative`` shots:
    one sentence describing the camera move and what animates. The still
    image already carries composition/subject/style, so the i2v model
    needs motion language — re-sending the still's own description gives
    it nothing to animate. Optional (frozen pre-motion lists lack it;
    the renderer substitutes a configurable default), inert on other
    sources.

    ``narration_offset_s`` is where in the podcast audio this shot
    starts. The renderer slices the audio rather than re-narrating
    per shot — keeps the podcast as the single source of voice.
    """

    idx: int = Field(..., ge=0, description="Zero-based shot index")
    duration_s: float = Field(..., gt=0, le=30, description="Target shot duration in seconds")
    intent: str = Field(..., min_length=1, max_length=200, description="Director's note on why this shot exists")
    source: ShotSource = Field(..., description="Which plugin renders this shot")
    prompt: str | None = Field(None, max_length=2000, description="Generation prompt for image-gen/Wan2.1 sources")
    motion: str | None = Field(
        None,
        max_length=500,
        description="Camera + subject motion direction for generative (i2v) shots",
    )
    query: str | None = Field(None, max_length=200, description="Stock-library search query for Pexels source")
    demo_id: str | None = Field(
        None,
        max_length=100,
        description="Catalog slug of a pre-baked CLI recording (cli_demo source)",
    )
    kenburns_zoom: tuple[float, float] | None = Field(
        None,
        description="Start/end zoom for image_kenburns shots (e.g. (1.0, 1.2))",
    )
    narration_offset_s: float = Field(..., ge=0, description="Audio offset where this shot's narration starts")
    # Cast fields (creative studio, poindexter#1135). Optional so every frozen
    # shot list still validates. Both hold persona slugs; the renderer looks
    # them up as ``persona.<slug>.*`` settings, so they are constrained to the
    # slug shape like ``demo_id``.
    characters: list[str] = Field(
        default_factory=list,
        max_length=6,
        description=(
            "Persona slugs in frame. Their ``appearance`` text is joined into "
            "the still's prompt in code, keeping recurring characters on-model"
        ),
    )
    speaker: str | None = Field(
        None,
        description=(
            "Persona slug that speaks in a ``presenter`` shot. Set: that "
            "persona's portrait is lip-synced. Unset: the niche's persona"
        ),
    )

    frame_prompt: str | None = Field(
        None,
        max_length=1500,
        description=(
            "Cast shots: the prompt for this scene's talking frame — the speaker "
            "drawn in this scene's setting and style, from their portrait. The "
            "lip-sync animates it instead of the fixed portrait (poindexter#1141)"
        ),
    )
    frame_full_body: bool = Field(
        False,
        description=(
            "Cast shots: the talking frame shows the speaker's body (a medium or wider "
            "setup), so it is drawn from their model sheet's full-body view"
        ),
    )
    # Backdrop continuity (2026-10-14). Optional, so every frozen list still
    # validates. A screenplay shot carries its scene, and one shot per scene is
    # its establishing picture: every other picture of the scene is drawn with
    # it as a "this is the place" reference, so close-ups and inserts keep the
    # same background instead of each inventing its own.
    scene_id: str | None = Field(None, max_length=64, description="The screenplay scene this shot belongs to")
    location_id: str | None = Field(
        None, max_length=64,
        description="The screenplay location this shot is set in; its scenes share one background plate",
    )
    palette: str | None = Field(
        None, max_length=300,
        description=(
            "The scene's colour-script entry: its colours and light. A location seen "
            "in another palette has its plate relit to this one"
        ),
    )
    establishing: bool = Field(
        False,
        description="This shot's picture is its scene's place reference for the scene's other pictures",
    )
    place_prompt: str | None = Field(
        None,
        max_length=1500,
        description=(
            "On an establishing shot: the scene's empty background plate (the place with "
            "nobody in it), drawn once and used as every picture's place reference"
        ),
    )
    frame_url: str | None = Field(
        None,
        max_length=1000,
        description=(
            "An approved first frame (a storyboard's picture of this shot). The renderer "
            "starts from it instead of drawing the shot again, so the video keeps the "
            "storyboard's pictures and backgrounds"
        ),
    )

    @field_validator("characters")
    @classmethod
    def _validate_characters_are_slugs(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for slug in value or []:
            slug = (slug or "").strip().lower()
            if not _PERSONA_SLUG_RE.fullmatch(slug):
                raise ValueError(
                    f"characters entry {slug!r} must be a persona slug "
                    "(a-z, 0-9, '-'; e.g. 'dex')",
                )
            if slug not in cleaned:
                cleaned.append(slug)
        return cleaned

    @field_validator("speaker")
    @classmethod
    def _validate_speaker_is_a_slug(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        slug = value.strip().lower()
        if not _PERSONA_SLUG_RE.fullmatch(slug):
            raise ValueError(
                f"speaker {slug!r} must be a persona slug (a-z, 0-9, '-'; e.g. 'dex')",
            )
        return slug

    @field_validator("demo_id")
    @classmethod
    def _validate_demo_id_is_a_slug(cls, value: str | None) -> str | None:
        """Constrain ``demo_id`` to a bare slug.

        This value is LLM-authored and the renderer resolves it to a filename,
        so an unconstrained string is a path-traversal seam — ``../../`` or an
        absolute path would read outside the clip directory. Anchored here as
        well as at the render site: the schema is what a frozen shot list is
        re-validated against weeks later, so it is the durable boundary.
        """
        if value is None:
            return None
        if not _DEMO_ID_RE.fullmatch(value):
            raise ValueError(
                f"demo_id {value!r} must be a lowercase slug "
                f"(letters, digits, hyphens; e.g. 'posts-list')",
            )
        return value

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_deprecated_source(cls, value: object) -> object:
        """Rewrite retired source spellings to their canonical names.

        Runs before the ``ShotSource`` literal check, so both this model's
        own rules and every downstream renderer branch only ever see a
        canonical value. Unknown sources fall through untouched and are
        rejected by the Literal — this is a fixed alias map, not a wildcard.
        """
        if isinstance(value, str) and value in _DEPRECATED_SOURCES:
            canonical = _DEPRECATED_SOURCES[value]
            logger.info(
                "shot source %r is retired — normalized to %r (frozen shot "
                "list predates the rename)",
                value,
                canonical,
            )
            return canonical
        return value

    @model_validator(mode="after")
    def _validate_source_inputs(self, info: ValidationInfo) -> Shot:
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
        elif self.source == "cli_demo":
            # The director SELECTS a pre-baked recording; it never authors the
            # command. A prompt/query here would mean the model tried to
            # describe or generate footage instead of choosing from the
            # catalog, so reject rather than silently ignore the extra field.
            if not self.demo_id:
                raise ValueError(
                    "source='cli_demo' requires a ``demo_id`` naming a catalog "
                    "entry (see `poindexter media demos list`)",
                )
            if self.prompt or self.query:
                raise ValueError(
                    "source='cli_demo' must not have a prompt or query — the "
                    "clip is pre-recorded, not generated",
                )
        elif self.source == "presenter":
            # The persona and the narration window define the shot; a
            # "prompt" here is an optional delivery note. Query / demo_id would
            # mean the director confused it with stock or a recording.
            if self.query or self.demo_id:
                raise ValueError(
                    "source='presenter' must not carry a ``query`` or ``demo_id`` — "
                    "the niche's persona and the narration window define the shot "
                    "(an optional ``prompt`` is a delivery note)",
                )
            if str((info.context or {}).get("human_subjects", "")) == "none":
                raise ValueError(
                    "source='presenter' needs a niche whose media policy allows "
                    "people (human_subjects=none forbids an on-camera presenter)",
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

        # The human-noun scan is the ``none`` branch of the per-niche media policy
        # (services/media_subject_policy.py). Callers that know the niche pass
        # ``context={"human_subjects": policy.human_subjects}``; with no context the
        # scan runs (the pre-policy behaviour), so an unaware caller stays strict.
        ctx = info.context or {}
        scan_humans = str(ctx.get("human_subjects", "none")) == "none"
        if scan_humans and self.source in ("image_gen", "image_kenburns", "wan21", "generative"):
            for field_name, text in (("prompt", self.prompt), ("motion", self.motion)):
                if not text:
                    continue
                human_tokens = scan_for_human_tokens(text)
                if human_tokens:
                    logger.warning(
                        "video shot idx=%s source=%s %s has human-indicator "
                        "tokens %s — AI-generated humans are the worst AI-tell "
                        "zone. Route human shots through source='pexels' (real "
                        "footage) or rephrase as 'faceless silhouette'.",
                        self.idx, self.source, field_name, human_tokens,
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
    # How the renderer times the shots against the narration (poindexter#1134).
    # "fit": the director planned on an estimated timeline, so the assembly
    # stretches/compresses shots to the real narration (min/max shot clamps,
    # the end-card carve-out) — the blog explainer path. "exact": the shots
    # already tile the measured track (the screenplay director builds them
    # from media.render_dialogue's timeline), so any fit would move them off
    # their words. The 2-minute screenplay test on 2026-10-12 drifted 7.4 s
    # by the end under "fit": its 0.5-1.5 s action shots were clamped up to
    # video_fit_min_shot_seconds (2.0), and every lip-sync window after them
    # was cut from later audio.
    timing: Literal["fit", "exact"] = Field(
        "fit",
        description="'fit' = stretch shots to the narration; 'exact' = shots already tile the track",
    )
    # 120, not the blog director's 30: a three-minute screenplay at ~5 s a
    # shot is ~36 shots (poindexter#1135). The blog director keeps its own cap.
    shots: list[Shot] = Field(..., min_length=1, max_length=MAX_SHOTS)
    # A reused character drawn in this piece's art style, for this piece only
    # (story.cast_characters): slug -> portrait URL. The renderer draws that
    # character's stills and talking frames from it instead of the persona's
    # own portrait, which may be in another piece's style. Empty = every
    # persona's own portrait.
    cast_portraits: dict[str, str] = Field(
        default_factory=dict,
        description="Per-piece portrait URL by persona slug, overriding persona.<slug>.portrait_url",
    )
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
        # A presenter shot's streak key includes its speaker: a dialogue cuts
        # between talking characters (shot / reverse shot), and that is a
        # change of picture even though every shot is a ``presenter``.
        streak_src: tuple[str, str | None] | None = None
        streak_count = 0
        for s in self.shots:
            if s.source == "holdover":
                streak_src = None
                streak_count = 0
                continue
            # An animated shot is keyed by its picture: three different
            # animated moments in a row are a scene in motion, not a repeat
            # (a cartoon animates every shot, 2026-10-14).
            key: tuple[str, str | None]
            if s.source == "presenter":
                key = (s.source, s.speaker)
            elif s.source == "generative":
                key = (s.source, s.prompt)
            else:
                key = (s.source, None)
            if key == streak_src:
                streak_count += 1
                if streak_count > 2:
                    raise ValueError(
                        f"more than 2 consecutive shots from source={s.source!r} "
                        f"at idx={s.idx} — director should diversify",
                    )
            else:
                streak_src = key
                streak_count = 1

        return self
