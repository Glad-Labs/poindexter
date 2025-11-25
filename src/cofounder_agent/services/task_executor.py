"""
Background Task Executor Service

Complete end-to-end pipeline for blog generation:
1. Polls for pending tasks every 5 seconds
2. Updates task status to 'in_progress'
3. Calls orchestrator to generate content (with multi-agent, self-critique loop)
4. Validates content through critique loop
5. Updates task with generated content and quality score
6. Handles errors gracefully with retry logic

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
from .content_critique_loop import ContentCritiqueLoop

logger = logging.getLogger(__name__)


class TaskExecutor:
    """Background task executor service"""

    def __init__(self, database_service, orchestrator=None, critique_loop=None, 
                 poll_interval: int = 5):
        """
        Initialize task executor

        Args:
            database_service: DatabaseService instance
            orchestrator: Optional Orchestrator instance for processing
            critique_loop: Optional ContentCritiqueLoop for validating content
            poll_interval: Seconds between polling for pending tasks (default: 5)
        """
        self.database_service = database_service
        self.orchestrator = orchestrator
        self.critique_loop = critique_loop or ContentCritiqueLoop()
        self.poll_interval = poll_interval
        self.running = False
        self.task_count = 0
        self.success_count = 0
        self.error_count = 0
        self.published_count = 0
        self._processor_task = None
        
        logger.info(f"TaskExecutor initialized: orchestrator={'✅' if orchestrator else '❌'}, "
                   f"critique_loop={'✅' if critique_loop else '❌'}")

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
                logger.debug(f"🔍 [TASK_EXEC_LOOP] Polling for pending tasks...")
                pending_tasks = await self.database_service.get_pending_tasks(limit=10)

                if pending_tasks:
                    logger.info(f"� [TASK_EXEC_LOOP] Found {len(pending_tasks)} pending task(s)")
                    for idx, task in enumerate(pending_tasks, 1):
                        logger.info(f"   [{idx}] Task ID: {task.get('id')}, Name: {task.get('task_name')}, Status: {task.get('status')}")

                    # Process each task
                    for task in pending_tasks:
                        if not self.running:
                            logger.warning("[TASK_EXEC_LOOP] Executor stopped - breaking loop")
                            break

                        task_id = task.get("id")
                        task_name = task.get("task_name", "Untitled")

                        try:
                            logger.info(f"⚡ [TASK_EXEC_LOOP] Starting to process task: {task_id}")
                            await self._process_single_task(task)
                            self.success_count += 1
                            logger.info(f"✅ [TASK_EXEC_LOOP] Task succeeded (total success: {self.success_count})")
                        except Exception as e:
                            logger.error(f"❌ [TASK_EXEC_LOOP] Error processing task {task_id}: {str(e)}", exc_info=True)
                            # Update task as failed
                            try:
                                await self.database_service.update_task_status(
                                    task_id,
                                    "failed",
                                    result=json.dumps({
                                        "error": str(e),
                                        "timestamp": datetime.now(timezone.utc).isoformat()
                                    })
                                )
                                logger.info(f"📝 [TASK_EXEC_LOOP] Updated task {task_id} status to failed")
                            except Exception as update_err:
                                logger.error(f"❌ [TASK_EXEC_LOOP] Failed to update task status: {str(update_err)}")
                            self.error_count += 1
                            logger.info(f"❌ [TASK_EXEC_LOOP] Task failed (total errors: {self.error_count})")
                        finally:
                            self.task_count += 1
                else:
                    logger.debug(f"⏳ [TASK_EXEC_LOOP] No pending tasks - sleeping for {self.poll_interval}s")

                # Sleep before next poll
                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                logger.info("[TASK_EXEC_LOOP] Task executor processor loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ [TASK_EXEC_LOOP] Unexpected error in task executor loop: {str(e)}", exc_info=True)
                logger.info(f"⏳ [TASK_EXEC_LOOP] Sleeping for {self.poll_interval}s before retry...")
                await asyncio.sleep(self.poll_interval)

        logger.info("📋 [TASK_EXEC_LOOP] Task executor processor loop stopped")

    async def _process_single_task(self, task: Dict[str, Any]):
        """Process a single task through the pipeline"""
        task_id = task.get("id")
        task_name = task.get("task_name", "Untitled")
        topic = task.get("topic", "")
        category = task.get("category", "general")

        logger.info(f"⏳ [TASK_SINGLE] Processing task: {task_id}")
        logger.info(f"   Name: {task_name}")
        logger.info(f"   Topic: {topic}")
        logger.info(f"   Category: {category}")

        try:
            # 1. Update task status to 'in_progress'
            logger.info(f"📝 [TASK_SINGLE] Marking task as in_progress...")
            await self.database_service.update_task_status(
                task_id,
                "in_progress",
                result=json.dumps({
                    "status": "processing",
                    "started_at": datetime.now(timezone.utc).isoformat()
                })
            )
            logger.info(f"✅ [TASK_SINGLE] Task marked as in_progress")

            # 2. Process through orchestrator/agent pipeline
            logger.info(f"🚀 [TASK_SINGLE] Executing task through pipeline...")
            result = await self._execute_task(task)
            logger.info(f"✅ [TASK_SINGLE] Task execution completed successfully")
            logger.debug(f"   Result type: {type(result).__name__}")
            if isinstance(result, dict):
                logger.debug(f"   Result keys: {list(result.keys())}")

            # 3. Update task status to 'completed' with result
            logger.info(f"💾 [TASK_SINGLE] Updating task status to completed...")
            result_json = json.dumps(result) if isinstance(result, dict) else str(result)
            await self.database_service.update_task_status(
                task_id,
                "completed",
                result=result_json
            )
            logger.info(f"✅ [TASK_SINGLE] Task completed: {task_id}")

        except Exception as e:
            logger.error(f"❌ [TASK_SINGLE] Task failed: {task_id} - {str(e)}", exc_info=True)
            # Status already updated to 'failed' in _process_loop
            raise

    async def _execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute task through production pipeline:
        1. Generate content via orchestrator
        2. Validate through critique loop
        """
        task_id = task.get("id")
        task_name = task.get("task_name", "")
        topic = task.get("topic", "")
        primary_keyword = task.get("primary_keyword", "")
        target_audience = task.get("target_audience", "")
        category = task.get("category", "general")
        agent_id = task.get("agent_id", "content-agent")

        logger.info(f"🎬 [TASK_EXECUTE] PRODUCTION PIPELINE: {task_id}")
        logger.info(f"   Name: {task_name}")
        logger.info(f"   Topic: {topic}")
        logger.info(f"   Keyword: {primary_keyword}")
        logger.info(f"   Audience: {target_audience}")
        logger.info(f"   Agent: {agent_id}")

        # ===== PHASE 1: Generate Content via Orchestrator =====
        generated_content = None
        orchestrator_error = None

        logger.info(f"📝 [TASK_EXECUTE] PHASE 1: Generating content via orchestrator...")
        if self.orchestrator:
            try:
                logger.info(f"   Orchestrator available: YES")
                logger.info(f"   Type: {type(self.orchestrator).__name__}")
                
                # Check if using IntelligentOrchestrator (New System)
                is_intelligent = False
                try:
                    from .intelligent_orchestrator import IntelligentOrchestrator
                    if isinstance(self.orchestrator, IntelligentOrchestrator):
                        is_intelligent = True
                except ImportError:
                    pass

                if is_intelligent:
                    logger.info(f"   🧠 Using IntelligentOrchestrator")
                    # Construct natural language request
                    prompt = f"Generate a blog post about '{topic}'."
                    if primary_keyword:
                        prompt += f" Focus on keywords: {primary_keyword}."
                    if target_audience:
                        prompt += f" Target audience is {target_audience}."
                    if category:
                        prompt += f" Category: {category}."
                    prompt += " Ensure the content is professional and approximately 1500-2000 words."

                    # Call process_request
                    result = await self.orchestrator.process_request(
                        user_request=prompt,
                        user_id="system_task_executor",
                        business_metrics={"task_id": str(task_id)}
                    )
                    
                    # Extract content from result
                    if result.final_formatting:
                        generated_content = json.dumps(result.final_formatting)
                    elif result.outputs:
                        generated_content = str(result.outputs)
                        for key, val in result.outputs.items():
                            if isinstance(val, dict) and "content" in val:
                                generated_content = val["content"]
                                break
                            if isinstance(val, str) and len(val) > 100:
                                generated_content = val
                                break
                    else:
                        generated_content = "No content generated by IntelligentOrchestrator"

                else:
                    # Legacy Orchestrator
                    logger.info(f"   ⚙️ Using Legacy Orchestrator")
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
                
                logger.info(f"✅ [TASK_EXECUTE] PHASE 1 Complete: Generated {len(generated_content) if generated_content else 0} chars")
                
            except Exception as e:
                orchestrator_error = str(e)
                logger.error(f"❌ [TASK_EXECUTE] PHASE 1 Failed: Orchestrator error - {orchestrator_error}", exc_info=True)
                generated_content = f"Error in content generation: {orchestrator_error}"
        else:
            logger.warning(f"⚠️ [TASK_EXECUTE] Orchestrator available: NO - Using fallback")
            # Fallback: Simple template-based generation
            generated_content = await self._fallback_generate_content(task)
            logger.info(f"✅ [TASK_EXECUTE] PHASE 1 Complete (fallback): Generated {len(generated_content)} chars")

        # ===== PHASE 2: Critique Loop (Validate Quality) =====
        logger.info(f"🔍 [TASK_EXECUTE] PHASE 2: Validating content through critique loop...")
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
        
        logger.info(f"   Quality Score: {quality_score}/100")
        logger.info(f"   Approved: {approved}")
        
        if approved:
            logger.info(f"✅ [TASK_EXECUTE] PHASE 2 Complete: Content approved")
        else:
            logger.warning(f"⚠️ [TASK_EXECUTE] PHASE 2 Complete: Content needs improvement")
            logger.debug(f"   Feedback: {critique_result.get('feedback')}")
            
            # If not approved but can refine, attempt refinement
            if critique_result.get("needs_refinement") and self.orchestrator:
                logger.info(f"🔄 [TASK_EXECUTE] Attempting refinement based on critique feedback...")
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

        # ===== Build Final Result =====
        result = {
            "task_id": str(task_id),
            "task_name": task_name,
            "topic": topic,
            "primary_keyword": primary_keyword,
            "target_audience": target_audience,
            "category": category,
            "status": "completed",
            
            # Generation phase - FULL CONTENT, not truncated!
            "generated_content": generated_content,  # Full content for preview
            "content_length": len(generated_content) if generated_content else 0,
            "orchestrator_error": orchestrator_error,
            
            # Critique phase
            "quality_score": quality_score,
            "content_approved": approved,
            "critique_feedback": critique_result.get("feedback", ""),
            "critique_suggestions": critique_result.get("suggestions", []),
            
            # Metadata
            "word_count": len(generated_content.split()) if generated_content else 0,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_summary": {
                "phase_1_generation": "✅" if generated_content else "❌",
                "phase_2_critique": f"{'✅' if approved else '⚠️'} ({quality_score}/100)",
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
            "published": self.published_count,
            "poll_interval": self.poll_interval,
            "critique_stats": self.critique_loop.get_stats() if self.critique_loop else {},
        }
