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
from collections.abc import Iterable
from functools import cache
from importlib.metadata import EntryPoint, entry_points
from typing import Any

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
    "topic_sources": "poindexter.topic_sources",
    "image_providers": "poindexter.image_providers",
    "tts_providers": "poindexter.tts_providers",
    "video_providers": "poindexter.video_providers",
    "audio_gen_providers": "poindexter.audio_gen_providers",
    "publish_adapters": "poindexter.publish_adapters",
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
            logger.exception(
                "plugin discovery: failed to load %s entry_point %r: %s",
                group, ep.name, e,
            )
            continue
        try:
            instance = target() if callable(target) else target
        except Exception as e:
            logger.exception(
                "plugin discovery: failed to instantiate %s plugin %r: %s",
                group, ep.name, e,
            )
            continue
        instances.append(instance)
    return instances


@cache
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


def get_topic_sources() -> list[Any]:
    """Return all registered TopicSource instances."""
    return list(_cached(ENTRY_POINT_GROUPS["topic_sources"]))


def get_image_providers() -> list[Any]:
    """Return all registered ImageProvider instances."""
    return list(_cached(ENTRY_POINT_GROUPS["image_providers"]))


def get_tts_providers() -> list[Any]:
    """Return all registered TTSProvider instances."""
    return list(_cached(ENTRY_POINT_GROUPS["tts_providers"]))


def get_video_providers() -> list[Any]:
    """Return all registered VideoProvider instances.

    Mirrors :func:`get_image_providers`. Tracks GitHub #124 — Wan 2.1
    T2V 1.3B as the first generation provider; the legacy Ken Burns
    slideshow pipeline ships as a sibling ``compose`` provider so a
    settings flip can swap engines without code changes.
    """
    return list(_cached(ENTRY_POINT_GROUPS["video_providers"]))
def get_audio_gen_providers() -> list[Any]:
    """Return all registered AudioGenProvider instances."""
    return list(_cached(ENTRY_POINT_GROUPS["audio_gen_providers"]))


def get_publish_adapters() -> list[Any]:
    """Return all registered PublishAdapter instances.

    Tracks Glad-Labs/poindexter#143 (video pipeline upload Stage) and
    Glad-Labs/poindexter#40 (OAuth seed flow). Adapters ship inert
    until the operator opts in — the registry just discovers them; per-
    adapter ``enabled`` + secret gating is each adapter's discipline.
    """
    return list(_cached(ENTRY_POINT_GROUPS["publish_adapters"]))


# ---------------------------------------------------------------------------
# Core sample plugins — registered imperatively as a workaround for this
# project's poetry packaging config (see pyproject.toml note). Third-party
# community plugins use entry_points as documented; core samples are
# imported directly until the packaging issue is resolved.
# ---------------------------------------------------------------------------


def get_core_samples() -> dict[str, list[Any]]:
    """Discover the bundled SAMPLE plugins.

    History note (gh#152): this function used to also load every
    *production* plugin imperatively because the worker container's
    Dockerfile did ``poetry install --no-root`` — which means
    poindexter-backend wasn't installed as a package, so its
    ``[tool.poetry.plugins."poindexter.*"]`` entry_points blocks
    were never registered in dist-info. The fallback was a hand-
    maintained ``_SAMPLES`` list inside this function that mirrored
    pyproject.toml. Adding a new plugin meant editing two files in
    sync, and forgetting one was a silent failure (caught + fixed
    in commit 5e421c6d for three jobs that were missing for days).

    The proper fix shipped in gh#152: Dockerfile.worker now runs
    ``pip install --no-deps .`` after copying source, which creates
    the dist-info entry_points and lets ``importlib.metadata`` find
    them. ``pyproject.toml`` is the single source of truth.

    What this function still loads
    ------------------------------

    Three bundled SAMPLE plugins under ``plugins/samples/``. They're
    test artifacts that prove each Protocol family loads correctly:
    ``HelloTap``, ``DatabaseProbe``, ``NoopJob``. They're useful for
    the integration test suite and as worked examples for plugin
    authors, so they remain discoverable even in dev environments
    where ``pip install .`` hasn't run.

    Returns a dict keyed by plugin type so callers that want to merge
    samples with entry_point-discovered plugins can do so cleanly.
    Import failures are logged + skipped (same policy as
    :func:`_load_group`).
    """
    samples: dict[str, list[Any]] = {k: [] for k in ENTRY_POINT_GROUPS}

    _SAMPLES: list[tuple[str, str, str]] = [
        # (plugin_type, module_path, class_name)
        # Bundled samples only — production plugins come from
        # pyproject.toml via setuptools entry_points (gh#152).
        ("taps", "plugins.samples.hello_tap", "HelloTap"),
        ("probes", "plugins.samples.database_probe", "DatabaseProbe"),
        ("jobs", "plugins.samples.noop_job", "NoopJob"),
    ]

    for plugin_type, module_path, class_name in _SAMPLES:
        try:
            import importlib
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            samples[plugin_type].append(cls())
        except Exception as e:
            logger.exception(
                "core sample load failed: %s.%s: %s",
                module_path, class_name, e,
            )

    return samples


def clear_registry_cache() -> None:
    """Invalidate the discovery cache.

    Useful in tests, after a ``pip install`` during a running session,
    or when a new plugin has been dynamically loaded. Production code
    should rarely need this — the cache lives for the process lifetime
    and container restarts pick up newly-installed plugins.
    """
    _cached.cache_clear()
