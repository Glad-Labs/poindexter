"""Tests for the TWO_PASS writer mode (LangGraph state machine).

Deviations from plan:
- Monkeypatches `services.topic_ranking.embed_text` instead of
  `services.embedding_service.embed_text` because embedding_service has
  no module-level `embed_text` helper — that helper lives in topic_ranking.
- Uses `raising=False` for the `generate_with_context` and `research_topic`
  monkeypatches because neither symbol currently exists in production
  (`ai_content_generator` and `research_service` modules). Production
  wire-up of those callables is tracked separately (Task 14 wires the
  writer; `research_topic` needs a follow-up to expose a module-level
  helper around `ResearchService`).
- The plan's `_fake_pool_with_no_snippets` helper used `AsyncMock()` for
  the pool, which made `pool.acquire()` return a coroutine and broke the
  `async with` protocol. Switched to `MagicMock` for the sync `acquire`
  method that returns an async-context-manager mock, matching the
  established pool-mocking pattern elsewhere in this codebase.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.content.atoms import two_pass_writer as two_pass

pytestmark = pytest.mark.asyncio


def _fake_site_config():
    """Minimal SiteConfig stub so two_pass's resolve_local_model has a
    pipeline_writer_model to return. Added 2026-05-12 with batch 11 of
    the fail-loud sweep (poindexter#485): the previous hardcoded
    ``glm-4.7-5090:latest`` fallback was removed in favour of failing
    loud when neither pipeline_writer_model nor cost_tier.standard.model
    resolves."""
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda key, default="": {
        "pipeline_writer_model": "glm-4.7-5090:latest",
        "cost_tier.standard.model": "",
        # The keep-best expansion pass is orthogonal to the draft / revise /
        # variant-fallback machinery these tests exercise (and would add an
        # extra ollama_chat_text call to their exact call-count assertions).
        # It has its own fixture (_short_draft_site_config) + dedicated tests,
        # so keep it OFF in the shared stub.
        "writer_length_expansion_enabled": "false",
    }.get(key, default))
    sc.get_int = MagicMock(side_effect=lambda key, default=0: default)
    sc.get_float = MagicMock(side_effect=lambda key, default=0.0: default)
    return sc


def _fake_pool_with_no_snippets():
    """Fake asyncpg pool whose acquire() context manager yields a conn with
    fetch() → []. Note: pool.acquire is a SYNC method that returns an
    object supporting `async with`, so we use MagicMock for acquire (not
    AsyncMock) to avoid the call returning a coroutine."""
    pool = MagicMock()
    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(return_value=[])
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


def _grounding_site_config(*, enabled="true", source_filter="", site_url=""):
    """SiteConfig stub for the prior-work anchor (#822) tests.

    Supplies get_int/get_float (the graph's snippet-fetch node reads them) and
    forces the keep-best expansion pass OFF so run() doesn't make an unpatched
    LLM call, mirroring _fake_site_config."""
    sc = MagicMock()
    values = {
        "writer_internal_grounding_enabled": enabled,
        "writer_rag_source_filter": source_filter,
        "rag_source_filter": "",
        "site_url": site_url,
        "pipeline_writer_model": "glm-4.7-5090:latest",
        "writer_length_expansion_enabled": "false",
    }
    sc.get = MagicMock(side_effect=lambda key, default="": values.get(key, default))
    sc.get_int = MagicMock(side_effect=lambda key, default=0: default)
    sc.get_float = MagicMock(side_effect=lambda key, default=0.0: default)
    return sc


def _grounding_pool(*, post_row=None):
    """Pool whose conn.fetchrow returns post_row (for the posts URL lookup)."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=post_row)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)
    return pool


async def test_grounding_section_post_eligible_has_preview_and_url(monkeypatch):
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)
    ig = {"source_table": "posts", "source_id": "42",
          "preview": "How we cut VRAM spill on a single GPU.", "similarity": 0.71}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(site_url="https://www.gladlabs.io"),
        pool=_grounding_pool(post_row={"slug": "vram-spill"}),
    )
    assert src == "posts"
    assert "PRIOR WORK" in section
    assert "How we cut VRAM spill on a single GPU." in section
    assert "https://www.gladlabs.io/posts/vram-spill" in section


async def test_grounding_section_ineligible_source_returns_empty(monkeypatch):
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)
    ig = {"source_table": "memory", "source_id": "9", "preview": "ops note", "similarity": 0.8}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(),  # default filter = posts only
        pool=_grounding_pool(),
    )
    assert section == ""
    assert src is None


async def test_grounding_section_nonpost_eligible_is_framing_only(monkeypatch):
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)
    ig = {"source_table": "claude_sessions", "source_id": "s1",
          "preview": "we debugged the checkpoint poisoning", "similarity": 0.66}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(source_filter="posts,claude_sessions"),
        pool=_grounding_pool(),
    )
    assert src == "claude_sessions"
    assert "PRIOR WORK" in section
    assert "we debugged the checkpoint poisoning" in section
    assert "/posts/" not in section  # no link for a non-post source


async def test_grounding_section_scrub_failure_returns_empty(monkeypatch):
    def _boom(_t):
        raise RuntimeError("scrub down")
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", _boom)
    ig = {"source_table": "posts", "source_id": "42", "preview": "x", "similarity": 0.9}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(),
        pool=_grounding_pool(post_row={"slug": "s"}),
    )
    assert section == ""
    assert src is None


async def test_grounding_section_post_url_relative_when_no_site_url(monkeypatch):
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)
    ig = {"source_table": "posts", "source_id": "7", "preview": "prev", "similarity": 0.7}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(site_url=""),
        pool=_grounding_pool(post_row={"slug": "rel-slug"}),
    )
    assert "/posts/rel-slug" in section
    assert "http" not in section


async def test_grounding_section_empty_preview_returns_empty(monkeypatch):
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)
    ig = {"source_table": "posts", "source_id": "42", "preview": "   ", "similarity": 0.9}
    section, src = await two_pass._build_internal_grounding_section(
        ig, site_config=_grounding_site_config(), pool=_grounding_pool(post_row={"slug": "s"}),
    )
    assert section == ""
    assert src is None


def test_internal_grounding_enabled_flag():
    assert two_pass._internal_grounding_enabled(_grounding_site_config(enabled="false")) is False
    assert two_pass._internal_grounding_enabled(_grounding_site_config(enabled="true")) is True
    assert two_pass._internal_grounding_enabled(None) is True


async def test_internal_grounding_post_injected_into_draft_prompt(monkeypatch):
    """A grounded post match reaches the writer's draft prompt as a PRIOR WORK
    section, after SOURCES, with the resolved /posts/<slug> link."""
    captured: dict[str, str] = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["instructions"] = extra_instructions or ""
        return "A clean first draft with no markers."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_grounding_pool(post_row={"slug": "vram-spill"}),
        site_config=_grounding_site_config(site_url="https://www.gladlabs.io"),
        research_context="Source A: something (https://example.com).",
        internal_grounding={"source_table": "posts", "source_id": "42",
                            "preview": "How we cut VRAM spill.", "similarity": 0.7},
    )
    instr = captured["instructions"]
    assert "PRIOR WORK" in instr
    assert "How we cut VRAM spill." in instr
    assert "https://www.gladlabs.io/posts/vram-spill" in instr
    # Appended AFTER the SOURCES section.
    assert instr.index("SOURCES") < instr.index("PRIOR WORK")
    assert result["internal_grounding_anchor_injected"] is True
    assert result["internal_grounding_source_table"] == "posts"


async def test_internal_grounding_absent_leaves_prompt_unchanged(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["instructions"] = extra_instructions or ""
        return "A clean first draft with no markers."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_grounding_pool(), site_config=_grounding_site_config(),
    )
    assert "PRIOR WORK" not in captured["instructions"]
    assert result["internal_grounding_anchor_injected"] is False


async def test_internal_grounding_disabled_flag_no_section(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["instructions"] = extra_instructions or ""
        return "draft"
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_grounding_pool(post_row={"slug": "s"}),
        site_config=_grounding_site_config(enabled="false", site_url="https://x.io"),
        internal_grounding={"source_table": "posts", "source_id": "1",
                            "preview": "p", "similarity": 0.9},
    )
    assert "PRIOR WORK" not in captured["instructions"]
    assert result["internal_grounding_anchor_injected"] is False


async def test_internal_grounding_ineligible_source_no_section(monkeypatch):
    captured: dict[str, str] = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["instructions"] = extra_instructions or ""
        return "draft"
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)
    monkeypatch.setattr("services.rag_scrub.scrub_rag_text", lambda t: t)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_grounding_pool(), site_config=_grounding_site_config(),  # posts-only
        internal_grounding={"source_table": "memory", "source_id": "9",
                            "preview": "ops", "similarity": 0.9},
    )
    assert "PRIOR WORK" not in captured["instructions"]
    assert result["internal_grounding_anchor_injected"] is False


