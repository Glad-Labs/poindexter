"""
MCP Server Discovery & Registry Service

Enables Poindexter to discover and use available MCP servers on-the-fly.
Handles caching, capability matching, and fallback logic.
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MCPServer:
    """Represents an available MCP server."""

    name: str
    url: str
    capabilities: List[str]
    cost_per_call: float = 0.0
    avg_latency_ms: float = 0.0
    quality_score: float = 1.0
    is_available: bool = True
    last_checked: Optional[datetime] = None
    auth_required: bool = False
    auth_token: Optional[str] = None

    def to_dict(self):
        """Convert to dict for JSON serialization."""
        data = asdict(self)
        data["last_checked"] = self.last_checked.isoformat() if self.last_checked else None
        return data


class MCPCapabilityRegistry:
    """
    Maps capabilities to available MCP servers.

    Examples of capabilities:
    - "web_search": Google search, Serper API, DuckDuckGo
    - "image_generation": DALL-E, Stable Diffusion, Midjourney
    - "social_media": Twitter API, LinkedIn API, Instagram
    - "sentiment_analysis": HuggingFace, OpenAI Moderation
    - "web_scraping": Puppeteer API, Cheerio
    - "database": PostgreSQL, MongoDB, Firestore
    - "analytics": Google Analytics, Mixpanel, Amplitude
    - "email": SendGrid, Mailgun, AWS SES
    """

    def __init__(self):
        """Initialize with default known MCP servers."""
        self.servers: Dict[str, List[MCPServer]] = {}
        self.cache_ttl = timedelta(hours=1)
        self.last_refresh = None
        self._initialize_known_servers()

    def _initialize_known_servers(self):
        """Set up known MCP servers grouped by capability."""
        known_servers = {
            "web_search": [
                MCPServer(
                    name="serper-api",
                    url="https://serper.dev/api/search",
                    capabilities=["web_search", "news", "trends"],
                    cost_per_call=0.05,
                    avg_latency_ms=2000,
                    quality_score=0.98,
                ),
                MCPServer(
                    name="google-search",
                    url="https://api.google.com/search",
                    capabilities=["web_search", "scholarly"],
                    cost_per_call=0.10,
                    avg_latency_ms=1500,
                    quality_score=0.99,
                    auth_required=True,
                ),
                MCPServer(
                    name="duckduckgo-search",
                    url="https://api.duckduckgo.com",
                    capabilities=["web_search"],
                    cost_per_call=0.0,
                    avg_latency_ms=3000,
                    quality_score=0.85,
                ),
            ],
            "image_generation": [
                MCPServer(
                    name="pexels-api",
                    url="https://api.pexels.com/v1/search",
                    capabilities=["image_search", "stock_photos"],
                    cost_per_call=0.0,
                    avg_latency_ms=1000,
                    quality_score=0.9,
                ),
                MCPServer(
                    name="dall-e",
                    url="https://api.openai.com/v1/images/generations",
                    capabilities=["image_generation", "image_editing"],
                    cost_per_call=0.02,
                    avg_latency_ms=10000,
                    quality_score=0.95,
                    auth_required=True,
                ),
                MCPServer(
                    name="stable-diffusion",
                    url="https://api.stability.ai/v1/generate",
                    capabilities=["image_generation"],
                    cost_per_call=0.015,
                    avg_latency_ms=8000,
                    quality_score=0.92,
                    auth_required=True,
                ),
            ],
            "social_media": [
                MCPServer(
                    name="twitter-api",
                    url="https://api.twitter.com/2",
                    capabilities=["twitter_publish", "tweet_search", "analytics"],
                    cost_per_call=0.0,
                    avg_latency_ms=2000,
                    quality_score=0.95,
                    auth_required=True,
                ),
                MCPServer(
                    name="linkedin-api",
                    url="https://api.linkedin.com/v2",
                    capabilities=["linkedin_publish", "post_analytics"],
                    cost_per_call=0.0,
                    avg_latency_ms=2500,
                    quality_score=0.93,
                    auth_required=True,
                ),
                MCPServer(
                    name="instagram-api",
                    url="https://graph.instagram.com",
                    capabilities=["instagram_publish", "story_analytics"],
                    cost_per_call=0.0,
                    avg_latency_ms=2000,
                    quality_score=0.91,
                    auth_required=True,
                ),
            ],
            "sentiment_analysis": [
                MCPServer(
                    name="huggingface-transformers",
                    url="https://api-inference.huggingface.co/models",
                    capabilities=["sentiment_analysis", "text_classification"],
                    cost_per_call=0.0,
                    avg_latency_ms=3000,
                    quality_score=0.88,
                ),
                MCPServer(
                    name="openai-moderation",
                    url="https://api.openai.com/v1/moderations",
                    capabilities=["content_moderation", "sentiment_analysis"],
                    cost_per_call=0.001,
                    avg_latency_ms=500,
                    quality_score=0.96,
                    auth_required=True,
                ),
            ],
            "web_scraping": [
                MCPServer(
                    name="puppeteer-api",
                    url="https://api.puppeteer.dev",
                    capabilities=["web_scraping", "page_capture", "javascript_execution"],
                    cost_per_call=0.01,
                    avg_latency_ms=5000,
                    quality_score=0.95,
                ),
                MCPServer(
                    name="cheerio-api",
                    url="https://api.cheerio.dev",
                    capabilities=["html_parsing", "web_scraping"],
                    cost_per_call=0.005,
                    avg_latency_ms=1000,
                    quality_score=0.90,
                ),
            ],
            "analytics": [
                MCPServer(
                    name="google-analytics",
                    url="https://analyticsreporting.googleapis.com/v4",
                    capabilities=["analytics", "traffic_analysis", "user_behavior"],
                    cost_per_call=0.0,
                    avg_latency_ms=2000,
                    quality_score=0.97,
                    auth_required=True,
                ),
                MCPServer(
                    name="mixpanel-api",
                    url="https://api.mixpanel.com",
                    capabilities=["event_tracking", "user_analytics"],
                    cost_per_call=0.0,
                    avg_latency_ms=1500,
                    quality_score=0.94,
                    auth_required=True,
                ),
            ],
            "database": [
                MCPServer(
                    name="postgresql",
                    url="postgresql://localhost:5432",
                    capabilities=["database_query", "data_retrieval", "transactions"],
                    cost_per_call=0.0,
                    avg_latency_ms=100,
                    quality_score=1.0,
                ),
                MCPServer(
                    name="firestore",
                    url="https://firestore.googleapis.com",
                    capabilities=["database_query", "realtime", "cloud_storage"],
                    cost_per_call=0.0,
                    avg_latency_ms=500,
                    quality_score=0.98,
                    auth_required=True,
                ),
            ],
            "email": [
                MCPServer(
                    name="sendgrid",
                    url="https://api.sendgrid.com/v3",
                    capabilities=["email_send", "template_management"],
                    cost_per_call=0.0001,
                    avg_latency_ms=1000,
                    quality_score=0.99,
                    auth_required=True,
                ),
                MCPServer(
                    name="mailgun",
                    url="https://api.mailgun.net/v3",
                    capabilities=["email_send", "event_tracking"],
                    cost_per_call=0.0001,
                    avg_latency_ms=800,
                    quality_score=0.98,
                    auth_required=True,
                ),
            ],
        }

        # Flatten into servers dict
        for capability, server_list in known_servers.items():
            if capability not in self.servers:
                self.servers[capability] = []
            self.servers[capability].extend(server_list)

    async def discover_by_capability(self, capability: str) -> List[MCPServer]:
        """
        Find all available MCP servers for a capability.

        Args:
            capability: What capability needed (e.g., "web_search", "image_generation")

        Returns:
            List of available MCPServer instances, sorted by quality/cost
        """
        if capability not in self.servers:
            logger.warning(f"No MCP servers found for capability: {capability}")
            return []

        servers = self.servers[capability]

        # Filter to available servers
        available = [s for s in servers if s.is_available]

        # Sort by quality/cost ratio (higher quality, lower cost = better)
        available.sort(key=lambda s: (s.quality_score / max(s.cost_per_call, 0.001)), reverse=True)

        return available

    async def discover_all_capabilities(self) -> Dict[str, List[str]]:
        """
        Get all available capabilities and their servers.

        Returns:
            {
                "web_search": ["serper-api", "google-search", "duckduckgo-search"],
                "image_generation": ["pexels-api", "dall-e", "stable-diffusion"],
                ...
            }
        """
        result = {}
        for capability, servers in self.servers.items():
            result[capability] = [s.name for s in servers if s.is_available]
        return result

    async def health_check_server(self, server: MCPServer) -> Tuple[bool, Optional[str]]:
        """
        Check if MCP server is alive and responsive.

        Returns:
            (is_available, error_message)
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{server.url}/health", follow_redirects=True)
                is_ok = response.status_code in [200, 204, 404]  # 404 is OK if server responds

                if is_ok:
                    server.is_available = True
                    server.last_checked = datetime.now()
                    return True, None
                else:
                    return False, f"HTTP {response.status_code}"
        except Exception as e:
            error_msg = f"Health check failed: {str(e)}"
            server.is_available = False
            server.last_checked = datetime.now()
            return False, error_msg

    async def refresh_servers(self):
        """
        Periodically check health of known servers.
        Run this in background to keep availability status fresh.
        """
        all_servers = []
        for servers_list in self.servers.values():
            all_servers.extend(servers_list)

        tasks = [self.health_check_server(server) for server in all_servers]
        await asyncio.gather(*tasks, return_exceptions=True)

        self.last_refresh = datetime.now()
        logger.info(f"Refreshed {len(all_servers)} MCP servers")


