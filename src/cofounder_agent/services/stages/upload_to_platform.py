"""UploadToPlatformStage — fan out finished videos to PublishAdapters.

Step 11 (final) of the video pipeline (Glad-Labs/poindexter#143). Closes
the V0 Stage chain::

    script → scene_visuals → tts
        → stitch_long_form / stitch_short_form
        → THIS Stage → done

This Stage takes the rendered ``video_outputs.long_form`` and
``video_outputs.short_form`` files produced by the upstream stitch
Stages, discovers every registered :class:`PublishAdapter`, and fans
the matching format out to each adapter that:

- has ``supports_long=True`` (for the long-form file) OR
  ``supports_short=True`` (for the short-form file);
- is enabled in app_settings (per the adapter's own gating discipline);
- has its OAuth secrets seeded (per the adapter's own gating).

Adapters that aren't enabled / aren't credentialed return
``PublishResult(success=False, ...)`` per their own discipline — this
Stage simply records the failure and moves on. Until
Glad-Labs/poindexter#40 lands every fan-out is currently a no-op
recording the gating error; that's expected, the chain is end-to-end
testable today.

## Robustness

- A single adapter raising never aborts the fan-out — every
  ``await adapter.publish(...)`` is wrapped in try/except.
- DB errors when updating ``platform_video_ids`` never abort the Stage —
  the upload already succeeded; we log warn and continue.
- No site_config / no video_outputs / no enabled adapters → ``ok=False``
  with informative ``detail``. Never silently no-ops.

## Context reads

- ``site_config`` — DI seam.
- ``video_outputs`` (dict) — at least one of ``long_form`` /
  ``short_form`` must contain ``output_path``.
- ``post_id`` (UUID, optional) — used to look up title/excerpt/
  seo_description from the ``posts`` table.
- ``title`` / ``content`` / ``tags`` (optional) — fallbacks when the
  posts row can't be found.

## Context writes

- ``video_publish_results`` (dict) — keyed by adapter name::

      {
          "youtube": {
              "success": bool,
              "external_id": str | None,
              "public_url": str | None,
              "status": str,
              "error": str | None,
              "format": "long_form" | "short_form",
          },
          ...
      }

- ``stages["video.upload"]`` (bool)

## media_assets side-effect

For each successful upload the Stage UPDATEs the matching
``media_assets`` row's ``platform_video_ids`` JSONB column to merge in
``{adapter.name: external_id}`` so the dashboard / future status-poll
Stage can find the platform-side asset by ``post_id``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from plugins.publish_adapter import PublishResult
from plugins.stage import StageResult

logger = logging.getLogger(__name__)


_DESCRIPTION_CAP = 4900  # leave headroom under YouTube's 5000-char limit
_FALLBACK_DESCRIPTION_CAP = 200


def _empty_result_dict(
    *,
    platform: str,
    fmt: str,
    success: bool,
    error: str | None,
) -> dict[str, Any]:
    """Build the context_updates entry for a skipped / failed adapter call."""
    return {
        "success": success,
        "external_id": None,
        "public_url": None,
        "status": "",
        "error": error,
        "format": fmt,
        "platform": platform,
    }


def _result_to_dict(result: PublishResult, fmt: str) -> dict[str, Any]:
    """Serialize a :class:`PublishResult` for the context payload."""
    return {
        "success": bool(result.success),
        "external_id": result.external_id,
        "public_url": result.public_url,
        "status": result.status or "",
        "error": result.error,
        "format": fmt,
        "platform": result.platform or "",
    }


async def _load_post_metadata(
    *,
    pool: Any,
    post_id: Any,
) -> dict[str, str]:
    """Fetch title / excerpt / seo_description from the ``posts`` table.

    Returns an empty dict on any failure — caller falls back to context-
    supplied values.
    """
    if pool is None or post_id is None:
        return {}
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT title, excerpt, seo_description FROM posts WHERE id = $1",
                post_id,
            )
    except Exception as exc:
        logger.warning(
            "[video.upload] posts row lookup failed for %s: %s",
            post_id, exc,
        )
        return {}
    if row is None:
        return {}
    return {
        "title": str(row["title"] or ""),
        "excerpt": str(row["excerpt"] or ""),
        "seo_description": str(row["seo_description"] or ""),
    }


def _build_publish_payload(
    *,
    post_meta: dict[str, str],
    context: dict[str, Any],
) -> tuple[str, str, list[str]]:
    """Pick title / description / tags from posts row + context fallbacks."""
    title = (
        post_meta.get("title")
        or str(context.get("title") or "")
    ).strip()

    description = (
        post_meta.get("excerpt")
        or post_meta.get("seo_description")
        or ""
    ).strip()
    if not description:
        # Fall back to a slice of the body content. Cap tighter than the
        # platform limit because content body tends to start with
        # boilerplate / frontmatter that's not useful as a description.
        body = str(context.get("content") or "").strip()
        description = body[:_FALLBACK_DESCRIPTION_CAP]
    description = description[:_DESCRIPTION_CAP]

    raw_tags = context.get("tags") or []
    tags: list[str] = []
    if isinstance(raw_tags, list):
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]

    return title, description, tags


async def _update_platform_video_ids(
    *,
    pool: Any,
    media_asset_id: Any,
    platform: str,
    external_id: str,
) -> None:
    """Merge ``{platform: external_id}`` into the row's JSONB column.

    Best-effort — DB failures must NEVER abort the Stage; the upload
    already succeeded, the operator just won't see the cross-reference
    in the dashboard until they re-run / repair.
    """
    if pool is None or not media_asset_id or not platform or not external_id:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE media_assets
                   SET platform_video_ids =
                       COALESCE(platform_video_ids, '{}'::jsonb)
                       || $2::jsonb
                 WHERE id = $1
                """,
                media_asset_id,
                json.dumps({platform: external_id}),
            )
    except Exception as exc:
        logger.warning(
            "[video.upload] platform_video_ids UPDATE failed "
            "(asset=%s, platform=%s): %s",
            media_asset_id, platform, exc,
        )


