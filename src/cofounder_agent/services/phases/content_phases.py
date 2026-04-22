"""
Content Generation Phases

Extracted from process_content_generation_task in content_router_service.py
Each phase is a discrete, composable step in content creation.
"""

from typing import Any

from services.logger_config import get_logger

from .base_phase import BasePhase, PhaseConfig, PhaseInputSpec, PhaseInputType, PhaseOutputSpec

logger = get_logger(__name__)


class GenerateContentPhase(BasePhase):
    """
    Generate blog post content from a topic.

    PHASE 2 of the 7-stage pipeline - AI content generation
    """

    @classmethod
    def get_phase_type(cls) -> str:
        return "generate_content"

    @classmethod
    def get_phase_config(cls) -> PhaseConfig:
        return PhaseConfig(
            name="Generate Content",
            description="AI-generated content from topic using specified style/tone",
            inputs=[
                PhaseInputSpec(
                    name="topic",
                    type="str",
                    source=PhaseInputType.USER_PROVIDED,
                    description="Blog post topic",
                    required=True,
                ),
                PhaseInputSpec(
                    name="tags",
                    type="list[str]",
                    source=PhaseInputType.USER_PROVIDED,
                    description="Keywords/tags for SEO",
                    required=False,
                    default=[],
                ),
            ],
            outputs=[
                PhaseOutputSpec(
                    name="content",
                    type="str",
                    description="Generated markdown content",
                ),
                PhaseOutputSpec(
                    name="model_used",
                    type="str",
                    description="Which LLM model was used",
                ),
                PhaseOutputSpec(
                    name="metrics",
                    type="dict",
                    description="Generation metrics (tokens, duration, etc.)",
                ),
            ],
            configurable_params={
                "style": "balanced",  # balanced | technical | narrative | listicle | thought-leadership
                "tone": "professional",  # professional | casual | academic | inspirational
                "target_length": 1500,  # words
                "preferred_model": None,  # Use model router if not specified
                "preferred_provider": None,  # Use router if not specified
            },
        )

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Generate content using AI"""

        # Validate inputs
        is_valid, error = await self.validate_inputs(inputs)
        if not is_valid:
            self.status = "failed"
            self.error = error
            raise ValueError(error)

        try:
            from .ai_content_generator import get_content_generator  # type: ignore[import]

            topic = inputs.get("topic")
            tags = inputs.get("tags", [topic])

            # Extract config
            style = config.get("style", "balanced")
            tone = config.get("tone", "professional")
            target_length = config.get("target_length", 1500)

            logger.info("[GenerateContentPhase] Generating %s %s content on '%s'", style, tone, topic)

            # Use existing content generation logic
            content_generator = get_content_generator()
            content_text, model_used, metrics = await content_generator.generate_blog_post(
                topic=topic,
                style=style,
                tone=tone,
                target_length=target_length,
                tags=tags,
                preferred_model=config.get("preferred_model"),
                preferred_provider=config.get("preferred_provider"),
            )

            self.status = "completed"
            self.result = {
                "content": content_text,
                "model_used": model_used,
                "metrics": metrics,
            }

            logger.info(
                "[GenerateContentPhase] Generated %d characters using %s",
                len(content_text), model_used,
            )

            return self.result

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.error("[GenerateContentPhase] Error: %s", e, exc_info=True)
            raise


class QualityEvaluationPhase(BasePhase):
    """
    Evaluate content quality across 7 dimensions.

    PHASE 2B of the 7-stage pipeline - Quality assessment
    """

    @classmethod
    def get_phase_type(cls) -> str:
        return "quality_evaluation"

    @classmethod
    def get_phase_config(cls) -> PhaseConfig:
        return PhaseConfig(
            name="Quality Evaluation",
            description="Evaluate content against 7 quality criteria (clarity, accuracy, completeness, relevance, SEO, readability, engagement)",
            inputs=[
                PhaseInputSpec(
                    name="content",
                    type="str",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="Content to evaluate",
                    required=True,
                    accepts_from_phases=["generate_content"],
                ),
                PhaseInputSpec(
                    name="topic",
                    type="str",
                    source=PhaseInputType.USER_PROVIDED,
                    description="Content topic (for context)",
                    required=True,
                ),
                PhaseInputSpec(
                    name="tags",
                    type="list[str]",
                    source=PhaseInputType.USER_PROVIDED,
                    description="Keywords for evaluation context",
                    required=False,
                    default=[],
                ),
            ],
            outputs=[
                PhaseOutputSpec(
                    name="overall_score",
                    type="float",
                    description="Overall quality score (0-100)",
                ),
                PhaseOutputSpec(
                    name="scores",
                    type="dict",
                    description="Individual dimension scores (clarity, accuracy, etc.)",
                ),
                PhaseOutputSpec(
                    name="passing",
                    type="bool",
                    description="Whether content meets quality threshold",
                ),
                PhaseOutputSpec(
                    name="feedback",
                    type="str",
                    description="Detailed feedback on quality",
                ),
                PhaseOutputSpec(
                    name="readability_metrics",
                    type="dict",
                    description="Readability data (word count, sentence length, etc.)",
                ),
            ],
            configurable_params={
                "threshold": 70.0,  # Minimum passing score
                "evaluation_method": "pattern_based",  # or "ai_based"
            },
        )

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Evaluate content quality"""

        is_valid, error = await self.validate_inputs(inputs)
        if not is_valid:
            self.status = "failed"
            self.error = error
            raise ValueError(error)

        try:
            from .quality_service import get_quality_service  # type: ignore[import]

            content = inputs.get("content")
            topic = inputs.get("topic")
            tags = inputs.get("tags", [topic])

            logger.info("[QualityEvaluationPhase] Evaluating content (%d chars)", len(content or ""))

            # Use existing quality service
            quality_service = get_quality_service()
            quality_result = await quality_service.evaluate(
                content=content,
                context={"topic": topic, "keywords": tags},
                method=config.get("evaluation_method", "pattern_based"),
            )

            threshold = config.get("threshold", 70.0)
            passing = quality_result["overall_score"] >= threshold

            self.status = "completed"
            self.result = {
                "overall_score": quality_result["overall_score"],
                "scores": quality_result.get("dimensions", {}),
                "passing": passing,
                "feedback": quality_result.get("feedback", ""),
                "readability_metrics": quality_result.get("readability_metrics", {}),
                "threshold_used": threshold,
            }

            logger.info(
                "[QualityEvaluationPhase] Score: %.1f (threshold: %s, passing: %s)",
                quality_result["overall_score"], threshold, passing,
            )

            return self.result

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.error("[QualityEvaluationPhase] Error: %s", e, exc_info=True)
            raise