async def test_no_external_needed_returns_pass1_draft(monkeypatch):
    """First draft has no [EXTERNAL_NEEDED] markers → graph short-circuits, no revise."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return "A clean first draft with no markers."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    async def fake_embed(text, *, site_config=None): return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )
    assert result["draft"] == "A clean first draft with no markers."
    assert result["external_lookups"] == []
    assert result["revision_loops"] == 0


async def test_external_needed_triggers_research_and_revise(monkeypatch):
    """One marker → research → revise → done in 1 loop.

    2026-05-16: ``_revise_node`` now calls
    :func:`services.llm_text.ollama_chat_text` (plain-text chat) instead
    of the JSON-format helper that wrapped prose in
    ``{"content": "..."}``. Patch path updated accordingly.
    """
    drafts = iter([
        "First draft with [EXTERNAL_NEEDED: a fact] inside.",
        "Revised draft with the actual fact inside.",
    ])
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return next(drafts)
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)
    async def fake_revise(prompt, **kwargs):
        return next(drafts)
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)
    async def fake_research(query, max_sources=2, *, site_config=None):
        return f"External research result for: {query}"
    monkeypatch.setattr("services.research_service.research_topic", fake_research, raising=False)
    async def fake_embed(text, *, site_config=None): return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )
    assert "Revised draft" in result["draft"]
    assert len(result["external_lookups"]) == 1
    assert result["revision_loops"] == 1


async def test_research_context_injected_into_draft_prompt(monkeypatch):
    """research_context (the ResearchService corpus) must reach the writer's
    DRAFT prompt so it can ground + cite its claims — not just the QA layer.

    Pins the niche-writer research disconnect found 2026-06-09:
    ``GenerateContentStage._collect_research_context`` fetched the external
    sources and handed them to the critic/ragas/deepeval rails, but
    ``two_pass.run`` never threaded ``research_context`` into the draft, so
    the writer drafted research-blind and the critic correctly rejected
    every ``glad-labs`` post for "completely ignores the provided SOURCES
    corpus" (gemma4 run, ollama_critic 68/100 FAIL, score 88 → rejected).
    """
    captured: dict[str, str] = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["instructions"] = extra_instructions or ""
        return "A clean first draft with no markers."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    research = (
        "Source A: 2026 Developer Content Survey "
        "(https://example.com/survey) — 60% of technical founders cite "
        "the strategy gap as their top blocker."
    )
    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
        research_context=research,
    )
    assert result["draft"] == "A clean first draft with no markers."
    # The collected research corpus must appear verbatim in the writer's
    # draft instruction, framed as citeable SOURCES.
    assert research in captured["instructions"]
    assert "SOURCES" in captured["instructions"]


async def test_no_research_context_leaves_draft_prompt_unchanged(monkeypatch):
    """When no research_context is supplied, the draft instruction must not
    gain an empty SOURCES section (byte-compatible with prior behaviour)."""
    captured: dict[str, str] = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["instructions"] = extra_instructions or ""
        return "A clean first draft with no markers."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", fake_pass1, raising=False)

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )
    assert "SOURCES" not in captured["instructions"]


async def test_loop_caps_at_max_revisions(monkeypatch):
    """Pathological: every revision adds new markers. Loop must terminate at _MAX_REVISION_LOOPS=3."""
    counter = {"n": 0}
    async def always_needs_more(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        counter["n"] += 1
        return f"Draft with [EXTERNAL_NEEDED: thing {counter['n']}] inside."
    monkeypatch.setattr("modules.content.ai_content_generator.generate_with_context", always_needs_more, raising=False)
    async def fake_revise(prompt, **kwargs):
        counter["n"] += 1
        return f"Revised with [EXTERNAL_NEEDED: another thing {counter['n']}]."
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)
    async def fake_research(query, max_sources=2, *, site_config=None):
        return "fact"
    monkeypatch.setattr("services.research_service.research_topic", fake_research, raising=False)
    async def fake_embed(text, *, site_config=None): return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )
    assert result["revision_loops"] == 3
    assert result["loop_capped"] is True


async def test_revise_uses_plain_text_helper_not_json_helper(monkeypatch):
    """Pins the 2026-05-16 fix: ``_revise_node`` must NOT route through
    ``services.topic_ranking._ollama_chat_json`` (which forces
    ``format=json`` on Ollama and produces ``{"content": "..."}`` blobs).
    Captured 2026-05-15: ``pipeline_versions.id=1851`` shipped a literal
    ``}`` as the final line because the wrong helper was wired in here.
    """
    # If two_pass regresses to the JSON helper, this stub will be
    # invoked. We mark that as a hard failure.
    async def forbidden_json_helper(prompt, **kwargs):
        raise AssertionError(
            "_revise_node regressed back to topic_ranking._ollama_chat_json — "
            "must use services.llm_text.ollama_chat_text (plain text) to "
            "avoid the JSON envelope leak pattern. See "
            "tests/unit/services/test_content_validator.py "
            "::TestJsonEnvelopeLeakDetection for the failure mode."
        )
    monkeypatch.setattr(
        "services.topic_ranking._ollama_chat_json", forbidden_json_helper,
    )

    drafts = iter([
        "First draft with [EXTERNAL_NEEDED: a fact] inside.",
        "Revised draft — clean prose, no JSON wrapper.",
    ])
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return next(drafts)
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )
    async def fake_revise(prompt, **kwargs):
        return next(drafts)
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)
    async def fake_research(query, max_sources=2, *, site_config=None):
        return "ok"
    monkeypatch.setattr(
        "services.research_service.research_topic",
        fake_research, raising=False,
    )
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )
    # Output is the plain-text revise result — no JSON wrapper, no
    # trailing brace.
    assert result["draft"] == "Revised draft — clean prose, no JSON wrapper."
    assert not result["draft"].rstrip().endswith("}")


async def test_draft_uses_plain_text_helper_not_json_helper(monkeypatch):
    """Pins poindexter#572: the first-draft path (``generate_with_context``)
    must NOT route through ``services.topic_ranking._ollama_chat_json``
    (which forces ``format=json`` on Ollama). Thinking models
    (glm-4.7-5090, qwen3) under ``response_format=json_object`` spend their
    whole token budget in the reasoning channel and return EMPTY
    ``content`` — which surfaced as canonical_blog "Content generation
    failed: no content produced" on every task post-#355. Mirrors
    ``test_revise_uses_plain_text_helper_not_json_helper`` for the draft.
    """
    import modules.content.ai_content_generator as acg

    async def forbidden_json_helper(prompt, **kwargs):
        raise AssertionError(
            "generate_with_context regressed back to "
            "topic_ranking._ollama_chat_json — the draft path must use "
            "services.llm_text.ollama_chat_text (plain text) so thinking "
            "models don't return empty content. See #572."
        )
    monkeypatch.setattr(
        "services.topic_ranking._ollama_chat_json", forbidden_json_helper,
    )

    called = {}
    async def fake_text(prompt, **kwargs):
        called["text"] = True
        return "# A clean draft\n\nProse body, no JSON envelope."
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_text)

    async def fake_resolve(*, site_config=None):
        return "glm-4.7-5090:latest"
    monkeypatch.setattr(
        "modules.content.ai_content_generator._resolve_rag_writer_model", fake_resolve,
    )
    monkeypatch.setattr(
        "modules.content.ai_content_generator.get_prompt_manager",
        lambda: MagicMock(get_prompt=MagicMock(return_value="PROMPT")),
    )

    out = await acg.generate_with_context(
        topic="t", angle="a", snippets=[],
        extra_instructions="write it", site_config=_fake_site_config(),
        pool=_fake_pool_with_no_snippets(),
    )
    assert called.get("text") is True
    assert out == "# A clean draft\n\nProse body, no JSON envelope."
    assert not out.rstrip().endswith("}")


# ---------------------------------------------------------------------------
# poindexter#574 — variant writer_model override fallback.
#
# A lab-harness experiment can assign a per-task ``writer_model`` override.
# If that model is unavailable/misconfigured, the revise call either RAISES
# (Ollama 404) or returns EMPTY content (reasoning model under some configs).
# Without a fallback, a single bad variant zeros every task it touches. The
# fix: fall back to the configured default writer + emit a loud canary, so
# a variant can never zero the pipeline (memory:
# feedback_writer_model_canary + feedback_no_silent_defaults).
# ---------------------------------------------------------------------------


def _wire_one_revise(monkeypatch):
    """Wire the embed + research + first-draft path so exactly one
    ``[EXTERNAL_NEEDED]`` marker fires a single revise pass, then the
    revised draft is clean (no further markers) so the loop terminates.

    Returns nothing — callers monkeypatch ``ollama_chat_text`` themselves
    to control the revise behavior under test.
    """
    drafts = iter([
        "First draft with [EXTERNAL_NEEDED: a fact] inside.",
    ])

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return next(drafts)

    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_research(query, max_sources=2, *, site_config=None):
        return f"External research result for: {query}"

    monkeypatch.setattr(
        "services.research_service.research_topic", fake_research, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768

    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)


async def test_bad_variant_override_raises_falls_back_to_default(monkeypatch):
    """A variant override whose model RAISES on the revise call must fall
    back to the configured default writer (not crash, not zero the
    pipeline) AND emit a finding canary. Pins poindexter#574."""
    _wire_one_revise(monkeypatch)

    calls: list[str] = []

    async def fake_revise(prompt, *, model=None, **kwargs):
        calls.append(model)
        if model == "bad-model:1b":
            raise RuntimeError("model 'bad-model:1b' not found, try pulling it")
        return "Revised draft via the default writer."

    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)

    findings: list[dict] = []

    def fake_emit(**kwargs):
        findings.append(kwargs)

    monkeypatch.setattr("utils.findings.emit_finding", fake_emit)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
        writer_model_override="bad-model:1b",
    )

    # The bad override was tried first, then the default writer.
    assert calls == ["bad-model:1b", "glm-4.7-5090:latest"]
    # Pipeline produced content from the default writer — NOT zeroed.
    assert result["draft"] == "Revised draft via the default writer."
    # Loud canary emitted with the right kind + the recovered/abandoned models.
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "variant_writer_model_fallback"
    assert f["severity"] == "warn"
    assert f["extra"]["bad_model"] == "bad-model:1b"
    assert f["extra"]["default_model"] == "glm-4.7-5090:latest"


async def test_bad_variant_override_empty_falls_back_to_default(monkeypatch):
    """A variant override whose model returns EMPTY content must also
    fall back to the default writer + emit the canary — the empty-output
    failure mode the issue describes (reasoning model burns its budget in
    the thinking channel and returns ''). Pins poindexter#574."""
    _wire_one_revise(monkeypatch)

    calls: list[str] = []

    async def fake_revise(prompt, *, model=None, **kwargs):
        calls.append(model)
        if model == "bad-model:1b":
            return ""  # empty content — the silent-zero failure mode
        return "Revised draft via the default writer."

    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)

    findings: list[dict] = []
    monkeypatch.setattr(
        "utils.findings.emit_finding", lambda **kw: findings.append(kw),
    )

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
        writer_model_override="bad-model:1b",
    )

    assert calls == ["bad-model:1b", "glm-4.7-5090:latest"]
    assert result["draft"] == "Revised draft via the default writer."
    assert len(findings) == 1
    assert findings[0]["kind"] == "variant_writer_model_fallback"
    assert "returned empty" in findings[0]["extra"]["reason"]


async def test_good_variant_override_used_as_is(monkeypatch):
    """A variant override whose model produces content is used as-is —
    NO fallback, NO finding. The happy A/B path must stay byte-equivalent
    so experiments actually exercise the variant model. Pins #574."""
    _wire_one_revise(monkeypatch)

    calls: list[str] = []

    async def fake_revise(prompt, *, model=None, **kwargs):
        calls.append(model)
        return "Revised draft via the variant model."

    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)

    findings: list[dict] = []
    monkeypatch.setattr(
        "utils.findings.emit_finding", lambda **kw: findings.append(kw),
    )

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
        writer_model_override="good-model:7b",
    )

    # Only the variant model was called — no fallback retry.
    assert calls == ["good-model:7b"]
    assert result["draft"] == "Revised draft via the variant model."
    # No canary — the variant worked, so nothing to flag.
    assert findings == []


async def test_no_override_single_call_no_fallback_machinery(monkeypatch):
    """The no-override production path is byte-identical to before #574:
    exactly one revise call against the resolved default, no finding."""
    _wire_one_revise(monkeypatch)

    calls: list[str] = []

    async def fake_revise(prompt, *, model=None, **kwargs):
        calls.append(model)
        return "Revised draft, default path."

    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)

    findings: list[dict] = []
    monkeypatch.setattr(
        "utils.findings.emit_finding", lambda **kw: findings.append(kw),
    )

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
        # No writer_model_override.
    )

    assert calls == ["glm-4.7-5090:latest"]
    assert result["draft"] == "Revised draft, default path."
    assert findings == []


