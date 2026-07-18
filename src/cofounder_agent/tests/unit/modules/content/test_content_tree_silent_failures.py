"""Unit tests — ``modules/content`` best-effort-failure visibility.

OLD-debt burn-down (batch 28) — the ``modules/content`` tree, 31 sites across
24 files. This tree was in noticeably better shape than the ones before it:
most handlers already carried an inline ``# noqa: BLE001 — <reason>`` rationale,
so 29 of the 31 sites were annotated ``# silent-ok`` rather than changed.

Two were genuine gaps, and both share a shape the whole burn-down keeps
finding: **a fallback that succeeds loudly enough to look like success.**

``_resolve_writer_models`` (ai_content_generator)
    Builds the ordered writer-model candidate list — task-row override, then
    ``pipeline_writer_model``, then ``pipeline_fallback_model``. Its docstring
    is explicit that an *empty* result is the caller's fail-loud condition, so
    total failure was already covered. The gap was **partial** failure: when the
    ``pipeline_writer_model`` read raises but ``pipeline_fallback_model``
    succeeds, the list is non-empty, nothing fails loud, and every post gets
    written by the fallback model instead of the operator's pin. That breaks the
    documented diagnostic trail in ``feedback_model_selection`` ("QA rejects
    everything → check ``pipeline_writer_model``"): the operator checks the pin,
    finds it correctly set, and has no way to learn it was never read.

``_upload_to_r2_with_fallback`` (atoms/_image_helpers)
    On R2 upload failure ``img_url`` keeps the local temp path, which the next
    statement rewrites to ``/images/generated/<file>``. That path resolves on
    the worker and **only** on the worker, so every in-process check passes:
    ``url_validation`` runs at graph position ~12, before the image block
    (13–17) injects anything, and ``qa.vision`` fetches from the worker itself
    where the file genuinely exists. The post ships, and the first observer of
    the broken image is a public reader — with one ``logger.debug`` line as the
    entire trail.

Both fixes are ``logger.warning``, not ``emit_finding``: these run on the hot
writer/image path where the surrounding handlers are already documented as
"never block the pipeline", and Loki carries WARNING even when the DB that
backs findings is the thing that broke.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _RaisingOnKey:
    """SiteConfig double whose ``get`` raises for one specific key."""

    def __init__(self, raising_key: str, values: dict[str, str]):
        self._raising_key = raising_key
        self._values = values
        self.reads: list[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        self.reads.append(key)
        if key == self._raising_key:
            raise RuntimeError(f"settings read blew up for {key}")
        return self._values.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        """The AIContentGenerator ctor reads one int setting before we
        reach the method under test; only ``get`` is the seam we exercise."""
        return default


class TestWriterModelPartialResolveIsVisible:
    """A writer-pin read that fails while the fallback succeeds must warn."""

    @pytest.mark.asyncio
    async def test_writer_pin_read_failure_warns(self, caplog):
        from modules.content.ai_content_generator import AIContentGenerator

        sc = _RaisingOnKey(
            "pipeline_writer_model",
            {"pipeline_fallback_model": "ollama/qwen3-8b"},
        )
        gen = AIContentGenerator(site_config=sc)

        with caplog.at_level(logging.WARNING):
            ordered = await gen._resolve_writer_models(None, pool=None)

        # Behaviour is preserved: the fallback still resolves, so the
        # pipeline is not blocked by its own telemetry.
        assert ordered == ["qwen3-8b"]

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, (
            "pipeline_writer_model read failed but the fallback resolved, so "
            "nothing fails loud — the operator's pin was silently ignored and "
            "the post is written by the fallback model with no WARNING trail"
        )
        blob = " ".join(r.getMessage() for r in warnings)
        assert "pipeline_writer_model" in blob
        assert "qwen3-8b" in blob, (
            "the warning must name the model actually used, otherwise the "
            "operator cannot tell which pin the pipeline fell through to"
        )

    @pytest.mark.asyncio
    async def test_fallback_pin_read_failure_warns(self, caplog):
        from modules.content.ai_content_generator import AIContentGenerator

        sc = _RaisingOnKey(
            "pipeline_fallback_model",
            {"pipeline_writer_model": "ollama/gemma-4-31B-it-qat"},
        )
        gen = AIContentGenerator(site_config=sc)

        with caplog.at_level(logging.WARNING):
            ordered = await gen._resolve_writer_models(None, pool=None)

        assert ordered == ["gemma-4-31B-it-qat"]
        blob = " ".join(
            r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )
        assert "pipeline_fallback_model" in blob, (
            "losing the backstop shrinks the retry ladder to a single model — "
            "the next dispatch failure becomes fatal with no prior warning"
        )

    @pytest.mark.asyncio
    async def test_clean_reads_do_not_warn(self, caplog):
        """The happy path must stay quiet — no WARNING-level noise per post."""
        from modules.content.ai_content_generator import AIContentGenerator

        sc = _RaisingOnKey(
            "__never__",
            {
                "pipeline_writer_model": "ollama/gemma-4-31B-it-qat",
                "pipeline_fallback_model": "ollama/qwen3-8b",
            },
        )
        gen = AIContentGenerator(site_config=sc)

        with caplog.at_level(logging.WARNING):
            ordered = await gen._resolve_writer_models(None, pool=None)

        assert ordered == ["gemma-4-31B-it-qat", "qwen3-8b"]
        assert not [
            r for r in caplog.records if r.levelno >= logging.WARNING
        ], "clean settings reads must not warn once per generated post"


class TestR2UploadFallbackIsVisible:
    """A failed R2 upload leaves a worker-local URL — that must warn."""

    @pytest.mark.asyncio
    async def test_r2_failure_warns_with_public_reader_impact(
        self, caplog, monkeypatch,
    ):
        from modules.content.atoms import _image_helpers

        class _BoomService:
            def __init__(self, **_kw: Any) -> None:
                raise RuntimeError("R2 credentials rejected")

        monkeypatch.setattr(
            "services.r2_upload_service.R2UploadService", _BoomService,
        )

        tmp = "/data/glad-labs-generated-images/inline-abc123.png"
        with caplog.at_level(logging.WARNING):
            url = await _image_helpers._upload_to_r2_with_fallback(
                tmp, site_config=object(),
            )

        # Behaviour preserved: still returns the worker-serve rewrite.
        assert url == "/images/generated/inline-abc123.png"

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, (
            "R2 upload failed and the post now carries a worker-local image "
            "path; url_validation runs before the image block and qa.vision "
            "resolves it on the worker, so no in-process check catches this — "
            "a public reader sees a broken image with only a debug line"
        )
        blob = " ".join(r.getMessage() for r in warnings)
        assert "R2" in blob
        assert "/images/generated/inline-abc123.png" in blob, (
            "the warning must name the URL that shipped so the operator can "
            "find the affected post without re-deriving it"
        )

    @pytest.mark.asyncio
    async def test_successful_upload_does_not_warn(self, caplog, monkeypatch):
        from modules.content.atoms import _image_helpers

        class _OkService:
            def __init__(self, **_kw: Any) -> None:
                pass

            async def upload_to_r2(self, *_a: Any, **_kw: Any) -> str:
                return "https://cdn.example.com/images/inline/abc.webp"

        monkeypatch.setattr(
            "services.r2_upload_service.R2UploadService", _OkService,
        )
        monkeypatch.setattr(_image_helpers.os, "remove", lambda _p: None)

        with caplog.at_level(logging.WARNING):
            url = await _image_helpers._upload_to_r2_with_fallback(
                "/tmp/x.png", site_config=object(),
            )

        assert url == "https://cdn.example.com/images/inline/abc.webp"
        assert not [
            r for r in caplog.records if r.levelno >= logging.WARNING
        ], "a successful upload must not warn once per inline image"


def _capture(monkeypatch) -> list[dict]:
    """Patch ``utils.findings.emit_finding`` and collect the calls.

    Every site imports ``emit_finding`` function-locally, so a fresh
    ``from utils.findings import emit_finding`` resolves the *current*
    attribute on the source module on each call. A module-level import in the
    site would bind a private copy at import time and this patch would miss it.
    """
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


class _ExplodingCacheGenerator:
    """A content generator whose internal-links cache raises when read."""

    @property
    def _internal_links_cache(self):
        raise RuntimeError("links cache unreadable")


class TestAtomSideTwinsOfWriterCoreGaps:
    """The atom copies of two helpers batch 1 already fixed in writer_core.

    Batch 1 de-silenced ``_build_real_slug_allowlist`` and
    ``_fetch_existing_titles`` in ``modules/content/writer_core.py``. Both
    helpers have a second, independent implementation inside the *atoms* —
    which is what actually runs on the graph_def path — and those copies were
    left silent. Same mechanism, same consequence, different file.
    """

    @pytest.mark.asyncio
    async def test_normalize_draft_allowlist_failure_is_visible(self, monkeypatch):
        """An empty allowlist makes link-scrubbing maximally aggressive."""
        from modules.content.atoms.content_normalize_draft import run

        findings = _capture(monkeypatch)
        await run({
            "content": "# T\n\nBody with [a link](/posts/real-slug).\n",
            "title": "T",
            "_content_generator": _ExplodingCacheGenerator(),
        })

        assert findings, (
            "the real-slug allowlist came back empty because the cache read "
            "raised, so scrub_fabricated_links now treats every internal link "
            "as fabricated and deletes legitimate ones — silently"
        )
        assert any(
            "slug" in (f.get("kind", "") + f.get("title", "")).lower()
            for f in findings
        )

    @pytest.mark.asyncio
    async def test_title_history_failure_is_visible(self, monkeypatch):
        """Losing the avoidance list lets a duplicate title ship unnoticed."""
        from modules.content.atoms.content_generate_title import (
            _fetch_existing_titles,
        )

        findings = _capture(monkeypatch)

        class _Boom:
            @property
            def pool(self):
                raise RuntimeError("pool unavailable")

        result = await _fetch_existing_titles(_Boom())

        assert result == "", "behaviour preserved: still returns the empty list"
        assert findings, (
            "the recent-titles query failed, so the title prompt gets no "
            "avoidance list and the diversity check is skipped entirely — a "
            "near-duplicate title can ship with nothing on a board to say why"
        )


class TestCompileMetaSilentLosses:
    """compile_meta runs at graph position ~36 — after every QA rail."""

    @pytest.mark.asyncio
    async def test_sources_section_failure_is_visible(self, caplog, monkeypatch):
        from modules.content.atoms import content_compile_meta

        def _boom(*_a: Any, **_kw: Any):
            raise RuntimeError("citation verifier exploded")

        monkeypatch.setattr("services.citation_verifier.extract_urls", _boom)

        with caplog.at_level(logging.WARNING):
            out = await content_compile_meta.run({
                "content": "# T\n\nSee https://example.com/x for detail.\n",
                "topic": "T",
                "seo_title": "T",
            })

        assert out.get("content"), "behaviour preserved: content still compiles"
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, (
            "the Sources section was silently dropped from the finished post. "
            "compile_meta runs after the whole QA block (nodes 18-30), so no "
            "rail re-checks the body — the post ships without its citations"
        )
        assert "ources" in " ".join(r.getMessage() for r in warnings)

    @pytest.mark.asyncio
    async def test_preview_url_failure_is_visible(self, caplog):
        from modules.content.atoms import content_compile_meta

        class _BoomConfig:
            def get(self, *_a: Any, **_kw: Any):
                raise RuntimeError("settings read failed")

        with caplog.at_level(logging.WARNING):
            out = await content_compile_meta.run({
                "content": "# T\n\nBody.\n",
                "topic": "T",
                "seo_title": "T",
                "site_config": _BoomConfig(),
            })

        assert out.get("preview_token"), "behaviour preserved: token still issued"
        assert [r for r in caplog.records if r.levelno >= logging.WARNING], (
            "the operator's preview link is now empty. Every post needs "
            "per-post sign-off, and the reviewer just lost the link they "
            "review it through — with no indication why"
        )


class TestQaAggregateAuditVisibility:
    """The QA Rails dashboard is powered entirely by these audit rows."""

    @pytest.mark.asyncio
    async def test_qa_pass_audit_write_failure_warns(self, caplog, monkeypatch):
        from modules.content.atoms import qa_aggregate

        class _BoomAudit:
            def write_bg(self, *_a: Any, **_kw: Any):
                raise RuntimeError("audit sink down")

        class _Platform:
            config = None
            audit = _BoomAudit()

        with caplog.at_level(logging.WARNING):
            await qa_aggregate.run({
                "platform": _Platform(),
                "qa_rail_reviews": [
                    {"reviewer": "programmatic", "approved": True, "score": 90},
                ],
                "task_id": "abc12345",
                "content": "# T\n\nBody.\n",
            })

        blob = " ".join(
            r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )
        assert "qa_pass_completed" in blob, (
            "the qa_pass_completed row is the ONLY source for the QA Rails "
            "dashboard — losing it silently blanks per-reviewer pass-rate and "
            "score distribution, and a blank panel reads as 'no runs' rather "
            "than 'writes are failing'"
        )

    @pytest.mark.asyncio
    async def test_gate_counter_write_failure_warns(self, caplog, monkeypatch):
        """Sibling of the audit row above — same file, same failure family.

        The in-code comment on this handler documents the symptom it hides:
        when the counters froze at 0 (poindexter#553) the operator dashboard
        showed every gate as ``last_run_at=NEVER``. A silent regression here
        reproduces that exact display with only a debug line to explain it.
        """
        from modules.content.atoms import qa_aggregate

        async def _boom(*_a: Any, **_kw: Any):
            raise RuntimeError("qa_gates counter write failed")

        monkeypatch.setattr(
            "services.qa_gates_db_writer.record_chain_run", _boom,
        )

        class _OkAudit:
            def write_bg(self, *_a: Any, **_kw: Any):
                return None

        class _Platform:
            config = None
            audit = _OkAudit()

        class _Db:
            """resolve_pool reads state['database_service'].pool, not
            state['pool'] — a bare pool key never reaches the counter path."""
            pool = object()

        with caplog.at_level(logging.WARNING):
            await qa_aggregate.run({
                "platform": _Platform(),
                "database_service": _Db(),
                "qa_rail_reviews": [
                    {"reviewer": "programmatic", "approved": True, "score": 90},
                ],
                "task_id": "abc12345",
                "content": "# T\n\nBody.\n",
            })

        blob = " ".join(
            r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )
        assert "qa_gates" in blob, (
            "the qa_gates run counters silently stopped advancing, which is "
            "the poindexter#553 symptom verbatim: the operator dashboard "
            "shows every gate as last_run_at=NEVER, indistinguishable from "
            "'QA never ran'"
        )


class TestPersistTaskRevisionVisibility:
    """content_revisions is the audit trail for what the pipeline produced."""

    @pytest.mark.asyncio
    async def test_final_snapshot_failure_is_visible(self, monkeypatch):
        from modules.content.atoms import content_persist_task

        findings = _capture(monkeypatch)

        async def _boom(*_a: Any, **_kw: Any):
            raise RuntimeError("revisions table unavailable")

        monkeypatch.setattr(
            "services.content_revisions_logger.log_revision", _boom,
        )

        class _Db:
            pool = object()

            async def update_task_status_guarded(self, **_kw: Any):
                return "ok"

            async def update_task(self, **_kw: Any):
                return None

        await content_persist_task.run({
            "task_id": "abc12345",
            "database_service": _Db(),
            "content": "# T\n\nBody.\n",
            "topic": "T",
            "seo_title": "T",
        })

        assert findings, (
            "the finalized draft never reached content_revisions, so the "
            "revision history silently ends at the previous snapshot and any "
            "later diff compares against the wrong baseline"
        )


class TestQaPersistLearningSignalVisibility:
    """The sibling write in this same function already emits a finding."""

    @pytest.mark.asyncio
    async def test_model_performance_outcome_failure_is_visible(
        self, monkeypatch,
    ):
        from modules.content.atoms import _qa_persist

        # NOTE the different patch target from every other test here.
        # ``_qa_persist`` imports ``emit_finding`` at MODULE level, so it binds
        # its own copy at import time and patching ``utils.findings`` would
        # never be seen. (That module-level import is also why the fix there
        # must NOT add a function-local one — doing so makes the name local to
        # the whole function and UnboundLocalErrors the earlier uses.)
        findings: list[dict] = []
        monkeypatch.setattr(
            _qa_persist, "emit_finding", lambda **kw: findings.append(kw),
        )

        class _Db:
            pool = object()

            async def mark_model_performance_outcome(self, *_a: Any, **_kw: Any):
                raise RuntimeError("outcome write failed")

            def __getattr__(self, _name: str):
                async def _noop(*_a: Any, **_kw: Any):
                    return None
                return _noop

        await _qa_persist.persist_qa_reject(
            _Db(),
            task_id="abc12345",
            reason="test",
            final_score=10.0,
            content="body",
            title="T",
            qa_feedback="fb",
            models_used_by_phase={},
        )

        kinds = " ".join(f.get("kind", "") for f in findings)
        assert "model_performance" in kinds or "outcome" in kinds, (
            "the reject never reached the router's learning signal, so the "
            "model that produced it keeps its score and gets picked again. "
            "The pipeline_versions write directly above this one already "
            "emits a finding on failure — this sibling was left silent"
        )
