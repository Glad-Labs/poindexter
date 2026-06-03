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
