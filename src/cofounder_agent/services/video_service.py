"""
Video Service — generates narrated slideshow videos from published posts.

Takes SDXL-generated images + podcast audio → Ken Burns slideshow → MP4.
Calls the host video-server (port 9837) via HTTP, similar to SDXL server pattern.

Usage:
    from services.video_service import generate_video_for_post

    result = await generate_video_for_post(
        post_id="abc123",
        title="Why Local LLMs Beat Cloud APIs",
        podcast_path="/root/.poindexter/podcast/abc123.mp3",
    )
"""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)

def _poindexter_data_root() -> Path:
    """Same layout as podcast_service._poindexter_data_root. See there for the
    full rationale — tl;dr the worker container's bind mount lives at
    /root/.poindexter regardless of which user the process runs as."""
    override = os.environ.get("POINDEXTER_DATA_ROOT")
    if override:
        return Path(override)
    root_mount = Path("/root/.poindexter")
    if root_mount.is_dir():
        return root_mount
    return Path(os.path.expanduser("~")) / ".poindexter"


VIDEO_DIR = _poindexter_data_root() / "video"


def _write_bytes(path: str, content: bytes) -> None:
    """Sync file-write helper suitable for ``asyncio.to_thread``.

    Used throughout video generation to write SDXL frames / downloaded
    MP4 chunks without blocking the event loop (ASYNC230). Binary
    mode; caller supplies the full bytes payload.
    """
    with open(path, "wb") as f:
        f.write(content)


def _video_server_url(site_config: Any) -> str:
    """Resolve VIDEO_SERVER_URL from site_config per-call.

    Previously captured at module-import time. Since this module
    imports before site_config.load() completes, the cached value was
    always the hardcoded default — changing video_server_url in
    app_settings had no effect until worker restart.

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    return site_config.get("video_server_url", "http://host.docker.internal:9837")


def _sdxl_server_url(site_config: Any) -> str:
    """Resolve SDXL_SERVER_URL from site_config per-call (same rationale).

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    return site_config.get("sdxl_server_url", "http://host.docker.internal:9836")


@dataclass
class VideoResult:
    """Result of generating a video."""
    success: bool
    file_path: str | None = None
    duration_seconds: int = 0
    file_size_bytes: int = 0
    images_used: int = 0
    error: str | None = None


