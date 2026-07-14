"""Unit tests for the pure content-type classify helpers (no I/O)."""
from __future__ import annotations

from services.jobs._content_type_classify import parse_labels_csv, validate_labels


class TestParseLabelsCsv:
    def test_strips_lowercases_dedupes_order_preserving(self):
        assert parse_labels_csv(" ai-ml, Gaming ,ai-ml, ") == ["ai-ml", "gaming"]

    def test_empty_is_empty_list(self):
        assert parse_labels_csv("") == []
        assert parse_labels_csv("   ") == []

    def test_none_safe(self):
        assert parse_labels_csv(None) == []  # type: ignore[arg-type]


class TestValidateLabels:
    ALLOWED = ["ai-ml", "pc-hardware", "gaming"]

    def test_keeps_allowed_drops_unknown_and_dupes_clamps_confidence(self):
        raw = (
            '{"labels": ['
            '{"label":"ai-ml","confidence":0.9},'
            '{"label":"pc-hardware","confidence":1.4},'  # clamps to 1.0
            '{"label":"crypto","confidence":0.8},'        # not allowed → dropped
            '{"label":"ai-ml","confidence":0.5}'          # dup → dropped
            ']}'
        )
        assert validate_labels(raw, self.ALLOWED) == [("ai-ml", 0.9), ("pc-hardware", 1.0)]

    def test_bare_array_form(self):
        assert validate_labels('["ai-ml", "nope"]', self.ALLOWED) == [("ai-ml", 1.0)]

    def test_case_insensitive_label_match(self):
        assert validate_labels('[{"label":"AI-ML"}]', self.ALLOWED) == [("ai-ml", 1.0)]

    def test_non_numeric_confidence_defaults_to_one(self):
        assert validate_labels('[{"label":"ai-ml","confidence":"high"}]', self.ALLOWED) == [
            ("ai-ml", 1.0)
        ]

    def test_negative_confidence_clamped_to_zero(self):
        assert validate_labels('[{"label":"ai-ml","confidence":-2}]', self.ALLOWED) == [
            ("ai-ml", 0.0)
        ]

    def test_malformed_json_returns_empty(self):
        assert validate_labels("not json", self.ALLOWED) == []

    def test_empty_labels_returns_empty(self):
        assert validate_labels('{"labels": []}', self.ALLOWED) == []

    def test_wrong_shape_returns_empty(self):
        assert validate_labels('{"labels": "ai-ml"}', self.ALLOWED) == []

    # --- real-world model output: reasoning preamble + markdown fence ----------
    def test_recovers_from_reasoning_preamble_and_markdown_fence(self):
        """gemma-4-31B (the structured-extraction model) emits a reasoning
        preamble, then a ```json fence, then a bare object. A bare json.loads of
        the whole string fails -> [] for every post; the classifier must recover
        the object. This is the exact shape that silently yielded zero labels
        across 146 published posts in prod."""
        raw = (
            "Goal: label this technical post.\n"
            "- software-engineering: definitely, it's about API design.\n"
            "- ai-ml: yes, discusses model routing.\n\n"
            "```json\n"
            '{"labels": [{"label": "ai-ml", "confidence": 1.0}, '
            '{"label": "gaming", "confidence": 0.9}]}\n'
            "```"
        )
        assert validate_labels(raw, self.ALLOWED) == [("ai-ml", 1.0), ("gaming", 0.9)]

    def test_recovers_bare_object_after_prose(self):
        raw = 'Here is my classification:\n{"labels": [{"label": "pc-hardware"}]}'
        assert validate_labels(raw, self.ALLOWED) == [("pc-hardware", 1.0)]

    def test_recovers_fenced_bare_array(self):
        assert validate_labels('```json\n["ai-ml", "nope"]\n```', self.ALLOWED) == [
            ("ai-ml", 1.0)
        ]

    def test_prose_without_any_json_still_empty(self):
        # No recoverable object/array -> still [] (no silent default label).
        assert validate_labels("I think this is about software engineering.",
                               self.ALLOWED) == []
