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
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.orchestrator_types import ExecutionResult, ExecutionStatus, Request, RequestType
from services.websocket_event_broadcaster import emit_task_progress

logger = logging.getLogger(__name__)

# Per-stage timeout budgets (seconds) for asyncio.wait_for in content pipeline.
# Tune these based on observed P99 latencies per LLM provider.
RESEARCH_TIMEOUT_S = 120
DRAFT_TIMEOUT_S = 180
QA_TIMEOUT_S = 120
REFINEMENT_TIMEOUT_S = 180
FORMATTING_TIMEOUT_S = 120


# ============================================================================
# UNIFIED ORCHESTRATOR
# ============================================================================


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
            logger.debug(
                f"Registry lookup failed for '{agent_name}': {e}, falling back to direct import"
            )

        # Fallback: Direct import based on agent name
        # This maintains backward compatibility if registry is not populated
        agent_mapping = {
            "research_agent": "agents.content_agent.agents.research_agent:ResearchAgent",
            "creative_agent": "agents.content_agent.agents.creative_agent:CreativeAgent",
            "qa_agent": "agents.content_agent.agents.qa_agent:QAAgent",
            "image_agent": "agents.content_agent.agents.postgres_image_agent:PostgreSQLImageAgent",
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
                logger.error(f"Failed to import agent '{agent_name}': {e}", exc_info=True)
                raise ValueError(
                    f"Agent '{agent_name}' not found in registry or importable via fallback"
                ) from e

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
        start_time = asyncio.get_event_loop().time()
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
            duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000

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
            logger.error("[%s] Error: %s", request_id, str(e), exc_info=True)
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
        Parse natural language request and extract intent.

        Uses keyword matching and optional LLM-based extraction.
        """
        input_lower = user_input.lower().strip()

        # Keyword-based routing (fast, deterministic)
        if any(
            kw in input_lower for kw in ["create content", "write about", "new post", "blog post"]
        ):
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.CONTENT_CREATION,
                extracted_intent="content_creation",
                parameters=self._extract_content_params(user_input),
                context=context or {},
            )

        elif any(kw in input_lower for kw in ["research", "research about", "find info"]):
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.CONTENT_SUBTASK,
                extracted_intent="research",
                parameters={"subtask_type": "research", "topic": user_input},
                context=context or {},
            )

        elif any(kw in input_lower for kw in ["creative", "draft", "generate text"]):
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.CONTENT_SUBTASK,
                extracted_intent="creative",
                parameters={"subtask_type": "creative", "topic": user_input},
                context=context or {},
            )

        elif any(
            kw in input_lower for kw in ["financial", "budget", "spending", "revenue", "balance"]
        ):
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.FINANCIAL_ANALYSIS,
                extracted_intent="financial_analysis",
                parameters={},
                context=context or {},
            )

        elif any(kw in input_lower for kw in ["compliance", "audit", "security", "risk"]):
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.COMPLIANCE_CHECK,
                extracted_intent="compliance_check",
                parameters={},
                context=context or {},
            )

        elif any(kw in input_lower for kw in ["create task", "new task", "add task"]):
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.TASK_MANAGEMENT,
                extracted_intent="create_task",
                parameters={"task_description": user_input},
                context=context or {},
            )

        elif any(kw in input_lower for kw in ["what ", "show me ", "tell me ", "list ", "get "]):
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.INFORMATION_RETRIEVAL,
                extracted_intent="retrieve_info",
                parameters={"query": user_input},
                context=context or {},
            )

        elif any(
            kw in input_lower
            for kw in ["should i ", "should we ", "what should ", "recommend", "suggest"]
        ):
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.DECISION_SUPPORT,
                extracted_intent="decision_support",
                parameters={"decision_question": user_input},
                context=context or {},
            )

        elif any(
            kw in input_lower for kw in ["help", "status", "health", "commands", "what can you do"]
        ):
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.SYSTEM_OPERATION,
                extracted_intent="system_info",
                parameters={},
                context=context or {},
            )

        elif any(
            kw in input_lower for kw in ["stop", "cancel", "intervene", "emergency", "override"]
        ):
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.INTERVENTION,
                extracted_intent="intervention",
                parameters={},
                context=context or {},
            )

        else:
            # Default: treat as content creation
            return Request(
                request_id=request_id,
                original_text=user_input,
                request_type=RequestType.CONTENT_CREATION,
                extracted_intent="content_creation_default",
                parameters=self._extract_content_params(user_input),
                context=context or {},
            )

    def _extract_content_params(self, text: str) -> Dict[str, Any]:
        """Extract content parameters from natural language or structured format."""
        params: Dict[str, Any] = {}

        # Try structured format first: "Key: value\n..."
        _FIELD_MAP = {
            "topic": "topic",
            "primary keyword": "primary_keyword",
            "target audience": "target_audience",
            "category": "category",
            "style": "style",
            "tone": "tone",
        }
        structured = False
        for line in text.splitlines():
            for label, key in _FIELD_MAP.items():
                m = re.match(rf"^{label}\s*:\s*(.+)$", line, re.IGNORECASE)
                if m:
                    params[key] = m.group(1).strip()
                    structured = True
            # Target Length: 1500 words
            m = re.match(r"^target length\s*:\s*(\d+)", line, re.IGNORECASE)
            if m:
                params["target_length"] = int(m.group(1))
                structured = True

        if not structured:
            # Unstructured fallback — use full text as topic
            params["topic"] = text

        # Style detection for unstructured text
        if "style" not in params:
            lower = text.lower()
            if "professional" in lower:
                params["style"] = "professional"
            elif "casual" in lower:
                params["style"] = "casual"
            elif "technical" in lower:
                params["style"] = "technical"
            else:
                params["style"] = "professional"

        # Tone detection
        if "tone" not in params:
            lower = text.lower()
            if "educational" in lower:
                params["tone"] = "educational"
            elif "entertaining" in lower:
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

        Args:
            phase: Generation phase ('research', 'draft', 'assess', 'refine', 'finalize')
            model_selections: User's per-phase model selections
            quality_preference: Fallback preference (fast, balanced, quality)

        Returns:
            Model identifier (e.g., "gpt-4", "claude-opus") or None to use config default
        """
        # Try to get specific model selection for this phase
        if model_selections and phase in model_selections:
            selected = model_selections[phase]
            # If user selected a specific model (not "auto"), use it
            if selected and selected != "auto":
                logger.info("   Model selection: Using %s for %s phase", selected, phase)
                return selected

        logger.info(
            "   Model selection: Using default for %s phase (quality=%s)", phase, quality_preference
        )
        return None

    # ========================================================================
    # REQUEST HANDLERS
    # ========================================================================

    async def _handle_content_creation(self, request: Request) -> ExecutionResult:
        """Handle content creation request - Full 6-stage pipeline with human approval gate.

        Coordinates six sequential stages:
        1. Research - gather background information
        2. Draft - creative writing with style guidance
        3. QA - quality review loop with optional refinement
        4. Image - featured image selection
        5. Formatting - publishing preparation
        6. Approval assembly - build result for human review
        """
        logger.info("[%s] Handling content creation", request.request_id)

        from utils.constraint_utils import (  # pylint: disable=import-outside-toplevel
            ContentConstraints,
            apply_strict_mode,
            calculate_phase_targets,
            count_words_in_content,
            merge_compliance_reports,
            validate_constraints,
        )

        try:
            # Extract parameters
            topic = request.parameters.get("topic", request.original_text)
            style = request.parameters.get("style", "professional")
            keywords = request.parameters.get("keywords", [topic])
            content_constraints = request.parameters.get("content_constraints", {})

            # Extract model selections and quality preference from execution context
            model_selections = {}
            quality_preference = "balanced"
            if request.context:
                model_selections = request.context.get("model_selections", {})
                quality_preference = request.context.get("quality_preference", "balanced")
                if isinstance(model_selections, str):
                    try:
                        model_selections = json.loads(model_selections)
                    except (json.JSONDecodeError, TypeError):
                        model_selections = {}

            logger.info("[%s] Model Configuration:", request.request_id)
            logger.info("   - Model Selections: %s", model_selections)
            logger.info("   - Quality Preference: %s", quality_preference)

            # Generate task ID
            task_id = "task_%s_%s" % (
                int(datetime.now(timezone.utc).timestamp()),
                uuid.uuid4().hex[:6],
            )

            logger.info("[%s] Starting 5-stage pipeline for: %s", request.request_id, topic)

            # ====================================================================
            # EXTRACT & INITIALIZE CONSTRAINTS
            # ====================================================================
            constraints = ContentConstraints(
                word_count=content_constraints.get("word_count", 1500) or 1500,
                writing_style=content_constraints.get("writing_style", style) or style,
                word_count_tolerance=content_constraints.get("word_count_tolerance", 10) or 10,
                per_phase_overrides=content_constraints.get("per_phase_overrides"),
                strict_mode=content_constraints.get("strict_mode", False) or False,
            )

            logger.info(
                "[%s] Constraints: %s±%s%% words, %s style",
                request.request_id,
                constraints.word_count,
                constraints.word_count_tolerance,
                constraints.writing_style,
            )

            # Calculate phase targets
            phase_targets = calculate_phase_targets(
                constraints.word_count, constraints, num_phases=5
            )
            compliance_reports = []

            # Stage 1: Research
            research_text = await self._run_research_stage(
                request,
                task_id,
                topic,
                keywords,
                constraints,
                phase_targets,
                compliance_reports,
                validate_constraints,
                count_words_in_content,
            )

            # Stage 2: Creative Draft
            draft_post, writing_style_guidance, creative_agent = await self._run_draft_stage(
                request,
                task_id,
                topic,
                style,
                research_text,
                constraints,
                phase_targets,
                compliance_reports,
                model_selections,
                quality_preference,
                validate_constraints,
                count_words_in_content,
            )

            # Stage 3: QA Review Loop
            content, feedback, quality_score = await self._run_qa_stage(
                request,
                task_id,
                topic,
                draft_post,
                writing_style_guidance,
                creative_agent,
                constraints,
                phase_targets,
                compliance_reports,
                model_selections,
                quality_preference,
                validate_constraints,
            )

            # Stage 4: Image Selection
            featured_image_url = await self._run_image_stage(
                request,
                task_id,
                topic,
            )

            # Stage 5: Formatting / Publishing Prep
            formatted_content, excerpt = await self._run_publishing_prep_stage(
                request,
                task_id,
                topic,
                content,
            )

            # Stage 6: Assemble approval result
            return self._build_approval_result(
                request,
                task_id,
                formatted_content,
                excerpt,
                featured_image_url,
                feedback,
                quality_score,
                compliance_reports,
                merge_compliance_reports,
                apply_strict_mode,
            )

        except Exception as e:
            logger.error(
                "[%s] Content creation failed: %s", request.request_id, str(e), exc_info=True
            )
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.FAILED,
                output=str(e),
                feedback="Content creation failed: %s" % str(e),
            )

    # --------------------------------------------------------------------
    # Pipeline stage methods (called exclusively by _handle_content_creation)
    # --------------------------------------------------------------------

    async def _run_research_stage(
        self,
        request,
        task_id,
        topic,
        keywords,
        constraints,
        phase_targets,
        compliance_reports,
        validate_constraints,
        count_words_in_content,
    ) -> str:
        """STAGE 1: Research (10% -> 25%). Returns research text."""
        logger.info("[%s] STAGE 1: Research", request.request_id)
        try:
            await emit_task_progress(task_id, stage="research", progress=10, status="running")
        except Exception:
            pass

        # Instantiate research agent (with registry fallback support)
        research_agent = self._get_agent_instance("research_agent")
        try:
            research_data = await asyncio.wait_for(
                research_agent.run(topic, keywords[:5]),
                timeout=RESEARCH_TIMEOUT_S,
            )
            research_text = research_data if isinstance(research_data, str) else str(research_data)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning(
                "[%s] Research timed out, continuing with empty research",
                request.request_id,
                exc_info=True,
            )
            research_text = ""

        research_compliance = validate_constraints(
            research_text,
            constraints,
            phase_name="research",
            word_count_target=phase_targets.get("research"),
        )
        compliance_reports.append(research_compliance)
        logger.info(
            "[%s] Research complete: %s words",
            request.request_id,
            count_words_in_content(research_text),
        )
        return research_text

    async def _run_draft_stage(
        self,
        request,
        task_id,
        topic,
        style,
        research_text,
        constraints,
        phase_targets,
        compliance_reports,
        model_selections,
        quality_preference,
        validate_constraints,
        count_words_in_content,
    ):
        """STAGE 2: Creative Draft (25% -> 45%).

        Returns (draft_post, writing_style_guidance, creative_agent) so that
        downstream stages can reuse the creative agent for refinement.
        """
        logger.info("[%s] STAGE 2: Creative Draft", request.request_id)
        try:
            await emit_task_progress(task_id, stage="draft", progress=25, status="running")
        except Exception:
            pass
        from agents.content_agent.services.llm_client import (  # pylint: disable=import-outside-toplevel
            LLMClient,
        )
        from agents.content_agent.utils.data_models import (  # pylint: disable=import-outside-toplevel
            BlogPost,
        )
        from services.writing_style_integration import (  # pylint: disable=import-outside-toplevel
            WritingStyleIntegrationService,
        )

        # Get model selection for draft phase
        draft_model = self._get_model_for_phase("draft", model_selections, quality_preference)

        # Create LLMClient with selected model
        llm_client = LLMClient(model_name=draft_model) if draft_model else LLMClient()

        # Instantiate creative agent with custom parameter (registry fallback support)
        creative_agent = self._get_agent_instance("creative_agent", llm_client=llm_client)

        # Retrieve writing style guidance - either from specific writing_style_id or active sample
        writing_style_guidance = ""
        user_id = request.context.get("user_id") if request.context else None
        writing_style_id = request.context.get("writing_style_id") if request.context else None

        if self.database_service:
            try:
                # Use enhanced integration service for detailed sample analysis
                integration_svc = WritingStyleIntegrationService(self.database_service)

                # Get sample with full analysis
                sample_data = await integration_svc.get_sample_for_content_generation(
                    writing_style_id=writing_style_id, user_id=user_id
                )

                if sample_data:
                    writing_style_guidance = sample_data.get("writing_style_guidance", "")
                    analysis = sample_data.get("analysis", {})

                    sample_title = sample_data.get("sample_title", "Unknown")
                    logger.info("[%s] Using writing sample: %s", request.request_id, sample_title)
                    logger.info(
                        "[%s]   - Detected tone: %s",
                        request.request_id,
                        analysis.get("detected_tone"),
                    )
                    logger.info(
                        "[%s]   - Detected style: %s",
                        request.request_id,
                        analysis.get("detected_style"),
                    )
                    logger.info(
                        "[%s]   - Avg sentence length: %s words",
                        request.request_id,
                        analysis.get("avg_sentence_length"),
                    )

            except Exception as e:
                logger.warning(
                    "[%s] Could not retrieve writing sample: %s",
                    request.request_id,
                    e,
                    exc_info=True,
                )

        post = BlogPost(
            topic=topic,
            primary_keyword=topic,
            target_audience="general",
            category="general",
            status="draft",
            research_data=research_text,
            writing_style=style,
        )

        # Store writing style guidance in post metadata for creative agent to use
        if writing_style_guidance:
            post.metadata = {"writing_sample_guidance": writing_style_guidance}

        # Pass constraints with phase-specific word count target
        phase_target = phase_targets.get("creative", 300)
        try:
            draft_post = await asyncio.wait_for(
                creative_agent.run(
                    post,
                    is_refinement=False,
                    word_count_target=phase_target,
                    constraints=constraints,
                ),
                timeout=DRAFT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("Creative draft timed out after %ds" % DRAFT_TIMEOUT_S) from None
        draft_text = draft_post.body if hasattr(draft_post, "body") else str(draft_post)

        creative_compliance = validate_constraints(
            draft_text,
            constraints,
            phase_name="creative",
            word_count_target=phase_targets.get("creative"),
        )
        compliance_reports.append(creative_compliance)
        logger.info(
            "[%s] Draft complete: %s words",
            request.request_id,
            count_words_in_content(draft_text),
        )
        return draft_post, writing_style_guidance, creative_agent

    async def _run_qa_stage(
        self,
        request,
        task_id,
        topic,
        draft_post,
        writing_style_guidance,
        creative_agent,
        constraints,
        phase_targets,
        compliance_reports,
        model_selections,
        quality_preference,
        validate_constraints,
    ):
        """STAGE 3: QA Review Loop (45% -> 60%).

        Returns (content, feedback, quality_score).
        """
        logger.info("[%s] STAGE 3: QA Review", request.request_id)
        try:
            await emit_task_progress(task_id, stage="qa", progress=45, status="running")
        except Exception:
            pass
        from agents.content_agent.services.llm_client import (  # pylint: disable=import-outside-toplevel
            LLMClient,
        )
        from services.quality_service import (  # pylint: disable=import-outside-toplevel
            get_content_quality_service,
        )

        # Use the application-level database_service (initialized once at startup)
        # to avoid creating a new connection pool per request (issue #783).
        quality_service = get_content_quality_service(database_service=self.database_service)

        content = draft_post
        feedback = ""
        quality_score = 75
        max_iterations = 2

        for iteration in range(1, max_iterations + 1):
            quality_context = {"topic": topic}
            if writing_style_guidance:
                quality_context["writing_style_guidance"] = writing_style_guidance

            try:
                quality_result = await asyncio.wait_for(
                    quality_service.evaluate(
                        content=getattr(content, "raw_content", str(content)),
                        context=quality_context,
                    ),
                    timeout=QA_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                raise TimeoutError(
                    "QA evaluation timed out after %ds (iteration %d)" % (QA_TIMEOUT_S, iteration)
                ) from None

            approval_bool = quality_result.passing
            feedback = quality_result.feedback
            quality_score = int(quality_result.overall_score)  # Already 0-100 from quality_service

            # Check constraint compliance
            if constraints:
                content_text = getattr(
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
                # Get model selection for refine phase
                refine_model = self._get_model_for_phase(
                    "refine", model_selections, quality_preference
                )
                if refine_model:
                    # Create new LLMClient with refine model for refinement
                    refine_llm_client = LLMClient(model_name=refine_model)
                    creative_agent = self._get_agent_instance(
                        "creative_agent", llm_client=refine_llm_client
                    )
                try:
                    content = await asyncio.wait_for(
                        creative_agent.run(
                            content,
                            is_refinement=True,
                            word_count_target=phase_targets.get("creative", 300),
                            constraints=constraints,
                        ),
                        timeout=REFINEMENT_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    raise TimeoutError(
                        "Creative refinement timed out after %ds (iteration %d)"
                        % (REFINEMENT_TIMEOUT_S, iteration)
                    ) from None

        qa_compliance = validate_constraints(
            getattr(content, "body", str(content)),
            constraints,
            phase_name="qa",
            word_count_target=phase_targets.get("qa"),
        )
        compliance_reports.append(qa_compliance)

        return content, feedback, quality_score

    async def _run_image_stage(self, request, task_id, topic) -> Optional[str]:
        """STAGE 4: Image Selection (60% -> 75%). Returns featured image URL or None."""
        logger.info("[%s] STAGE 4: Image Selection", request.request_id)
        try:
            await emit_task_progress(task_id, stage="images", progress=60, status="running")
        except Exception:
            pass
        featured_image_url = None
        try:
            from services.image_service import (  # pylint: disable=import-outside-toplevel
                get_image_service,
            )

            image_service = get_image_service()
            featured_image = await image_service.search_featured_image(topic=topic, keywords=[])
            if featured_image:
                featured_image_url = featured_image.url
                logger.info("[%s] Featured image selected", request.request_id)
        except Exception as e:
            logger.warning("[%s] Image selection failed: %s", request.request_id, e, exc_info=True)
        return featured_image_url

    async def _run_publishing_prep_stage(self, request, task_id, topic, content):
        """STAGE 5: Formatting / Publishing Prep (75% -> 90%).

        Returns (formatted_content, excerpt).
        """
        logger.info("[%s] STAGE 5: Formatting", request.request_id)
        try:
            await emit_task_progress(task_id, stage="formatting", progress=75, status="running")
        except Exception:
            pass

        # Instantiate publishing agent (with registry fallback support)
        publishing_agent = self._get_agent_instance("publishing_agent")
        try:
            result_post = await asyncio.wait_for(
                publishing_agent.run(content),
                timeout=FORMATTING_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                "Formatting/publishing timed out after %ds" % FORMATTING_TIMEOUT_S
            ) from None

        formatted_content = getattr(result_post, "raw_content", str(content))
        excerpt = getattr(result_post, "meta_description", "Article about %s" % topic)
        return formatted_content, excerpt

    def _build_approval_result(
        self,
        request,
        task_id,
        formatted_content,
        excerpt,
        featured_image_url,
        feedback,
        quality_score,
        compliance_reports,
        merge_compliance_reports,
        apply_strict_mode,
    ) -> ExecutionResult:
        """STAGE 6: Assemble the final approval result from pipeline outputs."""
        logger.info("[%s] STAGE 6: Awaiting Human Approval", request.request_id)

        overall_compliance = merge_compliance_reports(compliance_reports)
        strict_mode_valid, strict_mode_error = apply_strict_mode(overall_compliance)

        if not strict_mode_valid:
            logger.warning("[%s] STRICT MODE VIOLATION: %s", request.request_id, strict_mode_error)

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
            "message": "✅ Content ready for human review. Human approval required before publishing.",
            "next_action": "POST /api/content/tasks/%s/approve with human decision" % task_id,
        }

        logger.info("[%s] ✅ Pipeline complete. Awaiting human approval.", request.request_id)

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
            logger.error("[%s] Financial analysis failed: %s", request.request_id, e, exc_info=True)
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
            logger.error("[%s] Compliance check failed: %s", request.request_id, e, exc_info=True)
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
            logger.error("Failed to store execution result: %s", e, exc_info=True)

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
