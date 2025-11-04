"""
Base Agent Class for GLAD Labs

All specialized agents (ContentAgent, FinancialAgent, etc.) inherit from this class.
Provides standard interface for:
- Tool access via MCP
- Memory management
- Model selection
- Error handling
- Logging
- Cost tracking
"""

import asyncio
import logging
import json
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from uuid import uuid4

import aiohttp


# ============================================================================
# Type Definitions
# ============================================================================


class AgentRole(str, Enum):
    """Agent role/specialty"""
    RESEARCH = "research"
    CREATIVE = "creative"
    QA = "qa"
    IMAGE = "image"
    PUBLISHING = "publishing"
    FINANCIAL = "financial"
    MARKET = "market"
    COMPLIANCE = "compliance"


class ExecutionStatus(str, Enum):
    """Task execution status"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass
class ToolCall:
    """Record of a tool invocation"""
    tool_name: str
    arguments: Dict[str, Any]
    result: Optional[Any] = None
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    duration_ms: float = 0.0


@dataclass
class ExecutionLog:
    """Complete execution log for a task"""
    execution_id: str
    agent_name: str
    agent_role: str
    task_type: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    tool_calls: List[ToolCall] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    cost_usd: float = 0.0
    cost_energy_kwh: float = 0.0
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        data = asdict(self)
        data["status"] = self.status.value
        data["tool_calls"] = [asdict(tc) for tc in self.tool_calls]
        return data


@dataclass
class AgentContext:
    """Context passed to agent for execution"""
    task_id: str
    task_type: str
    parameters: Dict[str, Any]
    llm_config: Dict[str, Any]
    memory_context: Optional[Dict[str, Any]] = None
    previous_results: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Base Agent Class
# ============================================================================


class BaseAgent(ABC):
    """
    Abstract base class for all GLAD Labs agents.

    Features:
    - MCP tool access
    - Memory (short-term & long-term)
    - Model selection from router
    - Execution logging
    - Cost tracking
    - Error handling
    - Retry logic

    Example Usage:

        class MyAgent(BaseAgent):
            def __init__(self):
                super().__init__(
                    name="my_agent",
                    role=AgentRole.RESEARCH
                )

            async def execute(self, context: AgentContext) -> Dict[str, Any]:
                result = await self.call_tool("search_web", {
                    "query": "some query"
                })
                await self.remember("search_result", result)
                return {"data": result}

        agent = MyAgent()
        result = await agent.execute(context)
    """

    def __init__(
        self,
        name: str,
        role: AgentRole,
        mcp_server_url: str = "http://localhost:9000",
        memory_service_url: str = "http://localhost:8001",
        model_router_url: str = "http://localhost:8002",
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize base agent.

        Args:
            name: Agent name (must be unique)
            role: Agent specialty
            mcp_server_url: MCP server URL
            memory_service_url: Memory service URL
            model_router_url: Model router service URL
            logger: Optional logger instance
        """
        self.name = name
        self.role = role
        self.execution_id = str(uuid4())

        # Service URLs
        self.mcp_server_url = mcp_server_url
        self.memory_service_url = memory_service_url
        self.model_router_url = model_router_url

        # Logging
        self.logger = logger or logging.getLogger(f"agent.{name}")

        # Execution tracking
        self.current_log: Optional[ExecutionLog] = None
        self.current_context: Optional[AgentContext] = None

        # Service clients (lazy-loaded)
        self._mcp_client: Optional[aiohttp.ClientSession] = None
        self._memory_client: Optional[aiohttp.ClientSession] = None
        self._model_router_client: Optional[aiohttp.ClientSession] = None

    # ========================================================================
    # Abstract Methods (must be implemented by subclasses)
    # ========================================================================

    @abstractmethod
    async def execute(self, context: AgentContext) -> Dict[str, Any]:
        """
        Execute agent task.

        Args:
            context: Execution context with parameters

        Returns:
            Result dictionary

        Raises:
            Exception: On execution failure
        """
        pass

    # ========================================================================
    # Tool Access (via MCP)
    # ========================================================================

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        retry_count: int = 3
    ) -> Any:
        """
        Call an MCP tool.

        Example:
            result = await agent.call_tool(
                "create_task",
                {"task_type": "blog_post", "topic": "AI"}
            )

        Args:
            tool_name: Name of MCP tool
            arguments: Tool arguments
            retry_count: Max retry attempts

        Returns:
            Tool result

        Raises:
            Exception: If tool fails after retries
        """
        if not self.current_log:
            raise RuntimeError("No active execution log")

        tool_call = ToolCall(tool_name=tool_name, arguments=arguments)
        start_time = datetime.utcnow()

        try:
            # Make request to MCP server
            async with self._get_mcp_client() as session:
                async with session.post(
                    f"{self.mcp_server_url}/tools/call",
                    json={
                        "tool": tool_name,
                        "arguments": arguments,
                        "execution_id": self.execution_id
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        tool_call.result = data.get("result")
                    elif resp.status == 429:
                        # Rate limited - retry with backoff
                        if retry_count > 0:
                            await asyncio.sleep(2 ** (3 - retry_count))
                            return await self.call_tool(
                                tool_name, arguments, retry_count - 1
                            )
                        raise Exception("Tool rate limited - max retries exceeded")
                    else:
                        error_data = await resp.json()
                        tool_call.error = error_data.get("error", {}).get("message")
                        raise Exception(f"Tool call failed: {tool_call.error}")

        except asyncio.TimeoutError:
            tool_call.error = "Tool call timeout"
            raise
        except Exception as e:
            tool_call.error = str(e)
            raise
        finally:
            # Track tool call
            tool_call.duration_ms = (
                datetime.utcnow() - start_time
            ).total_seconds() * 1000
            self.current_log.tool_calls.append(tool_call)

        return tool_call.result

    # ========================================================================
    # Memory Access
    # ========================================================================

    async def remember(
        self,
        key: str,
        value: Any,
        memory_type: str = "long_term",
        tags: Optional[List[str]] = None
    ) -> None:
        """
        Store information in memory for future use.

        Example:
            await agent.remember("blog_research", research_data)

        Args:
            key: Memory key
            value: Value to store
            memory_type: "short_term" or "long_term"
            tags: Optional tags for organization
        """
        if not self.current_log:
            raise RuntimeError("No active execution log")

        async with self._get_memory_client() as session:
            async with session.post(
                f"{self.memory_service_url}/memory/store",
                json={
                    "key": key,
                    "value": value if isinstance(value, (str, int, float, bool)) else json.dumps(value),
                    "memory_type": memory_type,
                    "tags": tags or [],
                    "agent": self.name,
                    "execution_id": self.execution_id
                }
            ) as resp:
                if resp.status not in (200, 201):
                    error = await resp.text()
                    self.logger.warning(f"Failed to store memory: {error}")

    async def recall(self, key: str, memory_type: str = "long_term") -> Optional[Any]:
        """
        Retrieve stored memory.

        Example:
            previous_research = await agent.recall("blog_research")

        Args:
            key: Memory key
            memory_type: Type of memory to retrieve

        Returns:
            Stored value or None if not found
        """
        async with self._get_memory_client() as session:
            async with session.get(
                f"{self.memory_service_url}/memory/retrieve",
                params={"key": key, "memory_type": memory_type}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("value")
        return None

    async def search_memory(
        self,
        query: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search memories semantically.

        Example:
            similar_tasks = await agent.search_memory("blog posts about tech")

        Args:
            query: Search query
            limit: Max results

        Returns:
            List of matching memories
        """
        async with self._get_memory_client() as session:
            async with session.post(
                f"{self.memory_service_url}/memory/search",
                json={"query": query, "limit": limit}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("results", [])
        return []

    # ========================================================================
    # Model Selection & LLM Calls
    # ========================================================================

    async def query_model(
        self,
        prompt: str,
        task_type: str = "default",
        step: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> str:
        """
        Query an LLM model selected by the router.

        The model router will:
        1. Look up configured model for (task_type, step)
        2. Check if model is available
        3. Verify cost is within budget
        4. Fallback to next model if needed
        5. Return response

        Example:
            response = await agent.query_model(
                prompt="Write a blog post about AI",
                task_type="blog_post",
                step="creative"
            )

        Args:
            prompt: Prompt to send to model
            task_type: Type of task (for model selection)
            step: Step within task (for model selection)
            temperature: Creativity parameter
            max_tokens: Max response length

        Returns:
            Model response

        Raises:
            Exception: If no model available and budget exhausted
        """
        if not self.current_log:
            raise RuntimeError("No active execution log")

        async with self._get_model_router_client() as session:
            async with session.post(
                f"{self.model_router_url}/query",
                json={
                    "prompt": prompt,
                    "task_type": task_type,
                    "step": step,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "execution_id": self.execution_id,
                    "agent": self.name
                },
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Track cost
                    self.current_log.cost_usd += data.get("cost_usd", 0)
                    self.current_log.cost_energy_kwh += data.get("cost_energy_kwh", 0)
                    return data.get("response", "")
                else:
                    error = await resp.json()
                    raise Exception(f"Model query failed: {error.get('error')}")

    async def get_model_info(self, model_name: str) -> Dict[str, Any]:
        """
        Get information about a model.

        Args:
            model_name: Model identifier

        Returns:
            Model info (pricing, status, etc.)
        """
        async with self._get_model_router_client() as session:
            async with session.get(
                f"{self.model_router_url}/models/{model_name}"
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
        return {}

    # ========================================================================
    # Execution Lifecycle
    # ========================================================================

    async def start_execution(self, context: AgentContext) -> None:
        """
        Initialize execution log.

        Called at start of execute() method.
        """
        self.current_context = context
        self.current_log = ExecutionLog(
            execution_id=self.execution_id,
            agent_name=self.name,
            agent_role=self.role.value,
            task_type=context.task_type,
            start_time=datetime.utcnow().isoformat()
        )
        self.logger.info(
            f"Started execution {self.execution_id} for task {context.task_id}"
        )

    async def end_execution(self, status: ExecutionStatus) -> ExecutionLog:
        """
        Finalize execution log.

        Called at end of execute() method (success or error).
        """
        if not self.current_log:
            raise RuntimeError("No active execution log")

        self.current_log.status = status
        self.current_log.end_time = datetime.utcnow().isoformat()

        self.logger.info(
            f"Completed execution {self.execution_id} with status {status.value}"
        )

        return self.current_log

    def add_log_message(self, message: str) -> None:
        """Add message to execution log"""
        if self.current_log:
            self.current_log.messages.append(
                f"[{datetime.utcnow().isoformat()}] {message}"
            )

    def add_metadata(self, key: str, value: Any) -> None:
        """Add metadata to execution log"""
        if self.current_log:
            self.current_log.metadata[key] = value

    # ========================================================================
    # Private Methods
    # ========================================================================

    def _get_mcp_client(self) -> "ClientSessionManager":
        """Lazy-load MCP client"""
        return ClientSessionManager(self._mcp_client, create_new=not self._mcp_client)

    def _get_memory_client(self) -> "ClientSessionManager":
        """Lazy-load memory client"""
        return ClientSessionManager(
            self._memory_client, create_new=not self._memory_client
        )

    def _get_model_router_client(self) -> "ClientSessionManager":
        """Lazy-load model router client"""
        return ClientSessionManager(
            self._model_router_client, create_new=not self._model_router_client
        )

    async def cleanup(self) -> None:
        """Close all client connections"""
        if self._mcp_client:
            await self._mcp_client.close()
        if self._memory_client:
            await self._memory_client.close()
        if self._model_router_client:
            await self._model_router_client.close()

    # ========================================================================
    # Context Manager Support
    # ========================================================================

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.cleanup()


# ============================================================================
# Helper Classes
# ============================================================================


class ClientSessionManager:
    """Helper for lazy-loading aiohttp clients"""

    def __init__(self, existing_client: Optional[aiohttp.ClientSession], create_new: bool):
        self.client = existing_client
        self.create_new = create_new
        self.created_client = False

    async def __aenter__(self) -> aiohttp.ClientSession:
        if self.create_new and not self.client:
            self.client = aiohttp.ClientSession()
            self.created_client = True
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.created_client and self.client:
            await self.client.close()


# ============================================================================
# Decorator for common agent patterns
# ============================================================================


def with_execution_log(func: Callable) -> Callable:
    """
    Decorator that wraps execute() with execution log lifecycle.

    Usage:
        class MyAgent(BaseAgent):
            @with_execution_log
            async def execute(self, context: AgentContext):
                # ... agent logic ...
                return result
    """
    async def wrapper(self: BaseAgent, context: AgentContext) -> Dict[str, Any]:
        await self.start_execution(context)
        try:
            result = await func(self, context)
            await self.end_execution(ExecutionStatus.COMPLETED)
            return result
        except Exception as e:
            self.add_log_message(f"Error: {str(e)}")
            await self.end_execution(ExecutionStatus.FAILED)
            raise

    return wrapper
