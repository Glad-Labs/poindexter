"""integration_db: a media rejection stores why, and the Pipeline board reads it.

Pins the real schema end to end, which the mocked unit tests cannot: the
migration adds ``media_approvals.reject_reason``, ``decide()`` writes the
checked code there, and the "Why media was rejected" panel's SQL runs against
the migrated table and counts it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from poindexter.services import media_approval_service
from poindexter.services.site_config import SiteConfig

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_SLUG = "itest-media-reject-reason"


def _panel_sql() -> str:
    board = Path(media_approval_service.__file__).resolve().parents[4] / (
        "infrastructure/grafana/dashboards/pipeline-merged.json"
    )
    panels = json.loads(board.read_text(encoding="utf-8"))["panels"]
    [panel] = [p for p in panels if p.get("title") == "Why media was rejected (window)"]
    sql = panel["targets"][0]["rawSql"]
    return re.sub(r"\$__timeFilter\((\w+)\)", r"\1 > now() - interval '1 hour'", sql)


async def test_a_rejection_stores_its_reason_and_the_panel_counts_it(test_pool):
    sc = SiteConfig(initial_config={"media_reject_reasons": "voice=Voice; hook=Weak opening"})
    async with test_pool.acquire() as conn:
        await conn.execute("DELETE FROM posts WHERE slug = $1", _SLUG)  # cascades
        post_id = await conn.fetchval(
            "INSERT INTO posts (title, slug, content) VALUES ('Itest', $1, 'x') RETURNING id",
            _SLUG,
        )
        await conn.execute(
            "INSERT INTO media_approvals (post_id, medium, status) VALUES ($1, 'video_short', 'pending')",
            post_id,
        )
    subject = str(post_id)
    try:
        await media_approval_service.decide(
            test_pool, subject, "video_short", approved=False,
            decided_by="operator:itest", notes="cracks at 0:12",
            site_config=sc, reason="Voice",
        )
        async with test_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, reject_reason, notes FROM media_approvals WHERE subject_id = $1",
                subject,
            )
            assert dict(row) == {"status": "rejected", "reject_reason": "voice", "notes": "cracks at 0:12"}

            counts = {
                (r["Reason"], r["Medium"]): r["Rejects"] for r in await conn.fetch(_panel_sql())
            }
        assert counts.get(("voice", "video_short"), 0) >= 1

        with pytest.raises(media_approval_service.UnknownRejectReasonError):
            await media_approval_service.decide(
                test_pool, subject, "video_short", approved=False,
                decided_by="operator:itest", site_config=sc, reason="boring",
            )
        async with test_pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT reject_reason FROM media_approvals WHERE subject_id = $1", subject,
            ) == "voice"  # the refused call wrote nothing
    finally:
        async with test_pool.acquire() as conn:
            await conn.execute("DELETE FROM posts WHERE slug = $1", _SLUG)
