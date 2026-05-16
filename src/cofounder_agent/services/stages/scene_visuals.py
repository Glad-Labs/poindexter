"""SceneVisualsStage — pick (or generate) one image per video scene.

Strategy-driven per Matt's directive: "we want flexibility — try
everything and see what works." The ``video_scene_visuals_strategy``
setting picks the policy at runtime; per-scene visual_prompt
content can also bias the choice when the strategy is ``"mixed"``.

Strategies:

- ``"reuse_first"`` (default) — query :class:`media_assets` for images
  attached to the same post first, score by token-overlap with the
  scene's visual_prompt, pick the best match if it clears
  ``video_scene_visuals_reuse_threshold``. Fall through to the
  configured fresh source on a miss.
- ``"pexels"`` — always pull a stock photo from PexelsProvider. Free
  tier, fast, broad coverage. Best when the post is talking about
  concrete physical things.
- ``"sdxl"`` — always synthesize via SDXL. Best when the post is
  abstract / conceptual / brand-specific.
- ``"mixed"`` — rotate per scene to keep visual variety. Defaults to
  ``[reuse_first, pexels, sdxl]`` round-robin; first non-empty wins.

The Stage runs scenes sequentially (Pexels rate limits are generous
but SDXL can OOM if we stack 12 jobs). Bounded concurrency is left
for a follow-up once we have real numbers.

## Context reads

- ``video_script`` (dict) — output of ``ScriptForVideoStage``
- ``post_id`` (str / UUID) — used for media_assets reuse lookups
- ``site_config`` — DI seam

## Context writes

- ``video_scene_visuals`` (dict) — see :func:`_default_visuals` for shape:
  ``{"long_form": [<per-scene resolution>...], "short_form": [...]}``
- ``stages["video.scene_visuals"]`` (bool)

Each per-scene resolution is::

    {
        "scene_idx": int,
        "source": "media_assets" | "pexels" | "sdxl",
        "clip_path": str,            # local absolute path
        "url": str,                  # remote URL (if any)
        "reused": bool,              # True when source == "media_assets"
        "search_terms": str,         # what we queried with
        "metadata": dict[str, Any],  # provider-specific extras
    }
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from typing import Any
from uuid import uuid4

import httpx

from plugins.stage import StageResult

logger = logging.getLogger(__name__)


_DEFAULT_STRATEGY = "reuse_first"
_DEFAULT_REUSE_THRESHOLD = 0.20  # fraction of unique visual_prompt tokens that must match
_DEFAULT_DOWNLOAD_TIMEOUT_S = 30.0
# Default cap of 1 preserves the original sequential behavior — bumping the
# knob is the operator's explicit opt-in. SDXL is the bottleneck; on a 5090
# with VRAM headroom two parallel generations is the natural next step. See
# Glad-Labs/poindexter#164 for the timing-driven rollout plan.
_DEFAULT_MAX_CONCURRENT = 1
_TMP_PREFIX = "poindexter_scene_"

# Stop-words excluded from the keyword overlap score. Kept tiny to
# keep _score_match() fast; Postgres full-text dictionaries are the
# right answer if this ever grows.
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "in", "on", "at", "for",
    "to", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "this", "that", "these", "those", "it", "its", "no", "not", "do",
    "does", "did", "have", "has", "had", "we", "you", "your", "our",
    "they", "their", "i", "me", "my", "cinematic", "lighting", "no",
    "people", "text", "faces", "hands", "4k", "photorealistic",
})


def _default_visuals() -> dict[str, Any]:
    """Empty/default shape returned when there's no work to do."""
    return {
        "long_form": [],
        "short_form": [],
        # Dedicated hero visuals for the intro / outro bookends.
        # Always Pexels stock photos for V0 — keyed off post title /
        # intro_hook / outro_cta. Never reused for body scenes, so the
        # bookend doesn't dupe an image that plays moments later.
        "intro_clip_path": "",
        "outro_clip_path": "",
    }


def _tokenize(text: str) -> set[str]:
    """Lowercase tokenization with stop-word + length filtering."""
    if not text:
        return set()
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", text.lower())
    return {tok for tok in raw if len(tok) >= 4 and tok not in _STOP_WORDS}