class SearchImagePhase(BasePhase):
    """
    Search and retrieve featured image from Pexels.

    PHASE 3 of the 7-stage pipeline - Image search
    """

    @classmethod
    def get_phase_type(cls) -> str:
        return "search_image"

    @classmethod
    def get_phase_config(cls) -> PhaseConfig:
        return PhaseConfig(
            name="Search Featured Image",
            description="Find royalty-free image from Pexels",
            inputs=[
                PhaseInputSpec(
                    name="topic",
                    type="str",
                    source=PhaseInputType.USER_PROVIDED,
                    description="Topic for image search",
                    required=True,
                ),
                PhaseInputSpec(
                    name="tags",
                    type="list[str]",
                    source=PhaseInputType.USER_PROVIDED,
                    description="Search keywords",
                    required=False,
                    default=[],
                ),
            ],
            outputs=[
                PhaseOutputSpec(
                    name="image_url",
                    type="str",
                    description="URL to featured image (or None if not found)",
                ),
                PhaseOutputSpec(
                    name="photographer",
                    type="str",
                    description="Image photographer credit",
                ),
                PhaseOutputSpec(
                    name="source",
                    type="str",
                    description="Source (e.g., 'Pexels')",
                ),
            ],
            configurable_params={
                "enabled": True,  # Can skip image search
            },
        )

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Search for featured image"""

        if not config.get("enabled", True):
            self.status = "skipped"
            self.result = {
                "image_url": None,
                "photographer": None,
                "source": None,
            }
            logger.info("[SearchImagePhase] Skipped (disabled)")
            return self.result

        is_valid, error = await self.validate_inputs(inputs)
        if not is_valid:
            self.status = "failed"
            self.error = error
            raise ValueError(error)

        try:
            from .image_service import get_image_service  # type: ignore[import]

            topic = inputs.get("topic")
            tags = inputs.get("tags", [topic])

            logger.info("[SearchImagePhase] Searching images for '%s'", topic)

            # Use existing image service
            image_service = get_image_service()
            featured_image = await image_service.search_featured_image(topic=topic, keywords=tags)

            self.status = "completed"
            self.result = {
                "image_url": featured_image.get("url") if featured_image else None,
                "photographer": featured_image.get("photographer") if featured_image else None,
                "source": "Pexels" if featured_image else None,
            }

            logger.info("[SearchImagePhase] Found image: %s", self.result["image_url"] is not None)

            return self.result

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.warning("[SearchImagePhase] Error (non-fatal): %s", e, exc_info=True)
            # Image search failures are typically non-terminal, return null
            self.result = {
                "image_url": None,
                "photographer": None,
                "source": None,
            }
            return self.result


class GenerateSEOPhase(BasePhase):
    """
    Generate SEO metadata (title, description, keywords).

    PHASE 4 of the 7-stage pipeline - SEO optimization
    """

    @classmethod
    def get_phase_type(cls) -> str:
        return "generate_seo"

    @classmethod
    def get_phase_config(cls) -> PhaseConfig:
        return PhaseConfig(
            name="Generate SEO Metadata",
            description="Generate SEO-optimized title, description, and keywords",
            inputs=[
                PhaseInputSpec(
                    name="content",
                    type="str",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="Content to generate SEO for",
                    required=True,
                    accepts_from_phases=["generate_content"],
                ),
                PhaseInputSpec(
                    name="topic",
                    type="str",
                    source=PhaseInputType.USER_PROVIDED,
                    description="Content topic",
                    required=True,
                ),
            ],
            outputs=[
                PhaseOutputSpec(
                    name="seo_title",
                    type="str",
                    description="SEO title (max 60 chars)",
                ),
                PhaseOutputSpec(
                    name="seo_description",
                    type="str",
                    description="Meta description (max 160 chars)",
                ),
                PhaseOutputSpec(
                    name="seo_keywords",
                    type="list[str]",
                    description="Keywords for SERP",
                ),
            ],
            configurable_params={},
        )

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Generate SEO metadata"""

        is_valid, error = await self.validate_inputs(inputs)
        if not is_valid:
            self.status = "failed"
            self.error = error
            raise ValueError(error)

        try:
            from .seo_content_generator import get_seo_content_generator  # type: ignore[import]
            # Phase H step 5 (GH#95): site_config is seeded on the phase
            # inputs by the caller (process_content_generation_task for
            # the production path; tests wire it in explicitly).
            _sc = inputs["site_config"]

            content = inputs.get("content")
            topic = inputs.get("topic")

            logger.info("[GenerateSEOPhase] Generating SEO metadata for '%s'", topic)

            # Use existing SEO generator
            seo_generator = get_seo_content_generator(site_config=_sc)
            seo_assets = await seo_generator.generate_seo_assets(
                title=topic, content=content, topic=topic
            )

            self.status = "completed"
            self.result = {
                "seo_title": seo_assets.get("seo_title", ""),
                "seo_description": seo_assets.get("meta_description", ""),
                "seo_keywords": seo_assets.get("meta_keywords", []),
            }

            logger.info(
                "[GenerateSEOPhase] Generated SEO assets (title: %d chars, keywords: %d)",
                len(self.result["seo_title"]), len(self.result["seo_keywords"]),
            )

            return self.result

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.error("[GenerateSEOPhase] Error: %s", e, exc_info=True)
            raise


