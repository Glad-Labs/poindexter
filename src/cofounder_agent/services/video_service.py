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
        podcast_path="/root/.gladlabs/podcast/abc123.mp3",
    )
"""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)

VIDEO_DIR = Path(os.path.expanduser("~")) / ".gladlabs" / "video"
VIDEO_SERVER_URL = os.getenv("VIDEO_SERVER_URL", "http://host.docker.internal:9837")
SDXL_SERVER_URL = os.getenv("SDXL_SERVER_URL", "http://host.docker.internal:9836")


@dataclass
class VideoResult:
    """Result of generating a video."""
    success: bool
    file_path: Optional[str] = None
    duration_seconds: int = 0
    file_size_bytes: int = 0
    images_used: int = 0
    error: Optional[str] = None


async def _generate_images_for_video(
    title: str, content: str, num_images: int = 4
) -> list[str]:
    """Generate SDXL images for the video slideshow.

    Uses Ollama to create topic-specific prompts, then SDXL to generate images.
    Returns list of local file paths to generated images.
    """
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
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
            f"modern server infrastructure, glowing connections, cinematic, photorealistic",
            f"abstract data visualization with flowing light particles, cinematic, 4k",
        ][:num_images]

    # Generate images via SDXL
    neg = "text, words, letters, watermark, face, person, hands, blurry, low quality, distorted, ugly, deformed"
    output_dir = VIDEO_DIR / "frames"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[VIDEO] Generating %d SDXL images from %d prompts", len(prompts), len(prompts))
    async with httpx.AsyncClient(timeout=120) as client:
        for i, prompt in enumerate(prompts):
            try:
                logger.info("[VIDEO] SDXL frame %d: %s", i + 1, prompt[:80])
                resp = await client.post(f"{SDXL_SERVER_URL}/generate", json={
                    "prompt": prompt, "negative_prompt": neg,
                    "steps": 4, "guidance_scale": 1.0,
                })
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


async def generate_video_for_post(
    post_id: str,
    title: str,
    content: str = "",
    podcast_path: Optional[str] = None,
    image_urls: Optional[list[str]] = None,
    force: bool = False,
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

    # Generate images
    logger.info("[VIDEO] Generating images for '%s'", title[:50])
    image_paths = await _generate_images_for_video(title, content, num_images=5)

    if not image_paths:
        return VideoResult(success=False, error="No images could be generated")

    # Convert container paths to host paths for the video server
    # Container mount: /root/.gladlabs → C:/Users/mattm/.gladlabs (bind mount)
    host_home = os.getenv("HOST_HOME", "C:/Users/mattm")
    def _to_host_path(container_path: str) -> str:
        return container_path.replace("/root/.gladlabs", f"{host_home}/.gladlabs")

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


async def generate_video_episode(post_id: str, title: str, content: str) -> None:
    """Fire-and-forget video generation. Logs errors but never raises."""
    try:
        result = await generate_video_for_post(post_id, title, content)
        if not result.success:
            logger.warning("[VIDEO] Failed for post %s: %s", post_id, result.error)
    except Exception as e:
        logger.warning("[VIDEO] Unexpected error for post %s: %s", post_id, e)
