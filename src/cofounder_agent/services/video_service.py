"""
Video Service — generates narrated slideshow videos from published posts.

Takes SDXL-generated images + podcast audio → Ken Burns slideshow → MP4.
Calls the host video-server (port 9837) via HTTP, similar to SDXL server pattern.

Usage:
    from services.video_service import generate_video_for_post

    result = await generate_video_for_post(
        post_id="abc123",
        title="Why Local LLMs Beat Cloud APIs",
        image_urls=["https://cdn.example.com/img1.png", ...],
        podcast_path="/root/.poindexter/podcast/abc123.mp3",
    )
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)

VIDEO_DIR = Path(os.path.expanduser("~")) / ".poindexter" / "video"
VIDEO_SERVER_URL = site_config.get("video_server_url", "http://host.docker.internal:9837")
SDXL_SERVER_URL = site_config.get("sdxl_server_url", "http://host.docker.internal:9836")


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
    title: str, content: str, num_images: int = 4
) -> list[str]:
    """Generate SDXL images for the video slideshow.

    Uses Ollama to create topic-specific prompts, then SDXL to generate images.
    Returns list of local file paths to generated images.
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
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
        for i, prompt in enumerate(prompts):
            try:
                logger.info("[VIDEO] SDXL frame %d: %s", i + 1, prompt[:80])
                resp = await client.post(
                    f"{SDXL_SERVER_URL}/generate",
                    json={
                        "prompt": prompt, "negative_prompt": neg,
                        "steps": 4, "guidance_scale": 1.0,
                    },
                    timeout=60,
                )
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                    img_path = str(output_dir / f"frame_{i:02d}.png")
                    with open(img_path, "wb") as f:
                        f.write(resp.content)
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
                    with open(img_path, "wb") as f:
                        f.write(resp.content)
                    image_paths.append(img_path)
                    logger.info("[VIDEO] Downloaded post image %d: %s", i + 1, url[:80])
            except Exception as e:
                logger.debug("[VIDEO] Failed to download post image %d: %s", i, e)

    return image_paths


async def _generate_images_from_scenes(scenes: list[str]) -> list[str]:
    """Generate SDXL images from pre-generated scene descriptions.

    Skips Ollama prompt generation since scenes are already written.
    """
    neg = "text, words, letters, watermark, face, person, hands, blurry, low quality, distorted, ugly, deformed"
    output_dir = VIDEO_DIR / "frames"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_paths = []

    logger.info("[VIDEO] Generating %d SDXL images from pre-generated scenes", len(scenes))
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
        for i, prompt in enumerate(scenes):
            try:
                logger.info("[VIDEO] SDXL frame %d: %s", i + 1, prompt[:80])
                resp = await client.post(
                    f"{SDXL_SERVER_URL}/generate",
                    json={
                        "prompt": prompt, "negative_prompt": neg,
                        "steps": 4, "guidance_scale": 1.0,
                    },
                    timeout=60,
                )
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                    img_path = str(output_dir / f"frame_{i:02d}.png")
                    with open(img_path, "wb") as f:
                        f.write(resp.content)
                    image_paths.append(img_path)
                    logger.info("[VIDEO] Generated frame %d/%d (%d bytes)", i + 1, len(scenes), len(resp.content))
                else:
                    body = resp.text[:200] if resp.text else "(empty)"
                    logger.warning("[VIDEO] SDXL returned %d for frame %d: %s", resp.status_code, i, body)
            except Exception as e:
                logger.warning("[VIDEO] Failed to generate frame %d: %s", i, e)

    return image_paths


