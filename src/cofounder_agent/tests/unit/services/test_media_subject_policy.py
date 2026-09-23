"""media_policy — niche-first resolution, negative-prompt derivation, prompt fragments."""

from __future__ import annotations

import logging

from poindexter.services import media_subject_policy as mp
from poindexter.services.site_config import SiteConfig


def _sc(**kv):
    return SiteConfig(initial_config={k: str(v) for k, v in kv.items()})


def test_defaults_allow_people_and_keep_the_house_style():
    p = mp.resolve_media_policy(_sc(), "glad-labs")
    assert p.human_subjects == "allow" and p.style_policy == "stylized"
    assert p.humans_allowed and not p.photoreal_allowed and not p.photoreal_humans_allowed
    assert p.sources == ("default", "default")


def test_global_setting_then_niche_override_win_in_order():
    sc = _sc(media_human_subjects="none", media_style_policy="any")
    assert mp.resolve_media_policy(sc, None).human_subjects == "none"
    sc = _sc(media_human_subjects="none", **{"niche.dev_diary.media.human_subjects": "allow"})
    p = mp.resolve_media_policy(sc, "dev_diary")
    assert p.human_subjects == "allow" and p.sources[0] == "niche"
    assert mp.resolve_media_policy(sc, "glad-labs").human_subjects == "none"


def test_unknown_value_is_loud_and_falls_through(caplog):
    sc = _sc(**{"niche.x.media.human_subjects": "maybe", "media_human_subjects": "stylized_only"})
    with caplog.at_level(logging.WARNING, logger="poindexter.services.media_subject_policy"):
        p = mp.resolve_media_policy(sc, "x")
    assert p.human_subjects == "stylized_only" and p.sources[0] == "global"
    assert "not one of" in caplog.text and "niche.x.media.human_subjects" in caplog.text


def test_negative_prompt_strips_human_terms_unless_humans_are_forbidden():
    base = "text, words, letters, numbers, watermark, signature, logo, face, person, human, hands, fingers, blurry, low quality"
    allow = mp.resolve_media_policy(_sc(), None)
    out = mp.negative_prompt(allow, base)
    assert out == "text, words, letters, numbers, watermark, signature, logo, blurry, low quality"
    none = mp.resolve_media_policy(_sc(media_human_subjects="none"), None)
    assert mp.negative_prompt(none, out) == out + ", face, person, human, hands, fingers"
    # idempotent on a row that already carries the terms, and case-insensitive
    assert mp.negative_prompt(none, base) == out + ", face, person, human, hands, fingers"
    assert mp.negative_prompt(allow, "Text, FACE, Person") == "Text"


def test_negative_prompt_honours_operator_configured_terms():
    sc = _sc(media_human_subjects="none", media_negative_prompt_human_terms="face, mannequin")
    p = mp.resolve_media_policy(sc, None)
    assert mp.negative_prompt(p, "text, face, hands") == "text, hands, face, mannequin"  # hands is not an operator term, so it stays; forbidden terms are appended


def test_prompt_fragments_follow_the_policy():
    allow = mp.resolve_media_policy(_sc(), None)
    photo = mp.resolve_media_policy(_sc(media_style_policy="any"), None)
    none = mp.resolve_media_policy(_sc(media_human_subjects="none"), None)
    sty = mp.resolve_media_policy(_sc(media_human_subjects="stylized_only", media_style_policy="any"), None)
    assert "NEVER photoreal for a human" in mp.video_human_subject_policy(allow)
    assert "stylized or photoreal" in mp.video_human_subject_policy(photo)
    assert "No people" in mp.video_human_subject_policy(none) and "pexels" in mp.video_human_subject_policy(none)
    assert "stylized styles only" in mp.video_human_subject_rule(sty)
    assert "Never name a human noun" in mp.video_human_subject_rule(none)
    assert "must be STYLIZED" in mp.video_style_policy(allow) and "may be stylized OR photoreal" in mp.video_style_policy(photo)
    assert mp.writer_image_subject_rule(none).startswith("Never put identifiable people")
    assert "may appear" in mp.writer_image_subject_rule(allow) and "never photoreal" in mp.writer_image_subject_rule(allow)
    assert "never photoreal" not in mp.writer_image_subject_rule(photo)
    assert set(mp.prompt_variables(allow)) == {"human_subject_policy", "human_subject_rule",
                                               "style_policy", "style_prefix",
                                               "image_subject_rule", "people_sentence",
                                               "people_rule", "presenter_policy"}


def test_none_site_config_is_the_default_policy():
    p = mp.resolve_media_policy(None, "any-niche")
    assert p.human_subjects == "allow" and p.style_policy == "stylized"


# ---------------------------------------------------------------------------
# presenter (persona) resolution rides the same policy object
# ---------------------------------------------------------------------------

