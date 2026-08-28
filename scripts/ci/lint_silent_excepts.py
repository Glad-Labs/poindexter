#!/usr/bin/env python3
"""CI lint: no NEW silently-swallowed exceptions in production code.

Silent-failure audit follow-up (H2). A swallowed exception is invisible to
the operator when a single-statement handler records the failure with *only*:

  - ``pass`` — nothing recorded at all
  - ``<logger>.debug(...)`` — below the prod Loki level (INFO)
  - ``<logger>.info(...)`` — reaches Loki but is below GlitchTip's ERROR event
    gate (LoggingIntegration event_level=ERROR), so it never pages
  - ``return <sentinel>`` under a *broad* except (``except:`` /
    ``except Exception`` / ``except BaseException``) — swallows anything and
    returns None / a constant / an empty collection with no log, no finding,
    no re-raise. A *narrow* except returning a sentinel
    (``except ValueError: return None``) is a deliberate fallback, left alone.

Two-statement and control-flow swallows count too — a *broad* handler whose
whole body is low-visibility is silent even when spread across statements:

  - ``<logger>.debug(...)`` then ``return <sentinel>`` — the two-statement
    form of the same swallow
  - ``continue`` / ``break`` in a loop (skips the failure with no record)
  - a bare ``...`` (pass-equivalent) or a ``print(...)`` (stdout only, never
    a logging event — reaches neither GlitchTip nor a board)

A handler is VISIBLE (not counted) the moment any statement is a real signal:
an ``emit_finding(...)`` call, a ``warning``+/``error``/``exception``/
``critical`` log, or a ``raise``. That is the bar — surface a best-effort
failure as an info-severity finding (Findings dashboard, deduped, non-paging)
or a warning+; ``debug``/``info`` alone is below it. In a loop, count the
failures and emit ONE finding after the loop, never one per iteration.

…or when a whole block is wrapped in broad ``contextlib.suppress(Exception)``
(an ``ast.With`` node, not an ``ExceptHandler``, so the handler scan alone
never sees it).

Either way the failure never reaches the operator's alerting bar. This lint
stops that category from
GROWING. It does NOT try to fix the ~existing sites in one pass — those are
captured in a per-file BASELINE (``silent_excepts_baseline.json``) and the
lint only fails when a file's count exceeds its baseline (i.e. a NEW silent
swallow was added) or a previously-clean file grows one.

The baseline is a ratchet: it may only shrink. Lower a file's number when
you convert a silent swallow into a visible one (escalate the log level,
emit a finding, re-raise) and re-run with ``--update-baseline``.

Escape hatch for a genuinely-fine new silent swallow (best-effort cleanup,
a close() in a finally-ish except): add a bare ``# silent-ok: <reason>``
comment on the ``except`` line or anywhere inside the handler. The marker
is a plain comment this lint scans for, NOT a ruff code, so write it bare.
Do *not* write ``# noqa: silent-ok`` — ruff then tries to parse
``silent-ok`` as a suppression code and warns "Invalid ``# noqa``
directive" on every run. If you additionally need to silence a real ruff
rule, stack two separate comments on the line:
``# noqa: BLE001  # silent-ok: <reason>``. (BLE001 / S110 are not in this
repo's ruff ``select`` set, so a bare ``except Exception: pass`` needs no
noqa at all.)

Run:
    python scripts/ci/lint_silent_excepts.py                 # check
    python scripts/ci/lint_silent_excepts.py --update-baseline  # re-baseline

Exit 0 = no new silent swallows, exit 1 = at least one new one.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_scanned  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).resolve().parent / "silent_excepts_baseline.json"

# Production Python trees. Tests are intentionally excluded — a swallowed
# exception in a test asserts nothing, it isn't an operator-visibility risk.
SCAN_ROOTS = [
    (REPO_ROOT / "src" / "cofounder_agent", ("tests",)),
    (REPO_ROOT / "brain", ()),
    # The public MCP server — operator phone-facing tools. A swallowed
    # exception here means an operator action silently no-ops. The private
    # ``mcp-server-gladlabs/`` overlay is intentionally NOT scanned: its paths
    # must never enter this baseline, which ships in the public mirror.
    #
    # ``.venv`` is excluded because mcp-server has its own uv environment:
    # ``uv run pytest`` materialises ``mcp-server/.venv``, and this walk would
    # then scan every installed dependency's source for swallowed exceptions.
    # CI checks out fresh so it never saw this; locally it turned the ratchet
    # red against third-party code.
    (REPO_ROOT / "mcp-server", ("tests", ".venv")),
]

OVERRIDE_MARKER = "silent-ok"

# The "catch everything" exception types. Catching one — via ``except
# Exception:`` or ``contextlib.suppress(Exception)`` — and then not recording
# the failure is the swallow this lint guards. Narrow, named exceptions
# (OSError, ValueError, …) are deliberate control flow.
_BROAD_EXCEPTION_NAMES = {"Exception", "BaseException"}

# debug is below the prod Loki level (INFO); info reaches Loki but is below
# GlitchTip's ERROR event gate. For an *exception*, a handler that only logs at
# one of these never pages — both sit below the operator's alerting bar.
_LOW_VISIBILITY_LOG_METHODS = {"debug", "info"}

# Zero-arg calls to these builtins are the call-form spelling of an empty
# collection literal — ``return set()`` is the same sentinel swallow as
# ``return []`` / ``return {}``, just spelled as a constructor call (``set``
# has no empty-literal syntax at all — ``{}`` is a dict).
_EMPTY_COLLECTION_CTORS = {"set", "dict", "list", "tuple", "frozenset"}


def _is_low_visibility_log_call(stmt: ast.stmt) -> bool:
    """True if stmt is a bare ``<anything>.debug(...)`` / ``.info(...)`` call."""
    if not isinstance(stmt, ast.Expr):
        return False
    call = stmt.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr in _LOW_VISIBILITY_LOG_METHODS


def _is_ellipsis_expr(stmt: ast.stmt) -> bool:
    """True if stmt is a bare ``...`` (Ellipsis) — a pass-equivalent no-op body."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    )


