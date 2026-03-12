"""
Unified Orchestrator - Master AI System for Glad Labs

Consolidates Orchestrator, IntelligentOrchestrator, and ContentOrchestrator
into a single, unified system that:

1. UNDERSTANDS: Natural language requests with full context
2. ROUTES: Directs requests to appropriate agents/workflows
3. EXECUTES: Runs content pipeline or custom workflows
4. ASSESSES: Evaluates quality with 7-criteria framework
5. REFINES: Improves outputs until threshold met
6. LEARNS: Captures training data from every execution
7. PERSISTS: Stores results and metrics in PostgreSQL

Natural Language Routing:
- "Create content about X" → Content Pipeline
- "Analyze financial data" → Financial Agent
- "Check compliance" → Compliance Agent
- "Show me [what]" → Retrieval/Analytics
- "What should I [verb]" → Decision Support
- Other requests → Fallback handlers

Architecture:
- Single unified interface (this class)
- Pluggable agents and workflows
- PostgreSQL-backed persistence
- Training data accumulation
- MCP-based tool discovery
- Quality feedback loops
"""

import asyncio
import json
from services.logger_config import get_logger
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.websocket_event_broadcaster import emit_task_progress
from utils.error_handler import handle_service_error
from utils.json_encoder import safe_json_load

# Import shared types from orchestrator_types (single source of truth)
from .orchestrator_types import (
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    Request,
    RequestType,
)

# Import request classifier
from .request_router import classify_request

logger = get_logger(__name__)

# ============================================================================
# RE-EXPORTS (backward compatibility)
# ============================================================================
# Names previously defined in this module are now in orchestrator_types.
# They are imported above so existing `from services.unified_orchestrator import X`
# statements continue to work without modification.

__all__ = [
    "ExecutionContext",
    "ExecutionResult",
    "ExecutionStatus",
    "Request",
    "RequestType",
    "UnifiedOrchestrator",
]

