"""Tests for the migrated video-director skill.

`prompts/video_director.yaml` → `skills/content/video-director/SKILL.md`
(the final pack of the #528 prompt-catalog migration). The "Glad Labs"
persona token became a `{site_name}` placeholder rendered from site_config
by the generate_video_shot_list stage.
"""

from __future__ import annotations

import json

from poindexter.modules.content.stages.generate_video_shot_list import _extract_json_object
from poindexter.schemas.video_shot_list import scan_for_human_tokens
from poindexter.services.media_subject_policy import prompt_variables, resolve_media_policy

_POLICY_VARS = prompt_variables(resolve_media_policy(None, None))
import re
from pathlib import Path

import pytest

from poindexter.services.prompt_manager import UnifiedPromptManager

_KEY = "video.director_v1"
_SHORT_KEY = "video.director_short_v1"


def test_director_key_resolves_from_skill() -> None:
    pm = UnifiedPromptManager()
    assert _KEY in pm.prompts, f"{_KEY} did not load from the video-director skill"
    template = pm.prompts[_KEY]["template"]
    assert template.strip(), "director template is empty"
    # Placeholders the stage fills must survive.
    for placeholder in ("{site_name}", "{title}", "{content}",
                        "{podcast_script}", "{target_duration_s}",
                        "{model}", "{now_iso}"):
        assert placeholder in template, f"missing placeholder {placeholder}"
    # JSON schema braces must be escaped so .format() leaves literal braces.
    assert '{{' in template and '}}' in template
    # Clip semantics: single trailing newline (YAML | parity).
    assert template.endswith("\n")
    assert not template.endswith("\n\n")


def test_director_renders_operator_brand_no_literal_placeholder() -> None:
    """get_prompt must substitute {site_name} (and unescape JSON braces)."""
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _KEY,
        title="T",
        content="C",
        podcast_script="P",
        target_duration_s="60.0",
        model="m",
        now_iso="2026-05-30T00:00:00Z",
        site_name="Glad Labs",
        demo_catalog="- demo_id=\"posts-list\" (6.5s, content): Recent posts.",
        **_POLICY_VARS,
    )
    assert "video director for a Glad Labs blog post" in rendered
    assert "{site_name}" not in rendered
    # Escaped JSON braces unescaped to single braces.
    assert '"version": 1' in rendered
    assert "{{" not in rendered


def test_director_renders_clean_with_empty_site_name() -> None:
    """Unset operator brand (empty string) still renders without errors."""
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _KEY,
        title="T",
        content="C",
        podcast_script="P",
        target_duration_s="60.0",
        model="m",
        now_iso="2026-05-30T00:00:00Z",
        site_name="",
        demo_catalog="NONE AVAILABLE",
        **_POLICY_VARS,
    )
    assert "{site_name}" not in rendered
    assert "blog post" in rendered


# ---------------------------------------------------------------------------
# Short-form director (Plan 3, #517): video.director_short_v1 — purpose-built
# 9:16 vertical retention clip from the short summary script. It uses
# {short_script} where the long prompt uses {podcast_script}.
# ---------------------------------------------------------------------------


def test_short_director_key_resolves_from_skill() -> None:
    pm = UnifiedPromptManager()
    assert _SHORT_KEY in pm.prompts, (
        f"{_SHORT_KEY} did not load from the video-director skill"
    )
    template = pm.prompts[_SHORT_KEY]["template"]
    assert template.strip(), "short director template is empty"
    # Placeholders the stage fills must survive — note {short_script}, not
    # {podcast_script}, for the short-form narration.
    for placeholder in ("{site_name}", "{title}", "{content}",
                        "{short_script}", "{target_duration_s}",
                        "{model}", "{now_iso}"):
        assert placeholder in template, f"missing placeholder {placeholder}"
    # JSON schema braces must be escaped so .format() leaves literal braces.
    assert '{{' in template and '}}' in template


