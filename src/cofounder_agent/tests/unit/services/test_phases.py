"""
Unit tests for services/phases/ modules.

Covers:
- base_phase.py: PhaseInputType, PhaseInputSpec, PhaseOutputSpec, PhaseConfig,
  BasePhase (validate_inputs, status management)
- content_phases.py: GenerateContentPhase, QualityEvaluationPhase, SearchImagePhase,
  GenerateSEOPhase, CaptureTrainingDataPhase
- publishing_phases.py: CreatePostPhase, PublishPostPhase, helper functions
  (_extract_field, _resolve_database_service, _close_database_service)

All external I/O (AI, DB, image search) is mocked so tests run with zero real deps.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_content_phase(phase_id: str = "phase-1"):
    from services.phases.content_phases import GenerateContentPhase

    return GenerateContentPhase(phase_id=phase_id, phase_type="generate_content")


def _make_quality_phase(phase_id: str = "phase-2"):
    from services.phases.content_phases import QualityEvaluationPhase

    return QualityEvaluationPhase(phase_id=phase_id, phase_type="quality_evaluation")


def _make_search_image_phase(phase_id: str = "phase-3"):
    from services.phases.content_phases import SearchImagePhase

    return SearchImagePhase(phase_id=phase_id, phase_type="search_image")


def _make_seo_phase(phase_id: str = "phase-4"):
    from services.phases.content_phases import GenerateSEOPhase

    return GenerateSEOPhase(phase_id=phase_id, phase_type="generate_seo")


def _make_capture_phase(phase_id: str = "phase-5"):
    from services.phases.content_phases import CaptureTrainingDataPhase

    return CaptureTrainingDataPhase(phase_id=phase_id, phase_type="capture_training_data")


def _make_create_post_phase(phase_id: str = "phase-6"):
    from services.phases.publishing_phases import CreatePostPhase

    return CreatePostPhase(phase_id=phase_id, phase_type="create_post")


def _make_publish_post_phase(phase_id: str = "phase-7"):
    from services.phases.publishing_phases import PublishPostPhase

    return PublishPostPhase(phase_id=phase_id, phase_type="publish_post")


# ===========================================================================
# base_phase.py
# ===========================================================================


class TestPhaseInputType:
    def test_values_are_stable(self):
        from services.phases.base_phase import PhaseInputType

        assert PhaseInputType.USER_PROVIDED.value == "user_provided"
        assert PhaseInputType.PHASE_OUTPUT.value == "phase_output"
        assert PhaseInputType.OPTIONAL.value == "optional"


class TestPhaseInputSpec:
    def test_required_defaults(self):
        from services.phases.base_phase import PhaseInputSpec, PhaseInputType

        spec = PhaseInputSpec(name="topic", type="str", description="Topic")
        assert spec.source == PhaseInputType.OPTIONAL
        assert spec.required is True
        assert spec.default is None
        assert spec.accepts_from_phases is None

    def test_custom_values(self):
        from services.phases.base_phase import PhaseInputSpec, PhaseInputType

        spec = PhaseInputSpec(
            name="content",
            type="str",
            description="Content",
            source=PhaseInputType.PHASE_OUTPUT,
            required=False,
            default="",
            accepts_from_phases=["generate_content"],
        )
        assert spec.source == PhaseInputType.PHASE_OUTPUT
        assert spec.required is False
        assert spec.accepts_from_phases == ["generate_content"]


class TestPhaseOutputSpec:
    def test_fields(self):
        from services.phases.base_phase import PhaseOutputSpec

        spec = PhaseOutputSpec(name="content", type="str", description="Generated content")
        assert spec.name == "content"
        assert spec.type == "str"
        assert spec.description == "Generated content"


class TestPhaseConfig:
    def test_fields(self):
        from services.phases.base_phase import PhaseConfig, PhaseInputSpec, PhaseOutputSpec

        cfg = PhaseConfig(
            name="Test Phase",
            description="Does things",
            inputs=[PhaseInputSpec(name="x", type="str", description="x")],
            outputs=[PhaseOutputSpec(name="y", type="str", description="y")],
            configurable_params={"threshold": 70},
        )
        assert cfg.name == "Test Phase"
        assert len(cfg.inputs) == 1
        assert len(cfg.outputs) == 1
        assert cfg.configurable_params["threshold"] == 70


class TestBasePhaseValidateInputs:
    """Tests for BasePhase.validate_inputs() via a concrete subclass."""

    @pytest.mark.asyncio
    async def test_all_required_present_returns_true(self):
        phase = _make_content_phase()
        is_valid, error = await phase.validate_inputs({"topic": "AI"})
        assert is_valid is True
        assert error is None

    @pytest.mark.asyncio
    async def test_missing_required_input_returns_false(self):
        phase = _make_content_phase()
        is_valid, error = await phase.validate_inputs({})
        assert is_valid is False
        assert error is not None and "topic" in error

    @pytest.mark.asyncio
    async def test_optional_input_absent_still_valid(self):
        """tags is optional (required=False) on GenerateContentPhase."""
        phase = _make_content_phase()
        is_valid, error = await phase.validate_inputs({"topic": "AI"})
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_required_field_with_default_not_none_still_passes(self):
        """If a required field has a non-None default it shouldn't block."""
        from services.phases.base_phase import (
            BasePhase,
            PhaseConfig,
            PhaseInputSpec,
        )

        class _StubPhase(BasePhase):
            @classmethod
            def get_phase_type(cls):
                return "stub"

            @classmethod
            def get_phase_config(cls):
                return PhaseConfig(
                    name="Stub",
                    description="stub",
                    inputs=[
                        PhaseInputSpec(
                            name="x",
                            type="str",
                            description="x",
                            required=True,
                            default="fallback",  # non-None default
                        )
                    ],
                    outputs=[],
                    configurable_params={},
                )

            async def execute(self, inputs, config):
                pass

        phase = _StubPhase(phase_id="s1", phase_type="stub")
        is_valid, error = await phase.validate_inputs({})
        # required=True but default is not None → should pass
        assert is_valid is True


