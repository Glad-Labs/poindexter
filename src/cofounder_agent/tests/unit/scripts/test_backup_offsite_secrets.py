"""Unit tests for scripts/_backup_offsite_secrets.py (poindexter#386)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

# Load the script module by path (scripts/ isn't a package). From this test
# file: parents[0]=scripts, [1]=unit, [2]=tests, [3]=cofounder_agent, [4]=src,
# [5]=repo root — and the helper lives at <root>/scripts/.
_SCRIPT = (
    Path(__file__).resolve().parents[5] / "scripts" / "_backup_offsite_secrets.py"
)
_SPEC = importlib.util.spec_from_file_location("_backup_offsite_secrets", _SCRIPT)
assert _SPEC and _SPEC.loader
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_render_env_emits_three_keys():
    out = mod._render_env(
        {
            "offsite_backup_restic_password": "pw-123",
            "offsite_backup_s3_access_key_id": "akid-456",
            "offsite_backup_s3_secret_access_key": "secret-789",
        }
    )
    assert "RESTIC_PASSWORD=pw-123" in out
    assert "AWS_ACCESS_KEY_ID=akid-456" in out
    assert "AWS_SECRET_ACCESS_KEY=secret-789" in out


def test_render_env_missing_keys_emit_empty_assignments():
    out = mod._render_env({})
    # Loud-inert: explicit empty assignments so the runner idles, not crashes.
    assert "RESTIC_PASSWORD=" in out
    assert "AWS_ACCESS_KEY_ID=" in out
    assert "AWS_SECRET_ACCESS_KEY=" in out
    # No stray plaintext.
    assert "None" not in out
