"""Regression test for the immutable-cache / task-id-keyed R2 collision bug.

``_upload_featured_to_r2`` previously keyed the R2 object purely off
``task_id`` (``images/featured/{task_id}.jpg``). ``r2_upload_service.py``
stamps every image upload with ``Cache-Control: public, max-age=31536000,
immutable`` on the assumption that a new image always gets a new key — true
for inline images (random UUID key) but false for the featured image, whose
key was stable per ``task_id``.

Confirmed in production: task fdb156b8-1dd6-492a-92e3-c70e8a755a20 ("Adoption
over Operational Ease") ran through the full canonical_blog pipeline 3 times
between 2026-07-07T22:00 and 2026-07-08T03:40, each time picking a different
random image style and re-uploading to the SAME R2 key/URL — overwriting the
previous image. Because the object is cached immutable for a year, different
clients could see different images at the identical URL.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_upload_featured_to_r2_uses_unique_key_per_call() -> None:
    """Two uploads for the same task_id must land on two different R2 keys.

    A shared key means a pipeline retry (QA-rewrite loop, stale-task
    reclaim, manual retry) silently overwrites a prior run's featured image
    at a URL that's cached immutable for a year.
    """
    from modules.content.stages.source_featured_image import (
        _upload_featured_to_r2,
    )

    task_id = "fdb156b8-1dd6-492a-92e3-c70e8a755a20"
    site_config = MagicMock()

    captured_keys: list[str] = []

    async def _fake_upload_to_r2(local_path, r2_key, content_type=None):
        captured_keys.append(r2_key)
        return f"https://images.gladlabs.io/{r2_key}"

    mock_svc = MagicMock()
    mock_svc.upload_to_r2 = AsyncMock(side_effect=_fake_upload_to_r2)

    with patch(
        "services.r2_upload_service.R2UploadService",
        return_value=mock_svc,
    ):
        await _upload_featured_to_r2(
            "/tmp/attempt1.jpg", task_id, site_config=site_config,
        )
        await _upload_featured_to_r2(
            "/tmp/attempt2.jpg", task_id, site_config=site_config,
        )

    assert len(captured_keys) == 2
    assert captured_keys[0] != captured_keys[1], (
        "_upload_featured_to_r2 produced the same R2 key on both calls "
        f"({captured_keys[0]!r}). The object is cached "
        "Cache-Control: immutable for a year, so a same-key retry "
        "silently overwrites the previous run's featured image at the "
        "same public URL (poindexter task fdb156b8… regenerated 3x with "
        "a different image each time, same URL)."
    )
