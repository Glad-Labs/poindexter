"""
Podcast Service — Text-to-Speech audio generation for published blog posts.

Converts blog post content into MP3 podcast episodes using Microsoft Edge TTS
(edge-tts). Fully local, free, no API keys required.

Each episode includes:
- Intro: "Welcome to the {podcast_name} podcast. Today's episode: {title}"
- Body: The blog post content (markdown stripped to plain text)
- Outro: "Thanks for listening. Visit {site_domain} for more."

Audio files are saved to ~/.poindexter/podcast/ and served via the FastAPI
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
    # result = {"file_path": "~/.poindexter/podcast/abc123.mp3", "duration_seconds": 312}
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PODCAST_DIR = Path(os.path.expanduser("~")) / ".poindexter" / "podcast"

# Voice rotation pool — cycle through for variety across episodes
VOICE_POOL = [
    "en-US-AvaMultilingualNeural",       # Female, American (default)
    "en-US-AndrewMultilingualNeural",     # Male, American
    "en-US-BrianMultilingualNeural",      # Male, American (deeper)
    # Removed: en-GB-RyanNeural — too robotic per Matt's feedback
    # Removed: en-AU-WilliamNeural — accent doesn't fit brand voice
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
    # Blog-to-podcast medium adaptation (catch "post/article" → "episode/podcast")
    ("in this post", "in this episode"),
    ("In this post", "In this episode"),
    ("in this article", "in this episode"),
    ("In this article", "In this episode"),
    ("this blog post", "this episode"),
    ("This blog post", "This episode"),
    ("this post", "this episode"),
    ("This post", "This episode"),
    ("the article", "the episode"),
    ("Reading this", "Listening to this"),
    ("reading this", "listening to this"),
    ("Read on", "Stay tuned"),
    ("read on", "stay tuned"),
    ("as we discussed above", "as we discussed earlier"),
    ("As we discussed above", "As we discussed earlier"),
    ("See below", "Coming up next"),
    ("see below", "coming up next"),
    ("Shown below", "Coming up next"),
    ("shown below", "coming up next"),
    ("Listed below", "Coming up"),
    ("listed below", "coming up"),
    ("the following section", "the next section"),
    ("The following section", "The next section"),
    ("Scroll down", "Keep listening"),
    ("scroll down", "keep listening"),
    # Words TTS mispronounces
    ("GitFlow", "git flow"),
    ("GitHub", "git hub"),
    ("GitLab", "git lab"),
    ("DevSecOps", "dev sec ops"),
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
    ("\u2014", "; "),  # em dash — as pause
    ("\u2013", "; "),  # en dash – as pause
    (" - ", ", "),  # ASCII dash as pause
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

# Regex-based replacements (not DB-configurable — structural patterns)
_SPOKEN_REGEX_STATIC = [
    # File paths and URLs — skip entirely
    (re.compile(r"https?://\S+"), ""),
    (re.compile(r"[\w/\\]+\.\w{2,4}(?:\s|$)"), " "),  # file.ext
    # Version numbers — say naturally (v2.0 → version 2.0)
    (re.compile(r"\bv(\d)"), r"version \1"),
    # Acronym with expansion in parentheses — use plain language instead
    # "SOC (Security Operations Center)" → "security operations center"
    (re.compile(r"\b[A-Z]{2,6}\s*\(([A-Z][a-z][\w\s]{5,50})\)"), lambda m: m.group(1).lower()),
    # Parenthetical asides — convert to commas for natural pause
    (re.compile(r"\s*\(([^)]{1,50})\)\s*"), r", \1, "),
]

# Default acronym-to-plain-English mappings (overridden by DB key: tts_acronym_replacements)
_DEFAULT_ACRONYM_REPLACEMENTS = {
    "SOC": "security operations",
    "CRM": "customer relationship management",
    "SLA": "service level agreement",
    "KPI": "key performance indicator",
    "ROI": "return on investment",
    "MVP": "minimum viable product",
    "POC": "proof of concept",
    "EOL": "end of life",
}


def _get_tts_replacements(site_config: Any) -> list:
    """Load TTS pronunciation replacements, merging DB overrides with defaults.

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95). Required
            after the module-level singleton import was removed.
    """
    import json as _json

    # Simple replacements: DB key tts_pronunciations (JSON object: {"written": "spoken"})
    db_pronunciations = site_config.get("tts_pronunciations", "")
    if db_pronunciations:
        try:
            db_map = _json.loads(db_pronunciations)
            # Merge: DB entries override defaults with same key
            merged = dict(_SPOKEN_REPLACEMENTS)
            merged.update(db_map.items())
            simple = list(merged.items())
        except (ValueError, TypeError):
            simple = list(_SPOKEN_REPLACEMENTS)
    else:
        simple = list(_SPOKEN_REPLACEMENTS)

    return simple


def _get_acronym_regex(site_config: Any) -> list:
    """Load acronym replacements from DB, compile to regex list.

    Args:
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    import json as _json

    db_acronyms = site_config.get("tts_acronym_replacements", "")
    if db_acronyms:
        try:
            acronyms = _json.loads(db_acronyms)
        except (ValueError, TypeError):
            acronyms = _DEFAULT_ACRONYM_REPLACEMENTS
    else:
        acronyms = _DEFAULT_ACRONYM_REPLACEMENTS

    return [(re.compile(rf"\b{re.escape(k)}\b"), v) for k, v in acronyms.items()]


