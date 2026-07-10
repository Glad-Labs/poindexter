"""Bulk image rebuild for awaiting_approval drafts (spec 2026-07-09).

Re-plans every image (featured + inline) from the article text and
regenerates them, preferring generated images. Reuses the pipeline's own
image atoms; writes the result to the latest pipeline_versions row. Fail-loud
on stock fallback unless allow_stock (see rebuild_all_images / _gate).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from modules.content.atoms import (
    content_generate_images,
    content_inject_images,
    content_plan_image_markers,
)
from modules.content.stages.replace_inline_images import _try_image_gen, _try_pexels

logger = logging.getLogger(__name__)

_STATUS_SQL = "SELECT status FROM pipeline_tasks WHERE task_id = $1"
_TOPIC_SQL = "SELECT topic FROM pipeline_tasks WHERE task_id = $1"
_LATEST_SQL = (
    "SELECT content, version, featured_image_url FROM pipeline_versions "
    "WHERE task_id = $1 ORDER BY version DESC LIMIT 1"
)
_PERSIST_SQL = (
    "UPDATE pipeline_versions SET content = $1, featured_image_url = $2 "
    "WHERE task_id = $3 AND version = $4"
)
_BUMP_SQL = (
    "UPDATE pipeline_tasks SET regen_images_attempts = "
    "COALESCE(regen_images_attempts, 0) + 1 WHERE task_id = $1"
)
# An <img ...> tag plus an optional trailing <figcaption>…</figcaption> (Pexels).
_IMG_BLOCK_RE = re.compile(
    r"<img\b[^>]*>\s*(?:<figcaption>.*?</figcaption>)?",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class RebuildResult:
    task_id: str
    ok: bool
    detail: str
    inline_total: int = 0
    inline_generated: int = 0
    featured_source: str = "none"
    stock_slots: list[str] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)


class ImageRebuildService:
    """Rebuild all images on an awaiting_approval draft. Drafts only."""

    def __init__(
        self, *, pool: Any, site_config: Any = None, image_service: Any = None,
        database_service: Any = None, platform: Any = None,
    ) -> None:
        self._pool = pool
        self._site_config = site_config
        self._image_service = image_service
        self._db = database_service
        self._platform = platform

    async def rebuild_all_images(self, task_id: str, *, allow_stock: bool = False) -> RebuildResult:
        if self._image_service is None:
            raise RuntimeError("image service not available for rebuild")

        status = await self._pool.fetchval(_STATUS_SQL, task_id)
        if status != "awaiting_approval":
            raise ValueError(
                f"rebuild-images requires an awaiting_approval draft; "
                f"task {task_id} is {status!r}"
            )
        row = await self._pool.fetchrow(_LATEST_SQL, task_id)
        if not row:
            raise ValueError(f"no pipeline_versions row for task {task_id}")
        content = row["content"] or ""
        version = int(row["version"])
        topic = await self._pool.fetchval(_TOPIC_SQL, task_id) or ""

        stripped = _IMG_BLOCK_RE.sub("", content)

        plan_out = await content_plan_image_markers.run(
            {"content": stripped, "topic": topic, "site_config": self._site_config}
        )
        planned_content = plan_out.get("content", stripped)
        image_plans = plan_out.get("image_plans", [])
        featured_plan = plan_out.get("featured_image_plan") or {}

        gen_out = await content_generate_images.run(
            {"image_plans": image_plans, "topic": topic, "task_id": task_id,
             "post_id": None, "site_config": self._site_config,
             "image_service": self._image_service, "platform": self._platform}
        )
        image_results = gen_out.get("image_results", [])
        featured_url, featured_source = await self._gen_featured(featured_plan, topic, task_id)

        gate = self._gate(image_results, featured_url, featured_source, allow_stock)
        if gate is not None:
            gate.task_id = task_id
            return gate  # abort — draft unchanged

        inj_out = await content_inject_images.run(
            {"content": planned_content, "image_results": image_results,
             "task_id": task_id, "database_service": None}  # we persist to pipeline_versions ourselves
        )
        new_content = inj_out.get("content", planned_content)
        inline_generated = sum(1 for r in image_results if r.get("source") == "image_gen")

        await self._pool.execute(_PERSIST_SQL, new_content, featured_url, task_id, version)
        await self._pool.execute(_BUMP_SQL, task_id)
        await self._audit(task_id, image_results, featured_source, allow_stock)

        return RebuildResult(
            task_id, ok=True,
            detail=(f"rebuilt {len(image_results)} inline + featured "
                    f"({inline_generated} generated); draft updated (v{version})"),
            inline_total=len(image_results), inline_generated=inline_generated,
            featured_source=featured_source,
        )

    async def _gen_featured(self, featured_plan: dict, topic: str, task_id: str) -> tuple[str | None, str]:
        """Featured image via the same two-strategy as inline: image-gen then Pexels.

        ``_try_image_gen`` returns an already-uploaded R2 URL (or None); no separate
        upload step is needed.
        """
        desc = (featured_plan.get("prompt") if isinstance(featured_plan, dict) else "") or topic
        url = await _try_image_gen(
            "featured", desc, topic,
            site_config=self._site_config, task_id=task_id, platform=self._platform,
        )
        if url:
            return url, "image_gen"
        pex = await _try_pexels(desc, topic, self._image_service)
        if pex:
            return pex[0], "pexels"
        return None, "none"

    def _gate(self, image_results, featured_url, featured_source, allow_stock) -> RebuildResult | None:
        """Fail-loud gate. Returns an abort result, or None to proceed.

        - Any slot that produced NOTHING (url is None / featured None) always
          aborts — an empty slot cannot be persisted, even with --allow-stock.
        - Otherwise, any non-image_gen slot (Pexels stock) aborts unless
          allow_stock is set.
        (task_id on the abort result is stamped by the caller.)
        """
        empty = [f"inline:{r['num']}" for r in image_results if not r.get("url")]
        if featured_url is None:
            empty.append("featured")
        if empty:
            return RebuildResult(
                task_id="", ok=False,
                detail=(f"image generation produced nothing for {len(empty)} slot(s): "
                        f"{', '.join(empty)}. Check the image-gen server "
                        f"(image_gen_server_url); draft unchanged."),
                stock_slots=empty,
            )
        stock = [f"inline:{r['num']}" for r in image_results if r.get("source") != "image_gen"]
        if featured_source != "image_gen":
            stock.append("featured")
        if stock and not allow_stock:
            return RebuildResult(
                task_id="", ok=False,
                detail=(f"image-gen unavailable for {len(stock)} slot(s): "
                        f"{', '.join(stock)}. Refusing Pexels stock fallback "
                        f"(pass --allow-stock to accept). Draft unchanged."),
                stock_slots=stock,
            )
        return None

    async def _audit(self, task_id, image_results, featured_source, allow_stock) -> None:
        if self._platform is None:
            return
        await self._platform.audit.write(
            "post_images_rebuild",
            source="image_rebuild_service",
            details={
                "task_id": task_id,
                "inline_sources": [r.get("source") for r in image_results],
                "featured_source": featured_source,
                "allow_stock": allow_stock,
            },
            task_id=task_id,
            severity="info",
        )