def test_short_director_renders_9x16_and_brand() -> None:
    """get_prompt must substitute params (incl. {short_script}) + unescape braces."""
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _SHORT_KEY,
        title="T",
        content="C",
        short_script="S",
        target_duration_s="20.0",
        model="m",
        now_iso="2026-06-08T00:00:00Z",
        site_name="Glad Labs",
        demo_catalog="- demo_id=\"posts-list\" (6.5s, content): Recent posts.",
        **_POLICY_VARS,
    )
    assert "short-form video director for a Glad Labs post" in rendered
    assert "{site_name}" not in rendered
    assert "{short_script}" not in rendered
    # The short director targets a vertical 9:16 clip.
    assert '"aspect": "9:16"' in rendered
    assert "short_v1" in rendered
    # Escaped JSON braces unescaped to single braces.
    assert "{{" not in rendered


# ---------------------------------------------------------------------------
# HUMAN-SUBJECT POLICY regression — the SCHEMA example shots are what the
# director LLM imitates. A human subject in an example must route to
# source="pexels" (Pexels queries are never scanned); an image_gen / image_kenburns
# / wan21 example prompt must NEVER carry a _HUMAN_TOKENS noun — not even
# inside a "no people" negation, which scan_for_human_tokens flags regardless
# (only the literal "silhouette"/"faceless" escape hatch clears it). Clean
# examples are what drive the server-side advisory-warning count toward zero
# (the ~15-warning/30-shot render on task 5466fd20). Catches a re-introduced
# "no people"/"developer"/… in either director's example block.
# ---------------------------------------------------------------------------

_AI_SOURCES = ("image_gen", "image_kenburns", "wan21", "generative")


def _example_ai_prompts(rendered: str) -> list[str]:
    """Return the prompts of the AI-source example shots in a rendered director
    prompt's SCHEMA block.

    Parses the example with the SAME ``_extract_json_object`` the stage runs on
    real director output, so the examples are read exactly as the renderer
    reads the live LLM result.
    """
    body = _extract_json_object(rendered)
    assert body, "no JSON schema example found in rendered director prompt"
    shots = json.loads(body)["shots"]
    return [
        s["prompt"] for s in shots
        if s.get("source") in _AI_SOURCES and s.get("prompt")
    ]


def test_long_director_example_ai_prompts_have_no_human_tokens() -> None:
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _KEY,
        title="T", content="C", podcast_script="P",
        target_duration_s="60.0", model="m",
        now_iso="2026-05-30T00:00:00Z", site_name="Glad Labs",
        demo_catalog="NONE AVAILABLE",
        **_POLICY_VARS,
    )
    ai_prompts = _example_ai_prompts(rendered)
    assert ai_prompts, "expected at least one AI-source example shot to scan"
    for prompt in ai_prompts:
        assert scan_for_human_tokens(prompt) == [], (
            "long director example AI-source prompt carries human tokens "
            "(would render as stylized line-art instead of routing the human "
            f"subject to pexels real footage): {prompt!r}"
        )


def test_short_director_example_ai_prompts_have_no_human_tokens() -> None:
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _SHORT_KEY,
        title="T", content="C", short_script="S",
        target_duration_s="20.0", model="m",
        now_iso="2026-06-08T00:00:00Z", site_name="Glad Labs",
        demo_catalog="NONE AVAILABLE",
        **_POLICY_VARS,
    )
    ai_prompts = _example_ai_prompts(rendered)
    assert ai_prompts, "expected at least one AI-source example shot to scan"
    for prompt in ai_prompts:
        assert scan_for_human_tokens(prompt) == [], (
            "short director example AI-source prompt carries human tokens: "
            f"{prompt!r}"
        )


# ---------------------------------------------------------------------------
# cli_demo catalogue contract (poindexter#937 PR2)
# ---------------------------------------------------------------------------


