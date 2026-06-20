"""Unit tests for services/skill_frontmatter.py — the single SKILL.md parser
shared by the importer (install-time) and prompt_manager (runtime).

Before this module the two paths used DIFFERENT parsers: the importer's
newline-anchored ``find('\\n---')`` and the runtime loader's naive
``.split('---', 2)``. They disagreed on any ``---`` that appears inside a
frontmatter value — the split-based loader truncated the YAML, dropped
``metadata.prompts``, and silently skipped the skill. These tests pin the
robust behavior and the ``extract_section`` semantics the YAML loader had.
"""

from __future__ import annotations

import textwrap

import pytest

from services.skill_frontmatter import (
    SkillFrontmatterError,
    extract_section,
    parse_frontmatter,
)

_SKILL_WITH_DASH_IN_VALUE = textwrap.dedent(
    """\
    ---
    name: dashy
    description: "before --- after"
    license: MIT
    metadata:
      category: utility
      prompts:
        - key: dashy.go
    ---

    ## dashy.go

    ```text
    Do {x}.
    ```
    """
)


class TestParseFrontmatter:
    @pytest.mark.unit
    def test_returns_meta_and_body(self):
        meta, body = parse_frontmatter(_SKILL_WITH_DASH_IN_VALUE)
        assert meta["name"] == "dashy"
        assert meta["metadata"]["prompts"][0]["key"] == "dashy.go"
        assert "## dashy.go" in body

    @pytest.mark.unit
    def test_dash_in_frontmatter_value_is_not_a_delimiter(self):
        """A '---' inside a quoted value must not close the frontmatter.

        The retired ``.split('---', 2)`` loader truncated the YAML here,
        dropping metadata.prompts and silently failing to register the skill.
        """
        meta, body = parse_frontmatter(_SKILL_WITH_DASH_IN_VALUE)
        # Full frontmatter survived — license + metadata are intact.
        assert meta["license"] == "MIT"
        assert meta["description"] == "before --- after"
        assert meta["metadata"]["category"] == "utility"
        # Body starts at the real closing delimiter, not the inline dash.
        assert body.lstrip().startswith("## dashy.go")

    @pytest.mark.unit
    def test_dash_in_body_is_preserved(self):
        raw = (
            "---\nname: x\ndescription: d\nlicense: MIT\n"
            "metadata:\n  category: utility\n  prompts:\n    - key: x.go\n---\n\n"
            "## x.go\n```text\na\n```\n\n---\n\n## x.extra\n```text\nb\n```\n"
        )
        _, body = parse_frontmatter(raw)
        # A mid-body '---' thematic break and the section after it both survive.
        assert "## x.extra" in body

    @pytest.mark.unit
    def test_missing_open_delimiter_raises(self):
        with pytest.raises(SkillFrontmatterError, match="must start with"):
            parse_frontmatter("# no frontmatter\n")

    @pytest.mark.unit
    def test_unclosed_frontmatter_raises(self):
        with pytest.raises(SkillFrontmatterError, match="not closed"):
            parse_frontmatter("---\nname: oops\n")

    @pytest.mark.unit
    def test_non_mapping_frontmatter_raises(self):
        with pytest.raises(SkillFrontmatterError, match="mapping"):
            parse_frontmatter("---\n- just\n- a\n- list\n---\nbody\n")


class TestExtractSection:
    @pytest.mark.unit
    def test_returns_fenced_block(self):
        body = "## k\n```text\nhello\n```\n"
        assert extract_section(body, "k") == "hello\n"

    @pytest.mark.unit
    def test_missing_section_returns_empty(self):
        assert extract_section("## a\n```text\nx\n```\n", "b") == ""

    @pytest.mark.unit
    def test_clips_to_single_trailing_newline(self):
        body = "## k\n```text\nline\n\n\n```\n"
        assert extract_section(body, "k") == "line\n"

    @pytest.mark.unit
    def test_preserves_literal_braces(self):
        body = '## k\n```text\nreturn {{"x": 1}}\n```\n'
        assert extract_section(body, "k") == 'return {{"x": 1}}\n'
