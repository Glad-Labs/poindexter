"""Unit tests — loop-shaped best-effort-failure visibility (gap-site burn-down
batch 5).

Six sites across content atoms / services swallowed per-iteration failures
inside a loop (or per-stream-line, or per-scan-candidate) at ``logger.debug``
or a bare ``continue`` — invisible, AND if converted naively to a per-iteration
``emit_finding`` would write-amplify (one row per item instead of one per
run). Each now uses the aggregate-count pattern: track failures in a list/
counter across the loop, then emit exactly ONE ``info`` finding after the
loop completes (never per-iteration) — visible, non-paging, non-blocking.

A 7th site (``plugins/llm_providers/gemini.py`` — ``_response_to_raw``'s
dumper-fallback loop) is intentionally left ``# silent-ok``, not tested here:
both branches of that loop produce an equally-good fallback dict, so there is
no operational signal to surface.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import services.http_client as http_client_module
import services.skill_importer as skill_importer_module
from modules.content.atoms.content_reconcile_citations import (
    _resolve_youtube_authors,
)
from services.http_client import wire_http_client_modules
from services.llm_providers.openai_compat import OpenAICompatProvider
from services.research_context import build_rag_context
from services.skill_importer import SkillImportError, remove_skill
from services.topic_sources.codebase import CodebaseSource

pytestmark = pytest.mark.unit

# Captured before any test patches ``httpx.AsyncClient`` — the patch lambdas
# below construct a real client against a MockTransport, so they must call
# the ORIGINAL class, not look it up via ``httpx.AsyncClient`` again (which
# would resolve to the patch itself and recurse infinitely).
_RealAsyncClient = httpx.AsyncClient


def _capture(monkeypatch) -> list[dict]:
    """Patch ``utils.findings.emit_finding`` with a capture list."""
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


# ---------------------------------------------------------------------------
# content.reconcile_citations — _resolve_youtube_authors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_youtube_authors_aggregates_failures_into_one_finding(
    monkeypatch,
):
    calls = _capture(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "bad1" in url or "bad2" in url:
            raise httpx.ConnectError("dns failed")
        return httpx.Response(200, json={"author_name": "Some Channel"})

    transport = httpx.MockTransport(handler)

    with patch("httpx.AsyncClient", side_effect=lambda **kw: _RealAsyncClient(
        transport=transport, **{k: v for k, v in kw.items() if k != "transport"}
    )):
        authors = await _resolve_youtube_authors(
            ["https://youtube.com/watch?v=bad1",
             "https://youtube.com/watch?v=good",
             "https://youtube.com/watch?v=bad2"],
            site_config=None,
        )

    # Non-blocking — the good URL still resolved.
    assert authors == {"https://youtube.com/watch?v=good": "Some Channel"}
    # Visible + aggregated — ONE finding covering both failed URLs, not two.
    assert len(calls) == 1
    assert calls[0]["kind"] == "youtube_oembed_resolve_failed"
    assert calls[0]["dedup_key"] == "youtube_oembed_resolve_failed"
    assert "bad1" in calls[0]["body"]
    assert "bad2" in calls[0]["body"]


# ---------------------------------------------------------------------------
# http_client.wire_http_client_modules
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_http_client_state():
    yield
    http_client_module.set_http_client(None)


def test_wire_http_client_modules_aggregates_failures_into_one_finding(
    monkeypatch,
):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(
        http_client_module,
        "WIRED_HTTP_CLIENT_MODULES",
        ("services.no_such_module_xyz",),
    )

    wired = wire_http_client_modules(MagicMock())

    assert wired == 0  # non-blocking — the bad entry just doesn't count
    assert len(calls) == 1
    assert calls[0]["kind"] == "http_client_wiring_failed"
    assert calls[0]["dedup_key"] == "http_client_wiring_failed"
    assert "services.no_such_module_xyz" in calls[0]["body"]


# ---------------------------------------------------------------------------
# openai_compat.OpenAICompatProvider.stream — malformed SSE lines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_aggregates_malformed_lines_into_one_finding(monkeypatch):
    calls = _capture(monkeypatch)
    sse_body = (
        b"data: not-json-at-all\n"
        b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n'
        b"data: [DONE]\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body)

    transport = httpx.MockTransport(handler)
    provider = OpenAICompatProvider()

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda **kw: _RealAsyncClient(
            transport=transport, **{k: v for k, v in kw.items() if k != "transport"}
        ),
    ):
        tokens = [
            t async for t in provider.stream(
                messages=[{"role": "user", "content": "hi"}], model="local-model",
            )
        ]

    # Non-blocking — the valid line + the DONE sentinel still streamed.
    assert any(t.text == "hi" for t in tokens)
    assert tokens[-1].finish_reason == "stop"
    # Visible + aggregated — ONE finding for the whole stream, not per-line.
    assert len(calls) == 1
    assert calls[0]["kind"] == "stream_malformed_sse_lines"
    assert "1 malformed" in calls[0]["title"]


# ---------------------------------------------------------------------------
# research_context.build_rag_context — dropped_db_error
# ---------------------------------------------------------------------------


class _FakeHit:
    def __init__(self, source_id: str, similarity: float = 0.85) -> None:
        self.source_id = source_id
        self.similarity = similarity
        self.metadata = {"title": "Untitled"}


@pytest.mark.asyncio
async def test_build_rag_context_aggregates_db_errors_into_one_finding(
    monkeypatch,
):
    calls = _capture(monkeypatch)

    class _StubMemoryClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def find_similar_posts(self, topic, *, limit=5, min_similarity=0.75):
            return [_FakeHit("post-1"), _FakeHit("post-2")]

    fake_module = types.ModuleType("poindexter.memory")
    fake_module.MemoryClient = _StubMemoryClient
    grandparent = sys.modules.get("poindexter") or types.ModuleType("poindexter")
    grandparent.memory = fake_module
    monkeypatch.setitem(sys.modules, "poindexter", grandparent)
    monkeypatch.setitem(sys.modules, "poindexter.memory", fake_module)

    class _RaisingPool:
        async def fetchrow(self, *args, **kwargs):
            raise RuntimeError("posts lookup boom")

    class _DatabaseServiceDouble:
        pool = _RaisingPool()

    result = await build_rag_context(
        _DatabaseServiceDouble(), "some topic",
    )

    # Non-blocking — both hits dropped, but the call completes (None, same
    # as "no similar posts found" — not a crash).
    assert result is None
    assert len(calls) == 1
    assert calls[0]["kind"] == "rag_context_slug_lookup_failed"
    assert calls[0]["dedup_key"] == "rag_context_slug_lookup_failed"
    assert "2 of 2" in calls[0]["body"]


# ---------------------------------------------------------------------------
# skill_importer.remove_skill — parse-failure scan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_skill_aggregates_parse_failures_into_one_finding(
    monkeypatch, tmp_path,
):
    calls = _capture(monkeypatch)
    skills_root = tmp_path / "skills"
    good_dir = skills_root / "pack" / "good-skill"
    good_dir.mkdir(parents=True)
    (good_dir / "SKILL.md").write_text("---\nname: good-skill\n---\nbody", encoding="utf-8")
    bad_dir = skills_root / "pack" / "bad-skill"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("garbage", encoding="utf-8")

    real_parse = skill_importer_module._parse_frontmatter

    def _flaky_parse(raw: str):
        if raw == "garbage":
            raise ValueError("malformed frontmatter")
        return real_parse(raw)

    monkeypatch.setattr(skill_importer_module, "_parse_frontmatter", _flaky_parse)

    with patch("services.skill_importer._SKILLS_DIR", skills_root):
        result = await remove_skill("good-skill", pool=None)

    # Non-blocking — the good skill was still found and removed despite the
    # sibling file's unparseable frontmatter.
    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0]["kind"] == "skill_frontmatter_parse_failed"
    assert calls[0]["dedup_key"] == "skill_frontmatter_parse_failed"
    assert "bad-skill" in calls[0]["body"]


@pytest.mark.asyncio
async def test_remove_skill_not_found_still_raises_after_parse_failure(
    monkeypatch, tmp_path,
):
    """A parse failure must not mask the normal not-installed error when the
    target genuinely isn't there."""
    calls = _capture(monkeypatch)
    skills_root = tmp_path / "skills"
    bad_dir = skills_root / "pack" / "bad-skill"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text("garbage", encoding="utf-8")

    def _flaky_parse(raw: str):
        raise ValueError("malformed frontmatter")

    monkeypatch.setattr(skill_importer_module, "_parse_frontmatter", _flaky_parse)

    with patch("services.skill_importer._SKILLS_DIR", skills_root):
        with pytest.raises(SkillImportError, match="not installed"):
            await remove_skill("ghost-skill", pool=None)

    assert len(calls) == 1
    assert calls[0]["kind"] == "skill_frontmatter_parse_failed"


# ---------------------------------------------------------------------------
# topic_sources.codebase.CodebaseSource.extract — embed-dispatch failures
# ---------------------------------------------------------------------------


class _FakePool:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def fetch(self, *args: Any, **kwargs: Any):
        return self._rows


@pytest.mark.asyncio
async def test_codebase_extract_aggregates_embed_failures_into_one_finding(
    monkeypatch,
):
    calls = _capture(monkeypatch)
    embed_mock = AsyncMock(side_effect=[
        Exception("provider down"), Exception("provider down"), [0.1] * 768,
    ])
    pool = _FakePool([])

    with patch(
        "services.llm_providers.dispatcher.dispatch_embed", embed_mock,
    ):
        topics = await CodebaseSource().extract(
            pool=pool,
            config={"seed_queries": ["q1", "q2", "q3"]},
        )

    # Non-blocking — the pass completes (empty result here, but no crash).
    assert topics == []
    # Visible + aggregated — ONE finding covering both failed queries.
    assert len(calls) == 1
    assert calls[0]["kind"] == "codebase_seed_query_embed_failed"
    assert calls[0]["dedup_key"] == "codebase_seed_query_embed_failed"
    assert "2 of 3" in calls[0]["title"]
