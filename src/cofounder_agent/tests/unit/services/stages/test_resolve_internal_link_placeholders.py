"""Unit tests for ResolveInternalLinkPlaceholdersStage.

Pins the contract that closes the ~95% canonical_blog rejection rate
captured 2026-05-15 — writer-emitted ``[posts/<slug>]`` placeholders get
rewritten to real markdown links or stripped, BEFORE the
programmatic_validator runs and critical-flags them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.stages.resolve_internal_link_placeholders import (
    ResolveInternalLinkPlaceholdersStage,
    _PLACEHOLDER_RE,
    _resolve_all_placeholders,
    _resolve_one,
)


# ---- Pure helpers ----------------------------------------------------------


def test_placeholder_regex_catches_slug_form():
    """``[posts/some-slug]`` — the most common writer-emitted shape."""
    matches = _PLACEHOLDER_RE.findall("see [posts/intro-to-rag] for more")
    assert matches == ["intro-to-rag"]


def test_placeholder_regex_catches_uuid_form():
    """The validator also catches ``[posts/<uuid>]`` shapes."""
    matches = _PLACEHOLDER_RE.findall(
        "see [posts/6f48c270-94b8-48ec-b0cc-a583f797008b] for the writeup",
    )
    assert matches == ["6f48c270-94b8-48ec-b0cc-a583f797008b"]


def test_placeholder_regex_ignores_real_markdown_links():
    """``[posts/foo](/posts/foo)`` is a real markdown link — must NOT
    match. The validator at content_validator.py:339 has the same
    negative-lookahead; if we strip these we destroy legitimate output."""
    matches = _PLACEHOLDER_RE.findall("see [posts/foo](/posts/foo) for more")
    assert matches == []


def test_placeholder_regex_ignores_unrelated_brackets():
    """Don't false-positive on ``[ref]``, ``[1]``, ``[Author: name]`` etc."""
    text = "Per [Smith2024] and [1] and [posts/]: see foo."
    assert _PLACEHOLDER_RE.findall(text) == []


def test_resolve_one_known_slug_produces_markdown_link():
    """Found post → real markdown link with the post's title."""
    posts_by_id = {
        "intro-to-rag": {
            "id_text": "abc-123", "slug": "intro-to-rag",
            "title": "Intro to RAG",
        },
    }
    res = _resolve_one("[posts/intro-to-rag]", "intro-to-rag", posts_by_id)

    assert res.was_resolved is True
    assert res.replacement == "[Intro to RAG](/posts/intro-to-rag)"


def test_resolve_one_unknown_slug_strips_placeholder():
    """LLM-hallucinated slug → strip the bracket entirely. Better than
    shipping ``[posts/this-slug-doesnt-exist]`` to the reader."""
    res = _resolve_one("[posts/hallucinated]", "hallucinated", {})

    assert res.was_resolved is False
    assert res.replacement == ""


def test_resolve_one_known_slug_no_title_falls_back_to_slug_text():
    """If the post has no title (data quality issue) use the slug as
    the link text rather than producing ``[](/posts/x)`` which would
    render as an empty link."""
    posts_by_id = {
        "foo": {"id_text": "abc", "slug": "foo", "title": None},
    }
    res = _resolve_one("[posts/foo]", "foo", posts_by_id)
    assert res.replacement == "[foo](/posts/foo)"


# ---- Async _resolve_all_placeholders ---------------------------------------


class _FakePoolCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        return False