def test_long_director_offers_cli_demo_with_the_catalogue() -> None:
    """The long prompt must document cli_demo AND interpolate the catalogue.

    A literal ``{demo_catalog}`` reaching the model would read as an
    instruction to invent one, so the substitution is the contract.
    """
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        "video.director_v1",
        title="T", content="C", podcast_script="S",
        target_duration_s="60.0", model="m",
        now_iso="2026-07-29T00:00:00Z", site_name="Glad Labs",
        demo_catalog='- demo_id="ops-sweep" (20.0s, process): An operator sweep.',
        **_POLICY_VARS,
    )
    assert '"cli_demo"' in rendered
    assert "ops-sweep" in rendered
    assert "{demo_catalog}" not in rendered
    assert "demo_id" in rendered


def test_long_director_states_when_no_demos_are_baked() -> None:
    """An explicit 'none' beats an omitted section — silence invites invention."""
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        "video.director_v1",
        title="T", content="C", podcast_script="S",
        target_duration_s="60.0", model="m",
        now_iso="2026-07-29T00:00:00Z", site_name="Glad Labs",
        demo_catalog="NONE AVAILABLE — no demo clips are baked on this install.",
        **_POLICY_VARS,
    )
    assert "NONE AVAILABLE" in rendered


def test_short_director_forbids_cli_demo() -> None:
    """Clips bake 16:9; letterboxed terminal text is unreadable on a phone.

    The exclusion must be STATED — an unexplained absence invites the model to
    try the source anyway (same failure shape as the omitted catalogue).
    """
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        "video.director_short_v1",
        **_POLICY_VARS,
        title="T", content="C", short_script="S",
        target_duration_s="45.0", model="m",
        now_iso="2026-07-29T00:00:00Z", site_name="Glad Labs",
    )
    assert "cli_demo" in rendered
    assert "NOT available" in rendered


def test_review_prompt_restates_the_cli_demo_field_contract() -> None:
    """Critique prompts must RESTATE field rules, not summarise them.

    A summarised contract makes the reviewer emit schema-invalid output, and
    the review stage degrades silently when that happens.
    """
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        "video.review_v1",
        human_subject_rule=_POLICY_VARS["human_subject_rule"],
        style_policy=_POLICY_VARS["style_policy"],
        presenter_policy=_POLICY_VARS["presenter_policy"],
        current_shot_list="{}", podcast_script="S",
        title="T", content="C",
        target_duration_s="60.0", model="m",
        now_iso="2026-07-29T00:00:00Z", site_name="Glad Labs",
    )
    assert "cli_demo" in rendered
    assert "demo_id" in rendered
    assert "Never invent a demo_id" in rendered


@pytest.mark.parametrize("key", ["video.review_v1", "video.review_short_v1"])
def test_review_prompts_carry_the_style_policy_not_a_hardcoded_rotation(key: str) -> None:
    """The reviewer receives the SAME resolved style policy as the director.

    Before 2026-09-22 the long review template hardcoded "a stylized modifier
    (flat vector / cinematic illustration / isometric 3D / cyberpunk neon /
    glassmorphism)" and never received {style_policy}, so it rewrote a
    one-look draft (media_house_style, #3930) into three looks. The template
    must name the placeholder and must not carry its own modifier list.
    """
    pm = UnifiedPromptManager()
    text = Path(__file__).resolve().parents[3].joinpath(
        "skills", "content", "video-director", "SKILL.md"
    ).read_text(encoding="utf-8")
    start = text.index(f"## {key}")
    nxt = re.search(r"\n## ", text[start + 3:])
    template = text[start: start + 3 + nxt.start()] if nxt else text[start:]
    assert "{style_policy}" in template
    assert "flat vector / cinematic illustration / isometric 3D" not in template
    rendered = pm.get_prompt(
        key,
        human_subject_rule=_POLICY_VARS["human_subject_rule"],
        style_policy="HOUSE STYLE — begin every AI prompt with: probe-style illustration.",
        presenter_policy=_POLICY_VARS["presenter_policy"],
        current_shot_list="{}", podcast_script="S", short_script="S",
        title="T", content="C", model="m",
        now_iso="2026-09-22T00:00:00Z", site_name="Glad Labs",
    )
    assert "probe-style illustration" in rendered


