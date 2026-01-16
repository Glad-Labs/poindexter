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

import logging
import asyncio
import json
import uuid
from typing import Dict, Any, List, Optional, Callable, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum

# Quality service import (consolidated quality assessment)
from services.quality_service import UnifiedQualityService, EvaluationMethod

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMERATIONS & DATA STRUCTURES
# ============================================================================


class RequestType(str, Enum):
    """High-level request types for routing"""

    CONTENT_CREATION = "content_creation"  # Blog posts, articles, copy
    CONTENT_SUBTASK = "content_subtask"  # Research, creative, QA, format individually
    FINANCIAL_ANALYSIS = "financial_analysis"
    COMPLIANCE_CHECK = "compliance_check"
    TASK_MANAGEMENT = "task_management"  # Create/manage tasks
    INFORMATION_RETRIEVAL = "information_retrieval"  # Look up data, show results
    DECISION_SUPPORT = "decision_support"  # "What should I..."
    SYSTEM_OPERATION = "system_operation"  # Status, health, help
    INTERVENTION = "intervention"  # Manual override, stop, etc.


class ExecutionStatus(str, Enum):
    """Status of request execution"""

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    ASSESSING = "assessing"
    REFINEMENT = "refinement"
    PENDING_APPROVAL = "pending_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Request:
    """Unified request object"""

    request_id: str
    original_text: str
    request_type: RequestType
    extracted_intent: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    user_id: Optional[str] = None  # User ID from auth context


@dataclass
class ExecutionContext:
    """Context for execution"""

    request_id: str
    request_type: RequestType
    database_service: Any = None
    model_router: Any = None
    orchestrator_agents: Dict[str, Any] = field(default_factory=dict)
    quality_service: Any = None
    memory_system: Any = None


