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
# One look per video. Free text, prepended to every AI-source prompt by the
# director and used verbatim for escalations. Empty = the director may vary
# the modifier per shot (the pre-2026-09-22 behaviour). Niche override:
# ``niche.<slug>.media.house_style``.
HOUSE_STYLE_KEY = "media_house_style"
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
    house_style: str = ""
    niche_slug: str | None = None
    human_terms: str = DEFAULT_HUMAN_TERMS
    sources: tuple[str, str] = ("default", "default")  # where each value came from
    # On-camera presenter (persona_service): resolved here so every prompt pack
    # that renders policy text also knows whether a talking-head shot exists.
    presenter_slug: str = ""
    presenter_display_name: str = ""
    presenter_style: str = ""
    presenter_available: bool = False
    # Negative = no ceiling (the default): the three format beats are required
    # and the director adds as many more as the script earns.
    presenter_max_shots: int = -1

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
# -1 = uncapped. The operator asked for the presenter at the opening, the
# midpoint and the close as the format, with no limit on further presenter
# shots; a ceiling stays available as a GPU budget (each clip is a full S2V
# render), not as a format rule.
DEFAULT_PRESENTER_MAX_SHOTS = -1


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


def _resolve_text(site_config: Any, leaf: str, key: str, niche_slug: str | None) -> str:
    """Free-text setting: niche override, then global, then ``""``."""
    if niche_slug:
        val = _get(site_config, niche_key(niche_slug, leaf))
        if val:
            return val
    return _get(site_config, key)