def _score_match(visual_prompt: str, candidate_text: str) -> float:
    """Token-overlap score in [0, 1] for reuse_first ranking.

    Compares the unique-token sets — gives a candidate that mentions 4
    of the prompt's 10 distinctive words a 0.4 score. Cheap and
    deterministic; we'll swap to embedding cosine when media_assets
    grows an embedding column.
    """
    prompt_tokens = _tokenize(visual_prompt)
    if not prompt_tokens:
        return 0.0
    cand_tokens = _tokenize(candidate_text)
    if not cand_tokens:
        return 0.0
    matched = prompt_tokens & cand_tokens
    return len(matched) / len(prompt_tokens)


class SceneVisualsStage:
    """Pick one image per video scene — reuse, search, or generate."""

    name = "video.scene_visuals"
    description = "Choose (or generate) one image per scene per Matt's strategy"
    timeout_seconds = 600  # Up to 16 SDXL renders worst-case
    halts_on_failure = False  # individual scene misses are non-fatal

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
                context_updates={"video_scene_visuals": _default_visuals()},
                metrics={"skipped": True},
            )

        script = context.get("video_script") or {}
        long_form_scenes = ((script.get("long_form") or {}).get("scenes")) or []
        short_form_scenes = ((script.get("short_form") or {}).get("scenes")) or []
        if not long_form_scenes and not short_form_scenes:
            return StageResult(
                ok=False,
                detail="no scenes in video_script — script_for_video upstream?",
                context_updates={"video_scene_visuals": _default_visuals()},
                metrics={"skipped": True},
            )

        strategy = str(
            site_config.get(
                "video_scene_visuals_strategy", _DEFAULT_STRATEGY,
            ),
        )
        threshold = float(
            site_config.get(
                "video_scene_visuals_reuse_threshold",
                _DEFAULT_REUSE_THRESHOLD,
            ),
        )

        post_id = context.get("post_id")
        pool = getattr(site_config, "_pool", None)

        # poindexter#164: SDXL fan-out used to be strictly sequential. A
        # semaphore lets operators with VRAM headroom resolve scenes in
        # parallel without risking OOM. Default 1 preserves the prior
        # one-at-a-time behavior; raising it requires an explicit
        # app_settings flip. Clamp to >=1 so a misconfigured 0/-1 doesn't
        # deadlock the stage.
        max_concurrent = max(
            1,
            site_config.get_int(
                "video_scene_visuals_max_concurrent",
                _DEFAULT_MAX_CONCURRENT,
            ),
        )
        sem = asyncio.Semaphore(max_concurrent)
        task_id_for_audit = context.get("task_id") or context.get("post_id")

        visuals: dict[str, Any] = _default_visuals()
        counts: dict[str, int] = {
            "media_assets": 0, "pexels": 0, "sdxl": 0, "wan": 0, "miss": 0,
        }

        # Long-form, then short-form. Within each kind, scenes resolve
        # concurrently (bounded by ``max_concurrent``); ``asyncio.gather``
        # preserves input order in the returned list so visuals[kind] stays
        # scene-indexed regardless of completion order.
        for kind, scenes in (
            ("long_form", long_form_scenes),
            ("short_form", short_form_scenes),
        ):
            if not scenes:
                continue
            tasks = [
                self._resolve_scene_bounded(
                    sem=sem,
                    scene_idx=idx,
                    scene=scene,
                    strategy=strategy,
                    threshold=threshold,
                    post_id=post_id,
                    pool=pool,
                    site_config=site_config,
                    is_short=(kind == "short_form"),
                    rotation_idx=idx,
                    kind=kind,
                    task_id_for_audit=task_id_for_audit,
                )
                for idx, scene in enumerate(scenes)
            ]
            resolutions = await asyncio.gather(*tasks)
            for resolution in resolutions:
                visuals[kind].append(resolution)
                source = resolution.get("source") or "miss"
                counts[source] = counts.get(source, 0) + 1

        # Dedicated bookend visuals — fetched separately so the intro
        # and outro never reuse a body scene's image. Keyed off the
        # script's hook / CTA text (or the post title as a fallback)
        # so they're topically related but visually distinct from the
        # body imagery.
        #
        # gh#163 — when intro_hook + outro_cta are both empty (or both
        # collapse to the post title), intro/outro can pick the same
        # Pexels top-hit AND that hit can match a body-scene image.
        # Build a seen_urls set from body visuals + the chosen intro
        # so each bookend pick is genuinely distinct.
        long_form = script.get("long_form") or {}
        intro_query = (
            str(long_form.get("intro_hook") or "")
            or str(context.get("title") or "")
        )
        outro_query = (
            str(long_form.get("outro_cta") or "")
            or str(context.get("title") or "")
        )
        seen_urls: set[str] = {
            str(v.get("url"))
            for v in visuals["long_form"] + visuals["short_form"]
            if v.get("url")
        }
        intro_clip = (
            await _try_pexels(intro_query, site_config, seen_urls=seen_urls)
            if intro_query else None
        )
        if intro_clip:
            visuals["intro_clip_path"] = intro_clip["clip_path"]
            counts["pexels"] += 1
            if intro_clip.get("url"):
                seen_urls.add(intro_clip["url"])
        outro_clip = (
            await _try_pexels(outro_query, site_config, seen_urls=seen_urls)
            if outro_query else None
        )
        if outro_clip:
            visuals["outro_clip_path"] = outro_clip["clip_path"]
            counts["pexels"] += 1

        stages = context.setdefault("stages", {})
        long_count = sum(
            1 for v in visuals["long_form"] if v.get("clip_path")
        )
        short_count = sum(
            1 for v in visuals["short_form"] if v.get("clip_path")
        )
        # Stage is "ok" if at least the long-form was fully resolved;
        # short-form can degrade — stitch_short_form will skip
        # scenes without visuals.
        ok = long_count == len(long_form_scenes)
        stages[self.name] = ok

        return StageResult(
            ok=ok,
            detail=(
                f"long_form: {long_count}/{len(long_form_scenes)}, "
                f"short_form: {short_count}/{len(short_form_scenes)}, "
                f"sources: {counts}"
            ),
            context_updates={
                "video_scene_visuals": visuals,
                "stages": stages,
            },
            metrics={
                "strategy": strategy,
                "long_form_resolved": long_count,
                "short_form_resolved": short_count,
                "by_source": dict(counts),
            },
        )

    async def _resolve_scene_bounded(
        self,
        *,
        sem: asyncio.Semaphore,
        scene_idx: int,
        scene: dict[str, Any],
        strategy: str,
        threshold: float,
        post_id: Any,
        pool: Any,
        site_config: Any,
        is_short: bool,
        rotation_idx: int,
        kind: str,
        task_id_for_audit: Any,
    ) -> dict[str, Any]:
        """Resolve one scene under the shared concurrency semaphore.

        Wraps ``_resolve_scene`` so the outer ``asyncio.gather`` can fan
        every scene out at once while only ``sem._value`` of them run
        body-work concurrently. Also captures per-scene wall-clock and
        emits one ``video.scene_visual_resolved`` audit_log row per scene
        — the timing data is what makes Glad-Labs/poindexter#164's
        "increase the cap if VRAM headroom permits" decision possible.
        """
        async with sem:
            started_at = time.monotonic()
            resolution = await self._resolve_scene(
                scene_idx=scene_idx,
                scene=scene,
                strategy=strategy,
                threshold=threshold,
                post_id=post_id,
                pool=pool,
                site_config=site_config,
                is_short=is_short,
                rotation_idx=rotation_idx,
            )
            elapsed_s = time.monotonic() - started_at

        # Surface timing on the per-scene record so callers (and tests)
        # can see what just happened without grepping logs.
        metadata = resolution.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["elapsed_s"] = round(elapsed_s, 3)

        # Fire-and-forget audit. ``audit_log_bg`` is a no-op until the
        # global logger is initialised, so this is safe to call from
        # unit tests where no DB pool exists.
        try:
            from services.audit_log import audit_log_bg

            audit_log_bg(
                "video.scene_visual_resolved",
                "stages.scene_visuals",
                {
                    "scene_idx": scene_idx,
                    "kind": kind,
                    "source": resolution.get("source"),
                    "strategy": strategy,
                    "is_short": is_short,
                    "elapsed_s": round(elapsed_s, 3),
                    "reused": bool(resolution.get("reused")),
                },
                task_id=str(task_id_for_audit) if task_id_for_audit else None,
            )
        except Exception as exc:  # pragma: no cover — audit must never break the stage
            logger.debug(
                "[video.scene_visuals] audit_log_bg failed (non-fatal): %s", exc,
            )

        return resolution

    async def _resolve_scene(
        self,
        *,
        scene_idx: int,
        scene: dict[str, Any],
        strategy: str,
        threshold: float,
        post_id: Any,
        pool: Any,
        site_config: Any,
        is_short: bool,
        rotation_idx: int,
    ) -> dict[str, Any]:
        """Resolve one scene to ``{source, clip_path, url, reused, ...}``.

        Strategy fan-out lives here so the outer loop stays tight.
        Each branch returns the canonical dict shape on success and
        falls through to fresh generation on a miss.
        """
        visual_prompt = str(scene.get("visual_prompt") or "").strip()
        if not visual_prompt:
            return {
                "scene_idx": scene_idx,
                "source": None,
                "clip_path": "",
                "url": "",
                "reused": False,
                "search_terms": "",
                "metadata": {"error": "scene has no visual_prompt"},
            }

        # `reuse_first` and `mixed` both try the cache before going
        # external. Mixed rotates through the tail strategies on a
        # miss; reuse_first picks the configured default fallback.
        if strategy in ("reuse_first", "mixed") and post_id and pool:
            hit = await _try_reuse_from_media_assets(
                pool=pool,
                post_id=post_id,
                visual_prompt=visual_prompt,
                threshold=threshold,
            )
            if hit is not None:
                return {
                    "scene_idx": scene_idx,
                    "source": "media_assets",
                    "clip_path": await _ensure_local(hit["url"], hit.get("storage_path")),
                    "url": hit["url"],
                    "reused": True,
                    "search_terms": visual_prompt,
                    "metadata": {
                        "asset_id": str(hit.get("id") or ""),
                        "match_score": hit["score"],
                        "alt_text": hit.get("alt_text") or "",
                    },
                }

        # Pick the fresh source. The ``mixed`` strategy rotates so
        # adjacent scenes get different aesthetics. ``wan`` produces
        # actual generative video (5s clips); the others produce
        # stills that the compositor renders with Ken Burns motion.
        if strategy == "pexels":
            order: tuple[str, ...] = ("pexels", "sdxl")
        elif strategy == "sdxl":
            order = ("sdxl", "pexels")
        elif strategy == "wan":
            # Wan-only strategy. Falls back to SDXL stills then Pexels
            # if the inference server is unreachable / OOM.
            order = ("wan", "sdxl", "pexels")
        elif strategy == "mixed":
            # Three-way rotation: pexels → sdxl → wan → repeat. Wan
            # gets every third scene so the wall-clock cost is bounded.
            mixed_orders = (
                ("pexels", "sdxl", "wan"),
                ("sdxl", "wan", "pexels"),
                ("wan", "pexels", "sdxl"),
            )
            order = mixed_orders[rotation_idx % len(mixed_orders)]
        else:
            # ``reuse_first`` miss → default to pexels (cheap, fast),
            # SDXL on Pexels miss. Wan is opt-in via explicit strategy.
            order = ("pexels", "sdxl")

        for source in order:
            try:
                if source == "pexels":
                    res = await _try_pexels(visual_prompt, site_config)
                elif source == "sdxl":
                    res = await _try_sdxl(visual_prompt, site_config, is_short)
                elif source == "wan":
                    res = await _try_wan(visual_prompt, site_config, is_short)
                else:
                    continue
                if res is not None:
                    return {
                        "scene_idx": scene_idx,
                        "source": source,
                        "clip_path": res["clip_path"],
                        "url": res.get("url", ""),
                        "reused": False,
                        "search_terms": visual_prompt,
                        "metadata": res.get("metadata", {}),
                    }
            except Exception as exc:
                logger.warning(
                    "[video.scene_visuals] %s failed on scene %d: %s",
                    source, scene_idx, exc,
                )

        return {
            "scene_idx": scene_idx,
            "source": "miss",
            "clip_path": "",
            "url": "",
            "reused": False,
            "search_terms": visual_prompt,
            "metadata": {"error": "all configured sources failed"},
        }


