"""CLI render tests for `poindexter social list`.

``social_post_drafts`` only grows (one row per platform per post, terminal
rows never pruned), so the listing is capped. A cap that renders as though it
were the whole table is the failure mode these tests guard: the command must
say what it withheld and report totals spanning every row, not the page.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from poindexter.cli.social import list_drafts
from services.social_drafts import SocialDraftPage, SocialDraftRow

pytestmark = pytest.mark.unit


def _row(draft_id: str = "abcdef1234", status: str = "pending") -> SocialDraftRow:
    return SocialDraftRow(
        id=draft_id,
        pipeline_task_id="task-1",
        post_id=None,
        platform="bluesky",
        content="Why VRAM bandwidth matters https://gladlabs.io/posts/x",
        platform_config={},
        status=status,
        postiz_post_id=None,
        error=None,
        retry_count=0,
        last_retry_at=None,
        created_at=None,
        approved_at=None,
        posted_at=None,
        post_status=None,
    )


def _run(page: SocialDraftPage, args: list[str] | None = None):
    with patch("poindexter.cli.social.run_service", return_value=page):
        return CliRunner().invoke(list_drafts, args or [])


def test_renders_rows_and_totals():
    result = _run(
        SocialDraftPage(
            rows=[_row()],
            total=77,
            status_counts={"pending": 10, "posted": 26, "rejected": 41},
        )
    )
    assert result.exit_code == 0
    assert "[PENDING ] abcdef12" in result.output
    assert "bluesky" in result.output
    # Totals span the table, not the one row rendered.
    assert "pending=10" in result.output
    assert "posted=26" in result.output
    assert "rejected=41" in result.output


def test_announces_withheld_rows_with_resume_offset():
    """A silent cap reads as 'that is all of them'. It must not."""
    result = _run(
        SocialDraftPage(
            rows=[_row(), _row("bbbbbbbb22")],
            total=77,
            status_counts={"pending": 10, "posted": 26, "rejected": 41},
        ),
        ["--limit", "2"],
    )
    assert result.exit_code == 0
    assert "Showing 1-2 of 77" in result.output
    assert "--offset 2" in result.output


def test_offset_window_reports_its_own_position():
    result = _run(
        SocialDraftPage(
            rows=[_row()], total=77, status_counts={"pending": 10}
        ),
        ["--limit", "1", "--offset", "50"],
    )
    assert result.exit_code == 0
    assert "Showing 51-51 of 77" in result.output


def test_no_withheld_note_when_page_covers_everything():
    result = _run(
        SocialDraftPage(rows=[_row()], total=1, status_counts={"pending": 1})
    )
    assert result.exit_code == 0
    assert "Showing" not in result.output
    assert "pending=1" in result.output


def test_passes_limit_and_offset_through_to_the_service():
    page = SocialDraftPage(rows=[], total=0, status_counts={})
    with patch("poindexter.cli.social.run_service", return_value=page) as rs:
        with patch("poindexter.cli.social._svc") as svc:
            result = CliRunner().invoke(
                list_drafts, ["--limit", "5", "--offset", "10"]
            )
            assert result.exit_code == 0
            # run_service is handed a thunk; invoke it to observe the call.
            rs.call_args[0][0]("pool-sentinel")
            assert svc.list_drafts.call_args.kwargs == {
                "limit": 5,
                "offset": 10,
            }


def test_empty_result_says_so():
    result = _run(SocialDraftPage(rows=[], total=0, status_counts={}))
    assert result.exit_code == 0
    assert "No drafts found." in result.output
