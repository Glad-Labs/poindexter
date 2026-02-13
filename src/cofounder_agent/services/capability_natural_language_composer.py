"""
Capability Natural Language Composer - AI-powered task composition.

Analyzes natural language requests and automatically generates capability task chains.

Features:
- Parse natural language requests into capability sequences
- LLM-powered task composition (uses cost-optimized model router)
- Validates generated tasks against available capabilities
- Returns suggested tasks with execution option
- Supports both auto-execution and review modes
"""

import logging
import json
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from services.capability_registry import get_registry, CapabilityTaskDefinition, CapabilityStep
from services.model_router import ModelRouter
from services.capability_task_executor import execute_capability_task

logger = logging.getLogger(__name__)


@dataclass
class TaskCompositionResult:
    """Result of natural language task composition."""
    success: bool
    task_definition: Optional[CapabilityTaskDefinition] = None
    suggested_task: Optional[Dict[str, Any]] = None  # For user review
    explanation: str = ""
    error: Optional[str] = None
    confidence: float = 0.0  # 0-1 confidence in the composition
    execution_id: Optional[str] = None  # If auto-executed


class CapabilityNaturalLanguageComposer:
    """
    Analyzes natural language requests and generates capability task chains.
    
    Uses LLM to understand user intent and map it to available capabilities.
    """
    
    def __init__(self, model_router: Optional[ModelRouter] = None):
        """
        Initialize the composer.
        
        Args:
            model_router: Optional ModelRouter for LLM selection.
                         If not provided, will be instantiated.
        """
        self.registry = get_registry()
        self.model_router = model_router or ModelRouter()
        
    def _get_registry_context(self) -> str:
        """
        Generate registry context for the LLM prompt.
        
        Returns information about all available capabilities.
        """
        capabilities = self.registry.list_capabilities()
        
        context = "# Available Capabilities\n\n"
        for name, metadata in capabilities.items():
            capability = self.registry.get(name)
            context += f"## {name}\n"
            context += f"- Description: {metadata.description}\n"
            context += f"- Tags: {', '.join(metadata.tags)}\n"
            context += f"- Cost Tier: {metadata.cost_tier}\n"
            
            if capability and capability.input_schema:
                inputs = ", ".join([p.name for p in capability.input_schema.parameters])
                context += f"- Inputs: {inputs}\n"
            
            context += "\n"
        
        return context
    
    def _create_composition_prompt(self, request: str) -> str:
        """
        Create a prompt for the LLM to generate task composition.
        
        Args:
            request: Natural language request from user
            
        Returns:
            Formatted prompt for LLM
        """
        registry_context = self._get_registry_context()
        
        prompt = f"""You are an AI assistant that helps users compose automated tasks using available capabilities.

{registry_context}

User Request: "{request}"

Based on the user's request, generate a JSON task definition that chains capabilities together to accomplish the goal.

IMPORTANT RULES:
1. Only use capabilities from the "Available Capabilities" list above
2. Follow the exact format shown below
3. Output ONLY the JSON, no additional text
4. If the request cannot be fulfilled with available capabilities, output: {{"error": "reason"}}
5. Chain capabilities in logical order (output of one feeds into next when possible)
6. Use variable references like $output_key to pass data between steps

JSON FORMAT:
{{
  "name": "descriptive task name",
  "description": "what this task accomplishes",
  "steps": [
    {{
      "capability_name": "capability_name_from_list",
      "inputs": {{"param1": "value1", "param2": "$previous_output_key"}},
      "output_key": "meaningful_name_for_this_step_output",
      "order": 0
    }}
  ]
}}

Now generate the task JSON for the user's request:"""
        
        return prompt
    
    async def compose_from_request(
        self,
        request: str,
        auto_execute: bool = False,
        owner_id: Optional[str] = None,
    ) -> TaskCompositionResult:
        """
        Compose a capability task from a natural language request.
        
        Args:
            request: Natural language request (e.g., "Write a blog post about AI")
            auto_execute: Whether to execute the task immediately after creation
            owner_id: Owner/user ID for task persistence
            
        Returns:
            TaskCompositionResult with suggested or executed task
        """
        try:
            logger.info(f"[Composer] Processing request: {request[:100]}...")
            
            # Create prompt for LLM
            prompt = self._create_composition_prompt(request)
            
            # Get LLM response (use cheap tier for this analysis)
            logger.info("[Composer] Requesting LLM composition...")
            # Note: In production, call actual LLM here
            # For now, we'll use a mock implementation
            response_text = await self._call_llm(prompt)
            
            logger.info(f"[Composer] LLM response: {response_text[:200]}...")
            
            # Parse LLM response
            task_dict = self._parse_llm_response(response_text)
            
            if "error" in task_dict:
                return TaskCompositionResult(
                    success=False,
                    error=task_dict["error"],
                    explanation=f"Could not compose task: {task_dict['error']}"
                )
            
            # Validate task
            validation_result = self._validate_task_definition(task_dict)
            if not validation_result["valid"]:
                return TaskCompositionResult(
                    success=False,
                    error=validation_result["error"],
                    explanation=validation_result["error"]
                )
            
            # Convert to CapabilityTaskDefinition
            task_definition = self._dict_to_task_definition(task_dict)
            
            logger.info(f"[Composer] Task composed: {task_definition.name} with {len(task_definition.steps)} steps")
            
            # Execute if requested
            execution_id = None
            if auto_execute:
                logger.info("[Composer] Auto-executing task...")
                try:
                    result = await execute_capability_task(task_definition)
                    execution_id = result.execution_id
                    logger.info(f"[Composer] Task executed: {execution_id}")
                except Exception as e:
                    logger.error(f"[Composer] Execution failed: {e}")
                    return TaskCompositionResult(
                        success=False,
                        suggested_task=task_dict,
                        error=f"Task composition succeeded but execution failed: {str(e)}",
                        explanation=f"Task was generated but failed during execution. Error: {str(e)}"
                    )
            
            return TaskCompositionResult(
                success=True,
                task_definition=task_definition,
                suggested_task=task_dict,  # Always include for reference
                explanation=f"Composed {len(task_definition.steps)}-step task: {' → '.join([s.capability_name for s in task_definition.steps])}",
                confidence=validation_result.get("confidence", 0.8),
                execution_id=execution_id,
            )
            
        except Exception as e:
            logger.error(f"[Composer] Composition failed: {e}", exc_info=True)
            return TaskCompositionResult(
                success=False,
                error=str(e),
                explanation=f"Failed to compose task: {str(e)}"
            )
    
    async def _call_llm(self, prompt: str) -> str:
        """
        Call LLM with the composition prompt.
        
        Args:
            prompt: Prompt for LLM
            
        Returns:
            LLM response text
        """
        # In production, this would use:
        # response = await self.model_router.generate(prompt, cost_tier="cheap")
        # return response.text
        
        # For now, mock implementation
        logger.warning("[Composer] Using mock LLM response - implement real LLM integration")
        
        # Mock response for testing
        if "blog" in prompt.lower():
            return """{
  "name": "Blog Content Creation Pipeline",
  "description": "Create a blog post with AI generation and image selection",
  "steps": [
    {
      "capability_name": "research",
      "inputs": {"topic": "AI trends and developments"},
      "output_key": "research_data",
      "order": 0
    },
    {
      "capability_name": "generate_content",
      "inputs": {"topic": "AI trends", "research": "$research_data", "style": "professional"},
      "output_key": "blog_content",
      "order": 1
    },
    {
      "capability_name": "select_images",
      "inputs": {"content": "$blog_content", "count": 3},
      "output_key": "images",
      "order": 2
    },
    {
      "capability_name": "publish",
      "inputs": {"content": "$blog_content", "images": "$images", "platform": "blog"},
      "output_key": "published_post",
      "order": 3
    }
  ]
}"""
        
        return """{
  "error": "Could not determine capabilities for this request"
}"""
    
    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """
        Parse JSON response from LLM.
        
        Args:
            response: LLM response text
            
        Returns:
            Parsed task dictionary
        """
        try:
            # Try to extract JSON from response
            # (LLM might include explanations before/after JSON)
            start_idx = response.find('{')
            end_idx = response.rfind('}') + 1
            
            if start_idx == -1 or end_idx == 0:
                return {"error": "No JSON found in LLM response"}
            
            json_str = response[start_idx:end_idx]
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"[Composer] Failed to parse LLM JSON: {e}")
            return {"error": f"Invalid JSON from LLM: {str(e)}"}
    
    def _validate_task_definition(self, task_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate a task definition against available capabilities.
        
        Args:
            task_dict: Task definition dictionary from LLM
            
        Returns:
            Validation result {"valid": bool, "error": str, "confidence": float}
        """
        errors = []
        warnings = []
        
        # Check structure
        if "name" not in task_dict:
            errors.append("Task missing 'name' field")
        if "steps" not in task_dict:
            errors.append("Task missing 'steps' field")
        elif not isinstance(task_dict["steps"], list):
            errors.append("'steps' must be a list")
        elif len(task_dict["steps"]) == 0:
            errors.append("Task must have at least one step")
        
        if errors:
            return {
                "valid": False,
                "error": "; ".join(errors),
                "confidence": 0.0
            }
        
        # Check each step
        capabilities = self.registry.list_capabilities()
        valid_capability_names = set(capabilities.keys())
        
        for i, step in enumerate(task_dict["steps"]):
            if not isinstance(step, dict):
                errors.append(f"Step {i} is not a dictionary")
                continue
            
            if "capability_name" not in step:
                errors.append(f"Step {i} missing 'capability_name'")
                continue
            
            cap_name = step["capability_name"]
            if cap_name not in valid_capability_names:
                errors.append(f"Step {i}: unknown capability '{cap_name}'")
                warnings.append(f"Available capabilities: {', '.join(sorted(valid_capability_names))}")
            
            if "output_key" not in step:
                errors.append(f"Step {i} missing 'output_key'")
            
            if "inputs" not in step:
                step["inputs"] = {}  # Allow empty inputs
        
        if errors:
            return {
                "valid": False,
                "error": "; ".join(errors),
                "confidence": 0.0
            }
        
        # Validation passed
        confidence = 1.0 if not warnings else 0.7
        
        return {
            "valid": True,
            "error": None,
            "confidence": confidence,
            "warnings": warnings
        }
    
    def _dict_to_task_definition(self, task_dict: Dict[str, Any]) -> CapabilityTaskDefinition:
        """
        Convert task dictionary from LLM to CapabilityTaskDefinition.
        
        Args:
            task_dict: Task dictionary
            
        Returns:
            CapabilityTaskDefinition object
        """
        steps = [
            CapabilityStep(
                capability_name=step["capability_name"],
                inputs=step.get("inputs", {}),
                output_key=step["output_key"],
                order=i
            )
            for i, step in enumerate(task_dict["steps"])
        ]
        
        return CapabilityTaskDefinition(
            name=task_dict["name"],
            description=task_dict.get("description", ""),
            steps=steps,
            tags=task_dict.get("tags", [])
        )


# Global instance
_composer_instance: Optional[CapabilityNaturalLanguageComposer] = None


def get_composer() -> CapabilityNaturalLanguageComposer:
    """Get the global composer instance."""
    global _composer_instance
    if _composer_instance is None:
        _composer_instance = CapabilityNaturalLanguageComposer()
    return _composer_instance