def _discover_publish_adapters() -> list[Any]:
    """Discover registered PublishAdapter instances from the registry.

    Returns an empty list when the registry can't be imported or the
    entry-point group hasn't been installed yet — caller treats that as
    "no adapters enabled" and surfaces a clear ok=False detail.
    """
    try:
        from plugins.registry import get_publish_adapters
    except Exception as exc:
        logger.warning("[video.upload] registry import failed: %s", exc)
        return []
    try:
        return list(get_publish_adapters())
    except Exception as exc:
        logger.warning("[video.upload] adapter discovery failed: %s", exc)
        return []


class UploadToPlatformStage:
    """Fan out long-form + short-form video to enabled PublishAdapters."""

    name = "video.upload"
    description = "Fan out long-form + short-form video to enabled PublishAdapters"
    timeout_seconds = 1800  # large videos take time to upload
    halts_on_failure = False  # one platform failing must not kill the others

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],  # pyright: ignore[reportUnusedParameter]
    ) -> StageResult:
        site_config = context.get("site_config")
        if site_config is None:
            return StageResult(
                ok=False,
                detail="site_config missing on context",
                metrics={"skipped": True},
            )

        outputs = context.get("video_outputs") or {}
        long_form = outputs.get("long_form") or {}
        short_form = outputs.get("short_form") or {}
        if not long_form and not short_form:
            return StageResult(
                ok=False,
                detail=(
                    "no video_outputs on context — upstream stitch_long_form "
                    "/ stitch_short_form Stages produced nothing to upload"
                ),
                metrics={"skipped": True},
            )

        adapters = _discover_publish_adapters()
        if not adapters:
            return StageResult(
                ok=False,
                detail=(
                    "no adapters enabled — see Glad-Labs/poindexter#40 "
                    "(no PublishAdapter entry-points discovered)"
                ),
                metrics={"skipped": True, "adapter_count": 0},
            )

        pool = getattr(site_config, "_pool", None)
        post_id = context.get("post_id")
        post_meta = await _load_post_metadata(pool=pool, post_id=post_id)
        title, description, tags = _build_publish_payload(
            post_meta=post_meta,
            context=context,
        )
        task_id = str(context.get("task_id") or "untagged")

        publish_results: dict[str, dict[str, Any]] = {}
        attempted = 0
        succeeded = 0
        failed = 0
        skipped = 0

        # Pair each adapter with the format(s) it supports. An adapter
        # that supports both long and short uploads twice — once per
        # finished file. Order: long first (the primary deliverable),
        # short second.
        for adapter in adapters:
            platform_name = str(getattr(adapter, "name", "") or "unknown")
            supports_long = bool(getattr(adapter, "supports_long", False))
            supports_short = bool(getattr(adapter, "supports_short", False))

            format_pairs: list[tuple[str, dict[str, Any]]] = []
            if supports_long and long_form:
                format_pairs.append(("long_form", long_form))
            if supports_short and short_form:
                format_pairs.append(("short_form", short_form))

            if not format_pairs:
                # Capability mismatch — don't even count it as
                # attempted. Record so the operator can see *why* this
                # adapter wasn't called.
                skipped += 1
                publish_results[platform_name] = _empty_result_dict(
                    platform=platform_name,
                    fmt="",
                    success=False,
                    error=(
                        "skipped — adapter capability mismatch "
                        f"(supports_long={supports_long}, "
                        f"supports_short={supports_short}, "
                        f"long_form_present={bool(long_form)}, "
                        f"short_form_present={bool(short_form)})"
                    ),
                )
                continue

            for fmt, fmt_outputs in format_pairs:
                key = (
                    platform_name
                    if len(format_pairs) == 1
                    else f"{platform_name}:{fmt}"
                )

                media_path = str(fmt_outputs.get("output_path") or "")
                if not media_path or not os.path.exists(media_path):
                    skipped += 1
                    publish_results[key] = _empty_result_dict(
                        platform=platform_name,
                        fmt=fmt,
                        success=False,
                        error=(
                            f"skipped — output_path missing or not on disk: "
                            f"{media_path!r}"
                        ),
                    )
                    continue

                attempted += 1
                try:
                    result = await adapter.publish(
                        media_path=media_path,
                        title=title,
                        description=description,
                        tags=tags,
                        # Pass DI/auxiliary kwargs adapters may use for
                        # cost-guard wiring + observability.
                        _site_config=site_config,
                        _pool=pool,
                        task_id=task_id,
                        phase=f"publish.{fmt}",
                    )
                except Exception as exc:
                    failed += 1
                    logger.exception(
                        "[video.upload] adapter %s raised on %s",
                        platform_name, fmt,
                    )
                    publish_results[key] = _empty_result_dict(
                        platform=platform_name,
                        fmt=fmt,
                        success=False,
                        error=f"{type(exc).__name__}: {str(exc)[:500]}",
                    )
                    continue

                # Record the outcome shape regardless of success/failure.
                payload = _result_to_dict(result, fmt)
                publish_results[key] = payload

                if result.success and result.external_id:
                    succeeded += 1
                    # Wire the platform-side ID back to media_assets so
                    # the dashboard / future status-poll Stage can find
                    # the asset by post_id.
                    await _update_platform_video_ids(
                        pool=pool,
                        media_asset_id=fmt_outputs.get("media_asset_id"),
                        platform=platform_name,
                        external_id=result.external_id,
                    )
                else:
                    failed += 1

        stages = context.setdefault("stages", {})
        # Stage is "ok" if at least one upload succeeded. A run with all
        # adapters gating-failed (no OAuth yet) is still ok=False so the
        # operator sees the gating message in the pipeline UI.
        ok = succeeded > 0
        stages[self.name] = ok

        if attempted == 0:
            detail = (
                "no adapters enabled — every discovered PublishAdapter "
                "was skipped (capability mismatch or missing output_path). "
                "See Glad-Labs/poindexter#40 for OAuth setup."
            )
        elif succeeded == 0:
            detail = (
                f"all {attempted} upload attempts failed "
                f"(skipped={skipped}); see video_publish_results for "
                "per-adapter errors"
            )
        else:
            detail = (
                f"{succeeded}/{attempted} uploads succeeded "
                f"(failed={failed}, skipped={skipped})"
            )

        return StageResult(
            ok=ok,
            detail=detail,
            context_updates={
                "video_publish_results": publish_results,
                "stages": stages,
            },
            metrics={
                "adapter_count": len(adapters),
                "attempted": attempted,
                "succeeded": succeeded,
                "failed": failed,
                "skipped": skipped,
            },
        )
