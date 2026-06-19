"""The video.review_v1 / _short_v1 director self-critique prompts resolve
from skills/content/video-director/SKILL.md (Piece 1, video-quality spec §3.1)."""

from __future__ import annotations

import pytest

from services.prompt_manager import get_prompt_manager


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "script_kwarg"),
    [("video.review_v1", "podcast_script"), ("video.review_short_v1", "short_script")],
)
def test_review_prompt_renders_and_substitutes(key: str, script_kwarg: str) -> None:
    pm = get_prompt_manager()
    text = pm.get_prompt(
        key,
        site_name="Glad Labs",
        title="My Title",
        content="Body content.",
        current_shot_list='{"shots": []}',
        model="ollama/gemma-4-31B-it-qat:latest",
        now_iso="2026-06-19T00:00:00Z",
        **{script_kwarg: "the narration script"},
    )
    # Placeholders were substituted (not echoed literally).
    assert "{current_shot_list}" not in text
    assert "{title}" not in text
    # The draft list value was injected, and the revise instruction is present.
    assert '{"shots": []}' in text
    assert "REVISE" in text.upper()
    # Field-rule contract (prompt hardening): the review prompt must spell out
    # that AI-render sources carry a non-empty "prompt", or gemma omits it and
    # the revised list fails VideoShotList validation (silently falling back to
    # the unreviewed draft). Locks in the per-source field guidance.
    assert "FIELD RULES" in text
    assert '"prompt"' in text
    assert "wan21" in text
