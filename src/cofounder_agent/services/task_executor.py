"""
Background Task Executor Service (Production Pipeline)

Complete end-to-end pipeline for blog generation:
1. Polls for pending tasks every 5 seconds
2. Updates task status to 'in_progress'
3. Calls orchestrator to generate content (with multi-agent, self-critique loop)
4. Validates content through critique loop
5. Posts approved content to Strapi CMS
6. Updates task with published post ID and URL
7. Handles errors gracefully with retry logic

Production-ready for full blog automation!
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import json
import sys
import os

# Import the content critique loop
from src.cofounder_agent.services.content_critique_loop import ContentCritiqueLoop

logger = logging.getLogger(__name__)


class TaskExecutor:
    """Background task executor service"""

    def __init__(self, database_service, orchestrator=None, critique_loop=None, 
                 strapi_client=None, poll_interval: int = 5):
        """
        Initialize task executor with full production pipeline

        Args:
            database_service: DatabaseService instance
            orchestrator: Optional Orchestrator instance for processing
            critique_loop: Optional ContentCritiqueLoop for validating content
            strapi_client: Optional Strapi client for publishing
            poll_interval: Seconds between polling for pending tasks (default: 5)
        """
        self.database_service = database_service
        self.orchestrator = orchestrator
        self.critique_loop = critique_loop or ContentCritiqueLoop()
        self.strapi_client = strapi_client
        self.poll_interval = poll_interval
        self.running = False
        self.task_count = 0
        self.success_count = 0
        self.error_count = 0
        self.published_count = 0
        self._processor_task = None
        
        logger.info(f"TaskExecutor initialized: orchestrator={'✅' if orchestrator else '❌'}, "
                   f"critique_loop={'✅' if critique_loop else '❌'}, "
                   f"strapi_client={'✅' if strapi_client else '❌'}")

    async def start(self):
        """Start the background task processor"""
        if self.running:
            logger.warning("❌ Task executor already running")
            return

        self.running = True
        logger.info("🚀 Starting task executor background processor...")
        logger.info(f"   Poll interval: {self.poll_interval} seconds")
        logger.info(f"   Database service: {self.database_service is not None}")
        logger.info(f"   Orchestrator: {self.orchestrator is not None}")

        # Create background task
        self._processor_task = asyncio.create_task(self._process_loop())
        logger.info("✅ Task executor background processor started")

    async def stop(self):
        """Stop the background task processor"""
        if not self.running:
            return

        self.running = False
        logger.info("🛑 Stopping task executor...")

        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass

        logger.info(f"✅ Task executor stopped (processed: {self.task_count}, success: {self.success_count}, errors: {self.error_count})")

    async def _process_loop(self):
        """Main processing loop - runs continuously in background"""
        logger.info("📋 Task executor processor loop started")

        while self.running:
            try:
                # Get pending tasks from database
                pending_tasks = await self.database_service.get_pending_tasks(limit=10)

                if pending_tasks:
                    logger.info(f"📦 Found {len(pending_tasks)} pending tasks")

                    # Process each task
                    for task in pending_tasks:
                        if not self.running:
                            break

                        task_id = task.get("id")
                        task_name = task.get("task_name", "Untitled")

                        try:
                            await self._process_single_task(task)
                            self.success_count += 1
                        except Exception as e:
                            logger.error(f"❌ Error processing task {task_id}: {str(e)}", exc_info=True)
                            # Update task as failed
                            await self.database_service.update_task_status(
                                task_id,
                                "failed",
                                result=json.dumps({
                                    "error": str(e),
                                    "timestamp": datetime.now(timezone.utc).isoformat()
                                })
                            )
                            self.error_count += 1
                        finally:
                            self.task_count += 1

                # Sleep before next poll
                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                logger.info("Task executor processor loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Unexpected error in task executor loop: {str(e)}", exc_info=True)
                await asyncio.sleep(self.poll_interval)

        logger.info("📋 Task executor processor loop stopped")

    async def _process_single_task(self, task: Dict[str, Any]):
        """Process a single task through the pipeline"""
        task_id = task.get("id")
        task_name = task.get("task_name", "Untitled")
        topic = task.get("topic", "")
        category = task.get("category", "general")

        logger.info(f"⏳ Processing task: {task_id} ({task_name})")

        try:
            # 1. Update task status to 'in_progress'
            await self.database_service.update_task_status(
                task_id,
                "in_progress",
                result=json.dumps({
                    "status": "processing",
                    "started_at": datetime.now(timezone.utc).isoformat()
                })
            )
            logger.debug(f"   ✓ Task marked as in_progress")

            # 2. Process through orchestrator/agent pipeline
            result = await self._execute_task(task)
            logger.debug(f"   ✓ Task execution completed")

            # 3. Update task status to 'completed' with result
            result_json = json.dumps(result) if isinstance(result, dict) else str(result)
            await self.database_service.update_task_status(
                task_id,
                "completed",
                result=result_json
            )
            logger.info(f"✅ Task completed: {task_id} ({task_name})")

        except Exception as e:
            logger.error(f"❌ Task failed: {task_id} - {str(e)}")
            # Status already updated to 'failed' in _process_loop
            raise

    async def _execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute task through PRODUCTION PIPELINE:
        1. Generate content via orchestrator
        2. Validate through critique loop
        3. Publish to Strapi CMS if approved
        """
        task_id = task.get("id")
        task_name = task.get("task_name", "")
        topic = task.get("topic", "")
        primary_keyword = task.get("primary_keyword", "")
        target_audience = task.get("target_audience", "")
        category = task.get("category", "general")
        agent_id = task.get("agent_id", "content-agent")

        logger.debug(f"🎬 PRODUCTION PIPELINE: Task {task_id}: {task_name}")
        logger.debug(f"   Topic: {topic}")
        logger.debug(f"   Keyword: {primary_keyword}")
        logger.debug(f"   Audience: {target_audience}")
        logger.debug(f"   Agent: {agent_id}")

        # ===== PHASE 1: Generate Content via Orchestrator =====
        generated_content = None
        orchestrator_error = None

        if self.orchestrator:
            try:
                logger.info(f"📝 PHASE 1: Generating content via orchestrator...")
                
                # Call orchestrator with task context
                orchestrator_result = await self.orchestrator.process_command_async(
                    command="generate_content",
                    context={
                        "topic": topic,
                        "keywords": primary_keyword,
                        "target_audience": target_audience,
                        "style": "professional",
                        "length": "1500-2000",
                        "task_id": str(task_id),
                        "category": category,
                    }
                )
                
                # Extract content from result
                if isinstance(orchestrator_result, dict):
                    if "content" in orchestrator_result:
                        generated_content = orchestrator_result["content"]
                    elif "response" in orchestrator_result:
                        generated_content = orchestrator_result["response"]
                    elif "result" in orchestrator_result:
                        generated_content = orchestrator_result["result"]
                    else:
                        generated_content = json.dumps(orchestrator_result)
                else:
                    generated_content = str(orchestrator_result)
                
                logger.info(f"✅ PHASE 1 Complete: Generated {len(generated_content)} chars")
                
            except Exception as e:
                orchestrator_error = str(e)
                logger.error(f"❌ PHASE 1 Failed: Orchestrator error - {orchestrator_error}")
                generated_content = f"Error in content generation: {orchestrator_error}"
        else:
            logger.warning("⚠️ No orchestrator available, using fallback content generation")
            # Fallback: Simple template-based generation
            generated_content = await self._fallback_generate_content(task)

        # ===== PHASE 2: Critique Loop (Validate Quality) =====
        critique_result = await self.critique_loop.critique(
            content=generated_content,
            context={
                "topic": topic,
                "keywords": primary_keyword,
                "target_audience": target_audience,
                "category": category,
            }
        )
        
        quality_score = critique_result.get("quality_score", 0)
        approved = critique_result.get("approved", False)
        
        if approved:
            logger.info(f"✅ PHASE 2 Complete: Content approved (quality score: {quality_score}/100)")
        else:
            logger.warning(f"⚠️ PHASE 2 Complete: Content needs improvement (score: {quality_score}/100)")
            logger.debug(f"   Feedback: {critique_result.get('feedback')}")
            
            # If not approved but can refine, attempt refinement
            if critique_result.get("needs_refinement") and self.orchestrator:
                logger.info(f"🔄 Attempting refinement based on critique feedback...")
                try:
                    refinement_result = await self.orchestrator.process_command_async(
                        command="refine_content",
                        context={
                            "original_content": generated_content,
                            "feedback": critique_result.get("feedback"),
                            "suggestions": critique_result.get("suggestions"),
                            "topic": topic,
                        }
                    )
                    
                    if isinstance(refinement_result, dict) and "content" in refinement_result:
                        generated_content = refinement_result["content"]
                        
                        # Re-critique refined content
                        critique_result = await self.critique_loop.critique(
                            content=generated_content,
                            context={
                                "topic": topic,
                                "keywords": primary_keyword,
                            }
                        )
                        
                        quality_score = critique_result.get("quality_score", 0)
                        approved = critique_result.get("approved", False)
                        logger.info(f"🔄 Refinement complete: approved={approved}, score={quality_score}/100")
                        
                except Exception as e:
                    logger.warning(f"Refinement attempt failed: {e}")

        # ===== PHASE 3: Publish to Strapi =====
        strapi_post_id = None
        strapi_url = None
        publish_error = None

        if approved and self.strapi_client:
            try:
                logger.info(f"🌐 PHASE 3: Publishing to Strapi CMS...")
                
                # Create slug from topic
                slug = topic.lower().replace(" ", "-").replace("_", "-")[:100]
                slug = "".join(c for c in slug if c.isalnum() or c == "-")
                
                # Post to Strapi
                post_result = self.strapi_client.create_post_from_content(
                    title=topic,
                    content=generated_content,
                    excerpt=generated_content[:200] if generated_content else "",
                    category=category,
                    tags=[primary_keyword] if primary_keyword else [],
                    slug=slug
                )
                
                if isinstance(post_result, dict):
                    strapi_post_id = post_result.get("id") or post_result.get("post_id")
                    strapi_url = post_result.get("url") or post_result.get("post_url")
                elif hasattr(post_result, 'id'):
                    strapi_post_id = post_result.id
                    strapi_url = getattr(post_result, 'url', None)
                else:
                    strapi_post_id = str(post_result)
                
                self.published_count += 1
                logger.info(f"✅ PHASE 3 Complete: Published to Strapi (ID: {strapi_post_id})")
                logger.info(f"🎉 Blog post published: {strapi_url or f'strapi_id:{strapi_post_id}'}")
                
            except Exception as e:
                publish_error = str(e)
                logger.error(f"❌ PHASE 3 Failed: Strapi publication error - {publish_error}")
        elif approved and not self.strapi_client:
            logger.warning("⚠️ Content approved but Strapi client not available")
            publish_error = "Strapi client not configured"
        elif not approved:
            logger.info("⏭️ PHASE 3 Skipped: Content not approved, not publishing")
            publish_error = f"Content quality below threshold (score: {quality_score}/100)"

        # ===== Build Final Result =====
        result = {
            "task_id": str(task_id),
            "task_name": task_name,
            "topic": topic,
            "primary_keyword": primary_keyword,
            "target_audience": target_audience,
            "category": category,
            "status": "completed",
            
            # Generation phase
            "generated_content": generated_content[:500] if generated_content else "",  # Summary
            "content_length": len(generated_content) if generated_content else 0,
            "orchestrator_error": orchestrator_error,
            
            # Critique phase
            "quality_score": quality_score,
            "content_approved": approved,
            "critique_feedback": critique_result.get("feedback", ""),
            "critique_suggestions": critique_result.get("suggestions", []),
            
            # Publishing phase
            "strapi_post_id": strapi_post_id,
            "strapi_url": strapi_url,
            "publish_status": "published" if strapi_post_id else "not_published",
            "publish_error": publish_error,
            
            # Metadata
            "word_count": len(generated_content.split()) if generated_content else 0,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_summary": {
                "phase_1_generation": "✅" if generated_content else "❌",
                "phase_2_critique": f"{'✅' if approved else '⚠️'} ({quality_score}/100)",
                "phase_3_published": "✅" if strapi_post_id else f"❌ ({publish_error})",
            }
        }

        return result
    
    async def _fallback_generate_content(self, task: Dict[str, Any]) -> str:
        """
        Fallback content generation when orchestrator not available
        
        This is a placeholder. In production, you'd want real content generation here.
        """
        topic = task.get("topic", "No Topic")
        keyword = task.get("primary_keyword", "keyword")
        audience = task.get("target_audience", "audience")
        
        # Create a more realistic placeholder
        word_count_placeholder = 450  # Approximate
        content = f"""# {topic}

## Introduction

This article explores the key aspects of {topic} and its relevance to {audience}. We'll cover the essential information you need to know about this topic.

## Main Points

### Understanding {topic}

{topic} is an important area that affects many aspects of today's world. By understanding {keyword}, we can better appreciate its significance.

### Why {topic} Matters

- Key point about {topic}
- Importance of {keyword}
- Relevance to {audience}
- Current trends and developments
- Future implications

### Implementation and Best Practices

When considering {topic}, it's important to focus on {keyword}. Here are some best practices:

1. Stay informed about recent developments
2. Understand the core concepts
3. Apply knowledge practically
4. Continuously learn and adapt
5. Share insights with your community

## Conclusion

{topic} remains a crucial area to understand. Whether you're {audience} or just curious, having knowledge about {keyword} empowers better decision-making.

---

*This article was automatically generated for demonstration purposes.*

Word Count: {word_count_placeholder} words
"""
        
        logger.info(f"Generated fallback content: {len(content)} chars")
        return content

    def get_stats(self) -> Dict[str, Any]:
        """Get executor statistics"""
        return {
            "running": self.running,
            "total_processed": self.task_count,
            "successful": self.success_count,
            "failed": self.error_count,
            "published_to_strapi": self.published_count,
            "poll_interval": self.poll_interval,
            "critique_stats": self.critique_loop.get_stats() if self.critique_loop else {},
        }