async def generate_video_for_post(
    post_id: str,
    title: str,
    content: str = "",
    podcast_path: str | None = None,
    image_urls: list[str] | None = None,
    force: bool = False,
    pre_generated_scenes: list[str] | None = None,
) -> VideoResult:
    """Generate a video for a published post.

    Uses podcast audio as narration over a Ken Burns slideshow of SDXL images.

    Args:
        post_id: Post identifier (used as filename).
        title: Post title (used for image prompt generation).
        content: Post content excerpt (context for image prompts).
        podcast_path: Path to podcast MP3 file. If None, checks default location.
        image_urls: Optional pre-existing image URLs to download. If None, generates new ones.
        force: Regenerate even if video exists.

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
            new_images = await _generate_images_from_scenes(pre_generated_scenes[:supplement_count])
        else:
            new_images = await _generate_images_for_video(title, content, num_images=supplement_count)

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

    # Convert container paths to host paths for the video server
    # Container mount: /root/.poindexter → C:/Users/mattm/.poindexter (bind mount)
    host_home = site_config.get("host_home", "C:/Users/mattm")
    def _to_host_path(container_path: str) -> str:
        return container_path.replace("/root/.poindexter", f"{host_home}/.poindexter")

    host_image_paths = [_to_host_path(p) for p in image_paths]
    host_audio_path = _to_host_path(podcast_path)

    # Call video server with host-side file paths
    logger.info("[VIDEO] Rendering video (%d images + audio)", len(image_paths))
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(f"{VIDEO_SERVER_URL}/generate", json={
                "image_paths": host_image_paths,
                "audio_path": host_audio_path,
                "title": title,
                "transition_duration": 30,      # ~30s per image before transitioning
                "ken_burns": True,               # Enable zoom/pan effects on all images
                "ken_burns_zoom_range": [1.0, 1.15],  # Subtle zoom range
            })

            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("video/"):
                with open(str(output_path), "wb") as f:
                    f.write(resp.content)

                duration = int(resp.headers.get("X-Duration-Seconds", "0"))
                elapsed = resp.headers.get("X-Elapsed-Seconds", "?")
                size = output_path.stat().st_size

                logger.info(
                    "[VIDEO] Generated: %s (%d bytes, %ds, rendered in %ss)",
                    post_id, size, duration, elapsed,
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
        logger.error("[VIDEO] Video server error: %s", e)
        return VideoResult(success=False, error=str(e))


async def _generate_short_summary_audio(
    post_id: str, title: str, content: str,
) -> str | None:
    """Generate a 60-second summary TTS audio for the short-form video.

    Uses Ollama to write a tight ~150-word hook + key takeaways,
    then Edge TTS to convert to speech.
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

        summary_script = _normalize_for_speech(summary_script)
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
) -> VideoResult:
    """Generate a vertical short-form video (TikTok/YouTube Shorts).

    Generates a separate 60-second summary narration (not the full podcast).
    Uses post images + SDXL images for visuals.
    Output: 1080x1920 MP4, max 60 seconds.
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
            script = _normalize_for_speech(pre_generated_summary)
            short_audio_path = str(VIDEO_DIR / f"{post_id}-short-audio.mp3")
            communicate = edge_tts.Communicate(script, "en-US-AndrewMultilingualNeural")
            await communicate.save(short_audio_path)
            if os.path.exists(short_audio_path) and os.path.getsize(short_audio_path) > 1000:
                short_audio = short_audio_path
                logger.info("[SHORT] Used pre-generated summary script for audio")
        except Exception as e:
            logger.warning("[SHORT] Pre-generated summary TTS failed: %s", e)

    if not short_audio:
        short_audio = await _generate_short_summary_audio(post_id, title, content)
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
            new_images = await _generate_images_from_scenes(pre_generated_scenes[:supplement_count])
        else:
            new_images = await _generate_images_for_video(title, content, num_images=supplement_count)

    image_paths = (post_images + new_images)[:4]

    if not image_paths:
        return VideoResult(success=False, error="No images could be generated")

    host_home = site_config.get("host_home", "C:/Users/mattm")
    def _to_host_path(container_path: str) -> str:
        return container_path.replace("/root/.poindexter", f"{host_home}/.poindexter")

    host_image_paths = [_to_host_path(p) for p in image_paths]
    host_audio_path = _to_host_path(short_audio)

    logger.info("[SHORT] Rendering short video (%d images, summary audio)", len(image_paths))
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{VIDEO_SERVER_URL}/generate-short", json={
                "image_paths": host_image_paths,
                "audio_path": host_audio_path,
                "title": title,
                "max_duration": 60.0,
                "ken_burns": True,
                "ken_burns_zoom_range": [1.0, 1.2],
            })

            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("video/"):
                with open(str(output_path), "wb") as f:
                    f.write(resp.content)

                duration = int(resp.headers.get("X-Duration-Seconds", "0"))
                size = output_path.stat().st_size

                logger.info("[SHORT] Generated: %s (%d bytes, %ds)", post_id, size, duration)
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
        logger.error("[SHORT] Video server error: %s", e)
        return VideoResult(success=False, error=str(e))


async def generate_video_episode(
    post_id: str,
    title: str,
    content: str,
    *,
    pre_generated_scenes: list[str] | None = None,
) -> None:
    """Fire-and-forget full-length video generation. Logs errors but never raises."""
    try:
        result = await generate_video_for_post(
            post_id, title, content,
            pre_generated_scenes=pre_generated_scenes,
        )
        if not result.success:
            logger.warning("[VIDEO] Failed for post %s: %s", post_id, result.error)
    except Exception as e:
        logger.warning("[VIDEO] Unexpected error for post %s: %s", post_id, e)
