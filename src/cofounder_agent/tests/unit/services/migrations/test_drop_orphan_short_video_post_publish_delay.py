"""Regression guard for ``short_video_post_publish_delay_seconds``, retired 2026-09-27.

Follow-up to
``20260927_204836_drop_the_orphaned_short_video_post_publish_delay_seconds_setting``.

The key's one reader was the "11d" short-video hook in
``publish_service.publish_post_from_task``: it slept this many seconds so the
long-form podcast could finish first, then generated the short. #893
(2026-06-01) moved podcast/video/short generation off the publish path and
deleted that hook, leaving the baseline seed row with no reader.

Only one seed source ever carried it (``0000_baseline.seeds.sql``), and it is
removed there in the same commit as the migration. These tests pin that: if a
baseline regen or a "restore the missing delay knob" edit puts it back into a
seed source, a fresh install would seed a row that nothing reads.

The fat-finger floor keeps the live ``*_delay_seconds`` keys that share this
one's shape and ``general`` category, plus a video-family key and the
REQUIRED ``site_url`` that sits beside it in the seed file. All are read by
code today, so a prefix or ``LIKE`` sweep that caught any of them would break
something.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_BACKEND_PKG = _REPO / "src" / "cofounder_agent" / "poindexter"
_MIGRATIONS_DIR = _BACKEND_PKG / "services" / "migrations"
_DEFAULTS_PY = _BACKEND_PKG / "services" / "settings_defaults.py"
_BRAIN_SEED = _BACKEND_PKG / "brain" / "seed_app_settings.json"
_MIGRATION = (
    _MIGRATIONS_DIR
    / "20260927_204836_drop_the_orphaned_short_video_post_publish_delay_seconds_setting.py"
)

_DEAD_KEYS = ("short_video_post_publish_delay_seconds",)

# Keys that MUST survive, each with a live reader: two same-shape
# ``*_delay_seconds`` keys (newsletter_service, rag_engine), a video-family key
# (video_routes), and site_url, the next row after the dead one in the seed.
_KEEP_KEYS = (
    "newsletter_batch_delay_seconds",
    "rag_embed_retry_base_delay_seconds",
    "video_feed_name",
    "site_url",
)

# The backend package holds ~800 non-migration modules. A scan that visits far
# fewer has lost its root (a moved package, a renamed dir), so its "no reader"
# verdict means nothing.
_MIN_SCANNED = 500


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    return (_MIGRATIONS_DIR / "0000_baseline.seeds.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def brain_seed_keys() -> set[str]:
    data = json.loads(_BRAIN_SEED.read_text(encoding="utf-8"))
    return {s["key"] for s in data.get("settings", []) if isinstance(s, dict) and "key" in s}


@pytest.fixture(scope="module")
def defaults_keys() -> set[str]:
    tree = ast.parse(_DEFAULTS_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        target_is_defaults = False
        value = None
        if isinstance(node, ast.Assign):
            target_is_defaults = any(
                isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in node.targets
            )
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_is_defaults = node.target.id == "DEFAULTS"
            value = node.value
        if target_is_defaults and isinstance(value, ast.Dict):
            return {
                k.value
                for k in value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    raise AssertionError("DEFAULTS dict not found in settings_defaults.py")


def _seeds_key(seeds_text: str, key: str) -> bool:
    return re.search(rf"VALUES \('{re.escape(key)}',", seeds_text) is not None


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    assert not _seeds_key(baseline_seeds_text, key), (
        f"{key!r} is seeded in 0000_baseline.seeds.sql — it has had no reader "
        "since #893 removed the 11d short-video hook from publish_service "
        "(2026-06-01). Seeding it gives every fresh install a dead row."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_brain_seed(brain_seed_keys: set[str], key: str) -> None:
    assert key not in brain_seed_keys, (
        f"{key!r} is in brain/seed_app_settings.json — a fresh brain bootstrap "
        "would resurrect it."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_defaults(defaults_keys: set[str], key: str) -> None:
    assert key not in defaults_keys, (
        f"{key!r} is in settings_defaults.DEFAULTS — seed_all_defaults would "
        "re-insert it on the next boot, undoing the migration."
    )


@pytest.mark.parametrize("key", _KEEP_KEYS)
def test_live_keep_keys_still_seeded(baseline_seeds_text: str, key: str) -> None:
    assert _seeds_key(baseline_seeds_text, key), (
        f"live/keep key {key!r} was lost from 0000_baseline.seeds.sql — it has "
        "a live reader and must not be swept up with the retired short-video delay."
    )


def test_no_overlap_between_dead_and_keep() -> None:
    assert not (set(_DEAD_KEYS) & set(_KEEP_KEYS))


def test_migration_targets_exactly_the_dead_key() -> None:
    """The migration must name its key literally, never sweep by prefix.

    A ``LIKE '%_delay_seconds'`` would also match the live keys in
    ``_KEEP_KEYS``.
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
            f"a live *_delay_seconds key. Offending SQL: {sql!r}"
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
        f"{key!r} is referenced again by {readers} — it was retired as a "
        "zero-reader orphan (migration 20260927_204836). Seed it in "
        "settings_defaults.py if it is live again, and retire this guard."
    )
