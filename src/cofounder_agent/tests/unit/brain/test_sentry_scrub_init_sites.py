"""Every ``sentry_sdk.init`` in the tree passes the credential scrubber's options.

A new process that initialises Sentry without them reopens the leak: the SDK's
default stdlib integration records every outbound URL with its path and query,
and ``include_local_variables`` defaults to on. GlitchTip held a Discord
webhook token, a Telegram bot token, the Postgres password and API keys that
way (2026-09-28). This scans the source for init calls and requires the four
options on each, written out, splatted from ``sentry_scrub.init_options(...)``,
or splatted from a dict the same function builds (the worker's shape).

It fails if it finds none of the three init sites the tree has today, so a move
or rename cannot turn it into a check of nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import poindexter
from poindexter.brain.sentry_scrub import init_options

pytestmark = pytest.mark.unit

REQUIRED = frozenset(
    {"before_breadcrumb", "before_send", "before_send_transaction", "include_local_variables"}
)

KNOWN_SITES = frozenset(
    {
        "src/cofounder_agent/poindexter/services/sentry_integration.py",
        "src/cofounder_agent/poindexter/brain/brain_daemon.py",
        "mcp-server/http_server.py",
    }
)

SCAN_ROOTS = ("src/cofounder_agent", "mcp-server", "scripts")
SKIP_DIRS = frozenset(
    {"tests", ".venv", "venv", "node_modules", "__pycache__", ".git", ".claude", "site-packages"}
)


def _repo_root() -> Path:
    for candidate in Path(poindexter.__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src").is_dir():
            return candidate
    raise AssertionError("repo root (pyproject.toml beside src/) not found")


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for scan_root in SCAN_ROOTS:
        base = root / scan_root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if not SKIP_DIRS.intersection(path.relative_to(root).parts):
                files.append(path)
    return files


def _is_sentry_init(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "init"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sentry_sdk"
    )


def _callee_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _dict_keys(node: ast.AST | None) -> set[str]:
    if isinstance(node, ast.Call) and _callee_name(node) == "dict":
        return {kw.arg for kw in node.keywords if kw.arg}
    if isinstance(node, ast.Dict):
        return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return set()


def _bound_dict_keys(scope: ast.AST, name: str) -> set[str]:
    """Keys of every dict literal or ``dict(...)`` assigned to ``name`` in ``scope``."""
    keys: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                keys |= _dict_keys(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                keys |= _dict_keys(node.value)
    return keys


def _options_passed(call: ast.Call, scope: ast.AST) -> set[str]:
    names = {kw.arg for kw in call.keywords if kw.arg}
    for kw in call.keywords:
        if kw.arg is not None:
            continue
        if isinstance(kw.value, ast.Call) and _callee_name(kw.value) == "init_options":
            names |= set(init_options())
        elif isinstance(kw.value, ast.Name):
            names |= _bound_dict_keys(scope, kw.value.id)
    return names


def _init_sites(root: Path) -> dict[str, list[tuple[int, set[str]]]]:
    """``{repo-relative path: [(line, options passed), ...]}`` for every init call."""
    sites: dict[str, list[tuple[int, set[str]]]] = {}
    for path in _source_files(root):
        text = path.read_text(encoding="utf-8")
        if "sentry_sdk.init" not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        scopes: list[ast.AST] = [tree] + [
            n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        seen: set[int] = set()
        # Innermost scope first, so a call is attributed to the function that holds it.
        for scope in reversed(scopes):
            for node in ast.walk(scope):
                if _is_sentry_init(node) and id(node) not in seen:
                    seen.add(id(node))
                    rel = path.relative_to(root).as_posix()
                    sites.setdefault(rel, []).append((node.lineno, _options_passed(node, scope)))
    return sites


def test_init_options_supplies_exactly_the_required_set():
    assert set(init_options()) == REQUIRED


def test_every_sentry_init_passes_the_scrubber_options():
    sites = _init_sites(_repo_root())
    missing_known = KNOWN_SITES - set(sites)
    assert not missing_known, (
        f"no sentry_sdk.init found in {sorted(missing_known)}: moved or renamed? "
        "Update KNOWN_SITES, so this check keeps scanning the real init sites."
    )
    offences = [
        f"{path}:{line} lacks {sorted(REQUIRED - passed)}"
        for path, calls in sorted(sites.items())
        for line, passed in calls
        if not REQUIRED <= passed
    ]
    assert not offences, (
        "sentry_sdk.init without the credential scrubber: pass "
        "**sentry_scrub.init_options(...) (poindexter/brain/sentry_scrub.py) or "
        "its four options.\n" + "\n".join(offences)
    )


def test_the_scan_sees_each_shape_it_accepts():
    """Positive controls: written-out keywords, an init_options splat, and a
    splatted dict (both dict() and a literal) all count; a bare init does not."""
    source = '''
import sentry_sdk
from poindexter.brain import sentry_scrub

def written_out():
    sentry_sdk.init(dsn="x", before_breadcrumb=a, before_send=b,
                    before_send_transaction=c, include_local_variables=False)

def splat_init_options():
    sentry_sdk.init(dsn="x", **sentry_scrub.init_options(extra_patterns=""))

def splat_dict_call():
    options: dict = dict(dsn="x", before_breadcrumb=a, before_send=b,
                         before_send_transaction=c, include_local_variables=False)
    sentry_sdk.init(integrations=[], **options)

def splat_dict_literal():
    opts = {"before_breadcrumb": a, "before_send": b,
            "before_send_transaction": c, "include_local_variables": False}
    sentry_sdk.init(**opts)

def bare():
    sentry_sdk.init(dsn="x", before_send=b)
'''
    tree = ast.parse(source)
    results = {}
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        call = next(n for n in ast.walk(fn) if _is_sentry_init(n))
        results[fn.name] = REQUIRED <= _options_passed(call, fn)
    assert results == {
        "written_out": True,
        "splat_init_options": True,
        "splat_dict_call": True,
        "splat_dict_literal": True,
        "bare": False,
    }
