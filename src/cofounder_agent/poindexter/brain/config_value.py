"""brain/config_value.py — read a JSON list an operator wrote into app_settings.

Some brain probes take their targets from a JSON list in ``app_settings``:
``scheduled_workflow_watch`` reads ``scheduled_workflows`` and
``data_freshness_probe`` reads ``data_freshness_feeds``. When that value
cannot be used as written, the probe pages instead of watching nothing (or
less than the operator believes) and reporting healthy. Such a page must say
what is wrong in terms the operator can act on:
- where the JSON broke;
- what the value holds instead of a list;
- which entry was ignored, and why.

These are the parts the probes share. Each probe keeps its own entry
validation and its own page wording.

Pure and stdlib only: the brain image ships ``poindexter/brain/`` alone.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Ignored:
    """An entry the probe cannot use as written."""

    index: int | None  # its position in the operator's list, counting from 1
    field: str  # what is wrong with it: a short code the probe defines
    reason: str  # operator-facing: which entry, and why
    entry: Any  # the entry as written, for the episode signature


@dataclass(frozen=True)
class ValueProblem:
    """Why a value is not a usable JSON list at all."""

    kind: str  # the signature's tail: invalid-json:char-83, not-a-list:object, ...
    summary: str  # the problem in one line, for a pass detail
    problem: str  # what the page says is wrong
    hint: str = ""  # how that usually happens, when there is a usual way


def show(value: Any, limit: int = 60) -> str:
    """A value as the operator wrote it: JSON-quoted, cut to ``limit`` characters."""
    text = json.dumps(value, ensure_ascii=False)
    return clip(text, limit)


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def excerpt(raw: str, pos: int, width: int = 24) -> str:
    """The text around a JSON syntax error, on one line, for a page."""
    start, end = max(0, pos - width), min(len(raw), pos + width)
    text = " ".join(raw[start:end].split()).replace("`", "'")
    return f"{'…' if start else ''}{text}{'…' if end < len(raw) else ''}"


def json_type(value: Any) -> str:
    """The JSON name of a parsed value's type."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object" if isinstance(value, dict) else type(value).__name__


def not_a_list_hint(value: Any, noun: str = "entry") -> str:
    """The usual way a list setting ends up as something other than a list."""
    if isinstance(value, dict):
        return f"It must be a list even for one {noun}: wrap the object in [ ]."
    if isinstance(value, str):
        try:
            inner = json.loads(value)
        except (ValueError, RecursionError):
            return ""
        if isinstance(inner, list):
            return (
                "The string itself holds a JSON list, so the value was encoded "
                "twice: store the list, not a string that contains it."
            )
    return ""


def digest(ignored: list[Ignored]) -> str:
    """Eight hex characters that change whenever an ignored entry does.

    Covers each entry's position, what is wrong with it and the entry as
    written, so an edit that "fixes" an entry into another bad value is a new
    signature. sha256 over sorted-key JSON, so it is stable across processes.
    """
    blob = json.dumps(
        [[ig.index, ig.field, ig.entry] for ig in ignored], sort_keys=True, default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


def load_list(raw: str, ref: str, *, noun: str = "entry") -> list[Any] | ValueProblem:
    """Parse ``raw`` as a JSON list, or say why it is not one.

    Never raises. ``ref`` names the setting in the problem's words, e.g.
    ``app_settings.scheduled_workflows``, and ``noun`` is what one entry is
    (``workflow``, ``feed``). The caller decides what an empty value means
    before calling.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        where = f"line {exc.lineno}, column {exc.colno}"
        return ValueProblem(
            # The position is in the identity, so an edit that moves the error
            # (fixing one mistake to reveal the next) is news.
            kind=f"invalid-json:char-{exc.pos}",
            summary=f"{ref} is not valid JSON ({exc.msg} at {where})",
            problem=(
                f"{ref} is not valid JSON: {exc.msg} at {where}, near "
                f"`{excerpt(raw, exc.pos)}`."
            ),
        )
    except (ValueError, RecursionError) as exc:
        # Valid syntax that json.loads still refuses: an integer past Python's
        # 4,300-digit limit, or nesting deeper than the recursion limit.
        why = clip(str(exc) or type(exc).__name__, 160)
        return ValueProblem(
            kind=f"invalid-json:{type(exc).__name__}",
            summary=f"{ref} is not valid JSON ({why})",
            problem=f"{ref} cannot be read as JSON: {why}.",
        )
    if not isinstance(parsed, list):
        kind = json_type(parsed)
        return ValueProblem(
            kind=f"not-a-list:{kind}",
            summary=f"{ref} holds a JSON {kind}, not a list",
            problem=f"{ref} holds a JSON {kind}, not a list.",
            hint=not_a_list_hint(parsed, noun),
        )
    return parsed
