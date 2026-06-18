"""Tests for the two_pass_writer SKILL.md skill pack.

The two_pass_writer prompts were migrated from
``prompts/two_pass_writer.yaml`` to ``skills/content/two-pass-writer/SKILL.md``
(agentskills.io format), following the research/video/podcast/seo_metadata
migrations. These tests pin:

1. that both prompt keys still resolve (the migration didn't drop them),
2. that the templates keep their required placeholders and marker syntax,
3. that each template ends with exactly one trailing newline (YAML ``|`` clip
   semantics) so byte-fidelity with the retired YAML is preserved.

See docs/architecture/business-os-endgame.md.
"""

from __future__ import annotations

from services.prompt_manager import UnifiedPromptManager

_TWO_PASS_KEYS = (
    "atoms.two_pass_writer.revise_prompt",
    "atoms.two_pass_writer.generate_with_context",
)


def test_two_pass_keys_resolve_from_skill() -> None:
    """Both keys must load from skills/content/two-pass-writer/SKILL.md."""
    pm = UnifiedPromptManager()
    for key in _TWO_PASS_KEYS:
        assert key in pm.prompts, f"{key} did not load from the two-pass-writer skill"
        assert pm.prompts[key]["template"].strip(), f"{key} has an empty template"


def test_two_pass_templates_have_placeholders() -> None:
    """Templates must keep the placeholders str.format renders against."""
    pm = UnifiedPromptManager()

    revise = pm.prompts["atoms.two_pass_writer.revise_prompt"]["template"]
    assert "{draft}" in revise
    assert "{aug_block}" in revise
    # The marker syntax must survive verbatim — that's how the model knows
    # what to substitute.
    assert "[EXTERNAL_NEEDED:" in revise

    generate = pm.prompts["atoms.two_pass_writer.generate_with_context"]["template"]
    assert "{topic}" in generate
    assert "{angle}" in generate
    assert "{instructions}" in generate
    assert "{snippet_block}" in generate


def test_two_pass_templates_end_with_single_newline() -> None:
    """Each template ends with exactly one trailing newline (YAML | clip)."""
    pm = UnifiedPromptManager()
    for key in _TWO_PASS_KEYS:
        template = pm.prompts[key]["template"]
        assert template.endswith("\n"), f"{key} should end with a newline"
        assert not template.endswith("\n\n"), f"{key} has extra trailing newlines"


def test_two_pass_templates_render_without_stray_braces() -> None:
    """get_prompt() str.format()s the template — any literal ``{`` that isn't a
    real placeholder raises KeyError/ValueError. Render both via the production
    path to prove the enriched bodies have no stray braces."""
    pm = UnifiedPromptManager()
    generate = pm.get_prompt(
        "atoms.two_pass_writer.generate_with_context",
        topic="RTX 5090 local LLM inference",
        angle="real benchmarks",
        instructions="SOURCES: ...",
        snippet_block="[posts/1] we ran it on a 32GB card",
    )
    assert "RTX 5090 local LLM inference" in generate
    revise = pm.get_prompt(
        "atoms.two_pass_writer.revise_prompt",
        draft="a draft",
        aug_block="[EXTERNAL_NEEDED: x] -> fact",
    )
    assert "a draft" in revise


def test_generate_prompt_carries_assembly_directives() -> None:
    """The enriched draft prompt must keep the directives that stop the local
    model's assembly failures (duplication, truncation, unlinked citations,
    fake headings) and enable grounded first-person. Pinned so a future prompt
    edit can't silently drop them and reopen the QA-veto regression
    (glad-labs-stack#1672 follow-up)."""
    pm = UnifiedPromptManager()
    g = pm.prompts["atoms.two_pass_writer.generate_with_context"]["template"].lower()
    # Anti-duplication + clean-ending (the two ollama_critic vetoes)
    assert "once" in g and ("repeat" in g or "duplicate" in g)
    assert "mid-sentence" in g
    # Markdown-link citation (the programmatic unlinked-citation veto)
    assert "markdown link" in g and "url" in g
    # Real H2 headings, not bold fakes
    assert "## " in pm.prompts["atoms.two_pass_writer.generate_with_context"]["template"]
    # Grounded first-person voice (Matt's voice-policy update)
    assert "first person" in g