def _pool_returning(rows):
    """Build a pool whose only acquire-conn returns ``rows`` on fetch."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolCtx(conn))
    return pool


@pytest.mark.asyncio
async def test_resolve_all_replaces_each_match_in_reverse_offset_order():
    """Multiple placeholders in one document — each one resolves correctly.
    Reverse-offset substitution keeps earlier indices stable."""
    content = (
        "Background: see [posts/intro-to-rag] for the basics. "
        "Then read [posts/advanced-rag] for the next step."
    )
    pool = _pool_returning([
        {"id_text": "1", "slug": "intro-to-rag", "title": "Intro to RAG"},
        {"id_text": "2", "slug": "advanced-rag", "title": "Advanced RAG"},
    ])

    new, resolved, stripped = await _resolve_all_placeholders(content, pool)

    assert resolved == 2
    assert stripped == 0
    assert "[Intro to RAG](/posts/intro-to-rag)" in new
    assert "[Advanced RAG](/posts/advanced-rag)" in new
    assert "[posts/" not in new  # all placeholders consumed


@pytest.mark.asyncio
async def test_resolve_all_strips_unknown_and_keeps_known_in_same_doc():
    """Mixed batch — one slug is real, the other is a hallucination.
    Real one becomes a link; hallucinated one is stripped. Both flagged
    in the counts so the operator can see how often the writer is
    hallucinating."""
    content = "Real: [posts/exists]. Fake: [posts/nope]."
    pool = _pool_returning([
        {"id_text": "1", "slug": "exists", "title": "Exists"},
    ])

    new, resolved, stripped = await _resolve_all_placeholders(content, pool)

    assert resolved == 1
    assert stripped == 1
    assert "[Exists](/posts/exists)" in new
    assert "[posts/nope]" not in new
    assert "Fake: ." in new  # the bare period survives where the bracket was


@pytest.mark.asyncio
async def test_resolve_all_no_placeholders_returns_content_unchanged():
    """Empty/None content + no-match content both return cleanly. Stage
    must never throw on the no-op path."""
    pool = _pool_returning([])
    new, resolved, stripped = await _resolve_all_placeholders(
        "Just prose, no placeholders.", pool,
    )
    assert new == "Just prose, no placeholders."
    assert (resolved, stripped) == (0, 0)


@pytest.mark.asyncio
async def test_resolve_all_idempotent_on_already_resolved_links():
    """Run the resolver on content that already contains real markdown
    links — they must NOT be touched (negative-lookahead protection)."""
    content = "See [Intro to RAG](/posts/intro-to-rag) for the basics."
    pool = _pool_returning([])  # no DB lookup needed, no placeholder

    new, resolved, stripped = await _resolve_all_placeholders(content, pool)

    assert new == content
    assert (resolved, stripped) == (0, 0)


# ---- Stage integration (execute) -------------------------------------------


@pytest.mark.asyncio
async def test_stage_execute_writes_resolved_content_back_to_context():
    """The stage rewrites ``context['content']`` in place AND records the
    resolved/stripped counts so downstream stages + observability can
    see the work."""
    stage = ResolveInternalLinkPlaceholdersStage()
    pool = _pool_returning([
        {"id_text": "1", "slug": "x", "title": "X"},
    ])
    ctx: dict = {
        "content": "Hello [posts/x] world",
        "pool": pool,
    }

    result = await stage.execute(ctx, {})

    assert result.ok is True
    assert ctx["content"] == "Hello [X](/posts/x) world"
    assert ctx["internal_link_placeholders_resolved"] == 1
    assert ctx["internal_link_placeholders_stripped"] == 0
    assert ctx["stages"]["resolve_internal_link_placeholders"] is True


@pytest.mark.asyncio
async def test_stage_execute_returns_ok_on_empty_content():
    """No content — stage is a clean no-op. Don't crash the pipeline
    if generate_content somehow produced an empty string."""
    stage = ResolveInternalLinkPlaceholdersStage()
    ctx: dict = {"content": "", "pool": MagicMock()}

    result = await stage.execute(ctx, {})

    assert result.ok is True
    assert "no content" in result.detail


@pytest.mark.asyncio
async def test_stage_execute_skips_cleanly_without_pool():
    """If no pool is available (misconfigured context), the stage must
    NOT strip placeholders blindly — that would silently lose every
    legitimate internal link. Better to leave content unchanged and let
    the validator surface the leak."""
    stage = ResolveInternalLinkPlaceholdersStage()
    ctx: dict = {"content": "Has [posts/foo] placeholder"}

    result = await stage.execute(ctx, {})

    assert result.ok is True
    assert "no pool" in result.detail
    # Content unchanged — the placeholder survives
    assert ctx["content"] == "Has [posts/foo] placeholder"


@pytest.mark.asyncio
async def test_stage_execute_continues_workflow_on_resolver_crash():
    """Even if the DB query throws, the stage returns ok=False but
    ``continue_workflow=True`` so the rest of the pipeline runs.
    Validator will then surface the unresolved placeholder, which is
    the next-best signal for the operator."""
    stage = ResolveInternalLinkPlaceholdersStage()
    bad_pool = MagicMock()
    bad_pool.acquire = MagicMock(side_effect=RuntimeError("DB down"))
    ctx: dict = {"content": "Test [posts/x]", "pool": bad_pool}

    result = await stage.execute(ctx, {})

    assert result.ok is False
    assert result.continue_workflow is True
    assert "crashed" in result.detail
