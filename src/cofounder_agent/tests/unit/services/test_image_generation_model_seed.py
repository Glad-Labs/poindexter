"""Every seed of ``image_generation_model`` must name a model the server has.

``image_generation_model`` is the one image-model setting: the image-gen HTTP
server (``scripts/image-gen-server.py``) looks its value up in its own
``REGISTRY`` on startup and on every reload. A value that is not a REGISTRY key
leaves the server degraded ("unknown image model"): ``/generate`` answers 503
and the pipeline falls back to Pexels for every image.

That is not hypothetical. From 2026-08-26 (#3366) to 2026-09-28
``settings_defaults.DEFAULTS`` seeded it as ``'image_gen'``, copied from the
worker's gpu-lock label fallback (``site_config.get("image_generation_model",
"image_gen")``). The baseline seeds do not carry this key, so on a
``poindexter setup`` install DEFAULTS is its first writer, and that install's
image-gen server came up degraded. Nothing checked a seed against the
REGISTRY, and the seed-value drift lint compared DEFAULTS only with the
baseline, which has no row for this key to compare.

The expectation is derived, never hand-listed: the REGISTRY keys are read
from the server script itself, so adding or renaming a model there moves this
test with it.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from poindexter.services.settings_defaults import DEFAULTS

_KEY = "image_generation_model"


def _find_repo_root() -> Path | None:
    """The checkout root, found by the server script it holds, not by depth."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts" / "image-gen-server.py").is_file():
            return parent
    return None


_REPO = _find_repo_root()
if _REPO is None:  # a tree without scripts/ (the worker image's /app)
    pytest.skip(
        "scripts/image-gen-server.py is not in this tree; run from a checkout",
        allow_module_level=True,
    )
_SERVER = _REPO / "scripts" / "image-gen-server.py"
_BACKEND_PKG = _REPO / "src" / "cofounder_agent" / "poindexter"
_BASELINE_SEEDS = _BACKEND_PKG / "services" / "migrations" / "0000_baseline.seeds.sql"
_BRAIN_SEED = _BACKEND_PKG / "brain" / "seed_app_settings.json"


def _server_registry_keys() -> set[str]:
    """String keys of the module-level ``REGISTRY`` dict in the server script."""
    tree = ast.parse(_SERVER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            named, value = node.target.id == "REGISTRY", node.value
        elif isinstance(node, ast.Assign):
            named = any(isinstance(t, ast.Name) and t.id == "REGISTRY" for t in node.targets)
            value = node.value
        else:
            continue
        if named and isinstance(value, ast.Dict):
            return {
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    return set()


def _seeded_values() -> dict[str, str]:
    """source label -> the value that source seeds for the key, if it seeds it."""
    found: dict[str, str] = {}
    if _KEY in DEFAULTS:
        found["settings_defaults.DEFAULTS"] = DEFAULTS[_KEY]
    brain = json.loads(_BRAIN_SEED.read_text(encoding="utf-8"))
    for row in brain.get("settings", []):
        if isinstance(row, dict) and row.get("key") == _KEY:
            found["brain/seed_app_settings.json"] = str(row.get("value"))
    match = re.search(
        rf"VALUES \('{_KEY}', '((?:[^']|'')*)'",
        _BASELINE_SEEDS.read_text(encoding="utf-8"),
    )
    if match:
        found["0000_baseline.seeds.sql"] = match.group(1).replace("''", "'")
    return found


@pytest.fixture(scope="module")
def registry() -> set[str]:
    keys = _server_registry_keys()
    assert keys, (
        f"found no REGISTRY dict literal in {_SERVER}. The server script moved "
        "or reshaped its registry, so this guard can no longer see it."
    )
    return keys


def test_server_reads_this_key() -> None:
    """The premise of every check below: this is the key the server selects by."""
    source = _SERVER.read_text(encoding="utf-8")
    assert re.search(rf'^MODEL_SETTING_KEY = "{_KEY}"$', source, re.MULTILINE), (
        f"scripts/image-gen-server.py no longer reads {_KEY!r} as its model "
        "setting. Point this test (and the seeds) at the key it does read."
    )


def test_every_seed_names_a_registry_model(registry: set[str]) -> None:
    seeded = _seeded_values()
    assert seeded, f"no seed source carries {_KEY!r}, so a fresh install has no image model"
    unknown = {src: val for src, val in seeded.items() if val not in registry}
    assert not unknown, (
        f"{_KEY!r} is seeded with a value the image-gen server does not know: "
        f"{unknown}. Known: {sorted(registry)}. A fresh install whose first "
        "writer is that source boots the server degraded, and every image falls "
        "back to Pexels."
    )


def test_operator_overlay_names_a_registry_model(registry: set[str]) -> None:
    oo = pytest.importorskip("poindexter.services.operator_overrides")
    value = {**oo.OPERATOR_MODEL_PINS, **oo.OPERATOR_SETTING_OVERRIDES}.get(_KEY)
    if value is None:
        pytest.skip(f"the operator overlay does not pin {_KEY}")
    assert value in registry, (
        f"the operator overlay pins {_KEY}={value!r}, which the image-gen server "
        f"does not know (known: {sorted(registry)})"
    )
