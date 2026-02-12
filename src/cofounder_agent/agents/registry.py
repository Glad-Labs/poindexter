"""Central agent registry for runtime discovery and composition."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Central registry for all available agents.

    Provides:
    - Agent discovery (list all agents, get by name)
    - Agent registration (add/remove agents)
    - Agent querying (by type, capability, phase)
    - Agent metadata and capability lookup
    """

    def __init__(self):
        """Initialize empty agent registry."""
        self._agents: Dict[str, Dict[str, Any]] = {}  # name -> agent metadata
        self._agent_categories: Dict[str, List[str]] = {
            "content": [],
            "financial": [],
            "market": [],
            "compliance": [],
            "utility": [],
        }
        self._agent_phases: Dict[str, List[str]] = {}  # phase -> agent names

    def register(
        self,
        name: str,
        agent_class: Any,
        category: str = "utility",
        phases: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        description: str = "",
        version: str = "1.0",
    ) -> None:
        """
        Register an agent in the registry.

        Args:
            name: Unique agent name
            agent_class: Agent class (not instance)
            category: Agent category for organization
            phases: Pipeline phases this agent handles (e.g., ["research", "draft", "refine"])
            capabilities: Capabilities/skills (e.g., ["web_search", "content_generation"])
            description: Human-readable description
            version: Version string
        """
        if name in self._agents:
            logger.warning(f"Agent '{name}' already registered, overwriting")

        self._agents[name] = {
            "name": name,
            "class": agent_class,
            "category": category,
            "phases": phases or [],
            "capabilities": capabilities or [],
            "description": description,
            "version": version,
        }

        # Index by category
        if category in self._agent_categories:
            if name not in self._agent_categories[category]:
                self._agent_categories[category].append(name)
        else:
            logger.warning(f"Unknown category: {category}")

        # Index by phase
        if phases:
            for phase in phases:
                if phase not in self._agent_phases:
                    self._agent_phases[phase] = []
                if name not in self._agent_phases[phase]:
                    self._agent_phases[phase].append(name)

        logger.info(
            f"Registered agent: {name} v{version} (category: {category}, phases: {phases or 'none'})"
        )

    def get(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """
        Get agent metadata by name.

        Args:
            agent_name: Name of agent to retrieve

        Returns:
            Agent metadata dict or None if not found
        """
        return self._agents.get(agent_name)

    def get_agent_class(self, agent_name: str) -> Optional[Any]:
        """
        Get agent class for instantiation.

        Args:
            agent_name: Name of agent to retrieve

        Returns:
            Agent class or None if not found
        """
        agent = self._agents.get(agent_name)
        return agent["class"] if agent else None

    def list_agents(self, category: Optional[str] = None) -> List[str]:
        """
        List all registered agents.

        Args:
            category: Filter by category (optional)

        Returns:
            List of agent names
        """
        if category:
            return self._agent_categories.get(category, [])
        return list(self._agents.keys())

    def list_by_phase(self, phase: str) -> List[str]:
        """
        List agents that handle a specific phase.

        Args:
            phase: Pipeline phase name (e.g., "research", "draft", "refine")

        Returns:
            List of agent names that handle this phase
        """
        return self._agent_phases.get(phase, [])

    def list_by_capability(self, capability: str) -> List[str]:
        """
        List agents with a specific capability.

        Args:
            capability: Capability name (e.g., "web_search", "content_generation")

        Returns:
            List of agent names with this capability
        """
        return [
            name
            for name, metadata in self._agents.items()
            if capability in metadata.get("capabilities", [])
        ]

    def list_categories(self) -> Dict[str, List[str]]:
        """
        Get all agents organized by category.

        Returns:
            Dictionary of category -> agent names
        """
        return {
            cat: agents
            for cat, agents in self._agent_categories.items()
            if agents  # Only include categories with agents
        }

    def get_metadata(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """
        Get full agent metadata including phases, capabilities, description.

        Args:
            agent_name: Name of agent

        Returns:
            Agent metadata dict or None if not found
        """
        return self._agents.get(agent_name)

    def get_capabilities(self, agent_name: str) -> List[str]:
        """
        Get capabilities for an agent.

        Args:
            agent_name: Name of agent

        Returns:
            List of capabilities
        """
        agent = self._agents.get(agent_name)
        return agent.get("capabilities", []) if agent else []

    def get_phases(self, agent_name: str) -> List[str]:
        """
        Get phases handled by an agent.

        Args:
            agent_name: Name of agent

        Returns:
            List of phases
        """
        agent = self._agents.get(agent_name)
        return agent.get("phases", []) if agent else []

    def get_serializable_metadata(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """
        Get serializable metadata for a single agent (excluding the class object).

        Args:
            agent_name: Name of agent to retrieve

        Returns:
            Agent metadata dict without the agent class, or None if not found
        """
        agent = self.get(agent_name)
        if not agent:
            return None
        
        return {
            "name": agent.get("name"),
            "category": agent.get("category"),
            "phases": agent.get("phases", []),
            "capabilities": agent.get("capabilities", []),
            "description": agent.get("description", ""),
            "version": agent.get("version", "1.0"),
        }

    def list_all_with_metadata(self) -> List[Dict[str, Any]]:
        """
        Get all agents with their full metadata (excluding non-serializable fields).

        Returns:
            List of agent metadata dicts (without the agent class)
        """
        result = []
        for agent_name, agent_data in self._agents.items():
            # Exclude the "class" field which is not JSON serializable
            result.append({
                "name": agent_data.get("name"),
                "category": agent_data.get("category"),
                "phases": agent_data.get("phases", []),
                "capabilities": agent_data.get("capabilities", []),
                "description": agent_data.get("description", ""),
                "version": agent_data.get("version", "1.0"),
            })
        return result

    def __len__(self) -> int:
        """Number of registered agents."""
        return len(self._agents)

    def __contains__(self, agent_name: str) -> bool:
        """Check if agent is registered."""
        return agent_name in self._agents


# Global registry instance
_agent_registry: Optional[AgentRegistry] = None


def get_agent_registry() -> AgentRegistry:
    """Get the global agent registry instance."""
    global _agent_registry
    if _agent_registry is None:
        _agent_registry = AgentRegistry()
    return _agent_registry


def initialize_agent_registry() -> AgentRegistry:
    """
    Initialize the global agent registry.

    Returns:
        Initialized AgentRegistry instance
    """
    global _agent_registry
    _agent_registry = AgentRegistry()
    logger.info("Global agent registry initialized")
    return _agent_registry
