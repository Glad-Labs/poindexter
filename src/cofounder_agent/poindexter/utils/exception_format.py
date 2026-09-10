"""Render exceptions so failure surfaces always name a cause.

``str()`` on several exception classes this stack actually hits is the EMPTY
STRING — ``httpx.ReadTimeout``, ``httpx.ConnectTimeout``,
``asyncio.TimeoutError`` and friends carry their meaning in the type, not the
message. Interpolating them bare (``f"fetch failed: {exc}"`` /
``detail=str(exc)``) produces a failure record that names no cause: the
featured-image renders timed out for weeks behind a ``render failed ()`` log
line before poindexter#3229 spotted it, and the same shape kept being
re-introduced (and re-hand-rolled as ``{type(e).__name__}: {e}``) in job
after job.

Use :func:`describe_exception` anywhere a caught exception becomes an
operator-facing string — ``JobResult.detail``, ``emit_finding`` bodies,
notify messages, log lines. Stdlib-only on purpose: importable from jobs,
stages, atoms, and plugins without dragging in FastAPI or services.
"""

from __future__ import annotations


def describe_exception(exc: BaseException) -> str:
    """Render an exception as something an operator can act on.

    Always prefer the type name over an empty message: ``ReadTimeout`` beats
    ``""``, and ``RuntimeError: gpu busy`` beats ``gpu busy`` (the type is
    what distinguishes a timeout from a refusal in a one-line alert).
    """
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
