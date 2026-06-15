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

# ── Quiet a noisy third-party warning before any CLI submodule loads ─────────
# langgraph's Postgres checkpointer builds a module-level
# ``langchain_core.load.Reviver()`` at import time
# (``langgraph/checkpoint/serde/jsonplus.py``), and langchain-core >=1.3.3 emits
# a ``LangChainPendingDeprecationWarning`` when ``allowed_objects`` is left at
# its default. The call site is inside the dependency, so we cannot pass the
# argument — and a fresh process resets the once-per-process dedup, so every
# ``poindexter <cmd>`` would otherwise print it. Worker/server logs are
# untouched: they never import ``poindexter.cli``.
#
# Ordering is load-bearing. ``warnings.filters`` is LIFO (last writer wins), and
# langchain-core's ``surface_langchain_deprecation_warnings()`` prepends its own
# ``"default"`` filter for this category when langchain-core is first imported.
# So we import the category first (forcing that surface call), then register our
# ``ignore`` afterwards so it lands ahead of langchain-core's in the stack.
# Scoped to the exact message so other (actionable) langchain deprecations still
# surface.
import warnings

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects`",
    category=LangChainPendingDeprecationWarning,
)

from .app import main

__all__ = ["main"]