# ---------------------------------------------------------------------------
# poindexter#691 — default-path empty-revise guard (root cause).
#
# A reasoning writer model (e.g. a GLM thinking model) can intermittently
# emit all its tokens into the thinking channel and return EMPTY content.
# Pre-#691 the DEFAULT revise path (no experiment variant) had no empty
# check — an empty revise silently OVERWROTE a good prior draft with '',
# which then flowed into QA as a misleading reviewer_count:0 0/100 reject.
# Fix: retry once with the same model (preserve writer quality — do NOT
# downgrade the article body), and if still empty keep the prior draft
# (markers stripped so the graph terminates) + emit a visibility finding.
# ---------------------------------------------------------------------------


async def test_empty_revise_retries_once_and_recovers(monkeypatch):
    """Default path: an intermittently-empty revise response is retried once
    with the same model; when the retry produces content it is used as-is and
    no finding fires (clean self-heal). Pins poindexter#691."""
    _wire_one_revise(monkeypatch)

    calls: list[str] = []

    async def fake_revise(prompt, *, model=None, **kwargs):
        calls.append(model)
        return "" if len(calls) == 1 else "Revised draft after a retry."

    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)

    findings: list[dict] = []
    monkeypatch.setattr(
        "utils.findings.emit_finding", lambda **kw: findings.append(kw),
    )

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )

    # First attempt empty → one retry against the SAME default model.
    assert calls == ["glm-4.7-5090:latest", "glm-4.7-5090:latest"]
    assert result["draft"] == "Revised draft after a retry."
    assert findings == []


async def test_empty_revise_keeps_prior_draft_when_retry_also_empty(monkeypatch):
    """Default path: when BOTH the revise call and its retry return empty, the
    writer must NOT zero the good prior draft. It keeps the pre-revision draft
    (unresolved [EXTERNAL_NEEDED] markers stripped so detect_needs terminates
    the loop instead of re-looping to the cap) and emits a visibility finding.
    This is the exact prod failure: an empty revise overwrote a non-empty
    draft with '' → empty content flowed into QA. Pins poindexter#691."""
    _wire_one_revise(monkeypatch)

    calls: list[str] = []

    async def fake_revise(prompt, *, model=None, **kwargs):
        calls.append(model)
        return ""  # both the call and its retry come back empty

    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)

    findings: list[dict] = []
    monkeypatch.setattr(
        "utils.findings.emit_finding", lambda **kw: findings.append(kw),
    )

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )

    # Retried once with the same default model, then fell back to the prior draft.
    assert calls == ["glm-4.7-5090:latest", "glm-4.7-5090:latest"]
    # Prior draft preserved (non-empty), with the unresolved marker stripped.
    assert result["draft"].strip() != ""
    assert "First draft with" in result["draft"]
    assert "EXTERNAL_NEEDED" not in result["draft"]
    # Self-heal is not silent (feedback_self_heal_not_suppress).
    assert len(findings) == 1
    assert findings[0]["kind"] == "writer_empty_draft_kept_prior"
    assert findings[0]["severity"] == "warn"


# ---------------------------------------------------------------------------
# poindexter#806 — degenerate-draft guards (defense in depth alongside #691).
#
# A writer model that burns its generation budget in a hidden reasoning
# channel can emit a near-empty, NON-blank response — e.g. a handful of
# ellipsis/dot characters. ``"...".strip()`` is truthy, so the #691
# empty-only checks let this shape through as a "successful" call. These pin
# the classifier plus the draft/revise retry-and-recover guards; the
# keep-best expansion entry guard (which stops the actual observed prod
# failure — an expand call adopting the dots as its topic) lives in its own
# section below the expansion tests.
# ---------------------------------------------------------------------------


def test_classify_draft_substance_empty_is_empty():
    assert two_pass._classify_draft_substance("", min_words=8) == "empty"
    assert two_pass._classify_draft_substance("   \n\t  ", min_words=8) == "empty"


def test_classify_draft_substance_dots_is_degenerate():
    assert two_pass._classify_draft_substance("... . .. ...", min_words=8) == "degenerate"


def test_classify_draft_substance_few_real_words_is_degenerate():
    assert two_pass._classify_draft_substance("Hi there.", min_words=8) == "degenerate"


def test_classify_draft_substance_real_prose_is_ok():
    text = "This is a perfectly normal opening sentence with real content."
    assert two_pass._classify_draft_substance(text, min_words=8) == "ok"


def test_classify_draft_substance_exactly_at_threshold_is_ok():
    text = " ".join(f"word{i}" for i in range(8))
    assert two_pass._classify_draft_substance(text, min_words=8) == "ok"


def test_classify_draft_substance_one_below_threshold_is_degenerate():
    text = " ".join(f"word{i}" for i in range(7))
    assert two_pass._classify_draft_substance(text, min_words=8) == "degenerate"


def test_resolve_min_substance_words_default_when_no_site_config():
    assert two_pass._resolve_min_substance_words(None) == 2


def test_resolve_min_substance_words_reads_setting():
    sc = MagicMock()
    sc.get_int = MagicMock(return_value=15)
    assert two_pass._resolve_min_substance_words(sc) == 15
    sc.get_int.assert_called_once_with("writer_min_substance_words", 2)


async def test_degenerate_first_draft_retries_once_and_recovers(monkeypatch):
    """A first draft of bare dots/punctuation is retried once; a substantial
    retry result is used as-is (clean self-heal, no finding)."""
    calls: list[str] = []
    drafts = iter([
        "... . .. ...",  # degenerate: no real words
        "A real first draft with actual substance in it.",
    ])

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        calls.append("draft")
        return next(drafts)
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    findings: list[dict] = []
    monkeypatch.setattr(
        "utils.findings.emit_finding", lambda **kw: findings.append(kw),
    )

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )
    assert calls == ["draft", "draft"]
    assert result["draft"] == "A real first draft with actual substance in it."
    assert findings == []


async def test_degenerate_first_draft_kept_when_retry_also_degenerate(monkeypatch):
    """When BOTH the first draft and its retry are degenerate, there is no
    prior draft to fall back to — the writer keeps the degenerate output
    (never fabricates content) and emits a visibility finding so the
    upstream producer stays visible."""
    calls: list[str] = []

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        calls.append("draft")
        return "..."
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    findings: list[dict] = []
    monkeypatch.setattr(
        "utils.findings.emit_finding", lambda **kw: findings.append(kw),
    )

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )
    assert calls == ["draft", "draft"]
    assert result["draft"] == "..."
    assert len(findings) == 1
    assert findings[0]["kind"] == "writer_degenerate_draft_kept"
    assert findings[0]["severity"] == "warn"


async def test_degenerate_revise_retries_once_and_recovers(monkeypatch):
    """Default path: a non-empty-but-degenerate revise response ('...') is
    retried once with the same model; a substantial retry result is used
    as-is (clean self-heal, no finding). Extends #691 (which only caught
    truly-empty output) to the poindexter#806 dots/punctuation shape."""
    _wire_one_revise(monkeypatch)

    calls: list[str] = []

    async def fake_revise(prompt, *, model=None, **kwargs):
        calls.append(model)
        return "... . .." if len(calls) == 1 else "Revised draft after a retry."

    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)

    findings: list[dict] = []
    monkeypatch.setattr(
        "utils.findings.emit_finding", lambda **kw: findings.append(kw),
    )

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )

    assert calls == ["glm-4.7-5090:latest", "glm-4.7-5090:latest"]
    assert result["draft"] == "Revised draft after a retry."
    assert findings == []


async def test_degenerate_revise_keeps_prior_draft_when_retry_also_degenerate(monkeypatch):
    """Default path: when BOTH the revise call and its retry return
    non-empty-but-degenerate output ('...'), the writer must NOT adopt it
    over the good prior draft — same #691 keep-prior recovery, distinct
    finding kind so the empty vs degenerate failure shapes stay
    distinguishable on the Findings dashboard. Pins poindexter#806."""
    _wire_one_revise(monkeypatch)

    calls: list[str] = []

    async def fake_revise(prompt, *, model=None, **kwargs):
        calls.append(model)
        return ". .. ..."  # both the call and its retry come back degenerate

    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_revise)

    findings: list[dict] = []
    monkeypatch.setattr(
        "utils.findings.emit_finding", lambda **kw: findings.append(kw),
    )

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
    )

    assert calls == ["glm-4.7-5090:latest", "glm-4.7-5090:latest"]
    assert "First draft with" in result["draft"]
    assert "EXTERNAL_NEEDED" not in result["draft"]
    assert len(findings) == 1
    assert findings[0]["kind"] == "writer_degenerate_draft_kept_prior"
    assert findings[0]["severity"] == "warn"


# ---------------------------------------------------------------------------
# Length target threading (length-uniformity bug).
#
# The niche writer never received the task's ``target_length``: the picked
# value dead-ended at GenerateContentStage, so every glad-labs post came out
# at the model's natural ~600-word default regardless of whether the task
# asked for 600 or 2500. These pin that ``target_length`` reaches the draft
# call (and the prompt) so the writer can actually honour the requested
# length.
# ---------------------------------------------------------------------------


async def test_target_length_threaded_into_draft_call(monkeypatch):
    """``target_length`` passed to run() must reach the draft call
    (``generate_with_context``) so the writer is told the requested length."""
    captured: dict = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **kw):
        captured["target_length"] = kw.get("target_length")
        return "A clean first draft with no markers."
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_fake_site_config(),
        target_length=2500,
    )
    assert captured["target_length"] == 2500


async def test_generate_with_context_forwards_target_length_to_prompt(monkeypatch):
    """``generate_with_context`` must forward ``target_length`` into the
    prompt render so the SKILL.md ``{target_length}`` placeholder receives
    the requested word budget."""
    import modules.content.ai_content_generator as acg

    seen: dict = {}

    def fake_get_prompt(key, **kwargs):
        seen.update(kwargs)
        return "PROMPT"
    monkeypatch.setattr(
        "modules.content.ai_content_generator.get_prompt_manager",
        lambda: MagicMock(get_prompt=MagicMock(side_effect=fake_get_prompt)),
    )

    async def fake_resolve(*, site_config=None):
        return "glm-4.7-5090:latest"
    monkeypatch.setattr(
        "modules.content.ai_content_generator._resolve_rag_writer_model",
        fake_resolve,
    )

    async def fake_text(prompt, **kwargs):
        return "draft body"
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_text)

    await acg.generate_with_context(
        topic="t", angle="a", snippets=[],
        extra_instructions="write it", site_config=_fake_site_config(),
        pool=_fake_pool_with_no_snippets(), target_length=2500,
    )
    assert seen.get("target_length") == 2500


