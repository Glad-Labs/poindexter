"""Tests for utils.json_extract.extract_json_object — the canonical tolerant
JSON-object ladder (direct parse → fenced block → first-brace block).

The delegating callers (services.title_generation._extract_json and the SEO /
QA atoms) carry their own behavioral suites; this pins the shared ladder.
"""

from __future__ import annotations

from utils.json_extract import extract_json_object


def test_direct_object():
    assert extract_json_object('{"title": "X"}') == {"title": "X"}


def test_fenced_object():
    raw = 'Sure!\n```json\n{"title": "X"}\n```\nDone.'
    assert extract_json_object(raw) == {"title": "X"}


def test_bare_fence_without_language_tag():
    raw = '```\n{"a": 1}\n```'
    assert extract_json_object(raw) == {"a": 1}


def test_object_embedded_in_prose():
    raw = 'Here is my answer: {"title": "X", "n": 2} — hope that helps.'
    assert extract_json_object(raw) == {"title": "X", "n": 2}


def test_reasoning_preamble_discarded():
    raw = 'Let me think about this step by step...\n\n{"verdict": true}'
    assert extract_json_object(raw) == {"verdict": True}


def test_no_object_returns_none():
    assert extract_json_object("no json here") is None


def test_empty_and_none_return_none():
    assert extract_json_object("") is None
    assert extract_json_object(None) is None  # type: ignore[arg-type]


def test_top_level_array_is_not_an_object():
    assert extract_json_object("[1, 2, 3]") is None


def test_malformed_object_returns_none():
    assert extract_json_object('{"title": unquoted}') is None