async def _try_reuse_from_media_assets(
    *,
    pool: Any,
    post_id: Any,
    visual_prompt: str,
    threshold: float,
) -> dict[str, Any] | None:
    """Look up images attached to ``post_id`` and pick the best match.

    No vector index yet — we score by token overlap on the candidate's
    description / alt_text / title. The query caps at 50 candidates so
    a heavily-illustrated post doesn't blow a Stage budget on scoring.
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, url, storage_path, alt_text, description, title,
                       width, height
                FROM media_assets
                WHERE post_id = $1
                  AND type LIKE 'image%%'
                  AND url IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 50
                """,
                post_id,
            )
    except Exception as exc:
        logger.warning(
            "[video.scene_visuals] media_assets query failed: %s", exc,
        )
        return None

    best: dict[str, Any] | None = None
    best_score = 0.0
    for row in rows:
        candidate_text = " ".join(
            str(row[col] or "")
            for col in ("alt_text", "description", "title")
        )
        score = _score_match(visual_prompt, candidate_text)
        if score > best_score:
            best = dict(row)
            best_score = score

    if best is None or best_score < threshold:
        return None
    best["score"] = best_score
    return best


async def _try_pexels(
    visual_prompt: str,
    site_config: Any,
    *,
    seen_urls: set[str] | None = None,
) -> dict[str, Any] | None:
    """Run a Pexels search and download the top hit to a tempfile.

    When ``seen_urls`` is provided, picks the first result whose URL
    isn't already present — used by the bookend resolver (gh#163) so
    intro/outro never reuse a body-scene image when query overlap is
    high (e.g. intro_hook + outro_cta both empty, both falling through
    to the post title).
    """
    from services.image_providers.pexels import PexelsProvider

    provider = PexelsProvider()
    config = {"_site_config": site_config, "per_page": 5}
    results = await provider.fetch(_query_for_pexels(visual_prompt), config)
    if not results:
        return None

    chosen = None
    for candidate in results:
        if not candidate.url:
            continue
        if seen_urls is not None and candidate.url in seen_urls:
            continue
        chosen = candidate
        break
    if chosen is None:
        return None

    local = await _download_to_tmp(chosen.url, suffix=".jpg")
    if not local:
        return None
    return {
        "clip_path": local,
        "url": chosen.url,
        "metadata": {
            "photographer": chosen.photographer,
            "photographer_url": chosen.photographer_url,
            "width": chosen.width,
            "height": chosen.height,
        },
    }