async def _generate_images_for_video(
    title: str, content: str, num_images: int = 4, *, site_config: Any,
) -> list[str]:
    """Generate SDXL images for the video slideshow.

    Uses Ollama to create topic-specific prompts, then SDXL to generate images.
    Returns list of local file paths to generated images.

    Args:
        title: Post title for prompt seed.
        content: Post content for prompt context.
        num_images: Target image count.
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    ollama_url = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
    # Use llama3 for prompt generation — some models (glm, qwen thinking mode) return empty
    model = "llama3:latest"

    # Ask Ollama to generate multiple distinct image prompts
    prompt_request = (
        f"Generate exactly {num_images} Stable Diffusion XL image prompts for a video slideshow "
        f"about: {title}\n\n"
        f"Context: {content[:400]}\n\n"
        "Each prompt should describe a DIFFERENT photorealistic scene related to the topic. "
        "Requirements: cinematic lighting, no people, no text, no faces, no hands. "
        "Output ONLY the prompts, one per line, numbered 1-{num_images}. No other text."
    )

    image_paths = []
    prompts = []

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            logger.info("[VIDEO] Requesting %d image prompts from Ollama (%s)", num_images, model)
            resp = await client.post(f"{ollama_url}/api/generate", json={
                "model": model, "prompt": prompt_request, "stream": False,
                "options": {"num_predict": 500, "temperature": 0.8},
            })
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            logger.info("[VIDEO] Ollama returned %d chars of prompts", len(raw))

            # Parse numbered prompts
            for line in raw.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Strip numbering like "1.", "1)", "1:"
                import re
                cleaned = re.sub(r"^\d+[.):\-]\s*", "", line).strip().strip('"')
                if len(cleaned) > 20:
                    prompts.append(cleaned)
                if len(prompts) >= num_images:
                    break
    except Exception as e:
        logger.warning("[VIDEO] Failed to generate image prompts via Ollama: %s", e)
        # Fallback prompts
        prompts = [
            f"photorealistic {title} concept, cinematic lighting, 4k, detailed",
            f"futuristic technology scene related to {title}, blue lighting, photorealistic",
            "modern server infrastructure, glowing connections, cinematic, photorealistic",
            "abstract data visualization with flowing light particles, cinematic, 4k",
        ][:num_images]

    # Generate images via SDXL
    neg = "text, words, letters, watermark, face, person, hands, blurry, low quality, distorted, ugly, deformed"
    output_dir = VIDEO_DIR / "frames"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[VIDEO] Generating %d SDXL images from %d prompts", len(prompts), len(prompts))
    sdxl_url = _sdxl_server_url(site_config)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
        for i, prompt in enumerate(prompts):
            try:
                logger.info("[VIDEO] SDXL frame %d: %s", i + 1, prompt[:80])
                resp = await client.post(
                    f"{sdxl_url}/generate",
                    json={
                        "prompt": prompt, "negative_prompt": neg,
                        "steps": 4, "guidance_scale": 1.0,
                    },
                    timeout=60,
                )
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                    img_path = str(output_dir / f"frame_{i:02d}.png")
                    await asyncio.to_thread(_write_bytes, img_path, resp.content)
                    image_paths.append(img_path)
                    logger.info("[VIDEO] Generated frame %d/%d (%d bytes)", i + 1, len(prompts), len(resp.content))
                else:
                    body = resp.text[:200] if resp.text else "(empty)"
                    logger.warning("[VIDEO] SDXL returned %d for frame %d: %s", resp.status_code, i, body)
            except Exception as e:
                logger.warning("[VIDEO] Failed to generate frame %d: %s", i, e)

    return image_paths


async def _extract_images_from_content(content: str) -> list[str]:
    """Download images referenced in blog post content for reuse in video.

    Extracts R2 CDN URLs and Pexels URLs from markdown image tags,
    downloads them to the video frames directory.
    Returns list of local file paths.
    """
    import re
    image_paths = []
    output_dir = VIDEO_DIR / "frames"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find all image URLs in markdown: ![alt](url) or <img src="url">
    urls = re.findall(r'!\[.*?\]\((https?://[^\s)]+)\)', content)
    urls += re.findall(r'<img[^>]+src="(https?://[^\s"]+)"', content)
    # Deduplicate preserving order
    seen = set()
    unique_urls = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    if not unique_urls:
        return []

    logger.info("[VIDEO] Found %d images in post content to reuse", len(unique_urls))

    async with httpx.AsyncClient(timeout=30) as client:
        for i, url in enumerate(unique_urls):
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.content) > 5000:
                    ext = ".jpg" if "jpeg" in resp.headers.get("content-type", "") else ".png"
                    img_path = str(output_dir / f"post_img_{i:02d}{ext}")
                    await asyncio.to_thread(_write_bytes, img_path, resp.content)
                    image_paths.append(img_path)
                    logger.info("[VIDEO] Downloaded post image %d: %s", i + 1, url[:80])
            except Exception as e:
                logger.debug("[VIDEO] Failed to download post image %d: %s", i, e)

    return image_paths


async def _generate_images_from_scenes(
    scenes: list[str], *, site_config: Any,
) -> list[str]:
    """Generate SDXL images from pre-generated scene descriptions.

    Skips Ollama prompt generation since scenes are already written.

    Args:
        scenes: Pre-generated scene prompts.
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    neg = "text, words, letters, watermark, face, person, hands, blurry, low quality, distorted, ugly, deformed"
    output_dir = VIDEO_DIR / "frames"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_paths = []

    logger.info("[VIDEO] Generating %d SDXL images from pre-generated scenes", len(scenes))
    sdxl_url = _sdxl_server_url(site_config)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
        for i, prompt in enumerate(scenes):
            try:
                logger.info("[VIDEO] SDXL frame %d: %s", i + 1, prompt[:80])
                resp = await client.post(
                    f"{sdxl_url}/generate",
                    json={
                        "prompt": prompt, "negative_prompt": neg,
                        "steps": 4, "guidance_scale": 1.0,
                    },
                    timeout=60,
                )
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                    img_path = str(output_dir / f"frame_{i:02d}.png")
                    await asyncio.to_thread(_write_bytes, img_path, resp.content)
                    image_paths.append(img_path)
                    logger.info("[VIDEO] Generated frame %d/%d (%d bytes)", i + 1, len(scenes), len(resp.content))
                else:
                    body = resp.text[:200] if resp.text else "(empty)"
                    logger.warning("[VIDEO] SDXL returned %d for frame %d: %s", resp.status_code, i, body)
            except Exception as e:
                logger.warning("[VIDEO] Failed to generate frame %d: %s", i, e)

    return image_paths