def test_revise_prompt_guards_against_duplication() -> None:
    """The revise pass feeds the model the full draft; without an explicit
    'exactly once / do not duplicate' guard a weak model re-emits it doubled
    (the observed second-half-duplicate failure)."""
    pm = UnifiedPromptManager()
    r = pm.prompts["atoms.two_pass_writer.revise_prompt"]["template"].lower()
    assert "once" in r
    assert "duplicate" in r or "repeat" in r


def test_generate_prompt_carries_citation_and_antifabrication_directives() -> None:
    """After #1676 cleared the assembly vetoes, the live re-run (task
    601283cc, score 89) had two residual QA blockers: the writer (1) invented
    a statistic ("~25% increase") and (2) echoed the internal [source/ref]
    snippet labels inline as pseudo-citations (e.g. "[token_efficiency.md
    feedback_token]"). Pin the directives that close both so a later prompt
    edit can't silently reopen them (glad-labs-stack#1676 follow-up)."""
    pm = UnifiedPromptManager()
    g = pm.prompts["atoms.two_pass_writer.generate_with_context"]["template"].lower()
    # Anti-fabrication — never invent a number
    assert "never invent" in g
    assert "statistic" in g or "percentage" in g
    # Internal background-note labels are NOT citations; don't reproduce them
    assert "never reproduce" in g
    assert "label" in g
    # The only brackets allowed in the body are real markdown links
    assert "markdown link" in g


def test_format_snippet_block_drops_citation_bracket_template() -> None:
    """Root-cause fix for the snippet-echo bug: a weak/thinking model mimics
    whatever bracket form its background notes are shown in. The old
    ``[source/ref] text`` format taught it to emit
    ``[token_efficiency.md feedback_token]`` pseudo-citations into the prose.
    ``_format_snippet_block`` uses a plain ``From <source>:`` prefix — same
    framing (whose work this is, for first/third-person voice), no inline
    bracket to copy — and drops the ref slug entirely (the most-echoed token).
    """
    import modules.content.ai_content_generator as acg
    snippets = [
        {"source": "token_efficiency.md", "ref": "feedback_token",
         "snippet": "Cut tokens to cut cost."},
        {"source": "posts", "ref": "a51d-why-local",
         "snippet": "We moved inference on-prem."},
    ]
    block = acg._format_snippet_block(snippets, 500)
    # snippet text survives, with the source framing
    assert "Cut tokens to cut cost." in block
    assert "We moved inference on-prem." in block
    assert "From token_efficiency.md:" in block
    # no '[source/ref]' bracket template and no ref slug for the model to echo
    assert "[token_efficiency.md" not in block
    assert "feedback_token" not in block
    assert "a51d-why-local" not in block


def test_format_snippet_block_skips_empty_and_defaults_missing_source() -> None:
    """Empty snippets are skipped; a missing source falls back to a neutral
    label (never a KeyError, never a bare 'None')."""
    import modules.content.ai_content_generator as acg
    snippets = [
        {"source": "posts", "ref": "x", "snippet": ""},  # skipped — no snippet
        {"ref": "y", "snippet": "Kept."},                # missing source
    ]
    block = acg._format_snippet_block(snippets, 500)
    assert "Kept." in block
    assert block.count("From ") == 1
    assert "None" not in block


def test_generate_prompt_bans_footnotes_and_placeholder_urls() -> None:
    """The #1680 re-run (task 2b0255ad) passed the gate but the writer, denied
    its inline-label trick, switched to academic footnotes ([^1]) plus a bottom
    reference block of placeholder/guessed URLs (e.g. "[markaicode.com/...]",
    "Placeholder URLs derived from..."). Pin the explicit bans so un-URLed
    (internal) facts go in plain prose and only real external URLs become inline
    links (glad-labs-stack#1680 follow-up)."""
    pm = UnifiedPromptManager()
    g = pm.prompts["atoms.two_pass_writer.generate_with_context"]["template"].lower()
    assert "footnote" in g          # bans [^1]-style footnote markers
    assert "reference list" in g    # bans a bottom References/Sources section
    assert "guessed" in g           # bans placeholder / guessed URLs
