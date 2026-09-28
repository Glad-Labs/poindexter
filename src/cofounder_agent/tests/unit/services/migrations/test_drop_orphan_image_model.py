"""Regression guard for ``image_model``, retired 2026-09-28.

Follow-up to
``20260928_162435_drop_the_image_model_setting_orphaned_by_the_image_model_registry_removal``.

The key named the model for the worker's in-process diffusers path. Its only
reader, ``get_default_image_model``, went with the worker's image-model
registry in the same commit, and it never chose what renders: the image-gen
server reads ``image_generation_model``.

The key sat in five seed/metadata sites, all edited in that commit:
``settings_defaults.DEFAULTS``, its ``METADATA`` entry,
``0000_baseline.seeds.sql``, ``scripts/settings_defaults_extract.json`` (which
the seed-drift lint does not read) and ``StartupManager``'s non-Ollama
model-key list. These tests pin the seed and metadata sites. Any one of them
putting the key back gives a fresh install a row nothing reads.

The fat-finger floor keeps live keys a pattern sweep would catch.
``image_generation_model`` shares the prefix and is the key the server renders
from. ``image_prompt_model`` and ``inline_image_prompt_model`` match
``LIKE '%image%model'`` too. ``image_gen_enabled``, ``image_gen_server_url`` and
``image_negative_prompt`` come from the same family. All have readers today.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_BACKEND_PKG = _REPO / "src" / "cofounder_agent" / "poindexter"
_SERVICES = _BACKEND_PKG / "services"
_MIGRATIONS_DIR = _SERVICES / "migrations"
_DEFAULTS_PY = _SERVICES / "settings_defaults.py"
_CATEGORIES_PY = _SERVICES / "settings_categories.py"
_BRAIN_SEED = _BACKEND_PKG / "brain" / "seed_app_settings.json"
_EXTRACT_JSON = _REPO / "scripts" / "settings_defaults_extract.json"
_MIGRATION = (
    _MIGRATIONS_DIR
    / "20260928_162435_drop_the_image_model_setting_orphaned_by_the_image_model_registry_removal.py"
)

_DEAD_KEYS = ("image_model",)

_KEEP_KEYS = (
    "image_generation_model",
    "image_prompt_model",
    "inline_image_prompt_model",
    "image_gen_enabled",
    "image_gen_server_url",
    "image_negative_prompt",
)

# The backend package holds ~800 non-migration modules. A scan that visits far
# fewer has lost its root (a moved package, a renamed dir), so its "no reader"
# verdict means nothing.
_MIN_SCANNED = 500


def _dict_literal_keys(path: Path, name: str) -> set[str]:
    """String keys of the module-level dict literal bound to ``name``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            value = node.value
        if isinstance(value, ast.Dict):
            return {
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    raise AssertionError(f"{name} dict literal not found in {path.name}")


def _quoted(key: str) -> re.Pattern[str]:
    """The key as a string literal, ``'key'`` or ``"key"``.

    A read names the key as a literal (``site_config.get("image_model", ...)``).
    A bare-substring match would flag prose instead: the history this
    retirement leaves in ``ImageModel``'s docstring and the image-gen server's
    registry comment names the key without reading it.
    """
    return re.compile(rf"(['\"]){re.escape(key)}\1")


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return (_MIGRATIONS_DIR / "0000_baseline.seeds.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def brain_seed_keys() -> set[str]:
    data = json.loads(_BRAIN_SEED.read_text(encoding="utf-8"))
    return {s["key"] for s in data.get("settings", []) if isinstance(s, dict) and "key" in s}


@pytest.fixture(scope="module")
def defaults_keys() -> set[str]:
    return _dict_literal_keys(_DEFAULTS_PY, "DEFAULTS")


@pytest.fixture(scope="module")
def metadata_keys() -> set[str]:
    return _dict_literal_keys(_DEFAULTS_PY, "METADATA")


def _seeds_key(seeds_text: str, key: str) -> bool:
    return re.search(rf"VALUES \('{re.escape(key)}',", seeds_text) is not None


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    assert not _seeds_key(baseline_seeds_text, key), (
        f"{key!r} is seeded in 0000_baseline.seeds.sql. Nothing reads it; the "
        "image-gen server renders image_generation_model."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_brain_seed(brain_seed_keys: set[str], key: str) -> None:
    assert key not in brain_seed_keys, (
        f"{key!r} is in brain/seed_app_settings.json. A fresh brain bootstrap "
        "would resurrect it."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_defaults(defaults_keys: set[str], key: str) -> None:
    assert key not in defaults_keys, (
        f"{key!r} is in settings_defaults.DEFAULTS. seed_all_defaults would "
        "re-insert it on the next boot, undoing the migration."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_metadata(metadata_keys: set[str], key: str) -> None:
    assert key not in metadata_keys, (
        f"{key!r} is in settings_defaults.METADATA, annotating a row nothing "
        "seeds or reads."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_categories(key: str) -> None:
    assert not _quoted(key).search(_CATEGORIES_PY.read_text(encoding="utf-8")), (
        f"{key!r} is mapped in settings_categories.py."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_extract_json(key: str) -> None:
    """settings_seed_drift_lint does not read the extract JSON, so this is its
    only guard. The JSON is the #379 AST extract that DEFAULTS was bootstrapped
    from, and scripts/extract_secret_keys.py still reads it. A stale entry
    reads as a live default to anything that consults it."""
    data = json.loads(_EXTRACT_JSON.read_text(encoding="utf-8"))
    rows = [r for r in data.get("rows", []) if r.get("key") == key]
    assert not rows and key not in data.get("by_key", {}) and key not in data.get(
        "conflicts", {}
    ), f"{key!r} is still in scripts/settings_defaults_extract.json"


@pytest.mark.parametrize("key", _KEEP_KEYS)
def test_live_keep_keys_still_seeded(defaults_keys: set[str], key: str) -> None:
    assert key in defaults_keys, (
        f"live/keep key {key!r} was lost from settings_defaults.DEFAULTS. It has "
        "a live reader and must not be swept up with the retired image_model."
    )


def test_render_model_key_seeded_on_both_install_paths(
    defaults_keys: set[str], brain_seed_keys: set[str],
) -> None:
    """``image_generation_model`` is not in the baseline seeds, so DEFAULTS
    (the ``poindexter setup`` path) and the brain seed (the ``docker compose
    up`` path) are its only seeds. Losing either leaves that install path's
    image-gen server with no model: it reports "not set" and stays degraded."""
    assert "image_generation_model" in defaults_keys
    assert "image_generation_model" in brain_seed_keys


def test_no_overlap_between_dead_and_keep() -> None:
    assert not (set(_DEAD_KEYS) & set(_KEEP_KEYS))


def test_migration_targets_exactly_the_dead_key() -> None:
    """The migration must name its key literally, never sweep by prefix.

    A ``LIKE 'image_%model'`` or ``LIKE '%image%model%'`` would also match the
    live keys in ``_KEEP_KEYS``, starting with ``image_generation_model``.
    """
    src = _MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(src)
    orphaned: tuple[str, ...] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "ORPHANED_KEYS" for t in node.targets
        ):
            orphaned = tuple(
                e.value for e in node.value.elts  # type: ignore[attr-defined]
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            )
    assert orphaned == _DEAD_KEYS, (
        f"migration ORPHANED_KEYS is {orphaned!r}, expected {_DEAD_KEYS!r}"
    )
    # Scope the pattern-sweep check to actual SQL literals: the ORPHANED_KEYS
    # comment says "prefix/LIKE", which is legitimate prose a whole-file scan
    # would trip on.
    sql_literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "app_settings" in n.value
    ]
    assert sql_literals, "no app_settings SQL found in the migration"
    for sql in sql_literals:
        assert not re.search(r"\bLIKE\b", sql, re.IGNORECASE), (
            "migration SQL must not sweep keys by LIKE pattern — it could match "
            f"a live image-model key. Offending SQL: {sql!r}"
        )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_no_backend_module_reads_the_dead_key(key: str) -> None:
    """Why the key is dead, pinned so a new reader can't reappear unnoticed.

    If code starts reading it again, the key stops being an orphan: prod has
    already lost its row to the migration and no seed would put it back, so
    the reader would silently run on its code default. The model the
    image-gen server renders is ``image_generation_model``. Use that.
    """
    pattern = _quoted(key)
    readers: list[str] = []
    scanned = 0
    for path in sorted(_BACKEND_PKG.rglob("*.py")):
        if _MIGRATIONS_DIR in path.parents:
            continue
        scanned += 1
        if pattern.search(path.read_text(encoding="utf-8")):
            readers.append(str(path.relative_to(_REPO)))
    for path in sorted((_REPO / "scripts").glob("*.py")):
        if pattern.search(path.read_text(encoding="utf-8")):
            readers.append(str(path.relative_to(_REPO)))
    assert scanned >= _MIN_SCANNED, (
        f"scanned only {scanned} modules under {_BACKEND_PKG} — the scan root "
        "moved, so this guard is blind. Point _BACKEND_PKG at the backend package."
    )
    assert not readers, (
        f"{key!r} is named as a string literal again in {readers}. It was retired "
        "with the worker's image-model registry (migration 20260928_162435); the "
        "image-gen server renders image_generation_model. If a reader of the old "
        "key is really wanted, seed it in settings_defaults.py and retire this guard."
    )