class MCPServerClient:
    """
    Client for calling MCP servers.
    Handles authentication, error recovery, and response parsing.
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self.headers = {}

    async def call_server(
        self,
        server: MCPServer,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_body: Optional[Dict] = None,
    ) -> Tuple[bool, Any, Optional[str]]:
        """
        Call an MCP server method.

        Args:
            server: MCPServer to call
            method: HTTP method (GET, POST, etc.)
            endpoint: Endpoint path
            params: Query parameters
            json_body: JSON request body

        Returns:
            (success, result, error_message)
        """
        try:
            url = f"{server.url}/{endpoint.lstrip('/')}"

            # Add authentication if needed
            headers = self.headers.copy()
            if server.auth_required and server.auth_token:
                headers["Authorization"] = f"Bearer {server.auth_token}"

            # Make request
            if method.upper() == "GET":
                response = await self.client.get(url, params=params, headers=headers)
            elif method.upper() == "POST":
                response = await self.client.post(
                    url, json=json_body, params=params, headers=headers
                )
            else:
                return False, None, f"Unsupported HTTP method: {method}"

            if response.status_code == 200:
                return True, response.json(), None
            else:
                return False, None, f"HTTP {response.status_code}: {response.text[:200]}"

        except asyncio.TimeoutError:
            return False, None, "Request timeout"
        except Exception as e:
            return False, None, str(e)


class Poindexter_MCPIntegration:
    """
    Main integration point: Makes MCP discovery available to Poindexter agent.
    """

    def __init__(self):
        self.registry = MCPCapabilityRegistry()
        self.client = MCPServerClient()

    async def initialize(self):
        """Initialize and perform first health checks."""
        await self.registry.refresh_servers()

    async def discover_servers_tool(self, capability: str) -> List[Dict]:
        """
        Tool that Poindexter can call to discover MCP servers.

        Example:
        Poindexter: "I need web_search capability"
        Tool output: [
            {
                "name": "serper-api",
                "cost_per_call": 0.05,
                "quality_score": 0.98,
                "latency": 2000
            },
            ...
        ]

        Args:
            capability: Capability needed (e.g., "web_search", "image_generation")

        Returns:
            List of available servers with metadata
        """
        servers = await self.registry.discover_by_capability(capability)

        return [
            {
                "name": s.name,
                "url": s.url,
                "capabilities": s.capabilities,
                "cost_per_call": s.cost_per_call,
                "quality_score": s.quality_score,
                "avg_latency_ms": s.avg_latency_ms,
                "is_available": s.is_available,
            }
            for s in servers
        ]

    async def call_mcp_server_tool(
        self, server_name: str, method: str, endpoint: str, params: Optional[Dict] = None
    ) -> Dict:
        """
        Tool that Poindexter can use to call discovered MCP servers.

        Example:
        Poindexter selects "serper-api" for web search
        Calls this tool with:
            server_name="serper-api"
            method="POST"
            endpoint="/search"
            params={"q": "AI trends 2025"}

        Returns:
            Search results from Serper API
        """
        # Find the server
        all_servers = []
        for servers_list in self.registry.servers.values():
            all_servers.extend(servers_list)

        server = next((s for s in all_servers if s.name == server_name), None)
        if not server:
            return {"success": False, "error": f"Unknown server: {server_name}"}

        # Call the server
        success, result, error = await self.client.call_server(server, method, endpoint, params)

        return {
            "success": success,
            "result": result,
            "error": error,
            "server": server_name,
            "cost": server.cost_per_call,
        }


# Example usage
if __name__ == "__main__":

    async def main():
        # Initialize MCP discovery
        mcp = Poindexter_MCPIntegration()
        await mcp.initialize()

        # Discover web search servers
        print("Available web search servers:")
        servers = await mcp.discover_servers_tool("web_search")
        for server in servers:
            print(
                f"  - {server['name']}: ${server['cost_per_call']}/call, quality={server['quality_score']}"
            )

        # Discover all capabilities
        print("\nAll available capabilities:")
        capabilities = await mcp.registry.discover_all_capabilities()
        for cap, servers in capabilities.items():
            print(f"  {cap}: {', '.join(servers)}")

    asyncio.run(main())