async def _maybe_generate_ambient_bed(
    *, post_id: str, title: str, site_config: Any,
) -> str | None:
    """Opt-in ambient bed for the slideshow video (issue #125).

    Returns ``None`` when the audio-generation layer is disabled
    (default), the provider can't fulfill the request, or the call
    raises. Never propagates — audio generation is strictly additive
    and must not break existing video rendering.
    """
    try:
        from services.audio_gen_service import generate_audio, is_audio_gen_enabled
    except Exception as e:  # Defensive: import failures shouldn't break video
        logger.debug("[VIDEO] audio_gen_service unavailable: %s", e)
        return None

    if not is_audio_gen_enabled(site_config):
        return None

    try:
        prompt = (
            site_config.get("video_audio_bed_prompt", "")
            or f"warm cinematic ambient bed, gentle, no vocals, fits a video about: {title}"
        )
    except Exception:
        prompt = f"warm cinematic ambient bed, gentle, no vocals, fits a video about: {title}"

    bed_path = str(VIDEO_DIR / f"{post_id}-bed.wav")
    result = await generate_audio(
        prompt=prompt,
        kind="ambient",
        site_config=site_config,
        output_path=bed_path,
    )
    if result and result.file_path:
        return result.file_path
    return None


async def generate_video_for_post(
    post_id: str,
    title: str,
    content: str = "",
    podcast_path: str | None = None,
    force: bool = False,
    pre_generated_scenes: list[str] | None = None,
    *,
    site_config: Any,
) -> VideoResult:
    """Generate a video for a published post.

    Uses podcast audio as narration over a Ken Burns slideshow of SDXL images.

    Args:
        post_id: Post identifier (used as filename).
        title: Post title (used for image prompt generation).
        content: Post content excerpt (context for image prompts).
        podcast_path: Path to podcast MP3 file. If None, checks default location.
        force: Regenerate even if video exists.
        pre_generated_scenes: Optional pre-generated SDXL prompts.
        site_config: SiteConfig instance (DI — Phase H, GH#95).

    Returns:
        VideoResult with file path and duration info.
    """
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    output_path = VIDEO_DIR / f"{post_id}.mp4"

    # Skip if already generated
    if not force and output_path.exists() and output_path.stat().st_size > 0:
        logger.info("[VIDEO] Episode already exists: %s", post_id)
        return VideoResult(
            success=True,
            file_path=str(output_path),
            file_size_bytes=output_path.stat().st_size,
        )

    # Find podcast audio
    if not podcast_path:
        from services.podcast_service import PODCAST_DIR
        podcast_path = str(PODCAST_DIR / f"{post_id}.mp3")

    if not os.path.exists(podcast_path):
        return VideoResult(success=False, error=f"Podcast not found: {podcast_path}")

    # Collect images: reuse from post content + supplement with SDXL
    logger.info("[VIDEO] Collecting images for '%s'", title[:50])
    post_images = await _extract_images_from_content(content)
    logger.info("[VIDEO] Reusing %d images from post content", len(post_images))

    # Supplement with new SDXL images to reach ~8 total
    supplement_count = max(0, 8 - len(post_images))
    new_images = []
    if supplement_count > 0:
        if pre_generated_scenes and len(pre_generated_scenes) >= 2:
            new_images = await _generate_images_from_scenes(
                pre_generated_scenes[:supplement_count], site_config=site_config,
            )
        else:
            new_images = await _generate_images_for_video(
                title, content, num_images=supplement_count, site_config=site_config,
            )

    # Interleave: post image, new image, post image, new image...
    image_paths = []
    pi, ni = 0, 0
    while pi < len(post_images) or ni < len(new_images):
        if pi < len(post_images):
            image_paths.append(post_images[pi])
            pi += 1
        if ni < len(new_images):
            image_paths.append(new_images[ni])
            ni += 1

    if not image_paths:
        return VideoResult(success=False, error="No images could be generated")

    # Convert container paths to host paths for the video server.
    # Two container users are in play:
    #   SDXL container runs as root     → /root/.poindexter/...
    #   Worker container runs as appuser → /home/appuser/.poindexter/...
    # Both bind-mount into the host's .poindexter/ directory. Any path
    # containing ".poindexter/..." is normalized to the host path.
    host_home = site_config.get("host_home", "C:/Users/mattm")
    def _to_host_path(container_path: str) -> str:
        return (
            container_path
            .replace("/root/.poindexter", f"{host_home}/.poindexter")
            .replace("/home/appuser/.poindexter", f"{host_home}/.poindexter")
        )

    host_image_paths = [_to_host_path(p) for p in image_paths]
    host_audio_path = _to_host_path(podcast_path)

    # Optional ambient bed via the AudioGenProvider plugin. Default
    # off — only activates when audio_gen_engine names a registered
    # provider (Glad-Labs/poindexter#125). Best-effort: failures log
    # and we proceed with the original podcast audio unchanged.
    bed_path = await _maybe_generate_ambient_bed(
        post_id=post_id, title=title, site_config=site_config,
    )
    if bed_path:
        logger.info("[VIDEO] Generated ambient bed: %s", bed_path)

    # Call video server with host-side file paths
    logger.info("[VIDEO] Rendering video (%d images + audio)", len(image_paths))
    video_url = _video_server_url(site_config)
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(f"{video_url}/generate", json={
                "image_paths": host_image_paths,
                "audio_path": host_audio_path,
                "title": title,
                "transition_duration": 30,      # ~30s per image before transitioning
                "ken_burns": True,               # Enable zoom/pan effects on all images
                "ken_burns_zoom_range": [1.0, 1.15],  # Subtle zoom range
            })

            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("video/"):
                await asyncio.to_thread(_write_bytes, str(output_path), resp.content)

                duration = int(resp.headers.get("X-Duration-Seconds", "0"))
                elapsed = resp.headers.get("X-Elapsed-Seconds", "?")
                size = output_path.stat().st_size

                logger.info(
                    "[VIDEO] Generated: %s (%d bytes, %ds, rendered in %ss)",
                    post_id, size, duration, elapsed,
                )
                # Glad-Labs/poindexter#161 — legacy video path now
                # records the media_assets row that the V0 stitch
                # Stage already lands. Best-effort.
                await _record_video_asset(
                    site_config=site_config,
                    post_id=post_id,
                    asset_type="video",
                    output_path=str(output_path),
                    duration_seconds=duration,
                    file_size_bytes=size,
                    width=1920,
                    height=1080,
                    images_used=len(image_paths),
                    title=title,
                )
                return VideoResult(
                    success=True,
                    file_path=str(output_path),
                    duration_seconds=duration,
                    file_size_bytes=size,
                    images_used=len(image_paths),
                )
            else:
                error_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                error_msg = error_data.get("error", f"Video server returned {resp.status_code}")
                logger.error("[VIDEO] Generation failed: %s", error_msg)
                return VideoResult(success=False, error=error_msg)

    except Exception as e:
        logger.exception("[VIDEO] Video server error: %s", e)
        return VideoResult(success=False, error=str(e))


