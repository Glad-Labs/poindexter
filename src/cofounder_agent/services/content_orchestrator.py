"""
Content Orchestrator Service - Phase 5 Implementation

Master orchestrator coordinating 7 specialized agents with MANDATORY HUMAN APPROVAL GATE.

Pipeline: Research → Draft → QA Loop → Images → Format → ⏳ AWAITING HUMAN APPROVAL

This is Phase 5: Real Content Generation with Human-in-the-Loop Approval
All content stored directly to PostgreSQL (no Strapi required)
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

# Import constraint utilities for Tier 1-3 word count and style management
from ..utils.constraint_utils import (
    ContentConstraints,
    ConstraintCompliance,
    extract_constraints_from_request,
    inject_constraints_into_prompt,
    count_words_in_content,
    validate_constraints,
    calculate_phase_targets,
    check_tolerance,
    apply_strict_mode,
    merge_compliance_reports,
    format_compliance_report,
)

logger = logging.getLogger(__name__)


class ContentOrchestrator:
    """
    Master orchestrator for the complete content generation pipeline with human approval gate.

    Implements the 7-agent pipeline with QA feedback loop and mandatory human review
    before anything is published to PostgreSQL database.

    All content is stored directly to PostgreSQL tables:
    - posts: Blog articles and content
    - media: Images and visual assets
    - categories: Content organization
    - tags: Content tagging

    Pipeline stops at "awaiting_approval" status. Human must approve via:
        POST /api/content/tasks/{task_id}/approve with human decision
    """

    def __init__(self, task_store=None):
        """Initialize the orchestrator"""
        self.task_store = task_store
        self.pipelines_started = 0
        self.pipelines_completed = 0
        logger.info("✅ ContentOrchestrator initialized (Phase 5 - Human Approval)")

    async def run(
        self,
        topic: str,
        keywords: Optional[List[str]] = None,
        style: str = "educational",
        tone: str = "professional",
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        content_constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute the complete content generation pipeline with HUMAN APPROVAL GATE.

        Pipeline STOPS at "awaiting_approval" status. Human must approve via API.

        NOW WITH CONSTRAINT SUPPORT (Tier 1-3):
        - Word count targets and tolerance
        - Writing style guidance and validation
        - Per-phase word count overrides
        - Strict mode enforcement
        - Detailed compliance reporting

        Args:
            topic: Content topic
            keywords: SEO keywords (optional)
            style: Content style (for backward compatibility)
            tone: Tone
            task_id: Optional task ID (generated if not provided)
            metadata: Additional metadata
            content_constraints: Optional dict with word_count, writing_style, word_count_tolerance, etc.

        Returns:
            Dict with task info, status = "awaiting_approval", and constraint_compliance metrics
        """
        self.pipelines_started += 1
        logger.info(f"🚀 Phase 5 Pipeline START: {topic} (pipeline #{self.pipelines_started})")

        try:
            # ====================================================================
            # EXTRACT & INITIALIZE CONSTRAINTS (Tier 1-3)
            # ====================================================================
            constraints = ContentConstraints(
                word_count=(
                    content_constraints.get("word_count", 1500) if content_constraints else 1500
                ),
                writing_style=(
                    content_constraints.get("writing_style", style)
                    if content_constraints
                    else style
                ),
                word_count_tolerance=(
                    content_constraints.get("word_count_tolerance", 10)
                    if content_constraints
                    else 10
                ),
                per_phase_overrides=(
                    content_constraints.get("per_phase_overrides") if content_constraints else None
                ),
                strict_mode=(
                    content_constraints.get("strict_mode", False) if content_constraints else False
                ),
            )

            logger.info(
                f"📋 Content Constraints: word_count={constraints.word_count}, style={constraints.writing_style}, tolerance={constraints.word_count_tolerance}%, strict_mode={constraints.strict_mode}"
            )

            # Calculate phase-level word count targets (Tier 2)
            phase_targets = calculate_phase_targets(
                constraints.word_count, constraints, num_phases=5
            )
            logger.info(f"📊 Phase targets: {phase_targets}")

            # Track compliance across phases (Tier 2)
            compliance_reports: List[ConstraintCompliance] = []

            # Generate task ID if not provided
            if not task_id:
                import uuid

                task_id = f"task_{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex[:6]}"

            # Update status: processing
            if self.task_store:
                await self.task_store.update_task(
                    task_id,
                    {
                        "status": "processing",
                        "task_metadata": {
                            "stage": "research",
                            "percentage": 10,
                            "message": "🚀 Starting research...",
                        },
                    },
                )

            # ====================================================================
            # STAGE 1: RESEARCH (10% → 25%)
            # ====================================================================
            try:
                logger.info(f"📚 STAGE 1: Research Agent")
                research_data = await self._run_research(
                    topic,
                    keywords or [topic],
                    constraints=constraints,
                    phase_target=phase_targets.get("research", constraints.word_count // 5),
                )

                # Validate research output against constraints (Tier 1)
                research_compliance = validate_constraints(
                    research_data,
                    constraints,
                    phase_name="research",
                    word_count_target=phase_targets.get("research"),
                )
                compliance_reports.append(research_compliance)
                logger.info(
                    f"📊 Research compliance: {research_compliance.word_count_actual}/{research_compliance.word_count_target} words"
                )
            except Exception as stage_err:
                logger.error(f"STAGE 1 ERROR: {type(stage_err).__name__}: {stage_err}", exc_info=True)
                raise

            if self.task_store:
                await self.task_store.update_task(
                    task_id,
                    {
                        "status": "processing",
                        "task_metadata": {
                            "stage": "creative",
                            "percentage": 25,
                            "message": "✍️  Generating draft...",
                        },
                    },
                )

            # ====================================================================
            # STAGE 2: CREATIVE DRAFT (25% → 45%)
            # ====================================================================
            try:
                logger.info(f"✍️  STAGE 2: Creative Agent (Initial Draft)")
                draft_content = await self._run_creative_initial(
                    topic,
                    research_data,
                    style,
                    tone,
                    constraints=constraints,
                    phase_target=phase_targets.get("creative", constraints.word_count // 5),
                )

                # Validate creative output against constraints (Tier 1)
                creative_compliance = validate_constraints(
                    draft_content.body if hasattr(draft_content, "body") else str(draft_content),
                    constraints,
                    phase_name="creative",
                    word_count_target=phase_targets.get("creative"),
                )
                compliance_reports.append(creative_compliance)
                logger.info(
                    f"📊 Creative compliance: {creative_compliance.word_count_actual}/{creative_compliance.word_count_target} words"
                )
            except Exception as stage_err:
                logger.error(f"STAGE 2 ERROR: {type(stage_err).__name__}: {stage_err}", exc_info=True)
                raise

            if self.task_store:
                await self.task_store.update_task(
                    task_id,
                    {
                        "status": "processing",
                        "task_metadata": {
                            "stage": "qa",
                            "percentage": 45,
                            "message": "🔍 Quality review...",
                        },
                    },
                )

            # ====================================================================
            # STAGE 3: QA REVIEW LOOP (45% → 60%)
            # ====================================================================
            logger.info(f"🔍 STAGE 3: QA Agent (Review & Refinement Loop)")
            final_content, qa_feedback, quality_score = await self._run_qa_loop(
                topic,
                draft_content,
                research_data,
                style,
                tone,
                constraints=constraints,
                phase_target=phase_targets.get("qa", constraints.word_count // 5),
            )

            # Validate QA output against constraints (Tier 1)
            qa_compliance = validate_constraints(
                final_content.body if hasattr(final_content, "body") else str(final_content),
                constraints,
                phase_name="qa",
                word_count_target=phase_targets.get("qa"),
            )
            compliance_reports.append(qa_compliance)
            logger.info(
                f"📊 QA compliance: {qa_compliance.word_count_actual}/{qa_compliance.word_count_target} words"
            )

            if self.task_store:
                await self.task_store.update_task(
                    task_id,
                    {
                        "status": "processing",
                        "task_metadata": {
                            "stage": "images",
                            "percentage": 60,
                            "message": "🖼️  Selecting images...",
                        },
                    },
                )

            # ====================================================================
            # STAGE 4: IMAGE SELECTION (60% → 75%)
            # ====================================================================
            logger.info(f"🖼️  STAGE 4: Image Agent (Featured Image Selection)")
            featured_image_url = await self._run_image_selection(topic, final_content)
            logger.info(
                f"   Result: featured_image_url = {featured_image_url[:100] if featured_image_url else 'NONE/EMPTY'}"
            )

            if self.task_store:
                await self.task_store.update_task(
                    task_id,
                    {
                        "status": "processing",
                        "task_metadata": {
                            "stage": "formatting",
                            "percentage": 75,
                            "message": "📝 Formatting...",
                        },
                    },
                )

            # ====================================================================
            # STAGE 5: FORMATTING (75% → 90%)
            # ====================================================================
            logger.info(f"📝 STAGE 5: Publishing Agent (PostgreSQL Formatting)")
            formatted_content, excerpt = await self._run_formatting(
                topic, final_content, featured_image_url
            )

            # ====================================================================
            # STAGE 6: AWAITING HUMAN APPROVAL (90% → 100%) - **CRITICAL GATE**
            # ====================================================================
            logger.info(f"⏳ STAGE 6: AWAITING HUMAN APPROVAL (Mandatory Review Required)")

            # Aggregate compliance reports from all phases (Tier 2)
            from utils.constraint_utils import merge_compliance_reports

            overall_compliance = merge_compliance_reports(compliance_reports)

            # Check strict mode (Tier 2)
            strict_mode_valid, strict_mode_error = apply_strict_mode(overall_compliance)

            if not strict_mode_valid:
                logger.warning(f"⚠️ STRICT MODE VIOLATION: {strict_mode_error}")
                # In strict mode, we could fail the task here, but for now we'll log and continue
                # This allows human to still review before final decision

            if self.task_store:
                logger.info(
                    f"💾 Storing task with featured_image_url: {featured_image_url[:100] if featured_image_url else 'NONE'}"
                )
                await self.task_store.update_task(
                    task_id,
                    {
                        "status": "awaiting_approval",  # ✅ **STOPS HERE**
                        "approval_status": "awaiting_review",  # ✅ Requires human decision
                        "task_metadata": {
                            "stage": "awaiting_approval",
                            "percentage": 100,
                            "message": "⏳ Awaiting human approval",
                            "content": formatted_content,
                            "excerpt": excerpt,
                            "featured_image_url": featured_image_url,
                            "qa_feedback": qa_feedback,
                            "quality_score": quality_score,
                            "constraint_compliance": {
                                "word_count_actual": overall_compliance.word_count_actual,
                                "word_count_target": overall_compliance.word_count_target,
                                "word_count_within_tolerance": overall_compliance.word_count_within_tolerance,
                                "word_count_percentage": overall_compliance.word_count_percentage,
                                "writing_style": overall_compliance.writing_style_applied,
                                "strict_mode_enforced": overall_compliance.strict_mode_enforced,
                                "violation_message": overall_compliance.violation_message,
                            },
                        },
                    },
                )

            result = {
                "task_id": task_id,
                "status": "awaiting_approval",  # ✅ **REQUIRES HUMAN DECISION**
                "approval_status": "awaiting_review",
                "content": formatted_content,
                "excerpt": excerpt,
                "featured_image_url": featured_image_url,
                "qa_feedback": qa_feedback,
                "quality_score": quality_score,
                "constraint_compliance": {
                    "word_count_actual": overall_compliance.word_count_actual,
                    "word_count_target": overall_compliance.word_count_target,
                    "word_count_within_tolerance": overall_compliance.word_count_within_tolerance,
                    "word_count_percentage": overall_compliance.word_count_percentage,
                    "writing_style": overall_compliance.writing_style_applied,
                    "strict_mode_enforced": overall_compliance.strict_mode_enforced,
                    "violation_message": overall_compliance.violation_message,
                },
                "message": "✅ Content ready for human review. Human approval required before publishing.",
                "next_action": f"POST /api/content/tasks/{task_id}/approve with human decision",
            }

            self.pipelines_completed += 1
            logger.info(
                f"✅ Phase 5 Pipeline COMPLETE: Awaiting human approval. Quality: {quality_score}/100"
            )
            return result

        except Exception as e:
            logger.error(f"❌ Pipeline error: {e}", exc_info=True)
            
            # Add detailed context to error
            error_context = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "error_module": type(e).__module__,
            }
            
            logger.error(f"  Error Context: {error_context}")
            
            if self.task_store:
                await self.task_store.update_task(
                    task_id, {
                        "status": "failed",
                        "task_metadata": {
                            "error": str(e),
                            "error_context": error_context
                        }
                    }
                )
            raise

    async def _run_research(
        self,
        topic: str,
        keywords: List[str],
        constraints: Optional[ContentConstraints] = None,
        phase_target: Optional[int] = None,
    ) -> str:
        """Run research agent (Stage 1) with constraint support"""
        try:
            logger.info(f"📚 Research: Gathering information for '{topic}'")

            from agents.content_agent.agents.research_agent import ResearchAgent

            research_agent = ResearchAgent()

            # Inject constraint instructions into research (Tier 1)
            base_research_prompt = (
                f"Research the following topic: {topic}\nKeywords: {', '.join(keywords[:5])}"
            )
            if constraints:
                research_prompt = inject_constraints_into_prompt(
                    base_research_prompt,
                    constraints,
                    phase_name="research",
                    word_count_target=phase_target,
                )
            else:
                research_prompt = base_research_prompt

            # Run research (research_agent.run is async, so await it directly)
            research_result = await research_agent.run(topic, keywords[:5])  # Limit to 5 keywords

            result_text = (
                research_result if isinstance(research_result, str) else str(research_result)
            )
            logger.info(
                f"✅ Research complete: {len(result_text)} characters, {count_words_in_content(result_text)} words"
            )
            return result_text

        except Exception as e:
            logger.error(f"❌ Research stage failed: {e}")
            raise

    async def _run_creative_initial(
        self,
        topic: str,
        research_data: str,
        style: str,
        tone: str,
        constraints: Optional[ContentConstraints] = None,
        phase_target: Optional[int] = None,
    ) -> Any:
        """Run creative agent for initial draft (Stage 2) with constraint support"""
        try:
            logger.info(f"✍️ Creative: Drafting content for '{topic}'")

            from agents.content_agent.agents.creative_agent import CreativeAgent
            from agents.content_agent.services.llm_client import LLMClient
            from agents.content_agent.utils.data_models import BlogPost

            llm_client = LLMClient()
            creative_agent = CreativeAgent(llm_client=llm_client)

            # Create BlogPost object for creative agent
            post = BlogPost(
                topic=topic,
                primary_keyword=topic,
                target_audience="general",
                category="general",
                status="draft",
                research_data=research_data,
                writing_style=style,  # Pass writing style from request
            )

            # Inject constraint instructions before creative generation (Tier 1)
            if constraints:
                constraint_guidance = f"\n[CONSTRAINT GUIDANCE]\nTarget word count: {phase_target or constraints.word_count} words (±{constraints.word_count_tolerance}%)\nWriting style: {constraints.writing_style}"
                post.research_data = constraint_guidance + "\n\n" + research_data

            # Run creative agent (creative_agent.run is async, so await it directly)
            draft_post = await creative_agent.run(post, is_refinement=False)

            draft_text = draft_post.body if hasattr(draft_post, "body") else str(draft_post)
            logger.info(f"✅ Draft complete: {count_words_in_content(draft_text)} words")
            return draft_post

        except Exception as e:
            logger.error(f"❌ Creative stage failed: {e}")
            raise

    async def _run_qa_loop(
        self,
        topic: str,
        draft_content: Any,
        research_data: str,
        style: str,
        tone: str,
        constraints: Optional[ContentConstraints] = None,
        phase_target: Optional[int] = None,
        max_iterations: int = 2,
    ) -> tuple:
        """Run QA agent with feedback loop (Stage 3) with constraint support"""
        try:
            logger.info(f"🔍 QA: Reviewing content quality using unified service")

            from agents.content_agent.agents.creative_agent import CreativeAgent
            from agents.content_agent.services.llm_client import LLMClient
            from cofounder_agent.services.quality_service import (
                get_content_quality_service,
                EvaluationMethod,
            )
            from cofounder_agent.services.database_service import get_database_service

            llm_client = LLMClient()
            creative_agent = CreativeAgent(llm_client=llm_client)
            database_service = get_database_service()
            quality_service = get_content_quality_service(database_service=database_service)

            content = draft_content
            feedback = ""
            quality_score = 75  # Default score
            iteration = 0

            while iteration < max_iterations:
                iteration += 1
                logger.info(f"  QA Iteration {iteration}/{max_iterations}")

                # Use unified ContentQualityService for evaluation
                quality_result = await quality_service.evaluate(
                    content=getattr(content, "raw_content", str(content)),
                    context={"topic": topic},
                    method=EvaluationMethod.HYBRID,  # Use robust hybrid method
                )

                # Parse quality result
                approval_bool = quality_result.passing
                feedback = quality_result.feedback
                quality_score = int(quality_result.overall_score * 10)  # Convert to 0-100 scale

                # Check constraint compliance (Tier 1)
                if constraints:
                    content_text = getattr(
                        content, "body", getattr(content, "raw_content", str(content))
                    )
                    compliance = validate_constraints(
                        content_text, constraints, phase_name="qa", word_count_target=phase_target
                    )
                    if not compliance.word_count_within_tolerance:
                        logger.warning(
                            f"⚠️  QA: Word count constraint violated - {compliance.violation_message}"
                        )
                        approval_bool = False
                        feedback += f" [CONSTRAINT VIOLATION: {compliance.violation_message}]"

                if approval_bool:
                    logger.info(
                        f"✅ QA Approved (iteration {iteration}, score: {quality_score}/100)"
                    )
                    return content, feedback, quality_score
                else:
                    logger.warning(f"⚠️  QA Rejected - Feedback: {feedback[:100]}...")

                    if iteration < max_iterations:
                        logger.info(
                            f"  Refining content based on feedback (iteration {iteration})..."
                        )

                        # Refine based on feedback
                        content = await creative_agent.run(content, is_refinement=True)
                        logger.info(f"  Refinement complete, re-submitting to QA...")

            # After max iterations
            logger.warning(f"⚠️  QA loop completed after {max_iterations} iterations")
            return content, feedback, quality_score

        except Exception as e:
            logger.error(f"❌ QA stage failed: {e}")
            raise

    async def _run_image_selection(self, topic: str, content: Any) -> Optional[str]:
        """Run image agent (Stage 4) - Uses unified ImageService"""
        try:
            logger.info(f"🖼️ Images: Selecting visual assets using unified service")

            from cofounder_agent.services.image_service import get_image_service

            image_service = get_image_service()

            # Use unified ImageService to search for featured image
            featured_image = await image_service.search_featured_image(topic=topic, keywords=[])

            # Extract featured image URL and metadata
            image_url = None
            if featured_image:
                image_url = featured_image.url
                logger.info(f"✅ Featured image selected: {image_url[:60]}...")
                logger.info(f"   Photographer: {featured_image.photographer}")
            else:
                logger.warning(f"⚠️  No image found on Pexels, continuing without featured image")

            return image_url

        except Exception as e:
            logger.warning(f"⚠️  Image stage warning: {e}")
            logger.info("Continuing pipeline without images...")
            return None  # Continue without image

    async def _run_formatting(
        self, topic: str, content: Any, featured_image_url: Optional[str]
    ) -> tuple:
        """Run publishing agent for formatting (Stage 5)"""
        try:
            logger.info(f"📝 Formatting: Preparing content for PostgreSQL storage")

            # Use PostgreSQL-based publishing agent (no Strapi required)
            from agents.content_agent.agents.postgres_publishing_agent import (
                PostgreSQLPublishingAgent,
            )

            publishing_agent = PostgreSQLPublishingAgent()

            # Run publishing agent (for formatting, not actually publishing yet)
            result_post = await publishing_agent.run(content)

            # Extract formatted content and excerpt
            formatted_content = getattr(result_post, "raw_content", str(content))
            excerpt = getattr(result_post, "meta_description", f"Article about {topic}")

            logger.info(f"✅ Formatting complete")
            return formatted_content, excerpt

        except Exception as e:
            logger.warning(f"⚠️  Formatting stage warning: {e}")
            logger.info("Continuing with unformatted content...")
            # Return content as-is if formatting fails
            return str(content), f"Article about {topic}"


# ============================================================================
# Singleton instance
# ============================================================================

_orchestrator_instance: Optional[ContentOrchestrator] = None


def get_content_orchestrator(task_store=None) -> ContentOrchestrator:
    """Get or create the orchestrator instance"""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = ContentOrchestrator(task_store)
    return _orchestrator_instance
