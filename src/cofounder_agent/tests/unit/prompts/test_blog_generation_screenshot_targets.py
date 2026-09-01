"""The writer prompt's ``{screenshot_targets}`` / ``{chart_targets}`` vars must stay wired.

``UnifiedPromptManager.get_prompt`` renders with ``str.format``, so a
placeholder in the template with no matching kwarg raises ``KeyError`` — and
``ai_content_generator`` re-raises it, which fails the whole draft. Adding
``[SCREENSHOT: …]`` to the prompt (poindexter#1002) therefore also added a
kwarg at the one call site; this pins them together, because the failure mode
is "no post generates at all", not a degraded image.

Extras are ignored by ``str.format``, so passing the kwarg is safe against a
Langfuse premium override whose template omits the placeholder.
"""

from __future__ import annotations

import pytest

from services.prompt_manager import UnifiedPromptManager

# The exact kwarg set modules/content/ai_content_generator.py passes.
_CALL_SITE_KWARGS = {
    "topic": "a topic",
    "target_audience": "a general audience",
    "primary_keyword": "kw",
    "research_context": "ctx",
    "internal_link_titles": "none",
    "screenshot_targets": "- qa-rails: The QA Rails board",
    "chart_targets": "- llm-decode-vs-delivered: decode vs delivered",
    "target_length": 1200,
    "word_count": 1200,
    "style": "technical",
    "tone": "direct",
}


def test_initial_draft_renders_with_call_site_kwargs():
    pm = UnifiedPromptManager()
    if "blog_generation.initial_draft" not in pm.prompts:
        pytest.skip("blog_generation pack not registered in this install")
    rendered = pm.get_prompt("blog_generation.initial_draft", **_CALL_SITE_KWARGS)
    assert "- qa-rails: The QA Rails board" in rendered
    assert "{screenshot_targets}" not in rendered
    assert "{chart_targets}" not in rendered


def test_initial_draft_mentions_the_screenshot_marker():
    pm = UnifiedPromptManager()
    if "blog_generation.initial_draft" not in pm.prompts:
        pytest.skip("blog_generation pack not registered in this install")
    raw = pm.prompts["blog_generation.initial_draft"]
    body = raw if isinstance(raw, str) else str(raw)
    assert "[SCREENSHOT:" in body, (
        "the writer can no longer request a screenshot — the ScreenshotProvider "
        "is only reachable via this marker"
    )
    assert "{chart_targets}" in body, (
        "the [CHART:] allowlist placeholder vanished from the SKILL pack — the "
        "writer would be told charts exist without being told which keys are "
        "valid, and every invented key renders an empty slot"
    )
    assert "{screenshot_targets}" in body, (
        "the allowlist must be enumerated in the prompt, or the model invents "
        "target keys and every one resolves to an empty slot"
    )


def test_describe_screenshot_targets_renders_allowlist():
    from modules.content.ai_content_generator import _describe_screenshot_targets
    from services.site_config import SiteConfig

    sc = SiteConfig(initial_config={
        "plugin.image_provider.screenshot.targets":
            '{"qa-rails": {"url": "http://g/d/qa", "alt": "The QA Rails board"}}',
    })
    out = _describe_screenshot_targets(sc)
    assert "qa-rails" in out
    assert "The QA Rails board" in out


@pytest.mark.parametrize(
    "targets", ["", "{}", "{not json", None],
    ids=["unset", "empty", "malformed", "no-site-config"],
)
def test_describe_screenshot_targets_tells_writer_to_skip_when_unusable(targets):
    """An install with no usable allowlist must steer the writer away.

    The shipped default is empty, so this is the common path — the prompt has
    to say "don't use the marker" rather than leave a blank list the model
    fills in with guesses.
    """
    from modules.content.ai_content_generator import _describe_screenshot_targets
    from services.site_config import SiteConfig

    sc = None if targets is None else SiteConfig(
        initial_config={"plugin.image_provider.screenshot.targets": targets},
    )
    out = _describe_screenshot_targets(sc)
    assert "none configured" in out


def test_two_pass_writer_prompt_also_offers_the_chart_marker():
    """The marker must be on the writer canonical_blog ACTUALLY runs.

    ``[SCREENSHOT:]`` (poindexter#1002) is offered only on the initial_draft
    prompt, and two_pass — the live canonical_blog writer — carries no markers
    at all. Measured 2026-09-01: zero ``image.screenshot`` media assets have
    ever been produced and 0 of 199 recent drafts contain the marker. A chart
    marker wired the same way would have been dormant on arrival.
    """
    from services.prompt_manager import UnifiedPromptManager

    rendered = UnifiedPromptManager().get_prompt(
        "atoms.two_pass_writer.generate_with_context",
        topic="T", angle="A", instructions="", snippet_block="S",
        target_length=1200,
        chart_targets="- llm-decode-vs-delivered: decode vs delivered",
    )
    assert "[CHART: chart-key]" in rendered
    assert "{chart_targets}" not in rendered
    assert "llm-decode-vs-delivered" in rendered


def test_two_pass_prompt_does_not_open_the_other_image_markers():
    """Only CHART is added there. Image placement stays with the decision
    agent, so the blast radius is one allowlisted, code-defined chart rather
    than a change to how every post gets illustrated."""
    from pathlib import Path

    import services  # noqa: F401 — locate the package root

    skill = (
        Path(services.__file__).resolve().parent.parent
        / "skills" / "content" / "two-pass-writer" / "SKILL.md"
    )
    body = skill.read_text(encoding="utf-8")
    assert "[CHART: chart-key]" in body
    assert "[HERO-IMAGE:" not in body
    assert "[SCREENSHOT:" not in body
