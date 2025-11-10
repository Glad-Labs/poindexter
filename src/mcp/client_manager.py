"""
MCP Client Manager for Glad Labs

Manages connections to multiple MCP servers and provides a unified interface
for discovering and using tools and resources across all connected servers.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Union
import json


class MCPClient:
    """
    Individual MCP client for connecting to a specific server.
    
    This is a simplified implementation that will be replaced with
    the actual MCP Python SDK when it becomes available.
    """
    
    def __init__(self, server_name: str, server_instance: Any):
        self.server_name = server_name
        self.server_instance = server_instance
        self.logger = logging.getLogger(f"mcp.client.{server_name}")
        self._capabilities = {}
        self._tools = {}
        self._resources = {}
    
    async def initialize(self) -> bool:
        """Initialize connection and discover capabilities"""
        try:
            # Discover tools
            if hasattr(self.server_instance, '_tools'):
                self._tools = getattr(self.server_instance, '_tools', {})
            
            # Discover resources  
            if hasattr(self.server_instance, '_resources'):
                self._resources = getattr(self.server_instance, '_resources', {})
            
            self._capabilities = {
                "tools": list(self._tools.keys()),
                "resources": list(self._resources.keys()),
                "server_name": self.server_name
            }
            
            self.logger.info(f"Initialized MCP client for {self.server_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize MCP client: {e}")
            return False
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """List available tools"""
        tools = []
        
        # Get tools from server instance if available
        if hasattr(self.server_instance, 'STRAPI_TOOLS'):
            tools.extend(getattr(self.server_instance, 'STRAPI_TOOLS', []))
        
        return tools
    
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool on the server"""
        try:
            # Find the method on the server instance
            method = getattr(self.server_instance, name, None)
            if not method:
                return {"error": f"Tool '{name}' not found"}
            
            # Call the method with arguments
            result = await method(**arguments)
            return result
            
        except Exception as e:
            self.logger.error(f"Error calling tool {name}: {e}")
            return {"error": str(e)}
    
    async def list_resources(self) -> List[Dict[str, Any]]:
        """List available resources"""
        resources = []
        
        # Get resources from server instance if available
        if hasattr(self.server_instance, 'STRAPI_RESOURCES'):
            resources.extend(getattr(self.server_instance, 'STRAPI_RESOURCES', []))
        
        return resources
    
    async def read_resource(self, uri: str) -> Dict[str, Any]:
        """Read a resource by URI"""
        try:
            # Extract resource name from URI
            resource_name = uri.split("/")[-1].replace("-", "_")
            
            # Find the method on the server instance
            method = getattr(self.server_instance, f"get_{resource_name}", None)
            if not method:
                return {"error": f"Resource '{uri}' not found"}
            
            # Call the method
            result = await method()
            return {"data": result}
            
        except Exception as e:
            self.logger.error(f"Error reading resource {uri}: {e}")
            return {"error": str(e)}
    
    def get_capabilities(self) -> Dict[str, Any]:
        """Get client capabilities"""
        return self._capabilities


