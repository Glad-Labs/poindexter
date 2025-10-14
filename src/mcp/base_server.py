"""
Base MCP Server Class for GLAD Labs

Provides a foundation for creating MCP servers with standardized
tool registration, error handling, and logging.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Callable, Awaitable
from mcp.server.models import InitializeRequest
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, Tool


class BaseMCPServer:
    """
    Base class for all GLAD Labs MCP servers.
    
    Provides standardized server setup, tool registration,
    and common functionality for all MCP servers.
    """
    
    def __init__(self, name: str, version: str = "1.0.0"):
        """
        Initialize the base MCP server.
        
        Args:
            name: Server name for identification
            version: Server version
        """
        self.name = name
        self.version = version
        self.server = Server(name)
        self.logger = logging.getLogger(f"mcp.{name}")
        
        # Tool and resource registries
        self._tools: Dict[str, Callable] = {}
        self._resources: Dict[str, Callable] = {}
        
        # Setup base handlers
        self._setup_base_handlers()
    
    def _setup_base_handlers(self):
        """Setup base MCP handlers common to all servers"""
        
        @self.server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            """List all available tools"""
            tools = []
            for tool_name, _ in self._tools.items():
                tool_info = await self._get_tool_info(tool_name)
                if tool_info:
                    tools.append(tool_info)
            return tools
        
        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
            """Execute a tool with given arguments"""
            if name not in self._tools:
                raise ValueError(f"Tool '{name}' not found")
            
            try:
                self.logger.info(f"Executing tool: {name} with arguments: {arguments}")
                result = await self._tools[name](**arguments)
                
                # Ensure result is in proper format
                if isinstance(result, str):
                    return [{"type": "text", "text": result}]
                elif isinstance(result, dict):
                    return [{"type": "text", "text": str(result)}]
                elif isinstance(result, list):
                    return result
                else:
                    return [{"type": "text", "text": str(result)}]
                    
            except Exception as e:
                self.logger.error(f"Error executing tool {name}: {e}")
                return [{"type": "text", "text": f"Error: {str(e)}"}]
        
        @self.server.list_resources()
        async def handle_list_resources() -> List[Resource]:
            """List all available resources"""
            resources = []
            for resource_name, _ in self._resources.items():
                resource_info = await self._get_resource_info(resource_name)
                if resource_info:
                    resources.append(resource_info)
            return resources
        
        @self.server.read_resource()
        async def handle_read_resource(uri: str) -> Dict[str, Any]:
            """Read a resource by URI"""
            # Extract resource name from URI
            resource_name = uri.split("/")[-1]
            
            if resource_name not in self._resources:
                raise ValueError(f"Resource '{resource_name}' not found")
            
            try:
                self.logger.info(f"Reading resource: {resource_name}")
                result = await self._resources[resource_name]()
                
                return {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": str(result)
                        }
                    ]
                }
                    
            except Exception as e:
                self.logger.error(f"Error reading resource {resource_name}: {e}")
                raise
    
    def register_tool(self, name: str, description: str, input_schema: Dict[str, Any], 
                     handler: Callable[..., Awaitable[Any]]):
        """
        Register a tool with the server.
        
        Args:
            name: Tool name
            description: Tool description
            input_schema: JSON schema for tool inputs
            handler: Async function to handle tool calls
        """
        self._tools[name] = handler
        
        # Store tool metadata for list_tools
        if not hasattr(self, '_tool_metadata'):
            self._tool_metadata = {}
        
        self._tool_metadata[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema
        }
        
        self.logger.info(f"Registered tool: {name}")
    
    def register_resource(self, name: str, description: str, uri: str,
                         handler: Callable[[], Awaitable[Any]]):
        """
        Register a resource with the server.
        
        Args:
            name: Resource name
            description: Resource description  
            uri: Resource URI
            handler: Async function to provide resource data
        """
        self._resources[name] = handler
        
        # Store resource metadata
        if not hasattr(self, '_resource_metadata'):
            self._resource_metadata = {}
            
        self._resource_metadata[name] = {
            "uri": uri,
            "name": name,
            "description": description,
            "mimeType": "application/json"
        }
        
        self.logger.info(f"Registered resource: {name}")
    
    async def _get_tool_info(self, tool_name: str) -> Optional[Tool]:
        """Get tool information for list_tools response"""
        if hasattr(self, '_tool_metadata') and tool_name in self._tool_metadata:
            metadata = self._tool_metadata[tool_name]
            return Tool(
                name=metadata["name"],
                description=metadata["description"],
                inputSchema=metadata["inputSchema"]
            )
        return None
    
    async def _get_resource_info(self, resource_name: str) -> Optional[Resource]:
        """Get resource information for list_resources response"""
        if hasattr(self, '_resource_metadata') and resource_name in self._resource_metadata:
            metadata = self._resource_metadata[resource_name]
            return Resource(
                uri=metadata["uri"],
                name=metadata["name"],
                description=metadata["description"],
                mimeType=metadata["mimeType"]
            )
        return None
    
    async def setup(self):
        """
        Setup method to be overridden by subclasses.
        Used to register tools and resources.
        """
        pass
    
    async def cleanup(self):
        """
        Cleanup method to be overridden by subclasses.
        Called when server shuts down.
        """
        pass
    
    async def run(self):
        """Run the MCP server"""
        try:
            self.logger.info(f"Starting MCP server: {self.name} v{self.version}")
            await self.setup()
            
            async with stdio_server() as (read_stream, write_stream):
                await self.server.run(
                    read_stream,
                    write_stream,
                    InitializeRequest(
                        protocolVersion="2024-11-05",
                        capabilities=self.server.get_capabilities(
                            notification_options=NotificationOptions(),
                            experimental_capabilities={}
                        ),
                        clientInfo={"name": f"{self.name}-server", "version": self.version}
                    )
                )
        except KeyboardInterrupt:
            self.logger.info("Server shutdown requested")
        except Exception as e:
            self.logger.error(f"Server error: {e}")
        finally:
            await self.cleanup()


def create_server_main(server_class, server_name: str):
    """
    Create a main function for running an MCP server.
    
    Args:
        server_class: The server class to instantiate
        server_name: Name for the server instance
    
    Returns:
        Main function that can be used as script entry point
    """
    async def main():
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Create and run server
        server = server_class(server_name)
        await server.run()
    
    return main