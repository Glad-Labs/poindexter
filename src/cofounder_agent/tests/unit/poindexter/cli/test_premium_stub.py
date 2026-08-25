"""The retired `poindexter premium` group leaves a one-release pointer stub.

The Lemon Squeezy license-activation group was removed 2026-08-24 (no code
ever read the ``premium_*`` settings it stamped; Pro delivery is GitHub
collaborator access via ``poindexter pro``, glad-labs-stack#3216). Per
``feedback_backcompat_now_required`` the old name stays callable for one
release as a hidden stub that names the replacement and exits non-zero —
the CLI equivalent of 410 Gone. Delete this file together with the stub.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from poindexter.cli.app import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.parametrize(
    "argv",
    [
        ["premium"],
        ["premium", "activate", "lmsqueezy-key-here"],
        ["premium", "deactivate"],
        ["premium", "status"],
    ],
)
def test_old_invocations_get_the_pointer_not_a_usage_error(runner, argv) -> None:
    """Every historical invocation shape resolves to the stub, fails loud, and
    names `poindexter pro` — never click's bare 'No such command'."""
    result = runner.invoke(main, argv)
    assert result.exit_code == 2
    assert "retired" in result.stderr
    assert "poindexter pro" in result.stderr
    assert "No such command" not in result.stderr


def test_stub_is_hidden_from_help(runner) -> None:
    """The stub exists for muscle memory, not discovery — top-level help
    advertises `pro`, not the retired name."""
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "premium" not in result.output
    assert "pro" in result.output
