"""Process-wide accessor for the built :class:`AppContainer`.

Capstone of the #272 SiteConfig constructor-DI migration (design doc:
``docs/architecture/2026-05-28-site-config-di-migration.md``). This is a
deliberately tiny LEAF module — it imports nothing from the heavy
``services`` tree at runtime (only a ``TYPE_CHECKING``-guarded import of
``AppContainer`` for type hints), so the ambient-singleton modules that
register through it (``gpu_scheduler`` / ``ollama_client`` /
``prompt_manager`` / ``utils.route_utils``) can import it at their top
level without any circular-import risk.

Single chokepoint registration: ``services/bootstrap.py`` calls
:func:`set_container` immediately after constructing the container in
``build_container``. Every process (main lifespan, CLI ``_lifecycle``,
Prefect ``di_wiring``) reaches the container through ``build_container``,
so every process registers automatically.

Crash-safe-when-unset is the core invariant: :func:`get_container`
returns ``None`` when no container has been registered (CLI early paths,
import time, tests that never bootstrap). Consumers pair this with a
module-level empty-``SiteConfig`` fallback so a missing container behaves
exactly like the old empty per-module global did — never a crash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.container import AppContainer


# Module-level active container. ``None`` until a process calls
# ``set_container`` (which ``bootstrap.build_container`` does for every
# entry point). Deliberately a plain module global — there is exactly one
# AppContainer per process.
_active_container: "AppContainer | None" = None


def set_container(container: "AppContainer | None") -> None:
    """Register the process-wide built ``AppContainer``.

    Called by ``services.bootstrap.build_container`` right after the
    container is constructed. Passing ``None`` clears the registration
    (used by test teardown to restore the unset state).
    """
    global _active_container
    _active_container = container


def get_container() -> "AppContainer | None":
    """Return the process-wide ``AppContainer``, or ``None`` if unset.

    Crash-safe by design: callers MUST handle ``None`` (the early-boot /
    import-time / lightweight-test state) by falling back to an empty
    ``SiteConfig`` so behaviour matches the pre-migration empty global.
    """
    return _active_container
