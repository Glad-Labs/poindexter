"""Ask a model for one short piece of published text, check it in code, retry once.

The YouTube packaging (a long video's thumbnail text, a Short's title and its
description lead) is the most visible text a video has, and every piece of it
is written by a local model that does not reliably follow length or grounding
instructions. Measured more than once (see ``services/short_hook.py``): asked
for at most 42 characters, phi4 returned 33-82; asked for numbers only from
the source, a model put a "40% faster" on a video that never said it.

So the shape is always the same, and it lives here once:

1. one call with the writing prompt;
2. ``check(reply)`` reduces the reply to the published text, or names why it
   cannot be published;
3. on a rejection, one corrective call that carries that reason (the fix
   prompt), checked the same way;
4. after that, nothing. The caller decides what "nothing" means — no
   thumbnail text, or the deterministic fallback title — because a bad line in
   public is worse than a plain one.

Never raises: a failed call or a missing fix prompt comes back as
``("", why)``. The text the caller publishes is always the CHECKED text.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

#: ``(text, "")`` when the reply is publishable, else ``("", reason)``.
Check = Callable[[str], tuple[str, str]]
#: The corrective prompt for a rejection reason, or ``None`` for "no retry".
FixPrompt = Callable[[str], "str | None"]


async def generate_checked_line(
    *,
    pool: Any,
    model: str,
    prompt: str,
    check: Check,
    fix_prompt: FixPrompt,
    phase: str,
    temperature: float,
    max_tokens: int,
    timeout_s: float,
    disable_thinking: bool = True,
    label: str = "line",
    task_id: str | None = None,
) -> tuple[str, str]:
    """Return ``(text, note)``.

    ``note`` is ``""`` when the first reply passed, ``"retry"`` when the
    corrective reply did, and the reason otherwise (``text`` is then ``""``):
    ``"<label> call failed: …"``, ``"rejected (…); no retry prompt"`` or
    ``"rejected twice (…)"``. ``label`` names the text in logs and notes.
    """
    from poindexter.services.llm_providers.dispatcher import dispatch_complete
    from poindexter.utils.exception_format import describe_exception

    kwargs: dict[str, Any] = {}
    if disable_thinking:
        kwargs["think"] = False
    if task_id:
        kwargs["task_id"] = task_id
    messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    reason = ""
    for attempt in range(2):
        try:
            completion = await dispatch_complete(
                pool, messages, model, tier="standard", phase=phase,
                temperature=temperature, max_tokens=max_tokens, timeout_s=timeout_s, **kwargs,
            )
            raw = getattr(completion, "text", "") or ""
        except Exception as exc:  # noqa: BLE001 — a packaging line must never cost the render
            logger.warning(
                "[checked_llm_line] %s call to %s failed (%s)", label, model, describe_exception(exc),
            )
            return "", f"{label} call failed: {describe_exception(exc)}"
        text, reason = check(raw)
        if text:
            return text, "retry" if attempt else ""
        if attempt:
            break
        try:
            fix = fix_prompt(reason)
        except Exception as exc:  # noqa: BLE001 — no retry prompt means no retry, not a failed render
            logger.warning(
                "[checked_llm_line] %s fix prompt unavailable (%s) — no corrective retry",
                label, describe_exception(exc),
            )
            fix = None
        if not fix:
            return "", f"rejected ({reason}); no retry prompt"
        messages = [
            *messages,
            {"role": "assistant", "content": raw.strip()[:400]},
            {"role": "user", "content": fix},
        ]
    return "", f"rejected twice ({reason})"


__all__ = ["Check", "FixPrompt", "generate_checked_line"]