class TestBasePhaseInitialState:
    def test_initial_state(self):
        phase = _make_content_phase()
        assert phase.status == "pending"
        assert phase.result is None
        assert phase.error is None
        assert phase.execution_time == 0.0


class TestBasePhaseClassMethods:
    def test_get_phase_type(self):
        from services.phases.content_phases import GenerateContentPhase

        assert GenerateContentPhase.get_phase_type() == "generate_content"

    def test_get_phase_config_returns_phase_config(self):
        from services.phases.base_phase import PhaseConfig
        from services.phases.content_phases import GenerateContentPhase

        cfg = GenerateContentPhase.get_phase_config()
        assert isinstance(cfg, PhaseConfig)
        assert cfg.name == "Generate Content"


# ===========================================================================
# content_phases.py — GenerateContentPhase
# ===========================================================================


class TestGenerateContentPhase:
    @pytest.mark.asyncio
    async def test_execute_success(self):
        phase = _make_content_phase()
        mock_generator = AsyncMock()
        mock_generator.generate_blog_post = AsyncMock(
            return_value=("blog content text", "claude-3-sonnet", {"tokens": 500})
        )

        # GenerateContentPhase.execute() does a lazy `from .ai_content_generator import ...`
        # inside the function body — patch it via sys.modules under the dotted path
        # the import resolves to within the phases package.
        mock_ai_module = MagicMock()
        mock_ai_module.get_content_generator = lambda: mock_generator

        with patch.dict("sys.modules", {"services.phases.ai_content_generator": mock_ai_module}):
            result = await phase.execute(
                inputs={"topic": "AI in Healthcare", "tags": ["AI", "healthcare"]},
                config={"style": "technical", "tone": "professional", "target_length": 1500},
            )

        assert result["content"] == "blog content text"
        assert result["model_used"] == "claude-3-sonnet"
        assert result["metrics"]["tokens"] == 500
        assert phase.status == "completed"

    @pytest.mark.asyncio
    async def test_execute_missing_required_input_raises(self):
        phase = _make_content_phase()
        with pytest.raises(ValueError, match="topic"):
            await phase.execute(inputs={}, config={})
        assert phase.status == "failed"

    @pytest.mark.asyncio
    async def test_execute_generator_error_propagates(self):
        phase = _make_content_phase()
        mock_generator = AsyncMock()
        mock_generator.generate_blog_post = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
        with patch.dict(
            "sys.modules",
            {
                "services.phases.ai_content_generator": MagicMock(
                    get_content_generator=lambda: mock_generator
                )
            },
        ):
            with pytest.raises(RuntimeError, match="LLM unavailable"):
                await phase.execute(
                    inputs={"topic": "AI"},
                    config={},
                )
        assert phase.status == "failed"
        assert phase.error is not None and "LLM unavailable" in phase.error

    def test_phase_type(self):
        from services.phases.content_phases import GenerateContentPhase

        assert GenerateContentPhase.get_phase_type() == "generate_content"

    def test_phase_config_inputs(self):
        from services.phases.content_phases import GenerateContentPhase

        cfg = GenerateContentPhase.get_phase_config()
        input_names = [i.name for i in cfg.inputs]
        assert "topic" in input_names
        assert "tags" in input_names

    def test_phase_config_outputs(self):
        from services.phases.content_phases import GenerateContentPhase

        cfg = GenerateContentPhase.get_phase_config()
        output_names = [o.name for o in cfg.outputs]
        assert "content" in output_names
        assert "model_used" in output_names
        assert "metrics" in output_names

    def test_phase_config_defaults(self):
        from services.phases.content_phases import GenerateContentPhase

        cfg = GenerateContentPhase.get_phase_config()
        assert cfg.configurable_params["style"] == "balanced"
        assert cfg.configurable_params["target_length"] == 1500


