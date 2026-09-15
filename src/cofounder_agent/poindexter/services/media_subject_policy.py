"""Per-niche media subject and style policy — one seam, every surface.

(``services/media_policy.py`` is the older, unrelated "which media does this niche
generate" resolver; this module decides what those media may DEPICT.)

Whether AI-generated media may show people, and whether it may be photoreal,
used to be hardcoded in six places that had drifted apart: the director
prompt (people welcome, stylized), the blog writer prompt (never people), the
image negative prompt setting (``face, person, human, hands, fingers``), a
code constant with the same list, a shot-list validator that warns on any
human noun, and a vision QA check for photoreal humans. The rule was written
when diffusion models produced melted faces and six-fingered hands; the
current models do not, and a rule that cannot be tuned per niche cannot be
relaxed for one vertical and kept for another.

Two settings, resolved niche-first (DB-first per ``feedback_db_first_config``):

``media_human_subjects``  — ``allow`` (default) | ``stylized_only`` | ``none``
    allow          people are fine in AI media, in whatever style the style
                   policy permits
    stylized_only  people are fine but only in stylized styles, never photoreal
    none           no people, faces or hands in AI media; human subjects route
                   to real footage (Pexels) or a faceless silhouette

``media_style_policy``    — ``stylized`` (default) | ``any``
    stylized       AI prompts must name a stylized medium; photoreal buzzwords
                   are refused (the house style, and the AI-slop tell)
    any            photoreal is allowed when it serves the subject

Niche overrides: ``niche.<slug>.media.human_subjects`` and
``niche.<slug>.media.style_policy``. Unknown values log a warning and fall
back to the next level rather than silently picking something.

``media_negative_prompt_human_terms`` holds the terms appended to the image
negative prompt only when ``human_subjects == none``; the base
``image_negative_prompt`` no longer carries them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

HumanSubjects = Literal["allow", "stylized_only", "none"]
StylePolicy = Literal["stylized", "any"]

HUMAN_SUBJECTS_KEY = "media_human_subjects"
STYLE_POLICY_KEY = "media_style_policy"
HUMAN_TERMS_KEY = "media_negative_prompt_human_terms"

DEFAULT_HUMAN_SUBJECTS: HumanSubjects = "allow"
DEFAULT_STYLE_POLICY: StylePolicy = "stylized"
DEFAULT_HUMAN_TERMS = "face, person, human, hands, fingers"

_HUMAN_VALUES: tuple[str, ...] = ("allow", "stylized_only", "none")
_STYLE_VALUES: tuple[str, ...] = ("stylized", "any")


def niche_key(niche_slug: str, leaf: str) -> str:
    """``niche.<slug>.media.<leaf>`` — the same shape the media approvals use."""
    return f"niche.{niche_slug}.media.{leaf}"


@dataclass(frozen=True)
class MediaPolicy:
    human_subjects: HumanSubjects = DEFAULT_HUMAN_SUBJECTS
    style_policy: StylePolicy = DEFAULT_STYLE_POLICY
    niche_slug: str | None = None
    human_terms: str = DEFAULT_HUMAN_TERMS
    sources: tuple[str, str] = ("default", "default")  # where each value came from
    # On-camera presenter (persona_service): resolved here so every prompt pack
    # that renders policy text also knows whether a talking-head shot exists.
    presenter_slug: str = ""
    presenter_display_name: str = ""
    presenter_style: str = ""
    presenter_available: bool = False
    presenter_max_shots: int = 2

    @property
    def humans_allowed(self) -> bool:
        return self.human_subjects != "none"

    @property
    def photoreal_allowed(self) -> bool:
        return self.style_policy == "any"

    @property
    def photoreal_humans_allowed(self) -> bool:
        return self.human_subjects == "allow" and self.style_policy == "any"


def _get(site_config: Any, key: str) -> str:
    if site_config is None:
        return ""
    try:
        return str(site_config.get(key, "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — a policy read must never break a render
        logger.warning("[media_policy] could not read %s: %s", key, exc)
        return ""


def _resolve(site_config: Any, leaf: str, key: str, allowed: tuple[str, ...], default: str, niche_slug: str | None) -> tuple[str, str]:
    """(value, source) — niche override, then global, then the code default."""
    candidates: list[tuple[str, str]] = []
    if niche_slug:
        candidates.append((niche_key(niche_slug, leaf), "niche"))
    candidates.append((key, "global"))
    for setting_key, source in candidates:
        raw = _get(site_config, setting_key).lower()
        if not raw:
            continue
        if raw in allowed:
            return raw, source
        logger.warning(
            "[media_policy] %s=%r is not one of %s; ignoring it (no silent default — fix the setting)",
            setting_key, raw, allowed,
        )
    return default, "default"


PRESENTER_MAX_SHOTS_KEY = "video_presenter_shots_max"
DEFAULT_PRESENTER_MAX_SHOTS = 2


def _get_int(site_config: Any, key: str, default: int) -> int:
    raw = _get(site_config, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("[media_policy] %s=%r is not an integer; using %d", key, raw, default)
        return default


def _resolve_presenter(site_config: Any, niche_slug: str | None, *, human: str, style: str) -> tuple[str, str, str, bool]:
    """``(slug, display_name, style, available)`` for the niche's presenter persona.

    Available means: a persona resolves (``persona_service``), it is enabled,
    it has a portrait, and its style is allowed by the policy above — a
    photoreal presenter needs ``human_subjects=allow`` + ``style_policy=any``,
    a stylized one needs people allowed at all. Anything else is loud and
    unavailable, so the director is told "never emit presenter" rather than
    the renderer failing shots later.
    """
    from poindexter.services.persona_service import resolve_persona_for_niche

    persona = resolve_persona_for_niche(site_config, niche_slug)
    if persona is None:
        return "", "", "", False
    # A niche-less resolution is a generic policy read (the writer's image
    # subject rule, the post-edit negative prompt) — nothing there can put
    # the presenter on camera, so an "unavailable" verdict is not news. Only
    # the niche-bound callers (director, renderer, media dispatch) should be
    # loud: 18 WARNINGs in six hours came from the writer path alone.
    _say = logger.warning if niche_slug is not None else logger.debug
    display = persona.display_name or persona.slug
    if not persona.has_portrait:
        _say(
            "[media_policy] presenter %r resolves for niche %r but has no portrait — "
            "run `poindexter personas portrait %s`; presenter shots disabled",
            persona.slug, niche_slug, persona.slug,
        )
        return persona.slug, display, persona.style_policy, False
    if persona.style_policy == "photoreal" and not (human == "allow" and style == "any"):
        _say(
            "[media_policy] presenter %r is photoreal but niche %r policy is human_subjects=%s "
            "style_policy=%s — presenter shots disabled (loosen the niche policy or use a stylized persona)",
            persona.slug, niche_slug, human, style,
        )
        return persona.slug, display, persona.style_policy, False
    if human == "none":
        _say(
            "[media_policy] presenter %r cannot appear: niche %r forbids people (human_subjects=none)",
            persona.slug, niche_slug,
        )
        return persona.slug, display, persona.style_policy, False
    return persona.slug, display, persona.style_policy, True


def resolve_media_policy(site_config: Any, niche_slug: str | None = None) -> MediaPolicy:
    human, h_src = _resolve(site_config, "human_subjects", HUMAN_SUBJECTS_KEY, _HUMAN_VALUES, DEFAULT_HUMAN_SUBJECTS, niche_slug)
    style, s_src = _resolve(site_config, "style_policy", STYLE_POLICY_KEY, _STYLE_VALUES, DEFAULT_STYLE_POLICY, niche_slug)
    terms = _get(site_config, HUMAN_TERMS_KEY) or DEFAULT_HUMAN_TERMS
    slug, display, pstyle, available = _resolve_presenter(site_config, niche_slug, human=human, style=style)
    max_shots = _get_int(site_config, PRESENTER_MAX_SHOTS_KEY, DEFAULT_PRESENTER_MAX_SHOTS)
    return MediaPolicy(
        human_subjects=human, style_policy=style, niche_slug=niche_slug, human_terms=terms, sources=(h_src, s_src),
        presenter_slug=slug, presenter_display_name=display, presenter_style=pstyle,
        presenter_available=available, presenter_max_shots=max_shots,
    )  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Negative prompt
# ---------------------------------------------------------------------------

def _terms(csv: str) -> list[str]:
    return [t.strip() for t in csv.split(",") if t.strip()]


def negative_prompt(policy: MediaPolicy, base: str) -> str:
    """``base`` minus the human terms, plus the human terms only when humans are forbidden.

    The subtraction makes an old ``image_negative_prompt`` row that still lists
    ``face, person, …`` behave correctly under a permissive policy, so a
    niche can allow people without an operator hand-editing every install.
    """
    human = _terms(policy.human_terms)
    human_lc = {t.lower() for t in human}
    kept = [t for t in _terms(base) if t.lower() not in human_lc]
    if policy.human_subjects == "none":
        kept.extend(t for t in human if t.lower() not in {k.lower() for k in kept})
    return ", ".join(kept)


# ---------------------------------------------------------------------------
# Prompt fragments (the SKILL packs render these through {…} variables)
# ---------------------------------------------------------------------------

_VIDEO_ACTION = (
    "Keep the human ACTION specific and simple: one or two figures doing "
    "something concrete and relevant (\"speaking into a headset microphone\", "
    "\"pointing at a dashboard\"). Crowds and complex hand work are where any "
    "model is weakest."
)


def video_human_subject_policy(policy: MediaPolicy) -> str:
    """The HUMAN-SUBJECT POLICY block body for the video director prompts."""
    if not policy.humans_allowed:
        return (
            "No people in AI-rendered shots: no faces, hands, or figures, and never a human "
            "noun in an AI prompt, not even as \"no people\" (negations get rendered). A human "
            "subject routes to source=\"pexels\" (real footage) or, only if it MUST be AI, a "
            "faceless silhouette.\n"
            "Never render a \"diagram\" or \"chart\" as the SUBJECT of an AI shot (diffusion "
            "models fill those with garbled fake labels); abstract data shapes on a screen "
            "are fine."
        )
    if policy.photoreal_humans_allowed:
        lead = (
            "People are welcome in AI-rendered shots, stylized or photoreal — the current "
            "image models render people cleanly."
        )
        rules = f"- {_VIDEO_ACTION}\n"
    else:
        lead = (
            "People are welcome in AI-rendered shots, in the STYLIZED styles below (the "
            "current image models render people cleanly in illustration styles). Two rules "
            "still hold, because they are what keeps AI people clean:"
        )
        rules = (
            "- NEVER photoreal for a human — \"photorealistic\" / \"8K\" / \"DSLR\" humans still "
            "land in uncanny territory. Stylized illustration is the house style anyway.\n"
            f"- {_VIDEO_ACTION}\n"
        )
    return (
        f"{lead}\n{rules}"
        "Never render a \"diagram\" or \"chart\" as the SUBJECT of an AI shot (diffusion "
        "models fill those with garbled fake labels); abstract data shapes on a screen "
        "are fine."
    )


def video_human_subject_rule(policy: MediaPolicy) -> str:
    """The one-line numbered-rule form of the same policy."""
    if not policy.humans_allowed:
        return (
            "Human subject → source=\"pexels\" (or a faceless silhouette only if it MUST be AI). "
            "Never name a human noun in an AI prompt, not even as \"no people\"."
        )
    if policy.photoreal_humans_allowed:
        return "Human subjects are allowed in AI prompts; keep the action specific and simple."
    return (
        "Human subjects are allowed in AI prompts in stylized styles only — never a photoreal "
        "human; keep the action specific and simple."
    )


def video_style_policy(policy: MediaPolicy) -> str:
    """The STYLE POLICY FOR AI SOURCES block body."""
    if policy.photoreal_allowed:
        return (
            "image_gen / image_kenburns / generative prompts may be stylized OR photoreal. "
            "Stylized: pick a modifier such as flat vector illustration / isometric 3D / line "
            "art / cyberpunk neon / low poly / watercolor / paper cutout. Photoreal: ground the "
            "shot in real photographic language (lens, light, material) and skip \"8K\" / "
            "\"hyper-realistic\" buzzwords, which trigger the AI tell.\n"
            "Pexels is exempt from the style policy — it IS real footage."
        )
    return (
        "image_gen / image_kenburns / generative prompts must be STYLIZED, not photoreal — "
        "photorealistic AI output reads as slop. Pick a stylized modifier:\n"
        "flat vector illustration / cinematic illustration / isometric 3D /\n"
        "line art / cyberpunk neon / glassmorphism / low poly / watercolor /\n"
        "pixel art / paper cutout. Never include \"photorealistic\", \"8K\", \"DSLR\",\n"
        "\"hyper-realistic\", \"cinematic photography\" — those trigger the AI tell.\n"
        "\n"
        "Pexels is exempt from the style policy — it IS real footage."
    )


def writer_image_subject_rule(policy: MediaPolicy) -> str:
    """The blog writer's [IMAGE:] subject rule (first sentence of the bullet)."""
    if not policy.humans_allowed:
        return (
            "Never put identifiable people, faces, hands, or any text/words in an image "
            "subject — the brand style is objects, hardware, and environments."
        )
    style = "" if policy.photoreal_humans_allowed else " (they will be rendered stylized, never photoreal)"
    return (
        f"People may appear in an image subject when the section is about them{style} — one "
        "or two figures doing something concrete; never put text/words in an image subject."
    )


