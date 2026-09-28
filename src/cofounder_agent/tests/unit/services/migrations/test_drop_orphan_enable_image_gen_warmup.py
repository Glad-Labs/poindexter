"""Regression guard for ``enable_image_gen_warmup``, retired 2026-09-28.

Follow-up to
``20260928_140518_drop_the_enable_image_gen_warmup_setting_orphaned_by_the_warmup_removal``.

The key's one reader was ``StartupManager._warmup_image_models``, deleted in
the same commit. It read the key before the lifespan loaded the DB, so the
stored value never took effect (prod held ``'true'`` and every boot logged
"Skipped"), and the image-gen server unloads its model when idle anyway, so a
startup render warmed nothing.

Unlike #4111's orphan, this key sat in four seed/metadata sites, all edited in
the same commit: ``settings_defaults.DEFAULTS``, its ``METADATA`` owner entry,
the ``settings_categories`` map and ``0000_baseline.seeds.sql``. These tests pin
all four. If any one puts the key back, a fresh install seeds (or annotates) a
row that nothing reads.

The fat-finger floor keeps live keys a pattern sweep would catch: three
``enable_*`` keys of the same shape, and two image-gen keys from the same
family (``image_gen_enabled`` gates the featured-image render,
``image_gen_server_url`` addresses the server). All have readers today.
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
_MIGRATION = (
    _MIGRATIONS_DIR
    / "20260928_140518_drop_the_enable_image_gen_warmup_setting_orphaned_by_the_warmup_removal.py"
)

_DEAD_KEYS = ("enable_image_gen_warmup",)

_KEEP_KEYS = (
    "enable_pyroscope",
    "enable_tracing",
    "enable_writer_self_review",
    "image_gen_enabled",
    "image_gen_server_url",
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
        f"{key!r} is seeded in 0000_baseline.seeds.sql. Its only reader, the "
        "startup image-gen warmup, was deleted; seeding it gives every fresh "
        "install a dead row."
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
        f"{key!r} is in settings_defaults.METADATA, naming an owner that no "
        "longer reads it."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_categories(key: str) -> None:
    assert key not in _CATEGORIES_PY.read_text(encoding="utf-8"), (
        f"{key!r} is still mapped in settings_categories.py."
    )


@pytest.mark.parametrize("key", _KEEP_KEYS)
def test_live_keep_keys_still_seeded(
    baseline_seeds_text: str, defaults_keys: set[str], key: str,
) -> None:
    assert _seeds_key(baseline_seeds_text, key) and key in defaults_keys, (
        f"live/keep key {key!r} was lost from 0000_baseline.seeds.sql or "
        "settings_defaults.DEFAULTS. It has a live reader and must not be swept "
        "up with the retired warmup flag."
    )


def test_no_overlap_between_dead_and_keep() -> None:
    assert not (set(_DEAD_KEYS) & set(_KEEP_KEYS))


def test_migration_targets_exactly_the_dead_key() -> None:
    """The migration must name its key literally, never sweep by prefix.

    A ``LIKE 'enable_%'`` or ``LIKE '%image_gen%'`` would also match the live
    keys in ``_KEEP_KEYS``.
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
            f"a live enable_* or image-gen key. Offending SQL: {sql!r}"
        )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_no_backend_module_reads_the_dead_key(key: str) -> None:
    """Why the key is dead, pinned so a new reader can't reappear unnoticed.

    If code starts reading it again, the key stops being an orphan: prod has
    already lost its row to the migration and no seed would put it back, so
    the reader would silently run on its code default. Re-add the key through
    ``settings_defaults.py`` and retire this guard deliberately instead.
    """
    readers: list[str] = []
    scanned = 0
    for path in sorted(_BACKEND_PKG.rglob("*.py")):
        if _MIGRATIONS_DIR in path.parents:
            continue
        scanned += 1
        if key in path.read_text(encoding="utf-8"):
            readers.append(str(path.relative_to(_REPO)))
    assert scanned >= _MIN_SCANNED, (
        f"scanned only {scanned} modules under {_BACKEND_PKG} — the scan root "
        "moved, so this guard is blind. Point _BACKEND_PKG at the backend package."
    )
    assert not readers, (
        f"{key!r} is referenced again by {readers}. It was retired with the "
        "startup image-gen warmup (migration 20260928_140518). Seed it in "
        "settings_defaults.py if it is live again, and retire this guard."
    )