async def _record_video_asset(
    *,
    site_config: Any,
    post_id: str,
    asset_type: str,
    output_path: str,
    duration_seconds: int,
    file_size_bytes: int,
    width: int,
    height: int,
    images_used: int,
    title: str,
) -> None:
    """Best-effort ``media_assets`` insert for a legacy-pipeline video.

    Closes Glad-Labs/poindexter#161 — the V0 stitch Stages already
    write rows; this brings the legacy slideshow path to parity so
    cleanup / retention / cost-attribution find every video.
    """
    try:
        from services.media_asset_recorder import record_media_asset
    except Exception as exc:  # noqa: BLE001 — defensive import guard
        logger.debug("[VIDEO] media_asset_recorder unavailable: %s", exc)
        return
    pool = getattr(site_config, "_pool", None)
    await record_media_asset(
        pool=pool,
        post_id=post_id,
        asset_type=asset_type,
        storage_path=output_path,
        public_url="",  # uploaded separately via upload_video_episode
        mime_type="video/mp4",
        duration_ms=int(duration_seconds * 1000),
        file_size_bytes=file_size_bytes,
        width=width,
        height=height,
        provider_plugin="video.ken_burns_slideshow",
        source="pipeline",
        storage_provider="local",
        metadata={
            "title": title,
            "images_used": images_used,
        },
    )


