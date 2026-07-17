"""Contract tests for the app_settings seed VALUE-drift ratchet.

Sibling to ``test_settings_seed_drift_lint``, which guards a different failure
(a migration-deleted key that a seed file resurrects). This one guards *value*
drift between the three seed sources — the gap poindexter#819 named and did not
close:

    settings_seed_drift_lint only catches re-seeded deleted keys, not value
    drift between the two seed sources — which is how this slipped through the
    baseline squash.

The rule under test:

* ``settings_defaults.py::DEFAULTS`` and ``0000_baseline.seeds.sql`` are two
  encodings of the SAME thing (the reference default) and must agree exactly.
* ``brain/seed_app_settings.json`` is a different thing (``_meta.tier == "free"``)
  and may diverge — but only where declared in ``TIER_POLICY`` with a reason.

Synthetic-tree tests build their own three sources in ``tmp_path`` and repoint
the module's path constants, so they assert the *rule* and stay green regardless
of what the real tree currently holds. ``test_real_tree_is_clean`` is the one
that asserts the real tree, and it is the reconciliation's acceptance test.
"""

from __future__ import annotations

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_lint_module():
    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )
    script = repo_root / "scripts" / "ci" / "settings_seed_value_drift_lint.py"
    spec = spec_from_file_location("settings_seed_value_drift_lint", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LINT = _load_lint_module()


def _write_tree(
    tmp_path: Path,
    *,
    code: dict[str, str],
    baseline: dict[str, str],
    brain: dict[str, str],
) -> dict[str, Path]:
    """Write a synthetic trio of seed sources shaped like the real ones."""
    defaults_py = tmp_path / "settings_defaults.py"
    body = "\n".join(f"    {k!r}: {v!r}," for k, v in code.items())
    defaults_py.write_text(
        f'"""synthetic"""\n\nDEFAULTS: dict[str, str] = {{\n{body}\n}}\n',
        encoding="utf-8",
    )

    seeds_sql = tmp_path / "0000_baseline.seeds.sql"
    lines = []
    for k, v in baseline.items():
        esc = v.replace("'", "''")
        lines.append(
            "INSERT INTO app_settings (key, value, category, description, "
            f"is_secret, is_active) VALUES ('{k}', '{esc}', 'general', '', "
            "false, true) ON CONFLICT (key) DO NOTHING;"
        )
    seeds_sql.write_text("\n".join(lines) + "\n", encoding="utf-8")

    brain_json = tmp_path / "seed_app_settings.json"
    brain_json.write_text(
        json.dumps(
            {
                "_meta": {"tier": "free"},
                "settings": [
                    {"key": k, "value": v, "category": "general", "description": ""}
                    for k, v in brain.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    return {"code": defaults_py, "baseline": seeds_sql, "brain": brain_json}


def _point_at(monkeypatch, paths: dict[str, Path]) -> None:
    monkeypatch.setattr(LINT, "DEFAULTS_PY", paths["code"])
    monkeypatch.setattr(LINT, "BASELINE_SEEDS", paths["baseline"])
    monkeypatch.setattr(LINT, "BRAIN_SEED", paths["brain"])


# --------------------------------------------------------------------------
# the rule
# --------------------------------------------------------------------------

def test_clean_tree_passes(tmp_path, monkeypatch) -> None:
    """All three sources agreeing on every overlapping key is clean."""
    _point_at(monkeypatch, _write_tree(
        tmp_path, code={"a": "1"}, baseline={"a": "1"}, brain={"a": "1"}
    ))
    monkeypatch.setattr(LINT, "TIER_POLICY", {})
    assert LINT.main() == 0


def test_code_baseline_divergence_fails(tmp_path, monkeypatch, capsys) -> None:
    """code != baseline is ALWAYS drift — they encode the same thing."""
    _point_at(monkeypatch, _write_tree(
        tmp_path, code={"a": "1"}, baseline={"a": "2"}, brain={}
    ))
    monkeypatch.setattr(LINT, "TIER_POLICY", {})
    assert LINT.main() == 1
    out = capsys.readouterr().out
    assert "a" in out and "'1'" in out and "'2'" in out


def test_tier_policy_cannot_excuse_code_baseline_drift(tmp_path, monkeypatch) -> None:
    """TIER_POLICY is about the brain seed only. It must NOT be usable as a
    blanket escape hatch for code<->baseline drift, or the rule is toothless."""
    _point_at(monkeypatch, _write_tree(
        tmp_path, code={"a": "1"}, baseline={"a": "2"}, brain={}
    ))
    monkeypatch.setattr(LINT, "TIER_POLICY", {"a": "tier reason"})
    assert LINT.main() == 1


def test_brain_divergence_fails_when_not_declared(tmp_path, monkeypatch) -> None:
    """An undeclared brain divergence is drift."""
    _point_at(monkeypatch, _write_tree(
        tmp_path, code={"a": "1"}, baseline={"a": "1"}, brain={"a": "9"}
    ))
    monkeypatch.setattr(LINT, "TIER_POLICY", {})
    assert LINT.main() == 1


def test_brain_divergence_passes_when_declared(tmp_path, monkeypatch) -> None:
    """A declared brain divergence is the free/paid product boundary."""
    _point_at(monkeypatch, _write_tree(
        tmp_path, code={"a": "1"}, baseline={"a": "1"}, brain={"a": "9"}
    ))
    monkeypatch.setattr(LINT, "TIER_POLICY", {"a": "free tier caps this lower"})
    assert LINT.main() == 0


def test_stale_tier_policy_entry_fails(tmp_path, monkeypatch, capsys) -> None:
    """An entry for a key that no longer diverges must fail, so the allowlist
    cannot rot into a list of claims that stopped being true."""
    _point_at(monkeypatch, _write_tree(
        tmp_path, code={"a": "1"}, baseline={"a": "1"}, brain={"a": "1"}
    ))
    monkeypatch.setattr(LINT, "TIER_POLICY", {"a": "no longer true"})
    assert LINT.main() == 1
    assert "stale" in capsys.readouterr().out.lower()


def test_non_overlapping_keys_are_not_drift(tmp_path, monkeypatch) -> None:
    """A key only one source seeds has nothing to disagree with."""
    _point_at(monkeypatch, _write_tree(
        tmp_path, code={"a": "1", "only_code": "x"},
        baseline={"a": "1", "only_seed": "y"}, brain={"only_brain": "z"},
    ))
    monkeypatch.setattr(LINT, "TIER_POLICY", {})
    assert LINT.main() == 0


def test_quote_escaping_roundtrips(tmp_path, monkeypatch) -> None:
    """SQL doubles single-quotes; the parser must un-escape before comparing, or
    every apostrophe-carrying value (niche_goal_descriptions) reads as drift."""
    val = "Topic that teaches the reader something they didn't know"
    _point_at(monkeypatch, _write_tree(
        tmp_path, code={"a": val}, baseline={"a": val}, brain={}
    ))
    monkeypatch.setattr(LINT, "TIER_POLICY", {})
    assert LINT.main() == 0


# --------------------------------------------------------------------------
# the allowlist's own hygiene
# --------------------------------------------------------------------------

def test_every_tier_policy_entry_has_a_reason() -> None:
    """A bare key with no reason is how an allowlist becomes a junk drawer."""
    missing = [k for k, v in LINT.TIER_POLICY.items() if not (v or "").strip()]
    assert not missing, f"TIER_POLICY entries without a reason: {missing}"


# --------------------------------------------------------------------------
# the real tree
# --------------------------------------------------------------------------

def test_real_tree_is_clean() -> None:
    """The reconciliation's acceptance test: every conflict across the three
    real seed sources is either resolved or declared."""
    assert LINT.main() == 0, (
        "app_settings seed sources disagree — run "
        "`python scripts/ci/settings_seed_value_drift_lint.py` for the list"
    )
