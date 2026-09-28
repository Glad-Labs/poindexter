"""Regression guard for the ``findings.cloud_sync_returned_false.*`` policy
settings, retired 2026-09-28.

Follow-up to
``20260928_195254_drop_the_cloud_sync_finding_policy_settings_orphaned_by_the_sync_service_removal``
(Glad-Labs/poindexter#1112).

``publish_service._sync_published_post`` was the only source of the
``cloud_sync_returned_false`` and ``cloud_sync_exception`` findings. It pushed a
published post to a "cloud" DB through ``SyncService``, and on a stock stack the
cloud and local URLs were the same database, so it upserted each post over
itself. Both were deleted together, which left the four
``findings.cloud_sync_returned_false.*`` policy rows with nothing to govern.

The keys were seeded by ``settings_defaults.DEFAULTS`` and by
``0000_baseline.seeds.sql``; they are removed from both (and from ``METADATA``)
in the same commit as the migration. ``seed_all_defaults`` re-applies
``DEFAULTS`` on every boot with ``ON CONFLICT DO NOTHING``, so a restored line
would put a row back on the first boot after the migration deleted it.

These tests pin that, pin the migration's exact targets and behaviour, and pin
that nothing emits the two retired finding kinds again.
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
    / "20260928_195254_drop_the_cloud_sync_finding_policy_settings_orphaned_by_the_sync_service_removal.py"
)

_DEAD_KEYS = (
    "findings.cloud_sync_returned_false.cooldown_minutes",
    "findings.cloud_sync_returned_false.delivery",
    "findings.cloud_sync_returned_false.fallback",
    "findings.cloud_sync_returned_false.min_severity",
)

# The two finding kinds their only emitter produced.
_DEAD_KINDS = ("cloud_sync_returned_false", "cloud_sync_exception")

# Keys that MUST survive: other findings policies whose kinds still have a live
# emitter. The migration names its keys literally, and this floor is what would
# notice a sweep that widened to the whole ``findings.*`` family.
_KEEP_KEYS = (
    "findings.image_gen_unreachable.delivery",
    "findings.db_clock_skew.delivery",
    "findings.anomaly.delivery",
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


def _load(path: Path, name: str):
    spec = spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    # The filename starts with a digit, so it is not importable by name.
    return _load(_MIGRATION, "drop_cloud_sync_finding_policy_under_test")


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
        "re-insert it on the next boot, undoing the migration. Its finding kind "
        "has had no emitter since SyncService was removed (poindexter#1112)."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_metadata(metadata_keys: set[str], key: str) -> None:
    assert key not in metadata_keys, (
        f"{key!r} is in settings_defaults.METADATA — a lifecycle annotation for "
        "a key nothing seeds is a stale registry entry."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    assert re.search(rf"VALUES \('{re.escape(key)}',", baseline_seeds_text) is None, (
        f"{key!r} is seeded in 0000_baseline.seeds.sql — every fresh install "
        "would get a policy row for a finding kind nothing emits."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_brain_seed(brain_seed_keys: set[str], key: str) -> None:
    assert key not in brain_seed_keys, (
        f"{key!r} is in brain/seed_app_settings.json — a fresh brain bootstrap "
        "would resurrect it."
    )


@pytest.mark.parametrize("key", _KEEP_KEYS)
def test_live_findings_policies_still_seeded(defaults_keys: set[str], key: str) -> None:
    assert key in defaults_keys, (
        f"live key {key!r} was lost from settings_defaults.DEFAULTS — its finding "
        "kind still has an emitter, and it must not be swept up with the retired "
        "cloud-sync policy."
    )


def test_no_overlap_between_dead_and_keep() -> None:
    assert not (set(_DEAD_KEYS) & set(_KEEP_KEYS))


def test_migration_targets_exactly_the_dead_keys(migration) -> None:
    """The migration must name its keys literally, never sweep by prefix.

    A ``LIKE 'findings.%'`` would also match every live policy in the family.
    """
    assert tuple(migration.ORPHANED_KEYS) == _DEAD_KEYS
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
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
            f"a live findings.* policy. Offending SQL: {sql!r}"
        )


def test_seed_drift_lint_sees_the_deletion() -> None:
    """CI's seed-drift lint must recognise this migration as deleting the keys.

    That is what makes ``settings_seed_drift_lint`` fail a future PR that
    re-seeds a key in ANY source while this migration still deletes it. The lint
    finds deletions by shape (a ``DELETE FROM app_settings`` statement plus a
    deletion-named list such as ``ORPHANED_KEYS``), so a rename of the constant
    could silently take this migration out of its sight.
    """
    lint = _load(_SEED_DRIFT_LINT, "settings_seed_drift_lint_under_test")
    text = _MIGRATION.read_text(encoding="utf-8")
    assert lint._DELETE_MARKER in text
    assert set(_DEAD_KEYS) <= lint._deleted_keys_in(text)


# ---------------------------------------------------------------------------
# up() / down() behaviour, against a recording fake pool
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, present: tuple[str, ...] = ()) -> None:
        self._present = present
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.executemany_calls: list[tuple[str, list[tuple]]] = []

    async def fetch(self, sql: str, *args):
        self.fetch_calls.append((sql, args))
        # RETURNING key: report only the rows that exist on this "install".
        wanted = set(args[0]) if args else set()
        return [{"key": k} for k in self._present if k in wanted]

    async def executemany(self, sql: str, rows) -> None:
        self.executemany_calls.append((sql, list(rows)))


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_up_deletes_exactly_the_dead_keys_and_returns_the_deleted_rows(migration) -> None:
    conn = _FakeConn(present=_DEAD_KEYS)
    await migration.up(_FakePool(conn))

    assert len(conn.fetch_calls) == 1
    sql, args = conn.fetch_calls[0]
    assert sql.startswith("DELETE FROM app_settings WHERE key = ANY(")
    assert "RETURNING key" in sql
    assert args == (list(_DEAD_KEYS),)


@pytest.mark.asyncio
async def test_up_is_a_noop_on_an_install_that_never_had_the_rows(migration) -> None:
    conn = _FakeConn(present=())
    await migration.up(_FakePool(conn))  # must not raise

    assert len(conn.fetch_calls) == 1
    assert conn.executemany_calls == []


@pytest.mark.asyncio
async def test_down_restores_the_prod_rows_and_never_overwrites(migration) -> None:
    conn = _FakeConn()
    await migration.down(_FakePool(conn))

    assert len(conn.executemany_calls) == 1
    sql, rows = conn.executemany_calls[0]
    assert "ON CONFLICT (key) DO NOTHING" in sql
    by_key = {r[0]: r for r in rows}
    assert set(by_key) == set(_DEAD_KEYS)
    # (key, value, description, value_type) — the values the rows held on prod.
    assert (by_key[_DEAD_KEYS[0]][1], by_key[_DEAD_KEYS[0]][3]) == ("360", "integer")
    assert (by_key[_DEAD_KEYS[1]][1], by_key[_DEAD_KEYS[1]][3]) == ("discord", "string")
    assert (by_key[_DEAD_KEYS[2]][1], by_key[_DEAD_KEYS[2]][3]) == ("log_only", "string")
    assert (by_key[_DEAD_KEYS[3]][1], by_key[_DEAD_KEYS[3]][3]) == ("warn", "string")
    for row in rows:
        assert row[2].startswith("RETIRED 2026-09-28"), (
            "a restored row must announce that it is inert, so nobody mistakes it "
            "for a live policy"
        )


# ---------------------------------------------------------------------------
# nothing reads the keys or emits the kinds again
# ---------------------------------------------------------------------------


def _backend_sources() -> list[Path]:
    """Backend modules that could read a key or emit a kind.

    Migrations and the seeder name keys without reading them; the seed sources
    are pinned by the tests above.
    """
    return [
        path
        for path in sorted(_BACKEND_PKG.rglob("*.py"))
        if _MIGRATIONS_DIR not in path.parents and path != _DEFAULTS_PY
    ]


def _quoted(text: str, needle: str) -> bool:
    """True when ``needle`` appears as a whole quoted literal, not in prose."""
    return re.search(rf"""["']{re.escape(needle)}["']""", text) is not None


@pytest.mark.parametrize("needle", _DEAD_KEYS + _DEAD_KINDS)
def test_no_backend_module_reads_a_dead_key_or_emits_a_dead_kind(needle: str) -> None:
    """Why the keys are dead, pinned so a new emitter can't reappear unnoticed.

    If code emits ``cloud_sync_returned_false`` again, its policy rows are gone
    from prod and from every seed, so the finding would route through the
    dispatcher's default matrix with no per-kind cooldown. Seed the policy in
    ``settings_defaults.py`` and retire this guard deliberately instead.
    """
    sources = _backend_sources()
    assert len(sources) >= _MIN_SCANNED, (
        f"scanned only {len(sources)} modules under {_BACKEND_PKG} — the scan "
        "root moved, so this guard is blind. Point _BACKEND_PKG at the backend "
        "package."
    )
    hits = [
        str(path.relative_to(_REPO))
        for path in sources
        if _quoted(path.read_text(encoding="utf-8"), needle)
    ]
    assert not hits, (
        f"{needle!r} is referenced again by {hits} — it was retired with "
        "SyncService (migration 20260928_195254, poindexter#1112)."
    )