async def _generate_short_summary_audio(
    post_id: str, title: str, content: str, *, site_config: Any,
) -> str | None:
    """Generate a 60-second summary TTS audio for the short-form video.

    Uses Ollama to write a tight ~150-word hook + key takeaways,
    then Edge TTS to convert to speech.

    Args:
        post_id: Post identifier (filename stem for the audio).
        title: Post title for hook.
        content: Post content.
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    ollama_url = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
    model = "llama3:latest"

    # Strip markdown for cleaner input
    from services.podcast_service import _normalize_for_speech, _strip_markdown

    prompt = f"""Write a 60-second video narration (about 150 words) summarizing this article.

RULES:
- Start with a compelling hook that grabs attention in the first 5 seconds
- Cover the 2-3 most important takeaways
- End with a call to action: "Full article at glad labs dot io"
- Conversational, energetic tone — this is for TikTok/YouTube Shorts
- No URLs, no markdown, no special characters
- Write ONLY the narration text, nothing else

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{_strip_markdown(content)[:3000]}

NARRATION:"""

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{ollama_url}/api/generate", json={
                "model": model, "prompt": prompt, "stream": False,
                "options": {"num_predict": 300, "temperature": 0.6},
            })
            resp.raise_for_status()
            summary_script = resp.json().get("response", "").strip()

        if len(summary_script) < 50:
            logger.warning("[SHORT] Summary script too short (%d chars)", len(summary_script))
            return None

        summary_script = _normalize_for_speech(summary_script, site_config)
        logger.info("[SHORT] Generated summary script: %d chars", len(summary_script))

        # Generate TTS audio
        import edge_tts
        short_audio_path = str(VIDEO_DIR / f"{post_id}-short-audio.mp3")
        communicate = edge_tts.Communicate(summary_script, "en-US-AndrewMultilingualNeural")
        await communicate.save(short_audio_path)

        if os.path.exists(short_audio_path) and os.path.getsize(short_audio_path) > 1000:
            logger.info("[SHORT] Summary audio generated: %s", short_audio_path)
            return short_audio_path
        return None

    except Exception as e:
        logger.warning("[SHORT] Summary audio generation failed: %s", e)
        return None


async def generate_short_video_for_post(
    post_id: str,
    title: str,
    content: str = "",
    podcast_path: str | None = None,
    pre_generated_scenes: list[str] | None = None,
    pre_generated_summary: str | None = None,
    force: bool = False,
    *,
    site_config: Any,
) -> VideoResult:
    """Generate a vertical short-form video (TikTok/YouTube Shorts).

    Generates a separate 60-second summary narration (not the full podcast).
    Uses post images + SDXL images for visuals.
    Output: 1080x1920 MP4, max 60 seconds.

    Args:
        post_id: Post identifier (filename stem).
        title: Post title.
        content: Post content excerpt.
        podcast_path: Optional podcast MP3 path to fall back to.
        pre_generated_scenes: Optional pre-generated SDXL prompts.
        pre_generated_summary: Optional pre-generated summary script.
        force: Regenerate even if file exists.
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    output_path = VIDEO_DIR / f"{post_id}-short.mp4"

    if not force and output_path.exists() and output_path.stat().st_size > 0:
        logger.info("[SHORT] Short video already exists: %s", post_id)
        return VideoResult(
            success=True,
            file_path=str(output_path),
            file_size_bytes=output_path.stat().st_size,
        )

    # Use pre-generated summary script if available, otherwise generate one
    short_audio = None
    if pre_generated_summary and len(pre_generated_summary) > 50:
        # TTS the pre-generated summary directly
        try:
            import edge_tts

            from services.podcast_service import _normalize_for_speech
            script = _normalize_for_speech(pre_generated_summary, site_config)
            short_audio_path = str(VIDEO_DIR / f"{post_id}-short-audio.mp3")
            communicate = edge_tts.Communicate(script, "en-US-AndrewMultilingualNeural")
            await communicate.save(short_audio_path)
            if os.path.exists(short_audio_path) and os.path.getsize(short_audio_path) > 1000:
                short_audio = short_audio_path
                logger.info("[SHORT] Used pre-generated summary script for audio")
        except Exception as e:
            logger.warning("[SHORT] Pre-generated summary TTS failed: %s", e)

    if not short_audio:
        short_audio = await _generate_short_summary_audio(
            post_id, title, content, site_config=site_config,
        )
    if not short_audio:
        # Fall back to full podcast if summary generation fails
        if not podcast_path:
            from services.podcast_service import PODCAST_DIR
            podcast_path = str(PODCAST_DIR / f"{post_id}.mp3")
        if not os.path.exists(podcast_path):
            return VideoResult(success=False, error="No audio available for short video")
        short_audio = podcast_path
        logger.info("[SHORT] Falling back to full podcast audio")

    # Collect images: reuse from post + supplement with SDXL
    post_images = await _extract_images_from_content(content)
    supplement_count = max(0, 4 - len(post_images))
    new_images = []
    if supplement_count > 0:
        if pre_generated_scenes and len(pre_generated_scenes) >= 2:
            new_images = await _generate_images_from_scenes(
                pre_generated_scenes[:supplement_count], site_config=site_config,
            )
        else:
            new_images = await _generate_images_for_video(
                title, content, num_images=supplement_count, site_config=site_config,
            )

    image_paths = (post_images + new_images)[:4]

    if not image_paths:
        return VideoResult(success=False, error="No images could be generated")

    host_home = site_config.get("host_home", "C:/Users/mattm")
    def _to_host_path(container_path: str) -> str:
        return container_path.replace("/root/.poindexter", f"{host_home}/.poindexter")

    host_image_paths = [_to_host_path(p) for p in image_paths]
    host_audio_path = _to_host_path(short_audio)

    logger.info("[SHORT] Rendering short video (%d images, summary audio)", len(image_paths))
    video_url = _video_server_url(site_config)
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{video_url}/generate-short", json={
                "image_paths": host_image_paths,
                "audio_path": host_audio_path,
                "title": title,
                "max_duration": 60.0,
                "ken_burns": True,
                "ken_burns_zoom_range": [1.0, 1.2],
            })

            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("video/"):
                await asyncio.to_thread(_write_bytes, str(output_path), resp.content)

                duration = int(resp.headers.get("X-Duration-Seconds", "0"))
                size = output_path.stat().st_size

                logger.info("[SHORT] Generated: %s (%d bytes, %ds)", post_id, size, duration)
                # Glad-Labs/poindexter#161 — short-form variant of the
                # legacy slideshow path.
                await _record_video_asset(
                    site_config=site_config,
                    post_id=post_id,
                    asset_type="video_short",
                    output_path=str(output_path),
                    duration_seconds=duration,
                    file_size_bytes=size,
                    width=1080,
                    height=1920,
                    images_used=len(image_paths),
                    title=title,
                )
                return VideoResult(
                    success=True,
                    file_path=str(output_path),
                    duration_seconds=duration,
                    file_size_bytes=size,
                    images_used=len(image_paths),
                )
            else:
                error_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                error_msg = error_data.get("error", f"Video server returned {resp.status_code}")
                logger.error("[SHORT] Generation failed: %s", error_msg)
                return VideoResult(success=False, error=error_msg)

    except Exception as e:
        logger.exception("[SHORT] Video server error: %s", e)
        return VideoResult(success=False, error=str(e))


