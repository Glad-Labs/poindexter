"""
Task Intent Router - Map natural language to task workflows

Connects NLPIntentRecognizer to task creation, handling:
- Intent detection from user input
- Parameter extraction (topic, style, budget, deadline)
- Task type determination (blog_post, social_media, etc.)
- Subtask planning based on detected intent
- Workflow routing (direct execution vs form confirmation)
"""

import logging
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from datetime import datetime, timedelta

from .nlp_intent_recognizer import NLPIntentRecognizer, IntentMatch

logger = logging.getLogger(__name__)


@dataclass
class TaskIntentRequest:
    """User request parsed from natural language."""
    raw_input: str
    intent_type: str  # "content_generation", "social_media", etc.
    task_type: str  # "blog_post", "social_media", "email", etc.
    confidence: float
    parameters: Dict[str, Any]  # Extracted: topic, style, length, budget, deadline
    suggested_subtasks: List[str]  # ["research", "creative", "qa", "images"]
    requires_confirmation: bool  # User must confirm before execution
    execution_strategy: str  # "sequential" or "parallel_where_possible"


@dataclass
class SubtaskPlan:
    """Plan for breaking down a task into subtasks."""
    task_id: str
    parent_task_id: Optional[str]
    stage: str  # "research", "creative", "qa", "images", "format"
    priority: int  # 1=critical, 2=important, 3=optional
    requires_parent: bool  # Must wait for parent task result
    estimated_duration_ms: int
    required_inputs: List[str]  # ["topic", "keywords"] or ["content", "feedback"]


