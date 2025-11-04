"""
MCP Server Implementation

FastAPI-based MCP (Model Context Protocol) server providing:
- Standardized tool access via JSON-RPC 2.0
- Resource management (tasks, models, memory)
- Error handling with retry strategies
- Rate limiting and performance monitoring
- External MCP server integration
- Comprehensive logging and cost tracking
"""

import asyncio
import logging
import time
import json
import os
from typing import Any, Dict, Optional, List, Callable
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, asdict

from fastapi import FastAPI, WebSocket, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import aiohttp

from .tool_registry import ToolRegistry, ToolDefinition
from .resource_manager import ResourceManager


# ============================================================================
# Configuration & Types
# ============================================================================


class MCPErrorCode(str, Enum):
    """MCP Error Codes (from MCP_SPECIFICATION.md)"""
    # Client errors
    INVALID_REQUEST = "INVALID_REQUEST"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    
    # Server errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"


@dataclass
class MCPConfig:
    """MCP Server Configuration"""
    host: str = "0.0.0.0"
    port: int = 9000
    debug: bool = False
    max_request_size: int = 10_000_000  # 10MB
    request_timeout: int = 300  # 5 minutes
    rate_limit_requests_per_minute: int = 1000
    enable_external_mcp: bool = True
    cors_origins: List[str] = None
    
    def __post_init__(self):
        if self.cors_origins is None:
            self.cors_origins = [
                "http://localhost:3000",
                "http://localhost:3001",
                "http://localhost:8000",
            ]


@dataclass
class MCPErrorResponse:
    """Standardized error response"""
    code: MCPErrorCode
    message: str
    details: Optional[Dict[str, Any]] = None
    retry_after_seconds: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict"""
        result = {
            "code": self.code.value,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        if self.retry_after_seconds:
            result["retry_after_seconds"] = self.retry_after_seconds
        return result


@dataclass
class MCPRequest:
    """Incoming MCP request"""
    id: str
    jsonrpc: str = "2.0"
    method: str = "call_tool"
    params: Dict[str, Any] = None
    agent_id: Optional[str] = None
    execution_id: Optional[str] = None


@dataclass
class MCPResponse:
    """Outgoing MCP response"""
    id: str
    jsonrpc: str = "2.0"
    result: Optional[Any] = None
    error: Optional[MCPErrorResponse] = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    timestamp: str = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict"""
        result = {
            "id": self.id,
            "jsonrpc": self.jsonrpc,
            "timestamp": self.timestamp,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
        }
        
        if self.error:
            result["error"] = self.error.to_dict()
        else:
            result["result"] = self.result
        
        return result


# ============================================================================
# Rate Limiter
# ============================================================================


class RateLimiter:
    """Token bucket rate limiter"""
    
    def __init__(self, requests_per_minute: int):
        self.capacity = requests_per_minute
        self.tokens = float(requests_per_minute)
        self.last_update = time.time()
        self.lock = asyncio.Lock()
    
    async def acquire(self, tokens: int = 1) -> bool:
        """Check if tokens available, consume them if so"""
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_update
            self.last_update = now
            
            # Replenish tokens
            self.tokens = min(
                self.capacity,
                self.tokens + (elapsed * self.capacity / 60.0)
            )
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            
            return False
    
    async def get_retry_after(self) -> int:
        """Calculate seconds until tokens available"""
        async with self.lock:
            if self.tokens >= 1:
                return 0
            
            # Time to recover 1 token
            tokens_needed = 1 - self.tokens
            recovery_rate = self.capacity / 60.0
            return int(tokens_needed / recovery_rate) + 1


# ============================================================================
# MCP Server
# ============================================================================