async def generate_video_episode(
    post_id: str,
    title: str,
    content: str,
    *,
    site_config: Any,
    pre_generated_scenes: list[str] | None = None,
) -> None:
    """Fire-and-forget full-length video generation. Logs errors but never raises.

    Dispatches through :func:`dispatch_video_generation` so the active
    engine is selected by ``app_settings.video_engine`` (default
    ``ken_burns_slideshow``; flip to ``wan2.1-1.3b`` to opt in to true
    text-to-video per GH#124).

    Args:
        post_id: Post identifier.
        title: Post title.
        content: Post content.
        site_config: SiteConfig instance (DI — Phase H, GH#95).
        pre_generated_scenes: Optional pre-generated SDXL prompts.
    """
    try:
        result = await dispatch_video_generation(
            post_id=post_id,
            title=title,
            content=content,
            short=False,
            site_config=site_config,
            pre_generated_scenes=pre_generated_scenes,
        )
        if not result.success:
            logger.warning("[VIDEO] Failed for post %s: %s", post_id, result.error)
    except Exception as e:
        logger.warning("[VIDEO] Unexpected error for post %s: %s", post_id, e)


# ---------------------------------------------------------------------------
# VideoProvider dispatch (GH#124)
#
# A thin selector that reads ``app_settings.video_engine``, looks up the
# matching VideoProvider via ``plugins.registry``, and forwards through
# the provider's ``fetch()`` method. Default = ``ken_burns_slideshow``
# (the legacy pipeline) so existing behavior is preserved; flipping the
# setting to ``wan2.1-1.3b`` opts in to the new T2V engine without any
# code change. ``[]`` from the active provider falls back to the legacy
# pipeline so a misconfigured/unreachable Wan server doesn't break the
# video feed.
# ---------------------------------------------------------------------------


