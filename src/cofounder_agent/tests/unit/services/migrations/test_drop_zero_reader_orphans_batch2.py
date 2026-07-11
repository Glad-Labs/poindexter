"""Regression guard for the 6 zero-reader orphan settings retired 2026-07-11 (batch 2).

Follow-up to ``20260711_013742_drop_zero_reader_orphan_settings`` (the 26-key
batch 1). After batch 1 dropped its keys, ``ProbeZeroReaderSettingsJob`` surfaced
the next-oldest slice of the 586-key zero-reader pool (the report is capped at
``settings_zero_reader_max_report``). A full-repo grep confirmed 6 more keys are
read by nothing — verified via the same telemetry-blind audit that KEPT the live
raw-SQL / brain readers:

  * ``staging_mode`` — no longer gates publish (``task_publishing_routes`` comment);
    a future scheduling feature will define its own key.
  * ``newsletter_email`` — seed marks it "(legacy)"; the live key is
    ``newsletter_from_email``.
  * ``local_database_url`` — the app_settings row is vestigial; the brain DB URL
    resolves from bootstrap.toml / env (chicken-and-egg — you can't read your DSN
    from the DB), never from this row.
  * ``repo_root`` — the app_settings row is unread; the container path comes from
    the environment, every ``repo_root`` in code is a local ``Path`` variable.
  * ``site_description`` / ``site_tagline`` — redundant; neither the backend nor
    the Next.js frontend reads them from app_settings (the frontend renders its
    own metadata).

Migration ``20260711_193000_drop_zero_reader_orphan_settings_batch2`` deletes them
from existing installs; this test pins every every-boot / drift-checked seed
source so a baseline regen or brain-seed edit can't silently resurrect a dead row.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_MIGRATIONS_DIR = _REPO / "src" / "cofounder_agent" / "services" / "migrations"
_DEFAULTS_PY = _REPO / "src" / "cofounder_agent" / "services" / "settings_defaults.py"
_BRAIN_SEED = _REPO / "brain" / "seed_app_settings.json"

_DEAD_KEYS = (
    "staging_mode",
    "newsletter_email",
    "local_database_url",
    "repo_root",
    "site_description",
    "site_tagline",
)

# Fat-finger floor: keys that sit next to the dead ones in the seed and MUST
# survive — the live newsletter key (not the dropped legacy alias), the kept
# social identity + api alias blind-spots, and a model pin from the same block.
_KEEP_KEYS = (
    "newsletter_from_email",
    "api_url",
    "social_x_handle",
    "social_x_url",
    "podcast_script_model",
)


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


@pytest.fixture(scope="module")
def migration_module():
    path = _MIGRATIONS_DIR / "20260711_193000_drop_zero_reader_orphan_settings_batch2.py"
    spec = importlib.util.spec_from_file_location("_drop_zero_reader_orphans_batch2", path)
    assert spec and spec.loader, "could not load the batch-2 drop migration"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seeds_key(seeds_text: str, key: str) -> bool:
    return re.search(rf"VALUES \('{re.escape(key)}',", seeds_text) is not None


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    assert not _seeds_key(baseline_seeds_text, key), (
        f"{key!r} is back in 0000_baseline.seeds.sql — it has no reader "
        "(zero-reader orphan retired 2026-07-11, batch 2). Leave it out."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_brain_seed(brain_seed_keys: set[str], key: str) -> None:
    assert key not in brain_seed_keys, (
        f"{key!r} is back in brain/seed_app_settings.json — a fresh brain "
        "bootstrap would resurrect it. Remove it from the brain seed."
    )


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_defaults(defaults_keys: set[str], key: str) -> None:
    assert key not in defaults_keys, (
        f"{key!r} is back in settings_defaults.DEFAULTS — seed_all_defaults would "
        "re-insert it on the next boot. Remove it from DEFAULTS."
    )


@pytest.mark.parametrize("key", _KEEP_KEYS)
def test_live_keep_keys_still_seeded(baseline_seeds_text: str, key: str) -> None:
    assert _seeds_key(baseline_seeds_text, key), (
        f"live/keep key {key!r} was lost from 0000_baseline.seeds.sql"
    )


def test_migration_targets_exactly_the_dead_keys(migration_module) -> None:
    assert tuple(migration_module._DEAD_KEYS) == _DEAD_KEYS


def test_no_overlap_between_dead_and_keep() -> None:
    assert not (set(_DEAD_KEYS) & set(_KEEP_KEYS))


def test_migration_exposes_runner_interface(migration_module) -> None:
    assert callable(migration_module.up)
    assert callable(migration_module.down)
