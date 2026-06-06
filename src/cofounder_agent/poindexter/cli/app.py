"""poindexter CLI root command group."""

from __future__ import annotations

import logging

import click

from .approval import (
    approve_command,
    gates_group,
    list_pending_command,
    reject_command,
    show_pending_command,
)
from .auth import auth_group
from .auto_publish import auto_publish_group
from .costs import costs_group
from .dev_diary import dev_diary_group
from .doctor import doctor_group
from .experiments import experiments_group
from .integrations import integrations_group
from .media import media_group
from .memory import memory_group
from .migrate import migrate_group
from .posts import post_group, posts_group
from .premium import premium_group
from .publish_approval import (
    approve_publish_command,
    list_pending_publish_command,
    reject_publish_command,
    show_pending_publish_command,
)
from .publishers import publishers_group
from .qa_gates import qa_gates_group
from .retention import retention_group
from .schedule import publish_at_command, schedule_group
from .settings import settings_group
from .setup import setup_command
from .stores import stores_group
from .taps import taps_group
from .tasks import tasks_group
from .topics import topics_group
from .validators import validators_group
from .vercel import vercel_group
from .webhooks import webhooks_group

# Quiet down the client's own info-level logs unless the user asks for -v.
logging.basicConfig(level=logging.WARNING, format="%(message)s")


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "poindexter — unified CLI for the public Poindexter pipeline.\n\n"
        "Wraps the same libraries the worker, MCP servers, and OpenClaw use, "
        "so every tool sees the same state. See `poindexter memory --help` "
        "to start."
    ),
)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging.")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    if verbose:
        logging.getLogger().setLevel(logging.INFO)


main.add_command(setup_command, name="setup")
main.add_command(auth_group, name="auth")
main.add_command(memory_group, name="memory")
main.add_command(tasks_group, name="tasks")
main.add_command(posts_group, name="posts")
main.add_command(post_group, name="post")
main.add_command(settings_group, name="settings")
main.add_command(costs_group, name="costs")
main.add_command(vercel_group, name="vercel")
main.add_command(premium_group, name="premium")
main.add_command(topics_group, name="topics")
main.add_command(experiments_group, name="experiments")
main.add_command(integrations_group, name="integrations")
main.add_command(validators_group, name="validators")
main.add_command(auto_publish_group, name="auto-publish")
main.add_command(dev_diary_group, name="dev-diary")
main.add_command(publishers_group, name="publishers")
main.add_command(media_group, name="media")
main.add_command(doctor_group, name="doctor")

# Declarative-data-plane operator surfaces. Each module manages one of
# the table+handler pairs (taps, retention, webhooks, qa_gates, etc.).
# These were on disk under poindexter/cli/ but had never been registered
# here — wired up 2026-05-09 alongside the OSS migration plan's Lane B.
main.add_command(taps_group, name="taps")
main.add_command(retention_group, name="retention")
main.add_command(webhooks_group, name="webhooks")
main.add_command(qa_gates_group, name="qa-gates")
main.add_command(stores_group, name="stores")
main.add_command(schedule_group, name="schedule")
# Single-post convenience shortcut: `poindexter publish-at <id> <when>`.
# Lives at the top level so operators don't have to remember a
# subcommand for the most common "schedule this one post for that time"
# operation; the group commands cover the bulk + management surface.
main.add_command(publish_at_command, name="publish-at")
main.add_command(migrate_group, name="migrate")

# HITL approval-gate operator commands (mid-pipeline approve/reject).
main.add_command(approve_command)
main.add_command(reject_command)
main.add_command(list_pending_command)
main.add_command(show_pending_command)
main.add_command(gates_group)

# Publish-time approval-gate operator commands (post-scheduling).
main.add_command(approve_publish_command)
main.add_command(reject_publish_command)
main.add_command(list_pending_publish_command)
main.add_command(show_pending_publish_command)

# Module-contributed CLI groups (Module v1 Phase 5). Each registered module
# mounts its own subcommands via register_cli, so a private module's CLI
# travels with its package — there is no module-specific line to strip from
# this shared bootstrap on the public mirror.
from plugins.registry import get_modules  # noqa: E402 — after the static groups

for _module in get_modules():
    _module.register_cli(main)


if __name__ == "__main__":
    main()
