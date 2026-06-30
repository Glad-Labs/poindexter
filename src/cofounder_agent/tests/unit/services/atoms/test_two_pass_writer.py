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
