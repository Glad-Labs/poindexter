"""Central agent registry for runtime discovery and composition."""

from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


class AgentRegistry:
    """Central registry for agent discovery, registration, and querying."""

    def __init__(self):
        """Initialize empty agent registry."""
        self._agents: dict[str, dict[str, Any]] = {}
        self._agent_categories: dict[str, list[str]] = {
            "content": [],
            "utility": [],
        }
        self._agent_phases: dict[str, list[str]] = {}

    def register(
        self,
        name: str,
        agent_class: Any,
        category: str = "utility",
        phases: list[str] | None = None,
        capabilities: list[str] | None = None,
        description: str = "",
        version: str = "1.0",
    ) -> None:
        """Register an agent in the registry."""
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

        if category in self._agent_categories:
            if name not in self._agent_categories[category]:
                self._agent_categories[category].append(name)
        else:
            logger.warning(f"Unknown category: {category}")

        if phases:
            for phase in phases:
                if phase not in self._agent_phases:
                    self._agent_phases[phase] = []
                if name not in self._agent_phases[phase]:
                    self._agent_phases[phase].append(name)

        logger.info(
            f"Registered agent: {name} v{version} (category: {category}, phases: {phases or 'none'})"
        )

    def get(self, agent_name: str) -> dict[str, Any] | None:
        """Get agent metadata by name, or None."""
        return self._agents.get(agent_name)

    def get_agent_class(self, agent_name: str) -> Any | None:
        """Get agent class for instantiation, or None."""
        agent = self._agents.get(agent_name)
        return agent["class"] if agent else None

    def list_agents(self, category: str | None = None) -> list[str]:
        """List registered agent names, optionally filtered by category."""
        if category:
            return self._agent_categories.get(category, [])
        return list(self._agents.keys())

    def list_by_phase(self, phase: str) -> list[str]:
        """List agent names that handle a specific pipeline phase."""
        return self._agent_phases.get(phase, [])

    def list_by_capability(self, capability: str) -> list[str]:
        """List agent names with a specific capability."""
        return [
            name
            for name, metadata in self._agents.items()
            if capability in metadata.get("capabilities", [])
        ]

    def list_categories(self) -> dict[str, list[str]]:
        """Get all non-empty categories with their agent names."""
        return {
            cat: agents
            for cat, agents in self._agent_categories.items()
            if agents
        }

    def get_metadata(self, agent_name: str) -> dict[str, Any] | None:
        """Alias for get() -- returns full agent metadata."""
        return self._agents.get(agent_name)

    def get_capabilities(self, agent_name: str) -> list[str]:
        """Get capabilities list for an agent."""
        agent = self._agents.get(agent_name)
        return agent.get("capabilities", []) if agent else []

    def get_phases(self, agent_name: str) -> list[str]:
        """Get phases handled by an agent."""
        agent = self._agents.get(agent_name)
        return agent.get("phases", []) if agent else []

    def get_serializable_metadata(self, agent_name: str) -> dict[str, Any] | None:
        """Get metadata for a single agent, excluding the class object."""
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

    def list_all_with_metadata(self) -> list[dict[str, Any]]:
        """Get all agents with serializable metadata (no class objects)."""
        return [
            {
                "name": d.get("name"),
                "category": d.get("category"),
                "phases": d.get("phases", []),
                "capabilities": d.get("capabilities", []),
                "description": d.get("description", ""),
                "version": d.get("version", "1.0"),
            }
            for d in self._agents.values()
        ]

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, agent_name: str) -> bool:
        return agent_name in self._agents


# Global registry instance
_agent_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    """Get the global agent registry instance."""
    global _agent_registry
    if _agent_registry is None:
        _agent_registry = AgentRegistry()
    return _agent_registry


def initialize_agent_registry() -> AgentRegistry:
    """Initialize (reset) the global agent registry."""
    global _agent_registry
    _agent_registry = AgentRegistry()
    logger.info("Global agent registry initialized")
    return _agent_registry
