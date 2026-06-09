"""Thinking-model detection.

Classifies whether a given model identifier is a "thinking" model — one
that produces ``<think>...</think>`` reasoning blocks before its final
answer. Thinking models need different handling than chat-completion
models:

- Higher max_tokens budget (the reasoning trace eats budget before the
  final output begins).
- The ``/nothink`` directive (qwen3 family) to suppress reasoning when
  the caller wants direct JSON output.
- Output stripping (``<think>...</think>`` blocks must be removed
  before parsing).

Pre-2026-05-09, four call sites each carried their own hardcoded list
of thinking-model substrings (``"qwen3"`` / ``"glm-4"`` /
``"deepseek-r1"``), and the lists drifted. ``ai_content_generator``
included ``"deepseek-r1"`` while ``image_decision_agent`` did not;
``multi_model_qa`` used ``"qwen3.5"`` and ``"qwen3:30b"`` but
``ai_content_generator`` only matched on the bare ``"qwen3"`` prefix.

This module is the one place that decides. Substrings live in
``app_settings.thinking_model_substrings`` so operators can add a new
thinking model without a code change.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Hardcoded fallback used only when ``app_settings`` is unreachable
# (test paths, fresh installs before the seed migration runs). Mirrors
# the union of the historical inline lists pre-2026-05-09 so existing
# behavior is preserved when the DB is silent.
_DEFAULT_SUBSTRINGS: tuple[str, ...] = (
    "qwen3",
    "qwen3.5",
    "glm-4",
    "glm-4.7",
    "deepseek-r1",
)


def strip_think_blocks(text: str) -> str:
    """Remove every ``<think>...</think>`` (case-insensitive, multiline)
    block from ``text``. Returns the stripped string (whitespace
    trimmed) — empty string if every token was inside think tags.

    Pre-2026-05-26, callers either rolled their own ``re.sub`` (see
    ``image_decision_agent.py:254``) or didn't strip at all. The triage
    path bit Matt 2026-05-26: ``ops_triage_writer_model`` resolved to
    ``glm-4.7-5090`` which produced ~20-second runs of pure think-tag
    output, leaving the operator-facing diagnosis empty. This helper
    is the canonical strip so every site that uses a thinking model
    gets consistent behaviour.

    Why ``DOTALL``: ``<think>`` blocks span newlines.
    Why case-insensitive: some providers emit ``<Think>`` or ``<THINK>``.
    Why ``.*?`` (non-greedy): a single response can contain multiple
    think blocks; we want to remove each individually, not the span
    from the first open to the last close.
    """
    if not text:
        return ""
    stripped = re.sub(
        r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE,
    )
    return stripped.strip()


# ── Reasoning / control-token artifact stripping ──────────────────────────
#
# Mis-templated or reasoning-channel models leak chat-template / reasoning
# control tokens straight into prose. Two real prod captures (2026-06-09):
# ``gemma-4-31B-it-qat`` (a broken Ollama ``<|turn>`` Modelfile template)
# and ``glm-4.7-5090`` both emitted article bodies that began, at char 1,
# with::
#
#     <|channel>thought
#     <channel|>The release of ...
#
# i.e. a mangled-Harmony channel header with the WHOLE article inside the
# ``thought`` channel — exactly the "some reasoning models put their output
# in the thinking block" case. :func:`strip_think_blocks` only knows
# ``<think>...</think>`` and misses this entirely.
# :func:`strip_reasoning_artifacts` handles both forms, keeps the prose
# (rather than discarding a thinking block that *is* the answer), and is
# fence-aware so a legitimate ``<|channel|>`` shown as a code EXAMPLE in an
# AI/ML post survives untouched.

# A mangled or proper channel header, consumed as a unit together with its
# channel-label word so the prose that follows survives clean:
#   "<|channel>thought\n<channel|>"  -> ""   (mangled Harmony)
#   "<|channel|>analysis<|message|>" -> ""   (proper Harmony)
# The opener requires a pipe on at least one side (``<|channel>`` / ``<channel|>``
# / ``<|channel|>``) — a bare ``<channel>`` with no pipe is left alone.
_CHANNEL_HEADER_RE = re.compile(
    r"<(?:\|channel\|?|channel\|)>\s*"
    r"(?:thought|analysis|final|commentary)?\s*"
    r"(?:<(?:\|(?:message|channel)\|?|(?:message|channel)\|)>)?",
    re.IGNORECASE,
)

# A chat-turn header with its role label (the broken gemma ``<|turn>`` template
# form): "<|turn>model\n...<turn|>" -> the marker + role label are consumed.
_TURN_HEADER_RE = re.compile(
    r"<(?:\|turn\|?|turn\|)>\s*"
    r"(?:system|user|model|assistant)?\s*"
    r"(?:<(?:\|turn\|?|turn\|)>)?",
    re.IGNORECASE,
)

# Proper multi-channel Harmony: a ``<|channel|>final<|message|>`` header means
# the real answer is everything after the LAST one; the analysis / commentary
# channel before it is private reasoning to discard.
_FINAL_CHANNEL_RE = re.compile(
    r"<(?:\|channel\|?|channel\|)>\s*final\s*<(?:\|message\|?|message\|)>",
    re.IGNORECASE,
)

# Standalone chat-template / reasoning control markers. EXACT keyword allowlist
# AND a pipe is REQUIRED on at least one side — never a generic ``<\w+>`` sweep —
# so legitimate JSX / HTML / prose the writer emits (``<user>``, ``<System>``,
# ``<message>``, ``<section>``, ``<article>`` …) is never touched. Only the real
# control tokens, which always carry a pipe (``<|im_start|>``, ``<|end|>``,
# ``<channel|>`` …), match.
_CONTROL_MARKER_RE = re.compile(
    r"<(?:"
    r"\|(?:channel|turn|message|start|end|return|im_start|im_end|assistant|user|system)\|?"
    r"|(?:channel|turn|message|start|end|return|im_start|im_end|assistant|user|system)\|"
    r")>",
    re.IGNORECASE,
)

_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)

# Fenced code (``` … ``` / ~~~ … ~~~) and inline ``code`` — preserved verbatim
# so a control token shown as an EXAMPLE is never stripped.
_CODE_SPAN_RE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]+`", re.DOTALL)

_COLLAPSE_BLANK_LINES = re.compile(r"\n{3,}")


def _apply_outside_code(text: str, transform: Any) -> str:
    """Run ``transform`` on the prose between code spans; leave code verbatim."""
    out: list[str] = []
    last = 0
    for m in _CODE_SPAN_RE.finditer(text):
        out.append(transform(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(transform(text[last:]))
    return "".join(out)


def _strip_think_global(text: str) -> str:
    """Drop ``<think>…</think>`` blocks. Runs on the WHOLE text (not per
    code-fenced segment) so a think block that legitimately *wraps* a code
    fence is still matched as one balanced pair. If the whole answer is
    *inside* the block (nothing survives outside — some reasoning models put
    their output there), unwrap and keep the inner prose rather than emptying.
    """
    if "<think" not in text.lower():
        return text
    without = _THINK_BLOCK_RE.sub("", text)
    if without.strip():
        return without  # a real answer survives outside the block — drop reasoning
    # The entire answer lived inside the think block — keep the inner prose.
    return _THINK_BLOCK_RE.sub(lambda m: m.group(1), text)


def _keep_final_channel(text: str) -> str:
    """Proper multi-channel Harmony: keep only the final-channel body. Slices
    on the LAST ``<|channel|>final<|message|>`` marker that is OUTSIDE a code
    span (so a fenced Harmony *example* never triggers a slice)."""
    spans = [(m.start(), m.end()) for m in _CODE_SPAN_RE.finditer(text)]
    last_end = None
    for m in _FINAL_CHANNEL_RE.finditer(text):
        if not any(a <= m.start() < b for a, b in spans):
            last_end = m.end()
    return text[last_end:] if last_end is not None else text


def _strip_control_markers(segment: str) -> str:
    segment = _CHANNEL_HEADER_RE.sub("", segment)
    segment = _TURN_HEADER_RE.sub("", segment)
    return _CONTROL_MARKER_RE.sub("", segment)


def _is_valid_json(text: str) -> bool:
    """True if ``text`` (trimmed) parses as a complete JSON object/array.

    A leaked-reasoning payload (``<think>…</think>{…}`` / ``<|channel>thought…``)
    does NOT parse — the wrapper breaks it — so a clean parse means the caller
    owns this JSON (e.g. a ``pipeline_architect`` graph spec consumed via
    ``json.loads``) and the strip must not mutate a control-token literal that
    legitimately sits inside a string value.
    """
    s = text.strip()
    if not s or s[0] not in "{[" or s[-1] not in "}]":
        return False
    try:
        json.loads(s)
        return True
    except (ValueError, TypeError):
        return False


def strip_reasoning_artifacts(text: str) -> str:
    """Strip leaked reasoning / chat-template control-token artifacts from
    model output while preserving the underlying prose and any code fences.

    Mirrors the defensive posture of ``maybe_unwrap_json`` (a no-op on clean
    output) but targets reasoning-channel leakage instead of JSON envelopes:

    - ``<think>…</think>`` blocks — dropped when a real answer follows them
      (handled globally, so a block that wraps a code fence is still matched);
      unwrapped (inner prose kept) when the whole answer is *inside* the block.
    - Proper multi-channel Harmony — when a ``final`` channel header is present,
      everything before it (the analysis / commentary reasoning) is dropped.
    - Mangled-Harmony channel headers (``<|channel>thought<channel|>``), chat
      ``<|turn>role`` headers, and standalone control markers (``<|message|>``,
      ``<|end|>``, ``<|im_start|>`` …) — removed, prose kept.

    Pipe-required: a control token must carry a pipe on at least one side, so a
    bare ``<user>`` / ``<System>`` / ``<message>`` (legitimate JSX / HTML /
    prose) survives. Fence-aware: control tokens shown as examples inside ```
    code blocks or inline ``code`` are left untouched, so a technical post about
    the Harmony format keeps its examples.

    Returns the cleaned string (whitespace trimmed) when anything was removed,
    and the input UNCHANGED otherwise — a true no-op on clean output and on
    falsy / ``<``-free input (the common fast path).
    """
    if not text or "<" not in text:
        return text
    # Caller-owned JSON is left intact: a payload that already parses as JSON
    # (e.g. a graph spec via ollama_chat_text + json.loads) must not have a
    # control-token literal mutated out of a string value. Reasoning leaks make
    # output NON-parseable (leading <think>/channel header), so they fall
    # through to the strip below.
    if _is_valid_json(text):
        return text
    cleaned = _strip_think_global(text)
    cleaned = _keep_final_channel(cleaned)
    cleaned = _apply_outside_code(cleaned, _strip_control_markers)
    if cleaned == text:
        return text  # nothing matched — true no-op (semantic HTML untouched)
    cleaned = _COLLAPSE_BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()


def is_thinking_model(model: str, *, substrings: tuple[str, ...] | list[str] | None = None) -> bool:
    """Return True if ``model`` looks like a thinking-model identifier.

    Args:
        model: The model identifier string. May be a bare name
            (``"glm-4.7-5090:latest"``), a LiteLLM-prefixed identifier
            (``"ollama/qwen3:30b"``), or any operator-supplied form.
            Comparison is case-insensitive.
        substrings: Override list. If ``None``, the caller is
            responsible for resolving the configured list and passing
            it in (typical pattern: read once via
            ``resolve_thinking_substrings(site_config)`` at the start
            of a hot path, then reuse). If unset and not provided, the
            module falls back to ``_DEFAULT_SUBSTRINGS``.

    Returns:
        True when any configured substring is found inside the
        lowercased model name; False otherwise.
    """
    if not model:
        return False
    needle = model.lower()
    pool = substrings if substrings is not None else _DEFAULT_SUBSTRINGS
    return any(s in needle for s in pool)


def resolve_thinking_substrings(site_config: Any) -> tuple[str, ...]:
    """Resolve the configured thinking-model substring list.

    Reads ``app_settings.thinking_model_substrings`` (a JSON array of
    strings). Falls back to ``_DEFAULT_SUBSTRINGS`` on missing key,
    empty value, or parse failure. Never raises — this helper is
    on every LLM-call hot path.

    Args:
        site_config: A ``SiteConfig`` instance (DI-provided). May also
            be ``None`` for legacy test paths; falls through to defaults.

    Returns:
        Tuple of substring needles, lowercase. Always non-empty.
    """
    if site_config is None:
        return _DEFAULT_SUBSTRINGS
    try:
        raw = site_config.get("thinking_model_substrings", "")
    except Exception:
        return _DEFAULT_SUBSTRINGS
    if not raw:
        return _DEFAULT_SUBSTRINGS
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return _DEFAULT_SUBSTRINGS
    if not isinstance(parsed, list) or not parsed:
        return _DEFAULT_SUBSTRINGS
    return tuple(str(s).lower() for s in parsed if s)