@pytest.mark.parametrize(
    "key", [
        "video.director_v1", "video.director_short_v1", "video.review_v1",
        "video.review_short_v1", "video.escalation_image_subject",
    ],
)
def test_every_director_template_forbids_words_inside_ai_images(key: str) -> None:
    """The short of 2026-09-22 closed on image_gen "a terminal screen displaying
    the 'Glad Labs' logo": image-gen's OCR gate rejected all three attempts
    (8 chars of text, limit 6) and the shot shipped as a plain brand card. The
    director illustrated the CTA literally; no template had told it not to.
    Every template that writes or revises AI prompts now says so."""
    text = Path(__file__).resolve().parents[3].joinpath(
        "skills", "content", "video-director", "SKILL.md"
    ).read_text(encoding="utf-8")
    start = text.index(f"## {key}")
    nxt = re.search(r"\n## ", text[start + 3:])
    template = text[start: start + 3 + nxt.start()] if nxt else text[start:]
    lowered = template.lower()
    assert "logos" in lowered and "brand names" in lowered
    assert "ocr gate" in lowered


# What each prompt key's REAL call site supplies, transcribed from the code:
#   video.director_*  -> generate_video_shot_list._render (**prompt_variables)
#   video.review_*    -> review_video_shot_list._render (named policy vars only)
#   video.restock_query -> video_renderers/shot_list_renderer._llm_restock_query
#   video.thumbnail_hook -> services/video_thumbnail.generate_thumbnail_hook
#   video.escalation_image_subject -> shot_list_renderer._llm_image_subject
_CALL_SITE_KWARGS: dict[str, set[str]] = {
    "video.director_v1": {
        "title", "content", "target_duration_s", "model", "now_iso",
        "site_name", "demo_catalog", "podcast_script", *_POLICY_VARS,
    },
    "video.director_short_v1": {
        "title", "content", "target_duration_s", "model", "now_iso",
        "site_name", "demo_catalog", "short_script", *_POLICY_VARS,
    },
    "video.review_v1": {
        "title", "content", "current_shot_list", "model", "now_iso",
        "site_name", "human_subject_rule", "style_policy", "presenter_policy",
        "podcast_script",
    },
    "video.review_short_v1": {
        "title", "content", "current_shot_list", "model", "now_iso",
        "site_name", "human_subject_rule", "style_policy", "presenter_policy",
        "short_script",
    },
    "video.restock_query": {"video_context", "intent", "failed_query"},
    "video.thumbnail_hook": {"title", "summary"},
    "video.thumbnail_hook_fix": {"reason", "max_chars"},
    "video.escalation_image_subject": {"video_context", "intent"},
}


def _section(key: str) -> str:
    text = Path(__file__).resolve().parents[3].joinpath(
        "skills", "content", "video-director", "SKILL.md"
    ).read_text(encoding="utf-8")
    start = text.index(f"## {key}")
    nxt = re.search(r"\n## ", text[start + 3:])
    return text[start: start + 3 + nxt.start()] if nxt else text[start:]


@pytest.mark.parametrize("key", sorted(_CALL_SITE_KWARGS))
def test_every_placeholder_is_supplied_by_its_call_site(key: str) -> None:
    """A placeholder nobody passes raises KeyError inside `.format()`, and both
    render call sites catch Exception and log "prompt render failed — skipping".
    So the failure mode is not a crash, it is the director quietly not running.

    The five keys share ONE SKILL.md but have different callers, so a variable
    added for the director can silently break the reviewer. `{style_prefix}`
    (2026-09-22) is exactly that shape: it belongs to the director sections and
    the reviewer's call site does not pass it.
    """
    placeholders = set(re.findall(r"(?<!\{)\{([a-z_][a-z0-9_]*)\}(?!\})", _section(key)))
    missing = placeholders - _CALL_SITE_KWARGS[key]
    assert not missing, f"{key} uses {sorted(missing)}, which its call site never passes"


