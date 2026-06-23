"""The featured/hero image is scored by the qa.vision image-relevance gate.

Before this the vision gate only saw *inline* images (``extract_inline_image_urls``
over the markdown body). The featured/hero image — which lives in pipeline state
under ``featured_image_url``, never embedded in the body — was never scored, so a
mismatched or low-quality hero sailed through the gate. ``_images_to_score`` is the
pure seam that folds the featured image into the same scan set, leading the list so
it always survives the ``qa_vision_max_images`` cap.
"""

from __future__ import annotations

import pytest

from modules.content.multi_model_qa import _images_to_score


@pytest.mark.unit
class TestImagesToScore:
    def test_featured_image_leads_the_scan_list(self):
        content = "intro ![a](https://cdn.example/a.webp) more"
        out = _images_to_score(content, "https://cdn.example/hero.webp", max_images=3)
        assert out[0] == "https://cdn.example/hero.webp"
        assert "https://cdn.example/a.webp" in out

    def test_featured_survives_the_max_images_cap(self):
        """With the cap already full of inline images, the hero is NOT the one dropped."""
        content = (
            "![a](https://cdn.example/a.webp) "
            "![b](https://cdn.example/b.webp) "
            "![c](https://cdn.example/c.webp)"
        )
        out = _images_to_score(content, "https://cdn.example/hero.webp", max_images=3)
        assert len(out) == 3
        assert out[0] == "https://cdn.example/hero.webp"
        # The last inline image is the one truncated, not the hero.
        assert "https://cdn.example/c.webp" not in out

    def test_featured_deduped_when_also_inline(self):
        """A hero that also appears inline is scored once (in the lead slot)."""
        content = "![hero](https://cdn.example/hero.webp) ![a](https://cdn.example/a.webp)"
        out = _images_to_score(content, "https://cdn.example/hero.webp", max_images=5)
        assert out.count("https://cdn.example/hero.webp") == 1
        assert out == ["https://cdn.example/hero.webp", "https://cdn.example/a.webp"]

    def test_empty_featured_is_inline_only_backcompat(self):
        content = "![a](https://cdn.example/a.webp) ![b](https://cdn.example/b.webp)"
        assert _images_to_score(content, "", max_images=5) == [
            "https://cdn.example/a.webp",
            "https://cdn.example/b.webp",
        ]
        assert _images_to_score(content, None, max_images=5) == [
            "https://cdn.example/a.webp",
            "https://cdn.example/b.webp",
        ]

    def test_featured_only_no_inline_still_scored(self):
        """A post with a hero but no inline images now produces a scan set (was empty)."""
        out = _images_to_score("body with no images", "https://cdn.example/hero.webp", max_images=3)
        assert out == ["https://cdn.example/hero.webp"]

    def test_no_images_at_all_is_empty(self):
        assert _images_to_score("plain body, no media", "", max_images=3) == []

    def test_whitespace_featured_is_ignored(self):
        content = "![a](https://cdn.example/a.webp)"
        assert _images_to_score(content, "   ", max_images=3) == ["https://cdn.example/a.webp"]
