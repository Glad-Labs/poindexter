"""No jsonb literal in the baseline seeds is a JSON container wrapped in a string.

poindexter#1061: the Phase G fold-forward dump wrote 58 jsonb values as
``'"{...}"'::jsonb`` — a jsonb STRING holding JSON text — so every fresh
install got strings where every consumer expects mappings. The dump step will
produce the same shape on the next squash unless something checks, so this
reads the seed file itself.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

_SEEDS = (
    pathlib.Path(__file__).resolve().parents[3]
    / "poindexter" / "services" / "migrations" / "0000_baseline.seeds.sql"
)
_JSONB_LITERAL = re.compile(r"(E?)'((?:[^'\\]|''|\\.)*)'::jsonb", re.S)


def _unescape(body: str, is_e: bool) -> str:
    body = body.replace("''", "'")
    if not is_e:
        return body
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), body)


def _literals():
    for m in _JSONB_LITERAL.finditer(_SEEDS.read_text(encoding="utf-8")):
        yield m[0][:120], _unescape(m[2], m[1] == "E")


def test_the_scan_sees_the_jsonb_literals():
    assert sum(1 for _ in _literals()) >= 100, "jsonb literal scan went blind"


def test_no_container_is_seeded_as_a_jsonb_string():
    bad = []
    for snippet, text in _literals():
        try:
            value = json.loads(text)
        except ValueError:
            continue  # not our shape; Postgres would reject it at apply time anyway
        if isinstance(value, str):
            try:
                inner = json.loads(value)
            except ValueError:
                continue  # a jsonb value that is genuinely a string
            if isinstance(inner, (dict, list)):
                bad.append(snippet)
    assert not bad, f"{len(bad)} double-encoded jsonb seed literal(s), e.g. {bad[:3]}"