class CaptureTrainingDataPhase(BasePhase):
    """
    Capture quality evaluations and training data for continuous improvement.

    PHASE 6 of the 7-stage pipeline - Learning data collection
    """

    @classmethod
    def get_phase_type(cls) -> str:
        return "capture_training_data"

    @classmethod
    def get_phase_config(cls) -> PhaseConfig:
        return PhaseConfig(
            name="Capture Training Data",
            description="Store quality scores and metadata for learning and continuous improvement",
            inputs=[
                PhaseInputSpec(
                    name="content",
                    type="str",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="Generated content",
                    required=True,
                ),
                PhaseInputSpec(
                    name="overall_score",
                    type="float",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="Quality score from QualityEvaluationPhase",
                    required=True,
                    accepts_from_phases=["quality_evaluation"],
                ),
                PhaseInputSpec(
                    name="scores",
                    type="dict",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="Individual quality scores",
                    required=False,
                ),
                PhaseInputSpec(
                    name="topic",
                    type="str",
                    source=PhaseInputType.USER_PROVIDED,
                    description="Content topic",
                    required=True,
                ),
                PhaseInputSpec(
                    name="model_used",
                    type="str",
                    source=PhaseInputType.PHASE_OUTPUT,
                    description="Which model generated content",
                    required=False,
                ),
            ],
            outputs=[
                PhaseOutputSpec(
                    name="stored",
                    type="bool",
                    description="Whether training data was successfully stored",
                ),
            ],
            configurable_params={},
        )

    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Capture training data"""

        is_valid, error = await self.validate_inputs(inputs)
        if not is_valid:
            self.status = "failed"
            self.error = error
            raise ValueError(error)

        content = inputs.get("content", "")
        overall_score = inputs.get("overall_score")
        topic = inputs.get("topic", "")
        model_used = inputs.get("model_used", "unknown")
        scores = inputs.get("scores", {})

        # Feature flag: opt-out via config or environment variable.
        # Phase H step 5 (GH#95): site_config is seeded on inputs by
        # the caller.
        _sc_capture = inputs["site_config"]
        _capture_flag = _sc_capture.get("enable_training_capture", "true")
        if str(_capture_flag).lower() == "false":
            logger.info("[CaptureTrainingDataPhase] Skipped (ENABLE_TRAINING_CAPTURE=false)")
            self.status = "completed"
            self.result = {"stored": False, "reason": "disabled"}
            return self.result

        # Database service may be injected via config for phase-level capture
        database_service = config.get("database_service")

        try:
            execution_id = config.get("execution_id") or config.get("task_id") or self.phase_id

            payload = {
                "execution_id": str(execution_id),
                "user_request": f"Generate content on: {topic}",
                "intent": "content_generation",
                "business_state": {
                    "topic": topic,
                    "model_used": model_used,
                    "content_length": len(content),
                    "quality_scores": scores,
                },
                "execution_result": "success",
                "quality_score": float(overall_score) / 100 if overall_score is not None else None,
                "success": (overall_score or 0) >= 70,
                "tags": [topic] if topic else [],
                "source_agent": "capture_training_data_phase",
            }

            if database_service is not None:
                await database_service.create_orchestrator_training_data(payload)
                logger.info(
                    "[CaptureTrainingDataPhase] Stored to DB (topic: %s, score: %s, id: %s)",
                    topic, overall_score, execution_id,
                )
                self.status = "completed"
                self.result = {"stored": True}
            else:
                # No database_service injected — log the payload so it's not silently lost
                logger.info(
                    "[CaptureTrainingDataPhase] No database_service in config -- logging payload only (topic: %s, score: %s)",
                    topic, overall_score,
                )
                logger.debug("[CaptureTrainingDataPhase] Payload: %s", payload)
                self.status = "completed"
                self.result = {"stored": False, "reason": "no_database_service"}

            return self.result

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.error("[CaptureTrainingDataPhase] Error: %s", e, exc_info=True)
            # Non-terminal failure — content generation pipeline continues
            self.result = {"stored": False, "reason": "error"}
            return self.result
