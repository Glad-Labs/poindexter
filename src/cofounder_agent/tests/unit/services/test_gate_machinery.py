"""Unit tests for the shared gate machinery (Glad-Labs/poindexter#622)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.gate_machinery import (
    GateServiceError,
    coerce_artifact,
    ensure_gate_match,
    iso_or_none,
    resolve_reject_status,
)

pytestmark = pytest.mark.unit


class _NotPaused(GateServiceError):
    pass


class _Mismatch(GateServiceError):
    pass


class TestCoerceArtifact:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, {}),
            ({"a": 1}, {"a": 1}),
            ('{"a": 1}', {"a": 1}),
            ("[1, 2]", {"raw": [1, 2]}),
            ("not json", {"raw": "not json"}),
            (42, {"raw": "42"}),
        ],
    )
    def test_cases(self, raw, expected):
        assert coerce_artifact(raw) == expected


class TestEnsureGateMatch:
    def _row(self, gate):
        return {"awaiting_gate": gate, "status": "x"}

    def test_returns_active_gate_when_no_assertion(self):
        out = ensure_gate_match(
            self._row("g"), None, entity_label="Task", entity_id="t1",
            not_paused_exc=_NotPaused, mismatch_exc=_Mismatch, verb="approve",
        )
        assert out == "g"

    def test_not_paused_raises(self):
        with pytest.raises(_NotPaused):
            ensure_gate_match(
                self._row(None), None, entity_label="Task", entity_id="t1",
                not_paused_exc=_NotPaused, mismatch_exc=_Mismatch, verb="approve",
            )

    def test_mismatch_raises_with_verb_in_message(self):
        with pytest.raises(_Mismatch, match="reject the wrong gate"):
            ensure_gate_match(
                self._row("g"), "other", entity_label="Post", entity_id="p1",
                not_paused_exc=_NotPaused, mismatch_exc=_Mismatch, verb="reject",
            )

    def test_matching_assertion_passes(self):
        out = ensure_gate_match(
            self._row("g"), "g", entity_label="Task", entity_id="t1",
            not_paused_exc=_NotPaused, mismatch_exc=_Mismatch, verb="approve",
        )
        assert out == "g"


class TestResolveRejectStatus:
    def test_default_when_no_config(self):
        assert resolve_reject_status(None, "g", "rejected") == "rejected"

    def test_override_applied(self):
        class _SC:
            def get(self, key, default=""):
                return "dismissed"

        assert resolve_reject_status(_SC(), "g", "rejected") == "dismissed"

    def test_blank_override_falls_back(self):
        class _SC:
            def get(self, key, default=""):
                return ""

        assert resolve_reject_status(_SC(), "g", "rejected") == "rejected"


class TestIsoOrNone:
    def test_datetime_isoformatted(self):
        dt = datetime(2026, 6, 2, tzinfo=timezone.utc)
        assert iso_or_none(dt) == dt.isoformat()

    def test_none_passthrough(self):
        assert iso_or_none(None) is None

    def test_plain_value_passthrough(self):
        assert iso_or_none("2026-06-02") == "2026-06-02"


class TestGateCatalog:
    def test_catalog_has_the_five_known_gates(self):
        from services.gate_machinery import GATE_CATALOG

        names = {g.name for g in GATE_CATALOG}
        assert names == {
            "draft_gate",
            "preview_gate",
            "seo_refresh_gate",
            "topic_decision",
            "final_publish_approval",
        }

    def test_mechanism_and_wiring_are_accurate(self):
        from services.gate_machinery import GATE_CATALOG_BY_NAME

        fpa = GATE_CATALOG_BY_NAME["final_publish_approval"]
        assert fpa.mechanism == "imperative-hold"
        assert fpa.wired_into == "scheduled_publisher"
        assert fpa.default_enabled is False

        td = GATE_CATALOG_BY_NAME["topic_decision"]
        assert td.mechanism == "imperative-hold"

        draft = GATE_CATALOG_BY_NAME["draft_gate"]
        assert draft.mechanism == "graph-node"
        assert draft.wired_into == "canonical_blog"

        seo = GATE_CATALOG_BY_NAME["seo_refresh_gate"]
        assert seo.default_enabled is True
