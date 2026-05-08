"""poindexter.cli — unified command-line interface for the public pipeline.

Scaffold for Gitea #191 (CLI unification) and #192 slice 6 (memory CLI
subcommands). Built on Click — zero new dependencies, already in the worker
image.

Entry points:
    python -m poindexter memory search "why gemma3"
    python -m poindexter memory status
    python -m poindexter memory store --writer claude-code --text "..."

The CLI imports `poindexter.memory.MemoryClient` directly — same library as
the worker, the MCP servers, and OpenClaw. One schema, one client, zero drift.

Future subcommand groups (tracked in #191):
    poindexter tasks   — task queue management
    poindexter posts   — published post queries + publish/archive
    poindexter costs   — budget + operational metrics
    poindexter vercel  — Vercel deployments via REST API
    poindexter settings — app_settings get/set
"""

from .app import main

__all__ = ["main"]