def test_style_prefix_reached_the_director_sections_only() -> None:
    """It replaced three different literal modifiers in the worked examples.
    The reviewer revises a draft rather than writing prompts from an example,
    so it neither needs the variable nor is passed it."""
    for key in ("video.director_v1", "video.director_short_v1"):
        assert "{style_prefix}" in _section(key)
    for key in ("video.review_v1", "video.review_short_v1", "video.restock_query"):
        assert "{style_prefix}" not in _section(key)


@pytest.mark.parametrize("key", ["video.director_v1", "video.director_short_v1"])
def test_worked_examples_never_show_two_different_looks(key: str) -> None:
    """The examples are copied more readily than the rules are followed: on the
    2026-09-22 NCCL pair the director reproduced all three example modifiers
    (cinematic illustration x3, flat vector illustration x2, cyberpunk neon),
    6 of its 8 AI shots. Two of those literals sat in the SAME example list, so
    the examples were teaching a look per shot."""
    section = _section(key)
    example_prompts = re.findall(r'"prompt": "([^"]+)"', section)
    assert example_prompts, f"{key} has no worked example prompts to check"
    prefixes = {p.split(",")[0].strip() for p in example_prompts}
    assert prefixes == {"{style_prefix}"}, f"{key} examples show {sorted(prefixes)}"


# ---------------------------------------------------------------------------
# A hero's motion ends on its subject (2026-09-25). The compositor plays a
# hero clip once and holds its LAST frame for the rest of the scene
# (shot_list_renderer._scenes_for_plan): a ~5 s clip in a scene the narration
# fit often stretches to ~11 s. Nothing in these templates said so, and they
# said the opposite ("loops when its shot is longer"), so the director wrote
# pull-back "reveals": f555bedc long shot 15 asked for "slow zoom out ... to
# reveal" and pulled back on 8 of 8 renders. Four collapsed under the 0.30
# detail rule; the rest shrank the scene to an island mid-frame that passed
# both the rule and the judge at 92. #4045 fixes only re-rolls; the first
# render follows the director, so the director has to write the ending.
# ---------------------------------------------------------------------------

from tests.unit._nonempty import nonempty

# Every template that WRITES or REVISES a generative "motion".
_MOTION_KEYS = (
    "video.director_v1", "video.director_short_v1",
    "video.review_v1", "video.review_short_v1",
)

_HERO_PARAGRAPH = re.compile(r"HOW A HERO PLAYS: the clip runs.*?when the move ends\.")

# Camera moves that leave the subject — the failure, named ONLY here in the
# test. The templates state the target shape and never demonstrate this one.
_PULL_BACK = re.compile(
    r"zoom(?:s|ing)?[\s-]*out|pull(?:s|ing)?[\s-]*(?:back|away)|dolly[\s-]*out|reveal",
    re.I,
)


def _flat(key: str) -> str:
    """The section with its line wrapping collapsed, so a phrase that the
    template wraps across lines still matches."""
    return " ".join(_section(key).split())


@pytest.mark.parametrize("key", _MOTION_KEYS)
def test_every_motion_template_says_the_last_frame_is_held(key: str) -> None:
    """The director cannot aim for a frame it does not know is held. Each
    template that writes or revises a motion states the mechanism (the clip
    plays once, its last frame stays on screen) and the target (end on the
    subject, at least as large as it starts, and name that ending)."""
    flat = _flat(key)
    for phrase in (
        "plays once",
        "its LAST frame stays on screen",
        "END ON ITS SUBJECT",
        "at least as large as in the first",
        "Name the ending in the sentence",
    ):
        assert phrase in flat, f"{key} is missing {phrase!r}"


