"""Tests for the ragas_score audit_log emission path (Grafana QA-rails
Ragas panel input).

Separate from ``test_ragas_eval.py`` because that suite is
``pytest.skip(allow_module_level=True)`` on Windows due to the pyarrow
access-violation when ``from datasets import Dataset`` runs at import
time. ``_emit_ragas_score_audit`` doesn't touch datasets/pyarrow at all
— it only formats a dict and hands it to ``audit_log_bg`` — so this
file can run on every platform and exercises the contract the Grafana
panel queries depend on (``event_type='ragas_score'``,
``details->>'score'`` as float, three component keys).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.ragas_eval import _emit_ragas_score_audit


@pytest.mark.unit
class TestEmitRagasScoreAudit:
    def test_writes_averaged_score_and_components(self):
        with patch("services.audit_log.audit_log_bg") as mock_bg:
            _emit_ragas_score_audit(
                {"faithfulness": 0.8, "answer_relevancy": 0.9, "context_precision": 0.7},
                topic="Bootstrapping a SaaS",
                task_id="task-xyz",
            )

        # Exactly one audit call, with the contract the Grafana panel
        # queries depend on.
        assert mock_bg.call_count == 1
        args, kwargs = mock_bg.call_args
        assert args[0] == "ragas_score"
        assert args[1] == "ragas_eval"
        details = args[2]
        assert details["score"] == pytest.approx((0.8 + 0.9 + 0.7) / 3, abs=1e-4)
        assert details["faithfulness"] == pytest.approx(0.8)
        assert details["answer_relevancy"] == pytest.approx(0.9)
        assert details["context_precision"] == pytest.approx(0.7)
        assert details["topic"] == "Bootstrapping a SaaS"
        assert details["metric_count"] == 3
        assert kwargs["task_id"] == "task-xyz"
        assert kwargs["severity"] == "info"

    def test_drops_minus_one_sentinels_before_averaging(self):
        """A single -1.0 sentinel (one metric judge-LLM hiccupped) must
        NOT drag the aggregate to 0 — the panel's trend line would
        plummet on a transient failure that's already surfaced through
        the rail's own warning log."""
        with patch("services.audit_log.audit_log_bg") as mock_bg:
            _emit_ragas_score_audit(
                {"faithfulness": 0.9, "answer_relevancy": -1.0, "context_precision": 0.7},
                topic="t",
                task_id=None,
            )

        assert mock_bg.call_count == 1
        details = mock_bg.call_args[0][2]
        assert details["score"] == pytest.approx((0.9 + 0.7) / 2, abs=1e-4)
        assert details["metric_count"] == 2
        # Component fields still record the raw sentinel — downstream
        # can detect "this metric was unreliable" from the JSON.
        assert details["answer_relevancy"] == pytest.approx(-1.0)

    def test_full_failure_skips_audit_write(self):
        """If ALL three metrics returned -1.0, Ragas failed entirely.
        The rail's evaluate_sample already logged a warning — emitting
        a ``score=0`` row here would pollute the Grafana trend line
        with a hard-zero on each transient outage."""
        with patch("services.audit_log.audit_log_bg") as mock_bg:
            _emit_ragas_score_audit(
                {"faithfulness": -1.0, "answer_relevancy": -1.0, "context_precision": -1.0},
                topic="t",
                task_id="task-1",
            )
        mock_bg.assert_not_called()

    def test_audit_write_failure_does_not_raise(self):
        """audit_log_bg failing (DB hiccup, pool not initialised yet,
        whatever) must never propagate to the Ragas caller — Ragas is
        best-effort telemetry, the chain shouldn't crash on a
        downstream write error."""
        with patch(
            "services.audit_log.audit_log_bg",
            side_effect=RuntimeError("pool not initialised"),
        ):
            # Just shouldn't raise.
            _emit_ragas_score_audit(
                {"faithfulness": 0.5, "answer_relevancy": 0.5, "context_precision": 0.5},
                topic="t",
                task_id=None,
            )

    def test_topic_truncated_at_200_chars(self):
        """``details->>'topic'`` is a hint string for the dashboard
        latest-rows panel, not a primary key — cap it so a
        pathologically long topic doesn't bloat the JSONB column."""
        long_topic = "x" * 500
        with patch("services.audit_log.audit_log_bg") as mock_bg:
            _emit_ragas_score_audit(
                {"faithfulness": 0.6, "answer_relevancy": 0.6, "context_precision": 0.6},
                topic=long_topic,
                task_id=None,
            )

        details = mock_bg.call_args[0][2]
        assert len(details["topic"]) == 200

    def test_score_rounded_to_4dp(self):
        """The Grafana time-series panel queries ``score`` as a float;
        rounding here keeps the JSONB payload compact and avoids
        spurious precision in the dashboard tooltips."""
        with patch("services.audit_log.audit_log_bg") as mock_bg:
            _emit_ragas_score_audit(
                {
                    "faithfulness": 1 / 3,
                    "answer_relevancy": 1 / 3,
                    "context_precision": 1 / 3,
                },
                topic="t",
                task_id=None,
            )
        details = mock_bg.call_args[0][2]
        # 1/3 averaged = 1/3; rounding to 4dp = 0.3333
        assert details["score"] == pytest.approx(0.3333, abs=1e-4)
