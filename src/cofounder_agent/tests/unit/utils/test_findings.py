"""Unit tests for utils/findings.py::emit_finding.

Covers the poindexter#758 event-shape validation wiring: emit_finding's
details dict is validated against services.audit_event_schemas.FindingDetails
before being handed to audit_log_bg.
"""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


class TestEmitFindingShapeValidation:
    def test_details_are_schema_version_stamped(self):
        from utils.findings import emit_finding

        with patch("utils.findings.audit_log_bg") as mock_write:
            emit_finding(source="test_job", kind="broken_link", title="t", body="b")

        _, kwargs = mock_write.call_args
        assert kwargs["details"]["schema_version"] == 1
        assert kwargs["details"]["kind"] == "broken_link"

    def test_dedup_key_and_extra_survive_validation(self):
        from utils.findings import emit_finding

        with patch("utils.findings.audit_log_bg") as mock_write:
            emit_finding(
                source="test_job",
                kind="broken_link",
                title="t",
                body="b",
                dedup_key="broken-link:https://example.com",
                extra={"urls": ["https://example.com"]},
            )

        _, kwargs = mock_write.call_args
        assert kwargs["details"]["dedup_key"] == "broken-link:https://example.com"
        assert kwargs["details"]["extra"] == {"urls": ["https://example.com"]}

    def test_event_type_and_severity_unaffected(self):
        from utils.findings import emit_finding

        with patch("utils.findings.audit_log_bg") as mock_write:
            emit_finding(source="test_job", kind="k", title="t", body="b", severity="warn")

        _, kwargs = mock_write.call_args
        assert kwargs["event_type"] == "finding"
        assert kwargs["source"] == "test_job"
        assert kwargs["severity"] == "warn"

    def test_severity_warning_normalized_to_warn(self):
        """"warning" drifted in at several call sites (copy-pasted from
        Prometheus/Alertmanager conventions); emit_finding canonicalizes it to
        "warn" so the by-severity rollup doesn't fragment into two badges."""
        from utils.findings import emit_finding

        with patch("utils.findings.audit_log_bg") as mock_write:
            emit_finding(source="test_job", kind="k", title="t", body="b", severity="warning")

        _, kwargs = mock_write.call_args
        assert kwargs["severity"] == "warn"

    def test_severity_normalized_case_insensitively(self):
        from utils.findings import emit_finding

        with patch("utils.findings.audit_log_bg") as mock_write:
            emit_finding(source="test_job", kind="k", title="t", body="b", severity="WARNING")

        _, kwargs = mock_write.call_args
        assert kwargs["severity"] == "warn"
