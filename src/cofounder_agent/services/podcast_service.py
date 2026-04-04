"""
Podcast Service — Text-to-Speech audio generation for published blog posts.

Converts blog post content into MP3 podcast episodes using Microsoft Edge TTS
(edge-tts). Fully local, free, no API keys required.

Each episode includes:
- Intro: "Welcome to the Glad Labs podcast. Today's episode: {title}"
- Body: The blog post content (markdown stripped to plain text)
- Outro: "Thanks for listening. Visit gladlabs.io for more."

Audio files are saved to ~/.gladlabs/podcast/ and served via the FastAPI
podcast routes. A valid podcast RSS feed is generated for Apple Podcasts /
Spotify distribution.

Usage:
    from services.podcast_service import PodcastService

    svc = PodcastService()
    result = await svc.generate_episode(
        post_id="abc123",
        title="Why Local LLMs Beat Cloud APIs",
        content="# Why Local LLMs...\\n\\nMarkdown body here...",
    )
    # result = {"file_path": "~/.gladlabs/podcast/abc123.mp3", "duration_seconds": 312}
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PODCAST_DIR = Path(os.path.expanduser("~")) / ".gladlabs" / "podcast"

# Default voice from DB config, with voice pool for rotation
VOICE_PRIMARY = site_config.get("tts_voice", "en-US-AvaMultilingualNeural")

# Voice rotation pool — cycle through for variety across episodes
VOICE_POOL = [
    "en-US-AvaMultilingualNeural",       # Female, American (default)
    "en-US-AndrewMultilingualNeural",     # Male, American
    "en-US-BrianMultilingualNeural",      # Male, American (deeper)
    "en-GB-RyanNeural",                   # Male, British
    "en-AU-WilliamNeural",               # Male, Australian
]
VOICE_FALLBACKS = [
    "en-US-GuyNeural",
    "en-US-ChristopherNeural",
    "en-US-EricNeural",
]


# ---------------------------------------------------------------------------
# Spoken English normalization — convert written conventions to natural speech
# ---------------------------------------------------------------------------

# Order matters — longer patterns first to avoid partial replacements
_SPOKEN_REPLACEMENTS = [
    # Words TTS mispronounces
    ("GitFlow", "git flow"),
    ("GitHub", "git hub"),
    ("GitLab", "git lab"),
    ("DevOps", "dev ops"),
    ("DevEx", "dev ex"),
    ("FastAPI", "fast A P I"),
    ("PostgreSQL", "postgres"),
    ("MongoDB", "mongo D B"),
    ("GraphQL", "graph Q L"),
    ("WebSocket", "web socket"),
    ("TypeScript", "type script"),
    ("JavaScript", "java script"),
    ("Next.js", "next J S"),
    ("Node.js", "node J S"),
    ("Vue.js", "view J S"),
    # Technical abbreviations with punctuation
    ("CI/CD", "CI CD"),
    ("I/O", "I O"),
    ("TCP/IP", "TCP IP"),
    ("OS/2", "OS 2"),
    # Common abbreviations
    ("e.g.", "for example"),
    ("i.e.", "that is"),
    ("etc.", "and so on"),
    ("vs.", "versus"),
    ("approx.", "approximately"),
    ("incl.", "including"),
    ("w/", "with"),
    ("w/o", "without"),
    # Symbols people don't say
    ("&", "and"),
    (" - ", ", "),  # em dash as pause
    (" -- ", ", "),
    ("->", "to"),
    ("=>", "becomes"),
    (">=", "at least"),
    ("<=", "at most"),
    ("!=", "not equal to"),
    ("==", "equals"),
    # Units and formats
    ("24/7", "twenty four seven"),
    ("/mo", " per month"),
    ("/yr", " per year"),
    ("$0", "zero dollars"),
]

# Regex-based replacements for patterns
_SPOKEN_REGEX = [
    # Hyphenated compound words — remove dash (real-time → real time)
    (re.compile(r"(\w+)-(\w+)"), r"\1 \2"),
    # File paths and URLs — skip entirely
    (re.compile(r"https?://\S+"), ""),
    (re.compile(r"[\w/\\]+\.\w{2,4}(?:\s|$)"), " "),  # file.ext
    # Version numbers — say naturally (v2.0 → version 2.0)
    (re.compile(r"\bv(\d)"), r"version \1"),
    # Parenthetical asides — convert to commas for natural pause
    (re.compile(r"\s*\(([^)]{1,50})\)\s*"), r", \1, "),
]


def _normalize_for_speech(text: str) -> str:
    """Convert written English conventions to natural spoken form."""
    for written, spoken in _SPOKEN_REPLACEMENTS:
        text = text.replace(written, spoken)
    for pattern, replacement in _SPOKEN_REGEX:
        text = pattern.sub(replacement, text)
    # Clean up double spaces and comma-space issues
    text = re.sub(r"  +", " ", text)
    text = re.sub(r",\s*,", ",", text)
    return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_markdown(text: str) -> str:
    """Convert markdown to natural spoken-word text for TTS.

    Removes everything a human wouldn't say out loud:
    headings, image captions, photographer credits, code blocks,
    markdown formatting, URLs, and reference links.
    """
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove images ![alt](url) and image captions
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    # Remove standalone image URLs
    text = re.sub(r"^https?://\S+\s*$", "", text, flags=re.MULTILINE)
    # Remove photographer/image credits (Photo by..., Image by..., Credit:...)
    text = re.sub(r"(?i)^(photo|image|credit|source|via|courtesy)[:\s].*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"(?i)photographer[:\s].*$", "", text, flags=re.MULTILINE)
    # Remove Pexels/Unsplash/Cloudinary attribution lines
    text = re.sub(r"(?i)^.*(?:pexels|unsplash|cloudinary|stock photo).*$", "", text, flags=re.MULTILINE)
    # Convert links [text](url) to just text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove section headings entirely (not natural in speech)
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Remove code blocks — summarize instead of reading code
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code backticks (keep the term)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove blockquote markers
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Remove list markers but keep text
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Remove reference-style links
    text = re.sub(r"^\[[^\]]+\]:\s+.*$", "", text, flags=re.MULTILINE)
    # Remove [IMAGE-N] placeholders
    text = re.sub(r"\[IMAGE-\d+\]", "", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove leading/trailing whitespace per line
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _build_script(title: str, content: str) -> str:
    """Build the full podcast script with intro, body, and outro."""
    plain_text = _strip_markdown(content)
    # Normalize written conventions to natural speech
    plain_text = _normalize_for_speech(plain_text)
    spoken_title = _normalize_for_speech(title)

    intro = f"Welcome to the Glad Labs podcast. Today's episode: {spoken_title}."
    outro = (
        "Thanks for listening to the Glad Labs podcast. "
        "Visit gladlabs dot io for more episodes, articles, and insights. "
        "See you next time."
    )

    # Add natural pauses between sections with periods
    return f"{intro}\n\n{plain_text}\n\n{outro}"


def _estimate_duration_from_text(text: str) -> int:
    """Rough duration estimate: ~150 words per minute for TTS."""
    word_count = len(text.split())
    return max(30, int(word_count / 150 * 60))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EpisodeResult:
    """Result of generating a podcast episode."""

    success: bool
    file_path: Optional[str] = None
    duration_seconds: int = 0
    file_size_bytes: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PodcastService:
    """Generate podcast MP3 episodes from blog post content using Edge TTS."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or PODCAST_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def get_episode_path(self, post_id: str) -> Path:
        """Return the expected path for an episode MP3."""
        return self.output_dir / f"{post_id}.mp3"

    def episode_exists(self, post_id: str) -> bool:
        """Check if an episode already exists for a post."""
        path = self.get_episode_path(post_id)
        return path.exists() and path.stat().st_size > 0

    def list_episodes(self) -> list[dict]:
        """List all generated episode files with metadata."""
        episodes = []
        for mp3 in sorted(self.output_dir.glob("*.mp3")):
            stat = mp3.stat()
            episodes.append({
                "post_id": mp3.stem,
                "file_path": str(mp3),
                "file_size_bytes": stat.st_size,
                "created_at": stat.st_ctime,
            })
        return episodes

    async def generate_episode(
        self,
        post_id: str,
        title: str,
        content: str,
        *,
        force: bool = False,
    ) -> EpisodeResult:
        """Generate a podcast episode MP3 from blog post content.

        Args:
            post_id: Unique post identifier (used as filename).
            title: Post title (used in the intro).
            content: Full post content (markdown — will be stripped).
            force: Regenerate even if the episode already exists.

        Returns:
            EpisodeResult with file path and duration info.
        """
        output_path = self.get_episode_path(post_id)

        # Skip if already generated
        if not force and self.episode_exists(post_id):
            size = output_path.stat().st_size
            logger.info("[PODCAST] Episode already exists: %s (%d bytes)", post_id, size)
            return EpisodeResult(
                success=True,
                file_path=str(output_path),
                file_size_bytes=size,
                duration_seconds=_estimate_duration_from_text(content),
            )

        script = _build_script(title, content)

        if not script.strip():
            return EpisodeResult(success=False, error="Empty content after markdown stripping")

        logger.info(
            "[PODCAST] Generating episode for '%s' (%d chars script)",
            title[:60],
            len(script),
        )

        # Try primary voice, then fallbacks
        voices_to_try = [VOICE_PRIMARY] + VOICE_FALLBACKS
        last_error = None

        for voice in voices_to_try:
            try:
                result = await self._generate_with_voice(script, voice, output_path)
                if result.success:
                    logger.info(
                        "[PODCAST] Generated: %s (%d bytes, ~%ds, voice=%s)",
                        post_id,
                        result.file_size_bytes,
                        result.duration_seconds,
                        voice,
                    )
                    return result
                last_error = result.error
            except Exception as e:
                last_error = f"{voice}: {type(e).__name__}: {e}"
                logger.warning("[PODCAST] Voice %s failed: %s", voice, last_error)

        error_msg = f"All voices failed. Last error: {last_error}"
        logger.error("[PODCAST] %s", error_msg)
        return EpisodeResult(success=False, error=error_msg)

    async def _generate_with_voice(
        self, script: str, voice: str, output_path: Path
    ) -> EpisodeResult:
        """Generate audio using edge-tts with a specific voice."""
        try:
            import edge_tts
        except ImportError:
            return EpisodeResult(
                success=False,
                error="edge-tts not installed. Run: pip install edge-tts",
            )

        try:
            communicate = edge_tts.Communicate(script, voice)
            await communicate.save(str(output_path))

            if not output_path.exists() or output_path.stat().st_size == 0:
                return EpisodeResult(
                    success=False,
                    error=f"edge-tts produced empty file with voice {voice}",
                )

            size = output_path.stat().st_size
            duration = _estimate_duration_from_text(script)

            return EpisodeResult(
                success=True,
                file_path=str(output_path),
                duration_seconds=duration,
                file_size_bytes=size,
            )
        except Exception as e:
            # Clean up partial file
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            return EpisodeResult(success=False, error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Convenience function (fire-and-forget from publish pipeline)
# ---------------------------------------------------------------------------


async def generate_podcast_episode(
    post_id: str,
    title: str,
    content: str,
) -> None:
    """Fire-and-forget podcast generation. Logs errors but never raises."""
    try:
        svc = PodcastService()
        result = await svc.generate_episode(post_id, title, content)
        if not result.success:
            logger.warning("[PODCAST] Failed for post %s: %s", post_id, result.error)
    except Exception as e:
        logger.warning("[PODCAST] Unexpected error for post %s: %s", post_id, e)
