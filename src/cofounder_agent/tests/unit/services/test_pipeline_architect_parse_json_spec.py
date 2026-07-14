"""Tests for ``pipeline_architect._parse_json_spec``'s fence handling.

Characterizes the fence-stripping contract before poindexter#643's dedup
(swapping the inline strip block for the canonical
``llm_text.strip_markdown_fence``), so the swap is provably
behavior-preserving — including the one case where the two
implementations genuinely diverge (a fence tagged with something other
than json/jsonc/json5): the canonical helper won't strip that opening
line, but ``_parse_json_spec``'s own outermost-``{...}`` scan finds the
JSON object regardless, so the end-to-end result is unchanged either way.
"""

from __future__ import annotations

from services import pipeline_architect


def test_parse_json_spec_strips_json_tagged_fence() -> None:
    raw = '```json\n{"nodes": []}\n```'
    spec, errors = pipeline_architect._parse_json_spec(raw)
    assert spec == {"nodes": []}
    assert errors == []


def test_parse_json_spec_strips_untagged_fence() -> None:
    raw = '```\n{"nodes": []}\n```'
    spec, errors = pipeline_architect._parse_json_spec(raw)
    assert spec == {"nodes": []}
    assert errors == []


def test_parse_json_spec_survives_wrongly_tagged_fence() -> None:
    """A fence tagged e.g. ```python instead of ```json — the canonical
    helper rejects stripping it, but the outermost-brace scan finds the
    JSON object anyway, so the end result is identical."""
    raw = '```python\n{"nodes": []}\n```'
    spec, errors = pipeline_architect._parse_json_spec(raw)
    assert spec == {"nodes": []}
    assert errors == []


def test_parse_json_spec_handles_prose_around_json() -> None:
    raw = 'Here is the spec:\n{"nodes": []}\nHope that helps!'
    spec, errors = pipeline_architect._parse_json_spec(raw)
    assert spec == {"nodes": []}
    assert errors == []


def test_parse_json_spec_no_fence_still_works() -> None:
    raw = '{"nodes": []}'
    spec, errors = pipeline_architect._parse_json_spec(raw)
    assert spec == {"nodes": []}
    assert errors == []
