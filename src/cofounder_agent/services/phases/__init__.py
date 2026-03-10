"""
Phases - Composable workflow building blocks

Each phase is an independent capability that can be:
- Executed in isolation
- Composed into workflows with other phases
- Configured with parameters
- Connected via data threading (outputs from one phase → inputs to next)

Available phases can be discovered via PhaseRegistry.
"""

from .base_phase import BasePhase, PhaseConfig, PhaseInputSpec, PhaseOutputSpec

try:
    from .phase_registry import get_phase_registry  # type: ignore[import]
except ImportError:
    get_phase_registry = None

__all__ = [
    "BasePhase",
    "PhaseConfig",
    "PhaseInputSpec",
    "PhaseOutputSpec",
]

if get_phase_registry is not None:
    __all__.append("get_phase_registry")