class UnifiedOrchestrator:
    """
    Master orchestrator handling all AI operations for Glad Labs.

    Single entry point for:
    1. Natural language understanding and routing
    2. Specialized agent invocation (content, financial, compliance)
    3. Quality assessment and refinement
    4. Training data collection
    5. Result persistence
    """

    def __init__(
        self,
        database_service=None,
        model_router=None,
        quality_service=None,
        memory_system=None,
        **agents,
    ):
        """
        Initialize unified orchestrator with all services

        Args:
            database_service: DatabaseService for PostgreSQL operations
            model_router: ModelRouter for LLM access
            quality_service: ContentQualityService for quality assessment
            memory_system: Memory system for learning
            **agents: Injected agent instances
                - content_orchestrator: ContentOrchestrator
                - financial_agent: FinancialAgent (optional)
                - compliance_agent: ComplianceAgent (optional)
                - Any other custom agents
        """
        self.database_service = database_service
        self.model_router = model_router
        self.quality_service = quality_service
        self.memory_system = memory_system

        # Register agents
        self.agents = agents or {}

        # Statistics
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0

        logger.info(
            "🚀 UnifiedOrchestrator initialized with %d agents: %s",
            len(self.agents),
            ", ".join(self.agents.keys()),
        )

    def _get_agent_instance(self, agent_name: str, **kwargs) -> Any:
        """
        Get an agent instance from the registry, with fallback to direct import.

        This method enables dynamic agent selection and instantiation, allowing:
        - Runtime agent discovery via AgentRegistry
        - Custom parameter passing (e.g., LLMClient for CreativeAgent)
        - Graceful fallback to direct imports for backward compatibility

        Args:
            agent_name: Name of the agent to instantiate (e.g., "research_agent", "creative_agent")
            **kwargs: Additional keyword arguments to pass to agent constructor

        Returns:
            Instantiated agent object

        Example:
            ```python
            # Dynamic instantiation from registry
            research_agent = self._get_agent_instance("research_agent")

            # With custom parameters
            llm_client = LLMClient(model_name="claude-3-sonnet")
            creative_agent = self._get_agent_instance(
                "creative_agent",
                llm_client=llm_client
            )

            # Fallback to direct import if registry not populated
            # Will still work if agent classes are importable
            ```
        """
        try:
            from agents.registry import get_agent_registry

            registry = get_agent_registry()

            # Try to get agent class from registry
            agent_class = registry.get_agent_class(agent_name)

            if agent_class:
                logger.debug(
                    f"Instantiating agent '{agent_name}' from registry with kwargs: {kwargs.keys()}"
                )
                try:
                    return agent_class(**kwargs)
                except TypeError as e:
                    # Agent doesn't accept kwargs, try without
                    logger.debug(
                        f"Agent '{agent_name}' doesn't accept kwargs, instantiating without: {e}"
                    )
                    return agent_class()

            logger.debug(
                f"Agent '{agent_name}' not found in registry, falling back to direct import"
            )
        except Exception as e:
            logger.error(
                f"[_resolve_agent_class] Registry lookup failed for '{agent_name}': {e}, falling back to direct import",
                exc_info=True,
            )

        # Fallback: Direct import based on agent name
        # This maintains backward compatibility if registry is not populated
        agent_mapping = {
            "research_agent": "agents.content_agent.agents.research_agent:ResearchAgent",
            "creative_agent": "agents.content_agent.agents.creative_agent:CreativeAgent",
            "qa_agent": "agents.content_agent.agents.qa_agent:QAAgent",
            "image_agent": "agents.content_agent.agents.image_agent:ImageAgent",
            "publishing_agent": "agents.content_agent.agents.postgres_publishing_agent:PostgreSQLPublishingAgent",
            "financial_agent": "agents.financial_agent:FinancialAgent",
            "market_agent": "agents.market_insight_agent:MarketInsightAgent",
            "compliance_agent": "agents.compliance_agent:ComplianceAgent",
        }

        if agent_name in agent_mapping:
            module_path, class_name = agent_mapping[agent_name].rsplit(":", 1)
            try:
                module = __import__(module_path, fromlist=[class_name])
                agent_class = getattr(module, class_name)
                logger.debug(
                    f"Instantiating agent '{agent_name}' via direct import with kwargs: {kwargs.keys()}"
                )

                try:
                    return agent_class(**kwargs)
                except TypeError:
                    # Agent doesn't accept kwargs, try without
                    logger.debug(
                        f"Agent '{agent_name}' doesn't accept kwargs, instantiating without"
                    )
                    return agent_class()
            except (ImportError, AttributeError) as e:
                logger.error(
                    f"Failed to import agent '{agent_name}': {e}",
                    exc_info=True,
                )
                raise ValueError(
                    f"Agent '{agent_name}' not found in registry or importable via fallback"
                )

        raise ValueError(f"Unknown agent: '{agent_name}'. Not in registry or fallback mapping.")

    async def process_request(
        self, user_input: str, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Process a user request end-to-end.

        Flow:
        1. Parse and route request
        2. Extract intent and parameters
        3. Execute appropriate workflow
        4. Assess quality
        5. Refine if needed
        6. Store training data
        7. Return result

        Args:
            user_input: Natural language request from user
            context: Optional execution context

        Returns:
            Dictionary with result, task_id, status, etc.
        """
        start_time = asyncio.get_running_loop().time()
        request_id = str(uuid.uuid4())

        try:
            self.total_requests += 1
            logger.info("[%s] Processing: %s", request_id, user_input[:100])

            # Step 1: Parse and route request
            request = await self._parse_request(user_input, request_id, context)
            logger.info("[%s] Detected type: %s", request_id, request.request_type.value)

            # Step 2: Route to appropriate handler
            if request.request_type == RequestType.CONTENT_CREATION:
                result = await self._handle_content_creation(request)
            elif request.request_type == RequestType.CONTENT_SUBTASK:
                result = await self._handle_content_subtask(request)
            elif request.request_type == RequestType.FINANCIAL_ANALYSIS:
                result = await self._handle_financial_analysis(request)
            elif request.request_type == RequestType.COMPLIANCE_CHECK:
                result = await self._handle_compliance_check(request)
            elif request.request_type == RequestType.TASK_MANAGEMENT:
                result = await self._handle_task_management(request)
            elif request.request_type == RequestType.INFORMATION_RETRIEVAL:
                result = await self._handle_information_retrieval(request)
            elif request.request_type == RequestType.DECISION_SUPPORT:
                result = await self._handle_decision_support(request)
            elif request.request_type == RequestType.SYSTEM_OPERATION:
                result = await self._handle_system_operation(request)
            elif request.request_type == RequestType.INTERVENTION:
                result = await self._handle_intervention(request)
            else:
                result = await self._handle_unknown(request)

            # Step 3: Store result and training data
            duration_ms = (asyncio.get_running_loop().time() - start_time) * 1000

            if isinstance(result, ExecutionResult):
                result.duration_ms = duration_ms
                if self.database_service:
                    await self._store_execution_result(result)
                self.successful_requests += 1
                return self._result_to_dict(result)

            # Legacy response format
            self.successful_requests += 1
            return result

        except Exception as e:
            self.failed_requests += 1
            logger.error("[process_request] Error: %s", str(e), exc_info=True)
            return {
                "request_id": request_id,
                "status": "error",
                "error": str(e),
                "message": "An error occurred processing your request: %s" % str(e),
            }

    # ========================================================================
    # REQUEST PARSING AND ROUTING
    # ========================================================================

    async def _parse_request(
        self, user_input: str, request_id: str, context: Optional[Dict[str, Any]] = None
    ) -> Request:
        """
        Parse a natural-language request and return a typed Request object.

        Delegates to :func:`request_router.classify_request` which owns the
        keyword routing table.  Passing ``self._extract_content_params`` as the
        content-params extractor keeps the dependency pointing inward (router
        does not import from this module).
        """
        return classify_request(
            user_input=user_input,
            request_id=request_id,
            context=context,
            extract_content_params_fn=self._extract_content_params,
        )

    def _extract_content_params(self, text: str) -> Dict[str, Any]:
        """Extract content parameters from natural language or structured request format.

        Handles the structured format produced by blog_generation.blog_generation_request:
            Topic: <value>
            Primary Keyword: <value>
            Style: <value>
            ...
        Falls back to keyword-based detection for unstructured natural language input.
        Fixes #151.
        """
        params: Dict[str, Any] = {}

        # Try to parse the structured format (from blog_generation_request prompt template)
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("Topic:"):
                params["topic"] = line[6:].strip()
            elif line.startswith("Primary Keyword:"):
                params["primary_keyword"] = line[16:].strip()
            elif line.startswith("Target Audience:"):
                params["target_audience"] = line[16:].strip()
            elif line.startswith("Category:"):
                params["category"] = line[9:].strip()
            elif line.startswith("Style:"):
                params["style"] = line[6:].strip()
            elif line.startswith("Tone:"):
                params["tone"] = line[5:].strip()
            elif line.startswith("Target Length:"):
                try:
                    params["target_length"] = int(line[14:].strip().split()[0])
                except (ValueError, IndexError):
                    pass

        if "topic" in params:
            # Successfully parsed structured format — return with defaults for missing fields
            if "style" not in params:
                params["style"] = "professional"
            if "tone" not in params:
                params["tone"] = "informative"
            return params

        # Fallback: unstructured natural language — use keyword detection
        params["topic"] = text

        if "professional" in text.lower():
            params["style"] = "professional"
        elif "casual" in text.lower():
            params["style"] = "casual"
        elif "technical" in text.lower():
            params["style"] = "technical"
        else:
            params["style"] = "professional"

        if "educational" in text.lower():
            params["tone"] = "educational"
        elif "entertaining" in text.lower():
            params["tone"] = "entertaining"
        else:
            params["tone"] = "informative"

        return params

    # ========================================================================
    # MODEL SELECTION HELPER
    # ========================================================================

    def _get_model_for_phase(
        self, phase: str, model_selections: Dict[str, str], quality_preference: str
    ) -> Optional[str]:
        """
        Get the appropriate LLM model for a given generation phase.

        Phase-differentiated model tiers (#196) — different phases have different needs:
        - draft/outline: complex creative generation → best available model
        - research/assess/finalize: simple filtering/classification → cheap model
        - refine: editing existing draft → cheap/medium model

        Args:
            phase: Generation phase ('research', 'draft', 'assess', 'refine', 'finalize')
            model_selections: User's per-phase model selections
            quality_preference: Fallback preference (fast, balanced, quality)

        Returns:
            Model identifier (e.g., "gpt-4", "claude-opus") or None to use config default
        """
        from .model_router import get_model_for_phase  # pylint: disable=import-outside-toplevel

        # Try to get specific model selection for this phase
        if model_selections and phase in model_selections:
            selected = model_selections[phase]
            # If user selected a specific model (not "auto"), use it
            if selected and selected != "auto":
                logger.info("   Model selection: Using %s for %s phase", selected, phase)
                return selected

        # Return differentiated default via model_router (#196)
        default = get_model_for_phase(phase, model_selections or {}, quality_preference or "balanced")
        logger.info(
            "   Model selection: %s for %s phase (quality=%s)", default, phase, quality_preference
        )
        return default

    # ========================================================================
    # REQUEST HANDLERS
    # ========================================================================

    async def _handle_content_creation(self, request: Request) -> ExecutionResult:
        """
        Handle content creation request -- Full 5-stage pipeline with human approval gate.

        Thin coordinator: delegates each stage to a private coroutine and assembles
        the final result.  All business logic lives in the stage methods.
        """
        logger.info("[%s] Handling content creation", request.request_id)

        from utils.constraint_utils import (  # pylint: disable=import-outside-toplevel
            ContentConstraints,
            apply_strict_mode,
            calculate_phase_targets,
            merge_compliance_reports,
        )

        try:
            # ----------------------------------------------------------------
            # Extract common parameters
            # ----------------------------------------------------------------
            topic = request.parameters.get("topic", request.original_text)
            style = request.parameters.get("style", "professional")
            keywords = request.parameters.get("keywords", [topic])
            content_constraints = request.parameters.get("content_constraints", {})

            model_selections: Dict[str, Any] = {}
            quality_preference = "balanced"
            if request.context:
                model_selections = safe_json_load(
                    request.context.get("model_selections", {}), fallback={}
                )
                quality_preference = request.context.get("quality_preference", "balanced")

            logger.info("[%s] Model Configuration:", request.request_id)
            logger.info("   - Model Selections: %s", model_selections)
            logger.info("   - Quality Preference: %s", quality_preference)

            task_id = "task_%s_%s" % (
                int(datetime.now(timezone.utc).timestamp()),
                uuid.uuid4().hex[:6],
            )
            logger.info("[%s] Starting 5-stage pipeline for: %s", request.request_id, topic)

            # ----------------------------------------------------------------
            # Initialize constraints & phase targets (shared across all stages)
            # ----------------------------------------------------------------
            constraints = ContentConstraints(
                word_count=content_constraints.get("word_count", 1500) or 1500,
                writing_style=content_constraints.get("writing_style", style) or style,
                word_count_tolerance=content_constraints.get("word_count_tolerance", 10) or 10,
                per_phase_overrides=content_constraints.get("per_phase_overrides"),
                strict_mode=content_constraints.get("strict_mode", False) or False,
            )
            logger.info(
                "[%s] Constraints: %s\u00b1%s%% words, %s style",
                request.request_id,
                constraints.word_count,
                constraints.word_count_tolerance,
                constraints.writing_style,
            )
            phase_targets = calculate_phase_targets(
                constraints.word_count, constraints, num_phases=5
            )

            # ----------------------------------------------------------------
            # Stage 1: Research  (10% -> 25%)
            # ----------------------------------------------------------------
            research_text, research_compliance, writing_style_guidance = (
                await self._run_research_stage(
                    request=request,
                    topic=topic,
                    keywords=keywords,
                    constraints=constraints,
                    phase_targets=phase_targets,
                    task_id=task_id,
                )
            )

            # ----------------------------------------------------------------
            # Stage 2: Creative Draft  (25% -> 45%)
            # ----------------------------------------------------------------
            draft_post, creative_compliance = await self._run_draft_stage(
                request=request,
                topic=topic,
                style=style,
                research_text=research_text,
                writing_style_guidance=writing_style_guidance,
                constraints=constraints,
                phase_targets=phase_targets,
                task_id=task_id,
                model_selections=model_selections,
                quality_preference=quality_preference,
            )

            # ----------------------------------------------------------------
            # Stage 3: QA Review Loop  (45% -> 60%)
            # ----------------------------------------------------------------
            content, feedback, quality_score, qa_compliance = await self._run_qa_stage(
                request=request,
                topic=topic,
                draft_post=draft_post,
                constraints=constraints,
                phase_targets=phase_targets,
                task_id=task_id,
                model_selections=model_selections,
                quality_preference=quality_preference,
                writing_style_guidance=writing_style_guidance,
            )

            # ----------------------------------------------------------------
            # Stage 4: Image Selection  (60% -> 75%)
            # ----------------------------------------------------------------
            featured_image_url = await self._run_image_stage(
                request=request,
                topic=topic,
                task_id=task_id,
            )

            # ----------------------------------------------------------------
            # Stage 5: Formatting  (75% -> 90%)
            # ----------------------------------------------------------------
            formatted_content, excerpt = await self._run_formatting_stage(
                request=request,
                topic=topic,
                content=content,
                task_id=task_id,
            )

            # ----------------------------------------------------------------
            # Stage 6: Awaiting Human Approval  (90% -> 100%)
            # ----------------------------------------------------------------
            logger.info("[%s] STAGE 6: Awaiting Human Approval", request.request_id)

            compliance_reports = [research_compliance, creative_compliance, qa_compliance]
            overall_compliance = merge_compliance_reports(compliance_reports)
            strict_mode_valid, strict_mode_error = apply_strict_mode(overall_compliance)

            if not strict_mode_valid:
                logger.warning(
                    "[%s] STRICT MODE VIOLATION: %s", request.request_id, strict_mode_error
                )

            result = {
                "task_id": task_id,
                "status": "awaiting_approval",
                "approval_status": "awaiting_review",
                "content": formatted_content,
                "excerpt": excerpt,
                "featured_image_url": featured_image_url,
                "qa_feedback": feedback,
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
                "message": "\u2705 Content ready for human review. Human approval required before publishing.",
                "next_action": "POST /api/content/tasks/%s/approve with human decision" % task_id,
            }

            logger.info("[%s] \u2705 Pipeline complete. Awaiting human approval.", request.request_id)

            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.PENDING_APPROVAL,
                output=result,
                task_id=task_id,
                quality_score=quality_score,
                feedback=feedback,
                metadata=result,
            )

        except Exception as e:
            logger.error(f"[_handle_content_creation] Content creation failed: {e}", exc_info=True)
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.FAILED,
                output=str(e),
                feedback="Content creation failed: %s" % str(e),
            )

    # ========================================================================
    # PIPELINE STAGE HELPERS (called exclusively by _handle_content_creation)
    # ========================================================================

    async def _run_research_stage(
        self,
        *,
        request: Request,
        topic: str,
        keywords: list,
        constraints: Any,
        phase_targets: Dict[str, Any],
        task_id: str,
    ) -> tuple:
        """
        Stage 1 -- Research (10% -> 25%).

        Runs research and writing-style lookup concurrently, handles timeouts and
        exceptions gracefully (pipeline continues with empty data on failure).

        Returns:
            (research_text, research_compliance, writing_style_guidance)
        """
        from utils.constraint_utils import (  # pylint: disable=import-outside-toplevel
            count_words_in_content,
            validate_constraints,
        )
        from services.writing_style_integration import (  # pylint: disable=import-outside-toplevel
            WritingStyleIntegrationService,
        )

        logger.info("[%s] STAGE 1: Research (+ concurrent writing-style lookup)", request.request_id)

        user_id = request.context.get("user_id") if request.context else None
        writing_style_id = request.context.get("writing_style_id") if request.context else None

        research_agent = self._get_agent_instance("research_agent")

        async def _fetch_writing_style() -> dict | None:
            if not self.database_service:
                return None
            integration_svc = WritingStyleIntegrationService(self.database_service)
            return await integration_svc.get_sample_for_content_generation(
                writing_style_id=writing_style_id, user_id=user_id
            )

        research_coro = asyncio.wait_for(
            research_agent.run(topic, keywords[:5]),
            timeout=60.0,
        )

        # Run concurrently; return_exceptions=True ensures writing-style failure
        # does not abort the research phase.
        research_result, style_result = await asyncio.gather(
            research_coro,
            _fetch_writing_style(),
            return_exceptions=True,
        )

        # --- process research result ---
        if isinstance(research_result, asyncio.TimeoutError):
            logger.warning(
                "[%s] Research agent timed out after 60s, using empty research data",
                request.request_id,
            )
            research_data: Any = ""
        elif isinstance(research_result, BaseException):
            logger.error(
                "[%s] Research agent raised an exception: %s",
                request.request_id,
                research_result,
                exc_info=research_result,
            )
            research_data = ""
        else:
            research_data = research_result

        research_text: str = research_data if isinstance(research_data, str) else str(research_data)

        research_compliance = validate_constraints(
            research_text,
            constraints,
            phase_name="research",
            word_count_target=phase_targets.get("research"),
        )
        logger.info(
            "[%s] Research complete: %s words",
            request.request_id,
            count_words_in_content(research_text),
        )

        try:
            await emit_task_progress(
                task_id=task_id,
                status="RUNNING",
                progress=25,
                current_step="Research Complete",
                total_steps=5,
                completed_steps=1,
                message="Research phase completed - gathered background information",
            )
        except Exception as e:
            logger.error(
                "[_run_research_stage] Failed to emit research progress: %s", e, exc_info=True
            )

        # --- process writing-style result ---
        writing_style_guidance = ""
        if isinstance(style_result, BaseException):
            logger.error(
                "[_run_research_stage] Could not retrieve writing sample: %s, %s",
                request.request_id,
                style_result,
                exc_info=style_result,
            )
        elif style_result is not None:
            sample_data = style_result
            writing_style_guidance = sample_data.get("writing_style_guidance", "")
            analysis = sample_data.get("analysis", {})
            sample_title = sample_data.get("sample_title", "Unknown")
            logger.info("[%s] Using writing sample: %s", request.request_id, sample_title)
            logger.info(
                "[%s]   - Detected tone: %s", request.request_id, analysis.get("detected_tone")
            )
            logger.info(
                "[%s]   - Detected style: %s", request.request_id, analysis.get("detected_style")
            )
            logger.info(
                "[%s]   - Avg sentence length: %s words",
                request.request_id,
                analysis.get("avg_sentence_length"),
            )

        return research_text, research_compliance, writing_style_guidance

    async def _run_draft_stage(
        self,
        *,
        request: Request,
        topic: str,
        style: str,
        research_text: str,
        writing_style_guidance: str,
        constraints: Any,
        phase_targets: Dict[str, Any],
        task_id: str,
        model_selections: Dict[str, Any],
        quality_preference: str,
    ) -> tuple:
        """
        Stage 2 -- Creative Draft (25% -> 45%).

        Instantiates creative agent with the per-phase model selection and runs the
        initial draft.  Raises RuntimeError if the agent exceeds its 120 s timeout
        (unrecoverable -- pipeline cannot continue without a draft).

        Returns:
            (draft_post, creative_compliance)
        """
        from utils.constraint_utils import (  # pylint: disable=import-outside-toplevel
            count_words_in_content,
            validate_constraints,
        )
        from agents.content_agent.services.llm_client import (  # pylint: disable=import-outside-toplevel
            LLMClient,
        )
        from agents.content_agent.utils.data_models import (  # pylint: disable=import-outside-toplevel
            BlogPost,
        )

        logger.info("[%s] STAGE 2: Creative Draft", request.request_id)

        draft_model = self._get_model_for_phase("draft", model_selections, quality_preference)
        llm_client = LLMClient(model_name=draft_model) if draft_model else LLMClient()
        creative_agent = self._get_agent_instance("creative_agent", llm_client=llm_client)

        post = BlogPost(
            topic=topic,
            primary_keyword=request.parameters.get("primary_keyword") or topic,
            target_audience=request.parameters.get("target_audience") or "general",
            category=request.parameters.get("category") or "general",
            status="draft",
            research_data=research_text,
            writing_style=style,
        )  # fix #152: use extracted params instead of hardcoded defaults

        if writing_style_guidance:
            post.metadata = {"writing_sample_guidance": writing_style_guidance}

        phase_target = phase_targets.get("creative", 300)
        try:
            draft_post = await asyncio.wait_for(
                creative_agent.run(
                    post,
                    is_refinement=False,
                    word_count_target=phase_target,
                    constraints=constraints,
                ),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error(
                "[%s] Creative agent timed out after 120s -- pipeline cannot continue without draft",
                request.request_id,
            )
            raise RuntimeError(
                f"[{request.request_id}] Creative agent exceeded 120s timeout"
            ) from None

        draft_text: str = draft_post.body if hasattr(draft_post, "body") else str(draft_post)

        creative_compliance = validate_constraints(
            draft_text,
            constraints,
            phase_name="creative",
            word_count_target=phase_targets.get("creative"),
        )
        logger.info(
            "[%s] Draft complete: %s words",
            request.request_id,
            count_words_in_content(draft_text),
        )

        try:
            await emit_task_progress(
                task_id=task_id,
                status="RUNNING",
                progress=45,
                current_step="Creative Draft Complete",
                total_steps=5,
                completed_steps=2,
                message="Creative draft generated - ready for quality review",
            )
        except Exception as e:
            logger.error(
                "[_run_draft_stage] Failed to emit creative progress: %s", e, exc_info=True
            )

        return draft_post, creative_compliance

    async def _run_qa_stage(
        self,
        *,
        request: Request,
        topic: str,
        draft_post: Any,
        constraints: Any,
        phase_targets: Dict[str, Any],
        task_id: str,
        model_selections: Dict[str, Any],
        quality_preference: str,
        writing_style_guidance: str,
    ) -> tuple:
        """
        Stage 3 -- QA Review Loop (45% -> 60%).

        Evaluates draft quality and optionally refines it (up to max_iterations=2).
        Constraint violations suppress QA approval and append feedback.
        QA timeouts are handled gracefully -- pipeline proceeds with current draft.

        Returns:
            (content, feedback, quality_score, qa_compliance)
        """
        from utils.constraint_utils import (  # pylint: disable=import-outside-toplevel
            validate_constraints,
        )
        from agents.content_agent.services.llm_client import (  # pylint: disable=import-outside-toplevel
            LLMClient,
        )
        from services.database_service import (  # pylint: disable=import-outside-toplevel
            DatabaseService,
        )
        from services.quality_service import (  # pylint: disable=import-outside-toplevel
            get_content_quality_service,
        )

        logger.info("[%s] STAGE 3: QA Review", request.request_id)

        database_service = DatabaseService()
        quality_service = get_content_quality_service(database_service=database_service)

        content = draft_post
        feedback = ""
        quality_score = 75
        max_iterations = 2
        creative_agent = None  # lazily re-instantiated only if refinement is needed

        for iteration in range(1, max_iterations + 1):
            quality_context: Dict[str, Any] = {"topic": topic}
            if writing_style_guidance:
                quality_context["writing_style_guidance"] = writing_style_guidance

            try:
                quality_result = await asyncio.wait_for(
                    quality_service.evaluate(
                        content=getattr(content, "raw_content", str(content)),
                        context=quality_context,
                    ),
                    timeout=60.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[%s] QA evaluation timed out after 60s on iteration %d -- "
                    "skipping QA, proceeding with current draft",
                    request.request_id,
                    iteration,
                )
                break

            approval_bool = quality_result.passing
            feedback = quality_result.feedback
            quality_score = int(quality_result.overall_score)

            if constraints:
                content_text: str = getattr(
                    content, "body", getattr(content, "raw_content", str(content))
                )
                compliance = validate_constraints(
                    content_text,
                    constraints,
                    phase_name="qa",
                    word_count_target=phase_targets.get("qa"),
                )
                if not compliance.word_count_within_tolerance:
                    logger.warning(
                        "[%s] QA: Constraint violation - %s",
                        request.request_id,
                        compliance.violation_message,
                    )
                    approval_bool = False
                    feedback += " [CONSTRAINT: %s]" % compliance.violation_message

            if approval_bool:
                logger.info(
                    "[%s] QA Approved (iteration %d, score: %d/100)",
                    request.request_id,
                    iteration,
                    quality_score,
                )
                break
            elif iteration < max_iterations:
                logger.info("[%s] QA Rejected - Refining...", request.request_id)
                refine_model = self._get_model_for_phase(
                    "refine", model_selections, quality_preference
                )
                if refine_model:
                    refine_llm_client = LLMClient(model_name=refine_model)
                    creative_agent = self._get_agent_instance(
                        "creative_agent", llm_client=refine_llm_client
                    )
                if creative_agent is None:
                    creative_agent = self._get_agent_instance("creative_agent")
                content = await creative_agent.run(
                    content,
                    is_refinement=True,
                    word_count_target=phase_targets.get("creative", 300),
                    constraints=constraints,
                )

        qa_compliance = validate_constraints(
            getattr(content, "body", str(content)),
            constraints,
            phase_name="qa",
            word_count_target=phase_targets.get("qa"),
        )

        try:
            await emit_task_progress(
                task_id=task_id,
                status="RUNNING",
                progress=60,
                current_step="QA Review Complete",
                total_steps=5,
                completed_steps=3,
                message="Quality assurance review complete - content approved",
            )
        except Exception as e:
            logger.error("[_run_qa_stage] Failed to emit QA progress: %s", e, exc_info=True)

        return content, feedback, quality_score, qa_compliance

    async def _run_image_stage(
        self,
        *,
        request: Request,
        topic: str,
        task_id: str,
    ) -> Optional[str]:
        """
        Stage 4 -- Image Selection (60% -> 75%).

        Searches for a featured image.  Failures are caught and logged; the pipeline
        continues with no image rather than aborting.

        Returns:
            featured_image_url or None
        """
        logger.info("[%s] STAGE 4: Image Selection", request.request_id)
        featured_image_url: Optional[str] = None

        try:
            from services.image_service import (  # pylint: disable=import-outside-toplevel
                get_image_service,
            )

            image_service = get_image_service()
            featured_image = await image_service.search_featured_image(
                topic=topic, keywords=[]
            )
            if featured_image:
                featured_image_url = featured_image.url
                logger.info("[%s] Featured image selected", request.request_id)
        except Exception as e:
            logger.error(
                "[_run_image_stage] Image selection failed: %s", e, exc_info=True
            )

        try:
            await emit_task_progress(
                task_id=task_id,
                status="RUNNING",
                progress=75,
                current_step="Image Selection Complete",
                total_steps=5,
                completed_steps=4,
                message="Featured image selected - ready for final formatting",
            )
        except Exception as e:
            logger.error(
                "[_run_image_stage] Failed to emit image progress: %s", e, exc_info=True
            )

        return featured_image_url

    async def _run_formatting_stage(
        self,
        *,
        request: Request,
        topic: str,
        content: Any,
        task_id: str,
    ) -> tuple:
        """
        Stage 5 -- Formatting (75% -> 90%).

        Runs the publishing/formatting agent to produce the final HTML/Markdown
        content and excerpt.

        Returns:
            (formatted_content, excerpt)
        """
        logger.info("[%s] STAGE 5: Formatting", request.request_id)

        publishing_agent = self._get_agent_instance("publishing_agent")
        result_post = await publishing_agent.run(content)

        formatted_content: str = getattr(result_post, "raw_content", str(content))
        excerpt: str = getattr(result_post, "meta_description", "Article about %s" % topic)

        try:
            await emit_task_progress(
                task_id=task_id,
                status="RUNNING",
                progress=90,
                current_step="Formatting Complete",
                total_steps=5,
                completed_steps=5,
                message="Content formatted and ready for publication",
            )
        except Exception as e:
            logger.error(
                "[_run_formatting_stage] Failed to emit formatting progress: %s", e, exc_info=True
            )

        return formatted_content, excerpt

    async def _handle_content_subtask(self, request: Request) -> ExecutionResult:
        """Handle individual content subtask (research, creative, QA, etc.)"""
        logger.info(
            "[%s] Handling content subtask: %s", request.request_id, request.extracted_intent
        )

        subtask_type = request.parameters.get("subtask_type", "research")
        topic = request.parameters.get("topic", request.original_text)

        # This would delegate to existing subtask routes
        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.COMPLETED,
            output="Executed %s subtask for: %s" % (subtask_type, topic),
            feedback="Subtask '%s' queued for execution" % subtask_type,
        )

    async def _handle_financial_analysis(self, request: Request) -> ExecutionResult:
        """Handle financial analysis request"""
        logger.info("[%s] Handling financial analysis", request.request_id)

        agent = self.agents.get("financial_agent")
        if not agent:
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.FAILED,
                output="Financial agent not available",
                feedback="Financial agent not available",
            )

        try:
            # Delegate to financial agent
            result = (
                await agent.analyze()
                if asyncio.iscoroutinefunction(agent.analyze)
                else agent.analyze()
            )
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.COMPLETED,
                output=result,
                feedback="Financial analysis complete",
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f"[_handle_financial_analysis] Financial analysis failed: {e}", exc_info=True)
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.FAILED,
                output=None,
                feedback=str(e),
            )

    async def _handle_compliance_check(self, request: Request) -> ExecutionResult:
        """Handle compliance check request"""
        logger.info("[%s] Handling compliance check", request.request_id)

        agent = self.agents.get("compliance_agent")
        if not agent:
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.FAILED,
                output="Compliance agent not available",
                feedback="Compliance agent not available",
            )

        try:
            result = (
                await agent.audit() if asyncio.iscoroutinefunction(agent.audit) else agent.audit()
            )
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.COMPLETED,
                output=result,
                feedback="Compliance audit complete",
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "[_handle_compliance_check] Compliance check failed: %s, %s",
                request.request_id,
                e,
                exc_info=True,
            )
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.FAILED,
                output=None,
                feedback=str(e),
            )

    async def _handle_task_management(self, request: Request) -> ExecutionResult:
        """Handle task management request"""
        logger.info("[%s] Handling task management", request.request_id)

        # This delegates to existing task routes
        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.COMPLETED,
            output={"task_created": True},
            task_id=str(uuid.uuid4()),
            feedback="Task created successfully",
        )

    async def _handle_information_retrieval(self, request: Request) -> ExecutionResult:
        """Handle information retrieval request"""
        logger.info("[%s] Handling information retrieval", request.request_id)

        query = request.parameters.get("query", request.original_text)

        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.COMPLETED,
            output="Retrieved information for: %s" % query,
            feedback="Query executed",
        )

    async def _handle_decision_support(self, request: Request) -> ExecutionResult:
        """Handle decision support request"""
        logger.info("[%s] Handling decision support", request.request_id)

        question = request.parameters.get("decision_question", request.original_text)

        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.COMPLETED,
            output="Decision support for: %s" % question,
            feedback="Decision analysis provided",
        )

    async def _handle_system_operation(self, request: Request) -> ExecutionResult:
        """Handle system operation request"""
        logger.info("[%s] Handling system operation", request.request_id)

        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.COMPLETED,
            output=self._get_system_info(),
            feedback="System information retrieved",
        )

    async def _handle_intervention(self, request: Request) -> ExecutionResult:
        """Handle manual intervention"""
        logger.info("[%s] Handling intervention", request.request_id)

        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.CANCELLED,
            output={"intervention": "acknowledged"},
            feedback="Intervention processed",
        )

    async def _handle_unknown(self, request: Request) -> ExecutionResult:
        """Handle unknown request type"""
        logger.info("[%s] Unknown request type, treating as content creation", request.request_id)

        return await self._handle_content_creation(request)

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def _get_system_info(self) -> Dict[str, Any]:
        """Get system information"""
        return {
            "status": "operational",
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": (
                self.successful_requests / self.total_requests * 100
                if self.total_requests > 0
                else 0
            ),
            "available_agents": list(self.agents.keys()),
        }

    async def _store_execution_result(self, result: ExecutionResult) -> None:
        """Store execution result in database"""
        try:
            if not self.database_service:
                return

            # Store in database (specific table logic depends on result type)
            logger.info("Storing execution result: %s", result.request_id)
            # Result storage is handled by TaskExecutor service after processing
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "[_store_execution_result] Failed to store execution result: %s", e, exc_info=True
            )

    def _result_to_dict(self, result: ExecutionResult) -> Dict[str, Any]:
        """Convert ExecutionResult to dictionary"""
        return {
            "request_id": result.request_id,
            "request_type": result.request_type.value,
            "status": result.status.value,
            "output": result.output,
            "task_id": result.task_id,
            "quality_score": result.quality_score,
            "passed_quality": result.passed_quality,
            "feedback": result.feedback,
            "duration_ms": result.duration_ms,
            "cost_usd": result.cost_usd,
            "metadata": result.metadata,
        }
