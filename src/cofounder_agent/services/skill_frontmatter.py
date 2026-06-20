"""Single source of truth for reading SKILL.md files.

Both the importer (:mod:`services.skill_importer`, install-time validation)
and the runtime prompt loader
(:meth:`services.prompt_manager.UnifiedPromptManager._initialize_skills`)
parse SKILL.md through these helpers, so the install path and the runtime path
can never disagree about what a skill contains.

Historically the two paths used DIFFERENT parsers: the importer anchored the
closing delimiter to a newline (``find('\\n---')``), while the loader used a
naive ``.split('---', 2)`` that treated ANY ``---`` substring — including one
inside a quoted frontmatter value — as the delimiter. A skill could import
clean and then silently fail to load. This module collapses both onto the
robust, newline-anchored parse.
"""

from __future__ import annotations

import re
from typing import Any

import yaml  # type: ignore[import-untyped]


class SkillFrontmatterError(ValueError):
    """Raised when a SKILL.md cannot be parsed.

    Covers missing/unclosed frontmatter, invalid YAML, and non-mapping
    frontmatter. Subclasses ``ValueError`` so the runtime loader's broad
    ``except Exception`` keeps skipping malformed skills, while the importer
    can catch this specific type and re-raise it as the user-facing
    ``SkillImportError``.
    """


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md string into ``(frontmatter_mapping, body)``.

    The closing delimiter is the first ``---`` that begins its own line after
    the opening block (``find('\\n---')``), so a ``---`` inside a quoted
    frontmatter value — or a thematic break in the body — is never mistaken
    for it.

    Raises :class:`SkillFrontmatterError` when the frontmatter is missing,
    unclosed, invalid YAML, or not a mapping.
    """
    if not raw.startswith("---"):
        raise SkillFrontmatterError(
            "SKILL.md must start with a YAML frontmatter block "
            "(first line must be '---')."
        )

    end = raw.find("\n---", 3)
    if end == -1:
        raise SkillFrontmatterError(
            "SKILL.md frontmatter block is not closed (missing second '---')."
        )

    fm_text = raw[3:end].strip()
    body = raw[end + 4 :]  # skip the leading '\n---'

    try:
        meta = yaml.safe_load(fm_text)
    except Exception as exc:
        raise SkillFrontmatterError(
            f"SKILL.md frontmatter YAML is invalid: {exc}"
        ) from exc

    if not isinstance(meta, dict):
        raise SkillFrontmatterError("SKILL.md frontmatter must be a YAML mapping.")

    return meta, body


def extract_section(body: str, key: str) -> str:
    """Return the fenced template under a ``## <key>`` heading, or ''.

    Normalizes to YAML ``|`` (literal block, clip-chomp) semantics — a single
    trailing newline — so a SKILL.md template is byte-identical to the YAML
    ``template: |`` it replaced. Downstream snapshot tests pin that trailing
    ``\\n`` (e.g. test_topic_ranking_prompt.py) and rendered prompts assume it.
    """
    match = re.search(
        rf"^##\s+{re.escape(key)}\s*$\n+```[^\n]*\n(.*?)\n```",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return ""
    return match.group(1).rstrip("\n") + "\n"