# ---------------------------------------------------------------------------
# Keep-best soft expansion pass (Cause B enforcement).
#
# Local writer models under-deliver on long targets even when the prompt asks
# for ~N words. After the graph completes, run()'s expansion guard does ONE
# expansion pass when the draft lands under target_length * writer_min_length_ratio
# (default 0.7), keeping whichever of (original, expanded) is longer — a thin
# expansion or an empty model response can never shrink or zero the post.
# ---------------------------------------------------------------------------


def _short_draft_site_config(**overrides):
    """SiteConfig stub with the writer model pinned + expansion defaults,
    overridable per-test (e.g. to flip ``writer_length_expansion_enabled``)."""
    settings = {
        "pipeline_writer_model": "glm-4.7-5090:latest",
        "cost_tier.standard.model": "",
        "writer_length_expansion_enabled": "true",
    }
    settings.update(overrides)
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda key, default="": settings.get(key, default))
    sc.get_int = MagicMock(side_effect=lambda key, default=0: default)
    sc.get_float = MagicMock(side_effect=lambda key, default=0.0: default)
    return sc


async def test_short_draft_triggers_expansion_pass(monkeypatch):
    """A draft well below target_length * ratio gets one expansion pass, and
    the longer expanded text replaces the thin first draft."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **kw):
        return "Tiny draft."  # 2 words — far below 0.7 * 2500 = 1750
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    expand_calls: list[str] = []

    async def fake_expand(prompt, **kwargs):
        expand_calls.append(kwargs.get("phase"))
        return "word " * 2000  # a long, expanded draft
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_expand)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_short_draft_site_config(),
        target_length=2500,
    )
    assert "two_pass_expand" in expand_calls
    assert len(result["draft"].split()) > 1000
    assert result["length_expanded"] is True


async def test_expansion_shorter_keeps_original_draft(monkeypatch):
    """Keep-best: when the expansion pass returns something SHORTER than the
    original, the original draft is kept — expansion can never shrink the post.
    """
    original = "word " * 200  # 200 words, below threshold so expansion fires

    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **kw):
        return original
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    async def fake_expand(prompt, **kwargs):
        return "too short"  # 2 words — must NOT replace the 200-word draft
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_expand)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_short_draft_site_config(),
        target_length=2500,
    )
    assert result["draft"].strip() == original.strip()
    assert result["length_expanded"] is False


async def test_no_expansion_when_draft_meets_target(monkeypatch):
    """A draft already at/above target_length * ratio is returned unchanged —
    the expansion model is never called."""
    draft = "word " * 1800  # 1800 >= 0.7 * 2500 = 1750 → no expansion

    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **kw):
        return draft
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    called: list[int] = []

    async def fake_expand(prompt, **kwargs):
        called.append(1)
        return "x"
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_expand)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_short_draft_site_config(),
        target_length=2500,
    )
    assert called == []
    assert result["draft"].strip() == draft.strip()
    assert result["length_expanded"] is False


async def test_expansion_disabled_via_setting(monkeypatch):
    """``writer_length_expansion_enabled=false`` skips the expansion pass even
    when the draft is short (DB-configurable master switch)."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **kw):
        return "tiny"  # 1 word
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    called: list[int] = []

    async def fake_expand(prompt, **kwargs):
        called.append(1)
        return "word " * 2000
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_expand)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_short_draft_site_config(
            writer_length_expansion_enabled="false",
        ),
        target_length=2500,
    )
    assert called == []
    assert result["draft"].strip() == "tiny"
    assert result["length_expanded"] is False


# ---------------------------------------------------------------------------
# poindexter#806 — degenerate-draft guard at the expand entry.
#
# This is the guard that stops the actual observed prod failure: a
# near-empty draft ('...') reached the keep-best expansion pass, and because
# the expand prompt (_EXPAND_PROMPT_FALLBACK) includes ONLY the draft text —
# never the topic/angle — the expand model had nothing to work with but the
# dots and invented a whole ellipsis-themed article as their "expansion",
# which then won keep-best (trivially longer than a 3-character original)
# and got persisted as the post (tasks 9921678f / e46b449c / ecaf0c01).
# ---------------------------------------------------------------------------


async def test_degenerate_draft_skips_expansion_pass(monkeypatch):
    """The keep-best expansion entry must refuse to hand a degenerate draft
    to the expand LLM. The draft is left unchanged and the expand model is
    never called."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **kw):
        return "..."  # degenerate — far under any length threshold
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    expand_calls: list[str] = []

    async def fake_expand(prompt, **kwargs):
        expand_calls.append(kwargs.get("phase"))
        return "word " * 2000  # would win keep-best if it were ever called
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_expand)

    findings: list[dict] = []
    monkeypatch.setattr(
        "utils.findings.emit_finding", lambda **kw: findings.append(kw),
    )

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_short_draft_site_config(),
        target_length=2500,
    )
    assert expand_calls == []
    assert result["draft"] == "..."
    assert result["length_expanded"] is False
    assert result["degenerate_draft_input"] is True
    # fake_pass1 unconditionally returns "..." so _draft_node's own
    # retry-exhausted guard also fires (its own dedicated tests cover that
    # in isolation) — assert the expand-entry finding is present rather
    # than assuming it's the only one.
    expand_findings = [f for f in findings if f["kind"] == "writer_degenerate_draft_input"]
    assert len(expand_findings) == 1
    assert expand_findings[0]["severity"] == "warn"


async def test_substantial_short_draft_still_triggers_expansion(monkeypatch):
    """A short-but-substantial draft (real words, just thin) must still
    trigger the normal expansion pass — the degenerate guard must not
    over-fire on legitimately thin (but real) content."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **kw):
        return "A short draft covering the topic in brief but real terms."
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    expand_calls: list[str] = []

    async def fake_expand(prompt, **kwargs):
        expand_calls.append(kwargs.get("phase"))
        return "word " * 2000
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_expand)

    result = await two_pass.run(
        topic="t", angle="a", niche_id="n",
        pool=_fake_pool_with_no_snippets(),
        site_config=_short_draft_site_config(),
        target_length=2500,
    )
    assert "two_pass_expand" in expand_calls
    assert result["length_expanded"] is True
    assert result["degenerate_draft_input"] is False


# ---------------------------------------------------------------------------
# RAG snippet source scoping (corpus-pollution guard).
#
# _embed_and_fetch_snippets used to query the WHOLE embeddings table — 67% of
# which is claude_sessions / brain / audit ops-logs — with no source filter,
# so a session transcript ranking near the topic vector could be reproduced
# wholesale into a draft. It now honours the ``rag_source_filter`` app_setting
# (default 'posts') and NEVER queries unfiltered: an empty/unset value falls
# back to the content allowlist rather than "all tables".
# memory: project_rag_corpus_pollution.
# ---------------------------------------------------------------------------


def _recording_pool():
    """Fake pool whose conn.fetch records its (sql, *params) call args."""
    pool = MagicMock()
    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(return_value=[])
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn_mock


def test_resolve_snippet_source_filter_defaults_to_posts_when_empty():
    sc = MagicMock()
    sc.get = MagicMock(return_value="")  # rag_source_filter unset/empty
    assert two_pass._resolve_snippet_source_filter(sc) == ["posts"]


def test_resolve_snippet_source_filter_none_site_config():
    assert two_pass._resolve_snippet_source_filter(None) == ["posts"]


def test_resolve_snippet_source_filter_honors_csv():
    sc = MagicMock()
    sc.get = MagicMock(return_value=" posts , samples ")
    assert two_pass._resolve_snippet_source_filter(sc) == ["posts", "samples"]


async def test_embed_and_fetch_applies_source_filter(monkeypatch):
    """The snippet query must constrain source_table to the resolved content
    allowlist — never scan the whole embeddings table."""
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return "Clean draft, no markers."
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    pool, conn_mock = _recording_pool()
    await two_pass.run(
        topic="Some Topic", angle="technical | professional",
        niche_id="n", pool=pool, site_config=_fake_site_config(),
    )

    assert conn_mock.fetch.await_count == 1
    sql, *params = conn_mock.fetch.await_args.args
    assert "source_table = ANY(" in sql
    # The resolved content allowlist is passed as a query param (default ['posts']).
    assert ["posts"] in params


# ---------------------------------------------------------------------------
# Prompt-echo guard — gemma-4-31B-it-qat preamble regurgitation.
#
# The 4-bit QAT writer intermittently dumps the prompt preamble (topic line,
# angle, niche writer_prompt_override, the revise/expand instructions, the
# citation rules, and its own planning notes) as the OPENING of the article
# instead of executing it. Captured 2026-06-29: stored canonical_blog drafts
# opened with the topic, then "Technical/Professional.", then the niche
# descriptor, then "Expand from ~57 words to closer to 1500 words. Add genuine
# substance...". The real body sat underneath. _strip_echoed_preamble removes a
# contiguous echoed preamble (no LLM call), gated on a high-precision identity
# echo so it is a no-op on clean drafts, and never zeroes a draft.
# ---------------------------------------------------------------------------

_ECHO_TOPIC = "Knowledge Distillation of Black-Box Large Language Models (2024)"
_ECHO_ANGLE = "technical | professional"
_ECHO_OVERRIDE = (
    "You are writing a blog post for Glad Labs — an AI-operated content "
    "business covering AI/ML, gaming, and PC hardware for indie developers "
    "and tinkerers."
)

_REAL_BODY = (
    "## Why distillation matters\n\n"
    "Black-box distillation lets a smaller local model inherit the behaviour of "
    "a frontier API model without ever touching its weights. We walk through the "
    "trade-offs below, with worked examples that run on a single consumer GPU and "
    "never leave the machine.\n\n"
    "## The Proxy-KD approach\n\n"
    "The core idea is to train a proxy that mimics the teacher's outputs, then "
    "distil from the proxy. This sidesteps the rate limits and per-token cost of "
    "querying the teacher directly for every training example, which matters a "
    "great deal when you are running on your own silicon."
)

# Mirrors the real ba4d627a contamination: topic / angle / brand echo, the
# expand instruction, citation-rule bullets, Title:/Context: labels, and the
# model's own planning prose.
_ECHO_PREAMBLE = (
    "Knowledge Distillation of Black-Box Large Language Models (2024).\n"
    "Technical/Professional.\n"
    "Glad Labs (AI/ML, gaming, PC hardware for indie devs/tinkerer).\n"
    "Expand from ~57 words to closer to 1500 words.\n"
    "Add genuine substance, concrete details, worked examples, and reasoning. No "
    'padding, filler, or repetition. Return complete Markdown. First person ("we").\n'
    "\n"
    "    *   Full markdown links for citations.\n"
    "    *   No fake URLs/placeholders.\n"
    "    *   Internal consistency.\n"
    "\n"
    "    *   Title: Knowledge Distillation of Black-Box Large Language Models (2024).\n"
    "    *   Context: Glad Labs.\n"
    "    *   Current content is essentially just the metadata and title. I need to "
    "create the substance based on this topic.\n"
)


