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


async def test_no_external_needed_returns_pass1_draft(monkeypatch):
    """First draft has no [EXTERNAL_NEEDED] markers → graph short-circuits, no revise."""
    async def fake_pass1(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        return "A clean first draft with no markers."
    monkeypatch.setattr("services.ai_content_generator.generate_with_context", fake_pass1, raising=False)
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
    monkeypatch.setattr("services.ai_content_generator.generate_with_context", fake_pass1, raising=False)
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


async def test_loop_caps_at_max_revisions(monkeypatch):
    """Pathological: every revision adds new markers. Loop must terminate at _MAX_REVISION_LOOPS=3."""
    counter = {"n": 0}
    async def always_needs_more(topic, angle, snippets, extra_instructions=None, site_config=None, **_kw):
        counter["n"] += 1
        return f"Draft with [EXTERNAL_NEEDED: thing {counter['n']}] inside."
    monkeypatch.setattr("services.ai_content_generator.generate_with_context", always_needs_more, raising=False)
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
        "services.ai_content_generator.generate_with_context",
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
    import services.ai_content_generator as acg

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
        "services.ai_content_generator._resolve_rag_writer_model", fake_resolve,
    )
    monkeypatch.setattr(
        "services.ai_content_generator.get_prompt_manager",
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
        "services.ai_content_generator.generate_with_context",
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
