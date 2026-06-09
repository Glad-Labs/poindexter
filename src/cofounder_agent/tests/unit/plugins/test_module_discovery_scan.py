"""Unit tests for presence-based in-tree module discovery (Module v1 Phase 5).

The scan replaces the hardcoded content/finance ``_SAMPLES`` tuples. These
tests pin: (1) the real in-tree tree is discovered, (2) an absent module
directory simply isn't registered, (3) a listed-but-broken module raises
loud per feedback_no_silent_defaults.
"""

from __future__ import annotations

import importlib.util

import pytest

from plugins import registry
from plugins.registry import (
    ModuleDiscoveryError,
    _scan_intree_modules,
)

# The sync filter strips ``src/cofounder_agent/modules/finance/`` from the
# public ``poindexter`` mirror. Tests that require the finance module to be
# present on disk (real-tree scan / forced-load) must skip there rather than
# fail. ``find_spec`` checks importability without executing the module.
_FINANCE_MODULE_PRESENT = importlib.util.find_spec("modules.finance") is not None
_finance_only = pytest.mark.skipif(
    not _FINANCE_MODULE_PRESENT,
    reason="modules.finance is private (operator overlay) — stripped from public mirror",
)


def _names(modules):
    return sorted(m.manifest().name for m in modules)


@pytest.fixture(autouse=True)
def _scan_is_sole_module_source(monkeypatch):
    """Pin module discovery to the in-tree directory scan by neutralizing
    the *other* discovery channel: pip-installed ``poindexter.modules``
    entry points.

    ``get_modules()`` merges two independent sources — the in-tree directory
    scan (what these tests drive via the ``_intree_module_names`` monkeypatch)
    AND any installed ``poindexter.modules`` entry points
    (``_merge_with_core_samples`` → ``_cached`` → ``entry_points``). On a clean
    ``poetry install --no-root`` (the CI ``test-backend`` job) and on the
    stripped public mirror, that entry-point group is empty, so the scan is the
    sole source. But a full-tree *editable* dev install whose ``dist-info``
    predates the 2026-06-04 Phase-5 change that emptied the pyproject
    ``[tool.poetry.plugins."poindexter.modules"]`` group still carries stale
    ``content``/``finance`` entry points (frozen in ``entry_points.txt``).
    Those leak ``finance`` past the scan monkeypatch and falsely red
    ``test_stripped_tree_has_no_finance_anywhere`` in local full-tree dev runs,
    even though CI stays green.

    Returning ``[]`` for the modules group models the clean-install /
    stripped-mirror state regardless of ambient ``dist-info`` — the same way
    ``test_registry.py``'s ``_reset_registry_cache`` neutralizes
    ``entry_points`` for the groups it isn't exercising. Other groups delegate
    to the real lookup, and the cache is cleared before+after so the patch is
    honoured and never leaks into neighbouring tests.
    """
    real_entry_points = registry.entry_points

    def _scan_only_entry_points(*args, **kwargs):
        if kwargs.get("group") == registry.ENTRY_POINT_GROUPS["modules"]:
            return []
        return real_entry_points(*args, **kwargs)

    monkeypatch.setattr(registry, "entry_points", _scan_only_entry_points)
    registry.clear_registry_cache()
    yield
    registry.clear_registry_cache()


@_finance_only
def test_scan_discovers_real_intree_modules():
    """The real modules/ tree yields content + finance, and finance's
    poll_mercury job rides along via modules/finance/jobs/JOBS."""
    scanned = _scan_intree_modules()
    assert "content" in _names(scanned["modules"])
    assert "finance" in _names(scanned["modules"])
    job_names = {getattr(j, "name", None) for j in scanned["jobs"]}
    assert "poll_mercury" in job_names


def test_absent_module_directory_is_not_registered(monkeypatch):
    """Simulate the stripped public mirror: only `content` on disk.
    finance must vanish from both modules and jobs — no source edits."""
    monkeypatch.setattr(registry, "_intree_module_names", lambda: ["content"])
    scanned = _scan_intree_modules()
    assert _names(scanned["modules"]) == ["content"]
    job_names = {getattr(j, "name", None) for j in scanned["jobs"]}
    assert "poll_mercury" not in job_names


def test_present_but_broken_module_raises(monkeypatch):
    """A directory the scan lists but cannot import is a real bug and
    must fail loud (feedback_no_silent_defaults), not silently skip."""
    monkeypatch.setattr(registry, "_intree_module_names", lambda: ["bogus_broken"])
    with pytest.raises(ModuleDiscoveryError):
        _scan_intree_modules()


def test_present_but_corrupt_module_is_wrapped(monkeypatch):
    """A module dir that IS listed but whose module file raises a non-import
    error on load (syntax error, bad manifest) is wrapped into a loud
    ModuleDiscoveryError rather than silently skipped — the broad-except path."""

    def _boom(_name):
        raise ValueError("corrupt module file")

    monkeypatch.setattr(registry, "_intree_module_names", lambda: ["content"])
    monkeypatch.setattr(registry, "_load_intree_module", _boom)
    with pytest.raises(ModuleDiscoveryError):
        _scan_intree_modules()


def test_jobs_loader_absent_package_returns_empty():
    """A module with no ``jobs/`` package contributes no jobs (e.g. content)."""
    assert registry._load_intree_module_jobs("content") == []


def test_jobs_loader_broken_inner_import_propagates(monkeypatch):
    """Regression: a ``jobs/`` package that IS present but whose inner import
    is broken must NOT be silently swallowed — it propagates so the scan
    wraps it loud (feedback_no_silent_defaults). Only the jobs package's own
    absence yields []."""
    import types

    real_import = registry.importlib.import_module

    def _fake_import(modpath, *args, **kwargs):
        if modpath == "modules.finance.jobs":
            # jobs/__init__.py present, but a DIFFERENT module it imports is missing.
            raise ModuleNotFoundError(
                "No module named 'modules.finance.jobs.poll_mercury'",
                name="modules.finance.jobs.poll_mercury",
            )
        return real_import(modpath, *args, **kwargs)

    monkeypatch.setattr(
        registry, "importlib", types.SimpleNamespace(import_module=_fake_import)
    )
    with pytest.raises(ModuleNotFoundError):
        registry._load_intree_module_jobs("finance")


@_finance_only
def test_get_modules_uses_scan(monkeypatch):
    """get_modules() surfaces the scanned in-tree modules (via the
    unchanged _merge_with_core_samples -> get_core_samples path)."""
    from plugins.registry import clear_registry_cache, get_modules

    clear_registry_cache()
    monkeypatch.setattr(registry, "_intree_module_names", lambda: ["content", "finance"])
    names = sorted(m.manifest().name for m in get_modules())
    assert names == ["content", "finance"]


def test_stripped_tree_has_no_finance_anywhere(monkeypatch):
    """The leak-class regression: with finance absent on disk, neither
    get_modules() nor the jobs bucket reference finance — the exact
    consistency the old substrate line-patching guaranteed by hand."""
    from plugins.registry import clear_registry_cache, get_core_samples, get_modules

    clear_registry_cache()
    monkeypatch.setattr(registry, "_intree_module_names", lambda: ["content"])
    assert sorted(m.manifest().name for m in get_modules()) == ["content"]
    job_names = {getattr(j, "name", None) for j in get_core_samples()["jobs"]}
    assert "poll_mercury" not in job_names
