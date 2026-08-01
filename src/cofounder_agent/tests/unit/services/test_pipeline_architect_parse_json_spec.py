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


# ── Reasoning-model output (glm-4.7 / qwen3 <think> leak) ────────────────
# First live chat plan turn (2026-08-01): glm-4.7's think prose contained a
# stray '{', anchoring the outermost-{...} scan on garbage → `invalid JSON
# … char 1`. compose() now requests think=False; these pin the parse-side
# defense in depth.


def test_parse_json_spec_strips_think_block_with_braces() -> None:
    raw = (
        "<think>The user wants {skip video}. I'll sketch it first.</think>\n"
        '{"name": "lean post", "nodes": []}'
    )
    spec, errors = pipeline_architect._parse_json_spec(raw)
    assert spec == {"name": "lean post", "nodes": []}
    assert errors == []


def test_parse_json_spec_unclosed_think_falls_back_to_balanced_scan() -> None:
    """An unclosed <think> can't be regex-stripped; the poisoned outermost
    slice fails to parse and the balanced-candidate fallback recovers."""
    raw = (
        "<think>plan {verify, write, qa\n"
        '{"name": "lean post", "nodes": [{"id": "verify_task"}]}'
    )
    spec, errors = pipeline_architect._parse_json_spec(raw)
    assert spec == {"name": "lean post", "nodes": [{"id": "verify_task"}]}
    assert errors == []


def test_parse_json_spec_prefers_largest_candidate_over_sketch() -> None:
    """A small VALID fragment sketched in prose must not beat the real
    spec — largest-parsing-candidate wins."""
    raw = (
        'First I considered {"id": "verify_task"} alone, but no —\n'
        '{"name": "real spec", "nodes": [{"id": "verify_task"},'
        ' {"id": "draft"}]} trailing }'
    )
    spec, errors = pipeline_architect._parse_json_spec(raw)
    assert spec["name"] == "real spec"
    assert len(spec["nodes"]) == 2
    assert errors == []


def test_parse_json_spec_braces_inside_strings_stay_balanced() -> None:
    raw = 'x{ oops\n{"name": "a {tricky} one", "nodes": []} done'
    spec, errors = pipeline_architect._parse_json_spec(raw)
    assert spec == {"name": "a {tricky} one", "nodes": []}
    assert errors == []


def test_parse_json_spec_all_garbage_reports_invalid_json() -> None:
    spec, errors = pipeline_architect._parse_json_spec("{not json at all}")
    assert spec == {}
    assert errors and errors[0].startswith("invalid JSON")
