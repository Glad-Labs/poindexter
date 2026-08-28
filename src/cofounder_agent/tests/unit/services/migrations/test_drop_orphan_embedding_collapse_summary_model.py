"""Regression guard for ``embedding_collapse_summary_model``, retired 2026-06-24.

Follow-up to ``20260828_005606_drop_the_orphaned_embedding_collapse_summary_model_setting``.

The standalone embeddings-collapse job folded into the ``embeddings_collapse``
retention handler on 2026-06-24; that handler reads its model from
``retention_policies.config->>'summary_model'`` (per-policy on purpose, so two
policies can collapse at different model sizes) and consults no app_setting.
The key has had no reader since.

Unlike most retired settings, this one was never in any seed source, so the
migration is the whole fix and there is no seed half to remove. These tests pin
that: if a future baseline regen or a "restore the missing model pin" edit ever
puts it back into a seed source, the migration would delete it once and the next
boot would re-insert it, giving an install a permanently resurrecting dead row.

**The fat-finger floor matters more here than usual.**
``memory_compression_summary_model`` shares the naming convention and looks like
the symmetric twin. It is not: it is LIVE, baseline-seeded, and read from
app_settings by ``retention_summarize_to_table``, which fails loud when it is
empty. It ALSO carries a NULL ``last_read_at`` — because it is read via raw SQL
(``_get_setting(pool, ...)``) and the #756 read telemetry only stamps
``SiteConfig.get`` / ``SettingsService.get`` — so the one signal that would
normally distinguish them says "dead" for both. A prefix sweep, or anyone
trusting that NULL, takes out a load-bearing key. It is asserted still-seeded
below.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[6]
_MIGRATIONS_DIR = _REPO / "src" / "cofounder_agent" / "services" / "migrations"
_DEFAULTS_PY = _REPO / "src" / "cofounder_agent" / "services" / "settings_defaults.py"
_BRAIN_SEED = _REPO / "brain" / "seed_app_settings.json"
_MIGRATION = (
    _MIGRATIONS_DIR
    / "20260828_005606_drop_the_orphaned_embedding_collapse_summary_model_setting.py"
)
_COLLAPSE_HANDLER = (
    _REPO / "src" / "cofounder_agent" / "services" / "integrations" / "handlers"
    / "retention_embeddings_collapse.py"
)

_DEAD_KEYS = ("embedding_collapse_summary_model",)

# Keys that MUST survive. The first is the look-alike this migration must never
# widen to; the rest sit in the same seed neighbourhood.
_KEEP_KEYS = (
    "memory_compression_summary_model",
    "vision_alt_model",
    "structured_extraction_model",
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


def _seeds_key(seeds_text: str, key: str) -> bool:
    return re.search(rf"VALUES \('{re.escape(key)}',", seeds_text) is not None


@pytest.mark.parametrize("key", _DEAD_KEYS)
def test_dead_key_absent_from_baseline_seeds(baseline_seeds_text: str, key: str) -> None:
    assert not _seeds_key(baseline_seeds_text, key), (
        f"{key!r} is seeded in 0000_baseline.seeds.sql — it has had no reader "
        "since 2026-06-24 (embeddings_collapse reads retention_policies.config "
        "summary_model). Seeding it would resurrect the row every boot."
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
        f"live/keep key {key!r} was lost from 0000_baseline.seeds.sql. "
        "memory_compression_summary_model in particular is READ by "
        "retention_summarize_to_table and fails loud when empty — its NULL "
        "last_read_at is a raw-SQL telemetry blind spot, NOT evidence it is dead."
    )


def test_no_overlap_between_dead_and_keep() -> None:
    assert not (set(_DEAD_KEYS) & set(_KEEP_KEYS))


def test_migration_targets_exactly_the_dead_key() -> None:
    """The migration must name its key literally, never sweep by prefix.

    A ``LIKE '%summary_model'`` would also match the live
    ``memory_compression_summary_model``.
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
    # Scope the pattern-sweep check to actual SQL literals. Checking the raw
    # file is too blunt twice over: the docstring says "Unlike", and the
    # ORPHANED_KEYS comment says "prefix/LIKE" — both legitimate prose that a
    # substring (or even word-boundary) scan over the whole source trips on.
    sql_literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "app_settings" in n.value
    ]
    assert sql_literals, "no app_settings SQL found in the migration"
    for sql in sql_literals:
        assert not re.search(r"\bLIKE\b", sql, re.IGNORECASE), (
            "migration SQL must not sweep keys by LIKE pattern — it would also "
            f"match the live memory_compression_summary_model. Offending SQL: {sql!r}"
        )


def test_collapse_handler_reads_policy_config_not_app_settings() -> None:
    """The reason the key is dead — pinned so the fix can't silently revert.

    If someone reintroduces an app_settings read here, the key stops being an
    orphan and this migration becomes wrong.
    """
    src = _COLLAPSE_HANDLER.read_text(encoding="utf-8")
    assert 'config.get("summary_model")' in src, (
        "embeddings_collapse no longer reads summary_model from its "
        "retention_policies.config JSONB — re-check whether "
        "embedding_collapse_summary_model is still safe to drop."
    )
