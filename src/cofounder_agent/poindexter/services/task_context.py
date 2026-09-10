"""Ambient pipeline-task identity — a ContextVar bound around each template run.

``TemplateRunner.run`` binds the running task's id here so low-level seams
(LLM dispatch, cost logging, Langfuse trace stamping) can attribute work to
the task WITHOUT every intermediate call site threading ``task_id`` through
its signature. asyncio propagates ContextVars across ``await`` boundaries and
copies them into ``asyncio.create_task`` children, so every LLM call made
anywhere inside a run sees the binding, while concurrent runs in the same
process each see their own.

An explicit ``task_id`` argument always wins — this is the fallback for call
sites that never threaded one, not an override (Glad-Labs/poindexter#902).
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_task_id: ContextVar[str | None] = ContextVar(
    "poindexter_current_task_id", default=None
)


def current_task_id() -> str | None:
    """The task id bound for this execution context, or ``None`` outside a run."""
    return _current_task_id.get()


def bind_task_id(task_id: str | None) -> Token:
    """Bind ``task_id`` for the current context; returns the token for reset.

    Falsy values bind ``None`` (still returns a token) so callers can wrap
    ad-hoc runs without conditionals.
    """
    return _current_task_id.set(str(task_id) if task_id else None)


def reset_task_id(token: Token) -> None:
    """Restore the binding that preceded the matching :func:`bind_task_id`."""
    _current_task_id.reset(token)
