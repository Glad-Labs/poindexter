"""
BasePhase - Abstract base class for all workflow phases

A Phase is an independent, composable unit of work that:
- Has well-defined inputs and outputs
- Can be configured via parameters
- Executes asynchronously
- Is idempotent when possible
- Threads data to downstream phases
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PhaseInputType(Enum):
    """Types of phase inputs"""

    USER_PROVIDED = "user_provided"  # From workflow params or user
    PHASE_OUTPUT = "phase_output"  # From previous phase output
    OPTIONAL = "optional"  # Optional, has default


@dataclass
class PhaseInputSpec:
    """Specification for a phase input"""

    name: str  # e.g., "topic", "content"
    type: str  # python type: "str", "dict", "list"
    description: str
    source: PhaseInputType = PhaseInputType.OPTIONAL
    required: bool = True
    default: Any | None = None
    accepts_from_phases: list[str] | None = None  # Which phases can provide this


@dataclass
class PhaseOutputSpec:
    """Specification for a phase output"""

    name: str  # e.g., "content", "image_url"
    type: str  # python type
    description: str


@dataclass
class PhaseConfig:
    """Specification for a phase's capabilities"""

    name: str  # Human-readable name
    description: str  # What it does
    inputs: list[PhaseInputSpec]  # Required/optional inputs
    outputs: list[PhaseOutputSpec]  # What it produces
    configurable_params: dict[str, Any]  # {param_name: default_value}


class BasePhase(ABC):
    """Abstract base class for all workflow phases"""

    def __init__(self, phase_id: str, phase_type: str):
        self.phase_id = phase_id
        self.phase_type = phase_type
        self.status = "pending"  # pending | executing | completed | failed | skipped
        self.result = None
        self.error = None
        self.execution_time = 0.0

    @classmethod
    @abstractmethod
    def get_phase_type(cls) -> str:
        """
        Return the phase type identifier.

        Example: "generate_content", "quality_evaluation", "search_image"
        Must be unique across all registered phases.
        """

    @classmethod
    @abstractmethod
    def get_phase_config(cls) -> PhaseConfig:
        """
        Return phase configuration schema.

        Defines:
        - Input requirements and sources
        - Output specification
        - Configurable parameters and defaults
        - Human-readable description
        """

    @abstractmethod
    async def execute(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """
        Execute the phase.

        Args:
            inputs: Dictionary with input names → values
                    e.g., {"topic": "AI in Healthcare", "style": "technical"}
            config: Phase-specific configuration
                    e.g., {"preferred_model": "claude", "threshold": 70}

        Returns:
            Dictionary with output names → values
            e.g., {"content": "...", "model_used": "gpt-4"}

        Should set self.status, self.result, and self.error appropriately.
        """

    async def validate_inputs(self, inputs: dict[str, Any]) -> tuple[bool, str | None]:
        """
        Validate that inputs satisfy the phase's requirements.

        Returns:
            (is_valid, error_message)
        """
        config = self.get_phase_config()

        for input_spec in config.inputs:
            if input_spec.required and input_spec.name not in inputs:
                if input_spec.default is None:
                    return False, f"Missing required input: '{input_spec.name}'"

        return True, None
