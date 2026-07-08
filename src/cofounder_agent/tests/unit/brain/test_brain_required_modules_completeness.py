"""Brain boot-audit registration completeness guard.

Captured 2026-07-08: ``clock_skew_probe.py`` (#2212) shipped wired via a
top-level try/except ImportError block (setting ``_HAS_CLOCK_SKEW_PROBE``)
but was never added to ``_BRAIN_REQUIRED_MODULES``. Packaging completeness
(is the file physically copied into the Docker image) is guarded separately
by ``test_brain_dockerfile_packaging.py`` — this test guards a different
failure mode: even with the file correctly present, ANY import failure
(a broken transitive dependency, a syntax error, anything short of the file
being missing) would never trip ``_audit_brain_module_imports()`` at boot,
because that function only inspects flags listed in
``_BRAIN_REQUIRED_MODULES``. A flag wired via try/except but never
registered there fails silently forever. Guards the underlying class of
bug: someone adds a new try/except-guarded probe and forgets to register
it for the boot-time audit (and the reverse: a stale registration for a
flag that no longer exists).
"""

from __future__ import annotations

import ast
from pathlib import Path

# Walk up from this test file to the repo root (the dir containing brain/).
_HERE = Path(__file__).resolve()
for _p in _HERE.parents:
    if (_p / "brain" / "brain_daemon.py").is_file():
        REPO_ROOT = _p
        break
else:  # pragma: no cover
    raise AssertionError("could not locate repo root containing brain/brain_daemon.py")

_BRAIN_DAEMON_PATH = REPO_ROOT / "brain" / "brain_daemon.py"


def _has_flags_within(node: ast.AST) -> set[str]:
    """``_HAS_<NAME>`` names assigned anywhere within this AST subtree."""
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign):
            for target in sub.targets:
                if isinstance(target, ast.Name) and target.id.startswith("_HAS_"):
                    found.add(target.id)
    return found


def _try_guarded_has_flags() -> set[str]:
    """``_HAS_<NAME>`` flags set inside a top-level try/except block that
    guards a ``from <module> import ...`` / ``from brain.<module> import
    ...`` statement.

    Deliberately excludes top-level try/except blocks that use the same
    flat-vs-package-qualified shape but never assign any ``_HAS_*`` flag
    (``secret_reader``, ``alert_sync``, ``psu_power``) — those always
    resolve one way or the other and aren't optional probes with a
    boot-audit entry.
    """
    tree = ast.parse(_BRAIN_DAEMON_PATH.read_text(encoding="utf-8"))
    flags: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Try):
            continue
        if not any(isinstance(n, ast.ImportFrom) for n in ast.walk(node)):
            continue
        flags |= _has_flags_within(node)
    return flags


def _registered_required_module_flags() -> set[str]:
    """Flag names currently registered in ``_BRAIN_REQUIRED_MODULES``."""
    tree = ast.parse(_BRAIN_DAEMON_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target: ast.Name | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target = node.targets[0]
        if target is None or target.id != "_BRAIN_REQUIRED_MODULES":
            continue
        assert isinstance(node.value, (ast.Tuple, ast.List)), (
            "_BRAIN_REQUIRED_MODULES is not a tuple/list literal — did its shape change?"
        )
        flags = {
            elt.elts[0].value
            for elt in node.value.elts
            if isinstance(elt, (ast.Tuple, ast.List))
            and elt.elts
            and isinstance(elt.elts[0], ast.Constant)
            and isinstance(elt.elts[0].value, str)
        }
        assert flags, "_BRAIN_REQUIRED_MODULES found but yielded no flag names — did its shape change?"
        return flags
    raise AssertionError("_BRAIN_REQUIRED_MODULES assignment not found — did its shape change?")


def test_every_try_except_guarded_probe_flag_is_registered_for_boot_audit():
    """A flag wired via try/except ImportError but missing from
    _BRAIN_REQUIRED_MODULES means a real import failure in that module
    will never page the operator — it only surfaces later as a quieter
    downstream symptom (missing rows, a dark dashboard panel, etc.)."""
    guarded = _try_guarded_has_flags()
    registered = _registered_required_module_flags()
    missing = sorted(guarded - registered)
    assert not missing, (
        f"{missing} are wired via try/except ImportError in brain_daemon.py "
        f"but never registered in _BRAIN_REQUIRED_MODULES — if the module "
        f"fails to import for any reason, _audit_brain_module_imports() will "
        f"never notice and the operator will not be paged. Add an entry for "
        f"each to _BRAIN_REQUIRED_MODULES."
    )


def test_every_registered_required_module_flag_still_exists_as_a_guarded_probe():
    """The reverse direction: _audit_brain_module_imports() reads each flag
    via globals().get(flag_name), which returns None (not False) for a
    name that no longer exists — so a stale entry for a renamed/removed
    flag silently never triggers the packaging-regression alert either."""
    guarded = _try_guarded_has_flags()
    registered = _registered_required_module_flags()
    stale = sorted(registered - guarded)
    assert not stale, (
        f"{stale} are registered in _BRAIN_REQUIRED_MODULES but no longer "
        f"correspond to any try/except-guarded _HAS_* flag in brain_daemon.py "
        f"(renamed or removed?). Remove or fix the stale entry."
    )