def _is_print_call(stmt: ast.stmt) -> bool:
    """True if stmt is a bare ``print(...)`` call — stdout only, never a logging
    event, so it reaches neither GlitchTip nor a surfaced board."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "print"
    )


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    """True if the ``except`` clause catches everything — bare ``except:`` or
    ``except Exception`` / ``except BaseException`` (alone or in a tuple)."""
    exc = handler.type
    if exc is None:
        return True  # bare ``except:``
    candidates = exc.elts if isinstance(exc, ast.Tuple) else [exc]
    for node in candidates:
        name = (
            node.id
            if isinstance(node, ast.Name)
            else node.attr
            if isinstance(node, ast.Attribute)
            else None
        )
        if name in _BROAD_EXCEPTION_NAMES:
            return True
    return False


def _is_sentinel_return(stmt: ast.stmt) -> bool:
    """True if stmt returns a sentinel — bare ``return``, a constant (None /
    bool / number / str / bytes), an empty collection literal, or a zero-arg
    call to an empty-collection constructor (``set()`` / ``dict()`` /
    ``list()`` / ``tuple()`` / ``frozenset()``). Returning a named value, a
    non-empty literal, or a call carrying an argument/keyword is a meaningful
    fallback, not a sentinel, and is NOT flagged."""
    if not isinstance(stmt, ast.Return):
        return False
    val = stmt.value
    if val is None:  # bare ``return``
        return True
    if isinstance(val, ast.Constant):  # return None / False / 0 / "" / b""
        return True
    if isinstance(val, (ast.List, ast.Set, ast.Tuple)):
        return len(val.elts) == 0
    if isinstance(val, ast.Dict):
        return len(val.keys) == 0
    if (
        isinstance(val, ast.Call)
        and isinstance(val.func, ast.Name)
        and val.func.id in _EMPTY_COLLECTION_CTORS
        and not val.args
        and not val.keywords
    ):
        return True
    return False


def _stmt_is_low_visibility(stmt: ast.stmt, *, broad: bool) -> bool:
    """True if one handler-body statement is below the operator's alerting bar.

    Always low-visibility (any breadth): ``pass``, ``...``, a
    ``<logger>.debug/info(...)`` call, a ``print(...)``.
    Low-visibility ONLY under a *broad* except (a narrow except names its
    exception, making these deliberate control flow): ``continue`` / ``break``
    and a sentinel ``return`` (bare / constant / empty collection).
    Anything else — a ``warning``+/``error``/``exception``/``critical`` log, an
    ``emit_finding(...)`` call, a ``raise``, an assignment, any real call — is a
    visibility signal.
    """
    if isinstance(stmt, ast.Pass) or _is_ellipsis_expr(stmt):
        return True
    if _is_low_visibility_log_call(stmt) or _is_print_call(stmt):
        return True
    if broad and isinstance(stmt, (ast.Continue, ast.Break)):
        return True
    if broad and _is_sentinel_return(stmt):
        return True
    return False


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    """True if EVERY statement in the handler body is below operator visibility —
    the failure is recorded nowhere an operator will see it (no finding, no
    warning+, no re-raise).

    Covers the single ``pass`` / ``<logger>.debug(...)`` handler AND its
    multi-statement forms — a broad ``except`` whose whole body is
    ``<logger>.debug(...)`` then ``return None`` (or ``continue`` in a loop) is
    the same swallow. Any visibility signal anywhere in the body — an
    ``emit_finding(...)`` call, a ``warning``+ log, a ``raise`` — makes it NOT
    silent.
    """
    broad = _is_broad_except(handler)
    return all(_stmt_is_low_visibility(s, broad=broad) for s in handler.body)


def _node_has_override(node: ast.AST, lines: list[str]) -> bool:
    """True if a ``silent-ok`` marker appears anywhere in the node's span.

    Works for both an ``except`` handler and a ``with suppress(...)`` block —
    the escape hatch is identical: a bare ``# silent-ok: <reason>`` comment on
    the opening line or anywhere inside the body exempts an intentional swallow.
    """
    start = getattr(node, "lineno", 1)
    end = start
    for child in ast.walk(node):
        node_end = getattr(child, "end_lineno", None)
        if node_end is not None and node_end > end:
            end = node_end
    # lines is 0-indexed; AST linenos are 1-indexed.
    for ln in range(start, end + 1):
        if 0 <= ln - 1 < len(lines) and OVERRIDE_MARKER in lines[ln - 1]:
            return True
    return False


def _is_broad_suppress(node: ast.With | ast.AsyncWith) -> bool:
    """True if a ``with`` statement swallows a broad exception via suppress().

    Matches ``with suppress(Exception):`` and ``with contextlib.suppress(
    BaseException):`` — both silence the whole body with no log, no finding,
    no re-raise, exactly like ``except Exception: pass``. Narrow suppression
    (``suppress(OSError)``, ``suppress(ValueError, TypeError)``) names the
    exception it expects and is NOT counted.
    """
    for item in node.items:
        call = item.context_expr
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        is_suppress = (isinstance(func, ast.Name) and func.id == "suppress") or (
            isinstance(func, ast.Attribute) and func.attr == "suppress"
        )
        if not is_suppress:
            continue
        for arg in call.args:
            arg_name = (
                arg.id
                if isinstance(arg, ast.Name)
                else arg.attr
                if isinstance(arg, ast.Attribute)
                else None
            )
            if arg_name in _BROAD_EXCEPTION_NAMES:
                return True
    return False


def scan_file(path: Path) -> int:
    """Count un-overridden silent swallows in one file.

    Two shapes count: an ``except`` handler whose body is only ``pass`` /
    ``<logger>.debug(...)``, and a ``with contextlib.suppress(Exception)``
    block (broad suppression). Both hide the failure below operator
    visibility; both are exempt with a bare ``# silent-ok: <reason>`` marker.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return 0
    lines = source.splitlines()
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _handler_is_silent(node):
            if not _node_has_override(node, lines):
                count += 1
        elif isinstance(node, (ast.With, ast.AsyncWith)) and _is_broad_suppress(node):
            if not _node_has_override(node, lines):
                count += 1
    return count


