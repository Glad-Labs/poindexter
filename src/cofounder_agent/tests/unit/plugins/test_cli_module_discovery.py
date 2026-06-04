"""Modules register their own CLI groups via register_cli iteration
(Module v1 Phase 5) — so a stripped private module's CLI travels with
its package and needs no line in the shared CLI bootstrap."""

from __future__ import annotations

import click

from modules.content.content_module import ContentModule
from modules.finance.finance_module import FinanceModule


def _root_group() -> click.Group:
    @click.group()
    def root() -> None:
        pass

    return root


def test_finance_module_registers_its_cli_group():
    root = _root_group()
    FinanceModule().register_cli(root)
    assert "finance" in root.commands


def test_content_module_registers_no_cli_group():
    root = _root_group()
    ContentModule().register_cli(root)
    assert root.commands == {}


def test_register_cli_noops_on_none():
    """The worker lifespan passes None (no CLI host in the worker
    process) — register_cli must no-op, not raise."""
    FinanceModule().register_cli(None)  # must not raise
