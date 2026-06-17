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
        from services.module_runner import run_module_migrations

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
        """Mount ``poindexter finance <subcommand>`` on the host CLI group.

        ``parser`` is the click root group. The worker lifespan invokes this
        with ``None`` (the worker process hosts no CLI), which is a no-op. A
        non-None host that isn't a click group fails loud per
        ``feedback_no_silent_defaults`` rather than silently dropping the
        finance commands. The CLI module lives inside this package
        (``modules/finance/cli.py``), so it is stripped from the public
        mirror together with the module directory (Module v1 Phase 5).
        """
        if parser is None:
            return
        if not hasattr(parser, "add_command"):
            raise RuntimeError(
                "FinanceModule.register_cli: expected a click Group with "
                f".add_command, got {type(parser).__name__}"
            )
        from modules.finance.cli import finance_group

        parser.add_command(finance_group, name="finance")  # type: ignore[attr-defined]

    def register_dashboards(self, grafana: object) -> None:
        """Phase 4 — finance dashboards (balance trend, burn rate)
        will register here."""
        del grafana

    def register_probes(self, brain: object) -> None:
        """Register finance brain probes (Glad-Labs/poindexter#565).

        ``brain`` is a
        :class:`plugins.probe_registry.BrainProbeRegistry`. We register the
        Mercury poll-staleness probe so it appears in ``/api/modules/probes``
        and rides the registry-driven execution path the brain daemon polls.
        The probe pages (via ``notify_operator`` + ``audit_log``) when the
        hourly Mercury poll stalls or loses auth — the gap #565 filed (the
        F1 docstring TODO'd exactly this).

        Defensive: a ``brain`` that is ``None`` (the worker process doesn't
        host the brain — lifespan passes ``None`` as the canonical
        "subsystem absent" sentinel) is a no-op. A real registry that
        somehow lacks ``register`` fails loud per
        ``feedback_no_silent_defaults`` rather than silently dropping the
        probe — a missing finance probe is the bug #565 is about.
        """
        if brain is None:
            return
        register = getattr(brain, "register", None)
        if not callable(register):
            raise RuntimeError(
                "FinanceModule.register_probes: expected a BrainProbeRegistry "
                f"with .register(...), got {type(brain).__name__}"
            )

        from modules.finance.probes import (
            FinancePollStalenessProbe,
            run_finance_poll_staleness_probe,
        )

        probe = FinancePollStalenessProbe()
        register(
            module="finance",
            name=probe.name,
            callable=run_finance_poll_staleness_probe,
            description=probe.description,
            interval_seconds=probe.interval_seconds,
        )

    def refresh_module_metrics(self, pool: object) -> object:
        """Scrape-time Prometheus refresh hook (Glad-Labs/poindexter#565).

        Called by ``services/metrics_exporter.refresh_metrics`` on every
        ``/metrics`` scrape via the generic module-metrics loop (the loop
        looks for this optional method on each registered Module — there is
        no finance-specific import in the public substrate exporter, so the
        whole surface stays inside the stripped ``modules/finance/`` tree).

        Returns the awaitable from
        :func:`modules.finance.metrics.refresh_finance_metrics` so the caller
        can ``await`` it; the exporter wraps the call in its own try/except so
        a finance refresh failure never makes ``/metrics`` error.
        """
        from modules.finance.metrics import refresh_finance_metrics

        return refresh_finance_metrics(pool)

    def bind_platform(self, platform: object) -> None:
        """Receive this module's capability-scoped kernel handle (Wave 2 of
        Seam 1, Glad-Labs/poindexter#667).

        Stored for the module's own code to reach the kernel through; finance
        grows ``_MANIFEST.capabilities`` as it routes its own kernel access
        through the handle. ``platform`` is a ``plugins.platform.Platform``
        (typed ``object`` to match this module's cheap-import convention)."""
        self._platform = platform

    async def healthcheck(self, pool: object) -> object:
        """Phase 4 — aggregate finance sub-probe results."""
        del pool
        return None


__all__ = ["FinanceModule"]
