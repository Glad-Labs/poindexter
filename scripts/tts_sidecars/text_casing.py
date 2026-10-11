"""Fold mid-sentence name casing for Chatterbox (stdlib-only).

Chatterbox's text vocabulary is a 704-token BPE whose multi-character pieces
are ALL lowercase: a capital letter only exists as a single-character token.
So "deepseek" tokenizes as ``de e p se e k`` while "DeepSeek" becomes
``D e e p S e e k``, one letter at a time. In the training data a lone capital
after a space mostly opened a sentence, and the model pauses there as if one
had started. Measured over five episode openings (2026-10-09), a pause of
0.4s or more landed right before a mid-sentence name on 15.7% of renders
against 0.6% of other unpunctuated word boundaries.

The fold lowercases a Title-case word that does not start a sentence
("score Alibaba" -> "score alibaba") and splits a word built only from
Title-case pieces into its words ("DeepSeek" -> "deep seek"). Same five
openings, two more renders each: the pre-name rate fell to 5.3% while the
other boundaries held at 0.7%, and a whisper round-trip still heard the names.

Left alone on purpose:

* ALL-CAPS tokens and anything with an uppercase run (NVMe, OpenAI, GPU):
  single capitals are how the model spells letters out, which is what an
  acronym needs.
* Tokens with digits (SN770M, RTX).
* The first word of a sentence, and "I" / "I'm" / "I've".
* ``keep_case`` words, whose capital changes the word: "August" the month,
  "august" the adjective. The operator list lives in app_settings.

Kept dependency-free (only ``re``) so it ships in the slim sidecar image and
tests without torch.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

DEFAULT_KEEP_CASE = ("August", "Polish", "Job", "Nice", "Reading", "Mobile")

_TITLE_RE = re.compile(r"^([\"'(]*)([A-Z][a-z]+)((?:'s|')?)([\"'),.;:!?]*)$")
_CAMEL_RE = re.compile(r"^([\"'(]*)((?:[A-Z][a-z]+){2,})((?:'s|')?)([\"'),.;:!?]*)$")
_PIECE_RE = re.compile(r"[A-Z][a-z]+")
_SENTENCE_END_RE = re.compile(r"[.!?][\"')]*$")
# A surname prefix reads as part of one word: "McDonald" is "mcdonald",
# never "mc donald" (which the engine would spell as M C).
_JOINED_PREFIXES = ("Mc", "Mac")


def _fold_token(tok: str, keep: frozenset[str]) -> str:
    m = _CAMEL_RE.match(tok)
    if m:
        lead, word, poss, trail = m.groups()
        if word in keep:
            return tok
        pieces = _PIECE_RE.findall(word)
        spoken = word.lower() if pieces[0] in _JOINED_PREFIXES else " ".join(pieces).lower()
        return f"{lead}{spoken}{poss}{trail}"
    m = _TITLE_RE.match(tok)
    if m:
        lead, word, poss, trail = m.groups()
        if word in keep:
            return tok
        return f"{lead}{word.lower()}{poss}{trail}"
    return tok


def fold_name_case(text: str, keep_case: Iterable[str] = DEFAULT_KEEP_CASE) -> tuple[str, int]:
    """Return ``(text, folded)``: mid-sentence names lowercased, and how many.

    Whitespace, including paragraph breaks, is preserved exactly.
    """
    keep = frozenset(w.strip() for w in keep_case if w and w.strip())
    parts = re.split(r"(\s+)", text or "")
    out: list[str] = []
    sentence_start = True
    folded = 0
    for part in parts:
        if not part or part.isspace():
            if part and "\n" in part:
                sentence_start = True
            out.append(part)
            continue
        if sentence_start or part == "I" or part.startswith(("I'", "I’")):
            new = part
        else:
            new = _fold_token(part, keep)
        folded += new != part
        out.append(new)
        sentence_start = bool(_SENTENCE_END_RE.search(part))
    return "".join(out), folded


__all__ = ["DEFAULT_KEEP_CASE", "fold_name_case"]
