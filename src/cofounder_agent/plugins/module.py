"""``Module`` Protocol — the unit of business-function composition.

A Module bundles the lower-level plugin contributions (stages, reviewers,
probes, jobs, taps, adapters, providers, packs) plus the things the
existing plugin registry doesn't yet track (DB migrations, Grafana
panels, HTTP routes, CLI subcommands).

See ``docs/architecture/module-v1.md`` for the design rationale + the
phased rollout this is Phase 1 of.

Discovery: modules are registered via the ``poindexter.modules`` entry-
point group, the same mechanism as every other plugin type in
``plugins/registry.py``. A ``pyproject.toml`` entry like::

    [project.entry-points."poindexter.modules"]
    content = "poindexter_module_content:ContentModule"

makes the module discoverable. The target resolves to a ``Module``
instance.

Phase 1 (this file) defines the Protocol + manifest dataclass only. The
lifecycle methods (``migrate``, ``register_routes``, etc.) are part of
the Protocol but no host code calls them yet — Phase 2-5 will wire each
call site in turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Type-only import: keeps the widely-imported manifest free of a runtime
    # dependency on ``plugins.platform`` (annotations are strings under
    # ``from __future__ import annotations``). The module that *declares*
    # capabilities imports ``Capability`` at runtime; this file only names it.
    from plugins.platform import Capability, Platform

Visibility = Literal["public", "private"]
"""Whether a module ships in the OSS sync (`public`) or stays in the
glad-labs-stack private overlay (`private`)."""


@dataclass(frozen=True)
class ModuleManifest:
    """Static description of a module — what it is, who made it, what
    it depends on. Returned by ``Module.manifest()`` and consumed by
    the registry + the OSS sync filter.

    Frozen so a Module's identity is hashable + safe to log without
    fear of accidental mutation. Modules that need runtime state hold
    it elsewhere (on the Module instance itself, or in app_settings).
    """

    name: str
    """Canonical lowercase slug. Used as Grafana folder name, MCP
    namespace, DB-migration prefix, route prefix. Example: ``content``,
    ``finance``, ``gladlabs_business``. Must match
    ``^[a-z][a-z0-9_]*$`` — see ``_MODULE_NAME_RE`` in
    ``plugins/registry.py``."""

    version: str
    """Semver. Used by future inter-module dependency resolution.
    Phase 1 stores it; Phase 2+ may enforce constraints."""

    visibility: Visibility
    """``public`` → ships in the Glad-Labs/poindexter OSS sync.
    ``private`` → glad-labs-stack overlay only. Default at Module
    instance level (subclasses pick); the registry does not enforce
    a default — every Module must declare it explicitly."""

    description: str = ""
    """One-line human-readable description for ``poindexter modules
    list`` and the eventual operator UI."""

    requires: tuple[str, ...] = field(default_factory=tuple)
    """Dependency specifiers, e.g. ``("substrate>=1.0",
    "module:memory>=0.3")``. Phase 1 stores them; resolution lands
    in a later phase if it proves load-bearing."""

    capabilities: tuple[Capability, ...] = field(default_factory=tuple)
    """The kernel capabilities this module's ``Platform`` handle exposes
    (Wave 2 of Seam 1 — the Platform handle, Glad-Labs/poindexter#667). The
    kernel injects a ``plugins.platform.ScopedPlatform`` granting exactly
    these; reaching for an undeclared capability fails loud.

    Distinct from ``requires`` (inter-module/substrate *dependency*
    specifiers) — this is the module's least-privilege *grant* on the kernel.
    Empty by default: a module that declares nothing gets a handle that
    exposes nothing (untrusted-by-default)."""


@runtime_checkable
class Module(Protocol):
    """A self-contained business function (content, finance, HR, ...).

    Implementations are typically classes that hold their manifest as
    a class attribute or build one in ``manifest()``. The Protocol's
    only hard requirements in Phase 1 are:

    - ``manifest()`` returns a valid ``ModuleManifest``
    - ``healthcheck(pool)`` returns something convertible to a
      ``plugins.probe.ProbeResult`` (Phase 1 returning ``None`` is OK)

    The other lifecycle methods (``migrate``, ``register_routes``,
    etc.) are declared here so the Protocol describes the WHOLE
    Module contract, but Phase 1 does not call them. Subsequent
    phases wire one call site each.
    """

    def manifest(self) -> ModuleManifest:
        """Return this module's manifest. Pure, no I/O."""
        ...

    async def migrate(self, pool: object) -> None:
        """Apply this module's DB migrations. Idempotent. Phase 2.

        ``pool`` is an ``asyncpg.Pool``; typed as ``object`` here to
        keep ``plugins/module.py`` free of an asyncpg import (the
        Protocol is meant to be cheap to import, even for tooling
        that doesn't have asyncpg installed). Implementations may
        narrow the type internally."""
        ...

    def register_routes(self, app: object) -> None:
        """Mount this module's HTTP routes on the host FastAPI app.
        Phase 4. ``app`` is typed as ``object`` for the same import-
        cheapness reason as ``migrate``."""
        ...

    def register_cli(self, parser: object) -> None:
        """Register ``poindexter <module> <subcommand>`` entries on
        the host CLI subparser. Phase 4."""
        ...

    def register_dashboards(self, grafana: object) -> None:
        """Contribute Grafana panels under the module's folder.
        Phase 4."""
        ...

    def register_probes(self, brain: object) -> None:
        """Register this module's brain probes for inclusion in the
        brain daemon's monitoring loop. Phase 4."""
        ...

    def bind_platform(self, platform: Platform) -> None:
        """Receive this module's capability-scoped kernel handle.

        Wave 2 of Seam 1 (Glad-Labs/poindexter#667). The kernel constructs the
        full ``Platform`` once, wraps it per-module via
        ``plugins.platform.scope_for_module`` (narrowing to this module's
        declared ``capabilities``), and hands the scoped handle here. The
        module stores it and reaches the kernel only through it. Called once
        at startup, after discovery."""
        ...

    async def healthcheck(self, pool: object) -> object:
        """Return a ``plugins.probe.ProbeResult`` summarising this
        module's health. ``None`` is acceptable in Phase 1."""
        ...


__all__ = ["Module", "ModuleManifest", "Visibility"]
