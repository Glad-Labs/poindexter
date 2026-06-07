"""``ContentModule`` — Glad Labs' blog publishing Module.

Phase 3-lite (Glad-Labs/poindexter#490). The pipeline code lives in
substrate (``services/content_router_service``, ``services/stages``,
``services/multi_model_qa``, ``services/content_validator``,
``services/flows/content_generation``) for now. This class is the
**identity + lifecycle bundler** — the unit of install, version,
migration tracking, and visibility flag — without the physical code
move that would burn 100+ import paths for a refactor we can defer
until module 2 lands.

What works today:
- ``manifest()`` returns the canonical content-module manifest.
- ``migrate(pool)`` runs migrations under ``content/migrations/``
  via the Phase 2 runner. Currently zero migrations live there;
  substrate keeps owning the content tables. When we move
  content-specific migrations out of ``services/migrations/`` in
  the future Phase 3.5, they land here.
- ``healthcheck(pool)`` returns ``None`` (Phase 1 acceptable shape).

What's TODO (the other lifecycle methods — register_routes /
register_cli / register_dashboards / register_probes — are Phase 4):
- Phase 4 wires those one call site each.
"""

from __future__ import annotations

from pathlib import Path

from plugins.module import ModuleManifest
from plugins.platform import Capability

_MANIFEST = ModuleManifest(
    name="content",
    version="0.1.0",
    visibility="public",
    description="Blog publishing pipeline — Glad Labs reference module.",
    requires=(),
    # Seam 1 (Glad-Labs/poindexter#667): content's declared capabilities — the
    # scoped handle exposes exactly these. Grows as each is migrated:
    #   AUDIT (Wave 3c): telemetry via ``platform.audit.write_bg`` instead of
    #     importing ``services.audit_log``.
    #   DISPATCH (Wave 3d): LLM completions via ``platform.dispatch.complete``
    #     instead of importing ``services.llm_providers.dispatcher``.
    #   CONFIG (Wave 3e): DB-backed ``app_settings`` reads via
    #     ``platform.config.get`` instead of ``context.get("site_config")``.
    #     The file sweep is chunked (3e-1+); reaching ``platform.config`` from
    #     any content file requires this declaration (an undeclared access
    #     raises ``CapabilityError``), so it lands with the first migrated chunk.
    capabilities=(Capability.AUDIT, Capability.DISPATCH, Capability.CONFIG),
)


class ContentModule:
    """The content-publishing business module.

    Mirrors the ``plugins.module.Module`` Protocol (Phase 1) — duck
    types via attribute presence, not inheritance, so the runtime_checkable
    Protocol's ``isinstance`` check succeeds.
    """

    @property
    def migrations_dir(self) -> Path:
        """Override for the startup wiring's discovery — points at this
        package's ``migrations/`` directory. The boot wiring prefers
        this attribute over the package-file fallback because we ship
        in-tree under ``cofounder_agent.modules.content`` rather than
        as a top-level package."""
        return Path(__file__).parent / "migrations"

    def manifest(self) -> ModuleManifest:
        return _MANIFEST

    async def migrate(self, pool: object) -> None:
        """Apply content-module-specific migrations. Idempotent.

        Phase 3-lite ships with zero content migrations — substrate
        still owns the content tables. The directory exists so the
        Phase 2 runner has somewhere to look; it's a no-op until
        Phase 3.5 moves content-specific migrations here.
        """
        from services.module_runner import run_module_migrations

        await run_module_migrations(pool, _MANIFEST.name, self.migrations_dir)

    def register_routes(self, app: object) -> None:
        """Phase 4 — not wired in Phase 3-lite."""
        del app  # intentional no-op until Phase 4

    def register_cli(self, parser: object) -> None:
        """Phase 4 — not wired in Phase 3-lite."""
        del parser  # intentional no-op until Phase 4

    def register_dashboards(self, grafana: object) -> None:
        """Phase 4 — not wired in Phase 3-lite."""
        del grafana  # intentional no-op until Phase 4

    def register_probes(self, brain: object) -> None:
        """Phase 4 — ``brain`` is a
        :class:`plugins.probe_registry.BrainProbeRegistry`. ContentModule
        has no concrete probes today (the pipeline's QA gates are
        operator-side concerns and ship via Grafana, not brain probes).
        When a content-specific brain probe is added (e.g. "stale
        pipeline_tasks rows", "embedding backlog depth"), it will
        register here as ``brain.register(module="content", name=...,
        callable=...)``. Phase 1 docstring contract: ``brain`` is typed
        as ``object`` to match the Protocol shape; the call site casts."""
        del brain

    def bind_platform(self, platform: object) -> None:
        """Receive this module's capability-scoped kernel handle (Wave 2 of
        Seam 1, Glad-Labs/poindexter#667).

        Stored for the module's own code to reach the kernel through. Content
        does not use it yet — Wave 3 migrates content's direct kernel imports
        onto it one capability at a time, and grows ``_MANIFEST.capabilities``
        in lockstep. ``platform`` is a ``plugins.platform.Platform`` (typed
        ``object`` to match this module's cheap-import convention)."""
        self._platform = platform

    async def healthcheck(self, pool: object) -> object:
        """Phase 1 contract allows ``None`` — Phase 4 will aggregate
        sub-probe results into a real ``ProbeResult``."""
        del pool  # intentional no-op until Phase 4
        return None


__all__ = ["ContentModule"]
