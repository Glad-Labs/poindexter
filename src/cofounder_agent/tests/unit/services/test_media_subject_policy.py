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
    assert set(mp.prompt_variables(allow)) == {"human_subject_policy", "human_subject_rule", "style_policy", "image_subject_rule", "people_sentence", "people_rule", "presenter_policy"}


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
