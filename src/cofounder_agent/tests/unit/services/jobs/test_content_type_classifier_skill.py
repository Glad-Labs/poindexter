"""Validate the content-type-classifier SKILL.md pack parses + renders.

The classifier job and its integration test both mock ``get_prompt``, so a
malformed pack (unknown ``metadata.category`` → whole pack silently skipped, a
missing ``## content.classify_content_type`` section, or a stray single brace
that breaks ``str.format``) would slip through to production. This test parses
the real file the way ``UnifiedPromptManager._initialize_skills`` does.
"""
from __future__ import annotations

from pathlib import Path

import services
from services.prompt_manager import UnifiedPromptManager
from services.skill_frontmatter import extract_section, parse_frontmatter

_SKILL_PATH = (
    Path(services.__file__).resolve().parent.parent
    / "skills" / "content" / "content-type-classifier" / "SKILL.md"
)
_KEY = "content.classify_content_type"


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


def test_declares_the_key_as_json():
    frontmatter, _ = _load()
    prompts = (frontmatter.get("metadata") or {}).get("prompts") or []
    entry = next((p for p in prompts if p.get("key") == _KEY), None)
    assert entry is not None, f"pack does not declare {_KEY}"
    assert entry.get("output_format") == "json"


def test_section_extracts_and_renders():
    _, body = _load()
    template = extract_section(body, _KEY)
    assert template, f"no '## {_KEY}' fenced section"
    rendered = template.format(
        labels="ai-ml, gaming",
        title="RTX 5090 for local LLMs",
        tags="gpu,llm",
        excerpt="body text",
    )
    # placeholders substituted...
    assert "ai-ml, gaming" in rendered
    assert "RTX 5090 for local LLMs" in rendered
    # ...and the literal JSON braces survived {{ }} escaping (not consumed by format)
    assert '{"labels"' in rendered
