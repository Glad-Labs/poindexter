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
        screenshot_targets="- qa-rails: The QA Rails board",
    )
    assert "[CHART: chart-key]" in rendered
    assert "{chart_targets}" not in rendered
    assert "llm-decode-vs-delivered" in rendered


def test_two_pass_prompt_offers_the_screenshot_marker():
    """poindexter#1002 shipped inert and stayed that way for a month.

    ``[SCREENSHOT:]`` lived only on ``blog_generation.initial_draft`` while
    canonical_blog runs two_pass, so measured on 2026-09-01: zero
    ``image.screenshot`` media assets EVER, and 0 of 199 recent drafts carried
    the marker — despite a ``qa-rails`` target being configured the whole time.
    """
    from services.prompt_manager import UnifiedPromptManager

    rendered = UnifiedPromptManager().get_prompt(
        "atoms.two_pass_writer.generate_with_context",
        topic="T", angle="A", instructions="", snippet_block="S",
        target_length=1200,
        chart_targets="- llm-decode-vs-delivered: decode vs delivered",
        screenshot_targets="- qa-rails: The QA Rails board",
    )
    assert "[SCREENSHOT: target-key]" in rendered
    assert "{screenshot_targets}" not in rendered
    assert "qa-rails" in rendered


def test_two_pass_prompt_opens_only_the_two_evidence_markers():
    """CHART and SCREENSHOT both show the reader something REAL. Ordinary
    illustrations stay with the Image Decision Agent, so this prompt must not
    grow [IMAGE:] / [HERO-IMAGE:] as well — that would change how every post
    is illustrated, which is a different decision."""
    from pathlib import Path

    import services  # noqa: F401 — locate the package root

    skill = (
        Path(services.__file__).resolve().parents[2]  # src/cofounder_agent (services/ sits under poindexter/)
        / "skills" / "content" / "two-pass-writer" / "SKILL.md"
    )
    body = skill.read_text(encoding="utf-8")
    assert "[CHART: chart-key]" in body
    assert "[SCREENSHOT: target-key]" in body
    assert "[HERO-IMAGE:" not in body
    assert "[IMAGE:" not in body


# --- topic gate: the allowlist is offered only on posts about this system ---
#
# Offered on every draft, the writer took it where it made no sense — a
# Findings-board capture was the sole image on a forever-chemicals post
# (2026-09-05), a QA Rails capture on posts about a MUD, web tricks, and 90s
# coding advice. Two OR-ed signals decide: the topic batch's
# picked_candidate_kind (internal = drawn from the operator's own work) and a
# keyword CSV over topic / angle / tags for batch-less manual tasks.

_TARGETS = '{"qa-rails": {"url": "http://g/d/qa", "alt": "The QA Rails board"}}'


def _gate_sc(**overrides):
    from services.site_config import SiteConfig

    cfg = {"plugin.image_provider.screenshot.targets": _TARGETS}
    cfg.update(overrides)
    return SiteConfig(initial_config=cfg)


def test_internal_topic_kind_is_offered_the_allowlist():
    from modules.content.ai_content_generator import screenshot_targets_for_post

    out = screenshot_targets_for_post(
        _gate_sc(), topic_kind="internal", texts=("Sweep Process Optimization",),
    )
    assert "qa-rails" in out and "The QA Rails board" in out


def test_external_topic_with_no_keyword_is_told_none_for_this_post():
    from modules.content.ai_content_generator import screenshot_targets_for_post

    out = screenshot_targets_for_post(
        _gate_sc(), topic_kind="external",
        texts=('How the Disaster of "Forever Chemicals" Was Kept Secret', "narrative | direct"),
    )
    assert "none for this post" in out
    assert "qa-rails" not in out
    assert "do not use [SCREENSHOT" in out


def test_keyword_in_topic_qualifies_a_manual_task():
    """No batch lineage (topic_kind=None) — the keyword half still applies."""
    from modules.content.ai_content_generator import screenshot_targets_for_post

    out = screenshot_targets_for_post(
        _gate_sc(), topic_kind=None, texts=("Building Poindexter's QA rails",),
    )
    assert "qa-rails" in out


def test_keyword_matches_tags_in_the_angle_case_insensitively():
    from modules.content.ai_content_generator import screenshot_targets_for_post

    out = screenshot_targets_for_post(
        _gate_sc(), topic_kind="external",
        texts=("A post", "technical | direct | tags: POINDEXTER, ops"),
    )
    assert "qa-rails" in out


def test_operator_can_widen_kinds_and_keywords():
    from modules.content.ai_content_generator import is_post_about_this_system

    sc = _gate_sc(
        screenshot_topic_kinds="internal, external",
        screenshot_topic_keywords="poindexter,glad labs",
    )
    assert is_post_about_this_system(sc, topic_kind="external", texts=("anything",))
    assert is_post_about_this_system(
        _gate_sc(screenshot_topic_keywords="poindexter,glad labs"),
        topic_kind=None, texts=("Inside Glad Labs",),
    )


def test_both_csvs_empty_means_never_offered():
    """The explicit off switch — not a blank list the model fills with guesses."""
    from modules.content.ai_content_generator import screenshot_targets_for_post

    sc = _gate_sc(screenshot_topic_kinds="", screenshot_topic_keywords="")
    out = screenshot_targets_for_post(sc, topic_kind="internal", texts=("poindexter",))
    assert "none for this post" in out


def test_no_site_config_is_never_about_this_system():
    from modules.content.ai_content_generator import screenshot_targets_for_post

    assert "do not use [SCREENSHOT" in screenshot_targets_for_post(
        None, topic_kind="internal", texts=("poindexter",),
    )


def test_gate_passes_but_no_targets_configured_still_says_none_configured():
    from modules.content.ai_content_generator import screenshot_targets_for_post
    from services.site_config import SiteConfig

    sc = SiteConfig(initial_config={"plugin.image_provider.screenshot.targets": ""})
    out = screenshot_targets_for_post(sc, topic_kind="internal")
    assert "none configured" in out


def test_two_pass_generate_with_context_threads_topic_kind_to_the_gate(monkeypatch):
    """The gate must sit on the writer canonical_blog actually runs."""
    from modules.content import ai_content_generator as acg

    seen = {}

    def fake_gate(site_config, *, topic_kind, texts=()):
        seen["topic_kind"] = topic_kind
        seen["texts"] = texts
        return "none for this post — stub"

    async def fake_chat(prompt, **kw):
        seen["prompt"] = prompt
        return "draft"

    async def fake_model(*, site_config):
        return "m"

    monkeypatch.setattr(acg, "screenshot_targets_for_post", fake_gate)
    monkeypatch.setattr(acg, "_resolve_rag_writer_model", fake_model)
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_chat)
    sc = _gate_sc(writer_rag_context_snippet_max_chars="500")

    import asyncio

    asyncio.run(acg.generate_with_context(
        topic="T", angle="A | tags: x", snippets=[], site_config=sc, topic_kind="external",
    ))
    assert seen["topic_kind"] == "external"
    assert seen["texts"] == ("T", "A | tags: x")
    assert "none for this post — stub" in seen["prompt"]