async def _try_sdxl(
    visual_prompt: str,
    site_config: Any,
    is_short: bool,
) -> dict[str, Any] | None:
    """Generate an image via SDXL with vertical or landscape geometry."""
    from services.image_providers.sdxl import SdxlProvider

    provider = SdxlProvider()
    width, height = (1080, 1920) if is_short else (1920, 1080)
    out_path = os.path.join(
        tempfile.gettempdir(),
        f"{_TMP_PREFIX}{uuid4().hex}.png",
    )
    config = {
        "_site_config": site_config,
        "output_path": out_path,
        "width": width,
        "height": height,
    }
    try:
        results = await provider.fetch(visual_prompt, config)
    except Exception as exc:
        logger.warning("[video.scene_visuals] SDXL raised: %s", exc)
        return None
    if not results:
        return None

    chosen = results[0]
    # SdxlProvider writes directly to output_path; if for some reason
    # it produced a URL but no local file, skip — V0 wants the local
    # bytes to hand to ffmpeg.
    if not os.path.exists(out_path):
        return None
    return {
        "clip_path": out_path,
        "url": chosen.url,
        "metadata": {
            "model": chosen.metadata.get("model", ""),
            "steps": chosen.metadata.get("num_inference_steps"),
            "width": chosen.width,
            "height": chosen.height,
        },
    }


