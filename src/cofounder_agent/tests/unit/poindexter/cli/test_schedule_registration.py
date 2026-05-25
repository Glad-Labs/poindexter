"""Regression: both `poindexter schedule` and `poindexter publish-at`
must stay registered on the main CLI app.

The schedule group + publish-at shortcut have existed since #147 but
the publish-at shortcut wasn't actually wired into the main app until
2026-05-25 (caught by the audit triggered by Matt's "finish schedule
batching + observability" ask). Pin the registration here so a future
refactor of poindexter/cli/app.py doesn't silently drop either entry.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_schedule_group_is_registered():
    """`poindexter schedule batch/list/show/shift/clear` are reachable
    through the main CLI."""
    from poindexter.cli.app import main

    import click
    assert isinstance(main, click.Group)
    assert "schedule" in main.commands
    schedule = main.commands["schedule"]
    assert isinstance(schedule, click.Group)
    # The group must expose at least the canonical operator subcommands.
    expected = {"batch", "list", "show", "shift", "clear"}
    actual = set(schedule.commands.keys())
    missing = expected - actual
    assert not missing, f"schedule group missing subcommands: {missing}"


@pytest.mark.unit
def test_publish_at_shortcut_is_registered():
    """The single-post convenience shortcut `poindexter publish-at` is
    a top-level command (not a schedule subcommand). Registration was
    added 2026-05-25 alongside the System Health observability panels;
    before that the import existed but the `main.add_command` call was
    missing, so the command was unreachable from the CLI surface."""
    import click
    from poindexter.cli.app import main

    assert isinstance(main, click.Group)
    assert "publish-at" in main.commands, (
        "publish-at command not registered on main CLI app — the "
        "import-without-add_command regression has re-appeared"
    )

    cmd = main.commands["publish-at"]
    param_names = {p.name for p in cmd.params}
    # Must accept the single-post arguments operators rely on.
    assert "post_id" in param_names
    assert "in_delta" in param_names  # the `--in DUR` relative form
    assert "force" in param_names
