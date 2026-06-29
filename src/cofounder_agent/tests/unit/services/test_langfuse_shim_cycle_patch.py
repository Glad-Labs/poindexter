"""Pin the ``_install_cycle_safe_serializer_patch`` contract.

Originally a workaround for an upstream langfuse bug — ``EventSerializer.default``
cycle-detected only on the ``__dict__`` path, so cyclic graphs reached via the
dict / list / ``__slots__`` branches recursed forever and hung the asyncio loop
(the 6-day, hourly-worker-hang incident of 2026-05-15).

Upstream fixed it in langfuse/langfuse-python#1577 (first released in v4.7.0 —
depth limit + cycle detection). We run v4.12.0, which contains the fix, but
RETAIN the monkey-patch as idempotent defense-in-depth (the call for the
tracing re-enable PR; safe to retire in a fast-follow once soaked). These tests
pin the contract so a future langfuse upgrade that moves the serializer or
breaks our patch fails LOUD instead of silently re-introducing the hang.

See [[reference-langfuse-serializer-recursion-bug]] for the original incident.
"""

from __future__ import annotations

import sys

import pytest

# Skip the file ONLY when langfuse is genuinely absent (brain daemon /
# minimal-dep CI containers don't always have it; the production worker
# always does). When langfuse IS installed, every import below is a HARD
# import on purpose: a moved serializer path or a missing patch helper means
# the cycle-safe monkey-patch is silently no-opping, and THAT must fail LOUD
# (a collection error) rather than skip — surfacing the regression is the
# entire reason this file exists.
langfuse = pytest.importorskip("langfuse")

try:
    from langfuse._utils.serializer import EventSerializer  # noqa: E402
except ImportError as exc:  # pragma: no cover - only hit if upstream moves the path
    raise RuntimeError(
        "langfuse is installed but langfuse._utils.serializer.EventSerializer no "
        "longer resolves, so services.langfuse_shim._install_cycle_safe_serializer_patch "
        "is silently no-opping. Point the patch at the new path (or retire it if "
        "upstream's fix in v4.7.0+ is deemed sufficient) and update this test."
    ) from exc

# Must exist — the patch shipped in PR #1626. A missing symbol is a real
# regression, not a reason to skip.
from services.langfuse_shim import _install_cycle_safe_serializer_patch  # noqa: E402


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

    Pre-patch (Langfuse 3.x and ≤4.6.1): infinite recursion → RecursionError
    swallowed inside a finalizer → MainThread stuck in the serializer
    for minutes → asyncio event loop blocks → worker hang. (v4.7.0+ added a
    MAX_DEPTH cap upstream, so without our patch it bloat-truncates at depth
    20 rather than hanging — our patch still breaks the cycle cleanly.)"""
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


def test_mixed_branch_cycle_through_object_list_dict_emits_our_marker():
    """Regression for the 2026-05-15 incident shape — a cycle that threads
    through an object (``__dict__``) -> list -> dict -> back to the list,
    i.e. across exactly the branches the upstream bug left unguarded.

    The closing reference is a *list*, so OUR patch is what breaks the cycle
    (emitting ``<cycle:list>``). This is deliberately distinct from:
      * upstream's ``__dict__``-only ``seen`` set (would emit the type name
        only if the cycle closed on the object), and
      * upstream's ``_MAX_DEPTH`` truncation (would emit a ``<Type>``
        placeholder, never the word "cycle", and only after 20 levels).

    Asserting the ``<cycle:list>`` marker therefore pins OUR contract
    specifically, keeping the test meaningful as defense-in-depth even on a
    langfuse (>= v4.7.0) that already carries the upstream fix.
    """
    class _Holder:
        payload: list

    holder = _Holder()
    inner_list: list = [{"k": "v"}]
    inner_list[0]["loop"] = inner_list  # dict value points back to its list
    holder.payload = inner_list  # object(__dict__) -> list -> dict -> list

    out = _serialize(holder)

    assert isinstance(out, str)
    assert "<cycle:list>" in out  # OUR patch fired on the list-branch cycle


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