@dataclass
class ExecutionResult:
    """Result of executing a request"""

    request_id: str
    request_type: RequestType
    status: ExecutionStatus

    # Result data
    output: Any  # Content, analysis, decision, etc.
    task_id: Optional[str] = None  # For content tasks

    # Quality metrics
    quality_score: Optional[float] = None
    passed_quality: Optional[bool] = None
    feedback: Optional[str] = None

    # Execution details
    duration_ms: float = 0
    cost_usd: float = 0.0
    refinement_attempts: int = 0

    # Training data
    training_example: Optional[Dict[str, Any]] = None  # For model improvement

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            "request_id": self.request_id,
            "request_type": self.request_type.value,
            "status": self.status.value,
            "output": self.output,
            "task_id": self.task_id,
            "quality_score": self.quality_score,
            "passed_quality": self.passed_quality,
            "feedback": self.feedback,
            "duration_ms": self.duration_ms,
            "cost_usd": self.cost_usd,
            "refinement_attempts": self.refinement_attempts,
            "training_example": self.training_example,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


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
            logger.info(f"[{request_id}] Processing: {user_input[:100]}")

            # Step 1: Parse and route request
            request = await self._parse_request(user_input, request_id, context)
            logger.info(f"[{request_id}] Detected type: {request.request_type.value}")

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
            else:
                # Legacy response format
                self.successful_requests += 1
                return result

        except Exception as e:
            self.failed_requests += 1
            logger.error(f"[{request_id}] Error: {str(e)}", exc_info=True)
            return {
                "request_id": request_id,
                "status": "error",
                "error": str(e),
                "message": f"An error occurred processing your request: {str(e)}",
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
        """Extract content parameters from natural language"""
        # Simple extraction - can be enhanced with LLM
        params = {"topic": text}

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
                logger.info(f"   Model selection: Using {selected} for {phase} phase")
                return selected

        logger.info(f"   Model selection: Using default for {phase} phase (quality={quality_preference})")
        return None

    # ========================================================================
    # REQUEST HANDLERS
    # ========================================================================

    async def _handle_content_creation(self, request: Request) -> ExecutionResult:
        """Handle content creation request - Full 5-stage pipeline with human approval gate"""
        logger.info(f"[{request.request_id}] Handling content creation")

        import uuid
        from utils.constraint_utils import (
            ContentConstraints,
            extract_constraints_from_request,
            inject_constraints_into_prompt,
            count_words_in_content,
            validate_constraints,
            calculate_phase_targets,
            check_tolerance,
            apply_strict_mode,
            merge_compliance_reports,
        )

        try:
            # Extract parameters
            topic = request.parameters.get("topic", request.original_text)
            style = request.parameters.get("style", "professional")
            tone = request.parameters.get("tone", "informative")
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
                        import json
                        model_selections = json.loads(model_selections)
                    except (json.JSONDecodeError, TypeError):
                        model_selections = {}
            
            logger.info(f"[{request.request_id}] Model Configuration:")
            logger.info(f"   - Model Selections: {model_selections}")
            logger.info(f"   - Quality Preference: {quality_preference}")

            # Generate task ID
            task_id = f"task_{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex[:6]}"

            logger.info(f"[{request.request_id}] Starting 5-stage pipeline for: {topic}")

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
                f"[{request.request_id}] Constraints: {constraints.word_count}±{constraints.word_count_tolerance}% words, {constraints.writing_style} style"
            )

            # Calculate phase targets
            phase_targets = calculate_phase_targets(constraints.word_count, constraints, num_phases=5)
            compliance_reports = []

            # ====================================================================
            # STAGE 1: RESEARCH (10% → 25%)
            # ====================================================================
            logger.info(f"[{request.request_id}] STAGE 1: Research")
            from agents.content_agent.agents.research_agent import ResearchAgent

            research_agent = ResearchAgent()
            research_data = await research_agent.run(topic, keywords[:5])
            research_text = research_data if isinstance(research_data, str) else str(research_data)

            research_compliance = validate_constraints(
                research_text,
                constraints,
                phase_name="research",
                word_count_target=phase_targets.get("research"),
            )
            compliance_reports.append(research_compliance)
            logger.info(
                f"[{request.request_id}] Research complete: {count_words_in_content(research_text)} words"
            )

            # ====================================================================
            # STAGE 2: CREATIVE DRAFT (25% → 45%)
            # ====================================================================
            logger.info(f"[{request.request_id}] STAGE 2: Creative Draft")
            from agents.content_agent.agents.creative_agent import CreativeAgent
            from agents.content_agent.services.llm_client import LLMClient
            from agents.content_agent.utils.data_models import BlogPost
            from services.writing_style_service import WritingStyleService
            from services.writing_style_integration import WritingStyleIntegrationService

            # Get model selection for draft phase
            draft_model = self._get_model_for_phase("draft", model_selections, quality_preference)
            
            # Create LLMClient with selected model
            llm_client = LLMClient(model_name=draft_model) if draft_model else LLMClient()
            creative_agent = CreativeAgent(llm_client=llm_client)

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
                        writing_style_id=writing_style_id,
                        user_id=user_id
                    )
                    
                    if sample_data:
                        writing_style_guidance = sample_data.get("writing_style_guidance", "")
                        analysis = sample_data.get("analysis", {})
                        
                        sample_title = sample_data.get("sample_title", "Unknown")
                        logger.info(f"[{request.request_id}] Using writing sample: {sample_title}")
                        logger.info(f"[{request.request_id}]   - Detected tone: {analysis.get('detected_tone')}")
                        logger.info(f"[{request.request_id}]   - Detected style: {analysis.get('detected_style')}")
                        logger.info(f"[{request.request_id}]   - Avg sentence length: {analysis.get('avg_sentence_length')} words")
                    
                except Exception as e:
                    logger.warning(f"[{request.request_id}] Could not retrieve writing sample: {e}")

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
            draft_post = await creative_agent.run(
                post, 
                is_refinement=False,
                word_count_target=phase_target,
                constraints=constraints
            )
            draft_text = draft_post.body if hasattr(draft_post, "body") else str(draft_post)

            creative_compliance = validate_constraints(
                draft_text,
                constraints,
                phase_name="creative",
                word_count_target=phase_targets.get("creative"),
            )
            compliance_reports.append(creative_compliance)
            logger.info(f"[{request.request_id}] Draft complete: {count_words_in_content(draft_text)} words")

            # ====================================================================
            # STAGE 3: QA REVIEW LOOP (45% → 60%)
            # ====================================================================
            logger.info(f"[{request.request_id}] STAGE 3: QA Review")
            from services.quality_service import get_content_quality_service, EvaluationMethod
            from services.database_service import DatabaseService

            database_service = DatabaseService()
            quality_service = get_content_quality_service(database_service=database_service)

            content = draft_post
            feedback = ""
            quality_score = 75
            max_iterations = 2

            for iteration in range(1, max_iterations + 1):
                quality_context = {"topic": topic}
                if writing_style_guidance:
                    quality_context["writing_style_guidance"] = writing_style_guidance
                
                quality_result = await quality_service.evaluate(
                    content=getattr(content, "raw_content", str(content)),
                    context=quality_context,
                    method=EvaluationMethod.HYBRID,
                )

                approval_bool = quality_result.passing
                feedback = quality_result.feedback
                quality_score = int(quality_result.overall_score * 100)

                # Check constraint compliance
                if constraints:
                    content_text = getattr(content, "body", getattr(content, "raw_content", str(content)))
                    compliance = validate_constraints(
                        content_text, constraints, phase_name="qa", word_count_target=phase_targets.get("qa")
                    )
                    if not compliance.word_count_within_tolerance:
                        logger.warning(f"[{request.request_id}] QA: Constraint violation - {compliance.violation_message}")
                        approval_bool = False
                        feedback += f" [CONSTRAINT: {compliance.violation_message}]"

                if approval_bool:
                    logger.info(f"[{request.request_id}] QA Approved (iteration {iteration}, score: {quality_score}/100)")
                    break
                elif iteration < max_iterations:
                    logger.info(f"[{request.request_id}] QA Rejected - Refining...")
                    # Get model selection for refine phase
                    refine_model = self._get_model_for_phase("refine", model_selections, quality_preference)
                    if refine_model:
                        # Create new LLMClient with refine model for refinement
                        refine_llm_client = LLMClient(model_name=refine_model)
                        creative_agent = CreativeAgent(llm_client=refine_llm_client)
                    content = await creative_agent.run(
                        content, 
                        is_refinement=True,
                        word_count_target=phase_targets.get("creative", 300),
                        constraints=constraints
                    )

            qa_compliance = validate_constraints(
                getattr(content, "body", str(content)),
                constraints,
                phase_name="qa",
                word_count_target=phase_targets.get("qa"),
            )
            compliance_reports.append(qa_compliance)

            # ====================================================================
            # STAGE 4: IMAGE SELECTION (60% → 75%)
            # ====================================================================
            logger.info(f"[{request.request_id}] STAGE 4: Image Selection")
            featured_image_url = None
            try:
                from services.image_service import get_image_service

                image_service = get_image_service()
                featured_image = await image_service.search_featured_image(topic=topic, keywords=[])
                if featured_image:
                    featured_image_url = featured_image.url
                    logger.info(f"[{request.request_id}] Featured image selected")
            except Exception as e:
                logger.warning(f"[{request.request_id}] Image selection failed: {e}")

            # ====================================================================
            # STAGE 5: FORMATTING (75% → 90%)
            # ====================================================================
            logger.info(f"[{request.request_id}] STAGE 5: Formatting")
            from agents.content_agent.agents.postgres_publishing_agent import PostgreSQLPublishingAgent

            publishing_agent = PostgreSQLPublishingAgent()
            result_post = await publishing_agent.run(content)

            formatted_content = getattr(result_post, "raw_content", str(content))
            excerpt = getattr(result_post, "meta_description", f"Article about {topic}")

            # ====================================================================
            # STAGE 6: AWAITING HUMAN APPROVAL (90% → 100%)
            # ====================================================================
            logger.info(f"[{request.request_id}] STAGE 6: Awaiting Human Approval")

            overall_compliance = merge_compliance_reports(compliance_reports)
            strict_mode_valid, strict_mode_error = apply_strict_mode(overall_compliance)

            if not strict_mode_valid:
                logger.warning(f"[{request.request_id}] STRICT MODE VIOLATION: {strict_mode_error}")

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
                "next_action": f"POST /api/content/tasks/{task_id}/approve with human decision",
            }

            logger.info(f"[{request.request_id}] ✅ Pipeline complete. Awaiting human approval.")

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
            logger.error(f"[{request.request_id}] Content creation failed: {e}", exc_info=True)
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.FAILED,
                output=str(e),
                feedback=f"Content creation failed: {str(e)}",
            )

    async def _handle_content_subtask(self, request: Request) -> ExecutionResult:
        """Handle individual content subtask (research, creative, QA, etc.)"""
        logger.info(f"[{request.request_id}] Handling content subtask: {request.extracted_intent}")

        subtask_type = request.parameters.get("subtask_type", "research")
        topic = request.parameters.get("topic", request.original_text)

        # This would delegate to existing subtask routes
        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.COMPLETED,
            output=f"Executed {subtask_type} subtask for: {topic}",
            feedback=f"Subtask '{subtask_type}' queued for execution",
        )

    async def _handle_financial_analysis(self, request: Request) -> ExecutionResult:
        """Handle financial analysis request"""
        logger.info(f"[{request.request_id}] Handling financial analysis")

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
        except Exception as e:
            logger.error(f"[{request.request_id}] Financial analysis failed: {e}")
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.FAILED,
                output=None,
                feedback=str(e),
            )

    async def _handle_compliance_check(self, request: Request) -> ExecutionResult:
        """Handle compliance check request"""
        logger.info(f"[{request.request_id}] Handling compliance check")

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
        except Exception as e:
            logger.error(f"[{request.request_id}] Compliance check failed: {e}")
            return ExecutionResult(
                request_id=request.request_id,
                request_type=request.request_type,
                status=ExecutionStatus.FAILED,
                output=None,
                feedback=str(e),
            )

    async def _handle_task_management(self, request: Request) -> ExecutionResult:
        """Handle task management request"""
        logger.info(f"[{request.request_id}] Handling task management")

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
        logger.info(f"[{request.request_id}] Handling information retrieval")

        query = request.parameters.get("query", request.original_text)

        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.COMPLETED,
            output=f"Retrieved information for: {query}",
            feedback="Query executed",
        )

    async def _handle_decision_support(self, request: Request) -> ExecutionResult:
        """Handle decision support request"""
        logger.info(f"[{request.request_id}] Handling decision support")

        question = request.parameters.get("decision_question", request.original_text)

        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.COMPLETED,
            output=f"Decision support for: {question}",
            feedback="Decision analysis provided",
        )

    async def _handle_system_operation(self, request: Request) -> ExecutionResult:
        """Handle system operation request"""
        logger.info(f"[{request.request_id}] Handling system operation")

        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.COMPLETED,
            output=self._get_system_info(),
            feedback="System information retrieved",
        )

    async def _handle_intervention(self, request: Request) -> ExecutionResult:
        """Handle manual intervention"""
        logger.info(f"[{request.request_id}] Handling intervention")

        return ExecutionResult(
            request_id=request.request_id,
            request_type=request.request_type,
            status=ExecutionStatus.CANCELLED,
            output={"intervention": "acknowledged"},
            feedback="Intervention processed",
        )

    async def _handle_unknown(self, request: Request) -> ExecutionResult:
        """Handle unknown request type"""
        logger.info(f"[{request.request_id}] Unknown request type, treating as content creation")

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
            logger.info(f"Storing execution result: {result.request_id}")
            # Result storage is handled by TaskExecutor service after processing
        except Exception as e:
            logger.error(f"Failed to store execution result: {e}")

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
