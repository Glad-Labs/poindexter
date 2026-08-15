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


@pytest.fixture
def no_existing_drafts(monkeypatch) -> AsyncMock:
    """Stub the idempotency pre-filter to 'no drafts exist yet' (the common
    first-run case) so tests exercising other contracts aren't coupled to it."""
    keys = AsyncMock(return_value=set())
    monkeypatch.setattr(social_generate_drafts._svc, "existing_draft_keys", keys)
    return keys


@pytest.mark.asyncio
async def test_drafts_use_pool_from_database_service(
    monkeypatch, enabled_site_config, no_existing_drafts
):
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


@pytest.mark.asyncio
async def test_drafts_predict_publish_slug_when_post_slug_missing(
    monkeypatch, enabled_site_config, no_existing_drafts
):
    """Root-cause regression (social-drafts linking bug): on canonical_blog the
    upstream ``content.persist_task`` atom deliberately produces
    ``post_slug=None`` (the posts row is only created at approval), so the atom
    must derive the FINAL publish slug itself — via the same
    ``derive_publish_identity`` chain ``publish_post_from_task`` uses — instead
    of baking a dead ``/posts/`` URL (empty slug) into every draft."""
    gen = AsyncMock(return_value=[])
    monkeypatch.setattr(social_generate_drafts, "generate_social_posts", gen)

    state = {
        "task_id": "f3a71ef6-27a9-47db-ad3c-426d7fc35a2f",
        "title": "Why Memory Bandwidth Beats Raw VRAM",
        "topic": "Some topic",
        "content": "# The Writer H1\n\nBody text here.",
        "post_slug": None,
        "database_service": SimpleNamespace(pool=object()),
        "site_config": enabled_site_config,
    }

    await drafts_run(state)

    gen.assert_awaited_once()
    # The invariant is "the predicted slug IS the publish slug", so assert it
    # against derive_publish_identity itself rather than against a literal.
    # Pinning a literal here is what made this test fail the 2026-08-15
    # precedence inversion even though the two sides still agreed — the
    # drift it exists to catch is between the atom and publish, not between
    # the atom and a hardcoded string.
    from services.publish_service import derive_publish_identity

    _t, _c, expected_slug = derive_publish_identity(
        state["content"], state["title"], state["topic"], state["task_id"],
    )
    assert gen.await_args.kwargs["slug"] == expected_slug
    # ...and it must be a real slug, not the dead ``/posts/`` index URL.
    assert gen.await_args.kwargs["slug"].endswith("-f3a71ef6")
    assert gen.await_args.kwargs["slug"] != "-f3a71ef6"


@pytest.mark.asyncio
async def test_drafts_predicted_slug_falls_back_to_title_then_topic(
    monkeypatch, enabled_site_config, no_existing_drafts
):
    """No H1 in content → the fallback chain mirrors publish exactly:
    merged title, then topic."""
    gen = AsyncMock(return_value=[])
    monkeypatch.setattr(social_generate_drafts, "generate_social_posts", gen)

    state = {
        "task_id": "511012cc-3a5a-4650-95b6-8485eb109d59",
        "title": "Automating AI Content Workflows",
        "topic": "unused when title present",
        "content": "Plain body with no leading heading.",
        "post_slug": None,
        "database_service": SimpleNamespace(pool=object()),
        "site_config": enabled_site_config,
    }

    await drafts_run(state)

    assert (
        gen.await_args.kwargs["slug"]
        == "automating-ai-content-workflows-511012cc"
    )


@pytest.mark.asyncio
async def test_drafts_honor_existing_post_slug(
    monkeypatch, enabled_site_config, no_existing_drafts
):
    """A real post_slug in state (e.g. a future existing-post/republish flow)
    is used verbatim — prediction only kicks in when it is missing."""
    gen = AsyncMock(return_value=[])
    monkeypatch.setattr(social_generate_drafts, "generate_social_posts", gen)

    state = {
        "task_id": "task-abc",
        "title": "A Title",
        "content": "# A Different Heading\n\nBody.",
        "post_slug": "already-live-slug-12345678",
        "database_service": SimpleNamespace(pool=object()),
        "site_config": enabled_site_config,
    }

    await drafts_run(state)

    assert gen.await_args.kwargs["slug"] == "already-live-slug-12345678"


