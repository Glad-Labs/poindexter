"""Unit tests for the social.generate_drafts atom.

Pins the pool-resolution contract: the atom reaches the DB pool through
``state['database_service'].pool`` (the graph_def seam that
``content_router_service`` actually seeds), falling back to a bare
``state['pool']`` only for ad-hoc callers.

Regression for the GlitchTip "'NoneType' object has no attribute 'acquire'"
issue (#855): the atom used to read ``state.get('pool')`` alone, which is
always ``None`` on the canonical_blog graph_def path (the runner seeds
``database_service``, never a top-level ``pool``). So every post's
``create_draft`` was called with ``pool=None`` → ``None.acquire()`` inside
``SocialDraftsService.create_draft``, caught + logged 14× rather than raised.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.content.atoms import social_generate_drafts
from modules.content.atoms.social_generate_drafts import run as drafts_run
from services.site_config import SiteConfig
from services.social_poster import SocialPost


@pytest.fixture
def enabled_site_config() -> SiteConfig:
    return SiteConfig(
        initial_config={
            "social_drafts_enabled": "true",
            "social_draft_platforms": "twitter,bluesky",
        }
    )


@pytest.mark.asyncio
async def test_drafts_use_pool_from_database_service(monkeypatch, enabled_site_config):
    """Regression (#855): pool resolves from database_service.pool when state
    carries no bare 'pool' key — the graph_def path only seeds
    database_service. create_draft must receive the real pool, not None."""
    posts = [
        SocialPost(platform="twitter", text="tweet copy", post_url="https://gladlabs.io/posts/a"),
        SocialPost(platform="bluesky", text="tweet copy", post_url="https://gladlabs.io/posts/a"),
    ]
    monkeypatch.setattr(
        social_generate_drafts,
        "generate_social_posts",
        AsyncMock(return_value=posts),
    )
    create_draft = AsyncMock(return_value="draft-1")
    monkeypatch.setattr(social_generate_drafts._svc, "create_draft", create_draft)

    fake_pool = object()
    state = {
        "task_id": "task-abc",
        "title": "A Title",
        "post_slug": "a-title",
        # NB: no top-level "pool" — mirrors the context content_router_service
        # actually builds (only database_service is seeded).
        "database_service": SimpleNamespace(pool=fake_pool),
        "site_config": enabled_site_config,
    }

    await drafts_run(state)

    assert create_draft.await_count == 2
    for call in create_draft.await_args_list:
        assert call.kwargs["pool"] is fake_pool
        assert call.kwargs["pipeline_task_id"] == "task-abc"


@pytest.mark.asyncio
async def test_drafts_no_pool_skips_before_llm(monkeypatch, enabled_site_config):
    """No database_service and no pool → skip the whole op without generating
    copy we could not persist (matches media.persist's no-pool guard)."""
    gen = AsyncMock(return_value=[SocialPost(platform="twitter", text="x", post_url="u")])
    monkeypatch.setattr(social_generate_drafts, "generate_social_posts", gen)
    create_draft = AsyncMock()
    monkeypatch.setattr(social_generate_drafts._svc, "create_draft", create_draft)

    state = {
        "task_id": "task-xyz",
        "title": "T",
        "post_slug": "t",
        "site_config": enabled_site_config,
        # no database_service, no pool
    }

    await drafts_run(state)

    gen.assert_not_awaited()
    create_draft.assert_not_awaited()
