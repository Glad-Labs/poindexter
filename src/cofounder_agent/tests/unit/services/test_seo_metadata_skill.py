"""Tests for the seo_metadata SKILL.md migration.

The ``seo_metadata`` prompts were migrated from ``prompts/seo_metadata.yaml``
to ``skills/content/seo-metadata/SKILL.md`` (agentskills.io format), following
the ``research`` pack precedent. These tests pin:

1. that all six seo keys still resolve (the migration didn't drop them),
2. that each template carries its expected ``{placeholder}`` tokens (no drift),
3. that templates retain the YAML ``|`` clip trailing newline (snapshot tests
   in test_prompt_manager.py / test_prompt_resolution.py depend on it).

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

from services.prompt_manager import UnifiedPromptManager


# (key, required placeholders) — byte-fidelity guard against silent drift.
_SEO_KEYS = (
    ("seo.generate_title", ("{topic}",)),
    ("seo.generate_meta_description", ("{topic}",)),
    ("seo.extract_keywords", ("{content}",)),
    ("seo.generate_excerpt", ("{content}",)),
    ("seo.match_category", ("{categories}", "{topic}")),
    ("seo.extract_tags", ("{content}",)),
)


def test_seo_keys_resolve_from_skill() -> None:
    """All six seo keys must load from skills/content/seo-metadata/SKILL.md."""
    pm = UnifiedPromptManager()
    for key, _ in _SEO_KEYS:
        assert key in pm.prompts, f"{key} did not load from the seo-metadata skill"
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


def test_seo_templates_have_placeholders() -> None:
    """Each template must keep its required {placeholder} tokens verbatim."""
    pm = UnifiedPromptManager()
    for key, placeholders in _SEO_KEYS:
        template = pm.prompts[key]["template"]
        for placeholder in placeholders:
            assert placeholder in template, f"{key} missing {placeholder}"


def test_seo_templates_end_with_newline() -> None:
    """Templates keep the YAML ``|`` clip trailing newline (byte fidelity)."""
    pm = UnifiedPromptManager()
    for key, _ in _SEO_KEYS:
        assert pm.prompts[key]["template"].endswith("\n"), (
            f"{key} lost its trailing newline"
        )
