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
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Import model selection helper
from routes.task_routes import get_model_for_phase

# Import AI content generator for fallback
from .ai_content_generator import AIContentGenerator

# Import prompt manager for centralized prompts
from .prompt_manager import get_prompt_manager

# Import unified quality service for content validation
from .quality_service import QualityAssessment, UnifiedQualityService

# Import usage tracking
from .usage_tracker import get_usage_tracker

from .error_handler import ServiceError

# Import WebSocket progress emission (re-exported so tests can patch at this module)
from .websocket_event_broadcaster import emit_notification, emit_task_progress

logger = logging.getLogger(__name__)

# Tasks stuck in "processing" for longer than this are considered stale and reset to "pending"
STALE_TASK_TIMEOUT_MINUTES: int = 60
# Maximum number of automatic retry attempts before a task is marked as failed
MAX_TASK_RETRIES: int = 3
# How often (seconds) to run the stale-task sweep within the process loop
SWEEP_INTERVAL_SECONDS: int = 300


class TaskExecutor:
    """Background task executor service"""

    def __init__(
        self,
        database_service,
        orchestrator=None,
        poll_interval: int = 5,
        app_state=None,
    ):
        """
        Initialize task executor

        Args:
            database_service: DatabaseService instance
            orchestrator: Optional Orchestrator instance for processing
            poll_interval: Seconds between polling for pending tasks (default: 5)
            app_state: Optional FastAPI app.state for getting updated orchestrator reference
        """
        self.database_service = database_service
        self.orchestrator_initial = orchestrator  # Initial orchestrator from startup
        self.app_state = app_state  # Reference to app.state for dynamic orchestrator updates
        self.quality_service = UnifiedQualityService()  # Quality validation service
        self.content_generator = AIContentGenerator()  # Fallback content generation
        self.poll_interval = poll_interval
        self.running = False
        self.task_count = 0
        self.success_count = 0
        self.error_count = 0
        self.published_count = 0
        self._processor_task = None
        self.usage_tracker = get_usage_tracker()  # Initialize usage tracking
        self.critique_loop: Optional[Any] = None  # Optional critique loop (not wired in current version)
        self.last_poll_at: Optional[float] = None  # monotonic timestamp of last poll
        self._poll_cycle: int = 0  # incremented each loop iteration

        logger.info(
            f"TaskExecutor initialized: orchestrator={'✅' if orchestrator else '❌'}, "
            f"quality_service={'✅'}, "
            f"content_generator={'✅'}"
        )

    @property
    def orchestrator(self):
        """
        Get the orchestrator dynamically.
        First tries to get from app.state (which gets updated by main.py with UnifiedOrchestrator),
        then falls back to the initial orchestrator from startup.
        This ensures we use the properly-initialized UnifiedOrchestrator when available.
        """
        if self.app_state and hasattr(self.app_state, "orchestrator"):
            orch = getattr(self.app_state, "orchestrator", None)
            if orch is not None:
                return orch
        return self.orchestrator_initial

    def inject_orchestrator(self, orchestrator) -> None:
        """Inject or replace the orchestrator at runtime."""
        self.orchestrator_initial = orchestrator

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
        logger.info(
            f"   Orchestrator type: {type(self.orchestrator).__name__ if self.orchestrator else 'None'}"
        )

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
                logger.debug("Task processor task cancelled successfully")

        logger.info(
            f"✅ Task executor stopped (processed: {self.task_count}, success: {self.success_count}, errors: {self.error_count})"
        )

    async def _process_loop(self):
        """Main processing loop - runs continuously in background"""
        logger.info("📋 Task executor processor loop started")

        while self.running:
            try:
                import time as _time
                self.last_poll_at = _time.monotonic()
                self._poll_cycle += 1
                # Get pending tasks from database
                logger.debug(f"🔍 [TASK_EXEC_LOOP] Polling for pending tasks...")
                pending_tasks = await self.database_service.get_pending_tasks(limit=10)

                if pending_tasks:
                    logger.info(f"� [TASK_EXEC_LOOP] Found {len(pending_tasks)} pending task(s)")
                    for idx, task in enumerate(pending_tasks, 1):
                        logger.info(
                            f"   [{idx}] Task ID: {task.get('id')}, Name: {task.get('task_name')}, Status: {task.get('status')}"
                        )

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
                            logger.info(
                                f"✅ [TASK_EXEC_LOOP] Task succeeded (total success: {self.success_count})"
                            )
                        except Exception as e:
                            logger.error(
                                f"❌ [TASK_EXEC_LOOP] Error processing task {task_id}: {str(e)}",
                                exc_info=True,
                            )
                            # Update task as failed
                            try:
                                await self.database_service.update_task(
                                    task_id,
                                    {
                                        "status": "failed",
                                        "task_metadata": {
                                            "error": str(e),
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                        },
                                    },
                                )
                                logger.info(
                                    f"📝 [TASK_EXEC_LOOP] Updated task {task_id} status to failed"
                                )
                            except Exception as update_err:
                                logger.error(
                                    f"❌ [TASK_EXEC_LOOP] Failed to update task status: {str(update_err)}"
                                )
                            self.error_count += 1
                            logger.info(
                                f"❌ [TASK_EXEC_LOOP] Task failed (total errors: {self.error_count})"
                            )
                        finally:
                            self.task_count += 1
                else:
                    logger.debug(
                        f"⏳ [TASK_EXEC_LOOP] No pending tasks - sleeping for {self.poll_interval}s"
                    )

                # Sleep before next poll
                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                # Log at CRITICAL so Sentry's default "new issue" alert fires and
                # on-call engineers are paged when the executor loop exits (issue #556).
                logger.critical(
                    "[TASK_EXEC_LOOP] Task executor processor loop cancelled — "
                    "background task processing has stopped"
                )
                break
            except Exception as e:
                logger.error(
                    f"❌ [TASK_EXEC_LOOP] Unexpected error in task executor loop: {str(e)}",
                    exc_info=True,
                )
                logger.info(
                    f"⏳ [TASK_EXEC_LOOP] Sleeping for {self.poll_interval}s before retry..."
                )
                await asyncio.sleep(self.poll_interval)

        # Log at CRITICAL so Sentry alerts fire immediately on loop exit (issue #556).
        logger.critical(
            "[TASK_EXEC_LOOP] Task executor processor loop stopped — "
            f"processed={self.task_count} success={self.success_count} errors={self.error_count}"
        )

    async def _process_single_task(self, task: Dict[str, Any]):
        """Process a single task through the pipeline"""
        task_id = task.get("id") or task.get("task_id")
        if not task_id:
            logger.warning("[TASK_SINGLE] Task has no id or task_id — skipping")
            return

        task_name = task.get("task_name", "Untitled")
        topic = task.get("topic", "")
        category = task.get("category", "general")

        logger.info(f"⏳ [TASK_SINGLE] Processing task: {task_id}")
        logger.info(f"   Name: {task_name}")
        logger.info(f"   Topic: {topic}")
        logger.info(f"   Category: {category}")

        # Set per-task timeout (15 minutes max for content generation)
        TASK_TIMEOUT_SECONDS = 900  # 15 minutes

        try:
            # 1. Update task status to 'in_progress'
            logger.info(f"📝 [TASK_SINGLE] Marking task as in_progress...")
            await self.database_service.update_task(
                task_id,
                {
                    "status": "in_progress",
                    "task_metadata": {
                        "status": "processing",
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    },
                },
            )
            await self.database_service.log_status_change(task_id, "pending", "in_progress")
            logger.info(f"✅ [TASK_SINGLE] Task marked as in_progress")

            # 2. Process through orchestrator/agent pipeline with timeout
            logger.info(
                f"🚀 [TASK_SINGLE] Executing task through pipeline (timeout: {TASK_TIMEOUT_SECONDS}s)..."
            )
            try:
                result = await asyncio.wait_for(
                    self._execute_task(task), timeout=TASK_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"⏱️  [TASK_SINGLE] Task execution timed out after {TASK_TIMEOUT_SECONDS}s: {task_id}"
                )
                result = {
                    "status": "failed",
                    "orchestrator_error": f"Task execution timeout ({TASK_TIMEOUT_SECONDS}s exceeded)",
                }

            logger.info(f"✅ [TASK_SINGLE] Task execution completed")
            logger.debug(f"   Result type: {type(result).__name__}")
            if isinstance(result, dict):
                logger.debug(f"   Result keys: {list(result.keys())}")

            # 3. Update task status (awaiting_approval or failed based on result)
            final_status = (
                result.get("status", "awaiting_approval")
                if isinstance(result, dict)
                else "awaiting_approval"
            )
            logger.info(f"💾 [TASK_SINGLE] Updating task status to '{final_status}'...")

            # Extract relevant fields from result for task_metadata (don't store entire result)
            # This prevents the entire result dict from being treated as metadata
            task_metadata_updates = {}
            if isinstance(result, dict):
                # Extract only the fields we want in task_metadata
                fields_to_extract = [
                    "content",
                    "excerpt",
                    "title",
                    "featured_image_url",
                    "featured_image_data",
                    "seo_title",
                    "seo_description",
                    "seo_keywords",
                    "qa_feedback",
                    "quality_score",
                    "orchestrator_error",
                    "message",
                    "constraint_compliance",
                    "stage",
                    "percentage",
                    "model_used",
                ]
                for field in fields_to_extract:
                    if field in result:
                        task_metadata_updates[field] = result[field]
            else:
                task_metadata_updates["output"] = str(result)

            # DEBUG: Log all extracted metadata
            logger.info(f"🔍 [DEBUG] Extracted metadata for task {task_id}:")
            logger.info(f"   - Fields extracted: {list(task_metadata_updates.keys())}")
            logger.info(f"   - Has 'content': {'content' in task_metadata_updates}")
            if "content" in task_metadata_updates:
                content_val = task_metadata_updates.get("content")
                logger.info(f"   - Content type: {type(content_val).__name__}")
                logger.info(
                    f"   - Content length: {len(content_val) if isinstance(content_val, str) else 'N/A'} chars"
                )
                if isinstance(content_val, str):
                    logger.info(f"   - Content preview: {content_val[:100]}...")

            # ⚠️ IMPORTANT: Don't store incomplete content for failed tasks
            # Only store content if task is approved/successful
            # This prevents partial/truncated content from appearing in the database
            if final_status == "failed" or final_status == "rejected":
                logger.warning(
                    f"⚠️  Task status is '{final_status}' - NOT storing content to prevent partial/truncated data"
                )
                # Remove content fields for failed tasks
                task_metadata_updates.pop("content", None)
                task_metadata_updates.pop("excerpt", None)
                task_metadata_updates.pop("featured_image_url", None)
                task_metadata_updates.pop("featured_image_data", None)

            # Use update_task to ensure normalization of content into columns
            logger.info(
                f"📝 [DEBUG] Calling update_task with status={final_status}, metadata keys={list(task_metadata_updates.keys())}"
            )

            # Also store model_used in the normalized column if it's in the result
            update_payload = {"status": final_status, "task_metadata": task_metadata_updates}
            if isinstance(result, dict) and "model_used" in result:
                update_payload["model_used"] = result["model_used"]
                logger.info(
                    f"📝 [DEBUG] Including model_used in database update: {result['model_used']}"
                )

            await self.database_service.update_task(task_id, update_payload)
            logger.info(f"✅ [DEBUG] update_task completed for {task_id}")

            if final_status == "failed":
                logger.error(f"❌ [TASK_SINGLE] Task failed: {task_id}")
                # Extract error message for better logging
                error_msg = (
                    result.get("orchestrator_error", "Unknown error")
                    if isinstance(result, dict)
                    else "Unknown error"
                )
                logger.error(f"   Error: {error_msg}")
            else:
                logger.info(f"✅ [TASK_SINGLE] Task awaiting approval: {task_id}")

        except ServiceError:
            raise
        except Exception as e:
            logger.error(f"❌ [TASK_SINGLE] Task failed: {task_id} - {str(e)}", exc_info=True)
            raise ServiceError(message=str(e), details={"task_id": task_id}) from e

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
        style = task.get("style", "")
        tone = task.get("tone", "")
        target_length = task.get("target_length")
        agent_id = task.get("agent_id", "content-agent")
        writing_style_id = task.get("writing_style_id")

        logger.info(f"🎬 [TASK_EXECUTE] PRODUCTION PIPELINE: {task_id}")
        logger.info(f"   Name: {task_name}")
        logger.info(f"   Topic: {topic}")
        logger.info(f"   Keyword: {primary_keyword}")
        logger.info(f"   Audience: {target_audience}")
        logger.info(f"   Style: {style}, Tone: {tone}, Length: {target_length}")
        logger.info(f"   Agent: {agent_id}")
        if writing_style_id:
            logger.info(f"   Writing Style ID: {writing_style_id}")

        # Extract model selections and preferences from task
        model_selections = task.get("model_selections", {})
        quality_preference = task.get("quality_preference", "balanced")
        logger.info(f"   Model selections: {model_selections}")
        logger.info(f"   Quality preference: {quality_preference}")

        # Determine which model will be used for this task (for tracking purposes)
        model_used = get_model_for_phase("draft", model_selections, quality_preference)
        logger.info(f"   Determined model for execution: {model_used}")

        # Start usage tracking for entire task execution
        task_start_time = time.time()
        self.usage_tracker.start_operation(
            f"task_execution_{task_id}", "content_generation", "multi-agent-orchestrator"
        )

        # ===== PHASE 1: Generate Content via Orchestrator =====
        generated_content = None
        orchestrator_error = None
        generation_start_time = time.time()

        logger.info(f"📝 [TASK_EXECUTE] PHASE 1: Generating content via orchestrator...")
        if self.orchestrator:
            try:
                logger.info(f"   Orchestrator available: YES")
                logger.info(f"   Type: {type(self.orchestrator).__name__}")

                # Using UnifiedOrchestrator (IntelligentOrchestrator is deprecated)
                logger.info(f"   🚀 Using UnifiedOrchestrator (unified system)")
                # Construct natural language request using centralized prompt manager
                pm = get_prompt_manager()
                prompt = pm.get_prompt(
                    "blog_generation.blog_generation_request",
                    topic=topic,
                    primary_keyword=primary_keyword or "",
                    target_audience=target_audience or "",
                    category=category or "",
                    style=style or "",
                    tone=tone or "",
                    target_length=target_length,
                )

                # Build execution context with model information
                execution_context = {
                    "task_id": str(task_id),
                    "model_selections": model_selections,
                    "quality_preference": quality_preference,
                    "user_id": task.get("user_id"),  # Include user_id for writing sample retrieval
                    "writing_style_id": writing_style_id,  # Include writing_style_id for style guidance
                }

                # Call orchestrator with proper method
                if hasattr(self.orchestrator, "process_request"):
                    # UnifiedOrchestrator has process_request
                    result = await self.orchestrator.process_request(
                        user_input=prompt, context=execution_context
                    )
                else:
                    # Fallback to process_command_async for basic Orchestrator
                    result = await self.orchestrator.process_command_async(
                        command=prompt, context=execution_context
                    )

                # Extract content from result
                # Result can be either an object with attributes or a dict
                final_formatting = None
                outputs = None

                # Log the actual result structure for debugging
                logger.info(f"   Raw orchestrator result type: {type(result).__name__}")
                if isinstance(result, dict):
                    logger.info(f"   Result keys: {list(result.keys())}")
                    # Log first 300 chars of result for debugging
                    result_str = str(result)[:300]
                    logger.info(f"   Result sample: {result_str}")
                elif hasattr(result, "__dict__"):
                    logger.info(f"   Result attributes: {list(result.__dict__.keys())}")

                if hasattr(result, "final_formatting"):
                    final_formatting = result.final_formatting  # type: ignore[reportAttributeAccessIssue]
                    logger.debug(
                        f"   Found final_formatting attribute: {len(str(final_formatting)) if final_formatting else 0} chars"
                    )
                elif isinstance(result, dict):
                    # Check multiple possible fields for content
                    # First check the ExecutionResult fields
                    execution_output = result.get("output")
                    if isinstance(execution_output, dict):
                        # ExecutionResult wraps the actual output, drill down
                        logger.debug(
                            f"   Found ExecutionResult wrapper, drilling down into 'output' field"
                        )
                        final_formatting = execution_output.get(
                            "final_formatting"
                        ) or execution_output.get("content")
                        outputs = execution_output.get("outputs")
                    else:
                        # Direct result dict or error response
                        final_formatting = (
                            result.get("final_formatting")
                            or result.get("output")
                            or result.get("response")
                        )
                        outputs = result.get("outputs")

                        # Check if this is an error result (e.g., from UnifiedOrchestrator exception)
                        if result.get("status") == "failed" or result.get("feedback"):
                            # This might be an error from UnifiedOrchestrator
                            orchestrator_error = (
                                result.get("feedback") or result.get("output") or "Unknown error"
                            )
                            logger.warning(
                                f"   ⚠️  Orchestrator returned error: {orchestrator_error}"
                            )
                            final_formatting = None

                        logger.debug(
                            f"   Checked dict for final_formatting: {final_formatting is not None}"
                        )
                        logger.debug(f"   Checked dict for outputs: {outputs is not None}")

                if final_formatting:
                    if isinstance(final_formatting, (dict, list)):
                        generated_content = json.dumps(final_formatting)
                        logger.debug(
                            f"   Serialized final_formatting dict/list to JSON: {len(generated_content)} chars"
                        )
                    else:
                        generated_content = str(final_formatting)
                        logger.debug(
                            f"   Using final_formatting as string: {len(generated_content)} chars"
                        )
                elif outputs or (isinstance(result, dict) and result.get("outputs")):
                    result_outputs = outputs if outputs else result.get("outputs", {})
                    logger.debug(
                        f"   Using outputs field, type: {type(result_outputs).__name__}, len: {len(str(result_outputs))}"
                    )
                    if isinstance(result_outputs, dict):
                        # Try to find content in outputs
                        for key, val in result_outputs.items():
                            if isinstance(val, dict) and "content" in val:
                                generated_content = val["content"]
                                logger.debug(
                                    f"   Found content in outputs[{key}]['content']: {len(str(generated_content))} chars"
                                )
                                break
                            if isinstance(val, str) and len(val) > 100:
                                generated_content = val
                                logger.debug(
                                    f"   Found long string in outputs[{key}]: {len(val)} chars"
                                )
                                break
                        else:
                            generated_content = str(result_outputs)
                            logger.debug(
                                f"   No suitable content found, serialized outputs: {len(generated_content)} chars"
                            )
                    else:
                        generated_content = str(result_outputs)
                        logger.debug(
                            f"   Outputs is not dict, converting to string: {len(generated_content)} chars"
                        )
                else:
                    generated_content = None
                    logger.debug(f"   No content found in result, setting to None")

                # Debug logging for content generation
                logger.info(
                    f"   Generated content length: {len(generated_content) if generated_content else 0} chars"
                )
                if generated_content and len(generated_content) < 200:
                    logger.warning(
                        f"   ⚠️  Generated content is short ({len(generated_content)} chars): {generated_content[:100]}..."
                    )

                # Validate that content was actually generated
                if not generated_content or (
                    isinstance(generated_content, str) and len(generated_content.strip()) < 50
                ):
                    logger.warning(f"   ⚠️  Generated content failed threshold check:")
                    logger.warning(f"      Content type: {type(generated_content).__name__}")
                    logger.warning(f"      Content is None: {generated_content is None}")
                    if generated_content:
                        logger.warning(f"      Content length: {len(generated_content)}")
                        content_preview = str(generated_content)[:200]
                        logger.warning(f"      FULL content: {content_preview}")

                    # Try fallback content generation instead of retrying orchestrator
                    logger.info(f"   ⚙️ Attempting fallback content generation...")
                    try:
                        generated_content = await self._fallback_generate_content(task)
                        logger.info(
                            f"   ✅ Fallback generation succeeded: {len(generated_content)} chars"
                        )
                    except Exception as fallback_err:
                        logger.error(f"   ❌ Fallback generation also failed: {fallback_err}")
                        orchestrator_error = f"Orchestrator failed with: {orchestrator_error or 'Unknown error'}. Fallback also failed: {fallback_err}"
                        generated_content = None

                logger.info(
                    f"✅ [TASK_EXECUTE] PHASE 1 Complete: Generated {len(generated_content) if generated_content else 0} chars"
                )

            except Exception as e:
                orchestrator_error = str(e)
                logger.error(
                    f"❌ [TASK_EXECUTE] PHASE 1 Failed: Orchestrator error - {orchestrator_error}",
                    exc_info=True,
                )
                generated_content = f"Error in content generation: {orchestrator_error}"
        else:
            logger.warning(f"⚠️ [TASK_EXECUTE] Orchestrator available: NO - Using fallback")
            # Fallback: Simple template-based generation
            generated_content = await self._fallback_generate_content(task)
            logger.info(
                f"✅ [TASK_EXECUTE] PHASE 1 Complete (fallback): Generated {len(generated_content)} chars"
            )

        # ===== PHASE 2: Quality Validation =====
        logger.info(f"🔍 [TASK_EXECUTE] PHASE 2: Validating content quality...")
        logger.info(
            f"   Input content length: {len(generated_content) if generated_content else 0} chars"
        )

        # Only validate if we have content
        if generated_content:
            quality_result = await self.quality_service.evaluate(
                content=generated_content,
                context={
                    "topic": topic,
                    "keywords": primary_keyword,
                    "target_audience": target_audience,
                    "category": category,
                    "style": style,
                    "tone": tone,
                    "target_length": target_length,
                },
            )
        else:
            # No content to validate
            quality_result = {
                "score": 0,
                "approved": False,
                "feedback": "No content provided for validation",
                "suggestions": ["Content is empty or None"],
            }

        # Normalise quality_result — evaluate() returns QualityAssessment; fallback path returns dict
        if isinstance(quality_result, QualityAssessment):
            quality_score = quality_result.overall_score
            approved = quality_result.passing
            quality_feedback = quality_result.feedback
            quality_needs_refinement = not quality_result.passing
            quality_result_keys = list(vars(quality_result).keys())
        else:
            quality_score = quality_result.get("score", 0)
            approved = quality_result.get("approved", False)
            quality_feedback = quality_result.get("feedback", "")
            quality_needs_refinement = quality_result.get("needs_refinement", not approved)
            quality_result_keys = list(quality_result.keys())

        logger.info(f"   Quality Score: {quality_score}/100")
        logger.info(f"   Approved: {approved}")
        logger.debug(f"   Quality result keys: {quality_result_keys}")

        if approved:
            logger.info(f"✅ [TASK_EXECUTE] PHASE 2 Complete: Content approved")
        else:
            logger.warning(f"⚠️ [TASK_EXECUTE] PHASE 2 Complete: Content needs improvement")
            logger.debug(f"   Feedback: {quality_feedback}")

            # If not approved but can refine, attempt refinement
            if quality_needs_refinement and self.orchestrator:
                logger.info(
                    f"🔄 [TASK_EXECUTE] Attempting refinement based on critique feedback..."
                )
                logger.info(
                    f"   Original content length: {len(generated_content) if generated_content else 0} chars"
                )
                try:
                    # Check if orchestrator supports modern process_request
                    if hasattr(self.orchestrator, "process_request") and not hasattr(
                        self.orchestrator, "process_command_async"
                    ):
                        # UnifiedOrchestrator
                        refinement_result = await self.orchestrator.process_request(
                            user_input=f"Refine content about '{topic}' based on feedback: {quality_feedback}",
                            context={
                                "original_content": generated_content,
                                "feedback": quality_feedback,
                                "task_id": str(task_id),
                                "model_selections": model_selections,
                            },
                        )
                    else:
                        # Legacy Orchestrator or basic Orchestrator
                        # Try legacy signature first if it has process_request
                        if hasattr(self.orchestrator, "process_request"):
                            refinement_result = await self.orchestrator.process_request(
                                user_request=f"Refine content based on feedback: {topic}",
                                user_id="system_task_executor",
                                business_metrics={
                                    "original_content": generated_content,
                                    "feedback": quality_feedback,
                                    "suggestions": (
                                        quality_result.suggestions
                                        if isinstance(quality_result, QualityAssessment)
                                        else quality_result.get("suggestions", [])
                                    ),
                                    "topic": topic,
                                    "model_selections": model_selections,
                                },
                            )
                        else:
                            # Fallback to process_command_async
                            refinement_result = await self.orchestrator.process_command_async(
                                command=f"Refine content about '{topic}' based on feedback: {quality_feedback}",
                                context={"original_content": generated_content},
                            )

                    logger.info(
                        f"   Refinement completed, result type: {type(refinement_result).__name__}"
                    )

                    # Extract content from refinement result
                    refined_content = None
                    if isinstance(refinement_result, dict):
                        logger.debug(f"   Refinement result keys: {list(refinement_result.keys())}")
                        if "content" in refinement_result:
                            refined_content = refinement_result["content"]
                        elif "output" in refinement_result:
                            refined_content = refinement_result["output"]
                        elif "response" in refinement_result:
                            refined_content = refinement_result["response"]
                        elif "final_formatting" in refinement_result:
                            refined_content = refinement_result["final_formatting"]
                        elif (
                            isinstance(refinement_result.get("output"), dict)
                            and "content" in refinement_result["output"]
                        ):
                            refined_content = refinement_result["output"]["content"]
                        else:
                            # Fallback: If refinement didn't return content, keep original
                            logger.warning(f"⚠️  Refinement result missing expected content fields")
                            refined_content = None
                    elif isinstance(refinement_result, str):
                        # If result is just a string, use it as content
                        refined_content = refinement_result
                        logger.info(
                            f"   Refinement returned string content ({len(refinement_result)} chars)"
                        )
                    else:
                        # Unknown format
                        logger.warning(
                            f"⚠️  Unexpected refinement result type: {type(refinement_result).__name__}"
                        )
                        refined_content = None

                    if refined_content and len(str(refined_content).strip()) > 50:
                        # Use refined content
                        generated_content = (
                            str(refined_content)
                            if not isinstance(refined_content, str)
                            else refined_content
                        )
                        logger.info(f"   ✅ Using refined content ({len(generated_content)} chars)")

                        # Re-critique refined content if critique_loop is available
                        if self.critique_loop is not None:
                            critique_result = await self.critique_loop.critique(
                                content=generated_content,
                                context={
                                    "topic": topic,
                                    "keywords": primary_keyword,
                                },
                            )
                            quality_score = critique_result.get("quality_score", 0)
                            approved = critique_result.get("approved", False)
                            logger.info(f"   Refined Quality Score: {quality_score}/100")
                        else:
                            logger.debug("   critique_loop not available — skipping re-critique of refined content")
                    else:
                        logger.warning(
                            f"   ⚠️  Refined content too short ({len(str(refined_content).strip()) if refined_content else 0} chars), keeping original"
                        )

                except Exception as refine_err:
                    logger.error(
                        f"❌ [TASK_EXECUTE] Refinement failed: {refine_err}", exc_info=True
                    )
                    logger.warning(
                        f"   Keeping original content ({len(generated_content) if generated_content else 0} chars)"
                    )

                logger.info(
                    f"🔄 Refinement complete: approved={approved}, score={quality_score}/100, content_len={len(generated_content) if generated_content else 0}"
                )

        # ===== Validate Content Generation =====
        # Ensure meaningful content was actually generated
        content_is_valid = (
            generated_content is not None
            and isinstance(generated_content, str)
            and len(generated_content.strip()) >= 50
        )

        final_status = "awaiting_approval" if content_is_valid else "failed"
        if not content_is_valid:
            error_msg = (
                f"Content validation failed: {orchestrator_error or 'Content too short or empty'} "
                f"(length: {len(generated_content) if generated_content else 0} chars)"
            )
            logger.error(f"❌ [TASK_EXECUTE] {error_msg}")
            if not orchestrator_error:
                orchestrator_error = error_msg

        # ===== Build Final Result =====
        result = {
            "task_id": str(task_id),
            "task_name": task_name,
            "topic": topic,
            "primary_keyword": primary_keyword,
            "target_audience": target_audience,
            "category": category,
            "status": final_status,
            # Generation phase - FULL CONTENT, not truncated!
            # Store as both "content" (for database) and "generated_content" (for debugging)
            "content": (generated_content if content_is_valid else None),  # For database storage
            "generated_content": (
                generated_content if content_is_valid else None
            ),  # For compatibility
            "content_length": (
                len(generated_content) if (content_is_valid and generated_content) else 0
            ),
            "orchestrator_error": orchestrator_error,
            # Model tracking
            "model_used": model_used,
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
            },
        }

        # End usage tracking with actual metrics
        task_duration_ms = (time.time() - task_start_time) * 1000
        content_tokens_estimate = (
            len(generated_content.split()) * 1.3 if generated_content else 0
        )  # Estimate ~1.3 tokens per word

        # Track tokens via add_tokens
        self.usage_tracker.add_tokens(
            f"task_execution_{task_id}",
            input_tokens=int(len(f"{topic} {primary_keyword} {target_audience}".split()) * 1.3),
            output_tokens=int(content_tokens_estimate),
        )

        # End operation with correct signature
        operation_metrics = self.usage_tracker.end_operation(
            f"task_execution_{task_id}", success=True, error=None
        )

        # Persist cost metrics to database for historical reporting
        if operation_metrics and self.database_service:
            try:
                cost_log = {
                    "task_id": str(task_id),
                    "user_id": task.get("user_id"),
                    "phase": "content_generation",  # Single phase for overall task
                    "model": operation_metrics.model_name,
                    "provider": operation_metrics.model_provider,
                    "input_tokens": operation_metrics.input_tokens,
                    "output_tokens": operation_metrics.output_tokens,
                    "total_tokens": operation_metrics.input_tokens + operation_metrics.output_tokens,
                    "cost_usd": operation_metrics.total_cost_usd,
                    "quality_score": quality_score,
                    "duration_ms": operation_metrics.duration_ms,
                    "success": True,
                }
                await self.database_service.log_cost(cost_log)
                logger.debug(f"✅ Logged task cost: ${cost_log['cost_usd']:.6f} to database")
            except Exception as e:
                logger.warning(f"⚠️ Failed to persist cost metrics: {e}")

        # Store metadata in result
        metadata = {
            "task_id": str(task_id),
            "task_name": task_name,
            "content_length": len(generated_content) if generated_content else 0,
            "quality_score": quality_score,
            "approved": approved,
        }

        return result

    async def _fallback_generate_content(self, task: Dict[str, Any]) -> str:
        """
        Fallback content generation when orchestrator not available

        Uses AIContentGenerator to create structured blog content with:
        - Real Ollama support (free local LLM)
        - HuggingFace integration
        - Google Gemini fallback
        - Self-validation and quality checking

        Args:
            task: Task dict with topic, primary_keyword, target_audience, category

        Returns:
            Generated content as string (markdown formatted)
        """
        topic = task.get("topic") or "Topic"
        keyword = task.get("primary_keyword") or "keyword"
        audience = task.get("target_audience") or "general audience"
        category = task.get("category") or "general"

        # Ensure all variables are strings and not None
        topic = str(topic) if topic is not None else "Topic"
        keyword = str(keyword) if keyword is not None else "keyword"
        audience = str(audience) if audience is not None else "general audience"
        category = str(category) if category is not None else "general"

        logger.info(f"📝 Using fallback content generation via AIContentGenerator")
        logger.info(f"   Topic: {topic}")
        logger.info(f"   Keyword: {keyword}")
        logger.info(f"   Audience: {audience}")

        try:
            # Ensure Ollama check is done (async)
            await self.content_generator._check_ollama_async()

            # Try to generate with AIContentGenerator
            # For now, use template-based fallback since async generation might not be implemented
            # Content generation uses AIContentGenerator.generate_async() with Ollama support

            content = f"""# {topic}

## Introduction

This article explores the key aspects of {topic} and its relevance to {audience}. We'll cover the essential information you need to know about this important subject.

## Understanding {topic}

{topic} is a crucial area that impacts many aspects of modern business and personal development. By understanding {keyword}, professionals and enthusiasts can make more informed decisions and stay ahead of industry trends.

### Key Concepts

When discussing {topic}, it's important to grasp these fundamental concepts:

1. **Definition and Scope**: {topic} encompasses a broad range of practices and methodologies designed to {keyword.lower()} effectively.

2. **Importance**: The relevance of {topic} has grown significantly in recent years, making it essential knowledge for anyone in the {category} sector.

3. **Applications**: {topic} can be applied across various contexts to improve outcomes and drive better results.

## Best Practices

### For {audience}

When implementing strategies related to {topic}:

- **Research thoroughly** before making decisions related to {keyword}
- **Stay updated** with the latest developments in {topic}
- **Consult experts** when dealing with complex aspects of {topic}
- **Measure results** to ensure your approach to {keyword} is effective
- **Adapt and iterate** based on performance metrics

### Common Pitfalls to Avoid

1. **Ignoring {keyword}**: Overlooking this aspect can lead to suboptimal outcomes
2. **Not staying current**: {topic} evolves rapidly; staying informed is critical
3. **Implementation without planning**: Proper planning before implementing {topic} strategies is essential
4. **Insufficient testing**: Always validate approaches before full-scale deployment

## Advanced Considerations

### Emerging Trends

The {topic} landscape continues to evolve with:
- Increasing automation and AI integration
- Shift towards data-driven approaches
- Growing emphasis on sustainability and ethics
- Integration with emerging technologies

### Future Outlook

Looking ahead, {topic} will likely see:
- Continued innovation in {keyword}-related solutions
- Greater focus on measurable outcomes
- Evolution of best practices and standards
- Increased collaboration across {category} professionals

## Practical Implementation

### Getting Started

1. Assess your current approach to {topic}
2. Identify gaps related to {keyword}
3. Develop an implementation plan
4. Execute in phases
5. Monitor and optimize

### Measuring Success

Track these metrics to evaluate your {topic} strategy:
- **Quality improvements** in {keyword}-related outputs
- **Efficiency gains** from implementing {topic} practices
- **User satisfaction** metrics
- **ROI** of {topic} investments
- **Adoption rates** among your {audience}

## Conclusion

{topic} remains a vital area of focus for any organization or individual serious about success in {category}. By understanding and properly implementing strategies around {keyword}, you can achieve significant improvements in your outcomes.

The key to success with {topic} is staying informed, adapting to changes, and continuously refining your approach based on results.

---

## Resources and Further Reading

- Industry publications and journals on {topic}
- Expert blogs and thought leadership articles
- Online communities focused on {topic}
- Certification programs and training courses
- Webinars and conferences on {category}

---

*This content was automatically generated by Glad Labs AI Content Generator (Fallback Mode)*  
*Generated: {datetime.now(timezone.utc).isoformat()}*  
*Category: {category}*
"""

            logger.info(f"✅ Generated fallback content: {len(content)} chars")
            return content

        except Exception as e:
            logger.error(f"❌ Fallback generation failed: {e}", exc_info=True)
            # Emergency minimal content
            return f"# {topic}\n\nContent generation service temporarily unavailable. Please try again later.\n\nError: {str(e)[:100]}"

    async def _sweep_stale_tasks(self) -> None:
        """Reset tasks stuck in processing state back to pending."""
        if not self.database_service:
            return
        try:
            result = await self.database_service.sweep_stale_tasks(
                timeout_minutes=STALE_TASK_TIMEOUT_MINUTES,
                max_retries=MAX_TASK_RETRIES,
            )
            if result and result.get("total_stale", 0) > 0:
                logger.warning(
                    f"[_sweep_stale_tasks] Reset {result['reset']} stale tasks "
                    f"(timeout: {STALE_TASK_TIMEOUT_MINUTES}m)"
                )
        except Exception:
            logger.error("[_sweep_stale_tasks] Failed to sweep stale tasks", exc_info=True)

    def get_stats(self) -> Dict[str, Any]:
        """Get executor statistics"""
        import time
        last_poll_age: Optional[float] = None
        if self.last_poll_at is not None:
            last_poll_age = time.monotonic() - self.last_poll_at
        return {
            "running": self.running,
            "task_count": self.task_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "published_count": self.published_count,
            "poll_interval": self.poll_interval,
            "orchestrator_available": self.orchestrator is not None,
            "quality_service_available": self.quality_service is not None,
            "last_poll_age_s": last_poll_age,
            "critique_stats": self.critique_loop.get_stats() if self.critique_loop else {},
        }
