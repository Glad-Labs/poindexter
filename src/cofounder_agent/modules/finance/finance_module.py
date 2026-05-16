"""``FinanceModule`` — Glad Labs' finance / bank-integration Module.

Phase F1 (2026-05-13). Mirrors ``ContentModule`` shape — same Phase
3-lite minimum: manifest + migrate() pointing at this package's
migrations/ directory, plus Phase-4 lifecycle stubs.

The actual finance code (``MercuryClient``, future polling jobs,
DB tables) lives in this package as it grows. Unlike ContentModule
(which started large + we deferred physical moves), FinanceModule
starts at the right size — it grows organically as new bank
adapters / accounting features land.
"""

from __future__ import annotations

from pathlib import Path

from plugins.module import ModuleManifest

_MANIFEST = ModuleManifest(
    name="finance",
    version="0.1.0",
    visibility="private",
    description="Bank integrations + accounting surface (Mercury read-only).",
    requires=(),
)


class FinanceModule:
    """Glad Labs finance Module — Mercury read-only banking + future
    accounting surface."""

    @property
    def migrations_dir(self) -> Path:
        """Boot wiring's discovery hook — points at this package's
        ``migrations/`` subdirectory."""
        return Path(__file__).parent / "migrations"

    def manifest(self) -> ModuleManifest:
        return _MANIFEST

    async def migrate(self, pool: object) -> None:
        """Apply finance-module-specific migrations. Idempotent."""
        from services.module_migrations import run_module_migrations

        await run_module_migrations(pool, _MANIFEST.name, self.migrations_dir)

    def register_routes(self, app: object) -> None:
        """Mount the FinanceModule operator routes under
        ``/api/finance/*`` (balances / transactions / healthcheck).

        Called by ``utils/route_registration.register_all_routes`` after
        substrate routes mount — the module routes can therefore shadow
        substrate paths if a name collision ever shows up (none today;
        the ``/api/finance`` prefix is exclusively module-owned).

        ``app`` is the host FastAPI application. We type it as
        ``object`` to match the Protocol shape (cheap import for
        tooling that doesn't have fastapi installed) and only assert
        the ``include_router`` shape at the call site.
        """
        # Lazy import — routes.py imports MercuryClient + middleware,
        # both of which pull httpx + the OAuth issuer. Keeping the import
        # inside register_routes() means a Module-discovery pass that
        # doesn't ultimately mount the routes (e.g. a unit test that
        # only exercises manifest()) never pays the import cost.
        from modules.finance.routes import router as finance_router

        if not hasattr(app, "include_router"):
            # Per ``feedback_no_silent_defaults`` — fail loud if the host
            # passed something that isn't a FastAPI app. A silent no-op
            # here would leave /api/finance/* missing in production with
            # no log trail; the loud RuntimeError gets caught by the
            # caller's try/except and logged with full traceback.
            raise RuntimeError(
                f"FinanceModule.register_routes: expected a FastAPI app "
                f"with .include_router, got {type(app).__name__}"
            )
        app.include_router(finance_router)

    def register_cli(self, parser: object) -> None:
        """Phase 4 — ``poindexter finance <subcommand>`` subparsers
        will register here. F1 wires the CLI inline via
        ``cli/finance_commands.py`` until Phase 4 generalizes this."""
        del parser

    def register_dashboards(self, grafana: object) -> None:
        """Phase 4 — finance dashboards (balance trend, burn rate)
        will register here."""
        del grafana

    def register_probes(self, brain: object) -> None:
        """Phase 4 — finance probes (Mercury API reachability,
        token expiry) will register here."""
        del brain

    async def healthcheck(self, pool: object) -> object:
        """Phase 4 — aggregate finance sub-probe results."""
        del pool
        return None


__all__ = ["FinanceModule"]
