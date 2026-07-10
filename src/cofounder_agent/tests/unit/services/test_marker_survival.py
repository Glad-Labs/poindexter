"""Writer image markers must survive the early pipeline stages.

The writer places ``[IMAGE: subject]`` (inline) and ``[HERO-IMAGE: subject]``
(hero) markers in the draft (blog-generation SKILL.md). They aren't read until
``content.plan_image_markers`` (node 13), so they have to survive everything in
between. Two failure modes this guards:

- the anti-leak strippers at ``content.generate_draft`` (node 2) and
  ``content.normalize_draft`` (node 5) used to delete ``[IMAGE:]`` markers as if
  they were leaked image-prompt prose — now they're an intentional channel and
  only genuine leaks (``*A dramatic scene…*`` italic prose, ``[FIGURE:]``) are
  stripped;
- ``quality_evaluation`` (node 11) scored the raw body, so markers skewed the
  word-count / pattern scores — it now scores marker-stripped text.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_strip_image_markers_removes_all_forms():
    from modules.content.stages.quality_evaluation import _strip_image_markers

    text = "[HERO-IMAGE: x]\n# H\nBody [IMAGE: a] and [IMAGE-2: b] end."
    out = _strip_image_markers(text)
    assert "IMAGE" not in out
    assert "Body" in out and "end." in out


@pytest.mark.unit
def test_writer_markers_survive_generate_content_strip():
    from modules.content.stages.generate_content import _strip_leaked_image_prompts

    body = (
        "# Title\n\nIntro [IMAGE: a GPU die shot] body.\n\n"
        "[HERO-IMAGE: a motherboard] more.\n\n"
        "*A dramatic scene of rolling hills at dusk with cinematic lighting across the frame*\n\n"
        "Tail [FIGURE: a chart] end."
    )
    out = _strip_leaked_image_prompts(body)
    # intentional writer markers survive
    assert "[IMAGE: a GPU die shot]" in out
    assert "[HERO-IMAGE: a motherboard]" in out
    # genuine leaks are still stripped
    assert "dramatic scene" not in out
    assert "FIGURE" not in out


@pytest.mark.unit
def test_writer_markers_survive_normalize_draft_strip():
    from modules.content.atoms.content_normalize_draft import strip_leaked_image_prompts

    body = (
        "Intro [IMAGE: a GPU die shot] and [IMAGE-3: a fan] plus "
        "[HERO-IMAGE: a case] end. [FIGURE: x]"
    )
    out = strip_leaked_image_prompts(body)
    assert "[IMAGE: a GPU die shot]" in out
    assert "[IMAGE-3: a fan]" in out
    assert "[HERO-IMAGE: a case]" in out
    assert "FIGURE" not in out