def test_strip_echoed_preamble_removes_scaffolding_keeps_body():
    contaminated = _ECHO_PREAMBLE + "\n" + _REAL_BODY
    clean, n = two_pass._strip_echoed_preamble(
        contaminated, topic=_ECHO_TOPIC, angle=_ECHO_ANGLE,
        writer_prompt_override=_ECHO_OVERRIDE,
    )
    assert n >= 5
    assert clean.startswith("## Why distillation matters")
    assert "Expand from ~57 words" not in clean
    assert "Current content is essentially" not in clean
    assert "Glad Labs (AI/ML, gaming" not in clean
    assert "Technical/Professional" not in clean
    # The real body survives intact.
    assert "Proxy-KD approach" in clean
    assert "single consumer GPU" in clean


def test_strip_echoed_preamble_noop_on_clean_draft():
    clean, n = two_pass._strip_echoed_preamble(
        _REAL_BODY, topic=_ECHO_TOPIC, angle=_ECHO_ANGLE,
        writer_prompt_override=_ECHO_OVERRIDE,
    )
    assert n == 0
    assert clean == _REAL_BODY


def test_strip_echoed_preamble_preserves_real_heading_that_mentions_topic():
    """A genuine section heading that references the topic must NOT be stripped
    (only an exact topic restatement is). Guards against eating real content."""
    draft = (
        "## Knowledge distillation explained\n\n"
        "Here is the substantive body that actually delivers on the topic with "
        "concrete detail and a worked example on local hardware, well over the "
        "fifty word floor the guard requires before it will ever truncate."
    )
    clean, n = two_pass._strip_echoed_preamble(
        draft, topic=_ECHO_TOPIC, angle=_ECHO_ANGLE,
        writer_prompt_override=_ECHO_OVERRIDE,
    )
    assert n == 0
    assert clean == draft


def test_strip_echoed_preamble_never_zeroes_all_echo_draft():
    """A draft that is ENTIRELY echo (no real body underneath) is returned
    unchanged — the guard never truncates a draft to nothing; the contamination
    becomes a human-review signal (the finding) instead of a silent empty post."""
    clean, n = two_pass._strip_echoed_preamble(
        _ECHO_PREAMBLE, topic=_ECHO_TOPIC, angle=_ECHO_ANGLE,
        writer_prompt_override=_ECHO_OVERRIDE,
    )
    assert clean == _ECHO_PREAMBLE
    assert n == 0


async def test_run_strips_prompt_echo_from_final_draft(monkeypatch):
    """End-to-end: a draft that opens by echoing topic/angle/brand is cleaned in
    run()'s returned draft, and the stripped-line count is surfaced for metrics."""
    contaminated = _ECHO_PREAMBLE + "\n" + _REAL_BODY

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return contaminated
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    findings: list[dict] = []
    monkeypatch.setattr("utils.findings.emit_finding", lambda **kw: findings.append(kw))

    result = await two_pass.run(
        topic=_ECHO_TOPIC, angle=_ECHO_ANGLE, niche_id="glad-labs",
        pool=_fake_pool_with_no_snippets(), site_config=_fake_site_config(),
        writer_prompt_override=_ECHO_OVERRIDE,
    )
    assert result["draft"].startswith("## Why distillation matters")
    assert "Expand from ~57 words" not in result["draft"]
    assert result["prompt_echo_stripped"] >= 5
    # Self-heal is not silent: a visibility finding fires (feedback_self_heal_not_suppress).
    assert len(findings) == 1
    assert findings[0]["kind"] == "writer_prompt_echo_stripped"
    assert findings[0]["severity"] == "warn"