def _presenter_sc(**extra):
    base = {
        "media_default_persona": "presenter",
        "persona.presenter.display_name": "Ada",
        "persona.presenter.portrait_url": "https://cdn/personas/presenter.png",
        "persona.presenter.style_policy": "photoreal",
        "persona.presenter.enabled": "true",
        "media_human_subjects": "allow",
        "media_style_policy": "any",
    }
    base.update(extra)
    return _sc(**base)


def test_presenter_available_when_persona_has_portrait_and_policy_allows():
    p = mp.resolve_media_policy(_presenter_sc(), "glad-labs")
    assert p.presenter_available is True
    assert (p.presenter_slug, p.presenter_display_name, p.presenter_style) == ("presenter", "Ada", "photoreal")
    assert p.presenter_max_shots == 2
    text = mp.prompt_variables(p)["presenter_policy"]
    assert "PRESENTER AVAILABLE" in text and '"Ada"' in text and "At most 2" in text


def test_presenter_cap_is_a_setting():
    p = mp.resolve_media_policy(_presenter_sc(video_presenter_shots_max="1"), None)
    assert p.presenter_max_shots == 1


def test_no_persona_means_never_emit_presenter():
    p = mp.resolve_media_policy(_sc(), None)
    assert p.presenter_available is False
    assert "NEVER emit" in mp.prompt_variables(p)["presenter_policy"]


def test_persona_without_portrait_is_unavailable_and_loud(caplog):
    # Loud for a niche-bound caller (director / renderer / media dispatch).
    with caplog.at_level("WARNING"):
        p = mp.resolve_media_policy(_presenter_sc(**{"persona.presenter.portrait_url": ""}), "glad-labs")
    assert p.presenter_available is False and p.presenter_slug == "presenter"
    assert "no portrait" in caplog.text


def test_niche_less_policy_read_is_quiet_about_the_presenter(caplog):
    """The writer's image-subject rule and the post-edit negative prompt read
    the policy with no niche; nothing there can put the presenter on camera,
    so an "unavailable" verdict is DEBUG, not a WARNING per draft (18 of them
    in six hours on 2026-09-15)."""
    import logging
    with caplog.at_level(logging.DEBUG, logger="poindexter.services.media_subject_policy"):
        p = mp.resolve_media_policy(_presenter_sc(media_style_policy="stylized"), None)
    assert p.presenter_available is False
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("photoreal" in r.getMessage() for r in caplog.records)


def test_photoreal_persona_needs_photoreal_people_policy(caplog):
    with caplog.at_level("WARNING"):
        p = mp.resolve_media_policy(_presenter_sc(media_style_policy="stylized"), "glad-labs")
    assert p.presenter_available is False
    assert "photoreal" in caplog.text
    stylized = mp.resolve_media_policy(
        _presenter_sc(media_style_policy="stylized", **{"persona.presenter.style_policy": "stylized"}), None,
    )
    assert stylized.presenter_available is True


def test_people_forbidden_disables_the_presenter():
    p = mp.resolve_media_policy(_presenter_sc(media_human_subjects="none", **{"persona.presenter.style_policy": "stylized"}), None)
    assert p.presenter_available is False


# ---------------------------------------------------------------------------
# synthetic-media disclosure
# ---------------------------------------------------------------------------

def test_synthetic_media_needs_a_presenter_shot_and_a_photoreal_persona():
    photo = mp.resolve_media_policy(_presenter_sc(), None)
    shots = {"shots": [{"source": "pexels"}, {"source": "presenter"}]}
    assert mp.video_contains_synthetic_media(photo, shots) is True
    assert mp.video_contains_synthetic_media(photo, {"shots": [{"source": "pexels"}]}) is False
    assert mp.video_contains_synthetic_media(photo, None) is False
    stylized = mp.resolve_media_policy(
        _presenter_sc(media_style_policy="stylized", **{"persona.presenter.style_policy": "stylized"}), None,
    )
    assert mp.video_contains_synthetic_media(stylized, shots) is False


def test_synthetic_media_accepts_model_objects_too():
    from poindexter.schemas.video_shot_list import Shot

    class _List:
        shots = [Shot(idx=0, duration_s=5.0, intent="open", source="presenter", narration_offset_s=0.0)]

    photo = mp.resolve_media_policy(_presenter_sc(), None)
    assert mp.video_contains_synthetic_media(photo, _List()) is True


# ---------------------------------------------------------------------------
# House style — one look per video (2026-09-22)
# ---------------------------------------------------------------------------

def test_house_style_resolves_niche_then_global_then_empty():
    assert mp.resolve_media_policy(_sc(), "glad-labs").house_style == ""
    assert mp.resolve_media_policy(_sc(media_house_style="line art"), "glad-labs").house_style == "line art"
    sc = _sc(**{"media_house_style": "line art", "niche.glad-labs.media.house_style": "retro-tech cyberpunk"})
    assert mp.resolve_media_policy(sc, "glad-labs").house_style == "retro-tech cyberpunk"
    assert mp.resolve_media_policy(sc, "other").house_style == "line art"
    assert mp.resolve_media_policy(sc, None).house_style == "line art"


