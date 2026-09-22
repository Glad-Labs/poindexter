"""Repair a Short's opening line while it is still text.

A Short's first spoken sentence is also its YouTube title (#3944), and the
script prompt cannot be trusted to land it. Measured over ten published posts
on 2026-09-22 with the production model: an example sentence in the prompt
was copied onto unrelated articles 4 times in 10; with the example removed,
4 in 10 opened "Discover how …" and 10 in 10 overran the ~40 characters a
Shorts feed shows. The instruction is in the prompt. It does not hold.

So the hook gets the same shape every other unreliable generation here gets:
a deterministic gate, ONE corrective call when the gate fails, and a fallback
that is never worse than doing nothing.

    generate the script
      -> hook_defects(sentence 1)      no defects -> done, no LLM call
      -> one focused call, own model   the ONLY job is that sentence
      -> hook_defects(the new one)     better -> splice it into the script
                                       not better -> keep the original
      -> finding either way

Repairing at SCRIPT time, not at upload time, is the point: the narration is
rendered from this text, so the fixed line is what the presenter says AND
what the title shows. A title repaired later would promise something the
audio never says, which on Shorts costs more than a clumsy hook.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.services.short_hook import (
    content_defects,
    first_sentence,
    hook_limits,
    runaway_factor,
    sentences,
    strip_preamble,
)
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)

# Enough article for a claim without paying for the whole post.
_ARTICLE_CHARS = 3000
# One sentence out. Generous enough for a thinking preamble we then discard.
_MAX_TOKENS = 160
# The SCENE model by default, not the director's. Two measured reasons:
# gemma-4-31B restated the prompt on 10 of 10 hooks while phi4:14b wrote
# clean on-topic claims, and alternating an 8 GB scene model with a 17 GB
# hook model made GPU admission refuse 7 of 10 script calls outright.
_DEFAULT_MODEL = "ollama/phi4:14b"


def _enabled(site_config: Any) -> bool:
    if site_config is None:
        return True
    try:
        raw = site_config.get("media.short_hook.repair_enabled", "true")
    except Exception:  # noqa: BLE001  # silent-ok: a settings read must not decide a render
        return True
    return str(raw).strip().lower() not in ("false", "0", "no", "off")


def _model(site_config: Any) -> str:
    """The hook model, defaulting to the SCENE model the script just used.

    Not a bigger model, and that is a measured choice (2026-09-22, same ten
    articles): given the focused prompt, phi4:14b wrote clean on-topic claims
    while gemma-4-31B restated the brief on all ten. Reusing the scene model
    also keeps the card from swapping 8 GB of weights for 17 GB and back —
    which made admission refuse 7 of 10 script calls when it was tried.
    ``media.short_hook.model`` overrides it per install."""
    if site_config is None:
        return _DEFAULT_MODEL
    for key in ("media.short_hook.model", "video_scene_model"):
        try:
            val = str(site_config.get(key, "") or "").strip()
        except Exception:  # noqa: BLE001  # silent-ok
            val = ""
        if val and val.lower() != "auto":
            return val
    return _DEFAULT_MODEL


def build_hook_prompt(
    *, title: str, article: str, max_words: int, max_chars: int, target_seconds: int,
) -> str:
    """The repair prompt.

    Carries NO example sentence, deliberately: the shipped example in the
    script prompt was copied verbatim onto four unrelated articles, so this
    names the shapes to avoid instead of demonstrating one.
    """
    return (
        f"Write the opening line for a {target_seconds}-second vertical video "
        "about this article.\n\n"
        "ONE sentence, and nothing else. It is spoken first AND published as "
        "the video's title, so:\n"
        f"- at most {max_words} words and {max_chars} characters\n"
        "- name this article's own subject in the first three words\n"
        "- state the single most surprising concrete thing the article proves\n"
        "- make the claim; never describe the article. Do not begin with "
        "\"Discover how\", \"Learn how\", \"Find out\", \"This article\" or "
        "\"Here's why\"\n"
        "- no run-up. Do not begin with \"In today's ...\", \"In the world of "
        "...\", \"These days\", \"As we all know\" or \"Let's talk about\"\n"
        "- not a question, no hashtags, no quotation marks, no markdown, no "
        "trailing commentary\n"
        "- keep any number exactly as the article states it\n\n"
        f"ARTICLE TITLE: {title}\n\n"
        f"ARTICLE:\n{(article or '')[:_ARTICLE_CHARS]}\n\n"
        "OPENING LINE:"
    )


def clean_hook_reply(text: str) -> str:
    """The model's reply reduced to one bare sentence.

    Strips the wrappers a chat model adds around a one-line answer — quotes,
    a leading "Opening line:", markdown emphasis, a trailing explanation —
    then keeps the first sentence and the run-up strip.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    for line in raw.splitlines():
        candidate = line.strip().strip("*_` ").strip()
        if not candidate:
            continue
        low = candidate.lower()
        # gemma-4-31B restates the task before answering ("Goal: Write the
        # opening line for a 45-second vertical video...") — measured 10/10 on
        # 2026-09-22. Skip a line that is the brief rather than the answer.
        if low.startswith(("goal:", "task:", "objective:", "instruction", "prompt:", "constraints")):
            continue
        if low.startswith(("opening line", "here is", "here's the", "sure,", "certainly")):
            candidate = candidate.split(":", 1)[-1].strip()
            if not candidate:
                continue
        candidate = candidate.strip('"“”\'').strip()
        if not candidate:
            continue
        return strip_preamble(first_sentence(candidate) or candidate)
    return ""