_WAN_MOTION_SUFFIX = (
    "dynamic camera motion, shallow depth of field, subtle parallax, "
    "smooth panning, motion blur on moving subjects, cinematic, "
    "high frame rate, fluid action"
)


def _adapt_prompt_for_wan(visual_prompt: str) -> str:
    """Reshape an SDXL-style still prompt for a video model.

    The script Stage was written for stills (SDXL/Pexels), so prompts
    end with "cinematic lighting, no people, no text, 4k" — none of
    which encourages motion. Wan 2.1 1.3B in particular tends toward
    near-static output without explicit motion verbs (Matt caught this
    on the first smoke clip). Append a motion-emphasizing tail so the
    same script can drive both still and video sources without script
    changes.
    """
    base = (visual_prompt or "").strip()
    if not base:
        return ""
    # Avoid double-tagging if the prompt already mentions motion.
    motion_words = ("motion", "moving", "flowing", "spinning", "panning")
    if any(w in base.lower() for w in motion_words):
        return base
    return f"{base}, {_WAN_MOTION_SUFFIX}"


async def _try_wan(
    visual_prompt: str,
    site_config: Any,
    is_short: bool,
) -> dict[str, Any] | None:
    """Generate a 5s clip via Wan 2.1 1.3B (real text-to-video).

    Routes through the existing Wan21Provider plugin, which POSTs the
    prompt to the wan-server sidecar (port 9840) and writes the
    resulting MP4 to a tempfile. The provider already handles
    fall-through on unreachable server / non-200 — returning empty
    list — so we just translate that into ``None`` for the strategy
    loop to fall through to the next source.
    """
    from services.video_providers.wan2_1 import Wan21Provider

    provider = Wan21Provider()
    width, height = (480, 832) if is_short else (832, 480)
    out_path = os.path.join(
        tempfile.gettempdir(),
        f"{_TMP_PREFIX}{uuid4().hex}.mp4",
    )
    config = {
        "_site_config": site_config,
        "output_path": out_path,
        "width": width,
        "height": height,
        # 5s clips are the documented sweet spot for Wan 2.1 1.3B —
        # longer ones don't add coherence and cost a lot more wall-
        # clock. The downstream stitch Stage stretches scenes via TTS
        # narration timing, so a 5s clip works for ~5-15s scenes too
        # (it loops). Anything longer should be a real video model
        # like Wan 2.1 14B (future plugin).
        "duration_s": 5,
    }
    adapted = _adapt_prompt_for_wan(visual_prompt)
    try:
        results = await provider.fetch(adapted, config)
    except Exception as exc:
        logger.warning("[video.scene_visuals] Wan raised: %s", exc)
        return None
    if not results:
        return None

    chosen = results[0]
    if not os.path.exists(out_path):
        return None
    return {
        "clip_path": out_path,
        "url": chosen.file_url,
        "metadata": {
            "model": "wan2.1-1.3b",
            "duration_s": chosen.duration_s,
            "width": chosen.width,
            "height": chosen.height,
            "fps": chosen.fps,
        },
    }


