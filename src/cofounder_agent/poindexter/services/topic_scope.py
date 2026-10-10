"""Per-niche topic scope: what a niche covers, and a check that enforces it.

A niche states its subject in ``niches.topic_subject`` (plus optional
``topic_exclusions``). Two things read it:

- :func:`scope_block` renders it for prompts, so the ranking LLM and
  internal_rag's story distiller know what the niche is about.
- :func:`check_scope` asks the structured model, in one batched call per
  chunk, whether each candidate's main subject is in scope. The topic batch
  sweep drops the ones it rejects before ranking (poindexter#1127).

Why an LLM and not an embedding threshold: measured 2026-09-30 on 37 real
titles against an AI + hardware subject, title embeddings did not separate
the two sets ("Market Research vs Industry Research" scored 0.478, above
"Decode Speed Lies: phi4:14b" at 0.477), so any threshold would drop real
on-topic posts and keep off-topic ones. The same titles through one batched
qwen2.5:7b call came back about 33/37 correct in 5.4 s.

The check fails open: an unparseable reply, a model error or an id the
model skipped keeps the candidate, and the caller reports it. A scope check
that silently emptied every batch would stall the niche; one that silently
let everything through would hide that the filter is off. Both are loud.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from poindexter.services.logger_config import get_logger
from poindexter.services.site_config import SiteConfig

logger = get_logger(__name__)

PROMPT_KEY = "topic.scope_check"
# Measured 2026-10-01 on 107 live candidates with qwen2.5:7b: 40 per call
# wrongly dropped 8 of 16 hand-labelled on-topic titles, 15 per call dropped 4.
# Smaller chunks also shrink what one bad reply costs.
DEFAULT_CHUNK_SIZE = 15


@dataclass(frozen=True)
class ScopeItem:
    """One candidate to judge. ``id`` only has to be unique within a call."""

    id: str
    text: str


@dataclass
class ScopeResult:
    in_scope: set[str] = field(default_factory=set)
    out_of_scope: set[str] = field(default_factory=set)
    # Ids the model did not return a usable verdict for. Kept by the caller.
    unjudged: set[str] = field(default_factory=set)
    # Why anything is unjudged: a whole chunk failed (model error, unparseable
    # reply), or a reply left ids without a verdict (names its stray keys).
    errors: list[str] = field(default_factory=list)


def scope_block(niche: Any) -> str:
    """Render the niche's scope for a prompt; empty string when it has none.

    Takes anything with ``topic_subject`` / ``topic_exclusions`` attributes
    so callers can pass a :class:`~poindexter.services.niche_service.Niche`
    without this module importing it.
    """
    subject = (getattr(niche, "topic_subject", None) or "").strip()
    if not subject:
        return ""
    lines = [f"In scope: {subject}"]
    exclusions = [e for e in (getattr(niche, "topic_exclusions", None) or ()) if e]
    if exclusions:
        lines.append(
            "Out of scope, even when related to the subject: "
            + "; ".join(exclusions)
            + "."
        )
    return "\n".join(lines)


# A key that carries more than the id: "[i9] Re-ranker Improvement", "[i9]",
# "i9: Re-ranker Improvement". The leading bracketed or bare token is the id.
_KEY_ID_RE = re.compile(r"^\s*\[?\s*([A-Za-z]+\d+)\s*\]?")


def _key_to_id(key: str, ids: set[str] | None) -> str:
    """The candidate id a reply key names.

    qwen3-vl keyed a whole chunk by its candidate LINES ("[i9] Re-ranker
    Improvement": true) on every sweep from 2026-10-06; read verbatim, none
    of those keys was an id, so all 11 verdicts were discarded and the chunk
    went unjudged with no error. A key that is not an id itself resolves to
    the id it starts with, but only to an id of this chunk.
    """
    key = str(key)
    if ids is None or key in ids:
        return key
    m = _KEY_ID_RE.match(key)
    if m and m.group(1) in ids:
        return m.group(1)
    return key


def _parse_verdicts(raw: str, ids: set[str] | None = None) -> dict[str, bool]:
    """Map id -> in scope. Accepts booleans, "true"/"false" and 1/0.

    A value that is none of those is skipped, which leaves that id unjudged.
    With ``ids``, a key that wraps an id in its candidate line resolves to it
    (:func:`_key_to_id`).
    """
    # Tolerant: a model may fence the object or put a sentence around it.
    from poindexter.utils.json_extract import extract_json_object

    blob = extract_json_object(raw)
    if not isinstance(blob, dict):
        raise ValueError("no JSON object in the scope verdicts")
    verdicts: dict[str, bool] = {}
    for key, value in blob.items():
        item_id = _key_to_id(key, ids)
        if isinstance(value, bool):
            verdicts[item_id] = value
        elif isinstance(value, (int, float)) and value in (0, 1):
            verdicts[item_id] = bool(value)
        elif isinstance(value, str) and value.strip().lower() in ("true", "false"):
            verdicts[item_id] = value.strip().lower() == "true"
    return verdicts


async def check_scope(
    items: list[ScopeItem],
    niche: Any,
    *,
    site_config: SiteConfig,
    model: str | None = None,
) -> ScopeResult:
    """Judge each item against the niche's scope. Never raises.

    Returns an empty result when the niche has no subject or there is
    nothing to judge. Items are sent in chunks of
    ``niche_topic_scope_check_chunk_size`` (default 15).

    The model is ``niche_topic_scope_check_model`` when set, else the
    structured-extraction model. Measured 2026-10-01 on 107 live candidates
    at 15 per call: qwen2.5:7b dropped 4 of 16 on-topic titles, while
    qwen3-vl:30b-a3b-instruct dropped 0 and 1 across two runs and kept all
    14 off-topic titles out.
    """
    result = ScopeResult()
    block = scope_block(niche)
    if not block or not items:
        return result

    # Lazy imports: prompt_manager pulls in PyYAML, and topic_ranking's chat
    # helper pulls in the LLM dispatch stack; neither is needed to build a
    # scope block.
    from poindexter.services.prompt_manager import get_prompt_manager
    from poindexter.services.topic_ranking import _ollama_chat_json

    if model is None:
        model = str(site_config.get("niche_topic_scope_check_model", "") or "").strip()
    if not model:
        from poindexter.services.llm_text import resolve_structured_model

        model = resolve_structured_model(site_config=site_config)

    chunk_size = max(1, site_config.get_int(
        "niche_topic_scope_check_chunk_size", DEFAULT_CHUNK_SIZE,
    ))
    for start in range(0, len(items), chunk_size):
        chunk = items[start:start + chunk_size]
        ids = {item.id for item in chunk}
        cand_block = "\n".join(
            f"[{item.id}] {' '.join(item.text.split())}" for item in chunk
        )
        try:
            prompt = get_prompt_manager().get_prompt(
                PROMPT_KEY, scope_block=block, cand_block=cand_block,
            )
            raw = await _ollama_chat_json(prompt, model=model, site_config=site_config)
            verdicts = _parse_verdicts(raw or "", ids)
        except Exception as exc:  # noqa: BLE001 — fail open, reported by the caller
            logger.warning(
                "[topic_scope] scope check failed for %d candidate(s) (%s: %s); "
                "keeping them", len(chunk), type(exc).__name__, exc,
            )
            result.errors.append(f"{type(exc).__name__}: {exc}")
            result.unjudged |= ids
            continue
        missing = 0
        for item_id in ids:
            verdict = verdicts.get(item_id)
            if verdict is None:
                result.unjudged.add(item_id)
                missing += 1
            elif verdict:
                result.in_scope.add(item_id)
            else:
                result.out_of_scope.add(item_id)
        if missing:
            # Say why, or the caller's finding names a count and nothing else.
            stray = [k for k in verdicts if k not in ids][:3]
            result.errors.append(
                f"no verdict for {missing} of {len(ids)} candidate(s) in a chunk"
                + (f"; the reply used keys that are not candidate ids: {stray}" if stray else "")
            )
    return result


__all__ = [
    "PROMPT_KEY",
    "ScopeItem",
    "ScopeResult",
    "check_scope",
    "scope_block",
]