# ===========================================================================
# content_phases.py — QualityEvaluationPhase
# ===========================================================================


class TestQualityEvaluationPhase:
    @pytest.mark.asyncio
    async def test_execute_passing_score(self):
        phase = _make_quality_phase()
        mock_service = AsyncMock()
        mock_service.evaluate = AsyncMock(
            return_value={
                "overall_score": 85.0,
                "dimensions": {"clarity": 90, "accuracy": 80},
                "feedback": "Great content",
                "readability_metrics": {"word_count": 1500},
            }
        )
        with patch.dict(
            "sys.modules",
            {
                "services.phases.quality_service": MagicMock(
                    get_quality_service=lambda: mock_service
                )
            },
        ):
            result = await phase.execute(
                inputs={"content": "Some content", "topic": "AI"},
                config={"threshold": 70.0},
            )

        assert result["overall_score"] == 85.0
        assert result["passing"] is True
        assert result["feedback"] == "Great content"
        assert phase.status == "completed"

    @pytest.mark.asyncio
    async def test_execute_failing_score(self):
        phase = _make_quality_phase()
        mock_service = AsyncMock()
        mock_service.evaluate = AsyncMock(
            return_value={
                "overall_score": 55.0,
                "dimensions": {},
                "feedback": "Needs improvement",
                "readability_metrics": {},
            }
        )
        with patch.dict(
            "sys.modules",
            {
                "services.phases.quality_service": MagicMock(
                    get_quality_service=lambda: mock_service
                )
            },
        ):
            result = await phase.execute(
                inputs={"content": "Weak content", "topic": "AI"},
                config={"threshold": 70.0},
            )

        assert result["passing"] is False
        assert result["threshold_used"] == 70.0

    @pytest.mark.asyncio
    async def test_execute_missing_content_raises(self):
        phase = _make_quality_phase()
        with pytest.raises(ValueError):
            await phase.execute(inputs={"topic": "AI"}, config={})
        assert phase.status == "failed"

    @pytest.mark.asyncio
    async def test_execute_service_error_propagates(self):
        phase = _make_quality_phase()
        mock_service = AsyncMock()
        mock_service.evaluate = AsyncMock(side_effect=RuntimeError("service down"))
        with patch.dict(
            "sys.modules",
            {
                "services.phases.quality_service": MagicMock(
                    get_quality_service=lambda: mock_service
                )
            },
        ):
            with pytest.raises(RuntimeError, match="service down"):
                await phase.execute(
                    inputs={"content": "x", "topic": "AI"},
                    config={},
                )
        assert phase.status == "failed"

    def test_phase_type(self):
        from services.phases.content_phases import QualityEvaluationPhase

        assert QualityEvaluationPhase.get_phase_type() == "quality_evaluation"

    def test_phase_config_threshold_default(self):
        from services.phases.content_phases import QualityEvaluationPhase

        cfg = QualityEvaluationPhase.get_phase_config()
        assert cfg.configurable_params["threshold"] == 70.0


# ===========================================================================
# content_phases.py — SearchImagePhase
# ===========================================================================


