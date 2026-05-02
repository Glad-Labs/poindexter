"""Unit tests for the ``poindexter auth migrate-scripts`` helper
(Glad-Labs/poindexter#248).

Focused on ``_write_bootstrap_oauth_creds`` — the one piece of logic
unique to migrate-scripts (the rest goes through
``_provision_consumer_client``, which is shared with migrate-cli /
migrate-brain and is exercised by their integration paths).

The helper does an in-place TOML edit so we don't lose existing keys
(``database_url``, ``api_token``, etc.). Test that:

* A missing file is created cleanly with both keys.
* An existing file with unrelated keys preserves them.
* An existing file with old script-OAuth keys gets them replaced
  in-place (no duplication).
"""

from __future__ import annotations

from pathlib import Path

from poindexter.cli.auth import _write_bootstrap_oauth_creds


def test_creates_missing_file(tmp_path, monkeypatch):
    """No bootstrap.toml present — the helper must create one with
    both OAuth keys."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ok = _write_bootstrap_oauth_creds("pdx_abc123", "secret-xyz")
    assert ok is True
    written = (tmp_path / ".poindexter" / "bootstrap.toml").read_text(encoding="utf-8")
    assert 'scripts_oauth_client_id = "pdx_abc123"' in written
    assert 'scripts_oauth_client_secret = "secret-xyz"' in written


def test_preserves_unrelated_keys(tmp_path, monkeypatch):
    """An existing file with database_url / api_token must not lose
    those lines when we add the OAuth keys."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    bootstrap_dir = tmp_path / ".poindexter"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "bootstrap.toml").write_text(
        'database_url = "postgresql://existing"\n'
        'api_token = "legacy-token"\n',
        encoding="utf-8",
    )

    ok = _write_bootstrap_oauth_creds("pdx_new", "secret-new")
    assert ok is True
    written = (bootstrap_dir / "bootstrap.toml").read_text(encoding="utf-8")
    assert 'database_url = "postgresql://existing"' in written
    assert 'api_token = "legacy-token"' in written
    assert 'scripts_oauth_client_id = "pdx_new"' in written
    assert 'scripts_oauth_client_secret = "secret-new"' in written


def test_in_place_update_no_duplication(tmp_path, monkeypatch):
    """Re-running migrate-scripts should overwrite the previous OAuth
    lines, not append duplicates."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    bootstrap_dir = tmp_path / ".poindexter"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "bootstrap.toml").write_text(
        'database_url = "postgresql://existing"\n'
        'scripts_oauth_client_id = "pdx_old"\n'
        'scripts_oauth_client_secret = "old-secret"\n',
        encoding="utf-8",
    )

    ok = _write_bootstrap_oauth_creds("pdx_rotated", "rotated-secret")
    assert ok is True

    written = (bootstrap_dir / "bootstrap.toml").read_text(encoding="utf-8")
    # Old values gone.
    assert "pdx_old" not in written
    assert "old-secret" not in written
    # New values present.
    assert 'scripts_oauth_client_id = "pdx_rotated"' in written
    assert 'scripts_oauth_client_secret = "rotated-secret"' in written
    # Each key appears exactly once (no append-on-rewrite duplication).
    assert written.count("scripts_oauth_client_id") == 1
    assert written.count("scripts_oauth_client_secret") == 1
