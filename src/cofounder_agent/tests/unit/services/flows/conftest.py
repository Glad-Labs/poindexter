"""Prefect harness for the ``services.flows`` test tree.

Closes the CI hang introduced by Glad-Labs/poindexter#410 (Prefect
Phase 1+2). When ``services.flows.content_generation`` is imported,
Prefect's ``@task`` / ``@flow`` decorators register with the global
runtime. With no ``PREFECT_API_URL`` set, Prefect spins up an
**ephemeral local server** (a subprocess uvicorn on a random port)
to back the registry — and that server's asyncio shutdown leaks a
"GlobalEventLoopThread" with pending items that never drain. pytest
hangs at the end of the services test group, hits the 30-minute
workflow timeout, and gets SIGTERM'd (exit 143).

Fix: bracket the whole ``flows/`` test subdir in an official
``prefect_test_harness()`` session — that swaps in an in-process
SQLite-backed test server with deterministic startup + teardown,
so the hang never materialises.

Also blocks the ephemeral-server fallback explicitly via
``PREFECT_SERVER_ALLOW_EPHEMERAL_MODE=false``: if the harness ever
fails to set up, every Prefect call inside the tests fails fast
with a clear "Failed to reach API" message instead of silently
re-summoning the leaky ephemeral server.
"""

from __future__ import annotations

import os

# Set BEFORE any prefect import. Prefect snapshots settings at import
# time; setting these later has no effect on whether the ephemeral
# server starts.
os.environ.setdefault("PREFECT_SERVER_ALLOW_EPHEMERAL_MODE", "false")

import pytest


@pytest.fixture(autouse=True, scope="module")
def _prefect_test_harness():
    """In-process SQLite Prefect server scoped to this test module.

    Module scope (not session) so the harness is torn down between
    services test files — keeps the per-file pytest-forked subprocess
    boundary clean and prevents Prefect runtime state from leaking
    into adjacent test modules.
    """
    from prefect.testing.utilities import prefect_test_harness

    with prefect_test_harness():
        yield
