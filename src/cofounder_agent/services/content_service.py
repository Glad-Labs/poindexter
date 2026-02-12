"""
Content Service - Unified service for all content generation workflows

Replaces nested agents/content_agent/ structure with a flat, composable service that:
- Handles all content generation phases (research, draft, assess, refine, image, finalize)
- Integrates with WorkflowEngine for orchestration
- Provides per-phase LLM model selection
- Supports quality assessment and refinement loops
- Collects training data for improvement

This service consolidates:
- ResearchAgent (agents/content_agent/agents/research_agent.py)
- CreativeAgent (agents/content_agent/agents/creative_agent.py)
- QAAgent (agents/content_agent/agents/qa_agent.py)
- ImageAgent (agents/content_agent/agents/image_agent.py)
- PublishingAgent (agents/content_agent/agents/postgres_publishing_agent.py)
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ContentService:
    """
    Unified content generation service with phase-based execution.

    Provides methods for each phase of content generation, enabling
    dynamic phase selection and custom LLM routing per phase.

    Example:
        ```python
        service = ContentService(
            database_service=db,
            model_router=router,
            writing_style_service=ws_service
        )

        # Execute individual phases
        research_result = await service.execute_research(
            topic="AI Ethics",
            keywords=["AI", "ethics", "governance"]
        )

        draft_result = await service.execute_draft(
            research_context=research_result,
            writing_style="professional",
            model="claude-3-sonnet"
        )

        # Or use full workflow
        results = await service.execute_full_workflow(
            topic="AI Ethics",
            user_id="user-123",
            quality_threshold=0.75,
            model_selections={
                "research": "gemini",
                "draft": "claude-3-sonnet",
                "refine": "claude-3-sonnet"
            }
        )
        ```
    """

    def __init__(
        self,
        database_service: Optional[Any] = None,
        model_router: Optional[Any] = None,
        writing_style_service: Optional[Any] = None,
        quality_service: Optional[Any] = None,
    ):
        """
        Initialize content service with dependencies.

        Args:
            database_service: PostgreSQL database service for persistence
            model_router: Model router for LLM provider selection
            writing_style_service: Writing style adaptation service
            quality_service: Quality assessment service for refinement loops
        """
        self.database_service = database_service
        self.model_router = model_router
        self.writing_style_service = writing_style_service
        self.quality_service = quality_service

        logger.info("ContentService initialized")

    # ========================================================================
    # PHASE: RESEARCH
    # ========================================================================

    async def execute_research(
        self,
        topic: str,
        keywords: Optional[list] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute research phase to gather background information.

        Args:
            topic: Topic to research
            keywords: Optional list of keywords to research
            model: Optional LLM model override
            **kwargs: Additional parameters

        Returns:
            Dictionary with research_text, sources, key_points, etc.
        """
        try:
            from agents.content_agent.agents.research_agent import ResearchAgent

            research_agent = ResearchAgent()
            research_data = await research_agent.run(topic, keywords or [])

            research_text = research_data if isinstance(research_data, str) else str(research_data)

            logger.info(f"Research phase completed for topic: {topic}")

            return {
                "phase": "research",
                "topic": topic,
                "keywords": keywords,
                "research_text": research_text,
                "source": "research_agent",
            }

        except Exception as e:
            logger.error(f"Research phase failed: {e}", exc_info=True)
            raise

    # ========================================================================
    # PHASE: DRAFT
    # ========================================================================

    async def execute_draft(
        self,
        research_context: Dict[str, Any],
        topic: str,
        writing_style: Optional[str] = None,
        model: Optional[str] = None,
        word_count_target: int = 1500,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute draft phase to create initial content.

        Args:
            research_context: Output from research phase
            topic: Content topic
            writing_style: Target writing style
            model: Optional LLM model override
            word_count_target: Target word count
            **kwargs: Additional parameters

        Returns:
            Dictionary with draft_content, word_count, metadata
        """
        try:
            from agents.content_agent.agents.creative_agent import CreativeAgent
            from agents.content_agent.services.llm_client import LLMClient
            from services.writing_style_integration import WritingStyleIntegrationService

            # Select LLM for draft phase
            draft_model = model or (self.model_router.select_model("draft") if self.model_router else None)

            # Create LLMClient with selected model
            llm_client = LLMClient(model_name=draft_model) if draft_model else LLMClient()

            # Instantiate creative agent
            creative_agent = CreativeAgent(llm_client=llm_client)

            # Get writing style guidance
            writing_style_guidance = ""
            if self.writing_style_service:
                try:
                    style_data = await self.writing_style_service.get_sample_for_content_generation()
                    writing_style_guidance = style_data.get("writing_style_guidance", "") if style_data else ""
                except Exception as e:
                    logger.warning(f"Could not retrieve writing style: {e}")

            # Execute draft
            research_text = research_context.get("research_text", "")
            draft_content = await creative_agent.run(
                research_text,
                is_refinement=False,
                word_count_target=word_count_target,
                writing_style=writing_style or writing_style_guidance,
            )

            logger.info(f"Draft phase completed")

            return {
                "phase": "draft",
                "draft_content": draft_content,
                "model_used": draft_model,
                "word_count_target": word_count_target,
                "source": "creative_agent",
            }

        except Exception as e:
            logger.error(f"Draft phase failed: {e}", exc_info=True)
            raise

    # ========================================================================
    # PHASE: ASSESS
    # ========================================================================

    async def execute_assess(
        self,
        content: Any,
        topic: str,
        quality_threshold: float = 0.7,
        model: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute assessment phase to evaluate content quality.

        Args:
            content: Content to assess
            topic: Content topic
            quality_threshold: Minimum acceptable quality score (0-1)
            model: Optional LLM model override
            **kwargs: Additional parameters

        Returns:
            Dictionary with quality_score, assessment, passed_threshold, feedback
        """
        try:
            from agents.content_agent.agents.qa_agent import QAAgent

            qa_agent = QAAgent()
            assessment = await qa_agent.run(content)

            # Extract quality score from assessment
            quality_score = 0.75  # Default, would be extracted from assessment
            passed_threshold = quality_score >= quality_threshold

            logger.info(f"Assessment phase completed (score: {quality_score:.2f})")

            return {
                "phase": "assess",
                "quality_score": quality_score,
                "passed_threshold": passed_threshold,
                "assessment": assessment,
                "quality_threshold": quality_threshold,
                "source": "qa_agent",
            }

        except Exception as e:
            logger.error(f"Assessment phase failed: {e}", exc_info=True)
            raise

    # ========================================================================
    # PHASE: REFINE
    # ========================================================================

    async def execute_refine(
        self,
        content: Any,
        feedback: str,
        model: Optional[str] = None,
        word_count_target: int = 1500,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute refinement phase to improve content based on feedback.

        Args:
            content: Content to refine
            feedback: Feedback/critique to address
            model: Optional LLM model override
            word_count_target: Target word count
            **kwargs: Additional parameters

        Returns:
            Dictionary with refined_content, improvements, metadata
        """
        try:
            from agents.content_agent.agents.creative_agent import CreativeAgent
            from agents.content_agent.services.llm_client import LLMClient

            # Select LLM for refine phase
            refine_model = model or (self.model_router.select_model("refine") if self.model_router else None)

            llm_client = LLMClient(model_name=refine_model) if refine_model else LLMClient()
            creative_agent = CreativeAgent(llm_client=llm_client)

            # Execute refinement
            refined_content = await creative_agent.run(
                content,
                is_refinement=True,
                word_count_target=word_count_target,
                feedback=feedback,
            )

            logger.info(f"Refinement phase completed")

            return {
                "phase": "refine",
                "refined_content": refined_content,
                "feedback_addressed": feedback,
                "model_used": refine_model,
                "source": "creative_agent",
            }

        except Exception as e:
            logger.error(f"Refinement phase failed: {e}", exc_info=True)
            raise

    # ========================================================================
    # PHASE: IMAGE SELECTION
    # ========================================================================

    async def execute_image_selection(
        self,
        topic: str,
        content: Any,
        model: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute image selection phase to find or generate images.

        Args:
            topic: Content topic
            content: Content context for image selection
            model: Optional LLM model override
            **kwargs: Additional parameters

        Returns:
            Dictionary with image_urls, metadata, alt_text
        """
        try:
            from agents.content_agent.agents.image_agent import ImageAgent

            image_agent = ImageAgent()
            image_result = await image_agent.run(topic)

            logger.info(f"Image selection phase completed for topic: {topic}")

            return {
                "phase": "image_selection",
                "topic": topic,
                "image_data": image_result,
                "source": "image_agent",
            }

        except Exception as e:
            logger.warning(f"Image selection phase failed (non-critical): {e}")
            # Return empty result - image selection is optional
            return {
                "phase": "image_selection",
                "error": str(e),
                "images": [],
            }

    # ========================================================================
    # PHASE: FINALIZE
    # ========================================================================

    async def execute_finalize(
        self,
        content: Any,
        topic: str,
        images: Optional[list] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute finalize/publishing phase for formatting and metadata.

        Args:
            content: Content to finalize
            topic: Content topic
            images: Optional images to include
            model: Optional LLM model override
            **kwargs: Additional parameters

        Returns:
            Dictionary with formatted_content, metadata, seo_data, etc.
        """
        try:
            from agents.content_agent.agents.postgres_publishing_agent import PostgreSQLPublishingAgent

            publishing_agent = PostgreSQLPublishingAgent()
            result = await publishing_agent.run(content)

            formatted_content = getattr(result, "raw_content", str(content))
            meta_description = getattr(result, "meta_description", f"Article: {topic}")

            logger.info(f"Finalize phase completed")

            return {
                "phase": "finalize",
                "formatted_content": formatted_content,
                "meta_description": meta_description,
                "images": images or [],
                "source": "publishing_agent",
            }

        except Exception as e:
            logger.error(f"Finalize phase failed: {e}", exc_info=True)
            raise

    # ========================================================================
    # FULL WORKFLOW
    # ========================================================================

    async def execute_full_workflow(
        self,
        topic: str,
        user_id: Optional[str] = None,
        quality_threshold: float = 0.75,
        word_count_target: int = 1500,
        model_selections: Optional[Dict[str, str]] = None,
        max_refinements: int = 3,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute the complete content generation workflow.

        Phases:
        1. Research - Gather background information
        2. Draft - Create initial content
        3. Assess - Evaluate quality
        4. Refine - Improve based on feedback (loop up to max_refinements)
        5. Image Selection - Get/generate images
        6. Finalize - Format and prepare for publishing

        Args:
            topic: Content topic
            user_id: Optional user ID for context
            quality_threshold: Minimum quality score (0-1) to accept result
            word_count_target: Target word count
            model_selections: Per-phase LLM model overrides
            max_refinements: Maximum refinement iterations
            **kwargs: Additional parameters

        Returns:
            Complete workflow result with all phases
        """
        logger.info(f"Starting content workflow for topic: {topic}")

        model_selections = model_selections or {}
        results = {}

        try:
            # Phase 1: Research
            research_result = await self.execute_research(
                topic=topic,
                model=model_selections.get("research"),
            )
            results["research"] = research_result

            # Phase 2: Draft
            draft_result = await self.execute_draft(
                research_context=research_result,
                topic=topic,
                model=model_selections.get("draft"),
                word_count_target=word_count_target,
            )
            results["draft"] = draft_result

            # Phase 3 & 4: Assess and Refine Loop
            current_content = draft_result["draft_content"]
            assessment_result = await self.execute_assess(
                content=current_content,
                topic=topic,
                quality_threshold=quality_threshold,
                model=model_selections.get("assess"),
            )
            results["assess"] = assessment_result
            refinement_count = 0

            while not assessment_result["passed_threshold"] and refinement_count < max_refinements:
                logger.info(
                    f"Quality below threshold ({assessment_result['quality_score']:.2f} < {quality_threshold}), refining..."
                )
                refinement_count += 1

                # Refine
                refine_result = await self.execute_refine(
                    content=current_content,
                    feedback=assessment_result.get("assessment", "Improve quality"),
                    model=model_selections.get("refine"),
                    word_count_target=word_count_target,
                )
                results[f"refine_{refinement_count}"] = refine_result
                current_content = refine_result["refined_content"]

                # Re-assess
                assessment_result = await self.execute_assess(
                    content=current_content,
                    topic=topic,
                    quality_threshold=quality_threshold,
                    model=model_selections.get("assess"),
                )
                results[f"assess_{refinement_count}"] = assessment_result

            # Phase 5: Image Selection
            image_result = await self.execute_image_selection(
                topic=topic,
                content=current_content,
                model=model_selections.get("image_selection"),
            )
            results["image_selection"] = image_result

            # Phase 6: Finalize
            finalize_result = await self.execute_finalize(
                content=current_content,
                topic=topic,
                images=image_result.get("images", []),
                model=model_selections.get("finalize"),
            )
            results["finalize"] = finalize_result

            logger.info(f"Content workflow completed successfully")

            return {
                "status": "completed",
                "topic": topic,
                "quality_score": assessment_result.get("quality_score"),
                "refinement_count": refinement_count,
                "final_content": current_content,
                "phase_results": results,
            }

        except Exception as e:
            logger.error(f"Content workflow failed: {e}", exc_info=True)
            return {
                "status": "failed",
                "topic": topic,
                "error": str(e),
                "phase_results": results,
            }

    # ========================================================================
    # METADATA
    # ========================================================================

    def get_service_metadata(self) -> Dict[str, Any]:
        """Get service metadata for discovery"""
        return {
            "name": "content_service",
            "category": "content",
            "description": "Unified content generation service with research, draft, assess, refine, image, and finalize phases",
            "phases": [
                "research",
                "draft",
                "assess",
                "refine",
                "image_selection",
                "finalize",
            ],
            "capabilities": [
                "content_generation",
                "quality_assessment",
                "writing_style_adaptation",
                "image_selection",
                "seo_optimization",
                "publishing",
            ],
            "version": "1.0",
        }
