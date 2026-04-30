"""poindexter CLI root command group."""

from __future__ import annotations

import logging

import click

from .costs import costs_group
from .memory import memory_group
from .posts import posts_group
from .premium import premium_group
from .settings import settings_group
from .setup import setup_command
from .sprint import sprint_group
from .tasks import tasks_group
from .topics import topics_group
from .vercel import vercel_group

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
main.add_command(topics_group, name="topics")


if __name__ == "__main__":
    main()