async def test_run_no_echo_leaves_clean_draft_untouched(monkeypatch):
    """A clean draft passes through run() unchanged and fires no echo finding."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return _REAL_BODY
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    findings: list[dict] = []
    monkeypatch.setattr("utils.findings.emit_finding", lambda **kw: findings.append(kw))

    result = await two_pass.run(
        topic=_ECHO_TOPIC, angle=_ECHO_ANGLE, niche_id="glad-labs",
        pool=_fake_pool_with_no_snippets(), site_config=_fake_site_config(),
        writer_prompt_override=_ECHO_OVERRIDE,
    )
    assert result["draft"] == _REAL_BODY
    assert result["prompt_echo_stripped"] == 0
    assert findings == []


# ---------------------------------------------------------------------------
# Prompt-echo guard — paraphrased INSTRUCTION echo (2026-07-01 regression).
#
# Tasks e46b449c + 9921678f leaked the expand-pass instructions as the article
# opening even with the #2016 guard live: the model PARAPHRASED the prompt
# ("Expand a draft from ~416 words ... to closer to 651 words") and echoed no
# topic/angle/brand lines at all, so the identity-only signature gate never
# opened. These fixtures are the verbatim openings from pipeline_versions.
# ---------------------------------------------------------------------------

# Task e46b449c (glad-labs canonical_blog, 2026-07-01): pure instruction
# paraphrase — zero identity lines, then the model's planning bullets.
_E46_INSTRUCTION_ECHO = (
    'Expand a draft from ~416 words (actually the provided "Draft" is more '
    "like 250-300 words of content, though the prompt says it's about 416) "
    "to closer to 651 words.\n"
    "Genuine added substance, concrete details, worked examples, and "
    "reasoning grounded in existing content.\n"
    "Do NOT pad, repeat, restate points, or add filler. If a section is "
    "complete, leave it alone. Preserve all facts, links, headings, and "
    "original voice. No preamble/notes. Markdown format.\n"
    "\n"
    "    *   *Intro:* Definition, etymology (*élleipsis*), purpose "
    "(omission/pause). Links: Wikipedia.\n"
    "    *   *Usage:* Omitting words from quotes to save space, increase "
    "relevance, avoid distraction. Link: GrammarBook.com.\n"
    "\n"
    "An **ellipsis** (plural: ellipses) is a punctuation mark consisting of "
    "three dots. Derived from the Ancient Greek word *élleipsis*, meaning "
    '"leave out," it is primarily used to indicate the omission of text or a '
    "pause in speech. While often used interchangeably with three consecutive "
    "periods, a formal ellipsis is frequently treated as a single typographic "
    "character to ensure consistent spacing and kerning across renderers.\n"
)

# Task 9921678f (2026-06-30): instruction paraphrase + ONE brand echo line +
# instruction bullets. The old gate saw only one identity category.
_9921_INSTRUCTION_ECHO = (
    "Expand a draft (which was provided as a set of dots/ellipses, but the "
    '*actual* content is found in the "Background Context" and "Internal '
    'Snippets") to approximately 1954 words.\n'
    "System alerts regarding `operator_paged [warning]`, specifically "
    "`Operator surface unreachable` errors.\n"
    "Glad Labs (AI-operated content business for AI/ML, gaming, PC hardware). "
    "Technical/Professional voice.\n"
    "\n"
    "        *   Preserve existing facts, links, headings, and original voice.\n"
    "        *   Expand with genuine substance, concrete detail, worked "
    "examples, and reasoning.\n"
    "        *   NO padding, repeating, or filler.\n"
    "        *   No preamble/notes in the output.\n"
    "        *   Markdown format.\n"
    "        *   End on a complete sentence.\n"
    "\n"
    "When a monitoring surface goes quiet, the silence itself is the signal. "
    "Our alerting stack pages the operator the moment a telemetry endpoint "
    "stops answering, and the diagnosis usually comes down to a handful of "
    "failure classes that are worth walking through in detail here.\n"
)


def test_strip_pure_instruction_echo_without_identity_lines():
    """e46b449c regression: instruction-paraphrase opening with NO topic/angle/
    brand echo must still open the gate (>=2 instruction lines) and strip."""
    clean, n = two_pass._strip_echoed_preamble(
        _E46_INSTRUCTION_ECHO,
        topic="Automating Content Automation",
        angle="technical | professional",
        writer_prompt_override="",
    )
    assert n >= 3, f"expected the 3 instruction lines stripped, got {n}"
    assert "Expand a draft" not in clean
    assert "Genuine added substance" not in clean
    assert "Do NOT pad" not in clean
    # The body underneath survives.
    assert "An **ellipsis**" in clean


def test_strip_instruction_echo_with_single_brand_line():
    """9921678f regression: one instruction line + one brand line is enough
    signature (instruction + identity) — the leading instruction is stripped."""
    clean, n = two_pass._strip_echoed_preamble(
        _9921_INSTRUCTION_ECHO,
        topic=". .. . ... . .... . .... . ... .",
        angle="technical | professional",
        writer_prompt_override=(
            "Glad Labs — AI-operated content business for AI/ML, gaming, and "
            "PC hardware. Technical/Professional voice."
        ),
    )
    assert n >= 1
    assert not clean.startswith("Expand a draft")
    assert "to approximately 1954 words" not in clean.splitlines()[0]


def test_single_instruction_like_line_in_clean_article_is_noop():
    """False-positive guard: ONE instruction-shaped line in real prose (an
    article can open with an imperative) must NOT trip the gate."""
    draft = (
        "Do not pad your Docker images with build tools you never ship.\n"
        "\n"
        "Slim images pull faster, cold-start faster, and expose less attack "
        "surface. The route there is a multi-stage build: compile in one "
        "stage, copy only the artifacts into a distroless runtime stage, and "
        "let the builder cache absorb the heavy dependency layers so CI stays "
        "fast while production images stay lean and reproducible."
    )
    clean, n = two_pass._strip_echoed_preamble(
        draft, topic="Slim Docker images", angle="technical | professional",
        writer_prompt_override="",
    )
    assert n == 0
    assert clean == draft


def test_one_instruction_plus_topic_restatement_triggers():
    """Mixed signature: an instruction line + an exact topic restatement is
    two independent echo signals — the gate opens and both lines strip."""
    draft = (
        "Expand the draft to roughly 1200 words with concrete examples.\n"
        "Quantizing LLMs for consumer GPUs\n"
        "\n"
        "Quantization trades a little accuracy for a lot of memory headroom. "
        "On a 16 GB card the difference between FP16 and 4-bit weights is the "
        "difference between refusing to load and running with room to spare, "
        "and the calibration choices you make decide how much quality you "
        "give back in exchange for that fit."
    )
    clean, n = two_pass._strip_echoed_preamble(
        draft, topic="Quantizing LLMs for consumer GPUs",
        angle="technical | professional", writer_prompt_override="",
    )
    assert n == 2
    assert clean.startswith("Quantization trades")


# ---------------------------------------------------------------------------
# Planning-dump strip — structural bullet-dump preamble (#2036 follow-up).
#
# A dump with ZERO instruction/identity lines (task e46b449c, 2026-07-01:
# "*   Topic: Ellipses...", "*   Source Material provided:") slips past
# _has_echo_signature entirely, reaches qa.programmatic, and dies on the
# validator's planning_dump hard gate — a whole pipeline run (draft + images
# + every QA rail) wasted when the real article sits right under the dump.
# _strip_planning_dump_preamble self-heals at the writer instead: gated on
# the validator's own detect_planning_dump_preamble (one source of truth —
# the strip fires exactly when the validator would reject), it removes the
# opening through the LAST bullet of the pre-heading opening, splits a fused
# last bullet at the glued sentence boundary ("...crime novel).An
# **ellipsis**..."), and keeps the original whenever <50 words would survive.
# ---------------------------------------------------------------------------

# Verbatim-trimmed dump bullets from task e46b449c (same incident content as
# the validator fixture in test_content_validator.py). Ends mid-line: the
# article's intro sentence is FUSED onto the last bullet with no whitespace.
_E46_DUMP_BULLETS = """*   Topic: Ellipses and triple-dot punctuation marks.
    *   Source Material provided:
        *   Unicode categories ("Other Punctuation").
        *   Wikipedia (Ellipsis).
        *   Compart (Unicode characters).
        *   Graphemica (Arabic triple dot).
        *   (Irrelevant source: "Un doppio sospetto" book description).

    *   Definition of an ellipsis.
    *   Unicode characters associated with triple dots/ellipses.
    *   Usage in writing (omissions, pauses, etc.).
    *   Etymology and terminology.

    *   Ensure inline links use `[Tool Name](url)`.
    *   Attribute facts clearly (e.g., "According to...").
    *   Discard irrelevant data (the crime novel)."""

_E46_FUSED_INTRO = (
    "An **ellipsis** (plural: ellipses) is a punctuation mark consisting of "
    "three dots used primarily to indicate the omission of words, phrases, "
    "or paragraphs from a quoted passage."
)

# Article continuation — extended past the incident trim so the surviving
# body clears the 50-word survival floor (the real prod row carried a full
# article; the validator fixture was trimmed for detection-only tests).
_E46_ARTICLE_REST = (
    "\n\n### Etymology and Terminology\n"
    "The term originates from the Ancient Greek word *elleipsis*, which "
    "literally means \"leave out.\" Writers reach for it when a quotation "
    "needs trimming, when a sentence trails off deliberately, or when a "
    "pause carries more weight than any word could. Unicode encodes the "
    "horizontal ellipsis at U+2026, and typography guides still argue about "
    "the spacing around it."
)

_E46_FUSED_DUMP = _E46_DUMP_BULLETS + _E46_FUSED_INTRO + _E46_ARTICLE_REST


def test_strip_planning_dump_removes_bullets_keeps_fused_intro():
    """The e46b449c incident shape: outline bullets, article intro fused onto
    the last bullet, heading + body below. The dump goes; the fused intro
    paragraph is split out of the last bullet and SURVIVES."""
    clean, n = two_pass._strip_planning_dump_preamble(_E46_FUSED_DUMP)
    assert clean.startswith("An **ellipsis**")
    assert "*   Topic:" not in clean
    assert "Source Material provided" not in clean
    assert "Discard irrelevant data" not in clean
    assert "### Etymology and Terminology" in clean
    assert "Ancient Greek" in clean
    assert n >= 10


def test_strip_planning_dump_keeps_unfused_intro_between_bullets_and_heading():
    """When the article's intro is its own paragraph between the last bullet
    and the first heading, it survives — this is exactly the case a naive
    strip-to-first-heading (the normalize-atom approach) would amputate."""
    draft = (
        _E46_DUMP_BULLETS + "\n\n" + _E46_FUSED_INTRO + _E46_ARTICLE_REST
    )
    clean, n = two_pass._strip_planning_dump_preamble(draft)
    assert clean.startswith("An **ellipsis**")
    assert "Discard irrelevant data" not in clean
    assert "### Etymology and Terminology" in clean
    assert n >= 10


def test_strip_planning_dump_keeps_generated_h1_title():
    """A dump sitting below the generated H1 title: the title is kept, the
    dump under it goes (mirror of the detector's single-leading-heading skip)."""
    draft = "# Decoding the Dots\n\n" + _E46_FUSED_DUMP
    clean, n = two_pass._strip_planning_dump_preamble(draft)
    assert clean.startswith("# Decoding the Dots")
    assert "An **ellipsis**" in clean
    assert "*   Topic:" not in clean
    assert n >= 10


def test_strip_planning_dump_ecaf_section_plan_outline():
    """The ecaf0c01 shape: section-by-section plan labels + notes-to-self,
    with the article opening on its own H1 right after the dump."""
    draft = (
        "*   *Introduction:* Define hallucination using Wikipedia/IBM.\n"
        "    *   Connect to technical writing via the content problem.\n"
        "    *   Establish the stakes: precision is non-negotiable.\n"
        "*   *The Operational Bottleneck:* Discuss the current loop.\n"
        "    *   I should provide specific before-and-after quote "
        "examples to demonstrate the workflow.\n"
        "*   *Conclusion:* Summarize the path to enterprise-readiness.\n"
        "    *   Check word count (aiming for ~1057).\n"
        "    *   Ensure all links are preserved.\n\n"
        "# Addressing Hallucinations in Open-Source LLM Agents\n\n"
        "An AI hallucination occurs when a large language model generates "
        "fabricated information presented as fact. For technical writers "
        "the stakes are concrete: a fabricated flag in a runbook or an "
        "invented API parameter in a reference page erodes exactly the "
        "trust the documentation exists to build, and the reader who "
        "catches one invented detail stops believing the accurate ones "
        "around it too.\n"
    )
    clean, n = two_pass._strip_planning_dump_preamble(draft)
    assert clean.startswith("# Addressing Hallucinations")
    assert "*Introduction:*" not in clean
    assert "Check word count" not in clean
    assert "fabricated information presented as fact" in clean
    assert n >= 8


def test_strip_planning_dump_task_receipt_dialect():
    """The e46b449c REGENERATION (2026-07-01, post-#2036): gemma switched to a
    task-receipt planning dialect ("User wants...", "I have several provided
    sources", "irrelevant snippets") that left only one original vocabulary
    family matched — detector silent, so validator AND strip both missed it.
    With the task-receipt family + source-triage widening the strip heals it:
    dump gone, fused intro kept, real section content intact."""
    draft = (
        '*   User wants a comprehensive explanation of an "ellipsis."\n'
        "    *   I have several provided sources: Wikipedia (Ellipsis and "
        "Full stop), GrammarBook.com, The Editor's Manual, and some "
        "irrelevant snippets (about a crime novel, description logic, "
        "Salton Sea, etc.).\n\n"
        "    *   Definition: A punctuation mark consisting of three dots (...).\n"
        '    *   Origin: Ancient Greek *élleipsis*, meaning "leave out" '
        "[Ellipsis - Wikipedia](https://en.wikipedia.org/wiki/Ellipsis).\n"
        "    *   Usage in Quotations: Used to omit words, phrases, lines, "
        "or paragraphs from a quoted passage.\n"
        "    *   Usage in Dialogue/Narrative: Indicates faltering speech, "
        "a pause, or an incomplete thought.\n"
        "    *   Formatting: May be separated by spaces (. . .) or not "
        "(...), depending on the style guide (e.g., AP, Chicago).\n"
        "    *   Distinction: Different from a full stop (period).\n\n"
        "    *   Introduction (Definition and Origin).\n"
        "    *   Primary Uses (Quotations vs. Dialogue/Narrative).\n"
        "    *   Formatting Styles.\n"
        "    *   Distinction from other marks.An **ellipsis** (plural: "
        "**ellipses**) is a punctuation mark consisting of three dots (...). "
        "The term originates from the Ancient Greek word *élleipsis*, which "
        'literally means "leave out."\n\n'
        "Depending on the context, an ellipsis serves different purposes:\n\n"
        "### 1. Use in Quotations\n"
        "In formal writing and quotations, an ellipsis is used to indicate "
        "that words, phrases, lines, or entire paragraphs have been omitted "
        "from the original text. This allows a writer to save space, remove "
        "material that is less relevant, and get straight to the main idea "
        "without distraction.\n"
    )
    clean, n = two_pass._strip_planning_dump_preamble(draft)
    assert clean.startswith("An **ellipsis** (plural: **ellipses**)")
    assert "User wants" not in clean
    assert "provided sources" not in clean
    assert "Depending on the context" in clean
    assert "### 1. Use in Quotations" in clean
    assert n >= 10


def test_strip_planning_dump_eats_narration_tail_and_reanchors_glued_heading():
    """ba4d627a residue (2026-06-29 audit): a SHORT model-narration line
    between the last bullet and a heading the writer glued onto it is junk,
    not an intro — it goes too, and the glued heading is re-anchored to line
    start so it renders. A substantial (>=10 word) tail in the same position
    is a real intro and survives (previous test)."""
    draft = (
        _E46_DUMP_BULLETS + "\n\n"
        "(Proceeding to generate based on these steps).## Decoding the Dots\n\n"
        "The three little dots at the end of a trailing thought carry more "
        "typographic history than almost any other mark on the keyboard, and "
        "they are abused in modern writing more than any other mark too. This "
        "piece walks through where the ellipsis came from, what Unicode "
        "actually encodes for it, and the small set of rules that keep it "
        "meaningful instead of mumbling.\n"
    )
    clean, n = two_pass._strip_planning_dump_preamble(draft)
    assert clean.startswith("## Decoding the Dots")
    assert "Proceeding to generate" not in clean
    assert "typographic history" in clean
    assert n >= 10


def test_strip_planning_dump_noop_on_clean_draft():
    clean, n = two_pass._strip_planning_dump_preamble(_REAL_BODY)
    assert n == 0
    assert clean == _REAL_BODY


def test_strip_planning_dump_noop_on_legit_opening_list():
    """Structure without planning vocabulary — a legitimate opening list —
    must not strip (mirrors the detector's vocabulary requirement)."""
    draft = (
        "*   Timeouts on cold model loads.\n"
        "*   VRAM fragmentation after repeated swaps.\n"
        "*   Silent fallbacks that mask misconfigured pins.\n"
        "*   Retry storms when the queue backs up.\n"
        "*   Checkpoint corruption on mid-run kills.\n"
        "*   Disk pressure from orphaned model blobs.\n\n"
        "## The common thread\n\n"
        "Every one of these traces back to treating the GPU as free.\n"
    )
    clean, n = two_pass._strip_planning_dump_preamble(draft)
    assert n == 0
    assert clean == draft


def test_strip_planning_dump_noop_when_dump_quoted_in_code_fence():
    """An article that QUOTES a planning dump inside a fenced block (a post
    about this very bug class) is legitimate — code spans are masked before
    the gate, same as the validator call site."""
    draft = (
        "We caught our writer emitting its plan instead of the article. "
        "The leaked scaffold looked like this:\n\n"
        "```\n" + _E46_DUMP_BULLETS + "\n```\n\n"
        "The fix was a structural detector on the opening bullet block, "
        "wired into the programmatic validator as a hard gate.\n"
    )
    clean, n = two_pass._strip_planning_dump_preamble(draft)
    assert n == 0
    assert clean == draft


def test_strip_planning_dump_never_guts_thin_article():
    """Guardrail: when fewer than 50 words would survive the strip, the
    original is kept — contamination becomes a QA/finding problem, never a
    silent near-empty post."""
    draft = _E46_DUMP_BULLETS + "\n\n### Heading\nEight words of article only, nothing more.\n"
    clean, n = two_pass._strip_planning_dump_preamble(draft)
    assert n == 0
    assert clean == draft


def test_strip_planning_dump_noop_on_pure_dump_without_article():
    """An all-dump draft (no article underneath) is returned unchanged — the
    strip never zeroes a draft; the validator rejects it downstream."""
    clean, n = two_pass._strip_planning_dump_preamble(_E46_DUMP_BULLETS)
    assert n == 0
    assert clean == _E46_DUMP_BULLETS


async def test_run_strips_planning_dump_from_final_draft(monkeypatch):
    """End-to-end: a pure planning dump with ZERO instruction/identity echo
    lines is cleaned in run()'s returned draft, counted separately from the
    echo strip, and surfaced as its own finding kind."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **_kw):
        return _E46_FUSED_DUMP
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    findings: list[dict] = []
    monkeypatch.setattr("utils.findings.emit_finding", lambda **kw: findings.append(kw))

    result = await two_pass.run(
        topic="Ellipses and triple-dot punctuation marks",
        angle="informative", niche_id="glad-labs",
        pool=_fake_pool_with_no_snippets(), site_config=_fake_site_config(),
    )
    assert result["draft"].startswith("An **ellipsis**")
    assert "*   Topic:" not in result["draft"]
    assert result["planning_dump_stripped"] >= 10
    # Accounted separately from the instruction/identity echo strip.
    assert result["prompt_echo_stripped"] == 0
    assert len(findings) == 1
    assert findings[0]["kind"] == "writer_planning_dump_stripped"
    assert findings[0]["severity"] == "warn"


async def test_run_clean_draft_fires_no_planning_dump_finding(monkeypatch):
    """A clean draft reports planning_dump_stripped=0 and fires no finding."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **_kw):
        return _REAL_BODY
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    findings: list[dict] = []
    monkeypatch.setattr("utils.findings.emit_finding", lambda **kw: findings.append(kw))

    result = await two_pass.run(
        topic=_ECHO_TOPIC, angle=_ECHO_ANGLE, niche_id="glad-labs",
        pool=_fake_pool_with_no_snippets(), site_config=_fake_site_config(),
    )
    assert result["draft"] == _REAL_BODY
    assert result["planning_dump_stripped"] == 0
    assert findings == []


async def test_expansion_planning_dump_stripped_before_keep_best(monkeypatch):
    """#2009 compounding, planning-dump flavour: an expansion that returns a
    bullet dump + article is stripped BEFORE the keep-best word comparison,
    so the adopted draft is the clean article — never dump-inflated text."""
    original = "word " * 200  # below 0.7 * 2500 → expansion fires

    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **_kw):
        return original
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    # Expansion output: dump + article long enough that the CLEAN article
    # (not the dump-inflated total) wins keep-best on its own merits.
    long_article_tail = "\n\nSubstance sentence with real detail here. " * 60
    async def fake_expand(prompt, **kwargs):
        return _E46_FUSED_DUMP + long_article_tail
    monkeypatch.setattr("services.llm_text.ollama_chat_text", fake_expand)

    findings: list[dict] = []
    monkeypatch.setattr("utils.findings.emit_finding", lambda **kw: findings.append(kw))

    result = await two_pass.run(
        topic="Ellipses and triple-dot punctuation marks",
        angle="informative", niche_id="glad-labs",
        pool=_fake_pool_with_no_snippets(),
        site_config=_short_draft_site_config(),
        target_length=2500,
    )
    assert result["length_expanded"] is True
    assert result["draft"].startswith("An **ellipsis**")
    assert "*   Topic:" not in result["draft"]
    assert result["planning_dump_stripped"] >= 10
    assert any(f["kind"] == "writer_planning_dump_stripped" for f in findings)