# ---------------------------------------------------------------------------
# Idempotency pre-filter (poindexter#833): a finalize re-run (preview_gate
# regen loop, checkpoint restore, task retry) must not stack duplicate drafts
# — task 511012cc accumulated 3 identical Bluesky drafts and posted all three.
# Keys that already carry an active (pending/failed) or posted draft are
# skipped BEFORE any copy is generated, so replays spend zero LLM calls.
# ---------------------------------------------------------------------------

def _pooled_state(site_config: SiteConfig, **overrides) -> dict:
    state = {
        "task_id": "511012cc-3a5a-4650-95b6-8485eb109d59",
        "title": "Automating AI Content Workflows",
        "post_slug": "automating-ai-content-workflows-511012cc",
        "database_service": SimpleNamespace(pool=object()),
        "site_config": site_config,
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_replay_with_all_keys_taken_skips_llm_entirely(
    monkeypatch, enabled_site_config
):
    """The checkpoint-replay case: every configured platform already has a
    draft → the atom no-ops without generating copy or touching the table."""
    gen = AsyncMock()
    monkeypatch.setattr(social_generate_drafts, "generate_social_posts", gen)
    create_draft = AsyncMock()
    monkeypatch.setattr(social_generate_drafts._svc, "create_draft", create_draft)
    monkeypatch.setattr(
        social_generate_drafts._svc,
        "existing_draft_keys",
        AsyncMock(return_value={("twitter", ""), ("bluesky", "")}),
    )

    await drafts_run(_pooled_state(enabled_site_config))

    gen.assert_not_awaited()
    create_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_generates_only_missing_platforms(
    monkeypatch, enabled_site_config
):
    """A partially-covered task (twitter already drafted) only creates the
    missing platform's draft on re-run."""
    posts = [
        SocialPost(platform="twitter", text="tweet copy", post_url="u"),
        SocialPost(platform="bluesky", text="skeet copy", post_url="u"),
    ]
    monkeypatch.setattr(
        social_generate_drafts, "generate_social_posts", AsyncMock(return_value=posts)
    )
    create_draft = AsyncMock(return_value="draft-1")
    monkeypatch.setattr(social_generate_drafts._svc, "create_draft", create_draft)
    monkeypatch.setattr(
        social_generate_drafts._svc,
        "existing_draft_keys",
        AsyncMock(return_value={("twitter", "")}),
    )

    await drafts_run(_pooled_state(enabled_site_config))

    create_draft.assert_awaited_once()
    assert create_draft.await_args.kwargs["platform"] == "bluesky"


@pytest.mark.asyncio
async def test_replay_reddit_skips_taken_subreddits(monkeypatch):
    """Reddit dedups per subreddit: only the not-yet-drafted subreddit gets
    copy generated + a draft row."""
    site_config = SiteConfig(
        initial_config={
            "social_drafts_enabled": "true",
            "social_draft_platforms": "reddit",
            "social_reddit_subreddits": "r/LocalLLaMA,r/selfhosted",
        }
    )
    reddit_copy = AsyncMock(return_value="reddit copy")
    monkeypatch.setattr(social_generate_drafts, "_generate_reddit_copy", reddit_copy)
    create_draft = AsyncMock(return_value="draft-1")
    monkeypatch.setattr(social_generate_drafts._svc, "create_draft", create_draft)
    monkeypatch.setattr(
        social_generate_drafts._svc,
        "existing_draft_keys",
        AsyncMock(return_value={("reddit", "r/LocalLLaMA")}),
    )

    await drafts_run(_pooled_state(site_config))

    reddit_copy.assert_awaited_once()
    assert reddit_copy.await_args.kwargs["subreddit"] == "r/selfhosted"
    create_draft.assert_awaited_once()
    assert create_draft.await_args.kwargs["platform_config"] == {
        "subreddit": "r/selfhosted"
    }


# ---------------------------------------------------------------------------
# Generation-failure visibility (poindexter#863): a swallowed exception used
# to leave zero drafts with only a log line — no alert, no persisted record,
# and nothing for RetryFailedSocialDraftsJob to pick up (it only retries
# EXISTING 'failed' rows; this failure mode never creates one). The atom
# must notify_operator so the failure is actually visible, while still
# returning normally — a social-copy hiccup must never block the post from
# publishing.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_text_generation_failure_notifies_operator(
    monkeypatch, enabled_site_config, no_existing_drafts
):
    monkeypatch.setattr(
        social_generate_drafts,
        "generate_social_posts",
        AsyncMock(side_effect=RuntimeError("ollama timeout")),
    )
    notify = AsyncMock()
    monkeypatch.setattr(social_generate_drafts, "notify_operator", notify)

    result = await drafts_run(_pooled_state(enabled_site_config))

    assert result == {}
    notify.assert_awaited_once()
    assert "ollama timeout" in notify.await_args.args[0]
    assert notify.await_args.kwargs["critical"] is False


