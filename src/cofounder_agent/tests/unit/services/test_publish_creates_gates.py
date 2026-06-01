"""Contract tests for media-gate creation on post-approval (poindexter#24).

When a draft is approved (``publish_post_from_task(stage_only=True)`` — the
post parks at ``status='approved'``), the per-medium approval gate sequence
must be created so the driver can generate + gate-review media before the
post publishes. The post must NOT go live at this point.

Mock-based (mirrors test_publish_service_stage_only.py) — publish_post_from_task
is exercised against a stub DatabaseService; the gate-sequence call is
captured. The real gate-engine DB write is covered by
tests/unit/services/gates/test_post_approval_gates.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig

_TEST_SC = SiteConfig(initial_config={"site_url": "https://www.test-site.example.com"})
_TASK_ID = "11111111-1111-1111-1111-111111111111"
_POST_ID = "22222222-2222-2222-2222-222222222222"


def _make_task(niche_slug: str = "ai-ml") -> dict[str, Any]:
    return {
        "task_id": _TASK_ID,
        "topic": "Media gate sequence contract",
        "task_metadata": {
            "content": "## Heading\n\nBody.",
            "seo_description": "Test excerpt.",
            "seo_keywords": ["test"],
            "featured_image_url": "https://example.com/image.jpg",
        },
        "result": {},
        "niche_slug": niche_slug,
    }


def _make_db_service() -> Any:
    db = MagicMock()
    db.cloud_pool = None
    db.update_task_status = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="UPDATE 0")
    pool.fetchval = AsyncMock(return_value=None)
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 0")
    conn.fetchval = AsyncMock(return_value=None)
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=conn)
    txn_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn_cm)
    acq_cm = MagicMock()
    acq_cm.__aenter__ = AsyncMock(return_value=conn)
    acq_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acq_cm)
    db.pool = pool
    db._test_conn = conn
    return db


async def _run(db, *, media: list[str], gate_sink: list, niche_slug: str = "ai-ml"):
    """Drive publish_post_from_task(stage_only=True) with media-policy +
    gate-create patched, recording the gate sequence the producer requested."""
    from services.publish_service import publish_post_from_task

    captured: dict[str, Any] = {}

    async def _record_create_post(data: dict[str, Any]) -> Any:
        captured["post_data"] = data
        return MagicMock(id=_POST_ID)

    db.create_post = _record_create_post

    async def _capture_gates(pool, post_id, gates):
        gate_sink.append((post_id, gates))
        return []

    with patch("services.publish_service.resolve_media_to_generate",
               AsyncMock(return_value=media)), \
         patch("services.publish_service.create_gates_for_post", _capture_gates), \
         patch("services.publish_service._should_run_post_publish_hooks", return_value=False):
        result = await publish_post_from_task(
            db, _make_task(niche_slug), _TASK_ID,
            publisher="operator-test",
            stage_only=True,
            trigger_revalidation=False,
            queue_social=False,
            site_config=_TEST_SC,
        )
    return result, captured


@pytest.mark.asyncio
async def test_approve_creates_media_gates_and_does_not_publish() -> None:
    db = _make_db_service()
    gate_sink: list = []
    result, captured = await _run(db, media=["podcast", "video"], gate_sink=gate_sink)

    assert result.success, f"approve/stage path failed: {result.error}"
    # Parks at 'approved' — NOT published — while media gates are pending.
    assert captured["post_data"]["status"] == "approved"
    # Gate sequence created in canonical workflow order + final checkpoint.
    assert gate_sink, "no gate sequence was created on approval"
    assert gate_sink[0][0] == _POST_ID
    assert gate_sink[0][1] == ["podcast", "video", "final"]


@pytest.mark.asyncio
async def test_text_only_post_gets_lone_final_gate() -> None:
    """A niche opting into no media still gets a lone 'final' gate (D2):
    the driver auto-advances it to publish without waiting on media."""
    db = _make_db_service()
    gate_sink: list = []
    result, captured = await _run(db, media=[], gate_sink=gate_sink)

    assert result.success
    assert captured["post_data"]["status"] == "approved"
    assert gate_sink and gate_sink[0][1] == ["final"]