# ---------------------------------------------------------------------------
# Dangling-heading trim (2026-07-06 gemma early-stop investigation).
#
# The thinking-capable pipeline_writer_model can stop early and leave the draft
# ENDING at a bare section heading with no body under it — a shape the
# content_validator truncated_content rule EXEMPTS (headings legitimately lack
# terminal punctuation). _trim_dangling_heading drops it deterministically
# before the expansion decision so the persisted body ends on prose and the
# keep-best length compare uses the real word count.
# ---------------------------------------------------------------------------

_DANGLING_BODY = (
    "Real intro paragraph that sets up the piece with enough words to survive.\n\n"
    "## The VRAM Math\n\n"
    "A full paragraph of grounded prose about quantization and headroom here.\n\n"
    "## Local Inference vs"
)


def test_trim_dangling_heading_drops_trailing_bare_heading():
    """A draft ending at a bare heading is trimmed to end on the prior prose."""
    clean, n = two_pass._trim_dangling_heading(_DANGLING_BODY)
    assert n == 1
    assert clean.rstrip().endswith("headroom here.")
    assert "## Local Inference vs" not in clean


def test_trim_dangling_heading_noop_on_prose_ending():
    """A draft whose last line is real prose is returned untouched."""
    body = "## Verdict\n\nThe honest tradeoff is matching the tool to the use."
    clean, n = two_pass._trim_dangling_heading(body)
    assert n == 0
    assert clean == body


def test_trim_dangling_heading_noop_on_mid_sentence_tail():
    """A mid-sentence cut is NOT a heading — left for the validator's
    truncated_content rule + rescue, not this guard."""
    body = "## Costs\n\nThe amortized GPU cost means you aren"
    clean, n = two_pass._trim_dangling_heading(body)
    assert n == 0
    assert clean == body


def test_trim_dangling_heading_strips_stacked_headings():
    """Two stacked trailing headings are both removed in one pass."""
    body = "Intro prose that survives the trim.\n\n## Section A\n\n## Section B"
    clean, n = two_pass._trim_dangling_heading(body)
    assert n == 2
    assert clean.rstrip().endswith("survives the trim.")


def test_trim_dangling_heading_never_zeroes_heading_only_draft():
    """A heading-only body is left untouched (never trimmed to nothing) so the
    caller's finding + human review path takes over."""
    body = "## Only A Heading"
    clean, n = two_pass._trim_dangling_heading(body)
    assert n == 0
    assert clean == body


async def test_run_trims_dangling_heading_and_fires_finding(monkeypatch):
    """End-to-end: a draft ending at a bare heading is cleaned in run()'s
    returned draft, surfaced via ``dangling_heading_trimmed``, and reported as
    its own finding kind. Expansion is OFF so this isolates the trim."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **_kw):
        return _DANGLING_BODY
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    findings: list[dict] = []
    monkeypatch.setattr("utils.findings.emit_finding", lambda **kw: findings.append(kw))

    result = await two_pass.run(
        topic="Running a 27B LLM locally", angle="hands-on", niche_id="glad-labs",
        pool=_fake_pool_with_no_snippets(),
        # expansion OFF so the trim is the only transform under test
        site_config=_short_draft_site_config(writer_length_expansion_enabled="false"),
    )
    assert "## Local Inference vs" not in result["draft"]
    assert result["draft"].rstrip().endswith("headroom here.")
    assert result["dangling_heading_trimmed"] == 1
    assert len(findings) == 1
    assert findings[0]["kind"] == "writer_dangling_heading_trimmed"
    assert findings[0]["severity"] == "warn"


async def test_run_clean_ending_fires_no_dangling_finding(monkeypatch):
    """A draft that ends on prose reports dangling_heading_trimmed=0, no finding."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **_kw):
        return _REAL_BODY
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    findings: list[dict] = []
    monkeypatch.setattr("utils.findings.emit_finding", lambda **kw: findings.append(kw))

    result = await two_pass.run(
        topic=_ECHO_TOPIC, angle=_ECHO_ANGLE, niche_id="glad-labs",
        pool=_fake_pool_with_no_snippets(),
        site_config=_short_draft_site_config(writer_length_expansion_enabled="false"),
    )
    assert result["dangling_heading_trimmed"] == 0
    assert not any(f["kind"] == "writer_dangling_heading_trimmed" for f in findings)


# ---------------------------------------------------------------------------
# Writer thinking-channel disable (2026-07-06 gemma early-stop root fix).
#
# A thinking-capable writer model burns its generation budget in a hidden
# reasoning channel, truncating the visible draft. _resolve_writer_think returns
# False (disable) by default so the draft/revise/expand calls pass think=False.
# ---------------------------------------------------------------------------

def test_resolve_writer_think_disables_by_default():
    """Default (writer_disable_thinking unset/true) → False (disable channel)."""
    sc = _short_draft_site_config()  # no writer_disable_thinking override → default 'true'
    assert two_pass._resolve_writer_think(sc) is False


def test_resolve_writer_think_none_when_switch_off():
    """writer_disable_thinking=false → None (leave the call unchanged)."""
    sc = _short_draft_site_config(writer_disable_thinking="false")
    assert two_pass._resolve_writer_think(sc) is None


def test_resolve_writer_think_disables_when_site_config_missing():
    """No site_config → default to disabling (the safe writer default)."""
    assert two_pass._resolve_writer_think(None) is False


async def test_run_passes_think_false_to_draft_call(monkeypatch):
    """The draft call receives think=False by default so a thinking writer
    doesn't truncate itself."""
    seen: dict = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **kw):
        seen["think"] = kw.get("think")
        return _REAL_BODY
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    await two_pass.run(
        topic="t", angle="a", niche_id="glad-labs",
        pool=_fake_pool_with_no_snippets(),
        site_config=_short_draft_site_config(writer_length_expansion_enabled="false"),
    )
    assert seen["think"] is False


