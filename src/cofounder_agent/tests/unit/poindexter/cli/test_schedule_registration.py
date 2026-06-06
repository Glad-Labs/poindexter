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
from click.testing import CliRunner


@pytest.mark.unit
def test_schedule_group_is_registered():
    """`poindexter schedule batch/list/show/shift/clear` are reachable
    through the main CLI."""
    import click

    from poindexter.cli.app import main
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


# ---------------------------------------------------------------------------
# schedule batch — option contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_schedule_batch_required_options():
    """batch must declare --count, --interval, and --start as required
    options so a partial invocation fails before touching the DB."""
    from poindexter.cli.schedule import schedule_group

    batch = schedule_group.commands["batch"]
    param_map = {p.name: p for p in batch.params}
    for name in ("count", "interval", "start"):
        assert name in param_map, f"batch missing required option: {name}"
        assert param_map[name].required, f"batch --{name} must be required"


@pytest.mark.unit
def test_schedule_batch_has_force_and_ordered_by():
    """batch must expose --force (re-schedule idempotency) and
    --ordered-by (source-queue sort) so operator batch workflows
    don't silently ignore these flags after a refactor."""
    from poindexter.cli.schedule import schedule_group

    batch = schedule_group.commands["batch"]
    param_names = {p.name for p in batch.params}
    assert "force" in param_names
    assert "ordered_by" in param_names


# ---------------------------------------------------------------------------
# schedule shift — mutual-exclusion guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_schedule_shift_exits_2_when_neither_post_id_nor_all():
    """shift with no target is a user error; must exit 2 before any DB IO."""
    from poindexter.cli.schedule import schedule_group

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(schedule_group, ["shift", "--by", "1h"])
    assert result.exit_code == 2


@pytest.mark.unit
def test_schedule_shift_exits_2_when_both_post_id_and_all():
    """shift rejects ambiguous input (both a specific post_id and --all)."""
    from poindexter.cli.schedule import schedule_group

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(schedule_group, ["shift", "--all", "--by", "1h", "abc123"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# schedule clear — mutual-exclusion guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_schedule_clear_exits_2_when_neither_post_id_nor_all():
    """clear with no target must exit 2 before any DB IO."""
    from poindexter.cli.schedule import schedule_group

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(schedule_group, ["clear"])
    assert result.exit_code == 2


@pytest.mark.unit
def test_schedule_clear_exits_2_when_both_post_id_and_all():
    """clear rejects both a specific post_id and --all being supplied."""
    from poindexter.cli.schedule import schedule_group

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(schedule_group, ["clear", "--all", "abc123"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# publish-at — mutual-exclusion guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_publish_at_exits_2_when_neither_time_spec_nor_in_given():
    """publish-at with no time information exits 2 before any DB IO."""
    from poindexter.cli.schedule import publish_at_command

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(publish_at_command, ["abc123"])
    assert result.exit_code == 2


@pytest.mark.unit
def test_publish_at_exits_2_when_both_time_spec_and_in_given():
    """publish-at rejects both a positional TIME_SPEC and --in being supplied."""
    from poindexter.cli.schedule import publish_at_command

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        publish_at_command, ["abc123", "2026-06-10T09:00:00", "--in", "2h"]
    )
    assert result.exit_code == 2