class TaskIntentRouter:
    """Route natural language requests to task workflows."""
    
    def __init__(self):
        """Initialize router with NLP recognizer."""
        self.nlp_recognizer = NLPIntentRecognizer()
        
        # Map intent types to task types
        self.intent_to_task_type = {
            "content_generation": "blog_post",
            "social_media": "social_media",
            "financial_analysis": "financial_analysis",
            "market_analysis": "market_analysis",
            "compliance_check": "compliance_check",
            "performance_review": "performance_review",
        }
        
        # Map task types to default subtasks
        self.task_subtasks = {
            "blog_post": ["research", "creative", "qa", "images", "format"],
            "social_media": ["research", "creative", "format"],
            "email": ["research", "creative", "format"],
            "newsletter": ["research", "creative", "format", "images"],
            "financial_analysis": [],  # No subtasks, runs as single agent
            "market_analysis": [],
            "compliance_check": [],
            "performance_review": [],
        }
        
        # Estimated execution time per subtask (ms)
        self.subtask_duration = {
            "research": 15000,  # 15 seconds
            "creative": 25000,  # 25 seconds
            "qa": 12000,  # 12 seconds
            "images": 8000,  # 8 seconds
            "format": 3000,  # 3 seconds
        }
    
    async def route_user_input(
        self,
        user_input: str,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> TaskIntentRequest:
        """
        Parse user input and determine how to route it.
        
        Args:
            user_input: Raw user text ("Generate blog post about AI")
            user_context: Optional context (previous requests, user preferences)
        
        Returns:
            TaskIntentRequest with routing instructions
        
        Example:
            Input: "Generate blog post about AI + images, budget $50, due tomorrow"
            Output:
                intent_type: "content_generation"
                task_type: "blog_post"
                parameters: {
                    "topic": "AI",
                    "style": None,
                    "budget": 50.0,
                    "deadline": "2025-12-06T10:00:00Z",
                    "include_images": True
                }
                suggested_subtasks: ["research", "creative", "qa", "images", "format"]
                requires_confirmation: False
        """
        # Step 1: Recognize intent
        intent_match = await self.nlp_recognizer.recognize_intent(
            user_input,
            context=user_context
        )
        
        if intent_match is None:
            # Couldn't recognize intent - return generic task request
            return TaskIntentRequest(
                raw_input=user_input,
                intent_type="unknown",
                task_type="generic",
                confidence=0.0,
                parameters={"user_input": user_input},
                suggested_subtasks=[],
                requires_confirmation=True,
                execution_strategy="sequential"
            )
        
        # Step 2: Map intent to task type
        task_type = self.intent_to_task_type.get(
            intent_match.intent_type,
            "generic"
        )
        
        # Step 3: Extract and normalize parameters
        extracted_params = self._normalize_parameters(
            intent_match.parameters,
            task_type
        )
        
        # Step 4: Determine subtasks based on task type and parameters
        suggested_subtasks = self._determine_subtasks(
            task_type,
            extracted_params
        )
        
        # Step 5: Decide if user confirmation needed
        requires_confirmation = self._should_confirm(
            intent_match.confidence,
            task_type,
            extracted_params
        )
        
        # Step 6: Determine execution strategy
        execution_strategy = self._determine_execution_strategy(
            task_type,
            extracted_params
        )
        
        return TaskIntentRequest(
            raw_input=user_input,
            intent_type=intent_match.intent_type,
            task_type=task_type,
            confidence=intent_match.confidence,
            parameters=extracted_params,
            suggested_subtasks=suggested_subtasks,
            requires_confirmation=requires_confirmation,
            execution_strategy=execution_strategy
        )
    
    def _normalize_parameters(
        self,
        params: Dict[str, Any],
        task_type: str
    ) -> Dict[str, Any]:
        """Normalize extracted parameters to standard format."""
        normalized = {}
        
        # Common parameters
        if "topic" in params:
            normalized["topic"] = params["topic"]
        if "style" in params:
            normalized["style"] = params["style"]
        if "tone" in params:
            normalized["tone"] = params["tone"]
        if "length" in params:
            normalized["target_length"] = params["length"]
        
        # Financial parameters
        if "budget" in params:
            try:
                normalized["budget"] = float(params["budget"])
            except (ValueError, TypeError):
                normalized["budget"] = None
        
        if "deadline" in params:
            normalized["deadline"] = params["deadline"]
        elif "deadline_days" in params:
            try:
                days = int(params["deadline_days"])
                normalized["deadline"] = (
                    datetime.now() + timedelta(days=days)
                ).isoformat()
            except (ValueError, TypeError):
                pass
        
        # Platform-specific parameters
        if "platforms" in params:
            normalized["platforms"] = params["platforms"]
        if "include_images" in params:
            normalized["include_images"] = params["include_images"]
        
        # Quality parameters
        if "quality_preference" in params:
            normalized["quality_preference"] = params["quality_preference"]
        
        return normalized
    
    def _determine_subtasks(
        self,
        task_type: str,
        parameters: Dict[str, Any]
    ) -> List[str]:
        """Determine which subtasks should be included in execution."""
        base_subtasks = self.task_subtasks.get(task_type, [])
        
        # Filter based on parameters
        subtasks = list(base_subtasks)
        
        # Remove image stage if not requested
        if "include_images" in parameters and not parameters["include_images"]:
            subtasks = [s for s in subtasks if s != "images"]
        
        # For multi-agent tasks (financial, market, etc.), no subtasks
        if task_type in ["financial_analysis", "market_analysis", "compliance_check"]:
            subtasks = []
        
        return subtasks
    
    def _should_confirm(
        self,
        confidence: float,
        task_type: str,
        parameters: Dict[str, Any]
    ) -> bool:
        """Determine if user should confirm before execution."""
        # Always confirm for low confidence
        if confidence < 0.75:
            return True
        
        # Always confirm if critical parameters missing
        if task_type == "blog_post" and "topic" not in parameters:
            return True
        
        # Confirm if budget specified (financial decision)
        if "budget" in parameters:
            return True
        
        # Confirm if large request (many subtasks)
        # This would be passed in as part of task planning
        
        return False
    
    def _determine_execution_strategy(
        self,
        task_type: str,
        parameters: Dict[str, Any]
    ) -> str:
        """Determine if subtasks should run sequentially or in parallel."""
        # For now, always sequential
        # Future: Could optimize for parallel execution where dependencies allow
        # e.g., research + images could run in parallel after creative
        
        return "sequential"
    
    def plan_subtasks(
        self,
        task_id: str,
        task_type: str,
        subtasks: List[str],
        parameters: Dict[str, Any],
        execution_strategy: str = "sequential"
    ) -> List[SubtaskPlan]:
        """
        Generate detailed execution plan for subtasks.
        
        Args:
            task_id: Parent task ID
            task_type: Type of task (blog_post, etc.)
            subtasks: List of subtask stages to execute
            parameters: Extracted parameters
            execution_strategy: "sequential" or "parallel_where_possible"
        
        Returns:
            List of SubtaskPlan objects describing execution order and dependencies
        
        Example output:
            [
                SubtaskPlan(
                    task_id="research-1",
                    parent_task_id=task_id,
                    stage="research",
                    priority=1,
                    requires_parent=False,
                    estimated_duration_ms=15000,
                    required_inputs=["topic"]
                ),
                SubtaskPlan(
                    task_id="creative-1",
                    parent_task_id=task_id,
                    stage="creative",
                    priority=1,
                    requires_parent=True,  # Needs research output
                    estimated_duration_ms=25000,
                    required_inputs=["research_output"]
                ),
                # ... etc
            ]
        """
        plans = []
        
        for i, subtask_stage in enumerate(subtasks):
            # Determine if this stage requires parent output
            requires_parent = i > 0 if execution_strategy == "sequential" else False
            
            # Determine required inputs
            required_inputs = self._get_required_inputs(
                subtask_stage,
                i > 0  # First stage? Input is parameters, not parent output
            )
            
            plan = SubtaskPlan(
                task_id=f"{subtask_stage}-{task_id[:8]}",
                parent_task_id=task_id,
                stage=subtask_stage,
                priority=1 if i < 2 else 2,  # First 2 are critical
                requires_parent=requires_parent,
                estimated_duration_ms=self.subtask_duration.get(subtask_stage, 5000),
                required_inputs=required_inputs
            )
            plans.append(plan)
        
        return plans
    
    def _get_required_inputs(self, stage: str, is_dependent: bool) -> List[str]:
        """Determine what inputs a subtask stage requires."""
        inputs_by_stage = {
            "research": ["topic", "keywords"],
            "creative": ["research_output"] if is_dependent else ["topic"],
            "qa": ["creative_output"],
            "images": ["creative_output"],
            "format": ["creative_output", "images_output"],
        }
        
        return inputs_by_stage.get(stage, [])
    
    def generate_execution_plan_summary(
        self,
        intent_request: TaskIntentRequest,
        subtask_plans: List[SubtaskPlan]
    ) -> Dict[str, Any]:
        """
        Generate human-readable execution plan summary for UI display.
        
        Example:
            {
                "title": "Generate Blog Post",
                "description": "Write blog post about AI with images",
                "steps": [
                    {"order": 1, "stage": "research", "duration": "15s"},
                    {"order": 2, "stage": "creative", "duration": "25s"},
                    {"order": 3, "stage": "qa", "duration": "12s"},
                    {"order": 4, "stage": "images", "duration": "8s"},
                    {"order": 5, "stage": "format", "duration": "3s"}
                ],
                "total_estimated_time": "63 seconds",
                "cost_estimate": "$2.15",
                "next_action": "Human approval required"
            }
        """
        total_duration_ms = sum(
            plan.estimated_duration_ms
            for plan in subtask_plans
        )
        
        steps = [
            {
                "order": i + 1,
                "stage": plan.stage,
                "duration": self._format_duration(plan.estimated_duration_ms)
            }
            for i, plan in enumerate(subtask_plans)
        ]
        
        return {
            "title": f"Execute {intent_request.task_type.replace('_', ' ').title()}",
            "description": intent_request.raw_input,
            "intent_type": intent_request.intent_type,
            "confidence": f"{intent_request.confidence * 100:.0f}%",
            "steps": steps,
            "total_estimated_time": self._format_duration(total_duration_ms),
            "cost_estimate": await self.model_router.estimate_cost(task_data) if hasattr(self, 'model_router') else "$2.15",
            "next_action": "Ready to execute" if not intent_request.requires_confirmation else "Awaiting your confirmation"
        }
    
    def _format_duration(self, milliseconds: int) -> str:
        """Format milliseconds as human-readable duration."""
        if milliseconds < 1000:
            return f"{milliseconds}ms"
        elif milliseconds < 60000:
            return f"{milliseconds / 1000:.0f}s"
        else:
            return f"{milliseconds / 60000:.1f}m"