async def test_run_omits_think_when_switch_off(monkeypatch):
    """writer_disable_thinking=false → the draft call gets think=None (unchanged)."""
    seen: dict = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None,
                         site_config=None, **kw):
        seen["think"] = kw.get("think")
        return _REAL_BODY
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    await two_pass.run(
        topic="t", angle="a", niche_id="glad-labs",
        pool=_fake_pool_with_no_snippets(),
        site_config=_short_draft_site_config(
            writer_length_expansion_enabled="false", writer_disable_thinking="false",
        ),
    )
    assert seen["think"] is None


# ---------------------------------------------------------------------------
# Retrieval de-echo — MMR + near-duplicate ceiling (RAG self-echo root fix).
#
# _embed_and_fetch_snippets used to take the top-N nearest `posts` by raw cosine
# and hand them to the writer, which "draws ONLY from the provided snippets".
# For a topic in a dense cluster the #1 neighbor is a SIBLING post, and 3-4 of
# the top slots are the same echo cluster restating the same opening — so the
# writer parrots it (the 2026-06 "VRAM is the only currency" 4-post echo).
# qa.content_originality (#2182) flags it post-hoc; this is the retrieval-side
# root fix: oversample candidates, drop near-identical priors (fail-open), then
# MMR-select for diversity so an echo cluster collapses to ONE representative.
# memory: project_rag_corpus_pollution (the INVERSE self-echo failure).
# ---------------------------------------------------------------------------


def test_cosine_identical_vectors_is_one():
    assert two_pass._cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors_is_zero():
    assert two_pass._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_vectors_is_negative_one():
    assert two_pass._cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_is_zero_not_nan():
    """A zero vector has no direction — return 0.0, never divide-by-zero → NaN."""
    assert two_pass._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_mismatched_dims_is_zero():
    """A wrong-dimension vector is treated as 'not similar', never crashes."""
    assert two_pass._cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_parse_vector_pgvector_string():
    """asyncpg returns a `vector` column as its text form '[a,b,c]' (no codec)."""
    assert two_pass._parse_vector("[0.1,0.2,0.3]") == [0.1, 0.2, 0.3]


def test_parse_vector_passthrough_list():
    assert two_pass._parse_vector([1, 2, 3]) == [1.0, 2.0, 3.0]


def test_parse_vector_none_is_empty():
    assert two_pass._parse_vector(None) == []


def test_select_snippets_empty_returns_empty():
    assert two_pass._select_snippets([], k=5, dedup_ceiling=1.0, mmr_lambda=0.5) == []


def test_select_snippets_caps_at_k():
    cands = [
        {"ref": "a", "relevance": 0.9, "vec": [1.0, 0.0]},
        {"ref": "b", "relevance": 0.8, "vec": [0.0, 1.0]},
        {"ref": "c", "relevance": 0.7, "vec": [1.0, 1.0]},
    ]
    out = two_pass._select_snippets(cands, k=2, dedup_ceiling=1.0, mmr_lambda=1.0)
    assert len(out) == 2


def test_select_snippets_first_pick_is_highest_relevance():
    cands = [
        {"ref": "low", "relevance": 0.5, "vec": [0.0, 1.0]},
        {"ref": "high", "relevance": 0.95, "vec": [1.0, 0.0]},
    ]
    out = two_pass._select_snippets(cands, k=2, dedup_ceiling=1.0, mmr_lambda=0.5)
    assert out[0]["ref"] == "high"


def test_select_snippets_mmr_prefers_diverse_over_near_duplicate():
    """The load-bearing behaviour: given a top post A and a near-identical
    sibling B plus a diverse C of slightly lower relevance, MMR (lambda 0.5)
    picks A then the DIVERSE C — the echo sibling B is suppressed."""
    cands = [
        {"ref": "A", "relevance": 0.90, "vec": [1.0, 0.0]},
        {"ref": "B", "relevance": 0.85, "vec": [1.0, 0.0]},  # near-identical to A
        {"ref": "C", "relevance": 0.80, "vec": [0.0, 1.0]},  # diverse
    ]
    out = two_pass._select_snippets(cands, k=2, dedup_ceiling=1.0, mmr_lambda=0.5)
    assert [c["ref"] for c in out] == ["A", "C"]


def test_select_snippets_lambda_one_is_pure_relevance():
    """lambda=1.0 disables the diversity term → pure relevance ranking (MMR off),
    so the near-duplicate B (higher relevance than C) is kept. This is the DB
    escape hatch to turn MMR off."""
    cands = [
        {"ref": "A", "relevance": 0.90, "vec": [1.0, 0.0]},
        {"ref": "B", "relevance": 0.85, "vec": [1.0, 0.0]},
        {"ref": "C", "relevance": 0.80, "vec": [0.0, 1.0]},
    ]
    out = two_pass._select_snippets(cands, k=2, dedup_ceiling=1.0, mmr_lambda=1.0)
    assert [c["ref"] for c in out] == ["A", "B"]


def test_select_snippets_dedup_ceiling_drops_near_identical_prior():
    """A candidate at/above the ceiling is a near-republish of what we're writing
    — dropped BEFORE selection so it can never be snippet #1 and get parroted."""
    cands = [
        {"ref": "dupe", "relevance": 0.95, "vec": [1.0, 0.0]},   # >= 0.93 ceiling
        {"ref": "ok1", "relevance": 0.85, "vec": [0.0, 1.0]},
        {"ref": "ok2", "relevance": 0.80, "vec": [1.0, 1.0]},
    ]
    out = two_pass._select_snippets(cands, k=5, dedup_ceiling=0.93, mmr_lambda=1.0)
    refs = [c["ref"] for c in out]
    assert "dupe" not in refs
    assert refs == ["ok1", "ok2"]


def test_select_snippets_dedup_ceiling_fails_open_when_all_excluded():
    """If EVERY candidate is above the ceiling, keep them — grounding is never
    zeroed by the ceiling (a thin diverse set beats no grounding at all)."""
    cands = [
        {"ref": "x", "relevance": 0.98, "vec": [1.0, 0.0]},
        {"ref": "y", "relevance": 0.96, "vec": [0.0, 1.0]},
    ]
    out = two_pass._select_snippets(cands, k=5, dedup_ceiling=0.93, mmr_lambda=1.0)
    assert {c["ref"] for c in out} == {"x", "y"}


# -- node-level wiring: _embed_and_fetch_snippets oversamples + de-echoes --


def _mmr_site_config(*, snippet_limit=2, multiplier=10, dedup_ceiling=1.0, mmr_lambda=0.5):
    """SiteConfig stub with the retrieval de-echo knobs set to specific values
    (the shared _fake_site_config always returns the caller's default, which
    can't pin snippet_limit / multiplier / ceiling / lambda per-key)."""
    ints = {
        "writer_rag_two_pass_snippet_limit": snippet_limit,
        "writer_rag_candidate_multiplier": multiplier,
    }
    floats = {
        "writer_rag_dedup_ceiling": dedup_ceiling,
        "writer_rag_mmr_lambda": mmr_lambda,
    }
    strs = {
        "pipeline_writer_model": "glm-4.7-5090:latest",
        "cost_tier.standard.model": "",
        "writer_length_expansion_enabled": "false",
        "rag_source_filter": "posts",
    }
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda key, default="": strs.get(key, default))
    sc.get_int = MagicMock(side_effect=lambda key, default=0: ints.get(key, default))
    sc.get_float = MagicMock(side_effect=lambda key, default=0.0: floats.get(key, default))
    return sc


def _pool_returning(rows):
    """Fake asyncpg pool whose conn.fetch returns ``rows`` (dict records)."""
    pool = MagicMock()
    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(return_value=rows)
    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


async def test_embed_and_fetch_oversamples_candidate_pool(monkeypatch):
    """The fetch LIMIT must be snippet_limit * candidate_multiplier, not just
    snippet_limit — MMR needs a bigger pool than it returns to select from."""
    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return "Clean draft, no markers."
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    pool, conn_mock = _recording_pool()
    await two_pass.run(
        topic="t", angle="a", niche_id="n", pool=pool,
        site_config=_mmr_site_config(snippet_limit=5, multiplier=4),
    )
    sql, *params = conn_mock.fetch.await_args.args
    assert 20 in params  # 5 * 4 candidate LIMIT (oversampled), not 5


async def test_embed_and_fetch_mmr_suppresses_near_duplicate_sibling(monkeypatch):
    """End-to-end at the node: three candidates where B is a near-duplicate of
    the top A and C is diverse. The snippets handed to the writer must be
    [A, C] — the echo sibling B is suppressed by MMR."""
    captured: dict = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["snippets"] = snippets
        return "Clean draft, no markers."
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    rows = [
        {"source_table": "posts", "source_id": "A", "text_preview": "alpha", "embedding": "[1,0]", "relevance": 0.90},
        {"source_table": "posts", "source_id": "B", "text_preview": "beta", "embedding": "[1,0]", "relevance": 0.85},
        {"source_table": "posts", "source_id": "C", "text_preview": "gamma", "embedding": "[0,1]", "relevance": 0.80},
    ]
    await two_pass.run(
        topic="t", angle="a", niche_id="n", pool=_pool_returning(rows),
        site_config=_mmr_site_config(snippet_limit=2, dedup_ceiling=1.0, mmr_lambda=0.5),
    )
    refs = [s["ref"] for s in captured["snippets"]]
    assert refs == ["A", "C"]


async def test_embed_and_fetch_dedup_ceiling_drops_near_identical(monkeypatch):
    """A candidate above the ceiling (a near-republish) is dropped before the
    writer ever sees it; lambda=1.0 isolates the ceiling from MMR."""
    captured: dict = {}

    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        captured["snippets"] = snippets
        return "Clean draft, no markers."
    monkeypatch.setattr(
        "modules.content.ai_content_generator.generate_with_context",
        fake_pass1, raising=False,
    )

    async def fake_embed(text, *, site_config=None):
        return [0.0] * 768
    monkeypatch.setattr("services.topic_ranking.embed_text", fake_embed)

    rows = [
        {"source_table": "posts", "source_id": "dupe", "text_preview": "x", "embedding": "[1,0]", "relevance": 0.95},
        {"source_table": "posts", "source_id": "ok1", "text_preview": "y", "embedding": "[0,1]", "relevance": 0.85},
        {"source_table": "posts", "source_id": "ok2", "text_preview": "z", "embedding": "[1,1]", "relevance": 0.80},
    ]
    await two_pass.run(
        topic="t", angle="a", niche_id="n", pool=_pool_returning(rows),
        site_config=_mmr_site_config(snippet_limit=5, dedup_ceiling=0.93, mmr_lambda=1.0),
    )
    refs = [s["ref"] for s in captured["snippets"]]
    assert "dupe" not in refs
    assert set(refs) == {"ok1", "ok2"}
