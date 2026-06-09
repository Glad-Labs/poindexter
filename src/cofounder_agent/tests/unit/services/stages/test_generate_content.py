"""Unit tests for ``services/stages/generate_content.py``.

The stage has ~10 external dependencies (content generator, GPU lock,
research service, RAG, site_config, audit_log, ...). We patch them all
at the stage's import site so the tests exercise the stage's own control
flow — the what-to-call-when-and-in-what-order logic — not the helpers.

Helper functions (_strip_leaked_image_prompts, _extract_caller_research,
_self_review_enabled) are covered separately since they're pure.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from modules.content.stages.generate_content import (
    GenerateContentStage,
    _extract_caller_research,
    _self_review_enabled,
    _strip_leaked_image_prompts,
)

# ---------------------------------------------------------------------------
# Pure helpers — no patching required
# ---------------------------------------------------------------------------


class TestStripLeakedImagePrompts:
    def test_removes_italic_scene_descriptions(self):
        body = "Intro paragraph.\n\n*A dramatic scene of rolling hills at dusk with cinematic lighting across the frame*\n\nOutro."
        out = _strip_leaked_image_prompts(body)
        assert "dramatic scene" not in out
        assert "Intro paragraph." in out
        assert "Outro." in out

    def test_removes_image_placeholders(self):
        body = "Before [IMAGE-1: cat on sofa] after [FIGURE: graph here] end."
        out = _strip_leaked_image_prompts(body)
        assert "IMAGE" not in out
        assert "FIGURE" not in out
        assert "Before" in out
        assert "end." in out

    def test_collapses_resulting_blank_lines(self):
        body = "A\n\n\n\nB"
        out = _strip_leaked_image_prompts(body)
        assert out == "A\n\nB"

    def test_leaves_normal_prose_alone(self):
        body = "Just some prose with *emphasis* and a single sentence."
        out = _strip_leaked_image_prompts(body)
        assert out == body


class TestExtractCallerResearch:
    def test_reads_from_task_metadata_json_string(self):
        row = {"task_metadata": '{"research_context": "from caller"}'}
        assert _extract_caller_research(row) == "from caller"

    def test_reads_from_metadata_jsonb_dict(self):
        row = {"metadata": {"research_context": "from jsonb"}}
        assert _extract_caller_research(row) == "from jsonb"

    def test_reads_top_level_field(self):
        row = {"research_context": "top level"}
        assert _extract_caller_research(row) == "top level"

    def test_task_metadata_wins_over_others(self):
        row = {
            "task_metadata": '{"research_context": "winner"}',
            "metadata": {"research_context": "loser"},
            "research_context": "loser2",
        }
        assert _extract_caller_research(row) == "winner"

    def test_malformed_task_metadata_falls_through(self):
        row = {
            "task_metadata": "{not json",
            "research_context": "fallback",
        }
        assert _extract_caller_research(row) == "fallback"

    def test_returns_empty_when_nothing_present(self):
        assert _extract_caller_research({}) == ""


class TestSelfReviewEnabled:
    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("1", True), ("yes", True),
        ("TRUE", True), ("Yes", True),
        ("false", False), ("no", False), ("0", False), ("", False),
    ])
    def test_parses_flag(self, raw: str, expected: bool):
        # _self_review_enabled now takes site_config as a parameter
        # (glad-labs-stack#330) instead of importing the singleton.
        sc = SimpleNamespace(get=lambda _k, _d: raw)
        assert _self_review_enabled(sc) is expected


# ---------------------------------------------------------------------------
# Stage.execute — full flow with every external dep patched
# ---------------------------------------------------------------------------


class _FakeDb:
    def __init__(self, task_row: dict[str, Any] | None = None):
        self._task_row = task_row or {}
        self.updates: list[dict[str, Any]] = []
        self.costs: list[dict[str, Any]] = []
        self.pool = _FakePool()

    async def get_task(self, _task_id: str) -> dict[str, Any] | None:
        return self._task_row

    async def update_task(self, task_id: str, updates: dict[str, Any]) -> None:
        self.updates.append({"task_id": task_id, **updates})

    async def log_cost(self, cost_log: dict[str, Any]) -> None:
        self.costs.append(cost_log)


class _FakePoolConn:
    async def fetch(self, *_args: Any) -> list[dict[str, Any]]:
        return [{"title": "Old Title 1"}, {"title": "Old Title 2"}]


class _FakePoolCtx:
    async def __aenter__(self):
        return _FakePoolConn()

    async def __aexit__(self, *_exc):
        return None


class _FakePool:
    def acquire(self) -> _FakePoolCtx:
        return _FakePoolCtx()


@asynccontextmanager
async def _no_gpu_lock(*_args, **_kwargs):
    yield None


def _patch_everything():
    """Returns a tuple of patch context managers wrapping every external dep."""
    return [
        patch(
            "modules.content.ai_content_generator.get_content_generator",
            return_value=SimpleNamespace(
                _internal_links_cache=[
                    '- "Real Post" -> https://www.gladlabs.io/posts/real-slug'
                ],
                generate_blog_post=AsyncMock(return_value=(
                    # Realistic-length body so it clears the writer_min_draft_chars
                    # guard (default 200) added for poindexter#691 — a 44-char
                    # fixture would now (correctly) be treated as a too-short
                    # writer failure.
                    "# A Realistic Draft\n\nThis is a generated blog post body with "
                    "enough substance to comfortably clear the minimum-draft-length "
                    "guard the pipeline now enforces. It has a heading, multiple "
                    "sentences, and well over two hundred characters so the happy-path "
                    "stage flow runs end to end without tripping the empty/too-short "
                    "writer guard.",
                    "glm-4.7-5090",
                    {
                        "models_used_by_phase": {"writer": "glm-4.7-5090"},
                        "model_selection_log": {"picked": "glm-4.7-5090"},
                        "cost_log": {
                            "cost_usd": 0.0,
                            "provider": "ollama",
                            "model": "glm-4.7-5090",
                        },
                    },
                )),
            ),
        ),
        patch("services.model_preferences.parse_model_preferences",
              return_value=("glm-4.7-5090", "ollama")),
        patch("services.writing_style_context.build_writing_style_context",
              AsyncMock(return_value="style context")),
        patch("services.research_context.build_rag_context",
              AsyncMock(return_value="rag context")),
        patch("services.title_generation.generate_canonical_title",
              AsyncMock(return_value="Generated Title")),
        patch("services.title_generation.check_title_originality",
              AsyncMock(return_value={
                  "is_original": True, "max_similarity": 0.1, "similar_titles": [],
              })),
        patch("services.text_utils.normalize_text",
              side_effect=lambda x: x),
        patch("services.text_utils.scrub_fabricated_links",
              side_effect=lambda x, **_kw: x),
        patch("services.self_review.self_review_and_revise",
              AsyncMock(return_value=("revised text", {"revised": False, "contradictions_found": 0}))),
        patch("services.gpu_scheduler.gpu",
              SimpleNamespace(lock=_no_gpu_lock)),
        patch("services.research_service.ResearchService",
              return_value=SimpleNamespace(build_context=AsyncMock(return_value="auto research"))),
    ]


@pytest.mark.asyncio
class TestGenerateContentStageExecute:
    async def test_populates_context_and_persists(self):
        db = _FakeDb(task_row={"research_context": "caller-supplied"})
        ctx: dict[str, Any] = {
            "task_id": "t1",
            "topic": "AI trends",
            "style": "tech",
            "tone": "neutral",
            "target_length": 1200,
            "tags": ["AI"],
            "models_by_phase": {"writer": "glm-4.7-5090"},
            "database_service": db,
        }
        patches = _patch_everything()
        for p in patches:
            p.start()
        try:
            result = await GenerateContentStage().execute(ctx, {})
        finally:
            for p in reversed(patches):
                p.stop()

        assert result.ok is True
        # All the context_updates keys the downstream pipeline consumes
        assert result.context_updates["title"] == "Generated Title"
        assert result.context_updates["model_used"] == "glm-4.7-5090"
        assert result.context_updates["content_length"] > 0
        assert "content" in result.context_updates
        assert result.context_updates["stages"]["2_content_generated"] is True
        # Research context was threaded onto the context by the stage itself
        assert "caller-supplied" in ctx["research_context"]
        assert "auto research" in ctx["research_context"]
        assert "rag context" in ctx["research_context"]
        # Regression guard (Glad-Labs/poindexter#553): research_context MUST be
        # returned in context_updates, not only mutated onto the local context
        # dict. On the graph_def path (atom-cutover #355) make_stage_node merges
        # ONLY StageResult.context_updates back into the LangGraph state — a
        # bare context mutation is dropped, which starved the qa.ragas /
        # qa.deepeval grounding rails (they read state["research_context"] and
        # skipped 100% of the time). The two must stay in lockstep.
        assert "research_context" in result.context_updates
        assert result.context_updates["research_context"] == ctx["research_context"]
        assert "caller-supplied" in result.context_updates["research_context"]
        # DB got an update + a cost log
        assert len(db.updates) == 1
        assert db.updates[0]["status"] == "in_progress"
        assert db.updates[0]["title"] == "Generated Title"
        assert len(db.costs) == 1

    async def test_missing_task_id_returns_not_ok(self):
        result = await GenerateContentStage().execute({"database_service": object()}, {})
        assert result.ok is False
        assert "task_id" in result.detail

    async def test_missing_db_returns_not_ok(self):
        result = await GenerateContentStage().execute({"task_id": "x"}, {})
        assert result.ok is False
        assert "database_service" in result.detail

    async def test_title_originality_regenerates_when_duplicate(self):
        db = _FakeDb()
        ctx: dict[str, Any] = {
            "task_id": "t2",
            "topic": "AI trends",
            "style": "tech", "tone": "neutral", "target_length": 1200,
            "tags": [], "models_by_phase": {},
            "database_service": db,
        }
        # First originality check says not original; second check (on
        # regenerated title) says more original → stage should accept v2.
        originality_mock = AsyncMock(side_effect=[
            {"is_original": False, "max_similarity": 0.9, "similar_titles": ["Dup 1"]},
            {"is_original": True, "max_similarity": 0.1, "similar_titles": []},
        ])
        title_mock = AsyncMock(side_effect=["Title V1", "Title V2"])

        patches = _patch_everything()
        # Override the ones we care about
        for p in patches:
            p.start()
        try:
            with patch(
                "services.title_generation.check_title_originality",
                new=originality_mock,
            ), patch(
                "services.title_generation.generate_canonical_title",
                new=title_mock,
            ):
                result = await GenerateContentStage().execute(ctx, {})
        finally:
            for p in reversed(patches):
                p.stop()

        assert result.ok is True
        assert result.context_updates["title"] == "Title V2"
        assert originality_mock.await_count == 2
        assert title_mock.await_count == 2

    async def test_empty_content_fails_loud_marks_task_failed_and_emits_finding(self):
        """poindexter#691: an empty writer draft must FAIL THE TASK loud —
        a load-bearing terminal ``status='failed'`` write with a specific
        ``error_message`` + a ``finding`` — and still raise. Previously the
        raise was swallowed by the graph wrapper and the empty draft flowed
        into QA, surfacing as a misleading ``reviewer_count:0`` 0/100 reject
        instead of naming the real writer-empty cause.
        """
        db = _FakeDb()
        ctx: dict[str, Any] = {
            "task_id": "t3",
            "topic": "AI", "style": "", "tone": "",
            "target_length": 100, "tags": [], "models_by_phase": {},
            "database_service": db,
        }
        findings: list[dict[str, Any]] = []
        patches = _patch_everything()
        for p in patches:
            p.start()
        try:
            with patch(
                "modules.content.ai_content_generator.get_content_generator",
                return_value=SimpleNamespace(
                    _internal_links_cache=[],
                    generate_blog_post=AsyncMock(return_value=("", "glm-4.7-5090", {})),
                ),
            ), patch(
                "utils.findings.emit_finding",
                side_effect=lambda **kw: findings.append(kw),
            ):
                with pytest.raises(ValueError, match="empty draft"):
                    await GenerateContentStage().execute(ctx, {})
        finally:
            for p in reversed(patches):
                p.stop()

        # Load-bearing terminal failure write — the status sticks even though
        # the graph wrapper keeps running downstream nodes (matches the
        # qa.aggregate reject idiom). No happy-path in_progress write happens.
        assert db.updates, "expected a terminal status write before raising"
        last = db.updates[-1]
        assert last["status"] == "failed"
        assert "empty draft" in last["error_message"].lower()
        assert "glm-4.7-5090" in last["error_message"]
        # A finding surfaces it on the Findings dashboard / Discord.
        assert len(findings) == 1
        assert findings[0]["kind"] == "writer_empty_draft"
        assert findings[0]["severity"] == "warn"

    async def test_too_short_content_fails_loud(self):
        """poindexter#691: a draft below ``writer_min_draft_chars`` (default
        200) is treated as a writer failure too — a real canonical_blog post
        is never a single sentence; sub-threshold output is broken generation.
        """
        db = _FakeDb()
        ctx: dict[str, Any] = {
            "task_id": "t4",
            "topic": "AI", "style": "", "tone": "",
            "target_length": 1200, "tags": [], "models_by_phase": {},
            "database_service": db,
        }
        findings: list[dict[str, Any]] = []
        patches = _patch_everything()
        for p in patches:
            p.start()
        try:
            with patch(
                "modules.content.ai_content_generator.get_content_generator",
                return_value=SimpleNamespace(
                    _internal_links_cache=[],
                    generate_blog_post=AsyncMock(
                        return_value=("Too short to be a real post.", "glm-4.7-5090", {}),
                    ),
                ),
            ), patch(
                "utils.findings.emit_finding",
                side_effect=lambda **kw: findings.append(kw),
            ):
                with pytest.raises(ValueError, match="too-short draft"):
                    await GenerateContentStage().execute(ctx, {})
        finally:
            for p in reversed(patches):
                p.stop()

        assert db.updates[-1]["status"] == "failed"
        assert "too-short draft" in db.updates[-1]["error_message"].lower()
        assert len(findings) == 1
        assert findings[0]["kind"] == "writer_empty_draft"