def image_people_sentence(policy: MediaPolicy) -> str:
    """The people sentence for the image.* prompt-writer packs."""
    if not policy.humans_allowed:
        return "No people, faces, or hands in the scene."
    if policy.photoreal_humans_allowed:
        return "People are fine when the subject involves them, doing something concrete."
    return "People are fine when the subject involves them — stylized, never photoreal, doing something concrete."


def image_decision_people_rule(policy: MediaPolicy) -> str:
    """Rule 5 of the image.decision pack (people clause only; the text/diagram tail is static)."""
    if not policy.humans_allowed:
        return "Never put people, faces, or hands in AI-generated images — the brand style is objects, hardware, and environments."
    if policy.photoreal_humans_allowed:
        return "People are permitted and often clearer than a metaphor — one or two figures, doing something concrete and relevant."
    return (
        "People are permitted and often clearer than a metaphor — render them STYLIZED (never "
        "photorealistic), one or two figures, doing something concrete and relevant."
    )


def video_presenter_policy(policy: MediaPolicy) -> str:
    """The PRESENTER section of the director prompts: whether a talking-head
    shot exists for this niche and how it may be used."""
    if not policy.presenter_available:
        return (
            'No on-camera presenter is configured for this niche. NEVER emit '
            'source "presenter".'
        )
    return (
        f'PRESENTER AVAILABLE. "{policy.presenter_display_name}" is this channel\'s '
        'on-camera presenter: source "presenter" renders a talking-head clip of them '
        "speaking that shot's narration, lip-synced to the voice track. Use it for the "
        'opening address, the close, or one direct-address beat where a person speaking '
        f'to the viewer lands harder than footage. At most {policy.presenter_max_shots} '
        'presenter shots per video, each 3-10 seconds. No "query" and no "demo_id"; an '
        'optional "prompt" is a one-line delivery note (mood, framing), never a scene '
        'description. A presenter shot MAY open or close the video.'
    )


