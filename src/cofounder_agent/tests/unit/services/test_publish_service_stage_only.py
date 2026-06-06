"""Contract tests for ``publish_post_from_task(stage_only=True)``.

Pins the 2026-05-26 fix for the approve → schedule batch bridge:
``scheduling_service.schedule_batch`` queries
``posts.status IN ('approved', 'awaiting_approval') AND
published_at IS NULL`` for eligible posts. Nothing in the historical
pipeline produced posts at status='approved' — the approve_task
handler with default ``auto_publish=False`` left the pipeline_task
at status='approved' but never created the posts row. Schedule batch
returned ``No eligible posts to schedule`` even though pipeline_tasks
showed two approved.

The fix adds a ``stage_only=True`` path to ``publish_post_from_task``
that creates the posts row at ``status='approved'`` with
``published_at=NULL`` and skips every publish-only side effect
(distribution recording, revalidation, social-queue, cloud sync,
post.published webhook). The approve_task handler now calls this
path on approve-without-auto_publish.

These tests pin the contract — a future refactor that flips the
status to anything else, or that re-introduces the publish-only
side-effects on the stage path, fails here instead of leaving the
operator with another broken bridge.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig

# #272 Phase-2g: publish_post_from_task requires an injected site_config.
_TEST_SC = SiteConfig(initial_config={"site_url": "https://www.test-site.example.com"})


def _make_task() -> dict[str, Any]:
    return {
        "task_id": "11111111-1111-1111-1111-111111111111",
        "topic": "Test post for stage_only contract",
        "task_metadata": {
            "content": "## Heading\n\nBody.",
            "seo_description": "Test excerpt.",
            "seo_keywords": ["test"],
            "featured_image_url": "https://example.com/image.jpg",
        },
        "result": {},
        "category": "technology",
        "primary_keyword": "test",
        "niche_slug": "",
    }


def _make_db_service() -> Any:
    """Build a DatabaseService stub that records create_post + update_task_status."""
    # publish_post_from_task does `getattr(db_service, "cloud_pool", None)
    # or db_service.pool` — set cloud_pool to None explicitly so the
    # default-MagicMock fallthrough doesn't shadow our async-configured
    # pool.
    db = MagicMock()
    db.cloud_pool = None
    db.create_post = AsyncMock(
        side_effect=lambda data: MagicMock(id="22222222-2222-2222-2222-222222222222"),
    )
    db.update_task_status = AsyncMock(return_value=None)

    # Pool needs awaitable .fetchrow / .execute on the top-level pool
    # AND on the async-context-managed connection. publish_service uses
    # both shapes.
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="UPDATE 0")
    pool.fetchval = AsyncMock(return_value=None)
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 0")
    conn.fetchval = AsyncMock(return_value=None)
    # conn.transaction() returns an async context manager. The 2026-05-28
    # pipeline_tasks status-sync fix wraps the promote path in
    # ``async with conn.transaction()`` so the posts UPDATE + the
    # pipeline_tasks UPDATE move together. Without an explicit stub,
    # MagicMock returns a plain MagicMock that doesn't support
    # ``async with`` and crashes the publisher.
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=conn)
    txn_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn_cm)
    acq_cm = MagicMock()
    acq_cm.__aenter__ = AsyncMock(return_value=conn)
    acq_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acq_cm)
    db.pool = pool
    # Expose conn on the db stub so tests can assert against the
    # connection-level execute calls (the promote path now goes through
    # ``async with pool.acquire() as conn`` rather than pool.execute
    # directly, since 2026-05-28).
    db._test_conn = conn
    return db


@pytest.mark.asyncio
async def test_stage_only_creates_post_at_status_approved() -> None:
    """The created posts row must have status='approved' (not 'published',
    not 'draft', not 'awaiting_gates'). This is the seam schedule_batch
    queries — any other status and the post is invisible to scheduling."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    captured: dict[str, Any] = {}

    async def _record_create_post(data: dict[str, Any]) -> Any:
        captured["post_data"] = data
        return MagicMock(id="22222222-2222-2222-2222-222222222222")

    db.create_post = _record_create_post

    # Patch the internal_link_coherence import so we don't pull in the
    # full pipeline. Stub stages that publish_post_from_task calls.
    with patch("services.publish_service._spawn_background"), \
         patch("services.publish_service._should_run_post_publish_hooks", return_value=False):
        result = await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            trigger_revalidation=False,
            queue_social=False,
            site_config=_TEST_SC,
        )

    assert result.success, f"stage_only path failed: {result.error}"
    assert result.staged is True
    assert captured["post_data"]["status"] == "approved", (
        f"Expected status='approved' for schedule_batch eligibility, "
        f"got {captured['post_data']['status']!r}"
    )
    assert "published_at" not in captured["post_data"] or captured["post_data"]["published_at"] is None, (
        "stage_only posts must have published_at NULL — schedule_batch's "
        "WHERE clause filters published_at IS NULL"
    )


