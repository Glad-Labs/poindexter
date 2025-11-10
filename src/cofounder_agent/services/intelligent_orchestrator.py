"""
Intelligent Orchestrator Agent - Enhanced Multi-Agent Coordination System

This is the brain of Glad Labs. It:
1. Understands natural language business requests
2. Reasons about optimal workflows to accomplish goals
3. Dynamically discovers and orchestrates tools/agents via MCP
4. Implements quality feedback loops with automatic refinement
5. Learns from every execution via persistent memory system
6. Accumulates training data for fine-tuning proprietary LLMs
7. Supports financial & marketing metrics for strategic planning

Architecture:
- Modular agent/tool discovery (via MCP)
- Pluggable quality assessment
- Persistent memory with semantic search
- Learning pattern accumulation
- Training dataset generation
- Proprietary LLM integration hooks

This allows each organization to train a unique orchestrator LLM that
reflects their specific business logic, tone, and decision-making patterns.
"""

import asyncio
import logging
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable, Tuple, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
import hashlib

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMERATIONS & DATA STRUCTURES
# ============================================================================

class ExecutionPhase(str, Enum):
    """Phases of intelligent orchestration"""
    PLANNING = "planning"
    TOOL_DISCOVERY = "tool_discovery"
    DELEGATION = "delegation"
    EXECUTION = "execution"
    QUALITY_CHECK = "quality_check"
    REFINEMENT = "refinement"
    FORMATTING = "formatting"
    APPROVAL = "approval"
    LEARNING = "learning"


class WorkflowSource(str, Enum):
    """Where a workflow came from"""
    USER_REQUEST = "user_request"
    LEARNED_PATTERN = "learned_pattern"
    MCP_DISCOVERY = "mcp_discovery"
    PREVIOUS_SUCCESS = "previous_success"
    HYBRID = "hybrid"


class DecisionOutcome(str, Enum):
    """Outcome of a decision or workflow execution"""
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    REQUIRES_HUMAN_INTERVENTION = "requires_human_intervention"


@dataclass
class ToolSpecification:
    """Specification of a tool that can be used in workflows"""
    tool_id: str
    name: str
    description: str
    category: str  # "content", "analysis", "publishing", "research", etc.
    input_schema: Dict[str, Any]  # JSON schema
    output_schema: Dict[str, Any]
    estimated_cost: float  # USD
    estimated_duration: float  # seconds
    success_rate: float  # 0-1
    requires_approval: bool  # Needs human sign-off
    source: str  # "mcp", "api", "builtin"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowStep:
    """Single step in an execution workflow"""
    step_id: str
    tool_id: str
    description: str
    input_data: Dict[str, Any]
    dependencies: List[str] = field(default_factory=list)  # IDs of prerequisite steps
    retry_count: int = 0
    max_retries: int = 3
    quality_threshold: float = 0.75
    timeout_seconds: int = 300
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    execution_duration: Optional[float] = None


@dataclass
class ExecutionPlan:
    """Complete execution plan for a request"""
    plan_id: str
    request_id: str
    user_request: str
    intent: str
    requirements: List[str]
    workflow_steps: List[WorkflowStep]
    workflow_source: WorkflowSource
    estimated_duration: float
    estimated_cost: float
    priority: str  # "low", "medium", "high", "critical"
    business_metrics: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityAssessment:
    """Assessment of result quality"""
    score: float  # 0-1
    passed: bool
    issues: List[str]
    suggestions: List[str]
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    retry_needed: bool = False


