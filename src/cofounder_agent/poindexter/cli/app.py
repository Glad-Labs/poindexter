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
from .publish_approval import (
    approve_publish_command,
    list_pending_publish_command,
    reject_publish_command,
    show_pending_publish_command,
)
from .costs import costs_group
from .memory import memory_group
from .migrate import migrate_group
from .posts import posts_group
from .premium import premium_group
from .qa_gates import qa_gates_group
from .schedule import publish_at_command, schedule_group
from .settings import settings_group
from .setup import setup_command
from .sprint import sprint_group
from .stores import stores_group
from .tasks import tasks_group
from .topics import topics_group
from .vercel import vercel_group
from .retention import retention_group
from .taps import taps_group
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
main.add_command(memory_group, name="memory")
main.add_command(tasks_group, name="tasks")
main.add_command(posts_group, name="posts")
main.add_command(settings_group, name="settings")
main.add_command(costs_group, name="costs")
main.add_command(sprint_group, name="sprint")
main.add_command(vercel_group, name="vercel")
main.add_command(premium_group, name="premium")
main.add_command(webhooks_group, name="webhooks")
main.add_command(retention_group, name="retention")
main.add_command(taps_group, name="taps")
main.add_command(qa_gates_group, name="qa-gates")
main.add_command(stores_group, name="stores")
main.add_command(schedule_group, name="schedule")
main.add_command(publish_at_command, name="publish-at")
main.add_command(migrate_group, name="migrate")

# HITL approval gate commands (#145).
main.add_command(approve_command, name="approve")
main.add_command(reject_command, name="reject")
main.add_command(list_pending_command, name="list-pending")
main.add_command(show_pending_command, name="show-pending")
main.add_command(gates_group, name="gates")

# Final-publish-approval gate commands (Matt's 2026-04-27 ask) — operates
# on the posts table after scheduling, not on pipeline_tasks.
main.add_command(approve_publish_command, name="approve-publish")
main.add_command(reject_publish_command, name="reject-publish")
main.add_command(list_pending_publish_command, name="list-pending-publish")
main.add_command(show_pending_publish_command, name="show-pending-publish")

# Topic-decision approval queue (#146) — scoped wrapper over the generic
# approval CLI plus a manual ``topics propose`` injection path.
main.add_command(topics_group, name="topics")


if __name__ == "__main__":
    main()