def _query_for_pexels(visual_prompt: str) -> str:
    """Distill an SDXL-style prompt into a stock-photo search term.

    Pexels search degrades fast on long queries — "cinematic lighting,
    no people, no text, photorealistic, 4k" is noise; we want the
    nouns. Take the first 6 substantive tokens.
    """
    tokens = _tokenize(visual_prompt)
    # Preserve order from the original prompt for slightly better
    # results — tokenize() returns a set, but re-walk the prompt.
    ordered: list[str] = []
    for word in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]+", visual_prompt.lower()):
        if word in tokens and word not in ordered:
            ordered.append(word)
        if len(ordered) >= 6:
            break
    return " ".join(ordered) or visual_prompt[:80]


async def _ensure_local(url: str, storage_path: str | None) -> str:
    """Resolve a media_assets row to a local filesystem path.

    Prefers ``storage_path`` (already on disk in the worker volume);
    falls back to downloading ``url`` into a tempfile when storage_path
    is empty or missing on disk.
    """
    if storage_path and os.path.exists(storage_path):
        return storage_path
    if not url:
        return ""
    return await _download_to_tmp(url, suffix=_suffix_from_url(url))


def _suffix_from_url(url: str) -> str:
    """Pull a sane file extension off ``url`` for the tempfile."""
    match = re.search(r"\.(jpg|jpeg|png|webp|gif)(?:\?|$)", url.lower())
    return f".{match.group(1)}" if match else ".jpg"


async def _download_to_tmp(
    url: str,
    *,
    suffix: str = ".jpg",
    timeout_s: float = _DEFAULT_DOWNLOAD_TIMEOUT_S,
) -> str:
    """Stream an image URL to a tempfile and return the absolute path.

    Empty string on any failure — callers treat that as a miss and
    fall through to the next strategy.
    """
    out_path = os.path.join(
        tempfile.gettempdir(),
        f"{_TMP_PREFIX}{uuid4().hex}{suffix}",
    )
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url)
            response.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(response.content)
    except Exception as exc:
        logger.warning(
            "[video.scene_visuals] download failed for %s: %s",
            url[:120], exc,
        )
        return ""
    return out_path


