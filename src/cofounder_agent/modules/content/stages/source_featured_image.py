"""SourceFeaturedImageStage — stage 3 of the content pipeline.

Full port of ``_stage_source_featured_image`` from content_router_service.py
(no longer a thin wrapper). Tries image-gen editorial-illustration generation
first, falls back to a Pexels photo if image-gen is unavailable or fails.

## Strategy

1. **Early out** — if ``generate_featured_image`` is False in context,
   record the skip and return.
2. **image-gen editorial illustration** — the style is picked by a DB-driven
   rotation (``image_styles`` app_setting) that filters out the last 5
   published posts' styles and this worker's recent in-memory picks.
   An Ollama prompt build goes through first; image-gen server renders with
   the chosen style's palette/mood.
3. **R2 upload** — image-gen output uploads to R2 (replaced Cloudinary for
   cost reasons). Falls back to the worker's local serve path if R2
   fails.
4. **Pexels fallback** — if image-gen is unavailable, unreachable, or the
   pipeline errors at any point, the Pexels stock-photo search is used.
5. **Nothing found** — set ``stages["3_featured_image_found"] = False``
   and return None. Pipeline continues without a featured image.

## Context reads

- ``topic`` (str), ``tags`` (list[str])
- ``generate_featured_image`` (bool, default True)
- ``task_id`` (str)
- ``image_service`` (falls back to ``get_image_service()``)
- ``featured_image_prompt`` (optional — if set upstream, skips image-gen
  prompt generation and uses this verbatim)

## Context writes

- ``featured_image`` (GeneratedImage | Pexels image | None)
- ``featured_image_url`` (str)
- ``featured_image_alt`` (str, capped at 200 chars)
- ``featured_image_width``, ``featured_image_height`` (int)
- ``featured_image_photographer`` (str)
- ``featured_image_source`` (str: "image_gen_local" / "image_gen_cloudinary" / "pexels")
- ``image_style`` (str, set when image-gen's rotation picked one)
- ``featured_image_data`` (dict) — reproducibility metadata for the
  featured image: ``source``, ``provider_plugin``, ``width``, ``height``,
  ``photographer``, ``generated_at``, ``image_style``, ``topic``. For the
  image-gen branch this also captures ``image_gen_model``, ``image_gen_seed``,
  ``image_gen_prompt``, ``image_gen_negative_prompt``, ``image_gen_dimensions``,
  and ``generation_seconds`` so an operator can regenerate a similar image
  later. Lands on ``posts.featured_image_data`` via
  ``publish_service.publish_post_from_task``.
- ``stages["3_featured_image_found"]`` (bool)
- ``stages["3_image_source"]`` (str, "image_gen" or "pexels")
"""

from __future__ import annotations

import json
import logging
import os
import random
import tempfile
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


DEFAULT_STYLES: tuple[tuple[str, str], ...] = (
    (
        "flat vector illustration",
        "simple geometric shapes, cyan and dark navy, clean minimal, no text",
    ),
    (
        "cyberpunk neon style",
        "dark background, glowing cyan purple neon lines, futuristic, no text",
    ),
    (
        "isometric 3D illustration",
        "colorful clean technical, low angle, no text",
    ),
)


DEFAULT_NEGATIVE = (
    "text, words, letters, watermark, face, person, hands, blurry, "
    "low quality, distorted, ugly, deformed"
)


@dataclass
class GeneratedImage:
    """Return shape for an image-gen-generated featured image.

    ``gen_meta`` carries the reproducibility payload (model name,
    seed, prompt, negative prompt, dimensions, generation time) so
    callers can stash it on ``posts.featured_image_data`` for later
    regeneration / debugging. Empty dict on Pexels and on the
    image-bytes branch where the image-gen server didn't return JSON.
    """

    url: str
    photographer: str
    source: str
    gen_meta: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.gen_meta is None:
            self.gen_meta = {}


# ---------------------------------------------------------------------------
# featured_image_data composer
# ---------------------------------------------------------------------------


