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
    assert set(mp.prompt_variables(allow)) == {"human_subject_policy", "human_subject_rule", "style_policy", "image_subject_rule", "people_sentence", "people_rule"}


def test_none_site_config_is_the_default_policy():
    p = mp.resolve_media_policy(None, "any-niche")
    assert p.human_subjects == "allow" and p.style_policy == "stylized"
