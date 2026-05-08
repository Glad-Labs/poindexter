"""Unit tests for ``services/jobs/crosspost_to_devto.py``.

Pool + DevToCrossPostService mocked. Focus: API-key-missing skip,
no-candidates path, mixed success/failure across the batch, fetch
failure, Gitea opt-in/opt-out.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.crosspost_to_devto import CrosspostToDevtoJob


def _make_pool(
    rows: list[dict] | None = None,
    fetch_raises: BaseException | None = None,
) -> Any:
    conn = AsyncMock()
    if fetch_raises is not None:
        conn.fetch = AsyncMock(side_effect=fetch_raises)
    else:
        conn.fetch = AsyncMock(return_value=rows or [])
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _patched_svc(
    api_key: str | None = "dt_test_key",
    api_key_raises: BaseException | None = None,
    post_return_map: dict[str, Any] | None = None,
):
    """Return a patched DevToCrossPostService constructor context.

    ``post_return_map`` maps post_id → str (URL) / None / Exception.
    """
    svc = MagicMock()
    if api_key_raises is not None:
        svc._get_api_key = AsyncMock(side_effect=api_key_raises)
    else:
        svc._get_api_key = AsyncMock(return_value=api_key)

    async def _cross_post(post_id: str) -> Any:
        outcome = (post_return_map or {}).get(post_id)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    svc.cross_post_by_post_id = AsyncMock(side_effect=_cross_post)
    return svc


class TestContract:
    def test_has_required_attrs(self):
        job = CrosspostToDevtoJob()
        assert job.name == "crosspost_to_devto"
        assert job.schedule == "every 4 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_missing_api_key_skips_work(self):
        pool, _ = _make_pool([])
        svc = _patched_svc(api_key="")
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ):
            job = CrosspostToDevtoJob()
            result = await job.run(pool, {})
        assert result.ok is True
        assert result.changes_made == 0
        assert "not configured" in result.detail
        # Must NOT hit the candidate fetch when no key present.
        svc.cross_post_by_post_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_api_key_lookup_failure_returns_not_ok(self):
        pool, _ = _make_pool([])
        svc = _patched_svc(api_key_raises=RuntimeError("db missing"))
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ):
            job = CrosspostToDevtoJob()
            result = await job.run(pool, {})
        assert result.ok is False
        assert "api key lookup failed" in result.detail

    @pytest.mark.asyncio
    async def test_no_candidates_returns_ok(self):
        pool, _ = _make_pool([])
        svc = _patched_svc()
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ):
            job = CrosspostToDevtoJob()
            result = await job.run(pool, {})
        assert result.ok is True
        assert result.changes_made == 0
        assert "already on Dev.to" in result.detail

    @pytest.mark.asyncio
    async def test_successful_crosspost_counts(self):
        pool, _ = _make_pool([
            {"id": "p1", "title": "T1", "slug": "slug-1"},
            {"id": "p2", "title": "T2", "slug": "slug-2"},
        ])
        svc = _patched_svc(
            post_return_map={
                "p1": "https://dev.to/g/slug-1",
                "p2": "https://dev.to/g/slug-2",
            },
        )
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ):
            job = CrosspostToDevtoJob()
            result = await job.run(pool, {})
        assert result.ok is True
        assert result.changes_made == 2
        assert result.metrics["posts_crossposted"] == 2
        assert result.metrics["errors"] == 0

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self):
        """One post posts ok, one returns None (no URL), one raises."""
        pool, _ = _make_pool([
            {"id": "p1", "title": "T1", "slug": "ok"},
            {"id": "p2", "title": "T2", "slug": "no-url"},
            {"id": "p3", "title": "T3", "slug": "errored"},
        ])
        svc = _patched_svc(
            post_return_map={
                "p1": "https://dev.to/g/ok",
                "p2": None,
                "p3": RuntimeError("rate-limited"),
            },
        )
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ):
            job = CrosspostToDevtoJob()
            result = await job.run(pool, {})
        assert result.ok is True
        assert result.changes_made == 1
        assert result.metrics["errors"] == 2

    @pytest.mark.asyncio
    async def test_batch_size_passthrough(self):
        pool, conn = _make_pool([])
        svc = _patched_svc()
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ):
            job = CrosspostToDevtoJob()
            await job.run(pool, {"batch_size": 10})
        args = conn.fetch.call_args.args
        assert args[1] == 10

    @pytest.mark.asyncio
    async def test_gitea_opt_in_when_errors(self):
        pool, _ = _make_pool([
            {"id": "p1", "title": "T", "slug": "bad"},
        ])
        svc = _patched_svc(
            post_return_map={"p1": RuntimeError("500 at dev.to")},
        )
        mock_gitea = AsyncMock(return_value=True)
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ), patch(
            "services.jobs.crosspost_to_devto.emit_finding",
            new=mock_gitea,
        ):
            job = CrosspostToDevtoJob()
            await job.run(pool, {"file_gitea_issue": True})
        mock_gitea.assert_called_once()

    @pytest.mark.asyncio
    async def test_gitea_default_is_opt_out(self):
        """Default is file_gitea_issue=False — Dev.to errors are usually transient."""
        pool, _ = _make_pool([
            {"id": "p1", "title": "T", "slug": "bad"},
        ])
        svc = _patched_svc(post_return_map={"p1": RuntimeError("rate")})
        mock_gitea = AsyncMock(return_value=True)
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ), patch(
            "services.jobs.crosspost_to_devto.emit_finding",
            new=mock_gitea,
        ):
            job = CrosspostToDevtoJob()
            await job.run(pool, {})  # omit file_gitea_issue
        mock_gitea.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetch_raises=RuntimeError("pool closed"))
        svc = _patched_svc()
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ):
            job = CrosspostToDevtoJob()
            result = await job.run(pool, {})
        assert result.ok is False
        assert "pool closed" in result.detail

    @pytest.mark.asyncio
    async def test_candidate_query_excludes_terminal_devto_status(self):
        """#397 + #404 — the SELECT must skip posts marked with EITHER
        ``devto_status = 'gave_up'`` (permanent reject) or
        ``devto_status = 'already_exists'`` (canonical URL already on
        Dev.to). Without the second filter the cron keeps re-asking
        Dev.to about the same canonical URL every 4 hours forever."""
        pool, conn = _make_pool([])
        svc = _patched_svc()
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ):
            job = CrosspostToDevtoJob()
            await job.run(pool, {})
        sql = conn.fetch.call_args.args[0]
        # Canonical filters present
        assert "devto_url" in sql
        # Both terminal devto_status sentinels excluded by the SELECT.
        assert "devto_status" in sql
        assert "gave_up" in sql
        assert "already_exists" in sql

    @pytest.mark.asyncio
    async def test_already_exists_url_counts_as_success_not_error(self):
        """#404 — when ``cross_post_by_post_id`` returns the canonical
        URL (already_exists path), the job treats it as a successful
        crosspost (bumps changes_made / posts_crossposted, NOT
        errors). Verifies the job's truthy-URL check works for the
        success-at-destination case without any job-side change."""
        pool, _ = _make_pool([
            {"id": "p1", "title": "T1", "slug": "already"},
        ])
        svc = _patched_svc(
            post_return_map={
                # Service returns the canonical URL for already_exists.
                "p1": "https://www.gladlabs.io/posts/already",
            },
        )
        with patch(
            "services.devto_service.DevToCrossPostService",
            return_value=svc,
        ):
            job = CrosspostToDevtoJob()
            result = await job.run(pool, {})
        assert result.ok is True
        assert result.changes_made == 1
        assert result.metrics["posts_crossposted"] == 1
        assert result.metrics["errors"] == 0
