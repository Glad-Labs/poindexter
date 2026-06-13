"""Regression tests for ``poindexter pipeline resume`` on Windows.

``pipeline resume`` re-invokes ``TemplateRunner.run(..., resume=True)`` which
depends on LangGraph's ``AsyncPostgresSaver`` (psycopg3) to load the durable
checkpoint the worker wrote when the graph paused at a gate. psycopg3's async
mode cannot run on Windows' default ``ProactorEventLoop`` — it raises
``InterfaceError``, which ``TemplateRunner._resolve_checkpointer`` catches and
silently degrades to ``MemorySaver``. A fresh ``MemorySaver`` holds no
checkpoint, so LangGraph re-runs the graph from its entry node with the CLI's
thin initial state (no ``post_id``) and ``seo_refresh`` halts at
``content.load_existing_post`` with a ``RuntimeError``.

The fix forces a ``SelectorEventLoop`` on Windows before ``asyncio.run``
(mirroring ``scripts/smoke_371_postgres_checkpointer.py``). These tests pin the
switch — and its no-op behaviour everywhere else — so a refactor can't
re-break the resume path on Matt's host.
"""

from __future__ import annotations

import asyncio

import pytest

from poindexter.cli import pipeline as pl


@pytest.mark.unit
class TestSelectorEventLoopSwitch:
    def test_switches_policy_on_windows(self, monkeypatch):
        # asyncio.WindowsSelectorEventLoopPolicy does not exist off-Windows,
        # so inject a stand-in the helper can instantiate, and capture the
        # value handed to set_event_loop_policy.
        class _FakeSelectorPolicy:
            pass

        captured: list[object] = []
        monkeypatch.setattr(pl.sys, "platform", "win32")
        monkeypatch.setattr(
            asyncio, "WindowsSelectorEventLoopPolicy", _FakeSelectorPolicy, raising=False
        )
        monkeypatch.setattr(
            asyncio, "set_event_loop_policy", lambda p: captured.append(p)
        )

        pl._ensure_selector_event_loop_on_windows()

        assert len(captured) == 1
        assert isinstance(captured[0], _FakeSelectorPolicy)

    def test_noop_off_windows(self, monkeypatch):
        # On Linux/macOS the ProactorEventLoop problem doesn't exist — the
        # helper must not touch the global event-loop policy (doing so could
        # break a sibling command that relies on the default loop) and must
        # not reference the Windows-only policy symbol.
        called: list[object] = []
        monkeypatch.setattr(pl.sys, "platform", "linux")
        monkeypatch.setattr(
            asyncio, "set_event_loop_policy", lambda p: called.append(p)
        )

        pl._ensure_selector_event_loop_on_windows()

        assert called == []


@pytest.mark.unit
class TestRunAppliesSwitchBeforeRunning:
    def test_run_ensures_loop_before_asyncio_run(self, monkeypatch):
        # _run must apply the loop switch BEFORE asyncio.run creates the loop,
        # otherwise the switch can't take effect for that invocation.
        order: list[str] = []

        async def _coro():
            return "result"

        monkeypatch.setattr(
            pl, "_ensure_selector_event_loop_on_windows", lambda: order.append("ensure")
        )

        def _fake_run(coro):
            order.append("run")
            coro.close()  # avoid "coroutine was never awaited" warning
            return "result"

        monkeypatch.setattr(pl.asyncio, "run", _fake_run)

        result = pl._run(_coro())

        assert result == "result"
        assert order == ["ensure", "run"]
