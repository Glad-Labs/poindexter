"""
Poindexter Probe Interface — the contract all probes implement.

This is the pluggable boundary from #207/#215. Poindexter ships
infrastructure probes. Operators install business probes. Community
contributes everything in between.

Usage:
    from probe_interface import Probe, ProbeResult

    class MyCustomProbe(Probe):
        name = "my_service"
        description = "Checks if my service is healthy"
        interval_seconds = 300  # 5 minutes

        async def check(self, pool, config) -> ProbeResult:
            # ... your check logic ...
            return ProbeResult(ok=True, detail="All good")
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ProbeResult:
    """Result of a single probe check."""

    ok: bool
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)
    severity: str = "info"  # info, warning, critical

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "metrics": self.metrics,
            "severity": self.severity,
        }


@runtime_checkable
class Probe(Protocol):
    """Interface that all probes must implement.

    Probes check the health of a service, metric, or business process.
    They run on the brain daemon's cycle and report back ProbeResults.

    Built-in probes ship with Poindexter (infrastructure).
    Operator probes are installed as plugins (business metrics).
    Community probes are shared via the plugin ecosystem.
    """

    name: str
    description: str
    interval_seconds: int  # How often to run (brain checks this)

    async def check(self, pool: Any, config: dict[str, Any]) -> ProbeResult:
        """Run the health check.

        Args:
            pool: asyncpg connection pool (or None if DB is unavailable)
            config: probe-specific configuration from app_settings or YAML

        Returns:
            ProbeResult with ok/fail status, detail message, and optional metrics.
        """
        ...


# ---------------------------------------------------------------------------
# Probe Categories — used for registration and discovery
# ---------------------------------------------------------------------------

CATEGORY_INFRASTRUCTURE = "infrastructure"  # DB, worker, Ollama, site, disk
CATEGORY_CONTENT = "content"  # quality, publishing, embeddings, SEO
CATEGORY_BUSINESS = "business"  # revenue, email, social, analytics (operator-only)


# ---------------------------------------------------------------------------
# Probe Registry — discovered at startup, run by the brain daemon
# ---------------------------------------------------------------------------

_registered_probes: list[Probe] = []


def register_probe(probe: Probe) -> None:
    """Register a probe for execution by the brain daemon."""
    _registered_probes.append(probe)


def get_registered_probes() -> list[Probe]:
    """Get all registered probes."""
    return list(_registered_probes)


def clear_probes() -> None:
    """Clear all registered probes (for testing)."""
    _registered_probes.clear()
