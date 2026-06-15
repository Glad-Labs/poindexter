"""The ``poindexter`` CLI root group loads POINDEXTER_SECRET_KEY before dispatch.

Regression: a bare ``poindexter <cmd>`` shell that read or wrote an encrypted
secret raised ``SecretsError: POINDEXTER_SECRET_KEY env var is required`` (or
``Could not decrypt …``) because only a handful of commands loaded the key from
``bootstrap.toml``. Worker / brain containers get the key in their env at
startup, but host CLI invocations did not — so ``auth migrate-*``,
``webhooks/stores/publishers set-secret``, ``pipeline resume`` and friends all
failed, silently disabling the secret-gated feature.

The root callback now loads the key once, covering every current and future
subcommand. ``ensure_secret_key`` is best-effort + idempotent, so this is safe
for commands that don't touch secrets.
"""

from __future__ import annotations

from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from poindexter.cli.app import main


@pytest.mark.unit
def test_root_callback_loads_secret_key_before_dispatch():
    """Invoking any subcommand runs the root callback, which loads the key."""
    calls = {"n": 0}

    def _fake_ensure() -> bool:
        calls["n"] += 1
        return True

    runner = CliRunner()
    # Patch at the source module: main() imports ensure_secret_key lazily from
    # poindexter.cli._bootstrap, so the deferred ``from ._bootstrap import …``
    # picks up this replacement.
    with patch("poindexter.cli._bootstrap.ensure_secret_key", _fake_ensure):
        # `settings` is a group; invoked with no sub-subcommand, click runs the
        # ROOT callback (which must load the key) and then shows the settings
        # group help. This exercises main() without any DB / network I/O.
        runner.invoke(main, ["settings"])

    assert calls["n"] >= 1, "root callback must call ensure_secret_key()"


@pytest.mark.unit
def test_backup_group_registered():
    """The Tier 2 offsite-backup group + its core subcommands are wired in."""
    assert "backup" in main.commands
    backup = main.commands["backup"]
    assert isinstance(backup, click.Group)
    for sub in ("setup", "status", "run", "verify", "snapshots"):
        assert sub in backup.commands, f"missing `backup {sub}`"
