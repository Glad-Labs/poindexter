"""Surface tests for the CLI noun-group consolidation (#1652, sibling of #1340).

The 9 flat HITL/publish verbs fold into the ``gates`` / ``schedule`` noun-groups;
the old flat names survive as hidden deprecated aliases. The ``post`` (singular)
group merges into ``posts``. These tests pin the *new* canonical group paths and
the alias deprecation behavior. The pre-existing per-command tests
(``test_approval_cli`` / ``test_publish_approval_cli`` / ``test_schedule_cli`` /
``test_post_create_media_validation``) still cover the canonical command bodies
unchanged — they invoke the same command objects this module now also mounts
under the groups.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


def _alias_by_name(aliases):
    return {a.name: a for a in aliases}


# ===========================================================================
# gates noun-group (from poindexter.cli.approval — pipeline_tasks gates)
# ===========================================================================


class TestGatesGroupConsolidation:
    def test_gates_group_exposes_consolidated_subcommands(self):
        from poindexter.cli.approval import gates_group

        cmds = set(gates_group.commands)
        assert {"approve", "reject", "pending", "show"}.issubset(cmds)
        # The original gate-toggle subcommands stay.
        assert {"list", "set"}.issubset(cmds)

    def test_gates_approve_delegates_to_service(self, runner):
        from poindexter.cli.approval import gates_group

        with patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "poindexter.cli.approval._make_site_config",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "poindexter.cli.approval._resolve_task_id_prefix",
            new=AsyncMock(return_value="t-1"),
        ), patch(
            "services.approval_service.approve",
            new=AsyncMock(return_value={"ok": True, "task_id": "t-1", "gate_name": "g"}),
        ) as mock_svc:
            result = runner.invoke(gates_group, ["approve", "t-1", "--feedback", "ok"])

        assert result.exit_code == 0, result.output
        assert mock_svc.await_args.kwargs["task_id"] == "t-1"
        assert mock_svc.await_args.kwargs["feedback"] == "ok"

    def test_gates_pending_lists(self, runner):
        from poindexter.cli.approval import gates_group

        with patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "services.approval_service.list_pending",
            new=AsyncMock(return_value=[]),
        ):
            result = runner.invoke(gates_group, ["pending"])

        assert result.exit_code == 0
        assert "no pending" in result.output.lower()

    def test_gates_show_delegates(self, runner):
        from poindexter.cli.approval import gates_group

        payload = {
            "task_id": "t-1", "gate_name": "g", "artifact": {},
            "gate_paused_at": None, "status": "in_progress", "topic": "x", "title": "y",
        }
        with patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "poindexter.cli.approval._resolve_task_id_prefix",
            new=AsyncMock(return_value="t-1"),
        ), patch(
            "services.approval_service.show_pending",
            new=AsyncMock(return_value=payload),
        ):
            result = runner.invoke(gates_group, ["show", "t-1", "--json"])

        assert result.exit_code == 0, result.output


class TestApprovalFlatAliases:
    def test_aliases_are_hidden_and_cover_old_names(self):
        from poindexter.cli.approval import APPROVAL_FLAT_ALIASES

        by_name = _alias_by_name(APPROVAL_FLAT_ALIASES)
        assert set(by_name) == {"approve", "reject", "list-pending", "show-pending"}
        assert all(a.hidden for a in APPROVAL_FLAT_ALIASES)

    def test_list_pending_alias_warns_and_delegates(self, runner):
        from poindexter.cli.approval import APPROVAL_FLAT_ALIASES

        alias = _alias_by_name(APPROVAL_FLAT_ALIASES)["list-pending"]
        with patch(
            "poindexter.cli.approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "services.approval_service.list_pending",
            new=AsyncMock(return_value=[]),
        ):
            result = CliRunner().invoke(alias, [])

        assert result.exit_code == 0
        assert "deprecated" in result.stderr.lower()
        assert "gates pending" in result.stderr


# ===========================================================================
# schedule noun-group (from publish_approval — posts publish gate + publish-at)
# ===========================================================================

FULL = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"


async def _fake_with_pool(fn):
    """Stand-in for schedule._with_pool — hand the inner fn a dummy pool."""
    return await fn(MagicMock())


def _sched_result(*, ok=True, detail="ok"):
    from types import SimpleNamespace

    return SimpleNamespace(ok=ok, detail=detail, count=1 if ok else 0, rows=[])


class TestScheduleGroupConsolidation:
    def test_schedule_group_exposes_publish_and_at_subcommands(self):
        import poindexter.cli.publish_approval  # noqa: F401 — attaches the gate verbs
        from poindexter.cli.schedule import schedule_group

        cmds = set(schedule_group.commands)
        assert {"approve", "reject", "pending", "show-pending", "at"}.issubset(cmds)
        # The original queue subcommands stay — including `show`.
        assert {"batch", "list", "show", "shift", "clear"}.issubset(cmds)

    def test_schedule_show_stays_the_scheduling_detail_command(self):
        # Backcompat: `schedule show` must keep meaning schedule-detail, NOT the
        # publish gate (which is `schedule show-pending`).
        import poindexter.cli.publish_approval  # noqa: F401
        from poindexter.cli.schedule import schedule_group, schedule_show

        assert schedule_group.commands["show"] is schedule_show

    def test_schedule_approve_delegates_to_posts_approval_service(self, runner):
        import poindexter.cli.publish_approval  # noqa: F401
        from poindexter.cli.schedule import schedule_group

        with patch(
            "poindexter.cli.publish_approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "poindexter.cli.publish_approval._make_site_config",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "poindexter.cli.publish_approval._resolve_post_id",
            new=AsyncMock(return_value="p-1"),
        ), patch(
            "services.posts_approval_service.approve_publish",
            new=AsyncMock(return_value={"gate_name": "final_publish_approval"}),
        ) as mock_svc:
            result = runner.invoke(schedule_group, ["approve", "p-1", "--feedback", "ship"])

        assert result.exit_code == 0, result.output
        assert mock_svc.await_args.kwargs["post_id"] == "p-1"

    def test_schedule_show_pending_delegates(self, runner):
        import poindexter.cli.publish_approval  # noqa: F401
        from poindexter.cli.schedule import schedule_group

        payload = {
            "post_id": "p-1", "gate_name": "final_publish_approval",
            "gate_paused_at": None, "status": "scheduled", "published_at": None,
            "slug": "s", "title": "t", "artifact": {},
        }
        with patch(
            "poindexter.cli.publish_approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "poindexter.cli.publish_approval._resolve_post_id",
            new=AsyncMock(return_value="p-1"),
        ), patch(
            "services.posts_approval_service.show_pending_publish",
            new=AsyncMock(return_value=payload),
        ):
            result = runner.invoke(schedule_group, ["show-pending", "p-1"])

        assert result.exit_code == 0, result.output

    def test_schedule_at_delegates_to_scheduling_service(self, runner):
        from poindexter.cli.schedule import schedule_group

        with patch(
            "poindexter.cli.schedule._with_pool", new=_fake_with_pool,
        ), patch(
            "poindexter.cli.schedule._load_site_config",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "poindexter.cli.schedule.resolve_uuid_prefix",
            new=AsyncMock(return_value=FULL),
        ), patch(
            "services.scheduling_service.assign_slot",
            new=AsyncMock(return_value=_sched_result()),
        ) as mock_assign:
            result = runner.invoke(schedule_group, ["at", "6bf91cc3", "now"])

        assert result.exit_code == 0, result.output
        assert mock_assign.await_args.args[0] == FULL


class TestScheduleFlatAliases:
    def test_publish_aliases_hidden_and_cover_old_names(self):
        from poindexter.cli.publish_approval import PUBLISH_FLAT_ALIASES

        by_name = _alias_by_name(PUBLISH_FLAT_ALIASES)
        assert set(by_name) == {
            "approve-publish", "reject-publish",
            "list-pending-publish", "show-pending-publish",
        }
        assert all(a.hidden for a in PUBLISH_FLAT_ALIASES)

    def test_publish_at_alias_hidden_and_named(self):
        from poindexter.cli.schedule import SCHEDULE_FLAT_ALIASES

        by_name = _alias_by_name(SCHEDULE_FLAT_ALIASES)
        assert set(by_name) == {"publish-at"}
        assert all(a.hidden for a in SCHEDULE_FLAT_ALIASES)

    def test_approve_publish_alias_warns_and_points_at_schedule_approve(self):
        from poindexter.cli.publish_approval import PUBLISH_FLAT_ALIASES

        alias = _alias_by_name(PUBLISH_FLAT_ALIASES)["approve-publish"]
        with patch(
            "poindexter.cli.publish_approval._make_pool",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ), patch(
            "poindexter.cli.publish_approval._make_site_config",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "poindexter.cli.publish_approval._resolve_post_id",
            new=AsyncMock(return_value="p-1"),
        ), patch(
            "services.posts_approval_service.approve_publish",
            new=AsyncMock(return_value={"gate_name": "final_publish_approval"}),
        ):
            result = CliRunner().invoke(alias, ["p-1"])

        assert result.exit_code == 0
        assert "deprecated" in result.stderr.lower()
        assert "schedule approve" in result.stderr

    def test_publish_at_alias_warns_and_points_at_schedule_at(self):
        from poindexter.cli.schedule import SCHEDULE_FLAT_ALIASES

        alias = _alias_by_name(SCHEDULE_FLAT_ALIASES)["publish-at"]
        with patch(
            "poindexter.cli.schedule._with_pool", new=_fake_with_pool,
        ), patch(
            "poindexter.cli.schedule._load_site_config",
            new=AsyncMock(return_value=MagicMock()),
        ), patch(
            "poindexter.cli.schedule.resolve_uuid_prefix",
            new=AsyncMock(return_value=FULL),
        ), patch(
            "services.scheduling_service.assign_slot",
            new=AsyncMock(return_value=_sched_result()),
        ):
            result = CliRunner().invoke(alias, ["6bf91cc3", "now"])

        assert result.exit_code == 0
        assert "deprecated" in result.stderr.lower()
        assert "schedule at" in result.stderr


# ===========================================================================
# post (singular) merges into posts (plural)
# ===========================================================================


class TestPostsMerge:
    def test_posts_group_exposes_create_canonical(self):
        from poindexter.cli.posts import post_create, posts_group

        assert posts_group.commands.get("create") is post_create

    def test_post_group_is_hidden_with_deprecated_create_alias(self):
        from poindexter.cli.posts import post_create, post_group

        assert post_group.hidden is True
        alias = post_group.commands["create"]
        # A separate alias object, hidden, pointing at the canonical path.
        assert alias is not post_create
        assert alias.hidden is True
        assert "posts create" in f"{alias.short_help or ''} {alias.help or ''}"

    def test_post_create_alias_warns(self):
        from poindexter.cli.posts import post_group

        # The alias warns to stderr before delegating; the warning fires first,
        # so we make the pool factory blow up fast to avoid touching a real DB.
        with patch(
            "poindexter.cli.posts._make_gate_pool",
            new=AsyncMock(side_effect=RuntimeError("no db")),
        ):
            result = CliRunner().invoke(
                post_group, ["create", "--topic", "x"]
            )

        assert "deprecated" in result.stderr.lower()
        assert "posts create" in result.stderr


# ===========================================================================
# schedule.py app_settings SQL straggler fix (#1652 bundled cleanup, #1340)
# ===========================================================================


class TestScheduleStragglerRemoved:
    def test_no_inline_app_settings_sql_in_schedule_module(self):
        import pathlib

        import poindexter.cli.schedule as sched

        src = pathlib.Path(sched.__file__).read_text(encoding="utf-8")
        # The settings read must go through the settings service (SiteConfig),
        # not a hand-rolled query — clears a future adapter-purity entry (#1344).
        assert "FROM app_settings" not in src
        assert "SELECT key, value" not in src

    async def test_load_site_config_routes_through_settings_loader(self):
        from poindexter.cli.schedule import _load_site_config, _SinglePool

        conn = MagicMock()
        conn.fetch = AsyncMock(
            return_value=[{"key": "publish_quiet_hours", "value": "22:00-07:00"}]
        )
        pool = _SinglePool(conn)

        cfg = await _load_site_config(pool)

        # Loading still populates non-secret settings (behavior preserved).
        assert cfg.get("publish_quiet_hours") == "22:00-07:00"
        conn.fetch.assert_awaited()


# ===========================================================================
# root namespace: flat verbs hidden, noun-groups visible (cli/app.py wiring)
# ===========================================================================

_FLAT_VERBS = (
    "approve", "reject", "list-pending", "show-pending",
    "approve-publish", "reject-publish", "list-pending-publish",
    "show-pending-publish", "publish-at",
)


class TestRootNamespaceDecluttered:
    def test_flat_verbs_still_callable_but_hidden(self):
        from poindexter.cli.app import main

        for flat in _FLAT_VERBS:
            assert flat in main.commands, f"{flat} must stay callable (backcompat)"
            assert main.commands[flat].hidden, f"{flat} must be hidden from --help"
        # The singular `post` group is hidden too.
        assert main.commands["post"].hidden is True

    def test_noun_groups_present_and_visible(self):
        from poindexter.cli.app import main

        for grp in ("gates", "schedule", "posts"):
            assert grp in main.commands
            assert main.commands[grp].hidden is False

    def test_top_level_help_omits_flat_publish_verbs(self):
        from poindexter.cli.app import main

        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0
        # Unambiguous flat names must not appear in the visible command listing.
        for flat in ("approve-publish", "show-pending-publish", "publish-at"):
            assert flat not in result.output
        # The noun-groups that replaced them are listed.
        for grp in ("gates", "schedule", "posts"):
            assert grp in result.output
