"""Modules register their own CLI groups via register_cli iteration
(Module v1 Phase 5) — so a stripped private module's CLI travels with
its package and needs no line in the shared CLI bootstrap."""

from __future__ import annotations

import importlib.util

import click
import pytest

from modules.content.content_module import ContentModule

# The sync filter strips ``src/cofounder_agent/modules/finance/`` from the
# public ``poindexter`` mirror, so finance-specific tests must skip there
# rather than fail at import/collection. ``find_spec`` answers "is it
# importable?" without importing (and executing) the module — matching the
# guard already used in ``tests/unit/utils/test_route_registration.py``.
_FINANCE_MODULE_PRESENT = importlib.util.find_spec("modules.finance") is not None
_finance_only = pytest.mark.skipif(
    not _FINANCE_MODULE_PRESENT,
    reason="modules.finance is private (operator overlay) — stripped from public mirror",
)


def _root_group() -> click.Group:
    @click.group()
    def root() -> None:
        pass

    return root


@_finance_only
def test_finance_module_registers_its_cli_group():
    from modules.finance.finance_module import FinanceModule

    root = _root_group()
    FinanceModule().register_cli(root)
    assert "finance" in root.commands


def test_content_module_registers_no_cli_group():
    root = _root_group()
    ContentModule().register_cli(root)
    assert root.commands == {}


@_finance_only
def test_register_cli_noops_on_none():
    """The worker lifespan passes None (no CLI host in the worker
    process) — register_cli must no-op, not raise. Uses FinanceModule
    because it actually registers a group, so this exercises the real
    "module has a group but no host" path."""
    from modules.finance.finance_module import FinanceModule

    FinanceModule().register_cli(None)  # must not raise
