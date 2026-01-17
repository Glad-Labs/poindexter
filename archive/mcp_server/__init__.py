"""
MCP Server for GLAD Labs AI Orchestration

Model Context Protocol (MCP) server that provides standardized tool access,
resource management, and agent communication for all GLAD Labs agents.

Key Components:
- Tool Registry: Central registry of 35+ tools
- Resource Manager: Access to tasks, models, memory
- Error Handling: Standardized error responses
- Rate Limiting: Per-agent and global rate limits
- External MCP Support: Connect to external MCP servers
"""

from .server import MCPServer, MCPConfig
from .tool_registry import ToolRegistry, ToolDefinition
from .resource_manager import ResourceManager

__version__ = "1.0.0"
__all__ = [
    "MCPServer",
    "MCPConfig",
    "ToolRegistry",
    "ToolDefinition",
    "ResourceManager",
]
