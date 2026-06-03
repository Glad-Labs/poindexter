"""Backend-root override for scripts/ci/migrations_smoke.py (poindexter#441).

The brain's restore-test probe runs migrations_smoke.py inside the worker
container, where the backend is mounted at /app (not under a repo-root
tree). These tests pin the POINDEXTER_BACKEND_ROOT override and confirm
the CI default (env unset) is unchanged. Lives in the backend test suite
so it runs under the right pytest config and gets normal CI coverage.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts" / "ci" / "migrations_smoke.py").is_file():
            return parent
    raise RuntimeError("could not locate scripts/ci/migrations_smoke.py")


def _load(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("POINDEXTER_BACKEND_ROOT", raising=False)
    else:
        monkeypatch.setenv("POINDEXTER_BACKEND_ROOT", env_value)
    sys.path.insert(0, str(_repo_root() / "scripts" / "ci"))
    sys.modules.pop("migrations_smoke", None)
    return importlib.import_module("migrations_smoke")


def test_env_override_sets_backend_root(monkeypatch, tmp_path):
    mod = _load(monkeypatch, str(tmp_path))
    assert mod.BACKEND_ROOT == tmp_path
    assert mod.MIGRATIONS_DIR == tmp_path / "services" / "migrations"


def test_no_env_falls_back_to_repo_layout(monkeypatch):
    mod = _load(monkeypatch, None)
    # parents[2] of scripts/ci/migrations_smoke.py is the repo root.
    assert mod.BACKEND_ROOT.name == "cofounder_agent"
    assert mod.MIGRATIONS_DIR.name == "migrations"


# ---------------------------------------------------------------------------
# _evaluate: strict (CI) vs restored-backup (relaxed) verdict (poindexter#441)
# ---------------------------------------------------------------------------
_FILES = {"a.py", "b.py", "c.py"}


def test_evaluate_clean_passes_in_both_modes(monkeypatch):
    mod = _load(monkeypatch, None)
    for relaxed in (False, True):
        failed, _ = mod._evaluate(
            runner_ok=True, applied_names=set(_FILES),
            file_names=set(_FILES), allow_historical=relaxed)
        assert failed is False


def test_evaluate_strict_fails_on_historical_extra_rows(monkeypatch):
    mod = _load(monkeypatch, None)
    applied = set(_FILES) | {"0000_baseline.py", "20260101_old_squashed.py"}
    failed, messages = mod._evaluate(
        runner_ok=True, applied_names=applied,
        file_names=set(_FILES), allow_historical=False)
    assert failed is True
    assert any("no matching file" in m for m in messages)


def test_evaluate_restored_backup_tolerates_extra_rows(monkeypatch):
    mod = _load(monkeypatch, None)
    # mirrors prod: many historical rows, every current file applied
    applied = set(_FILES) | {f"hist_{i}.py" for i in range(50)}
    failed, messages = mod._evaluate(
        runner_ok=True, applied_names=applied,
        file_names=set(_FILES), allow_historical=True)
    assert failed is False
    assert any("tolerating 50 historical" in m for m in messages)


def test_evaluate_restored_backup_still_fails_on_missing(monkeypatch):
    mod = _load(monkeypatch, None)
    # a current migration did NOT apply to the restored DB — a real problem
    failed, messages = mod._evaluate(
        runner_ok=True, applied_names={"a.py"},
        file_names=set(_FILES), allow_historical=True)
    assert failed is True
    assert any("did not record a schema_migrations row" in m for m in messages)


def test_evaluate_restored_backup_still_fails_on_runner_error(monkeypatch):
    mod = _load(monkeypatch, None)
    failed, messages = mod._evaluate(
        runner_ok=False, applied_names=set(_FILES),
        file_names=set(_FILES), allow_historical=True)
    assert failed is True
    assert any("run_migrations() reported" in m for m in messages)
