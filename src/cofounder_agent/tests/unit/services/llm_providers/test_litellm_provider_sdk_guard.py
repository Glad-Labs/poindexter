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

from services.llm_providers.litellm_provider import (
    LangfuseConfigError as _ORIGINAL_LANGFUSE_ERROR,
)
from services.llm_providers.litellm_provider import (
    LiteLLMProvider as _ORIGINAL_PROVIDER,
)

_PROVIDER_MOD = "services.llm_providers.litellm_provider"


def _reload_provider():
    import services.llm_providers.litellm_provider as mod

    return importlib.reload(mod)


@pytest.fixture(autouse=True)
def _restore_provider_module():
    """Undo ``importlib.reload``'s damage to the module namespace.

    ``importlib.reload`` re-executes the module in its EXISTING ``__dict__``,
    so every class it defines is replaced by a brand-new object with the same
    name. ``LangfuseConfigError`` after a reload is not the ``LangfuseConfigError``
    anything imported before it — ``isinstance`` between them is False.

    That silently broke a different file. ``test_litellm_langfuse_callback.py``
    binds the exception at ITS import time (collection, before any test runs);
    once these tests reloaded the module, the code under test raised the NEW
    class while that file's ``pytest.raises(OLD)`` could not catch it, so four
    tests failed — but only when this file ran first, and only in a full-suite
    run. In isolation they passed, which is what made it look like flakiness
    rather than a hard ordering bug. It cost the ops `test-health` session three
    days of silent failures before anyone chased it.

    Re-reloading at the end (which the tests below already did) does NOT fix
    this: it produces a third set of class objects, equal to neither. The only
    restoration that works is putting the ORIGINAL objects back, so snapshot
    the namespace and hard-restore it.
    """
    import services.llm_providers.litellm_provider as mod

    snapshot = dict(mod.__dict__)
    try:
        yield
    finally:
        mod.__dict__.clear()
        mod.__dict__.update(snapshot)


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


class TestReloadLeavesNoCrossFileDamage:
    """The reloads above must not leak new class objects to anyone else.

    Regression guard for the ordering bug described on
    ``_restore_provider_module``. The guarantee is *between* tests, not within
    one — a test that reloads legitimately sees new classes while it runs; what
    must never happen is the NEXT test (or the next FILE) inheriting them.

    So these assert against ``_ORIGINAL_*``, captured at module import time
    (collection, before any test in any file has run). Passing means the module
    namespace was handed to this test in its pristine state, which is precisely
    what ``test_litellm_langfuse_callback.py`` needs and did not get.
    """

    def test_exception_class_identity_is_pristine_at_test_start(self):
        import services.llm_providers.litellm_provider as mod

        assert mod.LangfuseConfigError is _ORIGINAL_LANGFUSE_ERROR, (
            "LangfuseConfigError is not the object it was at import time — a "
            "previous test's importlib.reload leaked. Any file that bound this "
            "class at collection can no longer catch it (isinstance is False "
            "across the two), which is the order-dependent failure that broke "
            "test_litellm_langfuse_callback.py."
        )
        assert isinstance(_ORIGINAL_LANGFUSE_ERROR("x"), mod.LangfuseConfigError)

    def test_provider_class_identity_is_pristine_at_test_start(self):
        # Same hazard, different symbol — LiteLLMProvider is imported by name in
        # several test files and by the plugin registry.
        import services.llm_providers.litellm_provider as mod

        assert mod.LiteLLMProvider is _ORIGINAL_PROVIDER

    def test_a_reload_without_the_fixture_would_break_identity(self):
        """Proves the fixture is doing real work rather than passing vacuously.

        If reload were harmless, the guards above would be theatre. It is not:
        the reloaded class fails isinstance against the original.
        """
        importlib.reload(importlib.import_module(_PROVIDER_MOD))
        import services.llm_providers.litellm_provider as mod

        assert mod.LangfuseConfigError is not _ORIGINAL_LANGFUSE_ERROR
        assert not isinstance(mod.LangfuseConfigError("x"), _ORIGINAL_LANGFUSE_ERROR)
        # …and the autouse fixture puts it back for whoever runs next.