class MCPClientManager:
    """
    Manages multiple MCP clients and provides unified access to tools and resources.
    
    This manager allows agents to discover and use capabilities from multiple
    MCP servers without needing to know which server provides what functionality.
    """
    
    def __init__(self):
        self.clients: Dict[str, MCPClient] = {}
        self.logger = logging.getLogger("mcp.client_manager")
        self._tool_registry: Dict[str, str] = {}  # tool_name -> server_name
        self._resource_registry: Dict[str, str] = {}  # resource_uri -> server_name
    
    async def register_server(self, server_name: str, server_instance: Any) -> bool:
        """
        Register a new MCP server.
        
        Args:
            server_name: Unique name for the server
            server_instance: Server instance to connect to
            
        Returns:
            True if registration successful
        """
        try:
            client = MCPClient(server_name, server_instance)
            success = await client.initialize()
            
            if success:
                self.clients[server_name] = client
                await self._update_registries()
                self.logger.info(f"Registered MCP server: {server_name}")
                return True
            else:
                self.logger.error(f"Failed to register server: {server_name}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error registering server {server_name}: {e}")
            return False
    
    async def _update_registries(self):
        """Update tool and resource registries"""
        self._tool_registry.clear()
        self._resource_registry.clear()
        
        for server_name, client in self.clients.items():
            # Register tools
            tools = await client.list_tools()
            for tool in tools:
                tool_name = tool.get("name")
                if tool_name:
                    self._tool_registry[tool_name] = server_name
            
            # Register resources
            resources = await client.list_resources()
            for resource in resources:
                resource_uri = resource.get("uri")
                if resource_uri:
                    self._resource_registry[resource_uri] = server_name
    
    async def list_all_tools(self) -> Dict[str, List[Dict[str, Any]]]:
        """List tools from all connected servers"""
        all_tools = {}
        
        for server_name, client in self.clients.items():
            try:
                tools = await client.list_tools()
                all_tools[server_name] = tools
            except Exception as e:
                self.logger.error(f"Error listing tools from {server_name}: {e}")
                all_tools[server_name] = []
        
        return all_tools
    
    async def list_all_resources(self) -> Dict[str, List[Dict[str, Any]]]:
        """List resources from all connected servers"""
        all_resources = {}
        
        for server_name, client in self.clients.items():
            try:
                resources = await client.list_resources()
                all_resources[server_name] = resources
            except Exception as e:
                self.logger.error(f"Error listing resources from {server_name}: {e}")
                all_resources[server_name] = []
        
        return all_resources
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call a tool by name, automatically routing to the correct server.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments
            
        Returns:
            Tool execution result
        """
        # Find which server provides this tool
        server_name = self._tool_registry.get(tool_name)
        if not server_name:
            return {
                "error": f"Tool '{tool_name}' not found in any registered server",
                "available_tools": list(self._tool_registry.keys())
            }
        
        # Get the client
        client = self.clients.get(server_name)
        if not client:
            return {"error": f"Server '{server_name}' not available"}
        
        # Call the tool
        self.logger.info(f"Calling tool '{tool_name}' on server '{server_name}'")
        return await client.call_tool(tool_name, arguments)
    
    async def read_resource(self, resource_uri: str) -> Dict[str, Any]:
        """
        Read a resource by URI, automatically routing to the correct server.
        
        Args:
            resource_uri: URI of the resource to read
            
        Returns:
            Resource data
        """
        # Find which server provides this resource
        server_name = self._resource_registry.get(resource_uri)
        if not server_name:
            return {
                "error": f"Resource '{resource_uri}' not found in any registered server",
                "available_resources": list(self._resource_registry.keys())
            }
        
        # Get the client
        client = self.clients.get(server_name)
        if not client:
            return {"error": f"Server '{server_name}' not available"}
        
        # Read the resource
        self.logger.info(f"Reading resource '{resource_uri}' from server '{server_name}'")
        return await client.read_resource(resource_uri)
    
    async def get_server_status(self) -> Dict[str, Any]:
        """Get status of all connected servers"""
        status = {
            "total_servers": len(self.clients),
            "servers": {},
            "total_tools": len(self._tool_registry),
            "total_resources": len(self._resource_registry)
        }
        
        for server_name, client in self.clients.items():
            try:
                capabilities = client.get_capabilities()
                status["servers"][server_name] = {
                    "status": "connected",
                    "capabilities": capabilities
                }
            except Exception as e:
                status["servers"][server_name] = {
                    "status": "error",
                    "error": str(e)
                }
        
        return status
    
    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific tool"""
        server_name = self._tool_registry.get(tool_name)
        if server_name:
            return {
                "tool_name": tool_name,
                "server_name": server_name,
                "server_available": server_name in self.clients
            }
        return None
    
    async def discover_capabilities(self) -> Dict[str, Any]:
        """
        Discover all capabilities across connected servers.
        
        Returns:
            Comprehensive capability map
        """
        capabilities = {
            "servers": {},
            "tools_by_category": {},
            "resources_by_type": {},
            "total_capabilities": 0
        }
        
        all_tools = await self.list_all_tools()
        all_resources = await self.list_all_resources()
        
        for server_name in self.clients.keys():
            server_tools = all_tools.get(server_name, [])
            server_resources = all_resources.get(server_name, [])
            
            capabilities["servers"][server_name] = {
                "tools": len(server_tools),
                "resources": len(server_resources),
                "tool_names": [tool.get("name") for tool in server_tools],
                "resource_uris": [res.get("uri") for res in server_resources]
            }
            
            capabilities["total_capabilities"] += len(server_tools) + len(server_resources)
        
        return capabilities


# Example usage and testing
async def main():
    """
    Test the MCP Client Manager
    """
    logging.basicConfig(level=logging.INFO)
    
    manager = MCPClientManager()
    
    # Import and register servers
    try:
        from .servers.ai_model_server import AIModelServer
        from .servers.strapi_server import StrapiMCPServer
        
        # Register AI Model Server
        ai_server = AIModelServer()
        await manager.register_server("ai-models", ai_server)
        
        # Register Strapi Server  
        strapi_server = StrapiMCPServer()
        await manager.register_server("strapi-cms", strapi_server)
        
        print("=== MCP Client Manager Test ===")
        
        # Show server status
        print("\n1. Server Status:")
        status = await manager.get_server_status()
        print(json.dumps(status, indent=2))
        
        # Discover capabilities
        print("\n2. Discovered Capabilities:")
        capabilities = await manager.discover_capabilities()
        print(json.dumps(capabilities, indent=2))
        
        # Test tool call
        print("\n3. Test Tool Call - Get Content Stats:")
        result = await manager.call_tool("get_content_stats", {})
        print(json.dumps(result, indent=2))
        
    except ImportError as e:
        print(f"Could not import servers for testing: {e}")
        print("This is expected until the packages are installed.")


if __name__ == "__main__":
    asyncio.run(main())