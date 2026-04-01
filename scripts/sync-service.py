#!/usr/bin/env python3
"""
Glad Labs Database Sync CLI

Standalone script for syncing data between local brain DB and cloud (Railway) DB.

Usage:
    python scripts/sync-service.py push --post-id <UUID>   Push a single post
    python scripts/sync-service.py push --all               Push all published posts
    python scripts/sync-service.py pull                     Pull metrics from cloud
    python scripts/sync-service.py status                   Show sync state

Environment variables (optional overrides):
    CLOUD_DATABASE_URL  — Railway PostgreSQL connection string
    LOCAL_DATABASE_URL  — Local brain DB connection string

Both default to the Glad Labs standard values if not set.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

# ---------------------------------------------------------------------------
# Allow importing the service class from the FastAPI app tree.
# When running as `python scripts/sync-service.py` from the repo root,
# we add the cofounder_agent source directory to sys.path so the import
# `from services.sync_service import SyncService` resolves.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_AGENT_SRC = os.path.join(_REPO_ROOT, "src", "cofounder_agent")
if _AGENT_SRC not in sys.path:
    sys.path.insert(0, _AGENT_SRC)

from services.sync_service import SyncService  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sync-cli")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


async def cmd_push(args: argparse.Namespace) -> int:
    """Push posts from local DB to cloud DB."""
    async with SyncService() as sync:
        if args.all:
            stats = await sync.push_all_posts()
            print(json.dumps(stats, indent=2))
            return 0 if stats["failed"] == 0 else 1

        if args.post_id:
            ok = await sync.push_post(args.post_id)
            if ok:
                print(f"OK  Post {args.post_id} pushed to cloud")
                return 0
            else:
                print(f"FAIL  Could not push post {args.post_id}")
                return 1

        print("ERROR: specify --post-id <UUID> or --all")
        return 2


async def cmd_pull(args: argparse.Namespace) -> int:
    """Pull metrics from cloud DB to local DB."""
    async with SyncService() as sync:
        result = await sync.pull_metrics()
        print(json.dumps(result, indent=2, default=str))
        return 0


async def cmd_status(args: argparse.Namespace) -> int:
    """Show sync status across both databases."""
    async with SyncService() as sync:
        status = await sync.get_status()
        print(json.dumps(status, indent=2, default=str))
        return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Glad Labs split-DB sync tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # push
    push_parser = sub.add_parser("push", help="Push published content to cloud DB")
    push_group = push_parser.add_mutually_exclusive_group(required=True)
    push_group.add_argument(
        "--post-id",
        type=str,
        help="UUID of a single post to push",
    )
    push_group.add_argument(
        "--all",
        action="store_true",
        help="Push all published posts",
    )

    # pull
    sub.add_parser("pull", help="Pull metrics from cloud to local DB")

    # status
    sub.add_parser("status", help="Show sync state between databases")

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "push": cmd_push,
        "pull": cmd_pull,
        "status": cmd_status,
    }

    handler = dispatch.get(args.command)
    if not handler:
        parser.print_help()
        return 2

    return asyncio.run(handler(args))


if __name__ == "__main__":
    sys.exit(main())
