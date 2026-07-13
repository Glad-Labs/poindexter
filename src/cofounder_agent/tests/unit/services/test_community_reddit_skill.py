"""Validate the reddit-value-post SKILL.md pack parses + renders.

The generator mocks the prompt lookup, so a malformed pack (unknown
``metadata.category`` → whole pack silently skipped, a missing
``## community.reddit_value_post`` section, or a stray single brace that breaks
``str.format``) would slip through to production. This parses the real file the
way ``UnifiedPromptManager._initialize_skills`` does.
"""
from __future__ import annotations

from pathlib import Path

import services
from services.prompt_manager import UnifiedPromptManager
from services.skill_frontmatter import extract_section, parse_frontmatter

_SKILL_PATH = (
    Path(services.__file__).resolve().parent.parent
    / "skills" / "community" / "reddit-value-post" / "SKILL.md"
)
_KEY = "community.reddit_value_post"


def _load():
    frontmatter, body = parse_frontmatter(_SKILL_PATH.read_text(encoding="utf-8"))
    return frontmatter, body


def test_pack_exists():
    assert _SKILL_PATH.is_file(), f"missing SKILL.md at {_SKILL_PATH}"


def test_category_is_registrable():
    frontmatter, _ = _load()
    category = (frontmatter.get("metadata") or {}).get("category")
    # An unknown category makes _initialize_skills skip the entire pack.
    assert category in UnifiedPromptManager._CATEGORY_MAP


def test_declares_the_key_as_text():
    frontmatter, _ = _load()
    prompts = (frontmatter.get("metadata") or {}).get("prompts") or []
    entry = next((p for p in prompts if p.get("key") == _KEY), None)
    assert entry is not None, f"pack does not declare {_KEY}"
    assert entry.get("output_format") == "text"


def test_section_extracts_and_renders():
    _, body = _load()
    template = extract_section(body, _KEY)
    assert template, f"no '## {_KEY}' fenced section"
    rendered = template.format(
        title="RTX 5090 for local inference", content="body text",
        content_types="ai-ml, pc-hardware", rules_summary="No memes.",
        tone_notes="Technical, humble.", post_type="text",
        self_promo="strict", flair="Discussion",
    )
    assert "RTX 5090 for local inference" in rendered
    assert "Discussion" in rendered
    assert rendered.strip()
