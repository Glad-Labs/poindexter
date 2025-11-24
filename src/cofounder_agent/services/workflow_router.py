"""UnifiedWorkflowRouter - Single endpoint for all workflow types.

Routes requests to appropriate pipelines based on workflow_type,
supports custom pipelines, and integrates with NLP intent recognition
for natural language requests.
"""

import logging
from typing import Any, Dict, Optional

from src.cofounder_agent.services.pipeline_executor import (
    ModularPipelineExecutor,
    WorkflowRequest,
    WorkflowResponse,
)

logger = logging.getLogger(__name__)


class UnifiedWorkflowRouter:
    """Route requests to modular workflow pipelines.
    
    Supports:
    - Default pipelines by workflow_type
    - Custom pipeline specification
    - Natural language intent recognition (Phase 3)
    - Request validation and routing
    """
    
    def __init__(self):
        """Initialize router with executor."""
        self.executor = ModularPipelineExecutor()
    
    async def execute_workflow(
        self,
        workflow_type: str,
        input_data: Dict[str, Any],
        user_id: str,
        source: str = "api",
        custom_pipeline: Optional[list] = None,
        execution_options: Optional[Dict[str, Any]] = None,
    ) -> WorkflowResponse:
        """Execute workflow via unified router.
        
        Args:
            workflow_type: Type of workflow (content_generation, social_media, etc.)
            input_data: Input data for the workflow
            user_id: User executing the workflow
            source: Where request originated (form, chat, voice, api)
            custom_pipeline: Custom task pipeline (optional)
            execution_options: Execution configuration (optional)
        
        Returns:
            WorkflowResponse with execution results
        """
        # Create workflow request
        request = WorkflowRequest(
            workflow_type = workflow_type,
            input_data = input_data,
            user_id = user_id,
            source = source,
            custom_pipeline = custom_pipeline,
            execution_options = execution_options or {},
        )
        
        # Execute via modular executor
        response = await self.executor.execute(request)
        return response
    
    async def execute_from_natural_language(
        self,
        user_message: str,
        user_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowResponse:
        """Execute workflow from natural language request.
        
        Example: "Generate a blog post about AI trends"
        → Parsed to content_generation workflow with topic="AI trends"
        
        Args:
            user_message: Natural language request
            user_id: User making the request
            context: Optional context data
        
        Returns:
            WorkflowResponse with execution results
        """
        # Parse natural language intent
        intent_result = await self._parse_intent(user_message, context or {})
        
        if not intent_result["success"]:
            # Return error response
            from datetime import datetime
            return WorkflowResponse(
                workflow_id="unknown",
                workflow_type="unknown",
                status="FAILED",
                user_id=user_id,
                output={},
                task_results=[],
                start_time=datetime.utcnow(),
                end_time=datetime.utcnow(),
                duration_seconds=0,
                task_count=0,
                errors=[intent_result["error"]],
            )
        
        # Execute identified workflow
        return await self.execute_workflow(
            workflow_type=intent_result["workflow_type"],
            input_data=intent_result["input_data"],
            user_id=user_id,
            source="chat",
            custom_pipeline=intent_result.get("custom_pipeline"),
        )
    
    async def _parse_intent(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Parse natural language message to workflow intent.
        
        Maps user messages to workflow types and extracts parameters.
        
        Args:
            message: User's natural language message
            context: Additional context
        
        Returns:
            Dict with workflow_type, input_data, custom_pipeline
        """
        message_lower = message.lower()
        
        # Intent patterns (Phase 3 - can be enhanced with ML)
        intent_patterns = {
            # Content generation patterns
            ("blog", "generate", "post"): {
                "workflow_type": "content_generation",
                "extractor": self._extract_content_params,
            },
            ("write", "article"): {
                "workflow_type": "content_generation",
                "extractor": self._extract_content_params,
            },
            ("create", "content"): {
                "workflow_type": "content_generation",
                "extractor": self._extract_content_params,
            },
            
            # Social media patterns
            ("social", "post"): {
                "workflow_type": "social_media",
                "extractor": self._extract_social_params,
            },
            ("tweet", "linkedin", "instagram"): {
                "workflow_type": "social_media",
                "extractor": self._extract_social_params,
            },
            
            # Financial analysis
            ("cost", "analysis"): {
                "workflow_type": "financial_analysis",
                "extractor": self._extract_financial_params,
            },
            ("roi", "budget"): {
                "workflow_type": "financial_analysis",
                "extractor": self._extract_financial_params,
            },
            
            # Market analysis
            ("market", "research"): {
                "workflow_type": "market_analysis",
                "extractor": self._extract_market_params,
            },
            ("competitor", "analysis"): {
                "workflow_type": "market_analysis",
                "extractor": self._extract_market_params,
            },
        }
        
        # Match patterns
        matched_workflow = None
        for keywords, config in intent_patterns.items():
            if any(keyword in message_lower for keyword in keywords):
                matched_workflow = config
                break
        
        if not matched_workflow:
            return {
                "success": False,
                "error": f"Could not parse intent from message: '{message}'",
            }
        
        # Extract parameters from message
        input_data = matched_workflow["extractor"](message)
        
        return {
            "success": True,
            "workflow_type": matched_workflow["workflow_type"],
            "input_data": input_data,
            "custom_pipeline": None,
        }
    
    @staticmethod
    def _extract_content_params(message: str) -> Dict[str, Any]:
        """Extract parameters for content generation."""
        # Simple extraction - can be enhanced with NLP
        words = message.split()
        
        # Find topic (typically after "about" or "on")
        topic = None
        for i, word in enumerate(words):
            if word.lower() in ["about", "on"] and i + 1 < len(words):
                topic = " ".join(words[i+1:])
                break
        
        return {
            "topic": topic or message,
            "style": "professional",
            "length": "2000 words",
        }
    
    @staticmethod
    def _extract_social_params(message: str) -> Dict[str, Any]:
        """Extract parameters for social media."""
        # Simple extraction
        words = message.split()
        
        # Find topic
        topic = None
        for i, word in enumerate(words):
            if word.lower() in ["about", "on"] and i + 1 < len(words):
                topic = " ".join(words[i+1:])
                break
        
        return {
            "topic": topic or message,
            "platforms": ["twitter", "linkedin", "instagram"],
        }
    
    @staticmethod
    def _extract_financial_params(message: str) -> Dict[str, Any]:
        """Extract parameters for financial analysis."""
        return {
            "period": "monthly",
            "include_forecast": True,
        }
    
    @staticmethod
    def _extract_market_params(message: str) -> Dict[str, Any]:
        """Extract parameters for market analysis."""
        words = message.split()
        
        # Find market/industry
        market = None
        for i, word in enumerate(words):
            if word.lower() in ["in", "for"] and i + 1 < len(words):
                market = " ".join(words[i+1:])
                break
        
        return {
            "market": market or "general",
            "include_competitors": True,
        }
    
    async def list_available_workflows(self) -> Dict[str, Any]:
        """List all available workflows.
        
        Returns:
            Dict with workflow types and their default pipelines
        """
        pipelines = await self.executor.list_available_pipelines()
        
        return {
            "workflows": [
                {
                    "type": "content_generation",
                    "description": "Generate professional blog posts with research, creative writing, QA, and publishing",
                    "default_pipeline": pipelines.get("content_generation", []),
                },
                {
                    "type": "content_with_approval",
                    "description": "Content generation with approval gate before publishing",
                    "default_pipeline": pipelines.get("content_with_approval", []),
                },
                {
                    "type": "social_media",
                    "description": "Create and distribute social media posts across platforms",
                    "default_pipeline": pipelines.get("social_media", []),
                },
                {
                    "type": "financial_analysis",
                    "description": "Analyze costs, ROI, and financial projections",
                    "default_pipeline": pipelines.get("financial_analysis", []),
                },
                {
                    "type": "market_analysis",
                    "description": "Research market trends and competitor analysis",
                    "default_pipeline": pipelines.get("market_analysis", []),
                },
                {
                    "type": "performance_review",
                    "description": "Review campaign performance and metrics",
                    "default_pipeline": pipelines.get("performance_review", []),
                },
            ]
        }
