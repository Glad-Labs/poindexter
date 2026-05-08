"""poindexter CLI root command group."""

from __future__ import annotations

import logging

import click

from .auth import auth_group
from .auto_publish import auto_publish_group
from .costs import costs_group
from .dev_diary import dev_diary_group
from .experiments import experiments_group
from .memory import memory_group
from .posts import post_group, posts_group
from .premium import premium_group
from .settings import settings_group
from .setup import setup_command
from .tasks import tasks_group
from .topics import topics_group
from .validators import validators_group
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
main.add_command(validators_group, name="validators")
main.add_command(auto_publish_group, name="auto-publish")
main.add_command(dev_diary_group, name="dev-diary")


if __name__ == "__main__":
    main()