def splice_hook(script: str, hook: str) -> str:
    """``script`` with its first sentence replaced by ``hook``."""
    parts = sentences(script)
    if not parts:
        return hook
    hook = hook.strip()
    if hook and hook[-1] not in ".!?":
        hook += "."
    return " ".join([hook, *parts[1:]]).strip()


async def repair_short_hook(
    script: str,
    *,
    title: str,
    article: str,
    site_config: Any,
    platform: Any,
    pool: Any,
    task_id: Any = None,
    target_seconds: int = 45,
) -> tuple[str, dict[str, Any]]:
    """Return ``(script, outcome)`` with a repaired opening line when needed.

    Never raises and never returns a worse hook: a GPU-busy skip, a dispatch
    failure, an unusable reply, or a replacement that scores no better than
    the original all leave ``script`` exactly as it came in.

    Passing ``max_wait_s`` opts this call into the admission contract, so its
    work can be SKIPPED under GPU contention — which is the right trade here
    and why it is allowlisted: the cost of skipping is one weaker title on one
    Short, and the payload's strip still improves it at upload time.
    """
    original = first_sentence(script)
    max_chars, max_words = hook_limits(site_config)
    runaway = runaway_factor(site_config)
    # Only a CONTENT defect is worth a call: length is shortened
    # deterministically by the title builder, and phi4's over-long sentences
    # were measured to be good claims.
    defects = content_defects(
        original, max_chars=max_chars, max_words=max_words, article_title=title,
        runaway_factor=runaway,
    )
    outcome: dict[str, Any] = {
        "original": original, "defects": list(defects), "repaired": False, "hook": original,
    }
    if not defects:
        return script, outcome
    if not _enabled(site_config):
        outcome["skipped"] = "disabled"
        return script, outcome
    if platform is None or pool is None:
        # Tests / bootstrap: the strip is still applied downstream by the
        # payload, so this is a degraded path, not a broken one.
        outcome["skipped"] = "no_platform"
        return script, outcome

    model = _model(site_config)
    prompt = build_hook_prompt(
        title=title, article=article, max_words=max_words,
        max_chars=max_chars, target_seconds=target_seconds,
    )
    from poindexter.modules.content.stages._media_gpu_skip import (
        surface_media_gpu_busy_skip,
    )
    from poindexter.services.gpu_admission import GpuBusyError

    try:
        from poindexter.services.gpu_scheduler import gpu, media_wait_budget_s

        async with gpu.lock(
            "ollama", model=model, task_id=task_id, phase="short_hook",
            max_wait_s=media_wait_budget_s(), priority="background",
        ):
            result = await platform.dispatch.complete(
                pool=pool,
                messages=[{"role": "user", "content": prompt}],
                model=model,
                tier="standard",
                timeout_s=60,
                temperature=0.6,
                max_tokens=_MAX_TOKENS,
            )
        candidate = clean_hook_reply(getattr(result, "text", "") or "")
    except GpuBusyError as busy:
        # Admission refused the wait. This caller is allowlisted to be skipped
        # precisely because it can afford it: the script keeps the opener it
        # already had and the payload's strip still applies at upload time.
        # Never terminal — nothing is lost that the piece needed.
        logger.info(
            "[SHORT_HOOK] task %s: skipped — GPU busy (%s); keeping the original opener",
            task_id, getattr(busy, "reason", busy),
        )
        surface_media_gpu_busy_skip("short_hook", busy, task_id=str(task_id or "") or None)
        outcome["skipped"] = "gpu_busy"
        return script, outcome
    except Exception as exc:  # noqa: BLE001 — a hook is never worth failing a script over
        logger.warning(
            "[SHORT_HOOK] repair call failed for task %s (%s) — keeping the original opener",
            task_id, describe_exception(exc),
        )
        outcome["error"] = describe_exception(exc)
        _emit(task_id, outcome, model, repaired=False, reason="call_failed")
        return script, outcome

    new_defects = content_defects(
        candidate, max_chars=max_chars, max_words=max_words, article_title=title,
        runaway_factor=runaway,
    ) if candidate else ("empty",)
    outcome["candidate"] = candidate
    outcome["candidate_defects"] = list(new_defects)
    outcome["model"] = model

    # Strictly better only. A replacement that trades "too_long" for
    # "not_a_claim" is not an improvement, and the original at least matches
    # the narration the writer built around it.
    if candidate and len(new_defects) < len(defects):
        script = splice_hook(script, candidate)
        outcome.update(repaired=True, hook=first_sentence(script))
        logger.info(
            "[SHORT_HOOK] task %s: %r (%s) -> %r (%s) via %s",
            task_id, original, ",".join(defects) or "-",
            outcome["hook"], ",".join(new_defects) or "clean", model,
        )
        _emit(task_id, outcome, model, repaired=True, reason="repaired")
        return script, outcome

    logger.info(
        "[SHORT_HOOK] task %s: keeping the original opener %r (%s); candidate %r (%s)",
        task_id, original, ",".join(defects), candidate, ",".join(new_defects),
    )
    _emit(task_id, outcome, model, repaired=False, reason="no_improvement")
    return script, outcome