def test_the_four_motion_templates_share_one_hero_paragraph() -> None:
    """One rule, one wording. The keys share a SKILL.md but not a caller, and a
    template that drifts is the one whose output nobody re-measures. The
    paragraph is static text on purpose: a placeholder here would have to be
    passed by both the director and the reviewer call sites, and a missing one
    turns the stage into a silent "prompt render failed — skipping"."""
    paragraphs = {key: _HERO_PARAGRAPH.findall(_flat(key)) for key in _MOTION_KEYS}
    for key, found in paragraphs.items():
        assert len(found) == 1, f"{key} carries {len(found)} hero paragraphs"
    assert len({found[0] for found in paragraphs.values()}) == 1, (
        "the hero paragraph drifted between templates"
    )
    assert "{" not in paragraphs["video.review_v1"][0]


@pytest.mark.parametrize("key", _MOTION_KEYS)
def test_no_motion_template_says_a_generative_clip_loops(key: str) -> None:
    """Since #3990 no clip loops: a short clip plays once and its final frame
    carries the rest of the scene. A template that still says it loops tells
    the director the ending is a moment on screen, when it is the longest."""
    assert not re.search(r"\bloops?\b", _flat(key)), f"{key} still says a clip loops"


@pytest.mark.parametrize("key", _MOTION_KEYS)
def test_motion_guidance_is_worded_positively(key: str) -> None:
    """House rule: a prompt names the shape it wants, never the one it fears —
    what a prompt mentions, the model is more likely to write. So no template
    names a pull-back, and none frames a hero as a "reveal" (the worked
    example's hero was "the key reveal", and the reviewer upgraded "a key
    reveal" beat to generative, while the collapses asked to "reveal")."""
    flat = _flat(key)
    assert not _PULL_BACK.search(flat), f"{key} names a pull-back: {_PULL_BACK.search(flat)!r}"


@pytest.mark.parametrize("key", _MOTION_KEYS)
def test_motion_guidance_quotes_no_example_motion(key: str) -> None:
    """A quotable example is copied more readily than a rule is followed
    (4 of 10 unrelated Shorts opened with the hook prompt's one example
    sentence). The motion rules describe the shape — the camera move, what
    moves, where it ends — and quote no motion for the model to reuse."""
    quoted = re.findall(r'e\.g\.[^"]{0,20}"([^"]+)"', _flat(key))
    camera_words = re.compile(r"push|pan\b|zoom|drift|dolly|orbit|tilt|crane|track", re.I)
    assert not [q for q in quoted if camera_words.search(q)], f"{key} quotes {quoted!r}"


def test_worked_example_hero_ends_on_its_own_subject() -> None:
    """The worked example is what the director imitates, so its hero has to
    show the rule: a move toward the subject that names the ending, on a noun
    from the example's own still, so it ends on ITS subject."""
    pm = UnifiedPromptManager()
    rendered = pm.get_prompt(
        _KEY,
        title="T", content="C", podcast_script="P",
        target_duration_s="60.0", model="m",
        now_iso="2026-09-25T00:00:00Z", site_name="Glad Labs",
        demo_catalog="NONE AVAILABLE",
        **_POLICY_VARS,
    )
    shots = json.loads(_extract_json_object(rendered))["shots"]
    heroes = [s for s in shots if s.get("source") == "generative"]
    for hero in nonempty(heroes, "generative example shots"):
        motion = hero["motion"]
        assert not _PULL_BACK.search(motion), motion
        assert re.search(r"push-in|pan|drift|orbit", motion), motion
        assert "ending with" in motion, f"the example names no ending: {motion!r}"
        # The still's nouns, past the style prefix (everything before the
        # first comma, per test_worked_examples_never_show_two_different_looks).
        still = hero["prompt"].split(",", 1)[1].lower()
        subject_words = set(re.findall(r"[a-z]{5,}", still))
        subject_words -= {"glowing", "empty", "unpopulated", "scene"}
        assert subject_words & set(re.findall(r"[a-z]{5,}", motion.lower())), (
            f"the example motion ends on nothing its still shows: {motion!r}"
        )