def resolve_media_policy(site_config: Any, niche_slug: str | None = None) -> MediaPolicy:
    human, h_src = _resolve(site_config, "human_subjects", HUMAN_SUBJECTS_KEY, _HUMAN_VALUES, DEFAULT_HUMAN_SUBJECTS, niche_slug)
    style, s_src = _resolve(site_config, "style_policy", STYLE_POLICY_KEY, _STYLE_VALUES, DEFAULT_STYLE_POLICY, niche_slug)
    house = _resolve_text(site_config, "house_style", HOUSE_STYLE_KEY, niche_slug)
    terms = _get(site_config, HUMAN_TERMS_KEY) or DEFAULT_HUMAN_TERMS
    slug, display, pstyle, available = _resolve_presenter(site_config, niche_slug, human=human, style=style)
    max_shots = _get_int(site_config, PRESENTER_MAX_SHOTS_KEY, DEFAULT_PRESENTER_MAX_SHOTS)
    return MediaPolicy(
        human_subjects=human, style_policy=style, house_style=house, niche_slug=niche_slug, human_terms=terms, sources=(h_src, s_src),
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


def house_style_block(policy: MediaPolicy) -> str:
    """The one-look instruction, or ``""`` when no house style is set.

    Measured 2026-09-21 over the last ten shot lists: 4-6 distinct style
    modifiers across 5-8 AI shots in EVERY short — a purple 3D render, a gold
    key on white, a flat-vector businessman and a watercolour flower in one
    63-second clip. Each shot was on-style by itself; the video had no look.
    """
    if not policy.house_style:
        return ""
    return (
        "HOUSE STYLE — ONE LOOK PER VIDEO\n"
        "EVERY image_gen / image_kenburns / generative prompt starts with the "
        f"exact words \"{policy.house_style}\", then a comma, then that shot's "
        "subject. Do not vary it between shots; the subject changes, the look "
        "does not. Pexels is exempt (real footage).\n\n"
    )


def video_style_prefix(policy: MediaPolicy) -> str:
    """What every AI prompt in the director's WORKED EXAMPLES begins with.

    The examples used to carry three DIFFERENT literal modifiers — flat
    vector illustration, cinematic illustration, cyberpunk neon illustration
    — and a director copies an example far more readily than it obeys a rule:
    on the 2026-09-22 NCCL pair those three accounted for 6 of the 8 AI
    shots. Worse, two of them sat in the SAME example shot list, so the
    examples were demonstrating a different look per shot, which is the exact
    thing the house style forbids. One prefix, shown consistently.
    """
    return policy.house_style or "flat vector illustration"


def video_style_policy(policy: MediaPolicy) -> str:
    """The STYLE POLICY FOR AI SOURCES block body.

    A house style REPLACES the modifier menu; it does not sit on top of it.
    Prepending was tried first and measured to do nothing: the block said "do
    not vary the modifier" and the very next paragraph offered seven to pick
    from, so the director picked. On the 2026-09-22 NCCL pair, 0 of 12 AI
    shots began with the house style and five different menu modifiers did —
    cinematic illustration x3, flat vector illustration x2, cyberpunk neon,
    isometric 3D, and (via the photoreal branch) abstract photorealism.
    """
    if policy.house_style:
        return house_style_block(policy) + _house_style_tail()
    return _video_style_policy_base(policy)


def _house_style_tail() -> str:
    """What still applies once the look is settled.

    Deliberately names NO modifier: a list here is a menu, and the director
    reads a menu as an invitation. Whether shots read as illustration or as
    photography is decided by the house style string itself, so this tail is
    style-agnostic and only keeps the buzzword ban, which is an AI tell in
    every style.
    """
    return (
        "That house style is the ONLY modifier. Do not add a second one, do "
        "not swap in a different one for variety, and do not give any shot a "
        "look of its own — whether these shots read as illustration or as "
        "photography is already settled above.\n"
        "Never include \"8K\", \"DSLR\", \"hyper-realistic\" or \"ultra-detailed\" "
        "in a prompt: those trigger the AI tell whatever the style.\n"
        "\n"
        "Pexels is exempt from the style policy — it IS real footage."
    )


def _video_style_policy_base(policy: MediaPolicy) -> str:
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


# The talking-head format, in priority order: the presenter OPENS the video
# (the hook, said to camera), comes back at the MIDPOINT (the turn) and CLOSES
# it (the takeaway). A capped budget keeps the first N, so a single presenter
# shot still opens on the face and two never collapse into the bare bookend
# pair the operator rejected on 2026-09-23.
PRESENTER_BEATS: tuple[str, ...] = ("opening", "midpoint", "closing")

_BEAT_DIRECTIONS: dict[str, str] = {
    "opening": "OPENING: shot 0 is the presenter saying the hook straight to camera.",
    "midpoint": (
        "MIDDLE: one presenter shot in the middle third of the running time, on the "
        "turn: the line that reframes the problem or lands the key claim."
    ),
    "closing": (
        "CLOSING: the last shot is the presenter saying the takeaway as the "
        "sign-off. The branded end card follows it automatically."
    ),
}

# Sources a beat never overwrites: a holdover is a half-second cross-fade with
# no room for a face, and a cli_demo is real footage of the product the beat
# would erase.
_BEAT_KEEP_SOURCES = frozenset({"holdover", "cli_demo"})
_PRESENTER_SOURCE = "presenter"


@dataclass(frozen=True)
class PresenterFormat:
    """What :func:`place_presenter_beats` needs from the policy: how many
    beats to guarantee, the ceiling (negative = none), and the style prefix a
    demoted over-cap shot is re-prompted with."""

    beats: int
    max_shots: int
    style_prefix: str = ""


def presenter_beat_count(policy: MediaPolicy) -> int:
    """Beats this video carries: none without a presenter, otherwise all three
    unless a ceiling below three trims them (a negative ceiling is no ceiling)."""
    if not policy.presenter_available:
        return 0
    cap = policy.presenter_max_shots
    if cap < 0:
        return len(PRESENTER_BEATS)
    return min(cap, len(PRESENTER_BEATS))


def presenter_format(policy: MediaPolicy) -> PresenterFormat:
    return PresenterFormat(
        beats=presenter_beat_count(policy),
        max_shots=policy.presenter_max_shots,
        style_prefix=video_style_prefix(policy),
    )


def video_presenter_policy(policy: MediaPolicy) -> str:
    """The PRESENTER section of the director and review prompts: whether a
    talking-head shot exists for this niche, and the format it follows."""
    if not policy.presenter_available:
        return (
            'No on-camera presenter is configured for this niche. NEVER emit '
            'source "presenter".'
        )
    name = policy.presenter_display_name
    beats = presenter_beat_count(policy)
    if beats == 0:
        return (
            f'"{name}" is this channel\'s on-camera presenter, but the presenter '
            'budget (video_presenter_shots_max) is 0. NEVER emit source "presenter".'
        )
    # Only the beats are fixed. How many more presenter shots, and how long any
    # of them runs, is the director's call: the operator's rule (2026-09-23) is
    # no limit that the render does not need in order to work.
    cap = policy.presenter_max_shots
    if cap < 0:
        more = (
            "Beyond those, use the presenter wherever a person speaking to the viewer "
            "lands harder than footage: the line that names the stakes, the one claim "
            "you want them to believe. The script decides how many."
        )
    elif cap > beats:
        more = (
            "Beyond those, use the presenter wherever a person speaking to the viewer "
            f"lands harder than footage, within this channel's budget of {cap} "
            "presenter shots in total."
        )
    else:
        more = f"This channel's presenter budget ({cap}) covers only those beats."
    return "\n".join([
        f'PRESENTER AVAILABLE. "{name}" is this channel\'s on-camera presenter: '
        'source "presenter" renders a talking-head clip of them speaking that '
        "shot's narration, lip-synced to the voice track.",
        "THE FORMAT: the presenter anchors the video at these beats:",
        *(f"- {_BEAT_DIRECTIONS[b]}" for b in PRESENTER_BEATS[:beats]),
        more,
        'A presenter shot carries no "query" and no "demo_id". Its optional '
        '"prompt" is a one-line delivery note (mood, framing) that is added to the '
        "talking-head render, so it describes the delivery, not a scene.",
    ])


def _as_seconds(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def presenter_beat_indices(durations: list[Any], beats: int) -> list[int]:
    """The target shot index of each of the first ``beats`` beats, in beat order.

    opening = shot 0, closing = the last shot, midpoint = the interior shot
    whose span holds the halfway point of the running time. A list too short to
    keep them apart returns fewer (a two-shot list has no midpoint of its own).
    """
    n = len(durations)
    if n == 0 or beats <= 0:
        return []
    spans = [_as_seconds(d) for d in durations]
    half, start, mid = sum(spans) / 2.0, 0.0, n // 2
    for i, span in enumerate(spans):
        if span > 0 and start <= half < start + span:
            mid = i
            break
        start += span
    if n >= 3:
        mid = min(max(mid, 1), n - 2)
    target = {"opening": 0, "midpoint": mid, "closing": n - 1}
    out: list[int] = []
    for beat in PRESENTER_BEATS[:beats]:
        if target[beat] not in out:
            out.append(target[beat])
    return out


def _stacks_presenters(shots: list[dict[str, Any]], i: int) -> bool:
    """Would making shot ``i`` a presenter put three presenter shots in a row?
    The schema rejects any source repeated more than twice consecutively, and
    a rejected list loses every shot the director chose."""
    def is_p(j: int) -> bool:
        return 0 <= j < len(shots) and shots[j].get("source") == _PRESENTER_SOURCE

    return (is_p(i - 1) and is_p(i - 2)) or (is_p(i - 1) and is_p(i + 1)) or (
        is_p(i + 1) and is_p(i + 2)
    )


def place_presenter_beats(shots: list[dict[str, Any]], fmt: PresenterFormat) -> list[str]:
    """Make a director shot list follow the presenter format, in place.

    The director and reviewer are TOLD the format (:func:`video_presenter_policy`);
    this makes it hold when they drift, before the list is stored. It has to be
    the stored list: the YouTube synthetic-media disclosure is read from it, so a
    face added only at render time would ship undisclosed. For each beat, in order:

    1. the target shot is already a presenter shot: keep it;
    2. the director put a presenter shot beside it (or, for the midpoint, anywhere
       in the middle third): adopt that one. It is the director's pick of the
       moment, and promoting the target as well could stack three presenter shots;
    3. otherwise promote the target, or the nearest shot a beat may take, to
       ``presenter``, dropping its visual fields (the face is the shot there).

    Uncapped, every other presenter shot the director chose stays. With a ceiling
    (``fmt.max_shots >= 0``), presenter shots past it that are not beats become
    Ken-Burns stills of their intent. Returns one note per change, for the log.
    """
    notes: list[str] = []
    n = len(shots)
    if n == 0 or fmt.beats <= 0:
        return notes
    spans = [_as_seconds(s.get("duration_s")) for s in shots]
    total = sum(spans)
    centers, start = [], 0.0
    for span in spans:
        centers.append(start + span / 2.0)
        start += span

    def is_p(i: int) -> bool:
        return shots[i].get("source") == _PRESENTER_SOURCE

    taken: set[int] = set()
    # A list too short to keep the beats apart yields fewer targets than beats;
    # the unmatched beats are simply not placed.
    targets = presenter_beat_indices(spans, fmt.beats)
    for beat, target in zip(PRESENTER_BEATS[: fmt.beats], targets, strict=False):
        if is_p(target) and target not in taken:
            taken.add(target)
            continue
        adopt = [target - 1, target + 1]
        if beat == "midpoint":
            third = [
                i for i in range(1, n - 1)
                if is_p(i) and total / 3.0 <= centers[i] <= 2.0 * total / 3.0
            ]
            adopt = sorted(third, key=lambda i: abs(centers[i] - total / 2.0)) + adopt
        chosen = next((i for i in adopt if 0 <= i < n and i not in taken and is_p(i)), None)
        if chosen is None:
            order = sorted(range(n), key=lambda i: (abs(i - target), i))
            for i in order:
                if (
                    i in taken
                    or shots[i].get("source") in _BEAT_KEEP_SOURCES
                    or _stacks_presenters(shots, i)
                ):
                    continue
                notes.append(
                    f"{beat} beat: shot {i} {shots[i].get('source')!r} -> presenter"
                )
                shots[i]["source"] = _PRESENTER_SOURCE
                for key in ("prompt", "query", "demo_id", "motion", "kenburns_zoom"):
                    shots[i].pop(key, None)
                chosen = i
                break
        if chosen is not None:
            taken.add(chosen)

    if fmt.max_shots >= 0:
        extras = [i for i in range(n) if is_p(i) and i not in taken]
        for i in extras[max(0, fmt.max_shots - len(taken)):]:
            subject = str(shots[i].get("intent") or "").strip() or "an abstract visual for this beat"
            shots[i]["source"] = "image_kenburns"
            shots[i]["prompt"] = f"{fmt.style_prefix}, {subject}" if fmt.style_prefix else subject
            shots[i].pop("query", None)
            shots[i].pop("demo_id", None)
            notes.append(f"shot {i} presenter -> image_kenburns (over the {fmt.max_shots}-shot budget)")
    return notes


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
        "style_prefix": video_style_prefix(policy),
        "image_subject_rule": writer_image_subject_rule(policy),
        "people_sentence": image_people_sentence(policy),
        "people_rule": image_decision_people_rule(policy),
        "presenter_policy": video_presenter_policy(policy),
    }
