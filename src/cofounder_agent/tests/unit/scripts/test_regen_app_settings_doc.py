"""Unit tests for ``scripts/regen-app-settings-doc.py`` (Glad-Labs/poindexter#439).

Focused on ``resolved_stamp`` — the seam CI uses to pin the "Auto-generated
on {date}" banner across runs of the same source state so the scheduled
regen workflow doesn't open a stale-timestamp PR every night when nothing
real has changed. The rest of the script (DB query + markdown render) is
exercised end-to-end by the workflow itself against a fresh Postgres.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
SCRIPT_PATH = REPO_ROOT / "scripts" / "regen-app-settings-doc.py"


# Synthetic GitHub App installation tokens — never real, never minted.
# _CLASSIC is the opaque ghs_ + 36-alphanumeric shape; _STATELESS is the
# ghs_<APPID>_<JWT> format GitHub began rolling out 2026-04-27 (~520 chars
# in the wild, two dots, charset [A-Za-z0-9._-]).
_CLASSIC_GHS = "ghs_16C7e42F292c6912E7710c838347Ae178B4a"
_STATELESS_GHS = (
    "ghs_1234567_eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ0ZXN0IiwiaWF0IjoxNzAwMDAwMDAwLCJleHAiOjE3MDAwMDM2MDB9"
    ".dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
)

def _load_script_module() -> ModuleType:
    """Load the hyphen-named script via importlib + a brain.bootstrap stub.

    The script does ``from brain.bootstrap import resolve_database_url`` at
    import time. The stub lets the module load in a pure-unit context where
    neither the real brain package nor asyncpg need to be importable.
    """

    if "brain.bootstrap" not in sys.modules:
        stub = types.ModuleType("brain.bootstrap")
        stub.resolve_database_url = lambda: ""  # type: ignore[attr-defined]
        brain_pkg = sys.modules.setdefault("brain", types.ModuleType("brain"))
        brain_pkg.bootstrap = stub  # type: ignore[attr-defined]
        sys.modules["brain.bootstrap"] = stub

    spec = spec_from_file_location("regen_app_settings_doc", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def regen_module() -> ModuleType:
    return _load_script_module()


def test_looks_secret_flags_github_app_installation_tokens(
    regen_module: ModuleType,
) -> None:
    """Both installation-token shapes read as secrets.

    The classic opaque token was already caught incidentally by the generic
    ``^[A-Za-z0-9]{32,}$`` rule, but the stateless ``ghs_<APPID>_<JWT>``
    format (GitHub rollout began 2026-04-27) contains ``_`` and ``.`` and
    so escapes it — hence the explicit ``^ghs_`` prefix rule.
    """

    for token in (_CLASSIC_GHS, _STATELESS_GHS):
        # Neutral key name on purpose: "..._key"/"..._token" would trip
        # _SECRET_KEY_HINTS and return True before _SECRET_PATTERNS is
        # consulted, so the test would pass without exercising ^ghs_.
        assert regen_module.looks_secret("deploy_target", token) is True


def test_resolved_stamp_uses_override_when_set(regen_module: ModuleType) -> None:
    assert (
        regen_module.resolved_stamp({"REGEN_DATE_OVERRIDE": "2026-01-15"})
        == "2026-01-15"
    )


def test_resolved_stamp_falls_back_to_today_when_unset(regen_module: ModuleType) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert regen_module.resolved_stamp({}) == today


def test_resolved_stamp_treats_blank_override_as_unset(regen_module: ModuleType) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert regen_module.resolved_stamp({"REGEN_DATE_OVERRIDE": ""}) == today
