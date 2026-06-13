"""Smoke test: every provider implementation file is registered.

Discovered via Glad-Labs/poindexter#398 — three provider modules
(``gemini``, ``flux_schnell``, ``stable_audio_open``) had been added
to the source tree without a matching entry in
``plugins.registry.get_core_samples()``. Result: import resolved, but
``get_*_providers()`` lookups returned empty lists, and operators
setting (e.g.) ``app_settings.audio_gen_engine='stable-audio-open-1.0'``
got "no provider is registered with that name. Registered audio-gen
providers: []" at dispatch time.

This test scans the on-disk provider directories, finds every module
that defines a ``Provider``-shaped class (one with a string ``name``
class attribute), and verifies the class is referenced in the
registry's ``_SAMPLES`` list. Adding a new provider file without
registering it now fails CI instead of silently shipping a dead-code
plugin.

Known gaps live in :data:`KNOWN_UNREGISTERED` with an issue link so
the gap is auditable rather than invisible. The list should shrink,
not grow.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

# Locate the source tree relative to this test file rather than CWD —
# pytest may be invoked from anywhere.
_SRC_ROOT = Path(__file__).resolve().parents[3]


# (plugin_type, package_path_under_src) — the directories the smoke
# test walks. Each tuple maps a registry plugin_type to the importable
# package containing implementation modules.
_PROVIDER_DIRS: list[tuple[str, str]] = [
    ("llm_providers", "services/llm_providers"),
    ("llm_providers", "plugins/llm_providers"),
    ("image_providers", "services/image_providers"),
    ("audio_gen_providers", "services/audio_gen_providers"),
    ("video_providers", "services/video_providers"),
]


# Module filenames that live in a provider directory but are NOT
# providers (utilities, dispatchers, model loaders). Matched against
# the bare filename (no extension).
_NON_PROVIDER_MODULES: set[str] = {
    "__init__",
    "dispatcher",  # services/llm_providers/dispatcher.py — dispatch helper
    "thinking_models",  # services/llm_providers/thinking_models.py — model-class detection helpers
    "ollama_unload",  # services/llm_providers/ollama_unload.py — VRAM-guard helper (no provider class)
}


# Provider modules with a known registry gap. Each entry MUST link to
# the tracking issue. The test xfails on these so the gap is visible
# in test output without breaking CI; remove the entry when the
# provider lands in the registry.
KNOWN_UNREGISTERED: dict[str, str] = {
    # plugins/llm_providers/anthropic.py — paid-vendor SDK plugin,
    # opt-in via ``app_settings.plugin.llm_provider.anthropic.enabled``.
    # Same shape as gemini (registered in #398). File a follow-up
    # issue + register identically.
    "plugins.llm_providers.anthropic": "follow-up to Glad-Labs/poindexter#398",
}


def _module_path_from_file(file_path: Path) -> str:
    """Translate ``services/llm_providers/foo.py`` → ``services.llm_providers.foo``."""
    rel = file_path.relative_to(_SRC_ROOT).with_suffix("")
    return ".".join(rel.parts)


def _provider_classes_in(module_path: str) -> list[type]:
    """Import the module and return classes that look like providers.

    "Provider-shaped" = defined in this module + has a string ``name``
    class attribute. Matches the convention used by every existing
    LLMProvider / ImageProvider / AudioGenProvider / VideoProvider
    implementation in the tree.
    """
    module = importlib.import_module(module_path)
    out: list[type] = []
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if not isinstance(obj, type):
            continue
        if obj.__module__ != module_path:
            continue
        name = getattr(obj, "name", None)
        if isinstance(name, str) and name:
            out.append(obj)
    return out


def _registered_modules() -> set[str]:
    """Parse ``plugins/registry.py`` and return the module paths in
    ``_SAMPLES``.

    AST parse rather than import-and-introspect so the test is robust
    to mid-rewrite states of the registry (e.g. a syntax-error during
    a refactor doesn't take this test down — it just fails with a
    clear "couldn't parse registry" message).
    """
    registry_file = _SRC_ROOT / "plugins" / "registry.py"
    tree = ast.parse(registry_file.read_text(encoding="utf-8"))
    modules: set[str] = set()
    # _SAMPLES is defined inside the get_core_samples() function body,
    # so a flat module-level walk misses it. Walk the whole tree
    # (ast.walk recurses into function bodies) and pick out every
    # assignment to a name called _SAMPLES whose RHS is a list literal
    # of 3-element string tuples.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(node, ast.Assign):
            targets = node.targets
        else:
            targets = [node.target]
        if not any(
            isinstance(t, ast.Name) and t.id == "_SAMPLES" for t in targets
        ):
            continue
        if not isinstance(node.value, ast.List):
            continue
        for elt in node.value.elts:
            if not isinstance(elt, ast.Tuple) or len(elt.elts) != 3:
                continue
            mod = elt.elts[1]
            if isinstance(mod, ast.Constant) and isinstance(mod.value, str):
                modules.add(mod.value)
    return modules


def _discover_provider_modules() -> list[tuple[str, str]]:
    """Walk each provider directory and return ``(module_path, plugin_type)``
    for every implementation file.

    Skips ``__init__``, leading-underscore files (private helpers),
    and the ``_NON_PROVIDER_MODULES`` set.
    """
    discovered: list[tuple[str, str]] = []
    for plugin_type, rel_dir in _PROVIDER_DIRS:
        dir_path = _SRC_ROOT / rel_dir
        if not dir_path.is_dir():
            continue
        for py_file in sorted(dir_path.glob("*.py")):
            stem = py_file.stem
            if stem in _NON_PROVIDER_MODULES or stem.startswith("_"):
                continue
            discovered.append((_module_path_from_file(py_file), plugin_type))
    return discovered


_PROVIDER_MODULES = _discover_provider_modules()


@pytest.mark.parametrize(
    "module_path,plugin_type",
    _PROVIDER_MODULES,
    ids=[m for m, _ in _PROVIDER_MODULES],
)
def test_provider_module_is_registered(module_path: str, plugin_type: str) -> None:
    """Every provider implementation file must be referenced by the
    plugin registry.

    Catches the Glad-Labs/poindexter#398 failure mode: a provider file
    lands in the tree, the import path resolves, but no registry entry
    points at it — so the runtime ``get_*_providers()`` lookup misses
    and operators see "no provider is registered with that name".
    """
    if module_path in KNOWN_UNREGISTERED:
        pytest.xfail(
            f"{module_path} is a known registry gap "
            f"({KNOWN_UNREGISTERED[module_path]})",
        )

    classes = _provider_classes_in(module_path)
    assert classes, (
        f"{module_path} sits in a provider directory but defines no "
        "Provider-shaped class (one with a string ``name`` class attribute). "
        "If it's a helper module, add it to _NON_PROVIDER_MODULES; "
        "otherwise add the provider class."
    )

    registered = _registered_modules()
    assert module_path in registered, (
        f"{module_path} defines provider class(es) "
        f"{[c.__name__ for c in classes]} but is NOT in "
        f"plugins.registry._SAMPLES under plugin_type={plugin_type!r}. "
        "Add the entry so runtime lookups (get_*_providers / "
        "resolve_*_provider) can find it. See "
        "Glad-Labs/poindexter#398 for the failure mode this prevents."
    )


def test_known_unregistered_links_to_an_issue() -> None:
    """Every known-gap entry must reference a tracking issue so the
    backlog is auditable. Catches drive-by ``KNOWN_UNREGISTERED`` adds
    that would otherwise let unregistered modules linger forever.
    """
    for module_path, note in KNOWN_UNREGISTERED.items():
        assert (
            "Glad-Labs/poindexter#" in note
            or "GH#" in note
            or "gh#" in note
        ), (
            f"KNOWN_UNREGISTERED[{module_path!r}] must reference an "
            f"issue (Glad-Labs/poindexter#NNN); got {note!r}."
        )


# ---------------------------------------------------------------------------
# Cross-check: every pyproject.toml entry_point must be in ``_SAMPLES``.
#
# Captured 2026-05-20 (finding #189). Five plugins (CollapseOldEmbeddingsJob,
# BackfillPodcastsJob, BackfillVideosJob, OpenClawSQLiteTap, IGDBSource)
# had ``[tool.poetry.plugins."poindexter.*"]`` declarations in pyproject.toml
# but were never added to ``_SAMPLES``. In production the worker doesn't
# pip-install the package (it only bind-mounts the source), so
# ``importlib.metadata.entry_points()`` returns 0 and the imperative
# ``_SAMPLES`` list is the only thing that loads plugins. Without registry
# entries the plugins are dead code — schedules don't fire, lookups miss,
# operators don't notice until the silent-staleness probes catch up days
# later.
#
# This test fails when pyproject.toml declares an entry_point whose class
# isn't found in any ``_SAMPLES`` entry under the matching plugin group.
# ---------------------------------------------------------------------------


# Plugin groups in pyproject.toml that we cross-check. Excludes
# ``modules`` because its section's TOML keys ('name', 'url', 'priority')
# aren't class names — module manifest validation lives in
# ``test_module_registry.py``.
_CROSSCHECK_GROUPS: tuple[str, ...] = (
    "taps",
    "probes",
    "jobs",
    "topic_sources",
    "image_providers",
    "stages",
    "llm_providers",
)


def _pyproject_entry_points() -> dict[str, list[tuple[str, str]]]:
    """Parse pyproject.toml + return ``{group: [(ep_name, target), ...]}``.

    ``target`` is the raw ``module.path:ClassName`` string from TOML.
    Uses regex instead of tomllib so the test stays runnable on Python
    3.10 (tomllib is 3.11+).
    """
    import re as _re
    pyproject = _SRC_ROOT.parent / "pyproject.toml"
    if not pyproject.exists():
        # When pytest runs from the package root, _SRC_ROOT.parent is
        # already the cofounder_agent dir. Walk one more up.
        pyproject = _SRC_ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")

    out: dict[str, list[tuple[str, str]]] = {}
    sections = _re.findall(
        r'\[tool\.poetry\.plugins\."poindexter\.([^"]+)"\](.*?)(?=^\[tool|\Z)',
        text, _re.DOTALL | _re.MULTILINE,
    )
    for group, section in sections:
        entries = _re.findall(
            r'^([a-zA-Z_][a-zA-Z_0-9]*)\s*=\s*"([^"]+)"',
            section, _re.MULTILINE,
        )
        out[group] = entries
    return out


def _registered_class_names_per_group() -> dict[str, set[str]]:
    """Parse ``_SAMPLES`` and return ``{plugin_type: {ClassName, ...}}``.

    Same AST walk as :func:`_registered_modules` but keeps the
    ``(plugin_type, ClassName)`` split so the cross-check can match
    entry_points by their (group, class) pair.
    """
    registry_file = _SRC_ROOT / "plugins" / "registry.py"
    tree = ast.parse(registry_file.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(t, ast.Name) and t.id == "_SAMPLES" for t in targets
        ):
            continue
        if not isinstance(node.value, ast.List):
            continue
        for elt in node.value.elts:
            if not isinstance(elt, ast.Tuple) or len(elt.elts) != 3:
                continue
            type_node, _mod_node, cls_node = elt.elts
            if not (
                isinstance(type_node, ast.Constant)
                and isinstance(type_node.value, str)
                and isinstance(cls_node, ast.Constant)
                and isinstance(cls_node.value, str)
            ):
                continue
            out.setdefault(type_node.value, set()).add(cls_node.value)
    return out


@pytest.mark.parametrize("group", _CROSSCHECK_GROUPS)
def test_pyproject_entry_points_are_registered_in_samples(group: str) -> None:
    """Every entry_point declared in ``[tool.poetry.plugins."poindexter.<group>"]``
    must appear in ``_SAMPLES`` under the same plugin type.

    In production the worker bind-mounts the source rather than
    pip-installing it, so entry_points discovery returns nothing.
    The imperative ``_SAMPLES`` list is the sole load path. A drift
    here = a plugin that never runs. See finding #189 for the silent
    failure mode this caught (5 plugins dead for weeks).
    """
    ep_per_group = _pyproject_entry_points()
    declared = ep_per_group.get(group, [])
    registered = _registered_class_names_per_group().get(group, set())

    missing: list[str] = []
    for _ep_name, target in declared:
        cls = target.rsplit(":", 1)[-1]
        if cls not in registered:
            missing.append(cls)

    assert not missing, (
        f"pyproject.toml declares entry_points under "
        f"poindexter.{group} whose classes are NOT in "
        f"plugins.registry._SAMPLES: {sorted(missing)}. "
        "Either add the class to _SAMPLES (recommended — it's the "
        "only load path in production since the worker doesn't "
        "pip-install the package) or remove the dead entry_point "
        "from pyproject.toml. See finding #189."
    )


# ---------------------------------------------------------------------------
# Cross-check: every pyproject.toml entry_point TARGET must actually import.
#
# Captured 2026-06-13. The 2026-06-04 Module v1 Phase 3 migration moved the
# 12 content stages from ``services/stages/`` to ``modules/content/stages/``
# and updated the imperative ``_SAMPLES`` list to the new path — but left the
# ``[tool.poetry.plugins."poindexter.stages"]`` entry_points pointing at the
# now-deleted ``cofounder_agent.services.stages.*`` modules. The worker/CLI
# image pip-installs the package (Dockerfile, no ``--no-root``), so
# ``importlib.metadata.entry_points(group="poindexter.stages")`` discovered the
# 12 stale entries on every boot and ``plugins.registry._load_group`` logged a
# full ERROR traceback per entry (~12 ERROR lines per CLI/worker start).
#
# ``test_pyproject_entry_points_are_registered_in_samples`` did NOT catch it:
# it compares only the class name *after the colon*, never the module *path* —
# and the move left class names unchanged. This test closes that blind spot by
# importing each entry_point's full ``module:Class`` target.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("group", _CROSSCHECK_GROUPS)
def test_pyproject_entry_point_targets_import(group: str) -> None:
    """Every ``module.path:ClassName`` target declared under
    ``[tool.poetry.plugins."poindexter.<group>"]`` must resolve: the module
    imports and the named class attribute exists.

    Complements ``test_pyproject_entry_points_are_registered_in_samples``,
    which only checks the class *name* against ``_SAMPLES`` and so is blind to
    a stale module *path* (the failure mode the 2026-06-04 stages → modules/
    content/stages move introduced). Importing the target is the same load
    ``importlib.metadata`` does when the package is pip-installed, so a green
    test here means no ``_load_group`` ERROR-spam on boot.
    """
    declared = _pyproject_entry_points().get(group, [])

    broken: list[str] = []
    for ep_name, target in declared:
        module_path, _, cls_name = target.partition(":")
        if not cls_name:
            broken.append(f"{ep_name} = {target!r} (target has no ':ClassName')")
            continue
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:  # noqa: BLE001 - any import failure is a real defect
            broken.append(
                f"{ep_name} = {target!r} "
                f"(import failed: {type(exc).__name__}: {exc})"
            )
            continue
        if not hasattr(module, cls_name):
            broken.append(
                f"{ep_name} = {target!r} "
                f"(module imported but has no attribute {cls_name!r})"
            )

    assert not broken, (
        f"pyproject.toml declares poindexter.{group} entry_points whose "
        f"targets do not import:\n  " + "\n  ".join(broken) + "\n"
        "Repoint the entry_point at the implementation's current module path "
        "(was the class moved during a refactor? — update pyproject.toml AND "
        "plugins.registry._SAMPLES together). This is the failure mode the "
        "2026-06-04 stages → modules/content/stages move introduced."
    )
