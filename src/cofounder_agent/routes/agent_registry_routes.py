"""
Agent Registry Routes - REST API for agent discovery and metadata

Exposes the AgentRegistry via HTTP for dynamic agent discovery, capability querying,
and runtime agent selection based on workflow requirements.

This follows the same pattern as service_registry_routes.py but for agents instead of services.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from agents.registry import get_agent_registry

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/agents",
    tags=["agents"],
    responses={404: {"description": "Agent not found"}},
)


@router.get("/registry", response_model=Dict[str, Any], name="Get Agent Registry")
async def get_agent_registry_endpoint():
    """
    Get the complete agent registry with all agents and their metadata.

    This is the primary endpoint for discovering all available agents and their capabilities.
    Useful for UI dashboards, LLM prompting systems, and workflow composition.

    Returns:
        Dict with:
        - agents: List of all registered agents with full metadata
        - total_agents: Count of registered agents
        - categories: Agent categories and their agents
        - phases: Pipeline phases and agents that handle them

    Example:
        ```
        GET /api/agents/registry

        {
            "agents": [
                {
                    "name": "research_agent",
                    "category": "content",
                    "phases": ["research"],
                    "capabilities": ["web_search", "information_gathering", "summarization"],
                    "description": "Gathers research and background information for content generation",
                    "version": "1.0"
                },
                ...
            ],
            "total_agents": 9,
            "categories": {
                "content": ["research_agent", "creative_agent", "qa_agent", ...],
                "financial": ["financial_agent"],
                ...
            },
            "phases": {
                "research": ["research_agent"],
                "draft": ["creative_agent"],
                ...
            }
        }
        ```
    """
    try:
        registry = get_agent_registry()

        # Get all agents with metadata (now clean and serializable)
        agents = registry.list_all_with_metadata()

        # Build category index
        categories = {}
        for agent_metadata in agents:
            category = agent_metadata.get("category", "utility")
            if category not in categories:
                categories[category] = []
            categories[category].append(agent_metadata["name"])

        # Build phase index
        phases = {}
        for agent_metadata in agents:
            agent_phases = agent_metadata.get("phases", [])
            for phase in agent_phases:
                if phase not in phases:
                    phases[phase] = []
                phases[phase].append(agent_metadata["name"])

        return {
            "agents": agents,
            "total_agents": len(agents),
            "categories": categories,
            "phases": phases,
        }
    except Exception as e:
        logger.error(f"Error retrieving agent registry: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve agent registry: {str(e)}")


@router.get("/list", response_model=List[str], name="List Agent Names")
async def list_agents():
    """
    Get a simple list of all available agent names.

    Returns:
        List of agent names (strings)

    Example:
        ```
        GET /api/agents/list

        [
            "research_agent",
            "creative_agent",
            "qa_agent",
            "image_agent",
            "publishing_agent",
            "financial_agent",
            "market_insight_agent",
            "compliance_agent"
        ]
        ```
    """
    try:
        registry = get_agent_registry()
        agents = registry.list_agents()
        return agents
    except Exception as e:
        logger.error(f"Error listing agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list agents: {str(e)}")


@router.get("/{agent_name}", response_model=Dict[str, Any], name="Get Agent Metadata")
async def get_agent_metadata(agent_name: str):
    """
    Get metadata for a specific agent.

    Args:
        agent_name: Name of the agent to retrieve

    Returns:
        Dict with agent metadata:
        - name: Agent name
        - category: Agent category
        - phases: Phases this agent handles
        - capabilities: Agent capabilities
        - description: Human-readable description
        - version: Version string

    Example:
        ```
        GET /api/agents/creative_agent

        {
            "name": "creative_agent",
            "category": "content",
            "phases": ["draft", "refine"],
            "capabilities": ["content_generation", "writing", "style_adaptation"],
            "description": "Generates and refines creative content with style guidance",
            "version": "1.0"
        }
        ```

    Errors:
        - 404: Agent not found
    """
    try:
        registry = get_agent_registry()
        agent = registry.get_serializable_metadata(agent_name)

        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        return agent
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving agent '{agent_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve agent: {str(e)}")


@router.get("/{agent_name}/phases", response_model=List[str], name="Get Agent Phases")
async def get_agent_phases(agent_name: str):
    """
    Get the pipeline phases that an agent handles.

    Args:
        agent_name: Name of the agent

    Returns:
        List of phase names (strings)

    Example:
        ```
        GET /api/agents/creative_agent/phases

        ["draft", "refine"]
        ```

    Errors:
        - 404: Agent not found
    """
    try:
        registry = get_agent_registry()
        phases = registry.get_phases(agent_name)

        if phases is None:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        return phases
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving phases for agent '{agent_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve agent phases: {str(e)}")


@router.get("/{agent_name}/capabilities", response_model=List[str], name="Get Agent Capabilities")
async def get_agent_capabilities(agent_name: str):
    """
    Get the capabilities/skills of an agent.

    Args:
        agent_name: Name of the agent

    Returns:
        List of capability names (strings)

    Example:
        ```
        GET /api/agents/creative_agent/capabilities

        ["content_generation", "writing", "style_adaptation"]
        ```

    Errors:
        - 404: Agent not found
    """
    try:
        registry = get_agent_registry()
        capabilities = registry.get_capabilities(agent_name)

        if capabilities is None:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        return capabilities
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving capabilities for agent '{agent_name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve agent capabilities: {str(e)}")


@router.get("/by-phase/{phase}", name="Get Agents by Phase")
async def get_agents_by_phase(phase: str):
    """
    Get all agents that handle a specific pipeline phase.

    This is useful for workflow composition - when you need an agent for a specific phase,
    ask this endpoint for available options.

    Args:
        phase: Pipeline phase name (e.g., "research", "draft", "refine", "assess", "image_selection")

    Returns:
        List of agent metadata dicts for agents handling this phase

    Example:
        ```
        GET /api/agents/by-phase/draft

        [
            {
                "name": "creative_agent",
                "category": "content",
                "phases": ["draft", "refine"],
                "capabilities": ["content_generation", "writing", "style_adaptation"],
                "description": "Generates and refines creative content with style guidance",
                "version": "1.0"
            }
        ]
        ```
    """
    try:
        registry = get_agent_registry()
        agent_names = registry.list_by_phase(phase)
        
        # Get full metadata for each agent
        agents = []
        for name in agent_names:
            metadata = registry.get_serializable_metadata(name)
            if metadata:
                agents.append(metadata)
        
        return agents
    except Exception as e:
        logger.error(f"Error retrieving agents for phase '{phase}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve agents for phase: {str(e)}")


@router.get("/by-capability/{capability}", name="Get Agents by Capability")
async def get_agents_by_capability(capability: str):
    """
    Get all agents that have a specific capability.

    This is useful for capability-based agent selection in workflows.

    Args:
        capability: Capability name (e.g., "web_search", "content_generation", "image_search")

    Returns:
        List of agent metadata dicts for agents with this capability

    Example:
        ```
        GET /api/agents/by-capability/web_search

        [
            {
                "name": "research_agent",
                "category": "content",
                "phases": ["research"],
                "capabilities": ["web_search", "information_gathering", "summarization"],
                "description": "Gathers research and background information for content generation",
                "version": "1.0"
            }
        ]
        ```
    """
    try:
        registry = get_agent_registry()
        agent_names = registry.list_by_capability(capability)
        
        # Convert agent names to serializable metadata
        agents = []
        for name in agent_names:
            metadata = registry.get_serializable_metadata(name)
            if metadata:
                agents.append(metadata)
        
        return agents
    except Exception as e:
        logger.error(f"Error retrieving agents for capability '{capability}': {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve agents for capability: {str(e)}"
        )


@router.get("/by-category/{category}", response_model=List[Dict[str, Any]], name="Get Agents by Category")
async def get_agents_by_category(category: str):
    """
    Get all agents in a specific category.

    Args:
        category: Category name (e.g., "content", "financial", "market", "compliance")

    Returns:
        List of agent metadata dicts for agents in this category

    Example:
        ```
        GET /api/agents/by-category/content

        [
            {
                "name": "research_agent",
                "category": "content",
                "phases": ["research"],
                "capabilities": ["web_search", "information_gathering", "summarization"],
                "description": "Gathers research and background information for content generation",
                "version": "1.0"
            },
            ...
        ]
        ```
    """
    try:
        registry = get_agent_registry()
        agent_names = registry.list_categories().get(category, [])
        
        # Convert agent names to serializable metadata
        agents = []
        for name in agent_names:
            metadata = registry.get_serializable_metadata(name)
            if metadata:
                agents.append(metadata)
        
        return agents
    except Exception as e:
        logger.error(f"Error retrieving agents in category '{category}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve agents for category: {str(e)}")


@router.get("/search", response_model=List[Dict[str, Any]], name="Search Agents")
async def search_agents(
    capability: Optional[str] = Query(None, description="Filter by capability"),
    phase: Optional[str] = Query(None, description="Filter by phase"),
    category: Optional[str] = Query(None, description="Filter by category"),
):
    """
    Search for agents by optional filters.

    Allows combining multiple filters (AND logic) to find agents matching specific criteria.

    Query Parameters:
        capability: Optional capability to filter by
        phase: Optional phase to filter by
        category: Optional category to filter by

    Returns:
        List of agent metadata dicts matching all specified filters

    Example:
        ```
        GET /api/agents/search?phase=draft&category=content

        [
            {
                "name": "creative_agent",
                "category": "content",
                "phases": ["draft", "refine"],
                "capabilities": ["content_generation", "writing", "style_adaptation"],
                "description": "Generates and refines creative content with style guidance",
                "version": "1.0"
            }
        ]
        ```
    """
    try:
        registry = get_agent_registry()
        all_agents = registry.list_all_with_metadata()

        # Filter by capability if specified
        if capability:
            all_agents = [
                a for a in all_agents if capability in a.get("capabilities", [])
            ]

        # Filter by phase if specified
        if phase:
            all_agents = [a for a in all_agents if phase in a.get("phases", [])]

        # Filter by category if specified
        if category:
            all_agents = [a for a in all_agents if a.get("category") == category]

        return all_agents
    except Exception as e:
        logger.error(f"Error searching agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to search agents: {str(e)}")