class TestSearchImagePhase:
    @pytest.mark.asyncio
    async def test_execute_success(self):
        phase = _make_search_image_phase()
        mock_service = AsyncMock()
        mock_service.search_featured_image = AsyncMock(
            return_value={"url": "https://pexels.com/photo.jpg", "photographer": "Jane"}
        )
        with patch.dict(
            "sys.modules",
            {"services.phases.image_service": MagicMock(get_image_service=lambda: mock_service)},
        ):
            result = await phase.execute(
                inputs={"topic": "technology"},
                config={"enabled": True},
            )

        assert result["image_url"] == "https://pexels.com/photo.jpg"
        assert result["photographer"] == "Jane"
        assert result["source"] == "Pexels"
        assert phase.status == "completed"

    @pytest.mark.asyncio
    async def test_execute_skipped_when_disabled(self):
        phase = _make_search_image_phase()
        result = await phase.execute(
            inputs={"topic": "technology"},
            config={"enabled": False},
        )
        assert phase.status == "skipped"
        assert result["image_url"] is None
        assert result["photographer"] is None

    @pytest.mark.asyncio
    async def test_execute_no_image_found_returns_nulls(self):
        phase = _make_search_image_phase()
        mock_service = AsyncMock()
        mock_service.search_featured_image = AsyncMock(return_value=None)
        with patch.dict(
            "sys.modules",
            {"services.phases.image_service": MagicMock(get_image_service=lambda: mock_service)},
        ):
            result = await phase.execute(
                inputs={"topic": "technology"},
                config={"enabled": True},
            )

        assert result["image_url"] is None
        assert result["photographer"] is None
        assert result["source"] is None

    @pytest.mark.asyncio
    async def test_execute_image_error_non_fatal(self):
        """Image errors should not raise — they return null result."""
        phase = _make_search_image_phase()
        mock_service = AsyncMock()
        mock_service.search_featured_image = AsyncMock(side_effect=RuntimeError("Pexels API down"))
        with patch.dict(
            "sys.modules",
            {"services.phases.image_service": MagicMock(get_image_service=lambda: mock_service)},
        ):
            result = await phase.execute(
                inputs={"topic": "technology"},
                config={"enabled": True},
            )

        # Error is non-fatal — returns nulls, does NOT raise
        assert result["image_url"] is None
        # Status may be "completed" or "failed" depending on impl;
        # the key thing is no exception propagated.

    @pytest.mark.asyncio
    async def test_execute_missing_topic_raises(self):
        phase = _make_search_image_phase()
        with pytest.raises(ValueError):
            await phase.execute(inputs={}, config={"enabled": True})

    def test_phase_type(self):
        from services.phases.content_phases import SearchImagePhase

        assert SearchImagePhase.get_phase_type() == "search_image"


# ===========================================================================
# content_phases.py — GenerateSEOPhase
# ===========================================================================


class TestGenerateSEOPhase:
    @pytest.mark.asyncio
    async def test_execute_success(self):
        phase = _make_seo_phase()
        mock_generator = AsyncMock()
        mock_generator.generate_seo_assets = AsyncMock(
            return_value={
                "seo_title": "AI in Healthcare: A Guide",
                "meta_description": "Explore how AI transforms healthcare...",
                "meta_keywords": ["AI", "healthcare", "ML"],
            }
        )
        with patch.dict(
            "sys.modules",
            {
                "services.phases.seo_content_generator": MagicMock(
                    get_seo_content_generator=lambda: mock_generator
                )
            },
        ):
            result = await phase.execute(
                inputs={"content": "Long content...", "topic": "AI in Healthcare"},
                config={},
            )

        assert result["seo_title"] == "AI in Healthcare: A Guide"
        assert result["seo_description"] == "Explore how AI transforms healthcare..."
        assert "AI" in result["seo_keywords"]
        assert phase.status == "completed"

    @pytest.mark.asyncio
    async def test_execute_missing_inputs_raises(self):
        phase = _make_seo_phase()
        with pytest.raises(ValueError):
            await phase.execute(inputs={"topic": "AI"}, config={})  # content missing
        assert phase.status == "failed"

    @pytest.mark.asyncio
    async def test_execute_seo_error_propagates(self):
        phase = _make_seo_phase()
        mock_generator = AsyncMock()
        mock_generator.generate_seo_assets = AsyncMock(
            side_effect=RuntimeError("SEO service failed")
        )
        with patch.dict(
            "sys.modules",
            {
                "services.phases.seo_content_generator": MagicMock(
                    get_seo_content_generator=lambda: mock_generator
                )
            },
        ):
            with pytest.raises(RuntimeError, match="SEO service failed"):
                await phase.execute(
                    inputs={"content": "text", "topic": "AI"},
                    config={},
                )
        assert phase.status == "failed"

    def test_phase_type(self):
        from services.phases.content_phases import GenerateSEOPhase

        assert GenerateSEOPhase.get_phase_type() == "generate_seo"

    def test_phase_config_outputs(self):
        from services.phases.content_phases import GenerateSEOPhase

        cfg = GenerateSEOPhase.get_phase_config()
        output_names = [o.name for o in cfg.outputs]
        assert "seo_title" in output_names
        assert "seo_description" in output_names
        assert "seo_keywords" in output_names