_SCANNED = 0  # files actually read by the last compute_counts() call


def compute_counts() -> dict[str, int]:
    """Map of ``relpath -> silent-handler count`` across all scan roots."""
    global _SCANNED
    _SCANNED = 0
    counts: dict[str, int] = {}
    for root, excluded in SCAN_ROOTS:
        if not root.exists():
            continue
        for py_file in sorted(root.rglob("*.py")):
            rel_parts = py_file.relative_to(root).parts
            if excluded and rel_parts and rel_parts[0] in excluded:
                continue
            _SCANNED += 1
            n = scan_file(py_file)
            if n:
                rel = str(py_file.relative_to(REPO_ROOT)).replace("\\", "/")
                counts[rel] = n
    return counts


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate silent_excepts_baseline.json from the current tree.",
    )
    args = parser.parse_args()

    counts = compute_counts()

    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps(dict(sorted(counts.items())), indent=2) + "\n",
            encoding="utf-8",
        )
        total = sum(counts.values())
        print(
            f"lint_silent_excepts: baseline written — "
            f"{len(counts)} files, {total} silent handlers grandfathered."
        )
        return 0

    baseline = load_baseline()
    regressions: list[str] = []
    for rel, n in sorted(counts.items()):
        allowed = baseline.get(rel, 0)
        if n > allowed:
            regressions.append(
                f"  {rel}: {n} silent except handler(s), baseline allows {allowed}"
            )

    if regressions:
        print("NEW SILENT EXCEPTION HANDLERS (not in baseline):")
        for r in regressions:
            print(r)
        print(
            "\nThese handlers swallow a failure below the operator's alerting "
            "bar — body is only `pass`, `<logger>.debug/info(...)`, or "
            "`return <sentinel>` under a broad except, or a broad "
            "`contextlib.suppress(...)`. Make it visible — escalate to "
            "warning+/error, emit_finding(severity>=warn), or re-raise. If the "
            "swallow is genuinely fine (best-effort cleanup), add a bare "
            "`# silent-ok: <reason>` comment (a plain comment, NOT a ruff "
            "code — do not write `# noqa: silent-ok`, which makes ruff warn "
            "it can't parse the directive). If you intentionally reduced a "
            "count, re-run with --update-baseline."
        )
        return 1

    require_scanned(_SCANNED, lint="lint_silent_excepts", roots=[r for r, _ in SCAN_ROOTS])
    total = sum(counts.values())
    print(
        f"lint_silent_excepts: clean — no new silent handlers "
        f"({total} baselined across {len(counts)} files, {_SCANNED} scanned; "
        "ratchet only shrinks)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