@pytest.mark.asyncio
async def test_stage_only_skips_distributed_at_stamp() -> None:
    """Staged posts must NOT be marked distributed — the RSS feed and
    static export gate on distributed_at. A staged post is invisible
    until scheduled_publisher promotes it."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    captured: dict[str, Any] = {}

    async def _record_create_post(data: dict[str, Any]) -> Any:
        captured["post_data"] = data
        return MagicMock(id="22222222-2222-2222-2222-222222222222")

    db.create_post = _record_create_post

    with patch("services.publish_service._spawn_background"), \
         patch("services.publish_service._should_run_post_publish_hooks", return_value=False):
        await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            trigger_revalidation=False,
            queue_social=False,
            site_config=_TEST_SC,
        )

    assert captured["post_data"].get("distributed_at") is None, (
        "stage_only posts must NOT have distributed_at set — leaks staged "
        "content into the RSS feed and /posts static export before it's "
        "actually scheduled to publish"
    )


@pytest.mark.asyncio
async def test_stage_only_leaves_task_at_status_approved_not_published() -> None:
    """The pipeline_task must stay at status='approved' (the state the
    approve_task handler put it in). Flipping to 'published' would
    confuse downstream consumers + break the schedule_batch flow that
    expects the task to still be in the approval-staged pool."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    captured_status_updates: list[tuple[str, str]] = []

    async def _record_status_update(task_id: str, status: str, *args, **kwargs) -> None:
        captured_status_updates.append((task_id, status))

    db.update_task_status = _record_status_update

    with patch("services.publish_service._spawn_background"), \
         patch("services.publish_service._should_run_post_publish_hooks", return_value=False):
        await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            trigger_revalidation=False,
            queue_social=False,
            site_config=_TEST_SC,
        )

    # Should have exactly one status update — to 'approved', not 'published'.
    statuses = [s for _tid, s in captured_status_updates]
    assert "published" not in statuses, (
        f"stage_only flipped task to 'published' — should stay 'approved' "
        f"for the staging pool. Saw: {statuses}"
    )
    assert "approved" in statuses, (
        f"stage_only did not update task to 'approved'. Saw: {statuses}"
    )


