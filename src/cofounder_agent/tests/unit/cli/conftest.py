"""Shared fixtures for poindexter CLI unit tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_cli_token_cache(tmp_path, monkeypatch):
    """Redirect the CLI's cross-process token cache to a throwaway dir for
    every CLI test.

    Without this, a test that constructs ``WorkerClient`` would read or write
    the developer's real ``~/.poindexter/cli_token_cache.json``. A fresh
    cached token there would make ``__aenter__`` skip the credential read the
    reachability tests assert on, so results would depend on machine state.
    The kill-switch is cleared too, so cache behaviour is exercised by
    default; individual tests re-set it when they want it off.
    """
    monkeypatch.setenv("POINDEXTER_TOKEN_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("POINDEXTER_CLI_TOKEN_CACHE", raising=False)
    yield