# ===========================================================================
# content_phases.py — CaptureTrainingDataPhase
# ===========================================================================


class TestCaptureTrainingDataPhase:
    @pytest.mark.asyncio
    async def test_execute_with_db_service_stores_data(self):
        phase = _make_capture_phase()
        mock_db = AsyncMock()
        mock_db.create_orchestrator_training_data = AsyncMock(return_value=None)

        result = await phase.execute(
            inputs={
                "content": "blog post text",
                "overall_score": 82.0,
                "topic": "AI",
                "model_used": "claude-3-sonnet",
                "scores": {"clarity": 90},
            },
            config={"database_service": mock_db, "task_id": "task-123"},
        )

        assert result["stored"] is True
        mock_db.create_orchestrator_training_data.assert_called_once()
        assert phase.status == "completed"

    @pytest.mark.asyncio
    async def test_execute_without_db_service_logs_only(self):
        phase = _make_capture_phase()
        result = await phase.execute(
            inputs={
                "content": "blog post text",
                "overall_score": 82.0,
                "topic": "AI",
            },
            config={},  # no database_service
        )

        assert result["stored"] is False
        assert result["reason"] == "no_database_service"
        assert phase.status == "completed"

    @pytest.mark.asyncio
    async def test_execute_disabled_via_env_var(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRAINING_CAPTURE", "false")
        phase = _make_capture_phase()
        result = await phase.execute(
            inputs={
                "content": "text",
                "overall_score": 80.0,
                "topic": "AI",
            },
            config={},
        )
        assert result["stored"] is False
        assert result["reason"] == "disabled"
        assert phase.status == "completed"

    @pytest.mark.asyncio
    async def test_execute_db_error_is_non_terminal(self):
        """DB errors in capture phase should not raise — content pipeline must continue."""
        phase = _make_capture_phase()
        mock_db = AsyncMock()
        mock_db.create_orchestrator_training_data = AsyncMock(
            side_effect=RuntimeError("DB write failed")
        )

        result = await phase.execute(
            inputs={
                "content": "text",
                "overall_score": 80.0,
                "topic": "AI",
            },
            config={"database_service": mock_db},
        )
        # Should NOT raise; returns error indicator
        assert result["stored"] is False
        assert result["reason"] == "error"

    @pytest.mark.asyncio
    async def test_execute_missing_required_inputs_raises(self):
        phase = _make_capture_phase()
        with pytest.raises(ValueError):
            await phase.execute(inputs={"topic": "AI"}, config={})  # overall_score missing
        assert phase.status == "failed"

    @pytest.mark.asyncio
    async def test_payload_includes_quality_score_normalized(self):
        """overall_score (0-100) is divided by 100 in the payload."""
        phase = _make_capture_phase()
        captured_payload = {}
        mock_db = AsyncMock()

        async def _capture(payload):
            captured_payload.update(payload)

        mock_db.create_orchestrator_training_data = _capture

        await phase.execute(
            inputs={
                "content": "text",
                "overall_score": 80.0,
                "topic": "AI",
            },
            config={"database_service": mock_db},
        )

        assert captured_payload["quality_score"] == pytest.approx(0.80)
        assert captured_payload["success"] is True  # 80 >= 70

    def test_phase_type(self):
        from services.phases.content_phases import CaptureTrainingDataPhase

        assert CaptureTrainingDataPhase.get_phase_type() == "capture_training_data"


# ===========================================================================
# publishing_phases.py — helper functions
# ===========================================================================


class TestExtractField:
    def test_dict_payload(self):
        from services.phases.publishing_phases import _extract_field

        payload = {"id": "uuid-123", "status": "draft"}
        assert _extract_field(payload, "id") == "uuid-123"
        assert _extract_field(payload, "status") == "draft"

    def test_dict_missing_key_returns_default(self):
        from services.phases.publishing_phases import _extract_field

        payload = {"id": "uuid-123"}
        assert _extract_field(payload, "missing", default="fallback") == "fallback"

    def test_none_payload_returns_default(self):
        from services.phases.publishing_phases import _extract_field

        assert _extract_field(None, "id", default="x") == "x"

    def test_model_dump_payload(self):
        """Supports Pydantic-like objects with model_dump()."""
        from services.phases.publishing_phases import _extract_field

        class FakeModel:
            def model_dump(self):
                return {"id": "pydantic-id", "slug": "my-post"}

        result = _extract_field(FakeModel(), "id")
        assert result == "pydantic-id"

    def test_attribute_payload(self):
        """Falls back to getattr for plain objects."""
        from services.phases.publishing_phases import _extract_field

        class PlainObj:
            id = "attr-id"

        result = _extract_field(PlainObj(), "id")
        assert result == "attr-id"

    def test_attribute_missing_returns_default(self):
        from services.phases.publishing_phases import _extract_field

        class PlainObj:
            pass

        result = _extract_field(PlainObj(), "nonexistent", default="d")
        assert result == "d"


class TestCloseDatabaseService:
    @pytest.mark.asyncio
    async def test_does_nothing_when_not_owner(self):
        from services.phases.publishing_phases import _close_database_service

        mock_db = AsyncMock()
        await _close_database_service(mock_db, owns_service=False)
        mock_db.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_closes_when_owner(self):
        from services.phases.publishing_phases import _close_database_service

        mock_db = AsyncMock()
        mock_db.close = AsyncMock(return_value=None)
        await _close_database_service(mock_db, owns_service=True)
        mock_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_error_does_not_raise(self):
        """Close failures must be silent so they don't mask upstream errors."""
        from services.phases.publishing_phases import _close_database_service

        mock_db = AsyncMock()
        mock_db.close = AsyncMock(side_effect=RuntimeError("connection lost"))
        # Should not raise
        await _close_database_service(mock_db, owns_service=True)


class TestResolveDatabaseService:
    @pytest.mark.asyncio
    async def test_returns_injected_service_with_owns_false(self):
        from services.phases.publishing_phases import _resolve_database_service

        mock_db = AsyncMock()
        db_service, owns = await _resolve_database_service({"database_service": mock_db})
        assert db_service is mock_db
        assert owns is False

    @pytest.mark.asyncio
    async def test_creates_new_service_when_not_injected(self):
        from services.phases.publishing_phases import _resolve_database_service

        mock_db_instance = AsyncMock()
        mock_db_instance.initialize = AsyncMock(return_value=None)
        mock_db_class = MagicMock(return_value=mock_db_instance)

        # The function does `from ..database_service import DatabaseService` lazily —
        # the resolved module name is `services.database_service`.
        mock_db_module = MagicMock()
        mock_db_module.DatabaseService = mock_db_class

        with patch.dict("sys.modules", {"services.database_service": mock_db_module}):
            db_service, owns = await _resolve_database_service({})

        assert db_service is mock_db_instance
        assert owns is True
        mock_db_instance.initialize.assert_called_once()


# ===========================================================================
# publishing_phases.py — CreatePostPhase
# ===========================================================================


class TestCreatePostPhase:
    @pytest.mark.asyncio
    async def test_execute_success(self):
        phase = _make_create_post_phase()
        mock_db = AsyncMock()
        mock_db.create_post = AsyncMock(
            return_value={"id": "post-uuid-1", "slug": "ai-in-healthcare", "status": "draft"}
        )

        # `slugify` is imported lazily inside execute() as `from slugify import slugify`.
        # The `slugify` package resolves to the top-level `slugify` module; patch it there.
        mock_slugify_module = MagicMock()
        mock_slugify_module.slugify = MagicMock(return_value="ai-in-healthcare")

        with patch.dict("sys.modules", {"slugify": mock_slugify_module}):
            result = await phase.execute(
                inputs={
                    "content": "Blog content here",
                    "topic": "AI in Healthcare",
                    "seo_title": "AI in Healthcare",
                    "seo_description": "About AI",
                    "seo_keywords": ["AI", "health"],
                    "image_url": "https://pexels.com/img.jpg",
                },
                config={"database_service": mock_db, "status": "draft"},
            )

        assert result["post_id"] == "post-uuid-1"
        assert result["slug"] == "ai-in-healthcare"
        assert result["status"] == "draft"
        assert phase.status == "completed"
        mock_db.create_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_missing_content_raises(self):
        phase = _make_create_post_phase()
        with pytest.raises(ValueError):
            await phase.execute(
                inputs={"topic": "AI"},  # content missing
                config={},
            )
        assert phase.status == "failed"

    @pytest.mark.asyncio
    async def test_execute_db_error_propagates(self):
        phase = _make_create_post_phase()
        mock_db = AsyncMock()
        mock_db.create_post = AsyncMock(side_effect=RuntimeError("DB insert failed"))

        mock_slugify_module = MagicMock()
        mock_slugify_module.slugify = MagicMock(return_value="ai")

        with patch.dict("sys.modules", {"slugify": mock_slugify_module}):
            with pytest.raises(RuntimeError, match="DB insert failed"):
                await phase.execute(
                    inputs={"content": "text", "topic": "AI"},
                    config={"database_service": mock_db},
                )
        assert phase.status == "failed"

    @pytest.mark.asyncio
    async def test_execute_db_closed_on_completion(self):
        """Owned DB service must be closed in finally block."""
        phase = _make_create_post_phase()
        mock_db = AsyncMock()
        mock_db.create_post = AsyncMock(return_value={"id": "p1", "slug": "ai", "status": "draft"})
        mock_db.close = AsyncMock()

        mock_slugify_module = MagicMock()
        mock_slugify_module.slugify = MagicMock(return_value="ai")

        with patch.dict("sys.modules", {"slugify": mock_slugify_module}):
            with patch(
                "services.phases.publishing_phases._resolve_database_service",
                AsyncMock(return_value=(mock_db, True)),
            ):
                with patch(
                    "services.phases.publishing_phases._close_database_service",
                    AsyncMock(),
                ) as mock_close:
                    await phase.execute(
                        inputs={"content": "text", "topic": "AI"},
                        config={},
                    )
                    mock_close.assert_called_once_with(mock_db, True)

    @pytest.mark.asyncio
    async def test_execute_db_closed_even_on_error(self):
        """DB close must happen even when execute fails."""
        phase = _make_create_post_phase()
        mock_db = AsyncMock()
        mock_db.create_post = AsyncMock(side_effect=RuntimeError("insert error"))

        mock_slugify_module = MagicMock()
        mock_slugify_module.slugify = MagicMock(return_value="ai")

        with patch.dict("sys.modules", {"slugify": mock_slugify_module}):
            with patch(
                "services.phases.publishing_phases._resolve_database_service",
                AsyncMock(return_value=(mock_db, True)),
            ):
                with patch(
                    "services.phases.publishing_phases._close_database_service",
                    AsyncMock(),
                ) as mock_close:
                    with pytest.raises(RuntimeError):
                        await phase.execute(
                            inputs={"content": "text", "topic": "AI"},
                            config={},
                        )
                    mock_close.assert_called_once_with(mock_db, True)

    def test_phase_type(self):
        from services.phases.publishing_phases import CreatePostPhase

        assert CreatePostPhase.get_phase_type() == "create_post"

    def test_phase_config_inputs(self):
        from services.phases.publishing_phases import CreatePostPhase

        cfg = CreatePostPhase.get_phase_config()
        input_names = [i.name for i in cfg.inputs]
        assert "content" in input_names
        assert "topic" in input_names

    def test_phase_config_outputs(self):
        from services.phases.publishing_phases import CreatePostPhase

        cfg = CreatePostPhase.get_phase_config()
        output_names = [o.name for o in cfg.outputs]
        assert "post_id" in output_names
        assert "slug" in output_names
        assert "status" in output_names


# ===========================================================================
# publishing_phases.py — PublishPostPhase
# ===========================================================================


class TestPublishPostPhase:
    @pytest.mark.asyncio
    async def test_execute_success(self):
        phase = _make_publish_post_phase()
        mock_db = AsyncMock()
        mock_db.update_post = AsyncMock(return_value=True)

        result = await phase.execute(
            inputs={"post_id": "post-uuid-1", "slug": "ai-in-healthcare"},
            config={"database_service": mock_db, "base_url": "https://gladlabs.ai"},
        )

        assert result["post_id"] == "post-uuid-1"
        assert "published_at" in result
        assert result["public_url"] == "https://gladlabs.ai/posts/ai-in-healthcare"
        assert phase.status == "completed"
        mock_db.update_post.assert_called_once_with("post-uuid-1", {"status": "published"})

    @pytest.mark.asyncio
    async def test_execute_post_not_found_raises(self):
        phase = _make_publish_post_phase()
        mock_db = AsyncMock()
        mock_db.update_post = AsyncMock(return_value=False)  # not found

        with pytest.raises(ValueError, match="post not found"):
            await phase.execute(
                inputs={"post_id": "missing-id", "slug": "some-slug"},
                config={"database_service": mock_db},
            )
        assert phase.status == "failed"

    @pytest.mark.asyncio
    async def test_execute_missing_post_id_raises(self):
        phase = _make_publish_post_phase()
        with pytest.raises(ValueError):
            await phase.execute(inputs={"slug": "some-slug"}, config={})
        assert phase.status == "failed"

    @pytest.mark.asyncio
    async def test_execute_uses_default_base_url(self):
        phase = _make_publish_post_phase()
        mock_db = AsyncMock()
        mock_db.update_post = AsyncMock(return_value=True)

        result = await phase.execute(
            inputs={"post_id": "p1", "slug": "my-post"},
            config={"database_service": mock_db},  # no base_url
        )

        assert "https://example.com/posts/my-post" == result["public_url"]

    @pytest.mark.asyncio
    async def test_execute_db_closed_on_completion(self):
        phase = _make_publish_post_phase()
        mock_db = AsyncMock()
        mock_db.update_post = AsyncMock(return_value=True)

        with patch(
            "services.phases.publishing_phases._resolve_database_service",
            AsyncMock(return_value=(mock_db, True)),
        ):
            with patch(
                "services.phases.publishing_phases._close_database_service",
                AsyncMock(),
            ) as mock_close:
                await phase.execute(
                    inputs={"post_id": "p1", "slug": "slug"},
                    config={},
                )
                mock_close.assert_called_once_with(mock_db, True)

    @pytest.mark.asyncio
    async def test_execute_db_closed_on_error(self):
        phase = _make_publish_post_phase()
        mock_db = AsyncMock()
        mock_db.update_post = AsyncMock(side_effect=RuntimeError("DB error"))

        with patch(
            "services.phases.publishing_phases._resolve_database_service",
            AsyncMock(return_value=(mock_db, True)),
        ):
            with patch(
                "services.phases.publishing_phases._close_database_service",
                AsyncMock(),
            ) as mock_close:
                with pytest.raises(RuntimeError):
                    await phase.execute(
                        inputs={"post_id": "p1", "slug": "slug"},
                        config={},
                    )
                mock_close.assert_called_once_with(mock_db, True)

    def test_phase_type(self):
        from services.phases.publishing_phases import PublishPostPhase

        assert PublishPostPhase.get_phase_type() == "publish_post"

    def test_phase_config_outputs(self):
        from services.phases.publishing_phases import PublishPostPhase

        cfg = PublishPostPhase.get_phase_config()
        output_names = [o.name for o in cfg.outputs]
        assert "post_id" in output_names
        assert "published_at" in output_names
        assert "public_url" in output_names

    def test_phase_config_default_base_url(self):
        from services.phases.publishing_phases import PublishPostPhase

        cfg = PublishPostPhase.get_phase_config()
        assert cfg.configurable_params["base_url"] == "https://example.com"


# ===========================================================================
# example_workflows.py — sanity checks on workflow definitions
# ===========================================================================


class TestExampleWorkflows:
    def test_blog_generation_only_structure(self):
        from services.phases.example_workflows import BLOG_GENERATION_ONLY

        assert BLOG_GENERATION_ONLY["name"] == "Generate & Evaluate Blog"
        phases = BLOG_GENERATION_ONLY["phases"]
        assert len(phases) == 2
        assert phases[0]["type"] == "generate_content"
        assert phases[1]["type"] == "quality_evaluation"

    def test_blog_complete_workflow_structure(self):
        from services.phases.example_workflows import BLOG_COMPLETE_WORKFLOW

        phases = BLOG_COMPLETE_WORKFLOW["phases"]
        phase_types = [p["type"] for p in phases]
        assert "generate_content" in phase_types
        assert "quality_evaluation" in phase_types
        assert "search_image" in phase_types
        assert "generate_seo" in phase_types
        assert "create_post" in phase_types

    def test_research_and_content_workflow_threshold(self):
        from services.phases.example_workflows import RESEARCH_AND_CONTENT_WORKFLOW

        phases = RESEARCH_AND_CONTENT_WORKFLOW["phases"]
        qual_phase = next(p for p in phases if p["type"] == "quality_evaluation")
        assert qual_phase["config"]["threshold"] == 80

    def test_blog_to_social_workflow_requires_existing_content(self):
        from services.phases.example_workflows import BLOG_TO_SOCIAL_WORKFLOW

        assert BLOG_TO_SOCIAL_WORKFLOW["requires_existing_content"] is True

    def test_all_workflows_have_required_keys(self):
        from services.phases.example_workflows import (
            BLOG_COMPLETE_WORKFLOW,
            BLOG_GENERATION_ONLY,
            BLOG_TO_SOCIAL_WORKFLOW,
            RESEARCH_AND_CONTENT_WORKFLOW,
        )

        for wf in [
            BLOG_GENERATION_ONLY,
            BLOG_COMPLETE_WORKFLOW,
            BLOG_TO_SOCIAL_WORKFLOW,
            RESEARCH_AND_CONTENT_WORKFLOW,
        ]:
            assert "name" in wf
            assert "description" in wf
            assert "phases" in wf
