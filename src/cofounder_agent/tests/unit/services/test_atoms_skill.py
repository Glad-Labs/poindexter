"""Tests for the ``atoms`` SKILL.md pack in ``services.prompt_manager``.

The atom prompts were migrated from ``prompts/atoms.yaml`` to
``skills/content/atoms/SKILL.md`` (agentskills.io format). This pack is
special: the ``narrate_bundle`` and ``pipeline_architect`` templates carry
the operator persona as ``{site_name}`` / ``{site_url}`` placeholders that
the calling atom renders from the run-bound ``site_config`` before the text
reaches the model (the operator brand used to be hardcoded "Glad Labs" /
"gladlabs.io").

These tests pin:

1. that all three atom keys still resolve from the skill (migration didn't
   drop them),
2. that each template ends with a single trailing newline (YAML ``|`` clip
   semantics — byte-identical to the old YAML),
3. a render test for the brand-bearing keys — formatting the template with
   ``site_name`` / ``site_url`` produces the operator brand and leaves NO
   literal ``{site_name}`` / ``{site_url}`` markers behind, and the call does
   not raise (proves every other brace is escaped).

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

from services.prompt_manager import UnifiedPromptManager

_ATOM_KEYS = (
    "atoms.narrate_bundle.system_prompt",
    "atoms.review_with_critic.system_prompt",
    "atoms.pipeline_architect.system_prompt",
)

# The keys whose templates carry the operator persona as placeholders.
_BRAND_KEYS = (
    "atoms.narrate_bundle.system_prompt",
    "atoms.pipeline_architect.system_prompt",
)

_SITE_NAME = "Glad Labs"
_SITE_URL = "https://gladlabs.io"


def test_atom_keys_resolve_from_skill() -> None:
    """All three atom keys must load from skills/content/atoms/SKILL.md."""
    pm = UnifiedPromptManager()
    for key in _ATOM_KEYS:
        assert key in pm.prompts, f"{key} did not load from the atoms skill"
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


def test_atom_templates_end_with_single_trailing_newline() -> None:
    """Each template ends with exactly one ``\\n`` (YAML ``|`` clip semantics)."""
    pm = UnifiedPromptManager()
    for key in _ATOM_KEYS:
        template = pm.prompts[key]["template"]
        assert template.endswith("\n"), f"{key} must end with a trailing newline"
        assert not template.endswith("\n\n"), f"{key} must clip to one trailing newline"


def test_brand_placeholders_render_from_site_config() -> None:
    """Render test: the brand keys format cleanly with site vars.

    Formatting must (a) not raise (every JSON brace is escaped as ``{{``/
    ``}}``), (b) inject the operator brand, and (c) leave no literal
    ``{site_name}`` / ``{site_url}`` markers behind.
    """
    pm = UnifiedPromptManager()
    for key in _BRAND_KEYS:
        template = pm.prompts[key]["template"]
        # Sanity: the placeholder must be present before rendering.
        assert "{site_name}" in template, f"{key} should carry a {{site_name}} placeholder"

        rendered = template.format(site_name=_SITE_NAME, site_url=_SITE_URL)

        assert _SITE_NAME in rendered, f"{key} must contain the rendered site_name"
        assert "{site_name}" not in rendered, f"{key} left a literal {{site_name}} marker"
        assert "{site_url}" not in rendered, f"{key} left a literal {{site_url}} marker"


def test_narrate_bundle_renders_site_url() -> None:
    """narrate_bundle's grounding list names the site URL — it must render."""
    pm = UnifiedPromptManager()
    template = pm.prompts["atoms.narrate_bundle.system_prompt"]["template"]
    assert "{site_url}" in template
    rendered = template.format(site_name=_SITE_NAME, site_url=_SITE_URL)
    assert _SITE_URL in rendered


def test_review_with_critic_has_no_brand_token() -> None:
    """The critic prompt carries no operator brand — no placeholders to render."""
    pm = UnifiedPromptManager()
    template = pm.prompts["atoms.review_with_critic.system_prompt"]["template"]
    assert "{site_name}" not in template
    assert "{site_url}" not in template
    # The JSON schema braces survive extraction escaped as {{ }}.
    assert '"factual_accuracy"' in template
