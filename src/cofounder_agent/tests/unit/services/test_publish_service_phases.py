"""Unit tests for the extracted publish_post_from_task phases (poindexter#623).

The 941-line god-function was decomposed into named phases. These tests
exercise the pure / cohesive phases directly — proving the issue's goal that
each phase is now independently testable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.publish_service import (
    PublishResult,
    _niche_allowlist_block,
    _parse_publish_inputs,
    _promote_or_skip_existing,
)
from services.site_config import SiteConfig
from tests.unit.services._gate_fakes import FakeConn, FakePool

pytestmark = pytest.mark.unit

# A real (empty) SiteConfig satisfies the DI seam; the early-return guard cases
# never touch it, and the promote case patches the only consumer (export_post).
_SC = SiteConfig(initial_config={})


# ---------------------------------------------------------------------------
# Phase 1 — _parse_publish_inputs (pure)
# ---------------------------------------------------------------------------


class TestParsePublishInputs:
    def test_missing_content_returns_failed_result(self):
        out = _parse_publish_inputs({"topic": "T", "content": ""}, "task1")
        assert isinstance(out, PublishResult)
        assert out.success is False

    def test_missing_topic_returns_failed_result(self):
        out = _parse_publish_inputs({"topic": "", "content": "body"}, "task1")
        assert isinstance(out, PublishResult)
        assert out.success is False

    def test_happy_path_returns_parsed_inputs(self):
        out = _parse_publish_inputs(
            {"topic": "Cats", "content": "# Cats\n\nbody"}, "abcdef12"
        )
        assert not isinstance(out, PublishResult)
        assert out.topic == "Cats"
        assert out.draft_content
        # pipeline_task_id seam is always stamped onto post metadata.
        assert out.metadata["pipeline_task_id"] == "abcdef12"

    def test_result_wins_over_metadata_on_merge(self):
        task = {
            "topic": "T",
            "content": "body",
            "task_metadata": '{"seo_description": "from_meta", "k": "meta"}',
            "result": '{"seo_description": "from_result"}',
        }
        out = _parse_publish_inputs(task, "t1")
        assert not isinstance(out, PublishResult)
        # result overrides task_metadata for overlapping keys
        assert out.seo_description == "from_result"
        # non-overlapping metadata key survives
        assert out.merged["k"] == "meta"

    def test_seo_description_html_stripped(self):
        task = {
            "topic": "T",
            "content": "body",
            "seo_description": "Hello <img src=x> <b>world</b>",
        }
        out = _parse_publish_inputs(task, "t1")
        assert not isinstance(out, PublishResult)
        assert "<" not in out.seo_description
        assert out.seo_description == "Hello world"

    def test_non_dict_featured_image_data_coerced_to_empty(self):
        task = {
            "topic": "T",
            "content": "body",
            "featured_image_data": "not-a-dict",
        }
        out = _parse_publish_inputs(task, "t1")
        assert not isinstance(out, PublishResult)
        assert out.featured_image_data == {}

    def test_content_fallback_chain(self):
        # content empty → falls back to merged.body
        task = {
            "topic": "T",
            "content": "",
            "task_metadata": '{"body": "from_body"}',
        }
        out = _parse_publish_inputs(task, "t1")
        assert not isinstance(out, PublishResult)
        assert out.draft_content == "from_body"


# ---------------------------------------------------------------------------
# Phase 2 — _promote_or_skip_existing (idempotency / promote guard)
# ---------------------------------------------------------------------------


class TestPromoteOrSkipExisting:
    async def test_no_existing_post_returns_none(self):
        pool = FakePool(FakeConn(fetchrow_result=None))
        out = await _promote_or_skip_existing(
            pool, "task1", stage_only=False, draft_mode=False,
            topic="T", site_config=_SC,
        )
        assert out is None

    async def test_published_duplicate_is_idempotent_noop(self):
        existing = {"id": "p1", "slug": "cats-task1", "title": "Cats", "status": "published"}
        conn = FakeConn(fetchrow_result=existing)
        pool = FakePool(conn)
        out = await _promote_or_skip_existing(
            pool, "task1", stage_only=False, draft_mode=False,
            topic="T", site_config=_SC,
        )
        assert isinstance(out, PublishResult)
        assert out.success is True
        assert out.post_id == "p1"
        # idempotent no-op: no UPDATE issued
        assert conn.executed == []

    async def test_approved_with_stage_only_is_noop(self):
        existing = {"id": "p1", "slug": "cats-task1", "title": "Cats", "status": "approved"}
        conn = FakeConn(fetchrow_result=existing)
        pool = FakePool(conn)
        out = await _promote_or_skip_existing(
            pool, "task1", stage_only=True, draft_mode=False,
            topic="T", site_config=_SC,
        )
        assert isinstance(out, PublishResult)
        # re-stage path: no promote UPDATE
        assert conn.executed == []

    async def test_approved_publish_promotes_in_place(self):
        existing = {"id": "p1", "slug": "cats-task1", "title": "Cats", "status": "approved"}
        conn = FakeConn(fetchrow_result=existing)
        pool = FakePool(conn)
        # export_post is the only site_config consumer on the promote path;
        # patch it so the test asserts the promote UPDATEs deterministically.
        with patch(
            "services.static_export_service.export_post", new=AsyncMock(return_value=True)
        ):
            out = await _promote_or_skip_existing(
                pool, "task1", stage_only=False, draft_mode=False,
                topic="T", site_config=_SC,
            )
        assert isinstance(out, PublishResult)
        assert out.success is True
        assert out.static_export_success is True
        # promote path issues the posts + pipeline_tasks UPDATEs
        sql = "\n".join(s for s, _ in conn.executed)
        assert "UPDATE posts SET status = 'published'" in sql
        assert "UPDATE pipeline_tasks" in sql


# ---------------------------------------------------------------------------
# Phase 1c — _niche_allowlist_block (#729 backstop)
# ---------------------------------------------------------------------------


_ENFORCED = SiteConfig(initial_config={"enforce_niche_allowlist": "true"})


def _patch_known(slugs):
    return patch(
        "services.niche_service.get_known_niche_slugs",
        new=AsyncMock(return_value=set(slugs)),
    )


def _patch_notify():
    return patch(
        "services.integrations.operator_notify.notify_operator",
        new=AsyncMock(),
    )


class TestNicheAllowlistBlock:
    async def test_draft_mode_is_exempt(self):
        # Drafts (WIP) never hit the gate, even with an unknown niche.
        out = await _niche_allowlist_block(
            None, {"niche_slug": "ghost"}, "t1",
            draft_mode=True, site_config=_ENFORCED,
        )
        assert out is None

    async def test_disabled_gate_is_exempt(self):
        sc = SiteConfig(initial_config={"enforce_niche_allowlist": "false"})
        out = await _niche_allowlist_block(
            None, {"niche_slug": "ghost"}, "t1",
            draft_mode=False, site_config=sc,
        )
        assert out is None

    async def test_known_active_niche_allowed(self):
        with _patch_known({"glad-labs", "dev_diary"}):
            out = await _niche_allowlist_block(
                None, {"niche_slug": "glad-labs"}, "t1",
                draft_mode=False, site_config=_ENFORCED,
            )
        assert out is None

    async def test_known_inactive_niche_allowed(self):
        # The #729 fix: dev_diary is discovery-inactive (no topic sweep /
        # media backfill) but a known niche, so it must publish.
        with _patch_known({"glad-labs", "dev_diary"}):
            out = await _niche_allowlist_block(
                None, {"niche_slug": "dev_diary"}, "t1",
                draft_mode=False, site_config=_ENFORCED,
            )
        assert out is None

    async def test_unknown_niche_blocked(self):
        with _patch_known({"glad-labs", "dev_diary"}), _patch_notify():
            out = await _niche_allowlist_block(
                None, {"niche_slug": "totally-unknown"}, "t1",
                draft_mode=False, site_config=_ENFORCED,
            )
        assert isinstance(out, PublishResult)
        assert out.success is False
        assert "not a known niche" in (out.error or "")

    async def test_missing_niche_blocked(self):
        with _patch_known({"glad-labs"}), _patch_notify():
            out = await _niche_allowlist_block(
                None, {}, "t1",
                draft_mode=False, site_config=_ENFORCED,
            )
        assert isinstance(out, PublishResult)
        assert out.success is False
        assert "<none>" in (out.error or "")

    async def test_empty_known_set_fails_open(self):
        # DB unreadable -> empty set -> never block (can't brick publishing).
        with _patch_known(set()):
            out = await _niche_allowlist_block(
                None, {"niche_slug": "anything"}, "t1",
                draft_mode=False, site_config=_ENFORCED,
            )
        assert out is None
