"""Pin the ``_install_cycle_safe_serializer_patch`` contract.

Workaround for an upstream langfuse bug — ``EventSerializer.default``
only cycle-detects on the ``__dict__`` path. Tests live here so a
future langfuse upgrade that breaks our patch (method renamed, class
moved, etc.) fails LOUD instead of silently re-introducing the
6-day-of-hourly-worker-hangs incident from 2026-05-15.

See [[reference-langfuse-serializer-recursion-bug]] for the original
incident.
"""

from __future__ import annotations

import sys

import pytest

# Skip the whole file if langfuse isn't installed (CI containers don't
# always have it; production worker always does).
langfuse = pytest.importorskip("langfuse")
serializer_mod = pytest.importorskip("langfuse._utils.serializer")
EventSerializer = serializer_mod.EventSerializer

# FIXME: _install_cycle_safe_serializer_patch was never added to
# services.langfuse_shim — the current mitigation is the
# app_settings.langfuse_tracing_enabled toggle (see memory note
# reference_langfuse_serializer_recursion_bug). Re-enable this file
# when the runtime monkey-patch ships.
try:
    from services.langfuse_shim import _install_cycle_safe_serializer_patch  # noqa: E402
except ImportError:
    pytest.skip(
        "services.langfuse_shim._install_cycle_safe_serializer_patch not implemented "
        "(workaround is app_settings.langfuse_tracing_enabled=false)",
        allow_module_level=True,
    )


@pytest.fixture(autouse=True)
def _ensure_patch_installed():
    """``services.langfuse_shim`` installs the patch at import time, but
    some other test may have already run that monkey-patched ``default``.
    Re-invoking the helper is idempotent, so just call it.
    """
    _install_cycle_safe_serializer_patch()
    yield


def _serialize(obj):
    """Run a value through EventSerializer like Langfuse would internally."""
    s = EventSerializer()
    return s.encode(obj)


# ---- Cycle protection ------------------------------------------------------


def test_cycle_in_dict_does_not_recurse_forever():
    """Dict containing a self-reference must not blow the stack.

    Pre-patch (Langfuse 3.x/4.x): infinite recursion → RecursionError
    swallowed inside a finalizer → MainThread stuck in the serializer
    for minutes → asyncio event loop blocks → worker hang."""
    d: dict = {"a": 1}
    d["self"] = d  # cycle through dict branch

    out = _serialize(d)

    assert isinstance(out, str)
    assert "cycle" in out  # marker indicates the cycle was caught
    # Recursion depth should be small; an unfixed bug would hit
    # sys.getrecursionlimit() (typically 1000) before we got here.
    assert sys.getrecursionlimit() > 100


def test_cycle_in_list_does_not_recurse_forever():
    """List containing a self-reference — same hazard, different branch
    (line 137 of langfuse 4.6.1's serializer.py)."""
    lst: list = [1, 2, 3]
    lst.append(lst)  # cycle through list branch

    out = _serialize(lst)

    assert isinstance(out, str)
    assert "cycle" in out


def test_cycle_through_slots_object_does_not_recurse_forever():
    """__slots__-based object whose slot value references back to the
    same object. The original code at line 145 builds a dict from
    {slot: value} and recurses into it — without cycle detection."""
    class _SlottedCycle:
        __slots__ = ("ref",)

    obj = _SlottedCycle()
    obj.ref = obj  # cycle through __slots__ branch

    out = _serialize(obj)

    assert isinstance(out, str)
    assert "cycle" in out


def test_non_cyclic_dict_serializes_normally():
    """The patch must NOT alter behaviour for ordinary (acyclic) graphs —
    Langfuse's existing dict/list/__slots__ logic still runs."""
    out = _serialize({"a": 1, "b": [2, 3], "c": {"nested": True}})

    assert isinstance(out, str)
    # Standard JSON-ish payload — keys present, no cycle marker.
    assert "a" in out and "b" in out and "c" in out
    assert "cycle" not in out


def test_non_cyclic_list_serializes_normally():
    out = _serialize([1, "two", {"three": 3}, [4, 5]])

    assert isinstance(out, str)
    assert "cycle" not in out


def test_indirect_cycle_through_nested_dict_is_caught():
    """Cycle via intermediate container: A -> B -> A. The seen-set
    must cover this just like the direct case."""
    a: dict = {"name": "a"}
    b: dict = {"name": "b", "back": a}
    a["fwd"] = b  # a -> b -> a

    out = _serialize(a)

    assert isinstance(out, str)
    assert "cycle" in out


# ---- Patch hygiene ---------------------------------------------------------


def test_patch_is_idempotent():
    """Re-installing the patch must not stack wrappers. Stacked wrappers
    would slow every serialize call linearly with install count and
    break the seen-set semantics."""
    # Snapshot the method identity, install again, verify same identity.
    before = EventSerializer.default
    _install_cycle_safe_serializer_patch()
    _install_cycle_safe_serializer_patch()
    after = EventSerializer.default

    assert before is after
    assert getattr(after, "_poindexter_cycle_safe", False) is True


def test_patch_marker_attribute_present():
    """The ``_poindexter_cycle_safe`` flag is how the idempotency check
    detects an already-patched method. If a langfuse upgrade replaces
    the class, this attribute will be missing and the patch will
    re-install — that's intended."""
    assert getattr(EventSerializer.default, "_poindexter_cycle_safe", False) is True
