"""poindexter CLI root command group."""

from __future__ import annotations

import logging
import os

import click

from .approval import APPROVAL_FLAT_ALIASES, gates_group
from .auth import auth_group
from .auto_publish import auto_publish_group
from .backup import backup_group
from .costs import costs_group
from .dev_diary import dev_diary_group
from .doctor import doctor_group
from .experiments import experiments_group
from .integrations import integrations_group
from .media import media_group
from .memory import memory_group
from .migrate import migrate_group
from .model_eval import model_eval_group
from .pipeline import pipeline_group
from .posts import post_group, posts_group
from .premium import premium_group
from .publish_approval import PUBLISH_FLAT_ALIASES
from .publishers import publishers_group
from .social import social_group
from .qa_gates import qa_gates_group
from .retention import retention_group
from .schedule import SCHEDULE_FLAT_ALIASES, schedule_group
from .settings import settings_group
from .setup import setup_command
from .skills import skills_group
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
    # Stamp LOG_LEVEL into the environment NOW, before any lazy `from
    # services.*` import runs inside a subcommand handler.  services/
    # logger_config.py reads LOG_LEVEL at *module init time* (line ~48)
    # and calls root.handlers.clear() + root.setLevel(), which would
    # override the basicConfig(WARNING) below.  Setting the env var here
    # ensures logger_config initialises at the right level whenever it
    # first gets imported.  Respects any LOG_LEVEL already in the shell.
    if "LOG_LEVEL" not in os.environ:
        os.environ["LOG_LEVEL"] = "INFO" if verbose else "WARNING"
    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    # Load POINDEXTER_SECRET_KEY from bootstrap.toml into the env once, here at
    # the root, so EVERY subcommand can encrypt/decrypt secrets (auth migrate-*,
    # webhooks/stores/publishers set-secret, settings set --secret / get
    # --reveal, pipeline resume's R2 + ISR reads, …). The worker/brain containers
    # inherit the key in their env; a bare `poindexter <cmd>` shell does not, so
    # without this every host-side secret op raised SecretsError. Best-effort,
    # cheap, idempotent — commands that touch no secrets are unaffected, and the
    # per-command ensure_secret_key() calls stay as defense in depth.
    from ._bootstrap import ensure_secret_key

    ensure_secret_key()


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
main.add_command(skills_group, name="skills")
main.add_command(topics_group, name="topics")
main.add_command(experiments_group, name="experiments")
main.add_command(model_eval_group, name="model-eval")
main.add_command(integrations_group, name="integrations")
main.add_command(validators_group, name="validators")
main.add_command(auto_publish_group, name="auto-publish")
main.add_command(dev_diary_group, name="dev-diary")
main.add_command(publishers_group, name="publishers")
main.add_command(social_group, name="social")
main.add_command(media_group, name="media")
main.add_command(doctor_group, name="doctor")
# Tier 2 off-machine backup operator surface (#386): setup wizard + status /
# run / verify / snapshots over a restic S3-compatible repo.
main.add_command(backup_group, name="backup")

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
main.add_command(migrate_group, name="migrate")

# HITL approval-gate operator commands (mid-pipeline). The verbs live under
# `poindexter gates {approve,reject,pending,show}`; gate toggles under
# `gates {list,set}` (#1652). Single-post + publish-gate verbs live under
# `schedule` (see the alias loop below).
main.add_command(gates_group)

# Interrupt()-paused pipeline operator commands (list-paused / status /
# resume) — true LangGraph checkpoint resume (Glad-Labs/poindexter#363).
main.add_command(pipeline_group, name="pipeline")

# Backcompat (#1652, sibling of epic #1340): the 9 former flat verbs
# (approve / reject / list-pending / show-pending, their -publish siblings,
# and publish-at) now live under the `gates` and `schedule` noun-groups. Keep
# the old flat names callable as hidden, deprecated aliases so existing
# operator scripts don't break — each prints a one-line deprecation notice to
# stderr and delegates to its grouped command.
for _flat_alias in (*APPROVAL_FLAT_ALIASES, *PUBLISH_FLAT_ALIASES, *SCHEDULE_FLAT_ALIASES):
    main.add_command(_flat_alias)

# Module-contributed CLI groups (Module v1 Phase 5). Each registered module
# mounts its own subcommands via register_cli, so a private module's CLI
# travels with its package — there is no module-specific line to strip from
# this shared bootstrap on the public mirror.
from plugins.registry import get_modules  # noqa: E402 — after the static groups

for _module in get_modules():
    _module.register_cli(main)


if __name__ == "__main__":
    main()
