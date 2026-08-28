#!/usr/bin/env python3
"""CI lint: the brain daemon never blocks its event loop.

The brain (``brain/``) is a single-event-loop asyncio daemon. ``brain_daemon``
awaits every probe *sequentially* on that one loop (see
``brain/brain_daemon.py`` ``run_cycle`` -> ``await run_*_probe(pool)``). So any
**synchronous blocking call executed directly on the loop** freezes the entire
watchdog for its whole duration — no other probe runs, the heartbeat stalls, the
reasoning queue backs up. A single ``docker restart`` (30 s) or a
``time.sleep(120)`` retry wait is enough to trip the brain's own
"stale" detector.

This lint fails when a blocking call sits **directly inside an ``async def``**:

  - ``subprocess.run`` / ``.call`` / ``.check_output`` / ``.check_call``
    (they wait for the child process),
  - ``os.system`` (waits + is a shell-injection sink),
  - ``time.sleep`` (blocks instead of yielding).

The fix is always one of two shapes (both already used across ``brain/``):

  - blocking I/O (subprocess, urllib):  ``await asyncio.to_thread(fn, *args)``
  - a wait between retries:             ``await asyncio.sleep(seconds)``
    (make the injected ``sleep_fn`` seam default to ``asyncio.sleep`` and
    ``await`` it — mirror ``brain/alert_dispatcher.py``).

Deliberately NOT flagged
------------------------
- ``subprocess.Popen`` — fire-and-forget spawn; it returns immediately without
  waiting for the child, so it does not hold the loop. (A following
  ``.communicate()`` / ``.wait()`` WOULD block, but that is a separate call.)
- A blocking call inside a **sync helper** (``def _restart_container(): ...``) —
  the convention is that async callers reach those helpers through
  ``asyncio.to_thread``. The lint trusts that seam; the ``await ... to_thread``
  offload is what keeps the loop free.
- A blocking call inside a **lambda** — a lambda is a scope boundary here (it is
  typically the callable handed to ``asyncio.to_thread``), so its body is not
  "directly on the loop".

Also flagged: the blocking sleep-seam default ``x or time.sleep``. A brain sleep
seam must default to ``asyncio.sleep`` (awaitable); ``time.sleep`` as the ``or``
fallback re-introduces the freeze even though the call site reads
``sleep_fn(...)``.

Escape hatch: an ``async-safety-ok`` marker on the offending line exempts a
genuinely-intentional case, e.g. ``# async-safety-ok: <reason>``.

Run:
    python scripts/ci/brain_async_safety_lint.py

Exit 0 = clean, exit 1 = at least one blocking call on the brain loop.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_scan_floor import require_dir, require_scanned  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BRAIN_DIR = REPO_ROOT / "brain"

# subprocess methods that WAIT for the child (Popen is excluded — see docstring).
_BLOCKING_SUBPROCESS = {"run", "call", "check_output", "check_call"}


def _is_blocking_call(node: ast.Call) -> str | None:
    """Return a short label if ``node`` is a known blocking-waiter call, else None."""
    fn = node.func
    if isinstance(fn, ast.Attribute):
        base = fn.value
        if isinstance(base, ast.Name):
            if base.id == "subprocess" and fn.attr in _BLOCKING_SUBPROCESS:
                return f"subprocess.{fn.attr}"
            if base.id == "os" and fn.attr == "system":
                return "os.system"
            if base.id == "time" and fn.attr == "sleep":
                return "time.sleep"
    return None


def _is_time_sleep_ref(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "sleep"
        and isinstance(node.value, ast.Name)
        and node.value.id == "time"
    )


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        # stack of booleans: True = the innermost enclosing scope is `async def`
        self._scope_is_async: list[bool] = []
        self.violations: list[tuple[int, str]] = []  # (lineno, message)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope_is_async.append(False)
        self.generic_visit(node)
        self._scope_is_async.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scope_is_async.append(True)
        self.generic_visit(node)
        self._scope_is_async.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # A lambda is a scope boundary (usually the callable passed to
        # asyncio.to_thread), so its body is not "directly on the loop".
        self._scope_is_async.append(False)
        self.generic_visit(node)
        self._scope_is_async.pop()

    def visit_Call(self, node: ast.Call) -> None:
        label = _is_blocking_call(node)
        if label and self._scope_is_async and self._scope_is_async[-1]:
            self.violations.append(
                (node.lineno, f"blocking `{label}()` runs on the brain event loop")
            )
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if isinstance(node.op, ast.Or) and any(
            _is_time_sleep_ref(v) for v in node.values
        ):
            self.violations.append(
                (
                    node.lineno,
                    "blocking sleep-seam default `... or time.sleep` — default to "
                    "`asyncio.sleep` and `await` it",
                )
            )
        self.generic_visit(node)


def _line_has_marker(lines: list[str], lineno: int) -> bool:
    if 1 <= lineno <= len(lines):
        return "async-safety-ok" in lines[lineno - 1]
    return False


def check_file(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    visitor = _Visitor()
    visitor.visit(tree)
    lines = text.splitlines()
    return [(ln, msg) for ln, msg in visitor.violations if not _line_has_marker(lines, ln)]


def main() -> int:
    # A missing brain/ used to print this same diagnosis and then return 0 --
    # the lint named its own blindness and passed anyway. Now it fails.
    require_dir(BRAIN_DIR, lint="brain_async_safety_lint")
    total = 0
    scanned = 0
    for path in sorted(BRAIN_DIR.rglob("*.py")):
        scanned += 1
        for lineno, msg in check_file(path):
            total += 1
            rel = path.relative_to(REPO_ROOT).as_posix()
            print(f"{rel}:{lineno}: {msg}")
    if total:
        print(
            f"\nbrain_async_safety_lint: {total} blocking call(s) on the brain loop. "
            f"Offload subprocess/urllib via `await asyncio.to_thread(...)`, and use "
            f"`await asyncio.sleep(...)` for waits.",
            file=sys.stderr,
        )
        return 1
    require_scanned(scanned, lint="brain_async_safety_lint", roots=(BRAIN_DIR,))
    print(
        f"brain_async_safety_lint: OK — no blocking calls on the brain event loop "
        f"({scanned} files)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