def _normalize_for_speech(text: str, site_config: Any) -> str:
    """Convert written English conventions to natural spoken form.

    Args:
        text: Input string to normalize.
        site_config: SiteConfig instance (DI — Phase H, GH#95). Required
            for DB-configurable pronunciation + acronym overrides.
    """
    # Simple replacements (DB-configurable via tts_pronunciations)
    for written, spoken in _get_tts_replacements(site_config):
        # Case-insensitive replace for pronunciation fixes
        text = re.sub(re.escape(written), spoken, text, flags=re.IGNORECASE)
    # Structural regex patterns (static)
    for pattern, replacement in _SPOKEN_REGEX_STATIC:
        text = pattern.sub(replacement, text)
    # Acronym replacements (DB-configurable via tts_acronym_replacements)
    for pattern, replacement in _get_acronym_regex(site_config):
        text = pattern.sub(replacement, text)
    # Smart quotes → straight quotes (TTS handles these better)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    # Ellipsis → pause
    text = text.replace("\u2026", "...")
    # Clean up double spaces and comma-space issues
    text = re.sub(r"  +", " ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r";\s*;", ";", text)
    return text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_markdown(text: str) -> str:
    """Convert markdown to natural spoken-word text for TTS.

    Removes everything a human wouldn't say out loud:
    headings, image captions, photographer credits, code blocks,
    markdown formatting, URLs, reference links, and end-of-post
    resource/link sections.
    """
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove images ![alt](url) and image captions
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    # Remove standalone image URLs
    text = re.sub(r"^https?://\S+\s*$", "", text, flags=re.MULTILINE)

    # Remove photographer/image credits — with or without markdown formatting
    # Handles: *Photo by X on Pexels*, Photo by X, **Image credit: ...**
    text = re.sub(r"(?i)^[*_]*\s*(photo|image|credit|source|via|courtesy|photographer)\b.*$", "", text, flags=re.MULTILINE)
    # Remove Pexels/Unsplash/Cloudinary/stock attribution lines (anywhere in line)
    text = re.sub(r"(?i)^[*_]*.*(?:pexels|unsplash|cloudinary|stock photo|shutterstock|getty).*$", "", text, flags=re.MULTILINE)

    # Remove trailing resource/link sections (Suggested Resources, External Links, etc.)
    text = re.sub(
        r"(?i)^[#*_ ]*(?:suggested|external|further|additional|related)\s+(?:external\s+)?(?:resources|reading|links|references)\s*[*_:]*\s*\n[\s\S]*$",
        "", text, flags=re.MULTILINE,
    )

    # Remove section headings entirely (not natural in speech)
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.MULTILINE)

    # Convert internal cross-links to just the anchor text
    # e.g., [Long Article Title](/posts/slug-here) → just removes the link entirely
    # when it appears as a standalone reference in parentheses or brackets
    text = re.sub(r"\[([^\]]+)\]\(/posts/[^)]+\)", r"\1", text)
    # Convert external links [text](url) to just text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Remove bold/italic markers
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    # Remove code blocks — summarize instead of reading code
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code backticks (keep the term)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove blockquote markers
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    # Remove horizontal rules (---, ***, ___)
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Remove list markers but keep text
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Remove reference-style links
    text = re.sub(r"^\[[^\]]+\]:\s+.*$", "", text, flags=re.MULTILINE)
    # Remove [IMAGE-N] placeholders
    text = re.sub(r"\[IMAGE-\d+\]", "", text)
    # Remove any remaining bare URLs
    text = re.sub(r"https?://\S+", "", text)
    # Remove empty parentheses left after URL removal
    text = re.sub(r"\(\s*\)", "", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove leading/trailing whitespace per line
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _build_script_with_llm(title: str, content: str, site_config: Any) -> str:
    """Use Ollama to rewrite a blog post as a natural podcast script.

    The LLM handles all nuances: removing visual references, URLs, image credits,
    converting written style to conversational spoken English, restructuring for
    audio flow, etc. Falls back to regex stripping if Ollama is unavailable.

    Args:
        title: Post title.
        content: Post content (markdown).
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    import httpx

    ollama_url = site_config.get("ollama_base_url", "http://host.docker.internal:11434")
    try:
        # Use the podcast-specific model, fall back to writer model, then default
        model = (
            site_config.get("podcast_script_model")
            or site_config.get("pipeline_writer_model", "").replace("ollama/", "")
            or site_config.get("default_ollama_model", "gemma3:27b")
        )
    except Exception:
        model = site_config.get("default_ollama_model", "gemma3:27b")
    if model == "auto" or not model:
        model = "gemma3:27b"

    prompt = f"""Rewrite the following blog article as a podcast script for a single narrator.

RULES:
- This is for text-to-speech, so write exactly what should be spoken aloud
- Convert ALL written/visual conventions to natural spoken English
- Remove ALL URLs, links, image references, photo credits, and attribution lines
- Remove any "Suggested Resources", "External Links", or reference sections at the end
- Replace "this post", "this article", "this blog" with "this episode" or "today's episode"
- Replace "see below", "shown below", "scroll down" with "coming up next" or "in a moment"
- Replace "read on" with "stay with us" or "let's continue"
- Replace "as shown above" with "as we discussed"
- Don't read section headings as-is — weave transitions naturally ("Let's now turn to..." or "Next, let's explore...")
- Expand abbreviations: "e.g." → "for example", "i.e." → "that is", "etc." → "and so on"
- Spell out acronyms on first use if not commonly known
- Don't include any markdown formatting, asterisks, brackets, or special characters
- Keep the same depth, arguments, and structure — don't summarize or shorten
- Write in a warm, conversational but authoritative tone
- NEVER use first person ("I", "my", "I think", "what I call") — the narrator is presenting facts, not opinions
- Use "we" sparingly and only when the original article does — prefer impersonal phrasing ("the industry is seeing", "developers are finding")
- Do NOT add any meta-commentary like "Here's the script:" — just output the script text directly

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{_strip_markdown(content)}

PODCAST SCRIPT:"""

    try:
        # Podcast script generation is a long-form Ollama call (up to 8k
        # tokens). 180s is generous for local qwen3:30b/glm-4.7 on a 5090
        # while keeping the pipeline from ever stalling on a stuck model.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=5.0)
        ) as client:
            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 8192, "temperature": 0.4, "num_ctx": 16384},
                },
                timeout=180,
            )
            resp.raise_for_status()
            data = resp.json()
            script_body = data.get("response", "").strip()

            if len(script_body) < 200:
                logger.warning("[PODCAST] LLM script too short (%d chars), falling back to regex", len(script_body))
                return _build_script_fallback(title, content, site_config)

            logger.info("[PODCAST] LLM generated %d-char script for '%s'", len(script_body), title[:50])

    except Exception as e:
        logger.warning("[PODCAST] Ollama script generation failed (%s), falling back to regex", e)
        return _build_script_fallback(title, content, site_config)

    # Still apply speech normalization for TTS pronunciation fixes
    script_body = _normalize_for_speech(script_body, site_config)
    spoken_title = _normalize_for_speech(title, site_config)

    _pname = site_config.get("podcast_name", "the podcast")
    _domain_tts = site_config.get("site_domain", "our site").replace(".", " dot ")
    intro = f"Welcome to {_pname}. Today's episode: {spoken_title}."
    outro = (
        f"Thanks for listening to {_pname}. "
        f"Visit {_domain_tts} for more episodes, articles, and insights. "
        "See you next time."
    )
    return f"{intro}\n\n{script_body}\n\n{outro}"


def _build_script_fallback(title: str, content: str, site_config: Any) -> str:
    """Fallback: build script via regex stripping when Ollama is unavailable.

    Args:
        title: Post title.
        content: Post content (markdown).
        site_config: SiteConfig instance (DI — Phase H, GH#95).
    """
    plain_text = _strip_markdown(content)
    plain_text = _normalize_for_speech(plain_text, site_config)
    spoken_title = _normalize_for_speech(title, site_config)

    _pname = site_config.get("podcast_name", "the podcast")
    _domain_tts = site_config.get("site_domain", "our site").replace(".", " dot ")
    intro = f"Welcome to {_pname}. Today's episode: {spoken_title}."
    outro = (
        f"Thanks for listening to {_pname}. "
        f"Visit {_domain_tts} for more episodes, articles, and insights. "
        "See you next time."
    )
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
    file_path: str | None = None
    duration_seconds: int = 0
    file_size_bytes: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PodcastService:
    """Generate podcast MP3 episodes from blog post content using Edge TTS."""

    def __init__(
        self,
        output_dir: Path | None = None,
        *,
        site_config: Any | None = None,
    ):
        """Construct the PodcastService.

        Args:
            output_dir: Where to write episode MP3s. Defaults to PODCAST_DIR.
            site_config: SiteConfig instance (DI — Phase H, GH#95). Optional
                for tests that exercise disk-only methods (get_episode_path,
                episode_exists, list_episodes). Required when
                generate_episode() falls into LLM script generation or any
                path that reads TTS config from app_settings — will raise
                AttributeError if ``None`` there.
        """
        self.output_dir = output_dir or PODCAST_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._site_config = site_config

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
        pre_generated_script: str | None = None,
    ) -> EpisodeResult:
        """Generate a podcast episode MP3 from blog post content.

        Args:
            post_id: Unique post identifier (used as filename).
            title: Post title (used in the intro).
            content: Full post content (markdown — will be stripped).
            force: Regenerate even if the episode already exists.
            pre_generated_script: If provided, skip LLM script generation and use this script directly.

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

        if pre_generated_script and len(pre_generated_script) > 200:
            script = pre_generated_script
            logger.info("[PODCAST] Using pre-generated script (%d chars)", len(script))
        else:
            if self._site_config is None:
                raise RuntimeError(
                    "PodcastService.generate_episode() requires site_config — "
                    "pass it to the constructor (Phase H, GH#95)."
                )
            script = await _build_script_with_llm(title, content, self._site_config)

        if not script.strip():
            return EpisodeResult(success=False, error="Empty content after markdown stripping")

        logger.info(
            "[PODCAST] Generating episode for '%s' (%d chars script)",
            title[:60],
            len(script),
        )

        # Rotate voice based on post_id hash for variety across episodes.
        # usedforsecurity=False — MD5 here is "pick a stable index from this
        # post_id", not an integrity check; bandit's B324 (weak-hash-for-
        # security) is a false positive on this path.
        import hashlib
        voice_index = int(
            hashlib.md5(post_id.encode(), usedforsecurity=False).hexdigest(), 16,
        ) % len(VOICE_POOL)
        selected_voice = VOICE_POOL[voice_index]
        # Try selected voice first, then remaining pool voices, then fallbacks
        remaining_pool = [v for v in VOICE_POOL if v != selected_voice]
        voices_to_try = [selected_voice, *remaining_pool, *VOICE_FALLBACKS]
        last_error = None
        logger.info("[PODCAST] Voice rotation: selected '%s' (index %d) for post %s",
                    selected_voice, voice_index, post_id[:12])

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
    *,
    site_config: Any,
    pre_generated_script: str | None = None,
) -> None:
    """Fire-and-forget podcast generation. Logs errors but never raises.

    Args:
        post_id: Post identifier (filename stem).
        title: Post title.
        content: Post content (markdown).
        site_config: SiteConfig instance (DI — Phase H, GH#95).
        pre_generated_script: Skip LLM and use this script directly.
    """
    try:
        svc = PodcastService(site_config=site_config)
        result = await svc.generate_episode(
            post_id, title, content,
            pre_generated_script=pre_generated_script,
        )
        if not result.success:
            logger.warning("[PODCAST] Failed for post %s: %s", post_id, result.error)
    except Exception as e:
        logger.warning("[PODCAST] Unexpected error for post %s: %s", post_id, e)
