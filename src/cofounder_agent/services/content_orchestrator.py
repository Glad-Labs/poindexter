"""
Content Orchestrator Service - Phase 5 Implementation

Master orchestrator coordinating 7 specialized agents with MANDATORY HUMAN APPROVAL GATE.

Pipeline: Research → Draft → QA Loop → Images → Format → ⏳ AWAITING HUMAN APPROVAL

This is Phase 5: Real Content Generation with Human-in-the-Loop Approval
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class ContentOrchestrator:
    """
    Master orchestrator for the complete content generation pipeline with human approval gate.
    
    Implements the 7-agent pipeline with QA feedback loop and mandatory human review
    before anything is published to Strapi CMS.
    
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
    ) -> Dict[str, Any]:
        """
        Execute the complete content generation pipeline with HUMAN APPROVAL GATE.
        
        Pipeline STOPS at "awaiting_approval" status. Human must approve via API.
        
        Args:
            topic: Content topic
            keywords: SEO keywords (optional)
            style: Content style
            tone: Tone
            task_id: Optional task ID (generated if not provided)
            metadata: Additional metadata
        
        Returns:
            Dict with task info and status = "awaiting_approval"
        """
        self.pipelines_started += 1
        logger.info(f"🚀 Phase 5 Pipeline START: {topic} (pipeline #{self.pipelines_started})")

        try:
            # Generate task ID if not provided
            if not task_id:
                import uuid
                task_id = f"task_{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex[:6]}"
            
            # Update status: processing
            if self.task_store:
                self.task_store.update_task_status(
                    task_id, "processing",
                    progress={"stage": "research", "percentage": 10, "message": "🚀 Starting research..."}
                )

            # ====================================================================
            # STAGE 1: RESEARCH (10% → 25%)
            # ====================================================================
            logger.info(f"📚 STAGE 1: Research Agent")
            research_data = await self._run_research(topic, keywords or [topic])
            
            if self.task_store:
                self.task_store.update_task_status(
                    task_id, "processing",
                    progress={"stage": "creative", "percentage": 25, "message": "✍️  Generating draft..."}
                )

            # ====================================================================
            # STAGE 2: CREATIVE DRAFT (25% → 45%)
            # ====================================================================
            logger.info(f"✍️  STAGE 2: Creative Agent (Initial Draft)")
            draft_content = await self._run_creative_initial(topic, research_data, style, tone)
            
            if self.task_store:
                self.task_store.update_task_status(
                    task_id, "processing",
                    progress={"stage": "qa", "percentage": 45, "message": "🔍 Quality review..."}
                )

            # ====================================================================
            # STAGE 3: QA REVIEW LOOP (45% → 60%)
            # ====================================================================
            logger.info(f"🔍 STAGE 3: QA Agent (Review & Refinement Loop)")
            final_content, qa_feedback, quality_score = await self._run_qa_loop(
                topic, draft_content, research_data, style, tone
            )
            
            if self.task_store:
                self.task_store.update_task_status(
                    task_id, "processing",
                    progress={"stage": "images", "percentage": 60, "message": "🖼️  Selecting images..."}
                )

            # ====================================================================
            # STAGE 4: IMAGE SELECTION (60% → 75%)
            # ====================================================================
            logger.info(f"🖼️  STAGE 4: Image Agent (Featured Image Selection)")
            featured_image_url = await self._run_image_selection(topic, final_content)
            
            if self.task_store:
                self.task_store.update_task_status(
                    task_id, "processing",
                    progress={"stage": "formatting", "percentage": 75, "message": "📝 Formatting..."}
                )

            # ====================================================================
            # STAGE 5: FORMATTING (75% → 90%)
            # ====================================================================
            logger.info(f"📝 STAGE 5: Publishing Agent (Strapi Formatting)")
            formatted_content, excerpt = await self._run_formatting(topic, final_content, featured_image_url)
            
            # ====================================================================
            # STAGE 6: AWAITING HUMAN APPROVAL (90% → 100%) - **CRITICAL GATE**
            # ====================================================================
            logger.info(f"⏳ STAGE 6: AWAITING HUMAN APPROVAL (Mandatory Review Required)")
            
            if self.task_store:
                self.task_store.update_task({
                    "task_id": task_id,
                    "status": "awaiting_approval",  # ✅ **STOPS HERE**
                    "approval_status": "awaiting_review",  # ✅ Requires human decision
                    "content": formatted_content,
                    "excerpt": excerpt,
                    "featured_image_url": featured_image_url,
                    "qa_feedback": qa_feedback,
                    "quality_score": quality_score,
                    "progress": {
                        "stage": "awaiting_approval",
                        "percentage": 100,
                        "message": "⏳ Awaiting human approval"
                    }
                })
            
            result = {
                "task_id": task_id,
                "status": "awaiting_approval",  # ✅ **REQUIRES HUMAN DECISION**
                "approval_status": "awaiting_review",
                "content": formatted_content,
                "excerpt": excerpt,
                "featured_image_url": featured_image_url,
                "qa_feedback": qa_feedback,
                "quality_score": quality_score,
                "message": "✅ Content ready for human review. Human approval required before publishing.",
                "next_action": f"POST /api/content/tasks/{task_id}/approve with human decision",
            }
            
            self.pipelines_completed += 1
            logger.info(f"✅ Phase 5 Pipeline COMPLETE: Awaiting human approval. Quality: {quality_score}/100")
            return result

        except Exception as e:
            logger.error(f"❌ Pipeline error: {e}", exc_info=True)
            if self.task_store:
                self.task_store.update_task_status(task_id, "failed", error=str(e))
            raise

    async def _run_research(self, topic: str, keywords: List[str]) -> str:
        """Run research agent (Stage 1)"""
        try:
            logger.info(f"📚 Research: Gathering information for '{topic}'")
            
            from src.agents.content_agent.agents.research_agent import ResearchAgent
            research_agent = ResearchAgent()
            
            # Run research (returns search results as string)
            research_result = await asyncio.to_thread(
                research_agent.run,
                topic,
                keywords[:5]  # Limit to 5 keywords
            )
            
            result_text = research_result if isinstance(research_result, str) else str(research_result)
            logger.info(f"✅ Research complete: {len(result_text)} characters")
            return result_text

        except Exception as e:
            logger.error(f"❌ Research stage failed: {e}")
            raise

    async def _run_creative_initial(
        self, topic: str, research_data: str, style: str, tone: str
    ) -> Any:
        """Run creative agent for initial draft (Stage 2)"""
        try:
            logger.info(f"✍️  Creative: Generating initial draft for '{topic}'")
            
            from src.agents.content_agent.agents.creative_agent import CreativeAgent
            from src.agents.content_agent.services.llm_client import LLMClient
            from src.agents.content_agent.utils.data_models import BlogPost
            
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
            )
            
            # Run creative agent (returns BlogPost)
            draft_post = await asyncio.to_thread(
                creative_agent.run,
                post,
                is_refinement=False
            )
            
            logger.info(f"✅ Draft complete")
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
        max_iterations: int = 2,
    ) -> tuple:
        """Run QA agent with feedback loop (Stage 3)"""
        try:
            logger.info(f"🔍 QA: Reviewing content quality")
            
            from src.agents.content_agent.agents.qa_agent import QAAgent
            from src.agents.content_agent.agents.creative_agent import CreativeAgent
            from src.agents.content_agent.services.llm_client import LLMClient
            
            llm_client = LLMClient()
            qa_agent = QAAgent(llm_client=llm_client)
            creative_agent = CreativeAgent(llm_client=llm_client)
            
            content = draft_content
            feedback = ""
            quality_score = 75  # Default score
            iteration = 0
            
            while iteration < max_iterations:
                iteration += 1
                logger.info(f"  QA Iteration {iteration}/{max_iterations}")
                
                # Run QA agent (returns tuple[bool, str])
                qa_result = await asyncio.to_thread(
                    qa_agent.run,
                    content,
                    getattr(content, 'raw_content', str(content))
                )
                
                # Parse QA result
                if isinstance(qa_result, tuple) and len(qa_result) == 2:
                    approval_bool, feedback = qa_result
                    # Try to extract quality score from feedback
                    if "score:" in feedback.lower():
                        try:
                            score_str = feedback.split("score:")[1].split("/")[0].strip()
                            quality_score = int(score_str)
                        except:
                            quality_score = 75
                else:
                    approval_bool = False
                    feedback = str(qa_result)
                
                if approval_bool:
                    logger.info(f"✅ QA Approved (iteration {iteration}, score: {quality_score}/100)")
                    return content, feedback, quality_score
                else:
                    logger.warning(f"⚠️  QA Rejected - Feedback: {feedback[:100]}...")
                    
                    if iteration < max_iterations:
                        logger.info(f"  Refining content based on feedback (iteration {iteration})...")
                        
                        # Refine based on feedback
                        content = await asyncio.to_thread(
                            creative_agent.run,
                            content,
                            is_refinement=True
                        )
                        logger.info(f"  Refinement complete, re-submitting to QA...")
            
            # After max iterations
            logger.warning(f"⚠️  QA loop completed after {max_iterations} iterations")
            return content, feedback, quality_score

        except Exception as e:
            logger.error(f"❌ QA stage failed: {e}")
            raise

    async def _run_image_selection(self, topic: str, content: Any) -> Optional[str]:
        """Run image agent (Stage 4)"""
        try:
            logger.info(f"🖼️  Image: Selecting featured image for '{topic}'")
            
            from src.agents.content_agent.agents.image_agent import ImageAgent
            from src.agents.content_agent.services.llm_client import LLMClient
            from src.agents.content_agent.services.pexels_client import PexelsClient
            from src.agents.content_agent.services.strapi_client import StrapiClient
            
            llm_client = LLMClient()
            pexels_client = PexelsClient()
            strapi_client = StrapiClient()
            
            image_agent = ImageAgent(
                llm_client=llm_client,
                pexels_client=pexels_client,
                strapi_client=strapi_client
            )
            
            # Run image agent
            result_post = await asyncio.to_thread(
                image_agent.run,
                content
            )
            
            # Extract featured image URL
            image_url = None
            if hasattr(result_post, 'images') and result_post.images:
                image_url = result_post.images[0].public_url
            
            if image_url:
                logger.info(f"✅ Featured image selected: {image_url[:60]}...")
            else:
                logger.warning(f"⚠️  No image selected, continuing without featured image")
            
            return image_url

        except Exception as e:
            logger.error(f"❌ Image stage failed: {e}")
            return None  # Continue without image

    async def _run_formatting(
        self, topic: str, content: Any, featured_image_url: Optional[str]
    ) -> tuple:
        """Run publishing agent for formatting (Stage 5)"""
        try:
            logger.info(f"📝 Formatting: Converting to Strapi format")
            
            from src.agents.content_agent.agents.publishing_agent import PublishingAgent
            from src.agents.content_agent.services.strapi_client import StrapiClient
            
            strapi_client = StrapiClient()
            publishing_agent = PublishingAgent(strapi_client=strapi_client)
            
            # Run publishing agent (for formatting, not actually publishing yet)
            result_post = await asyncio.to_thread(
                publishing_agent.run,
                content
            )
            
            # Extract formatted content and excerpt
            formatted_content = getattr(result_post, 'raw_content', str(content))
            excerpt = getattr(result_post, 'meta_description', f"Article about {topic}")
            
            logger.info(f"✅ Formatting complete")
            return formatted_content, excerpt

        except Exception as e:
            logger.error(f"❌ Formatting stage failed: {e}")
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