class MCPServer:
    """Main MCP Server"""
    
    def __init__(self, config: MCPConfig = None):
        self.config = config or MCPConfig()
        self.app = FastAPI(
            title="GLAD Labs MCP Server",
            version="1.0.0",
            debug=self.config.debug,
        )
        
        # Components
        self.tool_registry = ToolRegistry()
        self.resource_manager = ResourceManager()
        self.rate_limiter = RateLimiter(self.config.rate_limit_requests_per_minute)
        
        # Logging
        self.logger = logging.getLogger(__name__)
        
        # Request tracking
        self.pending_requests: Dict[str, asyncio.Task] = {}
        
        # Setup middleware and routes
        self._setup_middleware()
        self._setup_routes()
    
    def _setup_middleware(self) -> None:
        """Configure FastAPI middleware"""
        # CORS
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=self.config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    
    def _setup_routes(self) -> None:
        """Configure FastAPI routes"""
        
        @self.app.get("/health")
        async def health_check():
            """Server health check"""
            return {
                "status": "healthy",
                "version": "1.0.0",
                "timestamp": datetime.utcnow().isoformat(),
                "tools_registered": len(self.tool_registry.tools),
            }
        
        @self.app.post("/mcp/tools/call")
        async def call_tool(request: Request):
            """Call a tool via MCP"""
            start_time = time.time()
            request_id = None
            
            try:
                # Parse request
                body = await request.json()
                request_id = body.get("id", "unknown")
                
                # Rate limiting
                if not await self.rate_limiter.acquire():
                    retry_after = await self.rate_limiter.get_retry_after()
                    error = MCPErrorResponse(
                        code=MCPErrorCode.RATE_LIMIT_EXCEEDED,
                        message="Rate limit exceeded",
                        retry_after_seconds=retry_after,
                    )
                    return JSONResponse(
                        status_code=429,
                        content=MCPResponse(
                            id=request_id,
                            error=error,
                            latency_ms=(time.time() - start_time) * 1000,
                        ).to_dict(),
                    )
                
                # Validate request
                mcp_request = await self._validate_request(body)
                
                # Execute tool
                result = await self.tool_registry.call_tool(
                    tool_name=mcp_request.params.get("tool"),
                    arguments=mcp_request.params.get("arguments", {}),
                    agent_id=mcp_request.agent_id,
                    execution_id=mcp_request.execution_id,
                )
                
                # Success response
                latency_ms = (time.time() - start_time) * 1000
                response = MCPResponse(
                    id=request_id,
                    result=result.get("result"),
                    cost_usd=result.get("cost_usd", 0.0),
                    latency_ms=latency_ms,
                )
                
                self.logger.info(
                    f"Tool call successful: {mcp_request.params.get('tool')} "
                    f"({latency_ms:.0f}ms, ${result.get('cost_usd', 0):.4f})"
                )
                
                return JSONResponse(status_code=200, content=response.to_dict())
            
            except ValueError as e:
                # Validation error
                error = MCPErrorResponse(
                    code=MCPErrorCode.INVALID_PARAMETERS,
                    message=str(e),
                )
                return JSONResponse(
                    status_code=400,
                    content=MCPResponse(
                        id=request_id or "unknown",
                        error=error,
                        latency_ms=(time.time() - start_time) * 1000,
                    ).to_dict(),
                )
            
            except Exception as e:
                # Internal error
                self.logger.exception(f"Error calling tool: {e}")
                error = MCPErrorResponse(
                    code=MCPErrorCode.INTERNAL_ERROR,
                    message="Internal server error",
                    details={"error": str(e)} if self.config.debug else None,
                )
                return JSONResponse(
                    status_code=500,
                    content=MCPResponse(
                        id=request_id or "unknown",
                        error=error,
                        latency_ms=(time.time() - start_time) * 1000,
                    ).to_dict(),
                )
        
        @self.app.get("/mcp/tools")
        async def list_tools():
            """List all available tools"""
            tools = self.tool_registry.list_tools()
            return {
                "tools": tools,
                "total": len(tools),
                "timestamp": datetime.utcnow().isoformat(),
            }
        
        @self.app.get("/mcp/tools/{tool_name}")
        async def get_tool_info(tool_name: str):
            """Get info about a specific tool"""
            tool = self.tool_registry.get_tool(tool_name)
            if not tool:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "TOOL_NOT_FOUND", "message": f"Tool '{tool_name}' not found"}
                )
            return {
                "name": tool_name,
                "tool": tool,
                "timestamp": datetime.utcnow().isoformat(),
            }
        
        @self.app.get("/mcp/resources")
        async def list_resources():
            """List available resource types"""
            resources = self.resource_manager.list_resources()
            return {
                "resources": resources,
                "timestamp": datetime.utcnow().isoformat(),
            }
        
        @self.app.get("/mcp/resources/{resource_type}/{resource_id}")
        async def get_resource(resource_type: str, resource_id: str):
            """Access a resource"""
            try:
                resource = await self.resource_manager.get_resource(
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
                return {
                    "resource": resource,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            except ValueError as e:
                raise HTTPException(status_code=404, detail={"message": str(e)})
        
        @self.app.post("/mcp/resources")
        async def create_resource(request: Request):
            """Create a new resource"""
            body = await request.json()
            try:
                resource = await self.resource_manager.create_resource(
                    resource_type=body.get("type"),
                    data=body.get("data"),
                )
                return {
                    "resource": resource,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            except ValueError as e:
                raise HTTPException(status_code=400, detail={"message": str(e)})
        
        @self.app.websocket("/mcp/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time tool calls"""
            await websocket.accept()
            
            try:
                while True:
                    data = await websocket.receive_json()
                    
                    # Process tool call
                    result = await self.tool_registry.call_tool(
                        tool_name=data.get("tool"),
                        arguments=data.get("arguments", {}),
                    )
                    
                    await websocket.send_json({
                        "id": data.get("id"),
                        "result": result,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
            
            except Exception as e:
                self.logger.exception(f"WebSocket error: {e}")
                await websocket.close(code=1000)
    
    async def _validate_request(self, body: Dict[str, Any]) -> MCPRequest:
        """Validate incoming MCP request"""
        
        # Check JSON-RPC format
        if body.get("jsonrpc") != "2.0":
            raise ValueError("Invalid JSON-RPC version")
        
        # Check required fields
        if "id" not in body:
            raise ValueError("Request ID is required")
        
        if "params" not in body:
            raise ValueError("Parameters are required")
        
        # Check tool exists
        tool_name = body["params"].get("tool")
        if not tool_name:
            raise ValueError("Tool name is required in parameters")
        
        if not self.tool_registry.get_tool(tool_name):
            raise ValueError(f"Tool '{tool_name}' not found")
        
        return MCPRequest(
            id=body["id"],
            jsonrpc=body.get("jsonrpc", "2.0"),
            method=body.get("method", "call_tool"),
            params=body.get("params"),
            agent_id=body.get("agent_id"),
            execution_id=body.get("execution_id"),
        )
    
    async def start(self) -> None:
        """Start the MCP server"""
        import uvicorn
        
        self.logger.info(
            f"Starting MCP Server on {self.config.host}:{self.config.port}"
        )
        
        config = uvicorn.Config(
            app=self.app,
            host=self.config.host,
            port=self.config.port,
            log_level="info" if not self.config.debug else "debug",
        )
        server = uvicorn.Server(config)
        await server.serve()


# ============================================================================
# Utility Functions
# ============================================================================


def create_mcp_server(config: MCPConfig = None) -> MCPServer:
    """Factory function to create MCP server"""
    return MCPServer(config=config)


async def run_server(config: MCPConfig = None) -> None:
    """Run MCP server standalone"""
    server = create_mcp_server(config)
    await server.start()


# ============================================================================
# CLI Entry Point
# ============================================================================


if __name__ == "__main__":
    import sys
    
    # Parse config from environment
    config = MCPConfig(
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "9000")),
        debug=os.environ.get("MCP_DEBUG", "false").lower() == "true",
    )
    
    # Run server
    asyncio.run(run_server(config))