_DEFAULT_VIDEO_ENGINE = "ken_burns_slideshow"


def _resolve_video_provider(name: str) -> Any | None:
    """Look up a registered VideoProvider by name.

    Mirrors :func:`services.image_service._resolve_image_provider`.
    Core providers ship via ``plugins.registry.get_core_samples()``
    while third-party providers register through entry_points and are
    exposed via ``get_video_providers()``. Check both sources so a
    community plugin can override a core provider by name.
    """
    try:
        from plugins.registry import get_core_samples, get_video_providers
    except Exception as e:
        logger.warning("video provider registry unavailable: %s", e)
        return None

    providers: list[Any] = []
    try:
        providers.extend(get_video_providers())
    except Exception as e:
        logger.debug("get_video_providers failed: %s", e)
    try:
        providers.extend(get_core_samples().get("video_providers", []))
    except Exception as e:
        logger.debug("get_core_samples failed: %s", e)

    for provider in providers:
        if getattr(provider, "name", None) == name:
            return provider
    return None


async def dispatch_video_generation(
    *,
    post_id: str,
    title: str,
    content: str = "",
    short: bool = False,
    site_config: Any,
    podcast_path: str | None = None,
    pre_generated_scenes: list[str] | None = None,
    pre_generated_summary: str | None = None,
    force: bool = False,
) -> VideoResult:
    """Generate a video using the engine selected in app_settings.

    Reads ``app_settings.video_engine`` (default
    ``ken_burns_slideshow``). When the configured engine is missing or
    returns an empty result, falls back to ``ken_burns_slideshow`` so
    the pipeline never silently drops a video.

    Args:
        post_id: Post identifier (filename stem).
        title: Post title (also used as the T2V prompt for generation
            providers).
        content: Post body — used by composition providers for image
            mining + by generation providers as additional prompt
            context.
        short: When True, render the 1080x1920 vertical Shorts variant.
            Composition providers honor this directly; generation
            providers render at portrait dimensions when set.
        site_config: SiteConfig instance (DI).
        podcast_path: Optional pre-rendered narration MP3.
        pre_generated_scenes: Optional pre-rendered SDXL prompts.
        pre_generated_summary: Optional pre-rendered Shorts summary
            script. Composition-provider only.
        force: Regenerate even if the file already exists.

    Returns:
        :class:`VideoResult` (legacy dataclass) so existing callers keep
        their field names. The plugin
        :class:`VideoResult <plugins.video_provider.VideoResult>`
        returned by the active provider is adapted via
        :func:`_adapt_plugin_result`.
    """
    engine = str(
        site_config.get("video_engine", _DEFAULT_VIDEO_ENGINE)
        or _DEFAULT_VIDEO_ENGINE,
    )
    logger.info("[VIDEO] dispatch engine=%s post_id=%s short=%s", engine, post_id, short)

    provider = _resolve_video_provider(engine)
    if provider is None:
        logger.warning(
            "[VIDEO] engine=%r not registered; falling back to %s",
            engine, _DEFAULT_VIDEO_ENGINE,
        )
        provider = _resolve_video_provider(_DEFAULT_VIDEO_ENGINE)
        engine = _DEFAULT_VIDEO_ENGINE

    if provider is None:
        # No registered provider at all — surface a clear failure so the
        # operator knows the registry isn't loading.
        return VideoResult(
            success=False,
            error="No VideoProvider registered (registry empty?)",
        )

    config: dict[str, Any] = {
        "post_id": post_id,
        "content": content,
        "podcast_path": podcast_path,
        "pre_generated_scenes": pre_generated_scenes,
        "pre_generated_summary": pre_generated_summary,
        "force": force,
        "short": short,
        "_site_config": site_config,
    }

    # Generation providers honor ``width``/``height``/``duration_s`` —
    # composition providers ignore them. Pass through always; the
    # provider documents what it consumes.
    if short:
        config.setdefault("width", 1080)
        config.setdefault("height", 1920)
    else:
        config.setdefault("width", 1920)
        config.setdefault("height", 1080)

    try:
        results = await provider.fetch(title, config)
    except Exception as e:
        logger.exception("[VIDEO] provider %r raised: %s", engine, e)
        results = []

    if not results and engine != _DEFAULT_VIDEO_ENGINE:
        logger.warning(
            "[VIDEO] engine=%r returned no results; falling back to %s",
            engine, _DEFAULT_VIDEO_ENGINE,
        )
        fallback = _resolve_video_provider(_DEFAULT_VIDEO_ENGINE)
        if fallback is not None:
            try:
                results = await fallback.fetch(title, config)
            except Exception as e:
                logger.exception(
                    "[VIDEO] fallback provider raised: %s", e,
                )

    if not results:
        return VideoResult(
            success=False,
            error=f"VideoProvider {engine!r} returned no results",
        )

    return _adapt_plugin_result(results[0])


def _adapt_plugin_result(plugin_result: Any) -> VideoResult:
    """Translate a plugin :class:`VideoResult` into the legacy
    ``video_service.VideoResult`` so existing callers
    (``publish_service``, ``backfill_videos``, the routes layer) don't
    have to change. New callers SHOULD prefer the plugin VideoResult
    directly via the provider.
    """
    metadata = getattr(plugin_result, "metadata", {}) or {}
    return VideoResult(
        success=bool(
            getattr(plugin_result, "file_path", None)
            or getattr(plugin_result, "file_url", None),
        ),
        file_path=getattr(plugin_result, "file_path", None),
        duration_seconds=int(getattr(plugin_result, "duration_s", 0) or 0),
        file_size_bytes=int(metadata.get("file_size_bytes", 0) or 0),
        images_used=int(metadata.get("images_used", 0) or 0),
        error=None,
    )
