"""Regression tests for services/rag_engine.py metadata coercion (#554).

The hybrid retriever's lexical-rehydration path called ``dict()`` directly
on an embeddings-row ``metadata`` value. When asyncpg returns that JSONB
column as a raw JSON string (the default — no type codec registered),
``dict()`` iterates the string's characters and raises ``ValueError:
dictionary update sequence element #0 has length 1; 2 is required`` — which
silently forced EVERY rag_engine query onto the legacy pgvector fallback
for ~3 weeks (the ``rag_engine_fallback`` audit rows). ``_coerce_metadata``
normalizes both the dict and JSON-string forms.

These tests import only the pure helper, so they run in CI WITHOUT the
opt-in LlamaIndex dep (the rest of the retriever does need it, which is
why the other rag_engine test file ``importorskip``s and is skipped in CI).
"""

from __future__ import annotations

import pytest

from services.rag_engine import _coerce_metadata


def test_json_string_does_not_raise_and_decodes():
    # The exact prod crash case: a JSONB column returned as a string.
    raw = '{"source": "blog", "n": 2}'
    # Sanity: the OLD code path — dict(raw) on a string — DID raise.
    with pytest.raises(ValueError):
        dict(raw)
    # The helper decodes it instead of crashing.
    assert _coerce_metadata(raw) == {"source": "blog", "n": 2}


def test_already_dict_is_copied():
    d = {"a": 1}
    out = _coerce_metadata(d)
    assert out == {"a": 1}
    # Defensive copy — the caller mutates it via .update(); must not touch
    # the original row dict.
    assert out is not d


def test_none_and_empty_string_become_empty_dict():
    assert _coerce_metadata(None) == {}
    assert _coerce_metadata("") == {}
    assert _coerce_metadata("   ") == {}


def test_malformed_json_becomes_empty_dict():
    assert _coerce_metadata("not json at all") == {}
    assert _coerce_metadata("{unterminated") == {}


def test_non_dict_json_becomes_empty_dict():
    # A JSON array decodes to a list — not valid metadata, and dict() of
    # 1-length items is the very crash we're guarding against.
    assert _coerce_metadata("[1, 2, 3]") == {}
    assert _coerce_metadata('["a", "b"]') == {}


def test_unexpected_type_becomes_empty_dict():
    assert _coerce_metadata(12345) == {}
    assert _coerce_metadata(["a", "b"]) == {}
