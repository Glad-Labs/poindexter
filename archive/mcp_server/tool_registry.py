"""
MCP Tool Registry

Centralized registry of 35+ tools available to agents.
- Task Management
- Model Configuration
- Distribution
- Analytics
- Memory
- Agent Control
- Database Access
"""

import logging
import json
from typing import Any, Dict, Optional, List, Callable
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
import asyncio


# ============================================================================
# Tool Definitions
# ============================================================================


@dataclass
class ParameterSchema:
    """JSON Schema for tool parameter"""
    type: str  # "string", "number", "boolean", "object", "array"
    description: str
    required: bool = False
    enum: Optional[List[str]] = None
    default: Optional[Any] = None


@dataclass
class ToolDefinition:
    """Tool specification"""
    name: str
    description: str
    category: str  # "task_management", "model_config", "distribution", etc.
    parameters: Dict[str, ParameterSchema]
    returns: Dict[str, Any]
    cost_usd: float = 0.0
    timeout_seconds: int = 300
    retry_count: int = 3
    requires_auth: bool = False


# ============================================================================
# Tool Registry
# ============================================================================


class ToolRegistry:
    """Registry of all available tools"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.tools: Dict[str, ToolDefinition] = {}
        self.tool_handlers: Dict[str, Callable] = {}
        self._register_default_tools()
    
    def _register_default_tools(self) -> None:
        """Register all default tools from MCP_SPECIFICATION"""
        
        # ====================================================================
        # Task Management Tools
        # ====================================================================
        
        self.register_tool(
            ToolDefinition(
                name="create_task",
                category="task_management",
                description="Create a new task",
                parameters={
                    "task_type": ParameterSchema(
                        type="string",
                        description="Type of task: blog_post, social_content, email, etc.",
                        required=True,
                        enum=["blog_post", "social_content", "email", "video", "image"]
                    ),
                    "topic": ParameterSchema(
                        type="string",
                        description="Topic or subject",
                        required=True,
                    ),
                    "parameters": ParameterSchema(
                        type="object",
                        description="Task-specific parameters",
                        required=False,
                    ),
                },
                returns={"task_id": "string", "status": "string"},
                cost_usd=0.0,
            ),
            self._handle_create_task,
        )
        
        self.register_tool(
            ToolDefinition(
                name="query_task_status",
                category="task_management",
                description="Query the status of a task",
                parameters={
                    "task_id": ParameterSchema(
                        type="string",
                        description="Task ID",
                        required=True,
                    ),
                },
                returns={"task_id": "string", "status": "string", "progress": "number"},
                cost_usd=0.0,
            ),
            self._handle_query_task_status,
        )
        
        self.register_tool(
            ToolDefinition(
                name="list_tasks",
                category="task_management",
                description="List all tasks",
                parameters={
                    "status": ParameterSchema(
                        type="string",
                        description="Filter by status",
                        required=False,
                        enum=["pending", "in_progress", "completed", "failed"],
                    ),
                    "limit": ParameterSchema(
                        type="number",
                        description="Maximum number of tasks to return",
                        required=False,
                        default=10,
                    ),
                },
                returns={"tasks": "array", "total": "number"},
                cost_usd=0.0,
            ),
            self._handle_list_tasks,
        )
        
        self.register_tool(
            ToolDefinition(
                name="cancel_task",
                category="task_management",
                description="Cancel a task",
                parameters={
                    "task_id": ParameterSchema(
                        type="string",
                        description="Task ID",
                        required=True,
                    ),
                },
                returns={"task_id": "string", "status": "string"},
                cost_usd=0.0,
            ),
            self._handle_cancel_task,
        )
        
        # ====================================================================
        # Model Configuration Tools
        # ====================================================================
        
        self.register_tool(
            ToolDefinition(
                name="get_available_models",
                category="model_config",
                description="Get all available AI models",
                parameters={},
                returns={"models": "array"},
                cost_usd=0.0,
            ),
            self._handle_get_available_models,
        )
        
        self.register_tool(
            ToolDefinition(
                name="configure_model_for_task",
                category="model_config",
                description="Configure model selection for a task type/step",
                parameters={
                    "task_type": ParameterSchema(
                        type="string",
                        description="Task type (blog_post, etc.)",
                        required=True,
                    ),
                    "step": ParameterSchema(
                        type="string",
                        description="Task step (research, creative, qa, etc.)",
                        required=True,
                    ),
                    "model_preference": ParameterSchema(
                        type="object",
                        description="Model preference config",
                        required=True,
                    ),
                },
                returns={"success": "boolean"},
                cost_usd=0.0,
            ),
            self._handle_configure_model_for_task,
        )
        
        self.register_tool(
            ToolDefinition(
                name="test_model_connection",
                category="model_config",
                description="Test connection to a model provider",
                parameters={
                    "provider": ParameterSchema(
                        type="string",
                        description="Model provider",
                        required=True,
                        enum=["ollama", "openai", "anthropic", "google"],
                    ),
                    "model": ParameterSchema(
                        type="string",
                        description="Model name",
                        required=True,
                    ),
                },
                returns={"status": "string", "latency_ms": "number"},
                cost_usd=0.0,
            ),
            self._handle_test_model_connection,
        )
        
        self.register_tool(
            ToolDefinition(
                name="get_model_pricing",
                category="model_config",
                description="Get pricing info for models",
                parameters={
                    "provider": ParameterSchema(
                        type="string",
                        description="Model provider",
                        required=False,
                        enum=["ollama", "openai", "anthropic", "google"],
                    ),
                },
                returns={"pricing": "object"},
                cost_usd=0.0,
            ),
            self._handle_get_model_pricing,
        )
        
        # ====================================================================
        # Distribution Tools
        # ====================================================================
        
        self.register_tool(
            ToolDefinition(
                name="publish_content",
                category="distribution",
                description="Publish content to CMS or social media",
                parameters={
                    "content": ParameterSchema(
                        type="object",
                        description="Content to publish",
                        required=True,
                    ),
                    "channel": ParameterSchema(
                        type="string",
                        description="Publication channel",
                        required=True,
                        enum=["cms", "twitter", "linkedin", "instagram"],
                    ),
                },
                returns={"success": "boolean", "url": "string"},
                cost_usd=0.0,
            ),
            self._handle_publish_content,
        )
        
        self.register_tool(
            ToolDefinition(
                name="get_channel_status",
                category="distribution",
                description="Get status of publication channels",
                parameters={
                    "channel": ParameterSchema(
                        type="string",
                        description="Channel name (optional)",
                        required=False,
                    ),
                },
                returns={"channels": "object"},
                cost_usd=0.0,
            ),
            self._handle_get_channel_status,
        )
        
        # ====================================================================
        # Analytics Tools
        # ====================================================================
        
        self.register_tool(
            ToolDefinition(
                name="get_analytics",
                category="analytics",
                description="Get content performance analytics",
                parameters={
                    "time_range": ParameterSchema(
                        type="string",
                        description="Time range for analytics",
                        required=False,
                        default="last_7_days",
                    ),
                },
                returns={"metrics": "object"},
                cost_usd=0.0,
            ),
            self._handle_get_analytics,
        )
        
        self.register_tool(
            ToolDefinition(
                name="get_cost_breakdown",
                category="analytics",
                description="Get cost breakdown by provider/model",
                parameters={
                    "period": ParameterSchema(
                        type="string",
                        description="Time period",
                        required=False,
                        default="current_month",
                    ),
                },
                returns={"costs": "object", "total_usd": "number"},
                cost_usd=0.0,
            ),
            self._handle_get_cost_breakdown,
        )
        
        self.register_tool(
            ToolDefinition(
                name="get_quality_metrics",
                category="analytics",
                description="Get quality metrics for generated content",
                parameters={
                    "task_type": ParameterSchema(
                        type="string",
                        description="Task type to get metrics for",
                        required=False,
                    ),
                },
                returns={"metrics": "object"},
                cost_usd=0.0,
            ),
            self._handle_get_quality_metrics,
        )
        
        # ====================================================================
        # Memory Tools
        # ====================================================================
        
        self.register_tool(
            ToolDefinition(
                name="store_memory",
                category="memory",
                description="Store information in memory",
                parameters={
                    "key": ParameterSchema(
                        type="string",
                        description="Memory key",
                        required=True,
                    ),
                    "value": ParameterSchema(
                        type="object",
                        description="Data to store",
                        required=True,
                    ),
                    "memory_type": ParameterSchema(
                        type="string",
                        description="Memory type",
                        required=False,
                        enum=["short_term", "long_term"],
                        default="long_term",
                    ),
                },
                returns={"success": "boolean"},
                cost_usd=0.0,
            ),
            self._handle_store_memory,
        )
        
        self.register_tool(
            ToolDefinition(
                name="retrieve_memory",
                category="memory",
                description="Retrieve information from memory",
                parameters={
                    "key": ParameterSchema(
                        type="string",
                        description="Memory key",
                        required=True,
                    ),
                },
                returns={"data": "object"},
                cost_usd=0.0,
            ),
            self._handle_retrieve_memory,
        )
        
        self.register_tool(
            ToolDefinition(
                name="semantic_search_memory",
                category="memory",
                description="Search memory using semantic similarity",
                parameters={
                    "query": ParameterSchema(
                        type="string",
                        description="Search query",
                        required=True,
                    ),
                    "limit": ParameterSchema(
                        type="number",
                        description="Maximum results",
                        required=False,
                        default=10,
                    ),
                },
                returns={"results": "array"},
                cost_usd=0.0,
            ),
            self._handle_semantic_search_memory,
        )
        
        # ====================================================================
        # Agent Control Tools
        # ====================================================================
        
        self.register_tool(
            ToolDefinition(
                name="invoke_agent",
                category="agent_control",
                description="Invoke another agent",
                parameters={
                    "agent_name": ParameterSchema(
                        type="string",
                        description="Name of agent to invoke",
                        required=True,
                    ),
                    "task": ParameterSchema(
                        type="object",
                        description="Task for agent",
                        required=True,
                    ),
                },
                returns={"result": "object", "cost_usd": "number"},
                cost_usd=0.0,
            ),
            self._handle_invoke_agent,
        )
        
        self.register_tool(
            ToolDefinition(
                name="get_agent_status",
                category="agent_control",
                description="Get status of agents",
                parameters={
                    "agent_name": ParameterSchema(
                        type="string",
                        description="Agent name (optional, gets all if not specified)",
                        required=False,
                    ),
                },
                returns={"agents": "object"},
                cost_usd=0.0,
            ),
            self._handle_get_agent_status,
        )
        
        # ====================================================================
        # Database Tools
        # ====================================================================
        
        self.register_tool(
            ToolDefinition(
                name="query_database",
                category="database",
                description="Query the database",
                parameters={
                    "query": ParameterSchema(
                        type="string",
                        description="SQL query",
                        required=True,
                    ),
                },
                returns={"rows": "array"},
                cost_usd=0.0,
            ),
            self._handle_query_database,
        )
        
        self.register_tool(
            ToolDefinition(
                name="save_result",
                category="database",
                description="Save task result to database",
                parameters={
                    "task_id": ParameterSchema(
                        type="string",
                        description="Task ID",
                        required=True,
                    ),
                    "result": ParameterSchema(
                        type="object",
                        description="Result data",
                        required=True,
                    ),
                    "cost_usd": ParameterSchema(
                        type="number",
                        description="Cost in USD",
                        required=False,
                        default=0.0,
                    ),
                },
                returns={"success": "boolean"},
                cost_usd=0.0,
            ),
            self._handle_save_result,
        )
        
        self.logger.info(f"Registered {len(self.tools)} default tools")
    
    def register_tool(
        self,
        definition: ToolDefinition,
        handler: Optional[Callable] = None,
    ) -> None:
        """Register a tool"""
        self.tools[definition.name] = definition
        if handler:
            self.tool_handlers[definition.name] = handler
        self.logger.info(f"Registered tool: {definition.name}")
    
    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Get tool definition"""
        return self.tools.get(name)
    
    def list_tools(self) -> List[Dict[str, Any]]:
        """List all tools"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "category": tool.category,
                "cost_usd": tool.cost_usd,
            }
            for tool in self.tools.values()
        ]
    
    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        agent_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Call a tool"""
        
        # Validate tool exists
        if tool_name not in self.tools:
            raise ValueError(f"Tool '{tool_name}' not found")
        
        # Get handler
        handler = self.tool_handlers.get(tool_name)
        if not handler:
            raise ValueError(f"No handler for tool '{tool_name}'")
        
        # Call handler
        self.logger.info(f"Calling tool: {tool_name}")
        result = await handler(arguments, agent_id, execution_id)
        
        return {
            "result": result,
            "cost_usd": 0.0,
        }
    
    # ========================================================================
    # Tool Handlers (Stubs)
    # ========================================================================
    
    async def _handle_create_task(self, args, agent_id, exec_id):
        return {"task_id": "task_123", "status": "pending"}
    
    async def _handle_query_task_status(self, args, agent_id, exec_id):
        return {"task_id": args.get("task_id"), "status": "in_progress", "progress": 0.5}
    
    async def _handle_list_tasks(self, args, agent_id, exec_id):
        return {"tasks": [], "total": 0}
    
    async def _handle_cancel_task(self, args, agent_id, exec_id):
        return {"task_id": args.get("task_id"), "status": "cancelled"}
    
    async def _handle_get_available_models(self, args, agent_id, exec_id):
        return {"models": ["ollama:mistral", "gpt-4", "claude-3-opus"]}
    
    async def _handle_configure_model_for_task(self, args, agent_id, exec_id):
        return {"success": True}
    
    async def _handle_test_model_connection(self, args, agent_id, exec_id):
        return {"status": "online", "latency_ms": 150}
    
    async def _handle_get_model_pricing(self, args, agent_id, exec_id):
        return {"pricing": {}}
    
    async def _handle_publish_content(self, args, agent_id, exec_id):
        return {"success": True, "url": "https://example.com"}
    
    async def _handle_get_channel_status(self, args, agent_id, exec_id):
        return {"channels": {}}
    
    async def _handle_get_analytics(self, args, agent_id, exec_id):
        return {"metrics": {}}
    
    async def _handle_get_cost_breakdown(self, args, agent_id, exec_id):
        return {"costs": {}, "total_usd": 0.0}
    
    async def _handle_get_quality_metrics(self, args, agent_id, exec_id):
        return {"metrics": {}}
    
    async def _handle_store_memory(self, args, agent_id, exec_id):
        return {"success": True}
    
    async def _handle_retrieve_memory(self, args, agent_id, exec_id):
        return {"data": {}}
    
    async def _handle_semantic_search_memory(self, args, agent_id, exec_id):
        return {"results": []}
    
    async def _handle_invoke_agent(self, args, agent_id, exec_id):
        return {"result": {}, "cost_usd": 0.0}
    
    async def _handle_get_agent_status(self, args, agent_id, exec_id):
        return {"agents": {}}
    
    async def _handle_query_database(self, args, agent_id, exec_id):
        return {"rows": []}
    
    async def _handle_save_result(self, args, agent_id, exec_id):
        return {"success": True}
