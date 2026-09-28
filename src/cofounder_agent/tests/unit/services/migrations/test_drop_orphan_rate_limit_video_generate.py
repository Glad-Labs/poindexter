"""Regression guard for ``rate_limit_video_generate_per_ip``, retired 2026-09-28.

Follow-up to
``20260928_135025_drop_the_orphaned_rate_limit_video_generate_per_ip_setting``.

The key was the slowapi limit on ``POST /api/video/generate/{post_id}``. #2254
(2026-07-10) deleted that route, and with it the key's only reader, when it
retired the legacy :9837 host-slideshow lane. The key's seed line in
``settings_defaults.DEFAULTS`` stayed behind.

``DEFAULTS`` was the only seed source that ever carried it, and it is removed
there (and from ``METADATA``) in the same commit as the migration. These tests
pin that. ``seed_all_defaults`` re-applies ``DEFAULTS`` on every boot with
``ON CONFLICT DO NOTHING``, so a restored line would put the row back on the
first boot after the migration deleted it.

The fat-finger floor keeps every live ``rate_limit_*`` key, and matters here:
``rate_limit_podcast_generate_per_ip`` is this key's exact twin in name, value
and seed block, and it is LIVE (``podcast_routes.py``). A ``LIKE
'rate_limit_%_generate_per_ip'`` sweep would take it out. The two-way
seed/reader contract for the whole family lives in
``tests/unit/utils/test_rate_limiter.py``.
"""

from __future__ import annotations

import ast
import json
import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_BACKEND_PKG = _REPO / "src" / "cofounder_agent" / "poindexter"
_MIGRATIONS_DIR = _BACKEND_PKG / "services" / "migrations"
_DEFAULTS_PY = _BACKEND_PKG / "services" / "settings_defaults.py"
_BRAIN_SEED = _BACKEND_PKG / "brain" / "seed_app_settings.json"
_SEED_DRIFT_LINT = _REPO / "scripts" / "ci" / "settings_seed_drift_lint.py"
_MIGRATION = (
    _MIGRATIONS_DIR
    / "20260928_135025_drop_the_orphaned_rate_limit_video_generate_per_ip_setting.py"
)

_DEAD_KEYS = ("rate_limit_video_generate_per_ip",)

# Keys that MUST survive: the live podcast twin first, then the rest of the
# rate-limit family. Each is read by a ``_settings_limit(...)`` decorator today.
_KEEP_KEYS = (
    "rate_limit_podcast_generate_per_ip",
    "rate_limit_token_per_ip",
    "rate_limit_triage_per_ip",
    "rate_limit_remediation_select_per_ip",
    "rate_limit_topics_from_url_per_ip",
)

# The backend package holds ~800 non-migration modules. A scan that visits far
# fewer has lost its root (a moved package, a renamed dir), so its "no reader"
# verdict means nothing.
_MIN_SCANNED = 500


def _dict_literal_keys(name: str) -> set[str]:
    """Keys of the module-level dict literal ``name`` in settings_defaults.py."""
    tree = ast.parse(_DEFAULTS_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and (
            isinstance(node.target, ast.Name) and node.target.id == name
        ):
            value = node.value
        if isinstance(value, ast.Dict):
            return {
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    raise AssertionError(f"{name} dict not found in settings_defaults.py")


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return (_MIGRATIONS_DIR / "0000_baseline.seeds.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def brain_seed_keys() -> set[str]:
    data = json.loads(_BRAIN_SEED.read_text(encoding="utf-8"))
    return {s["key"] for s in data.get("settings", []) if isinstance(s, dict) and "key" in s}


@pytest.fixture(scope="module")
def defaults_keys() -> set[str]:
    return _dict_literal_keys("DEFAULTS")


@pytest.fixture(scope="module")
def metadata_keys() -> set[str]:
    return _dict_literal_keys("METADATA")


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_defaults(defaults_keys: set[str], key: str) -> None:
    assert key not in defaults_keys, (
        f"{key!r} is in settings_defaults.DEFAULTS — seed_all_defaults would "
        "re-insert it on the next boot, undoing the migration. Its only reader, "
        "POST /api/video/generate/{post_id}, was deleted by #2254 (2026-07-10)."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_metadata(metadata_keys: set[str], key: str) -> None:
    assert key not in metadata_keys, (
        f"{key!r} is in settings_defaults.METADATA — a lifecycle annotation for "
        "a key nothing seeds or reads is a stale registry entry."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    assert re.search(rf"VALUES \('{re.escape(key)}',", baseline_seeds_text) is None, (
        f"{key!r} is seeded in 0000_baseline.seeds.sql — every fresh install "
        "would get a row that nothing reads."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_brain_seed(brain_seed_keys: set[str], key: str) -> None:
    assert key not in brain_seed_keys, (
        f"{key!r} is in brain/seed_app_settings.json — a fresh brain bootstrap "
        "would resurrect it."
    )


@pytest.mark.parametrize("key", _KEEP_KEYS)
def test_live_keep_keys_still_seeded(defaults_keys: set[str], key: str) -> None:
    assert key in defaults_keys, (
        f"live key {key!r} was lost from settings_defaults.DEFAULTS — a route "
        "still limits requests through it, and it must not be swept up with the "
        "retired video-generate limit."
    )


def test_no_overlap_between_dead_and_keep() -> None:
    assert not (set(_DEAD_KEYS) & set(_KEEP_KEYS))


def test_migration_targets_exactly_the_dead_key() -> None:
    """The migration must name its key literally, never sweep by prefix.

    A ``LIKE 'rate_limit_%'`` would also match every live key in ``_KEEP_KEYS``.
    """
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
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
            f"a live rate_limit_* key. Offending SQL: {sql!r}"
        )


def test_seed_drift_lint_sees_the_deletion() -> None:
    """CI's seed-drift lint must recognise this migration as deleting the key.

    That is what makes ``settings_seed_drift_lint`` fail a future PR that
    re-seeds the key in ANY source while this migration still deletes it. The
    lint finds deletions by shape (a ``DELETE FROM app_settings`` statement plus
    a deletion-named list such as ``ORPHANED_KEYS``), so a rename of the
    constant could silently take this migration out of its sight.
    """
    spec = spec_from_file_location("settings_seed_drift_lint_under_test", _SEED_DRIFT_LINT)
    assert spec is not None and spec.loader is not None
    lint = module_from_spec(spec)
    spec.loader.exec_module(lint)

    text = _MIGRATION.read_text(encoding="utf-8")
    assert lint._DELETE_MARKER in text
    assert set(_DEAD_KEYS) <= lint._deleted_keys_in(text)


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
        # Migrations and the seeder name keys without reading them; the seed
        # sources are pinned by the tests above.
        if _MIGRATIONS_DIR in path.parents or path == _DEFAULTS_PY:
            continue
        scanned += 1
        if key in path.read_text(encoding="utf-8"):
            readers.append(str(path.relative_to(_REPO)))
    assert scanned >= _MIN_SCANNED, (
        f"scanned only {scanned} modules under {_BACKEND_PKG} — the scan root "
        "moved, so this guard is blind. Point _BACKEND_PKG at the backend package."
    )
    assert not readers, (
        f"{key!r} is referenced again by {readers} — it was retired as a "
        "zero-reader orphan (migration 20260928_135025). Seed it in "
        "settings_defaults.py if it is live again, and retire this guard."
    )
