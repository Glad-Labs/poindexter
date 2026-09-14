"""The image-gen negative prompt follows the niche's media policy."""

from poindexter.modules.content.atoms._image_helpers import (
    IMAGE_GEN_NEGATIVE_PROMPT,
    _get_image_gen_negative_prompt,
)
from poindexter.services.site_config import SiteConfig


def test_default_negative_prompt_carries_no_human_terms():
    for term in ("face", "person", "human", "hands", "fingers"):
        assert term not in IMAGE_GEN_NEGATIVE_PROMPT.split(", ")


def test_negative_prompt_follows_the_niche_policy():
    sc = SiteConfig(initial_config={
        "image_negative_prompt": "text, words, face, person, blurry",
        "media_human_subjects": "allow",
        "niche.strict.media.human_subjects": "none",
    })
    assert _get_image_gen_negative_prompt(sc, "glad-labs") == "text, words, blurry"
    assert _get_image_gen_negative_prompt(sc, "strict") == "text, words, blurry, face, person, human, hands, fingers"
    assert _get_image_gen_negative_prompt(None) == IMAGE_GEN_NEGATIVE_PROMPT
