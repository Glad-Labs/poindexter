"""Regression: ``run_scheduled_publisher`` must be wired into the
lifespan at top scope, not under the coordinator-only ``else:`` branch.

Captured 2026-05-25: prod runs ``DEPLOYMENT_MODE=worker``, but the
``scheduled_publisher`` background task was created only in the
``else`` branch of the lifespan's deployment-mode switch — i.e. in
coordinator mode, which has never been used. The whole
``poindexter schedule batch`` + ``poindexter publish-at`` operator
surface produced ``status='scheduled'`` rows that sat in the queue
forever with no daemon to flip them.

This test pins the fix at the source-AST level: walk
``main.lifespan`` and assert the ``run_scheduled_publisher``
asyncio.create_task call is NOT a descendant of any
``if deployment_mode == "coordinator"`` (or its negative twin)
conditional.

A behavioural integration test would be more robust but FastAPI
lifespan setup pulls in the entire app graph; for a tight
regression guard the AST shape check is enough — if the wiring drifts
back into a mode-gated branch the test fails with a clear pointer.
"""

from __future__ import annotations

import ast
import inspect

import pytest


def _collect_ancestor_conditions(tree: ast.AST, target_call_name: str) -> list[str]:
    """Return the conditional expressions wrapping every call to
    ``target_call_name`` inside ``tree``. Empty list = the call is at
    top scope (the desired post-fix state)."""
    conditions: list[str] = []

    class _Walker(ast.NodeVisitor):
        def __init__(self) -> None:
            self._stack: list[str] = []

        def visit_If(self, node: ast.If) -> None:  # noqa: N802
            cond_src = ast.unparse(node.test)
            self._stack.append(cond_src)
            for child in node.body:
                self.visit(child)
            self._stack.pop()
            # The else / elif branch carries the negation
            self._stack.append(f"NOT ({cond_src})")
            for child in node.orelse:
                self.visit(child)
            self._stack.pop()

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            # Match either ``run_scheduled_publisher(...)`` directly or
            # ``asyncio.create_task(run_scheduled_publisher(...))``.
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == target_call_name
            ):
                conditions.extend(self._stack)
            self.generic_visit(node)

    _Walker().visit(tree)
    return conditions


@pytest.mark.unit
def test_scheduled_publisher_runs_in_both_deployment_modes():
    """The fix: ``run_scheduled_publisher`` is invoked at lifespan top
    scope, not inside a ``deployment_mode == "coordinator"`` branch."""
    from cofounder_agent import main

    src = inspect.getsource(main.lifespan)
    tree = ast.parse(inspect.cleandoc(src))
    ancestor_conditions = _collect_ancestor_conditions(
        tree, "run_scheduled_publisher",
    )

    # The deployment_mode == "worker" if/else MAY wrap call to
    # run_scheduled_publisher transitively (e.g. via the import + a
    # logger line), but the create_task call itself must not be
    # gated. Strip any condition that is the worker-vs-coordinator
    # discriminator — if anything else remains, the wiring is gated.
    deployment_mode_gates = {
        c for c in ancestor_conditions
        if "deployment_mode" in c and "coordinator" in c
    }
    bad_gates = set(ancestor_conditions) - deployment_mode_gates

    # Critical assertion: no deployment-mode discriminator should
    # wrap the call. If the regression came back, this set is
    # non-empty.
    assert not deployment_mode_gates, (
        "run_scheduled_publisher is gated behind deployment-mode "
        "branches — prod runs worker mode and would skip the "
        f"daemon. Found gates: {deployment_mode_gates}"
    )

    # Defense in depth — no OTHER conditional should gate it either.
    # If a future change adds a feature-flag gate the operator might
    # forget to enable, this catches it.
    assert not bad_gates, (
        f"run_scheduled_publisher is gated by other conditions: "
        f"{bad_gates}"
    )


@pytest.mark.unit
def test_scheduled_publisher_module_is_importable():
    """Sanity guard — if the module ever vanishes (e.g. rename) the
    lifespan import would crash at startup. Fail here first."""
    from services.scheduled_publisher import run_scheduled_publisher
    assert callable(run_scheduled_publisher)
