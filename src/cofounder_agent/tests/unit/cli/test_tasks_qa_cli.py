"""``poindexter tasks qa``: one task's QA results, readable at a glance."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from poindexter.cli._status_style import ACTIVE, FAILURE, INACTIVE, SUCCESS
from poindexter.cli.tasks import format_qa_results, tasks_group

_QA = {
    "approved": False,
    "final_score": 68.8,
    "approval_threshold": 80,
    "reviews": [
        {"reviewer": "citation_verifier", "score": 0, "approved": False, "advisory": False,
         "feedback_preview": "100% of citations dead"},
        {"reviewer": "self_claim", "score": 100, "approved": True, "advisory": False,
         "feedback_preview": "verified"},
        {"reviewer": "person_mention", "score": 75, "approved": True, "advisory": True,
         "feedback_preview": "[advisory] (failed, not required_to_pass) names Matt"},
        {"reviewer": "deepeval_g_eval", "score": 100, "approved": True, "advisory": True,
         "feedback_preview": "[advisory] (passed, not required_to_pass) on topic"},
        {"reviewer": "citation_grounding", "score": 64, "approved": False, "advisory": True,
         "feedback_preview": "6 named sources with no corpus match"},
        {"reviewer": "web_factcheck", "score": 0, "approved": True, "advisory": False,
         "not_applicable": True, "feedback_preview": "no claims to verify"},
        {"reviewer": "freshness", "score": 0, "approved": True, "advisory": False,
         "feedback_preview": "Evergreen draft"},
    ],
}


def test_verdict_line_names_score_threshold_and_counts():
    head, colour = format_qa_results(_QA)[0]
    assert head == "QA REJECTED  score 68.8 (needs 80)   4 blocking check(s), 3 advisory"
    assert colour == FAILURE
    passed, colour = format_qa_results({**_QA, "approved": True})[0]
    assert passed.startswith("QA PASSED") and colour == SUCCESS


def test_rows_mark_each_check_and_drop_the_advisory_prefix():
    rows = dict(format_qa_results(_QA))
    lines = list(rows)
    assert lines.index("  Can block the post") < lines.index("  Advisory")
    by_reviewer = {line.split()[1]: (line, colour) for line, colour in rows.items() if line.startswith("    ")}
    assert by_reviewer["citation_verifier"][0].split()[0] == "FAIL"
    assert by_reviewer["citation_verifier"][1] == FAILURE
    assert by_reviewer["self_claim"][1] == SUCCESS
    # A failed advisory check is recorded approved; it shows as a warning.
    line, colour = by_reviewer["person_mention"]
    assert line.split()[0] == "warn" and colour == ACTIVE
    assert line.endswith("names Matt")
    line, colour = by_reviewer["deepeval_g_eval"]
    assert line.split()[0] == "pass" and colour == INACTIVE
    assert "[advisory]" not in line
    # An advisory failure recorded unapproved is a warning too: it cannot block.
    line, colour = by_reviewer["citation_grounding"]
    assert line.split()[0] == "warn" and colour == ACTIVE


def test_a_check_with_nothing_to_judge_reads_n_a_not_zero():
    rows = {line.split()[1]: (line, colour) for line, colour in format_qa_results(_QA) if line.startswith("    ")}
    for reviewer in ("web_factcheck", "freshness"):  # flagged, and an older row without the flag
        line, colour = rows[reviewer]
        assert line.split()[:3] == ["n/a", reviewer, "-"], line
        assert colour == INACTIVE
    # A failed check that scored 0 is still a 0.
    assert rows["citation_verifier"][0].split()[:3] == ["FAIL", "citation_verifier", "0"]


def test_a_group_with_no_checks_is_left_out():
    rows = [line for line, _ in format_qa_results({**_QA, "reviews": _QA["reviews"][:2]})]
    assert "  Advisory" not in rows


def _client(result):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = MagicMock()
    client.json_or_raise.return_value = result
    return client


def test_command_reads_the_qa_route():
    client = _client(_QA)
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
        result = CliRunner().invoke(tasks_group, ["qa", "e79f20eb"])
    assert result.exit_code == 0, result.output
    assert client.get.call_args.args[0] == "/api/tasks/e79f20eb/qa"
    assert "QA REJECTED" in result.output and "citation_verifier" in result.output


def test_command_json_and_error_exit():
    client = _client(_QA)
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
        result = CliRunner().invoke(tasks_group, ["qa", "e79f20eb", "--json"])
    assert result.exit_code == 0 and '"final_score": 68.8' in result.output
    client.json_or_raise.side_effect = RuntimeError("404: Task e79f20eb has no QA results yet")
    with patch("poindexter.cli.tasks.WorkerClient", return_value=client):
        result = CliRunner().invoke(tasks_group, ["qa", "e79f20eb"])
    assert result.exit_code == 1 and "no QA results yet" in result.output