@pytest.mark.asyncio
async def test_stage_only_and_draft_mode_are_mutually_exclusive() -> None:
    """Both flags flip status away from the default — combining them
    is ambiguous. Caller error should surface loudly per
    feedback_no_silent_defaults."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()

    with pytest.raises(ValueError, match="mutually exclusive"):
        await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=True,
            draft_mode=True,
            site_config=_TEST_SC,
        )


@pytest.mark.asyncio
async def test_publish_promotes_existing_approved_post_to_published() -> None:
    """Regression for 2026-05-27 ``poindexter tasks publish`` silent
    no-op. After ``approve`` creates a post at ``status='approved'``,
    the operator's follow-up ``publish`` must transition that same row
    to ``status='published'`` + set ``published_at`` — NOT return
    "skipping duplicate" (the old behavior left the post invisible to
    the site forever).

    Caught manually 2026-05-27 on task 677cc2df: CLI returned HTTP 200
    with ``status=approved`` and Matt's blog post never went live.
    """
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    # Existing post already staged at status='approved' (what
    # approve_task created). The idempotency guard's SELECT picks
    # this up FIRST and used to bail.
    existing = {
        "id": "33333333-3333-3333-3333-333333333333",
        "slug": "test-post-for-stage-only-contract-11111111",
        "title": "Test post for stage_only contract",
        "status": "approved",
    }
    db.pool.fetchrow = AsyncMock(return_value=existing)
    db.pool.execute = AsyncMock(return_value="UPDATE 1")
    db._test_conn.execute = AsyncMock(return_value="UPDATE 1")

    result = await publish_post_from_task(
        db, _make_task(), "11111111-1111-1111-1111-111111111111",
        publisher="operator-test",
        stage_only=False,
        draft_mode=False,
        site_config=_TEST_SC,
    )

    # Result mirrors the existing post (no duplicate row created).
    assert result.success is True
    assert result.post_id == existing["id"]
    assert result.post_slug == existing["slug"]
    # The promotion UPDATE fired — must be the publish-status SQL, not
    # a generic touch. The 2026-05-28 fix moved the promote write from
    # ``pool.execute`` to ``async with pool.acquire() as conn`` so the
    # posts UPDATE and the new pipeline_tasks sync UPDATE run in the
    # same transaction. Look at conn-level execute calls now.
    all_conn_calls = db._test_conn.execute.await_args_list
    posts_update_calls = [
        c for c in all_conn_calls
        if "UPDATE posts" in (c.args[0] if c.args else "")
        and "status = 'published'" in (c.args[0] if c.args else "")
    ]
    pipeline_sync_calls = [
        c for c in all_conn_calls
        if "UPDATE pipeline_tasks" in (c.args[0] if c.args else "")
    ]
    assert len(posts_update_calls) == 1, (
        f"Expected one promote UPDATE on posts; got {len(posts_update_calls)}: "
        f"{[c.args[0][:80] for c in all_conn_calls]}"
    )
    assert len(pipeline_sync_calls) == 1, (
        f"Expected one pipeline_tasks status-sync UPDATE; got "
        f"{len(pipeline_sync_calls)}: {[c.args[0][:80] for c in all_conn_calls]}"
    )
    # And create_post must NOT have been called — promotion happens in
    # place, no duplicate row.
    assert db.create_post.await_count == 0


@pytest.mark.asyncio
async def test_publish_skips_when_post_already_published() -> None:
    """Idempotency: a second publish call on a row already at
    status='published' must be a clean no-op (no extra UPDATE, no
    duplicate post creation). Without this, retries from the CLI or
    a stuck scheduled_publisher cycle would double-stamp published_at
    and re-fire revalidation needlessly."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    existing = {
        "id": "44444444-4444-4444-4444-444444444444",
        "slug": "test-post-for-stage-only-contract-11111111",
        "title": "Already published",
        "status": "published",
    }
    db.pool.fetchrow = AsyncMock(return_value=existing)
    db.pool.execute = AsyncMock(return_value="UPDATE 1")

    result = await publish_post_from_task(
        db, _make_task(), "11111111-1111-1111-1111-111111111111",
        publisher="operator-test",
        stage_only=False,
        draft_mode=False,
        site_config=_TEST_SC,
    )

    assert result.success is True
    assert result.post_id == existing["id"]
    # No promotion UPDATE should run for an already-published row.
    publish_update_calls = [
        c for c in db.pool.execute.await_args_list
        if "status = 'published'" in (c.args[0] if c.args else "")
    ]
    assert publish_update_calls == [], (
        "Re-publishing an already-published post must NOT issue a "
        "promotion UPDATE — that would re-stamp published_at."
    )
    assert db.create_post.await_count == 0


