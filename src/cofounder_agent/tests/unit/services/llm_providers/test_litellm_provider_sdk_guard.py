"""LiteLLMProvider must not register when the litellm SDK is absent.

Every ``import litellm`` in ``litellm_provider`` sits inside a method, so
without a module-level guard the module imports cleanly on an install
without litellm, registers as an available provider, gets selected by the
dispatcher (prod pins ``plugin.llm_provider.primary.*='litellm'``), and
only then explodes — as a per-document ``ModuleNotFoundError`` at call
time rather than a clean "provider unavailable → fall back".

This is a real outage shape, not a hypothetical: the auto-embed sidecar
ships a minimal image with no litellm. It was silently protected because
the module ALSO failed to import for an unrelated missing package; the
moment that was fixed, litellm registered and every embedding store in
the sidecar began failing with `No module named 'litellm'`.
"""

from __future__ import annotations

import builtins
import importlib
import importlib.util

import pytest


def _reload_provider():
    import services.llm_providers.litellm_provider as mod

    return importlib.reload(mod)


class TestSdkGuard:
    def test_imports_when_sdk_present(self):
        """The normal case — the worker image has litellm installed."""
        if importlib.util.find_spec("litellm") is None:
            pytest.skip("litellm not installed in this environment")
        mod = _reload_provider()
        assert mod.LiteLLMProvider is not None

    def test_raises_importerror_when_sdk_absent(self, monkeypatch):
        """Absent SDK must raise at import so the registry skips the provider."""
        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *a, **kw):
            if name == "litellm":
                return None
            return real_find_spec(name, *a, **kw)

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

        with pytest.raises(ImportError, match="requires the 'litellm' package"):
            _reload_provider()

        # Leave the module importable for the rest of the session.
        monkeypatch.undo()
        _reload_provider()

    def test_error_names_the_fallback(self, monkeypatch):
        """The message must tell an operator what happens instead.

        A bare "module not found" sends people hunting; naming the
        fallback makes a minimal install self-explanatory.
        """
        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *a, **kw):
            return None if name == "litellm" else real_find_spec(name, *a, **kw)

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
        with pytest.raises(ImportError) as exc:
            _reload_provider()
        assert "ollama_native" in str(exc.value)

        monkeypatch.undo()
        _reload_provider()


class TestRegistryExcludesUnavailableProvider:
    def test_registry_tolerates_a_provider_that_refuses_to_import(self):
        """The registry must skip, not crash — that is what makes the guard safe.

        Pins the contract the fix depends on: raising ImportError at module
        scope degrades to "provider absent from the registry", never to a
        failed startup.
        """
        from plugins.registry import get_llm_providers

        real_import = builtins.__import__

        def boom(name, *a, **kw):
            if "litellm_provider" in name:
                raise ImportError("simulated missing SDK")
            return real_import(name, *a, **kw)

        builtins.__import__ = boom
        try:
            names = [p.name for p in get_llm_providers()]
        finally:
            builtins.__import__ = real_import

        # Whatever else is registered, the run completed without raising and
        # the healthy local provider is still there to fall back to.
        assert "ollama_native" in names