def _build_gen_featured_image_data(
    *,
    gen_image: GeneratedImage,
    gen_meta: dict[str, Any],
    topic: str,
    width: int,
    height: int,
    image_style: str,
) -> dict[str, Any]:
    """Compose the reproducibility blob persisted on posts.featured_image_data.

    The shape is intentionally flat and stable so operator-side queries
    (``WHERE featured_image_data->>'image_gen_model' = 'sdxl_lightning'``)
    don't need to know about nested envelopes. ``image_gen_*`` keys mirror
    the image-gen server's response fields; the prompt + negative_prompt
    were the ones the worker sent, not the ones the server echoed
    (the server doesn't echo them).
    """
    gen_ms = int(gen_meta.get("generation_time_ms") or 0)
    payload: dict[str, Any] = {
        "source": gen_image.source,
        "provider_plugin": f"image.{gen_image.source}",
        "width": int(width),
        "height": int(height),
        "photographer": gen_image.photographer,
        "topic": topic,
        "image_style": image_style or "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generation_seconds": round(gen_ms / 1000.0, 3) if gen_ms else 0.0,
        "image_gen_dimensions": [int(width), int(height)],
    }
    # Pull each image-gen response field if present. Missing fields stay
    # absent — never emit empty placeholders ("no mock/dummy data").
    if "model" in gen_meta:
        payload["image_gen_model"] = gen_meta["model"]
    if "seed" in gen_meta:
        payload["image_gen_seed"] = gen_meta["seed"]
    if gen_meta.get("prompt"):
        payload["image_gen_prompt"] = gen_meta["prompt"]
    if gen_meta.get("negative_prompt"):
        payload["image_gen_negative_prompt"] = gen_meta["negative_prompt"]
    if gen_meta.get("filename"):
        payload["image_gen_filename"] = gen_meta["filename"]
    return payload


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class SourceFeaturedImageStage:
    name = "source_featured_image"
    description = "Source a featured image — image-gen primary, Pexels fallback"
    timeout_seconds = 300
    halts_on_failure = False

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any],
    ) -> StageResult:
        from services.image_service import get_image_service

        topic = context.get("topic", "")
        tags = context.get("tags") or []
        generate_featured_image = bool(context.get("generate_featured_image", True))
        task_id = context.get("task_id")
        post_id = context.get("post_id")
        # DI seam (glad-labs-stack#330) — content_router_service seeds
        # site_config into the stage context.
        site_config = context.get("site_config")
        # Seam 1 Wave 3f (#667) — content's capability-scoped kernel handle,
        # threaded onto the stage context by the TemplateRunner (__services__
        # merge), exactly as the writer + inline-image atom receive it. It MUST
        # be forwarded into _try_image_gen_featured so the image-gen prompt build can run
        # the LLM; without it _build_image_gen_prompt raises "platform handle
        # required for dispatch" and the featured image falls back to the bare
        # style-only prompt (generic, not subject-specific — the 2026-06-18
        # production bug).
        platform = context.get("platform")

        stages = context.setdefault("stages", {})

        if not generate_featured_image:
            stages["3_featured_image_found"] = False
            logger.info("Image search skipped (disabled)")
            return StageResult(
                ok=True,
                detail="disabled via generate_featured_image flag",
                context_updates={"stages": stages, "featured_image": None},
            )

        # Build the image service only after the disabled-flag guard —
        # ``get_image_service`` requires a real ``site_config`` (#272
        # Phase-2e), seeded into the context by content_router_service.
        image_service = context.get("image_service") or get_image_service(
            site_config=site_config  # type: ignore[arg-type]
        )

        logger.info("STAGE 3: Sourcing featured image...")

        updates: dict[str, Any] = {"stages": stages}

        # Strategy 1: attempt the image-gen HTTP server.
        #
        # 2026-05-27 fix: the previous gate checked
        # ``image_service.gen_available or not image_service.gen_initialized``,
        # which is a leftover from the in-process diffusers era. The
        # worker container no longer installs the ``ml`` extras (diffusers
        # + torch + sentence-transformers moved out to dedicated
        # containers), so ``gen_available`` is permanently False here
        # and the gate skipped image-gen on every run — silently falling
        # back to Pexels.
        #
        # The real image-gen path goes through ``_try_image_gen_featured`` ->
        # ``_render_image_gen`` -> HTTP POST to ``image_gen_server_url``. That
        # path returns None gracefully on transport / 5xx errors, so the
        # gate adds no value beyond letting the operator switch image-gen off
        # via ``app_settings.image_gen_enabled``. Default behaviour is to
        # attempt image-gen; operators flip the setting to ``false`` only when
        # the image-gen server is intentionally down.
        image_gen_enabled = True
        if site_config is not None:
            try:
                raw = str(
                    site_config.get("image_gen_enabled", "true") or "true"
                ).strip().lower()
                image_gen_enabled = raw in ("true", "1", "yes", "on")
            except Exception:  # noqa: BLE001 — defensive
                image_gen_enabled = True
        image_gen_attempted = image_gen_enabled
        if image_gen_attempted:
            # Pull / lazily-create the style-rotation tracker. Production
            # keeps one long-lived instance per worker process (stashed on
            # app.state by lifespan); tests inject their own mock; absent
            # context just builds a fresh one here.
            style_tracker = context.get("image_style_tracker")
            if style_tracker is None:
                from services.image_style_rotation import ImageStyleTracker
                if site_config is not None:
                    style_tracker = ImageStyleTracker(
                        history_size=site_config.get_int("image_style_history_size", 10),
                        ttl_seconds=site_config.get_int("image_style_history_ttl_seconds", 3600),
                    )
                else:
                    style_tracker = ImageStyleTracker()

            # Capture the chosen style on the version-row update AND emit
            # an audit_log row so operators can verify rotation from
            # Grafana (see Mission Control "Image style mix" panel —
            # 2026-05-28). Per ``feedback_total_visibility``.
            async def _on_style(style: str) -> None:
                updates["image_style"] = style
                db = context.get("database_service")
                pool = getattr(db, "pool", None) if db else None
                if pool is None:
                    return
                try:
                    await pool.execute(
                        "INSERT INTO audit_log (timestamp, event_type, source, "
                        "task_id, details, severity) VALUES (now(), $1, $2, $3, $4::jsonb, $5)",
                        "image_style_picked",
                        "stages.source_featured_image",
                        task_id,
                        '{"style":"' + style.replace('"', '\\"') + '","topic":"' + topic.replace('"', '\\"')[:200] + '"}',
                        "info",
                    )
                except Exception:  # noqa: BLE001 — telemetry only
                    pass

            def _on_style_picked_sync(s: str) -> None:
                # The downstream API expects a sync callable. Schedule the
                # audit_log emit as a fire-and-forget asyncio task so the
                # render path stays non-blocking.
                import asyncio
                updates["image_style"] = s
                try:
                    asyncio.create_task(_on_style(s))
                except RuntimeError:
                    pass  # no running loop — tests/bootstrap

            gen_image = await _try_image_gen_featured(
                topic=topic,
                existing_prompt=context.get("featured_image_prompt", ""),
                task_id=task_id,
                on_style_picked=_on_style_picked_sync,
                style_tracker=style_tracker,
                site_config=site_config,
                platform=platform,
            )
            if gen_image is not None:
                stages["3_featured_image_found"] = True
                stages["3_image_source"] = "image_gen"
                # Prefer dimensions reported by the image-gen server (it can
                # return non-1024 sizes), fall back to the historical
                # default. ``gen_meta`` is empty on the legacy
                # image-bytes branch — same fallback applies there.
                gen_meta = gen_image.gen_meta or {}
                width = int(gen_meta.get("width") or 1024)
                height = int(gen_meta.get("height") or 1024)
                # featured_image_data — reproducibility blob landing on
                # posts.featured_image_data via publish_service.
                # Closes the dead seam from the 2026-05-19 jank-audit:
                # the column existed but nothing wrote to it.
                featured_image_data = _build_gen_featured_image_data(
                    gen_image=gen_image,
                    gen_meta=gen_meta,
                    topic=topic,
                    width=width,
                    height=height,
                    image_style=updates.get("image_style", ""),
                )
                updates.update({
                    "featured_image": gen_image,
                    "featured_image_url": gen_image.url,
                    "featured_image_alt": f"{topic} — AI generated illustration"[:200],
                    "featured_image_width": width,
                    "featured_image_height": height,
                    "featured_image_photographer": gen_image.photographer,
                    "featured_image_source": gen_image.source,
                    "featured_image_data": featured_image_data,
                    "stages": stages,
                })
                # Glad-Labs/poindexter#161: record media_assets row for
                # cleanup / retention / cost-attribution. Best-effort —
                # never breaks the Stage. We extend the metadata blob
                # with the image-gen reproducibility payload so media_assets
                # carries the same info as posts.featured_image_data
                # (one seam for retention-time queries, one seam for
                # operator-debug-time queries).
                await _record_featured_image_asset(
                    site_config=site_config,
                    post_id=post_id,
                    task_id=task_id,
                    public_url=gen_image.url,
                    width=width,
                    height=height,
                    provider_plugin=f"image.{gen_image.source}",
                    metadata={
                        "topic": topic,
                        "task_id": str(task_id or ""),
                        "photographer": gen_image.photographer,
                        "image_style": updates.get("image_style", ""),
                        **{
                            f"image_gen_{k}": v
                            for k, v in gen_meta.items()
                            if k in ("model", "seed", "prompt",
                                     "negative_prompt", "generation_time_ms")
                        },
                    },
                    # R2UploadService converts PNG→WebP at upload time (#732).
                    mime_type="image/webp",
                )
                logger.info("Featured image generated via image-gen + R2")
                return StageResult(
                    ok=True,
                    detail=f"image_gen: {gen_image.url[:60]}",
                    context_updates=updates,
                    metrics={"source": "image_gen"},
                )

        # Strategy 2: Pexels fallback.
        search_keywords = tags or [topic]
        try:
            pexels = await image_service.search_featured_image(
                topic=topic, keywords=search_keywords,
            )
            if pexels:
                stages["3_featured_image_found"] = True
                stages["3_image_source"] = "pexels"
                pexels_width = getattr(pexels, "width", 650)
                pexels_height = getattr(pexels, "height", 433)
                featured_image_data = {
                    "source": pexels.source,
                    "provider_plugin": "image.pexels",
                    "width": int(pexels_width),
                    "height": int(pexels_height),
                    "photographer": pexels.photographer,
                    "topic": topic,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
                updates.update({
                    "featured_image": pexels,
                    "featured_image_url": pexels.url,
                    "featured_image_alt": (
                        f"{topic} — Photo by {pexels.photographer} on Pexels"[:200]
                    ),
                    "featured_image_width": pexels_width,
                    "featured_image_height": pexels_height,
                    "featured_image_photographer": pexels.photographer,
                    "featured_image_source": pexels.source,
                    "featured_image_data": featured_image_data,
                    "stages": stages,
                })
                # Glad-Labs/poindexter#161 — same insert as the image-gen
                # branch above; Pexels images need a media_assets row
                # too so backfill scripts can find them.
                await _record_featured_image_asset(
                    site_config=site_config,
                    post_id=post_id,
                    task_id=task_id,
                    public_url=pexels.url,
                    width=getattr(pexels, "width", 650),
                    height=getattr(pexels, "height", 433),
                    provider_plugin="image.pexels",
                    metadata={
                        "topic": topic,
                        "task_id": str(task_id or ""),
                        "photographer": pexels.photographer,
                    },
                    mime_type="image/jpeg",
                )
                logger.info(
                    "Featured image found: %s (Pexels)", pexels.photographer,
                )
                return StageResult(
                    ok=True,
                    detail=f"pexels: {pexels.photographer}",
                    context_updates=updates,
                    metrics={"source": "pexels"},
                )
            stages["3_featured_image_found"] = False
            logger.warning("No featured image found for '%s'", topic)
        except Exception as e:
            logger.error("Image search failed: %s", e, exc_info=True)
            stages["3_featured_image_found"] = False

        updates["stages"] = stages
        updates.setdefault("featured_image", None)
        return StageResult(
            ok=True,
            detail="no image (image-gen unavailable + pexels returned none)",
            context_updates=updates,
            metrics={"source": "none"},
        )


# ---------------------------------------------------------------------------
# media_assets persistence (Glad-Labs/poindexter#161)
# ---------------------------------------------------------------------------


async def _record_featured_image_asset(
    *,
    site_config: Any,
    post_id: Any,
    task_id: Any,
    public_url: str,
    width: int,
    height: int,
    provider_plugin: str,
    metadata: dict[str, Any],
    mime_type: str,
) -> None:
    """Best-effort ``media_assets`` insert for the featured image.

    Wraps :func:`services.media_asset_recorder.record_media_asset` so
    the call site stays one line and never propagates DB errors out
    of the Stage. Used by both the image-gen and Pexels success branches.

    ``task_id`` is required as a kwarg because this stage runs BEFORE
    the post exists in the canonical_blog pipeline — ``post_id`` is
    typically ``None`` here, and the row needs the task_id so the
    publish path can back-stamp the FK once the post is created (see
    Glad-Labs/poindexter#193).
    """
    try:
        from services.media_asset_recorder import record_media_asset
    except Exception as exc:  # noqa: BLE001 — defensive import guard
        logger.debug(
            "[STAGE3] media_asset_recorder unavailable: %s", exc,
        )
        return
    pool = getattr(site_config, "_pool", None)
    storage_provider = (
        "cloudflare_r2"
        if public_url and public_url.startswith("http") and "r2" in public_url
        else ("local" if (public_url or "").startswith("/") else "external")
    )
    await record_media_asset(
        pool=pool,
        post_id=post_id,
        task_id=task_id,
        asset_type="featured_image",
        public_url=public_url,
        storage_path="",
        mime_type=mime_type,
        width=width,
        height=height,
        provider_plugin=provider_plugin,
        source="pipeline",
        storage_provider=storage_provider,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# image-gen: prompt building + rendering + R2 upload
# ---------------------------------------------------------------------------


async def _try_image_gen_featured(
    topic: str,
    existing_prompt: str,
    task_id: str | None,
    on_style_picked: Any,  # callable that records the chosen style
    style_tracker: Any,    # ImageStyleTracker instance
    *,
    site_config: Any = None,
    platform: Any = None,
) -> GeneratedImage | None:
    """Full image-gen path: pick style → build prompt → render → upload to R2.

    The returned ``GeneratedImage`` carries ``gen_meta`` populated with
    the prompt + negative_prompt actually sent to the image-gen server PLUS
    the response payload (model name, seed, generation_time_ms,
    dimensions). Empty when the image-gen server returns raw image bytes
    instead of JSON (legacy code path — current image-gen server always
    emits JSON).
    """
    # site_config is the DI seam (glad-labs-stack#330) — passed by execute().
    try:
        negative = (
            site_config.get("image_negative_prompt", DEFAULT_NEGATIVE)
            if site_config is not None else DEFAULT_NEGATIVE
        )
        img_gen_prompt = existing_prompt
        if not img_gen_prompt:
            img_gen_prompt = await _build_image_gen_prompt(
                topic, on_style_picked, style_tracker,
                site_config=site_config, platform=platform,
            )

        image_gen_url = (
            site_config.get("image_gen_server_url", "http://host.docker.internal:9836")
            if site_config is not None
            else "http://host.docker.internal:9836"
        )
        render_timeout = (
            site_config.get_int("image_render_timeout_seconds", 90)
            if site_config is not None else 90
        )
        gpu_model_label = (
            site_config.get("image_generation_model", "image_gen")
            if site_config is not None else "image_gen"
        )
        output_path, server_meta = await _render_image_gen(
            image_gen_url, img_gen_prompt, negative, task_id=task_id,
            timeout_seconds=render_timeout, gpu_model_label=gpu_model_label,
        )
        if output_path is None:
            return None

        image_url = await _upload_featured_to_r2(
            output_path, task_id, site_config=site_config,
        )
        source = "image_gen_cloudinary" if "cloudinary" in image_url else "image_gen_local"
        # Compose the reproducibility blob — prompt + negative come
        # from this function (the server doesn't echo them back), the
        # rest is the image-gen JSON response.
        gen_meta: dict[str, Any] = {
            "prompt": img_gen_prompt,
            "negative_prompt": negative,
        }
        gen_meta.update(server_meta or {})
        return GeneratedImage(
            url=image_url,
            photographer="AI Generated (image-gen)",
            source=source,
            gen_meta=gen_meta,
        )
    except Exception as e:
        logger.info("image-gen generation skipped (%s), falling back to Pexels", e)
        return None


def _resolve_image_prompt(key: str, **kwargs: Any) -> str:
    """Resolve an image-direction prompt via UnifiedPromptManager.

    Resolution order: Langfuse production override → ``image-generation`` skill
    YAML default. Operators tune the wording live (Langfuse, no restart) or via
    the skill — that's the "db/skill configurable" seam. Falls back to a
    deterministic, de-funnelled instruction if the prompt manager is
    unavailable (tests / bootstrap) or the key is missing, so a missing prompt
    never hard-fails the image stage. #image-zimage-and-variety.
    """
    try:
        from services.prompt_manager import get_prompt_manager

        return get_prompt_manager().get_prompt(key, **kwargs)
    except Exception as exc:  # noqa: BLE001 — prompt resolution is best-effort
        logger.debug(
            "[IMAGE] prompt %s resolution failed (%s); using fallback", key, exc,
        )
        style = kwargs.get("style", "")
        style_tags = kwargs.get("style_tags", "")
        subject = kwargs.get("topic") or kwargs.get("search_query") or ""
        return (
            f"Write a Stable Diffusion XL image prompt for a {style} illustration "
            f"depicting a concrete, specific scene about: {subject}. {style_tags}. "
            "Commit to the named art style; no people, no faces, no hands, no text. "
            "1-2 sentences. Output ONLY the prompt."
        )


async def _build_image_gen_prompt(
    topic: str,
    on_style_picked: Any,
    style_tracker: Any,
    *,
    site_config: Any = None,
    platform: Any = None,
) -> str:
    """Pick a rotation style + ask the LLM for an editorial prompt.

    The instruction wording lives in the ``image.featured_image`` skill
    prompt (UnifiedPromptManager: Langfuse override → skill YAML default),
    so it's tunable without a code edit. It receives the rotated ``style`` +
    ``style_tags`` and is written to depict a concrete subject *in* that
    style — not the old "evoke the FEELING / do not depict literally"
    funnel that collapsed every style into the same teal abstraction.
    #image-zimage-and-variety.
    """
    styles = _load_styles_from_settings(site_config) or list(DEFAULT_STYLES)

    recent = await _load_recent_published_styles(site_config)
    mem_recent = style_tracker.recent()
    all_recent = set(recent) | set(mem_recent)

    available = [s for s in styles if s[0] not in all_recent] or styles
    chosen_style, style_tags = random.choice(available)
    style_tracker.record(chosen_style)
    on_style_picked(chosen_style)

    prompt_model = (
        site_config.get("inline_image_prompt_model", "llama3:latest")
        if site_config is not None else "llama3:latest"
    )
    img_prompt = _resolve_image_prompt(
        "image.featured_image",
        topic=topic,
        style=chosen_style,
        style_tags=style_tags,
    )

    pool = getattr(site_config, "_pool", None) if site_config is not None else None
    if pool is None:
        # Tests / bootstrap — use the deterministic style+tags fallback so
        # the pipeline can still source a featured image without DB access.
        logger.debug(
            "[IMAGE] no DB pool on site_config; using fallback prompt",
        )
        return f"{chosen_style}, {style_tags}, no text, no faces"

    try:
        if platform is not None:
            result = await platform.dispatch.complete(
                pool=pool,
                messages=[{"role": "user", "content": img_prompt}],
                model=prompt_model,
                tier="standard",
                timeout_s=site_config.get_int("image_prompt_timeout_seconds", 90),
                temperature=site_config.get_float("image_prompt_temperature", 0.8),
                max_tokens=site_config.get_int("image_prompt_max_tokens", 150),
            )
        else:
            raise RuntimeError(
                "platform handle required for dispatch — check pipeline context threading"
            )
        prompt_text = (getattr(result, "text", "") or "").strip().strip('"')
        logger.info(
            "[IMAGE] Style: %s | image-gen prompt: %s", chosen_style, prompt_text[:80],
        )
        return prompt_text
    except Exception as e:
        logger.warning("[IMAGE] LLM prompt generation failed, using fallback: %s", e)
        return f"{chosen_style}, {style_tags}, no text, no faces"


def _load_styles_from_settings(site_config: Any = None) -> list[tuple[str, str]]:
    """Read app_settings.image_styles (JSON array of {scene, tags})."""
    if site_config is None:
        return []
    raw = site_config.get("image_styles", "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return [(s["scene"], s["tags"]) for s in parsed if "scene" in s and "tags" in s]


async def _load_recent_published_styles(site_config: Any = None) -> list[str]:
    """Fetch recently-published posts' image_style for cross-post dedup.

    Reads the chosen rotation style off ``featured_image_data->>'image_style'``
    (the image-gen reproducibility blob, reliably persisted on both finalize paths)
    and falls back to ``metadata->>'image_style'``. The window size is the
    ``image_style_dedup_window`` setting (default 5).

    History: this read ``metadata->>'image_style'`` exclusively, but that key
    was NULL on every published post (the finalize metadata builder never wrote
    it), so the query always returned [] and cross-post rotation was silently
    dead — the same style recurred across posts. ``featured_image_data`` carries
    ``image_style`` from ``_build_gen_featured_image_data`` and now persists via
    ``build_task_metadata``, so this read is populated going forward.
    #image-zimage-and-variety.

    2026-05-27 fix (Glad-Labs/poindexter#234): previously opened a
    raw ``asyncpg.connect`` on every image-stage run (one per published
    post). Under burst load the unbounded connections starved the
    Postgres connection budget. Now uses the lifespan pool attached to
    ``site_config._pool`` (set by ``SiteConfig.load`` +
    ``build_and_wire_for_subprocess``). Falls back to the raw connect
    path only when the pool isn't wired (tests, early-boot CLI).
    """
    if site_config is None:
        return []

    try:
        window = int(site_config.get("image_style_dedup_window", 5) or 5)
    except (TypeError, ValueError):
        window = 5
    if window <= 0:
        return []

    _QUERY = """
        SELECT COALESCE(
            featured_image_data->>'image_style',
            metadata->>'image_style'
        ) AS style
        FROM posts WHERE status = 'published'
        AND COALESCE(
            featured_image_data->>'image_style',
            metadata->>'image_style'
        ) IS NOT NULL
        ORDER BY published_at DESC LIMIT $1
    """

    pool = getattr(site_config, "_pool", None)
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(_QUERY, window)
            return [r["style"] for r in rows if r["style"]]
        except Exception:
            # Pool errors are recoverable — fall through to the
            # legacy raw-connect path rather than failing the stage.
            pass

    try:
        import asyncpg

        cloud_url = site_config.get("database_url", "")
        if not cloud_url:
            return []
        conn = await asyncpg.connect(cloud_url)
        try:
            rows = await conn.fetch(_QUERY, window)
            return [r["style"] for r in rows if r["style"]]
        finally:
            await conn.close()
    except Exception:
        return []


async def _render_image_gen(
    image_gen_url: str,
    img_gen_prompt: str,
    negative_prompt: str,
    task_id: str | None = None,
    *,
    timeout_seconds: float = 90.0,
    gpu_model_label: str = "image_gen",
) -> tuple[str | None, dict[str, Any]]:
    """Call the image-gen server, return (local_path, server_meta).

    ``gen_meta`` carries the response payload the image-gen server emits
    (``model``, ``seed``, ``generation_time_ms``, ``width``, ``height``,
    ``filename``) so the caller can persist it on
    ``posts.featured_image_data`` for later regeneration. Empty dict
    on the image-bytes branch (no JSON to parse) or on failure.

    ``timeout_seconds`` (the ``image_render_timeout_seconds`` setting) and
    ``gpu_model_label`` (the active ``image_generation_model``) are passed by
    the caller from ``site_config`` so neither the render cap nor the GPU-session
    attribution is hardcoded to a single model. #image-zimage-and-variety.
    """
    from services.gpu_scheduler import gpu

    async with gpu.lock(
        "image_gen", model=gpu_model_label,
        task_id=task_id, phase="featured_image",
    ):
        # Cap from image_render_timeout_seconds — headroom for a cold model
        # load (Z-Image ~21s) + render + upload.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=5.0)
        ) as client:
            resp = await client.post(
                f"{image_gen_url}/generate",
                json={
                    "prompt": img_gen_prompt,
                    "negative_prompt": negative_prompt,
                    # steps / guidance_scale are intentionally omitted: the image-gen
                    # server drives them from its per-model registry
                    # (sdxl_lightning=4/0, z_image_turbo=9/0). Hardcoding them
                    # here forced wrong values on a model swap (and was already
                    # clamped away for Lightning). #image-zimage-and-variety.
                },
                timeout=timeout_seconds,
            )

    if resp.status_code != 200:
        return None, {}

    return await _resolve_gen_featured_response(resp, image_gen_url=image_gen_url)


async def _resolve_gen_featured_response(
    resp: httpx.Response, *, image_gen_url: str,
) -> tuple[str | None, dict[str, Any]]:
    """Materialise the image-gen server's featured-image response on disk.

    Fetches bytes via ``GET <image_gen_url>/images/<filename>`` when image-gen
    returns JSON, rather than trusting the in-container path. The image-gen
    and worker containers both run as ``appuser`` with ephemeral
    in-container homes — the volume mount that was supposed to bridge
    them lands on ``/root/.poindexter/`` while the image-gen server writes
    to ``/home/appuser/.poindexter/``, so the worker never sees the
    file on disk. Closes Glad-Labs/poindexter#459.

    Returns ``(local_path, gen_meta)`` — ``gen_meta`` carries the
    JSON the image-gen server emitted on the JSON branch, empty dict on
    the image-bytes branch.
    """
    ct = resp.headers.get("content-type", "")
    if ct.startswith("application/json"):
        data = resp.json()
        filename = data.get("filename") or os.path.basename(
            data.get("image_path", "") or "",
        )
        gen_ms = data.get("generation_time_ms", 0)
        if not filename:
            logger.warning(
                "[IMAGE] image-gen returned JSON without filename / image_path",
            )
            return None, {}
        try:
            output_path = await _download_featured_gen_image(
                image_gen_url, filename,
            )
        except Exception as e:
            logger.warning(
                "[IMAGE] image-gen /images fetch failed for %s: %s", filename, e,
            )
            return None, {}
        logger.info(
            "[IMAGE] Featured image-gen generated: %s (%dms)",
            os.path.basename(output_path), gen_ms,
        )
        # The image-gen server's GenerateResponse pins ``model``,
        # ``generation_time_ms``, ``seed``, ``width``, ``height``. Capture
        # all of them so reproducibility doesn't depend on which fields
        # the operator later regenerates from.
        meta = {
            "filename": filename,
            "generation_time_ms": int(gen_ms or 0),
        }
        for key in ("model", "seed", "width", "height"):
            if key in data:
                meta[key] = data[key]
        return output_path, meta
    if ct.startswith("image/"):
        return _write_featured_bytes_to_tempfile(resp.content), {}
    return None, {}


def _featured_generated_images_dir() -> str:
    """Worker-local directory for materialised image-gen bytes.

    Matches the path fragment that ``_upload_featured_to_r2``'s
    local-fallback URL rewrite keys on (``/glad-labs-generated-images/``),
    so the local-serve URL still resolves when R2 is unavailable.
    """
    output_dir = os.path.join(
        os.path.expanduser("~"), "Downloads", "glad-labs-generated-images",
    )
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def _write_featured_bytes_to_tempfile(content: bytes) -> str:
    """Persist image bytes to the worker-local generated-images dir."""
    with tempfile.NamedTemporaryFile(
        suffix=".png", delete=False, dir=_featured_generated_images_dir(),
    ) as tmp:
        tmp.write(content)
        return tmp.name


async def _download_featured_gen_image(image_gen_url: str, filename: str) -> str:
    """GET the bytes from the image-gen server's ``/images/<filename>``.

    The image-gen server already serves its outputs over HTTP (see
    ``scripts/image-gen-server.py``'s ``GET /images/{filename}``), so the
    worker no longer needs the image-gen container's filesystem to be
    mounted in.
    """
    safe_name = os.path.basename(filename)
    url = f"{image_gen_url.rstrip('/')}/images/{safe_name}"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
    ) as client:
        get_resp = await client.get(url)
    if get_resp.status_code != 200:
        raise RuntimeError(
            f"image-gen /images returned {get_resp.status_code} for {safe_name}",
        )
    return _write_featured_bytes_to_tempfile(get_resp.content)


async def _upload_featured_to_r2(
    output_path: str, task_id: str | None, *, site_config: Any = None,
) -> str:
    """Upload the featured image to R2 and return the final URL."""
    try:
        from services.r2_upload_service import R2UploadService
        if site_config is None:
            raise RuntimeError(
                "R2 upload requires site_config; stage execute() must "
                "thread site_config from context (GH#95 / DI PR 4)",
            )
        svc = R2UploadService(site_config=site_config)
        r2_id = task_id or uuid.uuid4().hex[:12]
        # R2UploadService converts JPEG/PNG→WebP and rewrites the key
        # extension automatically (poindexter#732).
        r2_key = f"images/featured/{r2_id}.jpg"
        r2_url = await svc.upload_to_r2(output_path, r2_key, content_type="image/jpeg")
        if r2_url:
            logger.info("Uploaded to R2: %s", r2_url[:80])
            with suppress(OSError):
                os.remove(output_path)  # best-effort cleanup
            return r2_url
        logger.warning("R2 upload returned None, using local path")
    except Exception as e:
        logger.warning("R2 upload failed (using local): %s", e)

    # Rewrite local-dir paths to the worker's serve URL.
    if (
        "/glad-labs-generated-images/" in output_path
        and not output_path.startswith("http")
    ):
        return f"/images/generated/{os.path.basename(output_path)}"
    return output_path
