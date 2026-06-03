"""Regression test for poindexter#556 — social auto-post pool threading.

`generate_and_distribute_social_posts(..., pool=None)` only loads + dispatches
the `publishing_adapters` rows when a pool is passed. All three publish_service
call sites omitted `pool=`, so `load_enabled_publishers(None)` returned `[]`
every time and the entire row-driven publishing path was inert (bluesky_main /
youtube_main total_runs=0). These tests lock the pool through the seam.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from services import publish_service


class _FakeBackgroundTasks:
    """Records the kwargs add_task() is invoked with (FastAPI BackgroundTasks
    stores the callable + kwargs without calling it)."""

    def __init__(self):
        self.recorded_kwargs: dict | None = None

    def add_task(self, _fn, **kwargs):
        self.recorded_kwargs = kwargs


def _site_config() -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": default
    return sc


def test_queue_social_distribution_threads_pool_via_background_tasks():
    bg = _FakeBackgroundTasks()
    publish_service._queue_social_distribution(
        bg,
        task={"title": "T", "topic": "T"},
        slug="my-slug",
        seo_description="desc",
        seo_keywords=["k1", "k2"],
        post_title="T",
        site_config=_site_config(),
        pool="POOL_SENTINEL",
    )
    assert bg.recorded_kwargs is not None, "add_task was never called"
    # The publishing pool MUST be forwarded — without it the
    # publishing_adapters dispatch loop never runs (#556).
    assert bg.recorded_kwargs.get("pool") == "POOL_SENTINEL"


def test_queue_social_distribution_accepts_pool_kwarg():
    # The seam must accept pool= at all (the call site in
    # publish_post_from_task passes db_service.pool). A missing param would
    # TypeError at the call site and silently land in the best-effort except.
    import inspect

    sig = inspect.signature(publish_service._queue_social_distribution)
    assert "pool" in sig.parameters
