"""Plugin registry — discover plugins via setuptools entry_points.

No custom registry, no decorators, no pkgutil auto-imports. We use
``importlib.metadata.entry_points()`` — the same mechanism pytest,
click, and flask use.

Each plugin package declares its contributions in its ``pyproject.toml``:

.. code:: toml

    [project.entry-points."poindexter.taps"]
    gitea = "poindexter_tap_gitea:GiteaTap"

At runtime Poindexter discovers them:

.. code:: python

    from plugins.registry import get_taps
    for tap in get_taps():
        ...

Each ``get_*`` function returns *instances*, not classes — the entry_point
target is called with no arguments to instantiate the plugin. Plugins that
need more than parameterless construction should expose a factory function
as their entry_point target.

Results are cached for the lifetime of the process; call
:func:`clear_registry_cache` in tests or after a ``pip install`` if you
need to re-discover.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# Canonical entry_point group names. Kept as module-level constants so
# plugin authors can reference them and stay in sync.
ENTRY_POINT_GROUPS: dict[str, str] = {
    "taps": "poindexter.taps",
    "probes": "poindexter.probes",
    "jobs": "poindexter.jobs",
    "stages": "poindexter.stages",
    "reviewers": "poindexter.reviewers",
    "adapters": "poindexter.adapters",
    "providers": "poindexter.providers",
    "packs": "poindexter.packs",
    "llm_providers": "poindexter.llm_providers",
}


def _load_group(group: str) -> list[Any]:
    """Load every entry_point in ``group``, instantiate each, and return
    the resulting objects.

    Entry_points that fail to import or instantiate are logged and
    skipped — one broken plugin must not block discovery of the others.
    """
    try:
        # Python 3.10+: entry_points() accepts a group kwarg.
        eps: Iterable[EntryPoint] = entry_points(group=group)
    except TypeError:
        # Fallback for older 3.9 shape, just in case.
        all_eps = entry_points()
        eps = all_eps.get(group, []) if isinstance(all_eps, dict) else []

    instances: list[Any] = []
    for ep in eps:
        try:
            target = ep.load()
        except Exception as e:
            logger.error(
                "plugin discovery: failed to load %s entry_point %r: %s",
                group, ep.name, e,
            )
            continue
        try:
            instance = target() if callable(target) else target
        except Exception as e:
            logger.error(
                "plugin discovery: failed to instantiate %s plugin %r: %s",
                group, ep.name, e,
            )
            continue
        instances.append(instance)
    return instances


@lru_cache(maxsize=None)
def _cached(group: str) -> tuple[Any, ...]:
    """Cached tuple wrapper around ``_load_group``.

    Tuple return so ``lru_cache`` works (lists are unhashable — though
    that doesn't affect the cache key here, keeping it a tuple means
    callers can't mutate the cached list by accident).
    """
    return tuple(_load_group(group))


def get_taps() -> list[Any]:
    """Return all registered Tap instances."""
    return list(_cached(ENTRY_POINT_GROUPS["taps"]))


def get_probes() -> list[Any]:
    """Return all registered Probe instances."""
    return list(_cached(ENTRY_POINT_GROUPS["probes"]))


def get_jobs() -> list[Any]:
    """Return all registered Job instances."""
    return list(_cached(ENTRY_POINT_GROUPS["jobs"]))


def get_stages() -> list[Any]:
    """Return all registered Stage instances (excluding specializations)."""
    return list(_cached(ENTRY_POINT_GROUPS["stages"]))


def get_reviewers() -> list[Any]:
    """Return all registered Reviewer instances."""
    return list(_cached(ENTRY_POINT_GROUPS["reviewers"]))


def get_adapters() -> list[Any]:
    """Return all registered Adapter instances."""
    return list(_cached(ENTRY_POINT_GROUPS["adapters"]))


def get_providers() -> list[Any]:
    """Return all registered Provider instances."""
    return list(_cached(ENTRY_POINT_GROUPS["providers"]))


def get_packs() -> list[Any]:
    """Return all registered Pack instances."""
    return list(_cached(ENTRY_POINT_GROUPS["packs"]))


def get_llm_providers() -> list[Any]:
    """Return all registered LLMProvider instances."""
    return list(_cached(ENTRY_POINT_GROUPS["llm_providers"]))


def clear_registry_cache() -> None:
    """Invalidate the discovery cache.

    Useful in tests, after a ``pip install`` during a running session,
    or when a new plugin has been dynamically loaded. Production code
    should rarely need this — the cache lives for the process lifetime
    and container restarts pick up newly-installed plugins.
    """
    _cached.cache_clear()
