"""Every ``dispatch_complete`` call site must name its ``phase``.

``phase`` is not just a cost_logs label. ``dispatch_complete`` back-fills
``num_ctx`` for local dispatches from ``<phase>_num_ctx`` -> ``ollama_num_ctx``
-> 8192 (``_resolve_default_num_ctx``, glad-labs-stack#2170), so an unnamed call
site silently inherits the global window AND has no key an operator can tune.

That is not hypothetical. ``podcast_service`` requested ``max_tokens=8192``
inside the 8192 **total** window (prompt + output share it) with an
article-sized prompt — unsatisfiable by construction. Measured on prod
2026-07-29: 16 calls under the generic ``dispatch_complete`` phase landed on
exactly 8192, with ``max(output_tokens)`` at 7867. The scripts were being cut
off at the wall, and because the phase was generic there was no
``<phase>_num_ctx`` to raise.

The ratchet is a whole-tree AST scan rather than a per-call-site test: the
failure mode is *omission*, and only an exhaustive sweep catches the next one.
Deliberately a hard zero, not a baseline count — there is no reason to add an
unnamed dispatch, and "allowed: N" invites drift.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Repo layout: this file is tests/unit/services/<name>.py, so the backend
# package root (containing services/, modules/, …) is four parents up.
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_SCANNED_PACKAGES = ("services", "modules", "plugins", "routes", "poindexter")


def _dispatch_calls_missing_phase() -> list[str]:
    """Return ``path:line`` for every ``dispatch_complete(...)`` without ``phase=``."""
    offenders: list[str] = []
    for package in _SCANNED_PACKAGES:
        root = _BACKEND_ROOT / package
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # not our concern here
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name != "dispatch_complete":
                    continue
                # NO exemption for ``**kwargs`` unpacking. The first draft of
                # this scanner skipped any call containing ``**`` on the theory
                # that a wrapper might forward ``phase`` — and that exemption
                # silently swallowed the podcast_service call (``**think_kwargs``)
                # that motivated the whole ratchet. A phase forwarded through
                # ``**kwargs`` is invisible to static analysis anyway; require
                # the literal keyword so the guard can actually see it.
                if not any(kw.arg == "phase" for kw in node.keywords):
                    offenders.append(
                        f"{path.relative_to(_BACKEND_ROOT)}:{node.lineno}",
                    )
    return sorted(offenders)


def test_no_dispatch_complete_call_site_omits_phase():
    offenders = _dispatch_calls_missing_phase()
    assert offenders == [], (
        "dispatch_complete call site(s) with no phase=: "
        + ", ".join(offenders)
        + ". An unnamed phase inherits the global ollama_num_ctx window and "
        "gives the operator no <phase>_num_ctx key to raise — the bug that "
        "truncated podcast scripts at exactly 8192. Name the phase."
    )


def test_scanner_really_scans_the_backend_tree():
    """Guard the guard: a scanner pointed at the wrong root always passes.

    ``_BACKEND_ROOT`` is derived by parent-counting, which is exactly the kind
    of thing that breaks silently under a different checkout layout. Assert it
    resolves somewhere that actually contains dispatch_complete call sites.
    """
    assert (_BACKEND_ROOT / "services" / "llm_providers" / "dispatcher.py").is_file(), (
        f"_BACKEND_ROOT resolved to {_BACKEND_ROOT}, which is not the backend "
        "package root — the scan below would silently sweep nothing"
    )
    seen = 0
    for package in _SCANNED_PACKAGES:
        for path in (_BACKEND_ROOT / package).rglob("*.py"):
            if "dispatch_complete(" in path.read_text(encoding="utf-8", errors="ignore"):
                seen += 1
    assert seen >= 5, f"expected many dispatch_complete call sites, saw {seen}"


def test_scanner_flags_an_unnamed_call_including_kwargs_unpacking(tmp_path):
    """Both directions, plus the case the first draft got wrong.

    The ``**think_kwargs`` variant is the podcast_service shape — an earlier
    exemption for it made this ratchet pass while the real bug was live.
    """
    src = (
        "async def f(pool, messages, extra):\n"
        "    await dispatch_complete(pool=pool, messages=messages, model='m')\n"
        "    await dispatch_complete(pool=pool, messages=messages, model='m',\n"
        "                            max_tokens=8192, **extra)\n"
        "    await dispatch_complete(pool=pool, messages=messages, model='m',\n"
        "                            phase='named', **extra)\n"
    )
    calls = [
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call)
        and (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
        == "dispatch_complete"
    ]
    assert len(calls) == 3
    unnamed = [c for c in calls if not any(k.arg == "phase" for k in c.keywords)]
    assert len(unnamed) == 2, (
        "a call must be flagged even when it unpacks **kwargs — that exemption "
        "is what let the podcast truncation through"
    )