# Every modifier the menu used to offer. A house style must leave NONE of
# them in the prompt: the director read the list as an invitation and picked
# from it on every shot.
_MENU_MODIFIERS = (
    "flat vector illustration", "cinematic illustration", "isometric 3D",
    "line art", "cyberpunk neon", "glassmorphism", "low poly", "watercolor",
    "pixel art", "paper cutout",
)


def test_house_style_is_folded_into_the_style_policy_text():
    """No new template key — the director prompts already render {style_policy}."""
    off = mp.resolve_media_policy(_sc(), None)
    on = mp.resolve_media_policy(_sc(media_house_style="retro-tech cyberpunk illustration"), None)
    assert "HOUSE STYLE" not in mp.video_style_policy(off)
    text = mp.video_style_policy(on)
    assert "HOUSE STYLE" in text and "retro-tech cyberpunk illustration" in text
    # the variable set the packs are rendered from is unchanged
    assert set(mp.prompt_variables(on)) == set(mp.prompt_variables(off))


def test_a_house_style_replaces_the_menu_rather_than_prepending_to_it():
    """Prepending was tried first and did nothing: the block said "do not vary
    the modifier" and the next paragraph offered seven to pick from. On the
    2026-09-22 NCCL pair, 0 of 12 AI shots began with the house style and five
    menu modifiers did."""
    on = mp.resolve_media_policy(_sc(media_house_style="retro-tech cyberpunk illustration"), None)
    text = mp.video_style_policy(on).lower()
    for modifier in _MENU_MODIFIERS:
        assert modifier.lower() not in text, f"{modifier!r} is still on offer"
    assert "pick a modifier" not in text and "pick a stylized modifier" not in text
    assert "only modifier" in text


def test_the_photoreal_branch_offers_no_menu_either_when_a_house_style_is_set():
    """The glad-labs config exactly: style_policy 'any' (photoreal allowed) AND
    a house style. That combination licensed "abstract photorealism" on the
    NCCL long form — the rainbow-marbled shot."""
    on = mp.resolve_media_policy(
        _sc(media_house_style="retro-tech cyberpunk illustration", media_style_policy="any"), None)
    assert on.photoreal_allowed is True
    text = mp.video_style_policy(on).lower()
    assert "may be stylized or photoreal" not in text
    for modifier in _MENU_MODIFIERS:
        assert modifier.lower() not in text


def test_without_a_house_style_the_menu_is_untouched():
    """A install that has not chosen a look still needs the list."""
    stylized = mp.video_style_policy(mp.resolve_media_policy(_sc(), None))
    photoreal = mp.video_style_policy(mp.resolve_media_policy(_sc(media_style_policy="any"), None))
    assert "must be STYLIZED" in stylized and "flat vector illustration" in stylized
    assert "may be stylized OR photoreal" in photoreal

def test_pexels_stays_exempt_on_every_branch():
    """Stock is real footage; a house style cannot apply to it. Named here so
    the exemption is a decision on the record, not an omission."""
    for policy in (
        mp.resolve_media_policy(_sc(), None),
        mp.resolve_media_policy(_sc(media_style_policy="any"), None),
        mp.resolve_media_policy(_sc(media_house_style="retro-tech cyberpunk illustration"), None),
    ):
        assert "Pexels is exempt" in mp.video_style_policy(policy)


def test_the_buzzword_ban_survives_a_house_style():
    """They are an AI tell in any style, so they stay banned once the menu goes."""
    on = mp.resolve_media_policy(_sc(media_house_style="documentary photography"), None)
    text = mp.video_style_policy(on)
    for tell in ("8K", "DSLR", "hyper-realistic", "ultra-detailed"):
        assert tell in text


def test_style_prefix_is_the_house_style_when_one_is_set():
    """The director's worked examples begin with this. They used to carry
    three DIFFERENT literal modifiers, two of them in the SAME example shot
    list — so the examples demonstrated a look per shot, which is what the
    house style forbids. On the 2026-09-22 NCCL pair those three accounted
    for 6 of the 8 AI shots."""
    on = mp.resolve_media_policy(_sc(media_house_style="retro-tech cyberpunk illustration"), None)
    assert mp.video_style_prefix(on) == "retro-tech cyberpunk illustration"
    assert mp.prompt_variables(on)["style_prefix"] == "retro-tech cyberpunk illustration"


def test_style_prefix_falls_back_to_one_modifier_not_a_rotation():
    """With no house style the examples still have to agree with each other."""
    off = mp.resolve_media_policy(_sc(), None)
    prefix = mp.video_style_prefix(off)
    assert prefix and prefix in mp.video_style_policy(off)