def _emit(task_id: Any, outcome: dict[str, Any], model: str, *, repaired: bool, reason: str) -> None:
    kind = "short_hook_repaired" if repaired else "short_hook_unrepaired"
    emit_finding(
        source="media.short_hook",
        kind=kind,
        title=(
            f"short hook repaired ({','.join(outcome.get('defects') or []) or 'none'})"
            if repaired else
            f"short hook still weak ({','.join(outcome.get('defects') or []) or 'none'})"
        ),
        body=(
            f"Task {task_id}: the Short's first sentence is published verbatim as its "
            f"YouTube title.\n\nbefore: {outcome.get('original')!r} "
            f"({','.join(outcome.get('defects') or []) or 'clean'})\n"
            f"after:  {outcome.get('hook')!r}\n"
            f"candidate: {outcome.get('candidate')!r} "
            f"({','.join(outcome.get('candidate_defects') or []) or 'clean'})\n"
            f"model: {model}; reason: {reason}\n\n"
            "Frequent 'still weak' means the hook model or the script prompt needs "
            "attention — the title is the first thing a Shorts viewer reads."
        ),
        severity="info" if repaired else "warn",
        dedup_key=f"{kind}:{task_id}",
        extra={"task_id": str(task_id or ""), "model": model, "reason": reason,
               "defects": outcome.get("defects"), "repaired": repaired},
    )


__all__ = ["build_hook_prompt", "clean_hook_reply", "repair_short_hook", "splice_hook"]
