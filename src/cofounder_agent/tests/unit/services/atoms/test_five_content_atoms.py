"""Behavior tests for five live-path content atoms with zero prior tests.

Covers (poindexter#720):
- content.generate_title
- content.check_title_originality
- content.evaluate_auto_publish
- content.generate_images
- content.record_pipeline_version  (behavior tests beyond the #693 regression)

No module-level asyncio mark: ``asyncio_mode = "auto"`` (pyproject.toml)
auto-marks coroutine tests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_db(pool=None):
    db = MagicMock()
    db.pool = pool or MagicMock()
    db.update_task = AsyncMock(return_value=None)
    db.update_task_status_guarded = AsyncMock(return_value="ok")
    return db


def _base_state(**extra):
    state = {
        "task_id": "task-720-test",
        "topic": "python async patterns",
        "tags": ["asyncio", "python"],
        "content": "## Intro\n\nAsync Python enables high concurrency.\n\n## Details\n\nUse await properly.",
        "title": "Async Python Patterns",
        "database_service": _make_db(),
        "site_config": MagicMock(),
    }
    state.update(extra)
    return state


def _originality_result(is_original=True, max_similarity=0.1, similar_titles=None):
    return {
        "is_original": is_original,
        "similar_titles": similar_titles or [],
        "max_similarity": max_similarity,
        "external_verbatim_match": False,
        "external_near_match": False,
        "external_penalty": 0,
        "external_matches": [],
        "external_fail_open": False,
    }


# ---------------------------------------------------------------------------
# TestContentGenerateTitle
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentGenerateTitle:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_generate_title import ATOM_META
        assert ATOM_META.name == "content.generate_title"
        assert "content" in ATOM_META.requires
        assert "task_id" in ATOM_META.requires
        assert "title" in ATOM_META.produces
        assert "title_originality" in ATOM_META.produces
        assert "llm_call" in ATOM_META.side_effects
        assert "db_write" in ATOM_META.side_effects
        assert ATOM_META.cost_class == "api"

    async def test_generates_title_from_draft(self, monkeypatch):
        """Happy path: LLM returns a title, originality check passes."""
        from modules.content.atoms import content_generate_title as atom

        expected_title = "Python Async Concurrency Patterns"
        orig = _originality_result(is_original=True)

        monkeypatch.setattr(
            "services.title_generation.generate_canonical_title",
            AsyncMock(return_value=expected_title),
        )
        monkeypatch.setattr(
            "services.title_generation.choose_canonical_title",
            lambda topic, content, llm_title, **kw: llm_title,
        )
        monkeypatch.setattr(
            "services.title_generation.check_title_originality",
            AsyncMock(return_value=orig),
        )

        # Stub pool.acquire so _fetch_existing_titles succeeds
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=None)))
        db = _make_db(pool=mock_pool)

        state = _base_state(database_service=db)
        out = await atom.run(state)

        assert out["title"] == expected_title
        assert out["title_originality"]["is_original"] is True
        db.update_task.assert_called_once()

    async def test_empty_content_returns_empty(self, monkeypatch):
        """Empty content → atom is a no-op (no LLM call, empty return)."""
        from modules.content.atoms import content_generate_title as atom

        gen_mock = AsyncMock(return_value="Some Title")
        monkeypatch.setattr("services.title_generation.generate_canonical_title", gen_mock)

        state = _base_state(content="")
        out = await atom.run(state)

        assert out == {}
        gen_mock.assert_not_called()

    async def test_regenerates_when_title_not_original(self, monkeypatch):
        """If the first title is not original, a second title is generated."""
        from modules.content.atoms import content_generate_title as atom

        titles = ["Duplicate Title", "New Fresh Title"]
        title_iter = iter(titles)

        async def _gen(*a, **kw):
            return next(title_iter)

        orig_fail = _originality_result(
            is_original=False, max_similarity=0.85,
            similar_titles=["Duplicate Title Existing"],
        )
        orig_pass = _originality_result(is_original=True, max_similarity=0.1)

        checks = iter([orig_fail, orig_pass])

        async def _check(title, *, site_config):
            return next(checks)

        monkeypatch.setattr("services.title_generation.generate_canonical_title", _gen)
        monkeypatch.setattr("services.title_generation.choose_canonical_title", lambda t, c, l, **kw: l)
        monkeypatch.setattr("services.title_generation.check_title_originality", _check)

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=None)))
        db = _make_db(pool=mock_pool)

        state = _base_state(database_service=db)
        out = await atom.run(state)

        # The more-original second title wins.
        assert out["title"] == "New Fresh Title"
        assert out["title_originality"]["max_similarity"] == 0.1

    async def test_db_failure_does_not_raise(self, monkeypatch):
        """DB update failure is non-critical; atom still returns title."""
        from modules.content.atoms import content_generate_title as atom

        expected_title = "Test Title"
        orig = _originality_result()

        monkeypatch.setattr("services.title_generation.generate_canonical_title", AsyncMock(return_value=expected_title))
        monkeypatch.setattr("services.title_generation.choose_canonical_title", lambda t, c, l, **kw: l)
        monkeypatch.setattr("services.title_generation.check_title_originality", AsyncMock(return_value=orig))

        db = _make_db()
        db.pool = None  # causes _fetch_existing_titles to return ""
        db.update_task = AsyncMock(side_effect=RuntimeError("db down"))

        state = _base_state(database_service=db)
        out = await atom.run(state)

        assert out["title"] == expected_title  # still returns the title
        assert "title_originality" in out


# ---------------------------------------------------------------------------
# TestContentCheckTitleOriginality
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentCheckTitleOriginality:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_check_title_originality import ATOM_META
        assert ATOM_META.name == "content.check_title_originality"
        assert "title" in ATOM_META.requires
        assert "title_originality" in ATOM_META.produces
        assert ATOM_META.idempotent is True
        assert ATOM_META.cost_class == "compute"

    async def test_returns_originality_for_unique_title(self, monkeypatch):
        """Unique title: originality check passes, result surfaced on state."""
        from modules.content.atoms import content_check_title_originality as atom

        orig = _originality_result(is_original=True)
        monkeypatch.setattr(
            "services.title_generation.check_title_originality",
            AsyncMock(return_value=orig),
        )

        state = _base_state()
        out = await atom.run(state)

        assert out["title_originality"]["is_original"] is True
        assert out["title_originality"]["max_similarity"] == 0.1

    async def test_returns_duplicate_flag_for_similar_title(self, monkeypatch):
        """Duplicate title: is_original=False surfaced on state."""
        from modules.content.atoms import content_check_title_originality as atom

        orig = _originality_result(is_original=False, max_similarity=0.9, similar_titles=["Existing Post Title"])
        monkeypatch.setattr(
            "services.title_generation.check_title_originality",
            AsyncMock(return_value=orig),
        )

        state = _base_state(title="Existing Post Title Variant")
        out = await atom.run(state)

        assert out["title_originality"]["is_original"] is False
        assert out["title_originality"]["max_similarity"] == 0.9
        assert len(out["title_originality"]["similar_titles"]) == 1

    async def test_skips_when_title_originality_already_set(self, monkeypatch):
        """If title_originality already on state, skip the redundant check."""
        from modules.content.atoms import content_check_title_originality as atom

        check_mock = AsyncMock(return_value=_originality_result())
        monkeypatch.setattr("services.title_generation.check_title_originality", check_mock)

        existing = _originality_result(is_original=True, max_similarity=0.05)
        state = _base_state(title_originality=existing)
        out = await atom.run(state)

        assert out == {}  # no-op
        check_mock.assert_not_called()

    async def test_empty_title_returns_empty(self, monkeypatch):
        """Empty title → atom is a no-op."""
        from modules.content.atoms import content_check_title_originality as atom

        check_mock = AsyncMock(return_value=_originality_result())
        monkeypatch.setattr("services.title_generation.check_title_originality", check_mock)

        state = _base_state(title="")
        out = await atom.run(state)

        assert out == {}
        check_mock.assert_not_called()

    async def test_check_failure_returns_safe_default(self, monkeypatch):
        """Service exception → returns fail-open default (is_original=True)."""
        from modules.content.atoms import content_check_title_originality as atom

        async def _boom(title, *, site_config):
            raise RuntimeError("network error")

        monkeypatch.setattr("services.title_generation.check_title_originality", _boom)

        state = _base_state()
        out = await atom.run(state)

        assert out["title_originality"]["is_original"] is True
        assert out["title_originality"]["max_similarity"] == 0.0


# ---------------------------------------------------------------------------
# TestContentEvaluateAutoPublish
# ---------------------------------------------------------------------------


def _make_gate_decision(would_fire=False, dry_run=True, gate_state="disabled", reason="gate disabled"):
    from modules.content.auto_publish_gate import AutoPublishDecision
    return AutoPublishDecision(
        would_fire=would_fire,
        dry_run=dry_run,
        gate_state=gate_state,
        reason=reason,
        quality_score=0.0,
        threshold=-1.0,
        trailing_clean_runs=0,
        required_clean_runs=3,
    )


@pytest.mark.unit
class TestContentEvaluateAutoPublish:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_evaluate_auto_publish import ATOM_META
        assert ATOM_META.name == "content.evaluate_auto_publish"
        assert "task_id" in ATOM_META.requires
        assert "auto_publish_gate" in ATOM_META.produces
        assert ATOM_META.idempotent is True
        assert ATOM_META.cost_class == "free"

    async def test_returns_disabled_when_no_niche(self, monkeypatch):
        """Gate disabled when niche_slug absent → gate_state='disabled'."""
        from modules.content.atoms import content_evaluate_auto_publish as atom

        gate_decision = _make_gate_decision(would_fire=False, gate_state="disabled", reason="no niche_slug")
        monkeypatch.setattr(
            "modules.content.auto_publish_gate.evaluate",
            AsyncMock(return_value=gate_decision),
        )

        state = _base_state()  # no niche_slug
        out = await atom.run(state)

        assert out["auto_publish_gate"]["gate_state"] == "disabled"
        assert out["auto_publish_gate"]["would_fire"] is False

    async def test_returns_would_fire_when_threshold_met(self, monkeypatch):
        """Gate fires when quality_score >= threshold and niche opted in."""
        from modules.content.atoms import content_evaluate_auto_publish as atom
        from modules.content.auto_publish_gate import AutoPublishDecision

        gate_decision = AutoPublishDecision(
            would_fire=True,
            dry_run=False,
            gate_state="pass",
            reason="score >= threshold and trailing runs clean",
            quality_score=92.0,
            threshold=85.0,
            trailing_clean_runs=3,
            required_clean_runs=3,
        )
        monkeypatch.setattr(
            "modules.content.auto_publish_gate.evaluate",
            AsyncMock(return_value=gate_decision),
        )

        state = _base_state(quality_score=92.0, niche_slug="dev_diary")
        out = await atom.run(state)

        assert out["auto_publish_gate"]["would_fire"] is True
        assert out["auto_publish_gate"]["gate_state"] == "pass"
        assert out["auto_publish_gate"]["dry_run"] is False

    async def test_awaiting_approval_when_score_below_threshold(self, monkeypatch):
        """Quality score below threshold → gate blocks."""
        from modules.content.atoms import content_evaluate_auto_publish as atom
        from modules.content.auto_publish_gate import AutoPublishDecision

        gate_decision = AutoPublishDecision(
            would_fire=False,
            dry_run=True,
            gate_state="block_threshold",
            reason="score 60 < threshold 85",
            quality_score=60.0,
            threshold=85.0,
            trailing_clean_runs=0,
            required_clean_runs=3,
        )
        monkeypatch.setattr(
            "modules.content.auto_publish_gate.evaluate",
            AsyncMock(return_value=gate_decision),
        )

        state = _base_state(quality_score=60.0, niche_slug="dev_diary")
        out = await atom.run(state)

        assert out["auto_publish_gate"]["would_fire"] is False
        assert out["auto_publish_gate"]["gate_state"] == "block_threshold"

    async def test_missing_task_id_returns_empty(self, monkeypatch):
        """Missing task_id → atom returns empty without calling gate."""
        from modules.content.atoms import content_evaluate_auto_publish as atom

        eval_mock = AsyncMock()
        monkeypatch.setattr("modules.content.auto_publish_gate.evaluate", eval_mock)

        state = _base_state()
        state.pop("task_id")
        out = await atom.run(state)

        assert out == {}
        eval_mock.assert_not_called()

    async def test_gate_failure_returns_none_auto_publish_gate(self, monkeypatch):
        """Gate evaluation exception → auto_publish_gate=None (non-fatal)."""
        from modules.content.atoms import content_evaluate_auto_publish as atom

        async def _boom(*a, **kw):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr("modules.content.auto_publish_gate.evaluate", _boom)

        state = _base_state(niche_slug="dev_diary", quality_score=88.0)
        out = await atom.run(state)

        assert "auto_publish_gate" in out
        assert out["auto_publish_gate"] is None

    async def test_platform_audit_written_on_success(self, monkeypatch):
        """When platform is provided, audit.write_bg is called with gate decision."""
        from modules.content.atoms import content_evaluate_auto_publish as atom
        from plugins.fake_platform import FakePlatform

        gate_decision = _make_gate_decision(would_fire=False, gate_state="disabled")
        monkeypatch.setattr(
            "modules.content.auto_publish_gate.evaluate",
            AsyncMock(return_value=gate_decision),
        )

        platform = FakePlatform()
        state = _base_state(platform=platform, niche_slug="dev_diary")
        await atom.run(state)

        assert len(platform.audit.writes_bg) == 1
        written = platform.audit.writes_bg[0]
        assert written["event_type"] == "auto_publish_gate"
        assert "would_fire" in written["details"]


# ---------------------------------------------------------------------------
# TestContentGenerateImages
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentGenerateImages:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_generate_images import ATOM_META
        assert ATOM_META.name == "content.generate_images"
        assert "image_plans" in ATOM_META.requires
        assert "image_results" in ATOM_META.produces
        assert "gpu_call" in ATOM_META.side_effects
        assert "r2_upload" in ATOM_META.side_effects
        assert "db_write" in ATOM_META.side_effects
        assert ATOM_META.idempotent is False

    async def test_empty_plans_returns_empty_list(self):
        """No image plans → image_results=[] returned immediately."""
        from modules.content.atoms import content_generate_images as atom

        state = _base_state(image_plans=[])
        out = await atom.run(state)

        assert out == {"image_results": []}

    async def test_sdxl_path_stores_result(self, monkeypatch):
        """SDXL succeeds → image_results contains sdxl source entry."""
        from modules.content.atoms import content_generate_images as atom

        sdxl_url = "https://r2.example.com/img-001.png"

        monkeypatch.setattr(
            "modules.content.stages.replace_inline_images._try_sdxl",
            AsyncMock(return_value=sdxl_url),
        )
        monkeypatch.setattr(
            "modules.content.stages.replace_inline_images._try_pexels",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "modules.content.stages.replace_inline_images._record_inline_image_asset",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "services.image_service.get_image_service",
            MagicMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "services.alt_text.sanitize_alt_text",
            lambda alt, budget, topic: alt,
        )

        state = _base_state(
            image_plans=[{"num": "1", "desc": "a futuristic server room"}],
        )
        state["site_config"].get_int = MagicMock(return_value=120)

        out = await atom.run(state)

        assert len(out["image_results"]) == 1
        result = out["image_results"][0]
        assert result["num"] == "1"
        assert result["url"] == sdxl_url
        assert result["source"] == "sdxl"

    async def test_pexels_fallback_when_sdxl_fails(self, monkeypatch):
        """SDXL returns None → Pexels fallback provides the image."""
        from modules.content.atoms import content_generate_images as atom

        pexels_url = "https://images.pexels.com/photo.jpg"
        photographer = "Jane Doe"

        monkeypatch.setattr(
            "modules.content.stages.replace_inline_images._try_sdxl",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "modules.content.stages.replace_inline_images._try_pexels",
            AsyncMock(return_value=(pexels_url, photographer)),
        )
        monkeypatch.setattr(
            "modules.content.stages.replace_inline_images._record_inline_image_asset",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "services.image_service.get_image_service",
            MagicMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "services.alt_text.sanitize_alt_text",
            lambda alt, budget, topic: alt,
        )

        state = _base_state(
            image_plans=[{"num": "2", "desc": "mountain landscape"}],
        )
        state["site_config"].get_int = MagicMock(return_value=120)

        out = await atom.run(state)

        result = out["image_results"][0]
        assert result["url"] == pexels_url
        assert result["source"] == "pexels"
        assert "Jane Doe" in result["alt_text"]

    async def test_both_sources_fail_returns_none_url(self, monkeypatch):
        """Both SDXL and Pexels fail → entry has url=None, source='none'."""
        from modules.content.atoms import content_generate_images as atom

        monkeypatch.setattr(
            "modules.content.stages.replace_inline_images._try_sdxl",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "modules.content.stages.replace_inline_images._try_pexels",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "modules.content.stages.replace_inline_images._record_inline_image_asset",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "services.image_service.get_image_service",
            MagicMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "services.alt_text.sanitize_alt_text",
            lambda alt, budget, topic: alt,
        )

        state = _base_state(
            image_plans=[{"num": "3", "desc": "abstract concept"}],
        )
        state["site_config"].get_int = MagicMock(return_value=120)

        out = await atom.run(state)

        result = out["image_results"][0]
        assert result["url"] is None
        assert result["source"] == "none"

    async def test_multiple_plans_processed_independently(self, monkeypatch):
        """Each image plan produces a separate result entry."""
        from modules.content.atoms import content_generate_images as atom

        urls = ["https://r2.example.com/a.png", "https://r2.example.com/b.png"]
        url_iter = iter(urls)

        async def _sdxl(num, query, topic, *, site_config, task_id, platform):
            return next(url_iter, None)

        monkeypatch.setattr("modules.content.stages.replace_inline_images._try_sdxl", _sdxl)
        monkeypatch.setattr("modules.content.stages.replace_inline_images._try_pexels", AsyncMock(return_value=None))
        monkeypatch.setattr("modules.content.stages.replace_inline_images._record_inline_image_asset", AsyncMock())
        monkeypatch.setattr("services.image_service.get_image_service", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr("services.alt_text.sanitize_alt_text", lambda alt, budget, topic: alt)

        state = _base_state(
            image_plans=[
                {"num": "1", "desc": "server room"},
                {"num": "2", "desc": "code on screen"},
            ],
        )
        state["site_config"].get_int = MagicMock(return_value=120)

        out = await atom.run(state)

        assert len(out["image_results"]) == 2
        assert out["image_results"][0]["num"] == "1"
        assert out["image_results"][1]["num"] == "2"


# ---------------------------------------------------------------------------
# TestContentRecordPipelineVersion  (behavior tests beyond #693 regression)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentRecordPipelineVersionBehavior:
    def test_atom_meta_contract(self):
        from modules.content.atoms.content_record_pipeline_version import ATOM_META
        assert ATOM_META.name == "content.record_pipeline_version"
        assert "task_id" in ATOM_META.requires
        assert "content" in ATOM_META.requires
        assert "stages" in ATOM_META.produces
        assert ATOM_META.idempotent is True
        assert ATOM_META.cost_class == "free"
        assert "db_write" in ATOM_META.side_effects
        assert ATOM_META.retry.max_attempts == 3

    async def test_upserts_version_and_sets_stage_flag(self, monkeypatch):
        """Happy path: PipelineDB.upsert_version called, stages flag set."""
        from modules.content.atoms import content_record_pipeline_version as atom

        upsert_calls: list[tuple] = []

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, data):
                upsert_calls.append((task_id, data))

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)

        state = _base_state(
            content="# Post\n\nBody text.",
            title="The Real Title",
            quality_score=88.0,
            seo_title="SEO Title",
            seo_description="SEO description.",
            seo_keywords=["python", "async"],
        )
        out = await atom.run(state)

        assert out["stages"]["5_version_recorded"] is True
        assert len(upsert_calls) == 1
        task_id, data = upsert_calls[0]
        assert task_id == "task-720-test"
        assert data["title"] == "The Real Title"
        assert data["quality_score"] == 88.0
        assert data["seo_title"] == "SEO Title"

    async def test_seo_keywords_list_joined_to_string(self, monkeypatch):
        """List seo_keywords are joined as CSV before being upserted."""
        from modules.content.atoms import content_record_pipeline_version as atom

        upsert_calls: list[tuple] = []

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, data):
                upsert_calls.append((task_id, data))

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)

        state = _base_state(seo_keywords=["python", "async", "concurrency"])
        await atom.run(state)

        _, data = upsert_calls[0]
        assert data["seo_keywords"] == "python, async, concurrency"

    async def test_missing_task_id_returns_empty(self, monkeypatch):
        """Missing task_id → atom returns empty without DB writes."""
        from modules.content.atoms import content_record_pipeline_version as atom

        upsert_calls: list[tuple] = []

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, data):
                upsert_calls.append((task_id, data))

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)

        state = _base_state()
        state.pop("task_id")
        out = await atom.run(state)

        assert out == {}
        assert len(upsert_calls) == 0

    async def test_db_failure_swallowed_returns_empty(self, monkeypatch):
        """DB upsert failure is non-fatal; atom returns empty dict."""
        from modules.content.atoms import content_record_pipeline_version as atom

        class _BrokenPipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, data):
                raise RuntimeError("connection lost")

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _BrokenPipelineDB)

        state = _base_state(content="body text")
        out = await atom.run(state)

        assert out == {}  # failure swallowed, not raised

    async def test_merges_existing_stages_dict(self, monkeypatch):
        """Existing stages dict values are preserved alongside the new flag."""
        from modules.content.atoms import content_record_pipeline_version as atom

        class _FakePipelineDB:
            def __init__(self, pool): ...
            async def upsert_version(self, task_id, data): ...

        monkeypatch.setattr("services.pipeline_db.PipelineDB", _FakePipelineDB)

        existing_stages = {"2_content_generated": True, "3_qa_passed": True}
        state = _base_state(stages=existing_stages)
        out = await atom.run(state)

        assert out["stages"]["5_version_recorded"] is True
        assert out["stages"]["2_content_generated"] is True
        assert out["stages"]["3_qa_passed"] is True
