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


# --- truncated-object salvage (opt-in rung, Glad-Labs/poindexter#926) --------
#
# Shapes below are taken from real Langfuse traces of the topic-ranking call:
# the model emits valid JSON, then derails into a degenerate repetition loop
# on a key name ("BRAND own own own …") or into prose, and never closes the
# object.

def test_salvage_is_off_by_default():
    """A partial object must stay None unless the caller opts in — a QA rail
    reading a half-parsed verdict is the fail-open shape we forbid."""
    raw = '{"a": {"score": 1}, "b": {"score":'
    assert extract_json_object(raw) is None


def test_salvage_recovers_complete_entries():
    raw = '{"a": {"score": 1}, "b": {"score": 2}, "c": {"sco'
    assert extract_json_object(raw, salvage_truncated=True) == {
        "a": {"score": 1}, "b": {"score": 2},
    }


def test_salvage_recovers_flat_score_shape():
    raw = '{"id-1": 72, "id-2": 64, "id-3": '
    assert extract_json_object(raw, salvage_truncated=True) == {
        "id-1": 72, "id-2": 64,
    }


def test_salvage_on_repetition_loop_inside_a_key():
    """The observed production failure: a loop inside a breakdown key name."""
    raw = (
        '{\n  "cand-1": {"score": 45, "breakdown": {"AUTHORITY": 0.1}},\n'
        '  "cand-2": {\n    "score": 45,\n    "breakdown": {\n'
        '      "AUTHORITY": 0.20,\n      "EDUCATION own own own own own own'
    )
    assert extract_json_object(raw, salvage_truncated=True) == {
        "cand-1": {"score": 45, "breakdown": {"AUTHORITY": 0.1}},
    }


def test_salvage_returns_none_when_first_entry_never_completes():
    """No depth-1 comma yet — nothing was fully scored, so nothing to keep."""
    raw = '{\n  "cand-1": {\n    "score": 45,\n    "breakdown": {\n      "AUTH'
    assert extract_json_object(raw, salvage_truncated=True) is None


def test_salvage_never_returns_a_partial_entry():
    """Cuts land on entry boundaries: a half-written value is never emitted."""
    raw = '{"a": {"score": 1}, "b": {"score": 99'
    out = extract_json_object(raw, salvage_truncated=True)
    assert out == {"a": {"score": 1}}
    assert "b" not in out


def test_salvage_ignores_braces_and_commas_inside_strings():
    raw = '{"a": "a } string, with punctuation", "b": {"x": 1}, "c": {'
    assert extract_json_object(raw, salvage_truncated=True) == {
        "a": "a } string, with punctuation", "b": {"x": 1},
    }


def test_salvage_handles_escaped_quote_in_string():
    raw = '{"a": "he said \\"hi\\", then left", "b": 2, "c": '
    assert extract_json_object(raw, salvage_truncated=True) == {
        "a": 'he said "hi", then left', "b": 2,
    }


def test_salvage_does_not_alter_a_well_formed_object():
    raw = '{"a": 1, "b": 2}'
    assert extract_json_object(raw, salvage_truncated=True) == {"a": 1, "b": 2}


def test_salvage_prefers_the_closed_object_over_trailing_prose():
    """Model closed the object, then kept talking — keep the object as-is."""
    raw = '{"a": 1, "b": 2}\nLet me re-evaluate the candidates and rescore.'
    assert extract_json_object(raw, salvage_truncated=True) == {"a": 1, "b": 2}


def test_salvage_with_no_opening_brace():
    assert extract_json_object("Let me think about this.", salvage_truncated=True) is None


def test_salvage_on_empty_input():
    assert extract_json_object("", salvage_truncated=True) is None