def video_contains_synthetic_media(policy: MediaPolicy, shot_list: Any) -> bool:
    """Does this video show a realistic synthetic person?

    True when the shot list holds a ``presenter`` shot and the niche's persona
    is photoreal — a stylized presenter is a character, not a likeness. This
    is what YouTube's ``status.containsSyntheticMedia`` disclosure asks about;
    ``media_distribute`` derives the flag from it unless
    ``youtube_contains_synthetic_media`` forces a value.
    """
    shots: Any = None
    if isinstance(shot_list, dict):
        shots = shot_list.get("shots")
    elif shot_list is not None:
        shots = getattr(shot_list, "shots", None)
    if not shots:
        return False
    for shot in shots:
        source = shot.get("source") if isinstance(shot, dict) else getattr(shot, "source", "")
        if str(source or "") == "presenter":
            return policy.presenter_style == "photoreal"
    return False


def prompt_variables(policy: MediaPolicy) -> dict[str, str]:
    """Every policy-derived template variable, for call sites that render several packs."""
    return {
        "human_subject_policy": video_human_subject_policy(policy),
        "human_subject_rule": video_human_subject_rule(policy),
        "style_policy": video_style_policy(policy),
        "image_subject_rule": writer_image_subject_rule(policy),
        "people_sentence": image_people_sentence(policy),
        "people_rule": image_decision_people_rule(policy),
        "presenter_policy": video_presenter_policy(policy),
    }
