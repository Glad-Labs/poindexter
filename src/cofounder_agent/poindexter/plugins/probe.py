"""Probe — the state-checking Protocol.

Re-exports the Probe Protocol originally defined in
``brain/probe_interface.py`` (GitHub #207/#215) so it lives in one place
under ``plugins/``. The brain module keeps a thin shim that re-exports
from here to preserve existing imports during the migration.

Probes emit Prometheus metrics after Phase D lands. Alerting moves to
Alertmanager. The brain daemon pivots to being an Alertmanager webhook
consumer — it interprets alerts and decides on auto-remediation versus
human escalation; it no longer runs the check loop itself.

Register a Probe via ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.probes"]
    database = "poindexter.probes.database:DatabaseProbe"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Probe categories — used for registration, routing, and dashboard grouping.
CATEGORY_INFRASTRUCTURE = "infrastructure"  # DB, worker, Ollama, site, disk
CATEGORY_CONTENT = "content"  # quality, publishing, embeddings, SEO
CATEGORY_BUSINESS = "business"  # revenue, email, social, analytics


@dataclass
class ProbeResult:
    """Result of a single probe check."""

    ok: bool
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)
    severity: str = "info"  # info / warning / critical

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "metrics": self.metrics,
            "severity": self.severity,
        }


@runtime_checkable
class Probe(Protocol):
    """A single health or state check.

    Attributes:
        name: Unique probe name (matches the entry_point key).
        description: Human-readable explanation, shown in operator
            dashboards + alert notifications.
        interval_seconds: How often the brain daemon runs this probe.
        category: One of :data:`CATEGORY_INFRASTRUCTURE`,
            :data:`CATEGORY_CONTENT`, :data:`CATEGORY_BUSINESS`. Used to
            route alerts (infra → critical pages; business → 6h digest).
    """

    name: str
    description: str
    interval_seconds: int
    category: str

    async def check(
        self,
        pool: Any,  # asyncpg.Pool
        config: dict[str, Any],
    ) -> ProbeResult:
        """Run the check and return a structured result.

        The Probe should not raise — catch its own exceptions and return
        ``ProbeResult(ok=False, ...)`` so the brain cycle never dies from
        a single flaky probe.
        """
        ...
