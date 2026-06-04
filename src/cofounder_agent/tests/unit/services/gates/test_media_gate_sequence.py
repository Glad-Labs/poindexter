"""Unit tests for ``media_gate_sequence`` (Glad-Labs/poindexter#24, #338).

Pure function — maps a post's ``media_to_generate`` array to the ordered
gate-name sequence the per-medium gate engine creates on approval. No DB.
This is the smallest, most-testable unit of the media-gated-publish wiring;
everything else (gate creation on approval, the driver) depends on it.
"""

import pytest

from services.gates.post_approval_gates import (
    CANONICAL_GATE_NAMES,
    MEDIUM_GATE_NAMES,
    GateCascadeRequiredError,
    GateNotFoundError,
    GateServiceError,
    GateStateError,
    WorkflowAdvance,
    _row_to_dict,
    media_gate_sequence,
)


@pytest.mark.parametrize("media,expected", [
    (["podcast", "video"], ["podcast", "video", "final"]),
    (["video", "podcast", "short"], ["podcast", "video", "short", "final"]),  # canonical order
    (["podcast"], ["podcast", "final"]),
    ([], ["final"]),                       # text-only still gets a final gate (D2 fast-path in driver)
    (["video", "bogus"], ["video", "final"]),  # unknown media dropped
])
def test_media_gate_sequence(media, expected):
    assert media_gate_sequence(media) == expected


# ---------------------------------------------------------------------------
# media_gate_sequence — additional edge cases
# ---------------------------------------------------------------------------

def test_media_gate_sequence_none_input():
    # None is handled via the `or []` guard — same result as empty list.
    assert media_gate_sequence(None) == ["final"]  # type: ignore[arg-type]


def test_media_gate_sequence_all_three():
    assert media_gate_sequence(["short", "video", "podcast"]) == [
        "podcast", "video", "short", "final"
    ]


def test_media_gate_sequence_all_unknown():
    # Every element unknown → only the mandatory final gate survives.
    assert media_gate_sequence(["reel", "audio", "blog"]) == ["final"]


def test_media_gate_sequence_duplicates_deduped():
    # Duplicate input entries collapse via the set; canonical order preserved.
    assert media_gate_sequence(["podcast", "podcast", "video"]) == [
        "podcast", "video", "final"
    ]


def test_media_gate_sequence_case_sensitive():
    # Gate names are lowercase — mixed-case inputs are treated as unknowns.
    assert media_gate_sequence(["Podcast", "VIDEO"]) == ["final"]


# ---------------------------------------------------------------------------
# Constants — sanity
# ---------------------------------------------------------------------------

def test_medium_gate_names_subset_of_canonical():
    for name in MEDIUM_GATE_NAMES:
        assert name in CANONICAL_GATE_NAMES


def test_final_in_canonical_gate_names():
    assert "final" in CANONICAL_GATE_NAMES


# ---------------------------------------------------------------------------
# _row_to_dict — the helper all DB results flow through
# ---------------------------------------------------------------------------

def test_row_to_dict_none_returns_empty():
    assert _row_to_dict(None) == {}


def test_row_to_dict_parses_json_string_metadata():
    row = {"metadata": '{"key": "value"}', "id": "abc"}
    result = _row_to_dict(row)
    assert result["metadata"] == {"key": "value"}


def test_row_to_dict_bad_json_metadata_falls_back_to_empty():
    row = {"metadata": "{not valid json"}
    result = _row_to_dict(row)
    assert result["metadata"] == {}


def test_row_to_dict_none_metadata_normalized():
    row = {"metadata": None}
    result = _row_to_dict(row)
    assert result["metadata"] == {}


def test_row_to_dict_stringifies_integer_ids():
    row = {"id": 42, "post_id": 99, "metadata": {}}
    result = _row_to_dict(row)
    assert result["id"] == "42"
    assert result["post_id"] == "99"


def test_row_to_dict_preserves_existing_dict_metadata():
    row = {"metadata": {"nested": True}, "id": "x"}
    result = _row_to_dict(row)
    assert result["metadata"] == {"nested": True}


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

def test_gate_service_error_is_exception():
    assert issubclass(GateServiceError, Exception)


def test_gate_subclasses_inherit_from_base():
    assert issubclass(GateNotFoundError, GateServiceError)
    assert issubclass(GateStateError, GateServiceError)
    assert issubclass(GateCascadeRequiredError, GateServiceError)


def test_gate_errors_are_catchable_as_base():
    for exc_cls in (GateNotFoundError, GateStateError, GateCascadeRequiredError):
        with pytest.raises(GateServiceError):
            raise exc_cls("test")


# ---------------------------------------------------------------------------
# WorkflowAdvance dataclass
# ---------------------------------------------------------------------------

def test_workflow_advance_defaults():
    wa = WorkflowAdvance()
    assert wa.next_gate is None
    assert wa.ready_to_distribute is False
    assert wa.finished is False
    assert wa.reason == ""


def test_workflow_advance_is_frozen():
    wa = WorkflowAdvance(finished=True, reason="post_rejected")
    with pytest.raises((AttributeError, TypeError)):
        wa.finished = False  # type: ignore[misc]