@pytest.mark.asyncio
async def test_stage_only_does_not_promote_existing_approved() -> None:
    """The promote-on-publish path must not fire when the SECOND call is
    itself stage_only (e.g. operator approves the same task twice).
    The existing approved row stays at 'approved', no published_at,
    no UPDATE."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    existing = {
        "id": "55555555-5555-5555-5555-555555555555",
        "slug": "test-post-for-stage-only-contract-11111111",
        "title": "Already staged",
        "status": "approved",
    }
    db.pool.fetchrow = AsyncMock(return_value=existing)
    db.pool.execute = AsyncMock(return_value="UPDATE 1")

    result = await publish_post_from_task(
        db, _make_task(), "11111111-1111-1111-1111-111111111111",
        publisher="operator-test",
        stage_only=True,
        draft_mode=False,
        site_config=_TEST_SC,
    )

    assert result.success is True
    publish_update_calls = [
        c for c in db.pool.execute.await_args_list
        if "status = 'published'" in (c.args[0] if c.args else "")
    ]
    assert publish_update_calls == [], (
        "Second stage_only call must NOT promote — only the publish "
        "endpoint (stage_only=False) flips approved → published."
    )


@pytest.mark.asyncio
async def test_publish_result_exposes_staged_field() -> None:
    """The staged field on PublishResult lets callers distinguish a
    staged post (status='approved') from a live publish. Without it
    the approve handler can't tell the difference without re-fetching
    the row, and downstream consumers (operator notify, social queue)
    would treat both as published."""
    from services.publish_service import PublishResult

    live = PublishResult(success=True, post_id="x", post_slug="y", published_url="/posts/y")
    staged = PublishResult(success=True, post_id="x", post_slug="y", published_url="/posts/y", staged=True)
    assert live.staged is False
    assert staged.staged is True
    assert live.to_dict()["staged"] is False
    assert staged.to_dict()["staged"] is True


# --- Edge-case + error-path coverage for the pure helpers backing the
# publish path. The stage_only contract above sits on top of these; if
# any of these silently swallow a wrong-shaped input the whole path
# misbehaves without raising.


def test_parse_json_field_returns_dict_for_valid_json_string() -> None:
    """task_metadata / result columns come back as either dicts (asyncpg
    JSONB) or strings (some legacy code paths fetchrow's raw text).
    The helper must accept both so publish_post_from_task doesn't have
    to branch on type at every call site."""
    from services.publish_service import _parse_json_field

    parsed = _parse_json_field('{"content": "body", "seo_keywords": ["a"]}', "task_metadata")
    assert parsed == {"content": "body", "seo_keywords": ["a"]}


def test_parse_json_field_swallows_invalid_json_to_empty_dict() -> None:
    """A malformed JSON string must NOT raise — the publish path treats
    a corrupt metadata column as 'no metadata' and proceeds with the
    fallbacks. Raising here would 500 the whole publish for one bad row
    instead of degrading gracefully."""
    from services.publish_service import _parse_json_field

    assert _parse_json_field("not-json{", "task_metadata", "task-id") == {}
    assert _parse_json_field("", "task_metadata") == {}


def test_parse_json_field_none_and_non_dict_return_empty() -> None:
    """None (NULL JSONB column) and unexpected scalar types (int, list)
    both collapse to {}. Lists in particular would crash the downstream
    `.get("content")` calls in the publish path — this helper is the
    defensive shim that keeps that from happening."""
    from services.publish_service import _parse_json_field

    assert _parse_json_field(None) == {}
    assert _parse_json_field(42) == {}
    assert _parse_json_field(["not", "a", "dict"]) == {}


def test_parse_json_field_passes_through_dict_unchanged() -> None:
    """asyncpg already deserialises JSONB to dict — re-parsing would
    waste cycles AND lose any non-JSON-roundtrippable types the column
    might carry. The dict branch must be the identity transform."""
    from services.publish_service import _parse_json_field

    original = {"content": "x", "nested": {"k": "v"}}
    assert _parse_json_field(original) is original


def test_should_run_post_publish_hooks_worker_mode(monkeypatch) -> None:
    """DEPLOYMENT_MODE=worker is the trigger for the six post-publish
    hooks (podcast / video / R2 / RSS / YouTube / newsletter). Anything
    else and the hooks no-op — this is the seam that decides whether
    distribution actually happens on a publish."""
    from services import publish_service

    monkeypatch.setenv("DEPLOYMENT_MODE", "worker")
    assert publish_service._should_run_post_publish_hooks() is True


def test_should_run_post_publish_hooks_case_insensitive(monkeypatch) -> None:
    """Operators sometimes export DEPLOYMENT_MODE=WORKER (uppercase) —
    the docker-compose YAML conventions and Matt's PowerShell helpers
    don't enforce case. The .lower() in the helper must keep this
    working; a regression to a case-sensitive compare would silently
    disable distribution on those hosts."""
    from services import publish_service

    monkeypatch.setenv("DEPLOYMENT_MODE", "WORKER")
    assert publish_service._should_run_post_publish_hooks() is True


def test_should_run_post_publish_hooks_unset_defaults_off(monkeypatch) -> None:
    """When DEPLOYMENT_MODE is unset the default is 'coordinator' →
    False. Coordinator hosts (future cloud read-path) must NOT run the
    distribution hooks; they don't own the local pipeline + GPU + FS.
    This pins the safer default."""
    from services import publish_service

    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    assert publish_service._should_run_post_publish_hooks() is False


def test_publish_result_to_dict_carries_failure_payload() -> None:
    """PublishResult.to_dict() is the wire format the operator API +
    Discord notify use. On failure, the error field MUST round-trip so
    the operator sees the actual cause — not a generic 'publish failed'.
    Pins the full set of fields (a missing key in to_dict() would
    silently drop the diagnostic)."""
    from services.publish_service import PublishResult

    failure = PublishResult(success=False, error="boom: schema mismatch on insert")
    payload = failure.to_dict()
    assert payload["success"] is False
    assert payload["error"] == "boom: schema mismatch on insert"
    assert payload["staged"] is False
    assert payload["post_id"] is None
    assert payload["revalidation_success"] is False
    assert payload["static_export_success"] is False
    assert set(payload.keys()) >= {
        "success", "post_id", "post_slug", "published_url", "post_title",
        "revalidation_success", "static_export_success", "staged", "error",
    }


@pytest.mark.asyncio
async def test_publish_promote_triggers_r2_export() -> None:
    """The promote-on-publish path MUST call export_post so the R2 static
    index gets updated immediately. Without this, the static_export
    reconciliation probe sees DB ahead of R2 and fires a drift alert
    on every operator publish (Matt 2026-05-27 incident — DB=80 vs
    R2=79 after dev_diary publish)."""
    from services.publish_service import publish_post_from_task

    db = _make_db_service()
    existing = {
        "id": "66666666-6666-6666-6666-666666666666",
        "slug": "test-post-for-stage-only-contract-11111111",
        "title": "Test post",
        "status": "approved",
    }
    db.pool.fetchrow = AsyncMock(return_value=existing)
    db.pool.execute = AsyncMock(return_value="UPDATE 1")

    export_calls: list[tuple[Any, str]] = []

    async def fake_export_post(pool: Any, slug: str, **kwargs: Any) -> bool:
        export_calls.append((pool, slug))
        return True

    with patch(
        "services.static_export_service.export_post",
        side_effect=fake_export_post,
    ):
        result = await publish_post_from_task(
            db, _make_task(), "11111111-1111-1111-1111-111111111111",
            publisher="operator-test",
            stage_only=False,
            draft_mode=False,
            site_config=_TEST_SC,
        )

    assert result.success is True
    assert result.static_export_success is True
    assert len(export_calls) == 1
    assert export_calls[0][1] == existing["slug"]