@pytest.mark.asyncio
async def test_reddit_generation_failure_notifies_operator_once(monkeypatch):
    """Both subreddits fail → ONE batched notification, not one per
    subreddit — a shared root cause (e.g. Ollama down) shouldn't spam."""
    site_config = SiteConfig(
        initial_config={
            "social_drafts_enabled": "true",
            "social_draft_platforms": "reddit",
            "social_reddit_subreddits": "r/LocalLLaMA,r/selfhosted",
        }
    )
    monkeypatch.setattr(
        social_generate_drafts._svc,
        "existing_draft_keys",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        social_generate_drafts,
        "_generate_reddit_copy",
        AsyncMock(side_effect=RuntimeError("model unavailable")),
    )
    notify = AsyncMock()
    monkeypatch.setattr(social_generate_drafts, "notify_operator", notify)

    await drafts_run(_pooled_state(site_config))

    notify.assert_awaited_once()
    msg = notify.await_args.args[0]
    assert "r/LocalLLaMA" in msg and "r/selfhosted" in msg
    assert notify.await_args.kwargs["critical"] is False


@pytest.mark.asyncio
async def test_reddit_generation_partial_failure_still_creates_good_draft(
    monkeypatch,
):
    """One subreddit fails, the other succeeds — the failure must not lose
    the draft that DID generate successfully (per-subreddit try/except, not
    one try/except around the whole loop)."""
    site_config = SiteConfig(
        initial_config={
            "social_drafts_enabled": "true",
            "social_draft_platforms": "reddit",
            "social_reddit_subreddits": "r/LocalLLaMA,r/selfhosted",
        }
    )
    monkeypatch.setattr(
        social_generate_drafts._svc,
        "existing_draft_keys",
        AsyncMock(return_value=set()),
    )

    async def _copy(*, subreddit, **_kwargs):
        if subreddit == "r/LocalLLaMA":
            raise RuntimeError("boom")
        return "good copy"

    monkeypatch.setattr(social_generate_drafts, "_generate_reddit_copy", _copy)
    create_draft = AsyncMock(return_value="draft-1")
    monkeypatch.setattr(social_generate_drafts._svc, "create_draft", create_draft)
    monkeypatch.setattr(social_generate_drafts, "notify_operator", AsyncMock())

    await drafts_run(_pooled_state(site_config))

    create_draft.assert_awaited_once()
    assert create_draft.await_args.kwargs["platform_config"] == {
        "subreddit": "r/selfhosted"
    }