@dataclass
class ExecutionResult:
    """Result of executing a workflow"""
    result_id: str
    plan_id: str
    request_id: str
    status: DecisionOutcome
    outputs: Dict[str, Any]  # Step ID → step result
    quality_assessment: QualityAssessment
    total_cost: float
    total_duration: float
    refinement_attempts: int = 0
    final_formatting: Optional[Dict[str, Any]] = None
    execution_trace: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class TrainingExample:
    """Example extracted for fine-tuning proprietary LLM"""
    example_id: str
    request: str
    reasoning_trace: str  # How orchestrator thought through it
    executed_plan: ExecutionPlan
    result: ExecutionResult
    business_metrics_before: Dict[str, Any]
    business_metrics_after: Dict[str, Any]
    improvement: float  # -1 to 1
    feedback_label: str  # "excellent", "good", "acceptable", "poor"
    created_at: datetime = field(default_factory=datetime.now)


# ============================================================================
# CORE ORCHESTRATOR CLASS
# ============================================================================

class IntelligentOrchestrator:
    """
    Main orchestrator agent coordinating multi-agent workflows.
    
    Responsibilities:
    1. Parse natural language requests into executable plans
    2. Discover available tools/agents (via MCP or registry)
    3. Reason about optimal execution sequences
    4. Orchestrate parallel/sequential execution
    5. Monitor quality and implement feedback loops
    6. Accumulate learning for fine-tuning
    7. Support business metrics analysis
    """

    def __init__(
        self,
        llm_client,
        database_service,
        memory_system,
        mcp_orchestrator=None,
        tool_registry: Optional[Dict[str, ToolSpecification]] = None,
    ):
        """
        Initialize the intelligent orchestrator.

        Args:
            llm_client: LLM client for reasoning and planning
            database_service: Database for persistence
            memory_system: Memory system for learning
            mcp_orchestrator: MCP orchestrator for tool discovery
            tool_registry: Pre-configured tools (can be augmented by MCP)
        """
        self.llm_client = llm_client
        self.database_service = database_service
        self.memory_system = memory_system
        self.mcp_orchestrator = mcp_orchestrator
        
        self.tools: Dict[str, ToolSpecification] = tool_registry or {}
        self.execution_history: List[ExecutionResult] = []
        self.learned_workflows: Dict[str, ExecutionPlan] = {}
        self.training_examples: List[TrainingExample] = []
        
        # Hooks for proprietary LLM training
        self.custom_orchestrator_llm: Optional[Any] = None
        self.use_custom_llm_for_planning: bool = False

    # ========================================================================
    # MAIN ENTRY POINT
    # ========================================================================

    async def process_request(
        self,
        user_request: str,
        user_id: str,
        business_metrics: Optional[Dict[str, Any]] = None,
        preferences: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        Process a natural language business request end-to-end.

        This is the main entry point orchestrating the entire workflow:
        1. Planning - understand request & create execution plan
        2. Tool Discovery - find available tools via MCP
        3. Delegation - assign tools to workflow steps
        4. Execution - run workflow with parallel support
        5. Quality Check - assess result quality
        6. Refinement - retry on quality failures
        7. Formatting - prepare for user approval
        8. Learning - accumulate training data

        Args:
            user_request: Natural language instruction
            user_id: User making the request
            business_metrics: Current business KPIs (revenue, traffic, etc.)
            preferences: User preferences (tone, channels, etc.)

        Returns:
            ExecutionResult with status, outputs, quality assessment
        """
        request_id = self._generate_id("req")
        execution_context = {
            "request_id": request_id,
            "user_id": user_id,
            "start_time": datetime.now().isoformat(),
            "phases": {}
        }

        try:
            # PHASE 1: Planning
            logger.info(f"[{request_id}] PHASE 1: Planning")
            plan = await self._create_execution_plan(
                user_request,
                business_metrics,
                preferences
            )
            execution_context["phases"]["planning"] = {
                "status": "complete",
                "plan_id": plan.plan_id
            }

            # PHASE 2: Tool Discovery (via MCP)
            logger.info(f"[{request_id}] PHASE 2: Tool Discovery")
            tools = await self._discover_tools(plan)
            execution_context["phases"]["tool_discovery"] = {
                "status": "complete",
                "tools_found": len(tools)
            }

            # PHASE 3: Execution
            logger.info(f"[{request_id}] PHASE 3: Execution")
            outputs = await self._execute_workflow(plan, tools)
            execution_context["phases"]["execution"] = {
                "status": "complete",
                "steps_completed": len(outputs)
            }

            # PHASE 4: Quality Check
            logger.info(f"[{request_id}] PHASE 4: Quality Check")
            quality = await self._assess_quality(outputs, plan)
            execution_context["phases"]["quality_check"] = {
                "status": "complete",
                "score": quality.score,
                "passed": quality.passed
            }

            # PHASE 5: Refinement (if needed)
            refinement_attempts = 0
            if not quality.passed:
                logger.warning(f"[{request_id}] Quality check failed. Starting refinement...")
                refinement_attempts, outputs = await self._refine_results(
                    outputs, quality, plan
                )
                execution_context["phases"]["refinement"] = {
                    "status": "complete",
                    "attempts": refinement_attempts
                }

            # PHASE 6: Formatting for approval
            logger.info(f"[{request_id}] PHASE 6: Formatting")
            formatted = await self._format_for_approval(outputs, plan, preferences)
            execution_context["phases"]["formatting"] = {"status": "complete"}

            # PHASE 7: Learning
            logger.info(f"[{request_id}] PHASE 7: Learning")
            await self._accumulate_learning(
                request_id, user_request, plan, outputs, quality, business_metrics
            )
            execution_context["phases"]["learning"] = {"status": "complete"}

            # Create result
            result = ExecutionResult(
                result_id=self._generate_id("res"),
                plan_id=plan.plan_id,
                request_id=request_id,
                status=DecisionOutcome.SUCCESS if quality.passed else DecisionOutcome.PARTIAL_SUCCESS,
                outputs=outputs,
                quality_assessment=quality,
                total_cost=plan.estimated_cost,
                total_duration=(datetime.now() - plan.created_at).total_seconds(),
                refinement_attempts=refinement_attempts,
                final_formatting=formatted,
                execution_trace=self._build_execution_trace(execution_context)
            )

            logger.info(f"[{request_id}] Orchestration complete: {result.status.value}")
            return result

        except Exception as e:
            logger.error(f"[{request_id}] Orchestration failed: {e}", exc_info=True)
            return ExecutionResult(
                result_id=self._generate_id("res"),
                plan_id="unknown",
                request_id=request_id,
                status=DecisionOutcome.FAILURE,
                outputs={},
                quality_assessment=QualityAssessment(
                    score=0.0,
                    passed=False,
                    issues=[str(e)],
                    suggestions=[]
                ),
                total_cost=0.0,
                total_duration=(datetime.now() - datetime.now()).total_seconds(),
                execution_trace=[{"error": str(e)}]
            )

    # ========================================================================
    # PHASE 1: PLANNING
    # ========================================================================

    async def _create_execution_plan(
        self,
        user_request: str,
        business_metrics: Optional[Dict[str, Any]],
        preferences: Optional[Dict[str, Any]]
    ) -> ExecutionPlan:
        """
        Create an optimal execution plan for the request.

        Uses LLM to:
        1. Parse intent (what user really wants)
        2. Extract requirements (constraints, preferences)
        3. Identify business objectives (if metrics provided)
        4. Search for similar past successes in memory
        5. Generate workflow steps with tool assignments
        """
        plan_id = self._generate_id("plan")

        # First, check if we can use custom orchestrator LLM
        if self.use_custom_llm_for_planning and self.custom_orchestrator_llm:
            logger.info("Using custom orchestrator LLM for planning")
            planning_prompt = await self._build_planning_prompt(
                user_request, business_metrics, preferences
            )
            plan_json = await self.custom_orchestrator_llm.generate_text(
                planning_prompt,
                temperature=0.4,  # Deterministic
                max_tokens=3000
            )
        else:
            # Use standard LLM
            planning_prompt = await self._build_planning_prompt(
                user_request, business_metrics, preferences
            )
            plan_json = await self.llm_client.generate_text(
                planning_prompt,
                temperature=0.4,
                max_tokens=3000
            )

        try:
            plan_data = json.loads(plan_json)
        except json.JSONDecodeError:
            plan_data = self._create_fallback_plan(user_request)

        # Extract workflow steps
        workflow_steps = [
            WorkflowStep(
                step_id=f"step_{i}",
                tool_id=step.get("tool_id", "unknown"),
                description=step.get("description", ""),
                input_data=step.get("input_data", {}),
                dependencies=step.get("depends_on", []),
                quality_threshold=step.get("quality_threshold", 0.75),
                max_retries=step.get("max_retries", 3)
            )
            for i, step in enumerate(plan_data.get("workflow_steps", []))
        ]

        # Determine workflow source (was it learned before?)
        workflow_source = WorkflowSource.USER_REQUEST
        if await self._is_similar_to_learned_workflow(user_request):
            workflow_source = WorkflowSource.HYBRID

        return ExecutionPlan(
            plan_id=plan_id,
            request_id=self._generate_id("req"),
            user_request=user_request,
            intent=plan_data.get("intent", ""),
            requirements=plan_data.get("requirements", []),
            workflow_steps=workflow_steps,
            workflow_source=workflow_source,
            estimated_duration=plan_data.get("estimated_duration_seconds", 600),
            estimated_cost=plan_data.get("estimated_cost_usd", 0),
            priority=plan_data.get("priority", "medium"),
            business_metrics=business_metrics or {},
            metadata=plan_data.get("metadata", {})
        )

    async def _build_planning_prompt(
        self,
        user_request: str,
        business_metrics: Optional[Dict[str, Any]],
        preferences: Optional[Dict[str, Any]]
    ) -> str:
        """Build the LLM prompt for planning"""
        available_tools_desc = self._format_tools_for_prompt(self.tools.values())

        prompt = f"""
You are an expert business workflow orchestrator. Analyze this request and create an optimal execution plan.

USER REQUEST:
{user_request}

BUSINESS CONTEXT:
{json.dumps(business_metrics or {}, indent=2)}

USER PREFERENCES:
{json.dumps(preferences or {}, indent=2)}

AVAILABLE TOOLS & AGENTS:
{available_tools_desc}

YOUR TASK:
1. Identify the core intent (what user really wants)
2. Extract specific requirements and constraints
3. Search for similar patterns in your training (learned patterns)
4. Design optimal workflow respecting dependencies
5. Assign tools to workflow steps
6. Estimate duration and cost

RESPONSE FORMAT (JSON):
{{
  "intent": "what the user actually wants to achieve",
  "requirements": [
    "requirement 1",
    "requirement 2"
  ],
  "workflow_steps": [
    {{
      "step_id": "step_0",
      "tool_id": "tool_name",
      "description": "what this step does",
      "input_data": {{"key": "value"}},
      "depends_on": [],
      "quality_threshold": 0.8,
      "max_retries": 3
    }}
  ],
  "estimated_duration_seconds": 600,
  "estimated_cost_usd": 2.50,
  "priority": "medium",
  "reasoning": "explanation of why this workflow"
}}
"""
        return prompt

    def _format_tools_for_prompt(self, tools) -> str:
        """Format available tools for LLM prompt"""
        tool_descriptions = []
        for tool in tools:
            tool_descriptions.append(
                f"- {tool.name} (ID: {tool.tool_id}): {tool.description} "
                f"[Cost: ${tool.estimated_cost}, Duration: {tool.estimated_duration}s]"
            )
        return "\n".join(tool_descriptions)

    async def _is_similar_to_learned_workflow(self, request: str) -> bool:
        """Check if similar workflow exists in memory"""
        if not self.memory_system:
            return False
        
        # Use memory system to search for similar past requests
        similar = await self.memory_system.semantic_search(
            request,
            limit=1,
            memory_type="workflow_pattern"
        )
        return len(similar) > 0

    def _create_fallback_plan(self, user_request: str) -> Dict[str, Any]:
        """Fallback plan if LLM planning fails"""
        return {
            "intent": user_request[:100],
            "requirements": ["Execute request"],
            "workflow_steps": [
                {
                    "step_id": "step_0",
                    "tool_id": "generic_executor",
                    "description": f"Execute: {user_request}",
                    "input_data": {"request": user_request},
                    "depends_on": [],
                    "quality_threshold": 0.7,
                    "max_retries": 2
                }
            ],
            "estimated_duration_seconds": 300,
            "estimated_cost_usd": 1.0,
            "priority": "medium"
        }

    # ========================================================================
    # PHASE 2: TOOL DISCOVERY (MCP Integration)
    # ========================================================================

    async def _discover_tools(self, plan: ExecutionPlan) -> Dict[str, ToolSpecification]:
        """
        Discover available tools for the workflow.

        Uses MCP to dynamically discover tools and merges with existing registry.
        """
        discovered_tools: Dict[str, ToolSpecification] = {}

        # Start with registered tools
        discovered_tools.update(self.tools)

        # If MCP available, discover additional tools
        if self.mcp_orchestrator:
            try:
                mcp_tools = await self.mcp_orchestrator.discover_tools()
                for tool_id, tool_spec in mcp_tools.items():
                    if tool_id not in discovered_tools:
                        discovered_tools[tool_id] = tool_spec
                logger.info(f"Discovered {len(mcp_tools)} tools via MCP")
            except Exception as e:
                logger.warning(f"MCP tool discovery failed: {e}")

        # Filter to tools needed for this plan
        required_tools: Dict[str, ToolSpecification] = {}
        for step in plan.workflow_steps:
            if step.tool_id in discovered_tools:
                tool = discovered_tools[step.tool_id]
                if tool is not None:
                    required_tools[step.tool_id] = tool

        return required_tools

    # ========================================================================
    # PHASE 3: EXECUTION
    # ========================================================================

    async def _execute_workflow(
        self,
        plan: ExecutionPlan,
        tools: Dict[str, ToolSpecification]
    ) -> Dict[str, Any]:
        """
        Execute the workflow, respecting dependencies.

        Uses asyncio.gather() for parallel execution of independent steps.
        """
        results: Dict[str, Any] = {}
        completed_steps: Set[str] = set()
        step_map = {step.step_id: step for step in plan.workflow_steps}

        while len(completed_steps) < len(plan.workflow_steps):
            # Find steps ready to execute
            ready_steps = [
                step for step in plan.workflow_steps
                if step.step_id not in completed_steps 
                and all(dep in completed_steps for dep in step.dependencies)
            ]

            if not ready_steps:
                raise RuntimeError(
                    f"Circular or broken dependencies in workflow. "
                    f"Completed: {completed_steps}, Pending: {set(step_map.keys()) - completed_steps}"
                )

            # Execute ready steps in parallel
            tasks = [
                self._execute_step(step, results, tools)
                for step in ready_steps
            ]
            step_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for step, result in zip(ready_steps, step_results):
                if isinstance(result, Exception):
                    results[step.step_id] = {
                        "error": str(result),
                        "status": "failed"
                    }
                else:
                    results[step.step_id] = result

                completed_steps.add(step.step_id)

        return results

    async def _execute_step(
        self,
        step: WorkflowStep,
        results: Dict[str, Any],
        tools: Dict[str, ToolSpecification]
    ) -> Dict[str, Any]:
        """Execute a single workflow step"""
        logger.info(f"Executing step {step.step_id}: {step.description}")

        # Prepare input by merging original data and dependency results
        step_input = {
            **step.input_data,
            "dependencies": {
                dep_id: results.get(dep_id) for dep_id in step.dependencies
            }
        }

        # Get tool
        tool = tools.get(step.tool_id)
        if not tool:
            raise ValueError(f"Tool not found: {step.tool_id}")

        # Execute via MCP if available, otherwise use direct execution
        if self.mcp_orchestrator and tool.source == "mcp":
            try:
                output = await self.mcp_orchestrator.call_tool(
                    step.tool_id,
                    step_input,
                    timeout=step.timeout_seconds
                )
            except Exception as e:
                logger.error(f"MCP tool execution failed: {e}")
                output = {"error": str(e)}
        else:
            # Fallback to direct execution
            output = await self._execute_tool_direct(step.tool_id, step_input)

        return {
            "output": output,
            "step_id": step.step_id,
            "tool_id": step.tool_id,
            "executed_at": datetime.now().isoformat()
        }

    async def _execute_tool_direct(self, tool_id: str, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Direct tool execution (fallback if MCP not available)"""
        # This would route to appropriate agent/tool handler
        logger.warning(f"Direct execution for {tool_id}: MCP not available")
        return {"output": "Placeholder result"}

    # ========================================================================
    # PHASE 4: QUALITY CHECK
    # ========================================================================

    async def _assess_quality(
        self,
        outputs: Dict[str, Any],
        plan: ExecutionPlan
    ) -> QualityAssessment:
        """Assess overall quality of execution results"""
        quality_prompt = f"""
Assess the quality of this workflow execution.

PLAN:
{json.dumps(asdict(plan), indent=2, default=str)}

OUTPUTS:
{json.dumps(outputs, indent=2, default=str)}

ASSESSMENT CRITERIA (rate each 0-1):
1. Accuracy: Are results factually correct?
2. Completeness: Did we address all requirements?
3. Coherence: Do outputs flow logically?
4. Business_Value: Does it achieve business goals?
5. Safety: Are there compliance/legal issues?

RESPONSE FORMAT (JSON):
{{
  "overall_score": 0.85,
  "passed": true,
  "dimension_scores": {{
    "accuracy": 0.90,
    "completeness": 0.85,
    "coherence": 0.80,
    "business_value": 0.85,
    "safety": 0.95
  }},
  "issues": ["issue 1"],
  "suggestions": ["how to fix"]
}}
"""

        quality_json = await self.llm_client.generate_text(
            quality_prompt,
            temperature=0.2,
            max_tokens=1000
        )

        try:
            assessment_data = json.loads(quality_json)
        except json.JSONDecodeError:
            assessment_data = {"overall_score": 0.5, "passed": False, "issues": []}

        return QualityAssessment(
            score=assessment_data.get("overall_score", 0.5),
            passed=assessment_data.get("passed", False),
            issues=assessment_data.get("issues", []),
            suggestions=assessment_data.get("suggestions", []),
            dimension_scores=assessment_data.get("dimension_scores", {}),
            retry_needed=assessment_data.get("overall_score", 0.5) < 0.75
        )

    # ========================================================================
    # PHASE 5: REFINEMENT
    # ========================================================================

    async def _refine_results(
        self,
        outputs: Dict[str, Any],
        quality: QualityAssessment,
        plan: ExecutionPlan,
        max_refinements: int = 2
    ) -> Tuple[int, Dict[str, Any]]:
        """Refine results based on quality feedback"""
        refinement_attempts = 0

        while not quality.passed and refinement_attempts < max_refinements:
            refinement_attempts += 1
            logger.info(f"Refinement attempt {refinement_attempts}/{max_refinements}")

            # Identify steps to refine
            refinement_prompt = f"""
Based on these quality issues, which workflow steps should be re-executed?

ISSUES: {json.dumps(quality.issues)}
SUGGESTIONS: {json.dumps(quality.suggestions)}

WORKFLOW STEPS:
{json.dumps([asdict(s) for s in plan.workflow_steps], indent=2, default=str)}

Respond with JSON listing step IDs to retry.
"""

            refinement_json = await self.llm_client.generate_text(
                refinement_prompt,
                temperature=0.3,
                max_tokens=500
            )

            try:
                refinement_data = json.loads(refinement_json)
                steps_to_refine = refinement_data.get("steps_to_retry", [])
            except json.JSONDecodeError:
                steps_to_refine = list(outputs.keys())

            # Re-execute identified steps
            for step_id in steps_to_refine:
                if step_id in outputs:
                    outputs[step_id]["refinement_feedback"] = quality.suggestions

            # Re-assess quality
            quality = await self._assess_quality(outputs, plan)

        return refinement_attempts, outputs

    # ========================================================================
    # PHASE 6: FORMATTING
    # ========================================================================

    async def _format_for_approval(
        self,
        outputs: Dict[str, Any],
        plan: ExecutionPlan,
        preferences: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Format results for user approval and publishing"""
        formatting_prompt = f"""
Format this workflow output into a publication-ready result.

REQUEST: {plan.user_request}
REQUIREMENTS: {json.dumps(plan.requirements)}
OUTPUTS: {json.dumps(outputs, indent=2, default=str)}

Format as JSON with:
- main_content (title, body, summary)
- metadata (SEO, social, etc.)
- channel_variants (blog, linkedin, twitter, email)
- supporting_materials (images, citations, CTAs)
- approval_checklist (verification steps)
"""

        formatted_json = await self.llm_client.generate_text(
            formatting_prompt,
            temperature=0.1,
            max_tokens=4000
        )

        try:
            formatted = json.loads(formatted_json)
        except json.JSONDecodeError:
            formatted = {"outputs": outputs, "ready_for_approval": False}

        formatted["formatted_at"] = datetime.now().isoformat()
        return formatted

    # ========================================================================
    # PHASE 7: LEARNING & TRAINING DATA ACCUMULATION
    # ========================================================================

    async def _accumulate_learning(
        self,
        request_id: str,
        user_request: str,
        plan: ExecutionPlan,
        outputs: Dict[str, Any],
        quality: QualityAssessment,
        business_metrics_before: Optional[Dict[str, Any]] = None
    ):
        """
        Accumulate learning from execution for:
        1. Memory system (patterns, preferences)
        2. Training dataset (for fine-tuning)
        3. Workflow templates (learned patterns)
        """
        
        # Store in memory system
        if self.memory_system:
            await self.memory_system.store_memory(
                content=f"Workflow: {user_request}",
                memory_type="workflow_pattern",
                importance=5 if quality.score > 0.8 else 3,
                confidence=quality.score,
                metadata={"request_id": request_id, "outputs": outputs}
            )

        # Generate training example
        reasoning_trace = self._build_reasoning_trace(plan, outputs, quality)
        training_example = TrainingExample(
            example_id=self._generate_id("train"),
            request=user_request,
            reasoning_trace=reasoning_trace,
            executed_plan=plan,
            result=ExecutionResult(
                result_id=self._generate_id("res"),
                plan_id=plan.plan_id,
                request_id=request_id,
                status=DecisionOutcome.SUCCESS if quality.passed else DecisionOutcome.PARTIAL_SUCCESS,
                outputs=outputs,
                quality_assessment=quality,
                total_cost=plan.estimated_cost,
                total_duration=0,
            ),
            business_metrics_before=business_metrics_before or {},
            business_metrics_after={},  # Would be updated later
            improvement=quality.score,
            feedback_label=self._label_quality(quality.score),
            created_at=datetime.now()
        )

        # Store training example
        self.training_examples.append(training_example)
        
        # Persist to database if available
        if self.database_service:
            try:
                await self.database_service.store_training_example(training_example)
            except Exception as e:
                logger.error(f"Failed to store training example: {e}")

        logger.info(f"Learning accumulated: {training_example.example_id}")

    def _build_reasoning_trace(
        self,
        plan: ExecutionPlan,
        outputs: Dict[str, Any],
        quality: QualityAssessment
    ) -> str:
        """Build a trace of the reasoning for training data"""
        trace = f"""
REQUEST ANALYSIS:
- Intent: {plan.intent}
- Requirements: {json.dumps(plan.requirements)}

WORKFLOW DESIGNED:
- Steps: {len(plan.workflow_steps)}
- Estimated Duration: {plan.estimated_duration}s
- Estimated Cost: ${plan.estimated_cost}

EXECUTION RESULTS:
- Quality Score: {quality.score}
- Passed QA: {quality.passed}
- Issues: {json.dumps(quality.issues)}

QUALITY DIMENSIONS:
{json.dumps(quality.dimension_scores, indent=2)}

REFINEMENT NEEDED: {quality.retry_needed}
"""
        return trace

    def _label_quality(self, score: float) -> str:
        """Label quality score"""
        if score >= 0.9:
            return "excellent"
        elif score >= 0.8:
            return "good"
        elif score >= 0.7:
            return "acceptable"
        else:
            return "poor"

    # ========================================================================
    # TRAINING & PROPRIETARY LLM SUPPORT
    # ========================================================================

    def set_custom_orchestrator_llm(self, llm_client) -> None:
        """Set custom proprietary orchestrator LLM"""
        self.custom_orchestrator_llm = llm_client
        self.use_custom_llm_for_planning = True
        logger.info("Custom orchestrator LLM configured")

    def disable_custom_orchestrator_llm(self) -> None:
        """Fall back to standard LLM"""
        self.use_custom_llm_for_planning = False
        logger.info("Custom orchestrator LLM disabled")

    def get_training_dataset(self) -> List[TrainingExample]:
        """Get accumulated training examples"""
        return self.training_examples

    def export_training_dataset(self, format: str = "jsonl") -> str:
        """Export training data for fine-tuning"""
        if format == "jsonl":
            lines = []
            for example in self.training_examples:
                lines.append(json.dumps({
                    "example_id": example.example_id,
                    "request": example.request,
                    "reasoning": example.reasoning_trace,
                    "outcome": example.result.status.value,
                    "quality_score": example.result.quality_assessment.score,
                    "improvement": example.improvement,
                    "feedback": example.feedback_label
                }))
            return "\n".join(lines)
        elif format == "csv":
            import csv
            import io
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=[
                "example_id", "request", "outcome", "quality_score", "improvement", "feedback"
            ])
            writer.writeheader()
            for example in self.training_examples:
                writer.writerow({
                    "example_id": example.example_id,
                    "request": example.request,
                    "outcome": example.result.status.value,
                    "quality_score": example.result.quality_assessment.score,
                    "improvement": example.improvement,
                    "feedback": example.feedback_label
                })
            return output.getvalue()
        else:
            raise ValueError(f"Unsupported format: {format}")

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def _generate_id(self, prefix: str) -> str:
        """Generate unique ID"""
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

    def _build_execution_trace(self, execution_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Build execution trace for audit"""
        return [
            {
                "phase": phase,
                "status": data.get("status", "unknown"),
                "timestamp": datetime.now().isoformat()
            }
            for phase, data in execution_context.get("phases", {}).items()
        ]

    def register_tool(self, tool: ToolSpecification) -> None:
        """Register a new tool"""
        self.tools[tool.tool_id] = tool
        logger.info(f"Tool registered: {tool.name} ({tool.tool_id})")

    def deregister_tool(self, tool_id: str) -> None:
        """Deregister a tool"""
        if tool_id in self.tools:
            del self.tools[tool_id]
            logger.info(f"Tool deregistered: {tool_id}")

    def list_tools(self) -> List[ToolSpecification]:
        """List all available tools"""
        return list(self.tools.values())

    async def get_execution_history(self, limit: int = 100) -> List[ExecutionResult]:
        """Get execution history"""
        return self.execution_history[-limit:]
