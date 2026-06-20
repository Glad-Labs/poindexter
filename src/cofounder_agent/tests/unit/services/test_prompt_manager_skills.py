"""Tests for the SKILL.md skill loader in ``services.prompt_manager``.

The ``research`` prompts were migrated from ``prompts/research.yaml`` to
``skills/research/SKILL.md`` (agentskills.io format) as the first brick of the
skill-catalog adoption. These tests pin:

1. that the three research keys still resolve (the migration didn't drop them),
2. that the templates match what the old YAML shipped (no silent drift),
3. that the section-extraction helper is robust to missing/odd sections.

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

from services.prompt_manager import UnifiedPromptManager

_RESEARCH_KEYS = (
    "research.analyze_search_results",
    "topic.ranking",
    "research.distill_topic_angle",
)


def test_research_keys_resolve_from_skill() -> None:
    """All three research keys must load from skills/research/SKILL.md."""
    pm = UnifiedPromptManager()
    for key in _RESEARCH_KEYS:
        assert key in pm.prompts, f"{key} did not load from the research skill"
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


def test_research_templates_match_migrated_yaml() -> None:
    """Templates must match the text the retired research.yaml shipped.

    Guards against silent drift during the YAML->SKILL.md migration. These
    snapshots are the exact bodies from the old prompts/research.yaml.
    """
    pm = UnifiedPromptManager()

    analyze = pm.prompts["research.analyze_search_results"]["template"]
    assert "Analyze these search results and return JSON" in analyze
    assert "{search_results}" in analyze

    ranking = pm.prompts["topic.ranking"]["template"]
    assert "scoring topic candidates" in ranking
    assert "{weights_descr}" in ranking
    assert "{cand_block}" in ranking
    # Literal JSON braces in the template must survive (escaped as {{ }}).
    assert '{{"<id>"' in ranking

    distill = pm.prompts["research.distill_topic_angle"]["template"]
    assert "AI-operated content business's internal records" in distill
    assert "{joined}" in distill
    assert '{{"topic"' in distill


def test_skill_section_extraction_handles_missing_section() -> None:
    """A declared key with no matching '## key' body returns '' (skipped).

    Present sections get a single trailing newline (YAML ``|`` clip
    semantics) so migrated templates are byte-identical to the YAML they
    replaced.
    """
    body = "## present.key\n```text\nhello\n```\n"
    assert UnifiedPromptManager._extract_skill_section(body, "present.key") == "hello\n"
    assert UnifiedPromptManager._extract_skill_section(body, "absent.key") == ""


def test_skill_section_extraction_clips_trailing_newlines() -> None:
    """Extra blank lines before the fence clip to exactly one trailing \\n."""
    body = "## k\n```text\nline\n\n\n```\n"
    assert UnifiedPromptManager._extract_skill_section(body, "k") == "line\n"


def test_skill_section_extraction_preserves_literal_braces() -> None:
    """Templates with JSON braces must survive extraction verbatim."""
    body = '## k\n```text\nreturn {{"x": 1}}\n```\n'
    assert UnifiedPromptManager._extract_skill_section(body, "k") == 'return {{"x": 1}}\n'


def test_initialize_skills_loads_skill_with_dash_in_frontmatter(tmp_path) -> None:
    """The runtime loader registers a skill whose frontmatter value contains
    '---'.

    The retired ``.split('---', 2)`` loader treated the inline dash as the
    closing delimiter, truncated the YAML, lost ``metadata.prompts``, and
    silently skipped the skill. Routing through the shared
    ``skill_frontmatter.parse_frontmatter`` fixes it. The ``skills_dir`` arg
    is the DI seam that lets this test point the loader at a temp tree.
    """
    skill = tmp_path / "content" / "dashy" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        '---\nname: dashy\ndescription: "a --- b"\nlicense: MIT\n'
        "metadata:\n  category: utility\n  prompts:\n    - key: dashy.go\n---\n\n"
        "## dashy.go\n```text\nDo {x}.\n```\n",
        encoding="utf-8",
    )

    pm = UnifiedPromptManager()
    pm.prompts.clear()
    pm.metadata.clear()
    pm._initialize_skills(skills_dir=tmp_path)

    assert "dashy.go" in pm.prompts
    assert pm.prompts["dashy.go"]["template"] == "Do {x}.\n"
