"""
Podcast Service — Text-to-Speech audio generation for published blog posts.

Converts blog post content into MP3 podcast episodes using Speaches/Kokoro TTS.
Speaches runs as the ``poindexter-speaches`` Docker container and exposes an
OpenAI-compatible ``/v1/audio/speech`` endpoint backed by the Kokoro-82M model
(Apache 2.0). Gated by ``podcast_tts_enabled=true`` in app_settings.

Each episode includes:
- Intro: "Welcome to the {podcast_name} podcast. Today's episode: {title}"
- Body: The blog post content (markdown stripped to plain text)
- Outro: "Thanks for listening. Visit {site_domain} for more."

Audio files are saved to ~/.poindexter/podcast/ and served via the FastAPI
podcast routes. A valid podcast RSS feed is generated for Apple Podcasts /
Spotify distribution.

Usage:
    from services.podcast_service import PodcastService

    svc = PodcastService(site_config=site_config)
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

from services.logger_config import get_logger
from services.site_config import SiteConfig

# SiteConfig is now injected exclusively (#272 Phase-2f). The
# module-level ``site_config`` global + ``set_site_config`` setter were
# deleted; the public entry points (``PodcastService.__init__`` /
# ``generate_podcast_episode``) require a ``site_config=`` kwarg and
# thread it down into the internal free functions. Callers pass the
# run-bound instance: routes via ``Depends(get_site_config_dependency)``,
# publish_service via its own ``_sc``, jobs via ``config['_site_config']``.


def _resolve_site_config(sc: "SiteConfig | None") -> SiteConfig:
    """Resolve the SiteConfig to use, failing loud on a missing instance.

    The internal free functions keep a ``site_config: SiteConfig | None``
    signature so the public entry points can thread their resolved
    instance through them. After #272 Phase-2f there is no module-global
    fallback — a ``None`` here means a caller bypassed the public DI seam,
    which we surface loudly per ``feedback_no_silent_defaults`` rather
    than fabricating an empty ``SiteConfig()``.
    """
    if sc is None:
        raise ValueError(
            "podcast_service requires a site_config — construct "
            "PodcastService(site_config=...) / call generate_podcast_episode("
            "..., site_config=...) with the run-bound SiteConfig (#272)."
        )
    return sc


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PODCAST_DIR = Path(os.path.expanduser("~")) / ".poindexter" / "podcast"

# Voice rotation pool — Kokoro voice IDs (Speaches/Kokoro TTS)
VOICE_POOL = [
    "bf_emma",       # Female, British (Speaches default)
    "am_michael",    # Male, American
    "af_heart",      # Female, American
    "bm_george",     # Male, British
]
VOICE_FALLBACKS = [
    "af_bella",
    "am_adam",
]


def _resolve_voice_pool(site_config: "SiteConfig | None") -> list[str]:
    """Resolve the voice-rotation pool — DB-config first, constant fallback.

    Lifts the hardcoded ``VOICE_POOL`` to operator-tunable app_settings so the
    rotation pool is DB-configurable (config-in-DB principle, #689 Plan 7):

    - ``tts_voice_rotation_enabled`` (default ``false``) — master switch.
    - ``tts_voice_pool`` (default ``''``) — comma-separated voice names.

    Behavior is UNCHANGED when unset: a disabled flag OR an empty
    ``tts_voice_pool`` falls through to the module ``VOICE_POOL`` constant, so
    existing installs rotate exactly as before. An operator supplies
    engine-appropriate voice names via ``tts_voice_pool`` without touching code.
    """
    if site_config is None:
        return list(VOICE_POOL)
    try:
        enabled = bool(site_config.get_bool("tts_voice_rotation_enabled", False))
    except Exception:  # noqa: BLE001 — defensive; any read failure → constant
        enabled = False
    if not enabled:
        return list(VOICE_POOL)
    try:
        raw = str(site_config.get("tts_voice_pool", "") or "")
    except Exception:  # noqa: BLE001 — defensive; any read failure → constant
        raw = ""
    pool = [v.strip() for v in raw.split(",") if v.strip()]
    return pool or list(VOICE_POOL)


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

def _get_tts_replacements(*, site_config: "SiteConfig | None" = None) -> list:
    """Return structural transforms (always applied) plus DB pronunciation entries.

    Pronunciation opinions (brand names, abbreviations, units) live entirely in the
    DB under ``tts_pronunciations``.  If that key is empty, only the structural
    transforms above are applied — no hardcoded pronunciation fallback.
    Configure via ``poindexter settings set tts_pronunciations '{"GB": "gigabyte"}'``.
    """
    import json as _json

    _sc = _resolve_site_config(site_config)

    db_pronunciations = _sc.get("tts_pronunciations", "")
    if not db_pronunciations:
        return list(_SPOKEN_REPLACEMENTS)

    try:
        db_map = _json.loads(db_pronunciations)
    except (ValueError, TypeError):
        logger.warning("tts_pronunciations is not valid JSON — pronunciation table skipped")
        return list(_SPOKEN_REPLACEMENTS)

    return list(_SPOKEN_REPLACEMENTS) + list(db_map.items())


def _get_acronym_regex(*, site_config: "SiteConfig | None" = None) -> list:
    """Load acronym replacements from DB only — no hardcoded fallback.

    Returns an empty list when ``tts_acronym_replacements`` is unset or invalid.
    Configure via ``poindexter settings set tts_acronym_replacements '{"SOC": "security operations"}'``.
    """
    import json as _json

    _sc = _resolve_site_config(site_config)

    db_acronyms = _sc.get("tts_acronym_replacements", "")
    if not db_acronyms:
        return []

    try:
        acronyms = _json.loads(db_acronyms)
    except (ValueError, TypeError):
        logger.warning("tts_acronym_replacements is not valid JSON — acronym expansion skipped")
        return []

    return [(re.compile(rf"\b{re.escape(k)}\b"), v) for k, v in acronyms.items()]


def _apply_spoken_replacement(text: str, written: str, spoken: str) -> str:
    """Apply one written→spoken substitution, case-insensitively.

    Pure-letter tokens (e.g. ``GB``, ``VRAM``, ``CI``) get ``\\b`` word
    boundaries so a short abbreviation fires only as a whole token — ``GB``
    must not match inside ``RGB``, ``CI`` must not match inside ``social``.
    Tokens containing punctuation (``vs.``, ``CI/CD``, ``->``) use plain
    matching, which is correct for their punctuation-delimited role.

    Shared by ``_normalize_for_speech`` (script generation) and the TTS
    render boundary in ``_generate_with_voice`` so both passes apply
    pronunciations with identical, word-safe semantics.
    """
    if re.fullmatch(r"\w+", written):
        return re.sub(r"\b" + re.escape(written) + r"\b", spoken, text, flags=re.IGNORECASE)
    return re.sub(re.escape(written), spoken, text, flags=re.IGNORECASE)


def _normalize_for_speech(text: str, *, site_config: "SiteConfig | None" = None) -> str:
    """Convert written English conventions to natural spoken form."""
    # Simple replacements (DB-configurable via tts_pronunciations).
    for written, spoken in _get_tts_replacements(site_config=site_config):
        text = _apply_spoken_replacement(text, written, spoken)
    # Structural regex patterns (static)
    for pattern, replacement in _SPOKEN_REGEX_STATIC:
        text = pattern.sub(replacement, text)  # type: ignore[call-overload]
    # Acronym replacements (DB-configurable via tts_acronym_replacements)
    for pattern, replacement in _get_acronym_regex(site_config=site_config):
        text = pattern.sub(replacement, text)  # type: ignore[call-overload]
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


async def _build_script_with_llm(
    title: str, content: str, *, site_config: "SiteConfig | None" = None
) -> str:
    """Use the configured LLM provider to rewrite a blog post as a natural podcast script.

    Routes through :func:`services.llm_providers.dispatcher.dispatch_complete`
    so the call honors ``plugin.llm_provider.primary.standard`` (LiteLLM
    on prod). Falls back to regex stripping if the LLM call fails OR if
    no pool is available (tests / bootstrap).
    """
    from services.llm_providers.dispatcher import dispatch_complete

    _sc = _resolve_site_config(site_config)

    # Per-step model pin (podcast_script_model), then the legacy
    # default_ollama_model fallback. Per feedback_no_silent_defaults.md, if
    # both miss we page the operator and let the caller fall back to the
    # regex script. The cost_tier.standard.model indirection was removed.
    pool = getattr(_sc, "_pool", None)
    if pool is None:
        # No DB pool — tests / bootstrap path. Skip the LLM call entirely
        # and use the regex fallback so the episode still renders.
        logger.debug(
            "[PODCAST] no DB pool on site_config; falling back to regex script",
        )
        return _build_script_fallback(title, content, site_config=_sc)

    model = (_sc.get("podcast_script_model") or "").removeprefix("ollama/")
    if not model or model == "auto":
        # Per-step pin unset or left at the "auto" sentinel — fall back to
        # default_ollama_model; page + use the regex script if that's empty too.
        fallback = _sc.get("default_ollama_model") or ""
        if not fallback:
            from services.integrations.operator_notify import notify_operator
            await notify_operator(
                "podcast_service: podcast_script_model is unset/'auto' AND "
                "default_ollama_model is empty — falling back to regex "
                "script for this episode",
                critical=False,
                site_config=_sc,
            )
            return _build_script_fallback(title, content, site_config=_sc)
        model = fallback.removeprefix("ollama/")

    from services.prompt_manager import get_prompt_manager
    prompt = get_prompt_manager().get_prompt(
        "podcast.script_rewrite",
        title=title,
        content=_strip_markdown(content),
    )

    try:
        # Podcast script generation is a long-form completion (up to 8k
        # tokens). 180s is generous for local qwen3:30b/glm-4.7 on a 5090
        # while keeping the pipeline from ever stalling on a stuck model.
        messages = [{"role": "user", "content": prompt}]
        result = await dispatch_complete(
            pool=pool,
            messages=messages,
            model=model,
            tier="standard",
            timeout_s=180,
            temperature=0.4,
            max_tokens=8192,
        )
        script_body = (getattr(result, "text", "") or "").strip()

        if len(script_body) < 200:
            logger.warning(
                "[PODCAST] LLM script too short (%d chars), falling back to regex",
                len(script_body),
            )
            return _build_script_fallback(title, content, site_config=_sc)

        logger.info(
            "[PODCAST] LLM generated %d-char script for '%s'",
            len(script_body), title[:50],
        )

    except Exception as e:
        logger.warning(
            "[PODCAST] LLM script generation failed (%s), falling back to regex", e,
        )
        return _build_script_fallback(title, content, site_config=_sc)

    # Still apply speech normalization for TTS pronunciation fixes
    script_body = _normalize_for_speech(script_body, site_config=_sc)
    spoken_title = _normalize_for_speech(title, site_config=_sc)

    return _wrap_with_intro_outro(script_body, spoken_title, site_config=_sc)


def _build_script_fallback(
    title: str, content: str, *, site_config: "SiteConfig | None" = None
) -> str:
    """Fallback: build script via regex stripping when Ollama is unavailable."""
    _sc = _resolve_site_config(site_config)
    plain_text = _strip_markdown(content)
    plain_text = _normalize_for_speech(plain_text, site_config=_sc)
    spoken_title = _normalize_for_speech(title, site_config=_sc)

    return _wrap_with_intro_outro(plain_text, spoken_title, site_config=_sc)


def _build_intro(spoken_title: str, *, site_config: "SiteConfig | None" = None) -> str:
    """Construct the canonical podcast intro line. Pure function so the
    sibling ``_unwrap_intro_outro`` can reproduce it for stripping."""
    _sc = _resolve_site_config(site_config)
    _pname = _sc.get("podcast_name", "the podcast")
    return f"Welcome to {_pname}. Today's episode: {spoken_title}."


def _spoken_domain(domain: str, *, site_config: "SiteConfig | None" = None) -> str:
    """Render a domain for natural speech.

    Dots become " dot " and the final segment (the TLD) is mapped through the
    DB-configurable ``tts_domain_tld_pronunciations`` table, so ``gladlabs.io``
    is spoken "gladlabs dot eye oh" rather than "gladlabs dot eoh".

    A bare two-letter TLD like ``io`` cannot live in ``tts_pronunciations``:
    those entries also run at the render boundary, where matching "io" inside
    body words like "audio" would corrupt them. Confining the mapping to the
    last domain segment here avoids that.
    """
    import json as _json

    _sc = _resolve_site_config(site_config)
    tld_map: dict[str, str] = {}
    raw_map = _sc.get("tts_domain_tld_pronunciations", "")
    if raw_map:
        try:
            tld_map = {str(k).lower(): str(v) for k, v in _json.loads(raw_map).items()}
        except (ValueError, TypeError, AttributeError):
            logger.warning(
                "tts_domain_tld_pronunciations is not valid JSON — TLD spoken-map skipped"
            )

    parts = domain.split(".")
    if len(parts) > 1:
        spoken_tld = tld_map.get(parts[-1].lower())
        if spoken_tld:
            parts[-1] = spoken_tld
    return " dot ".join(parts)


def _build_outro(*, site_config: "SiteConfig | None" = None) -> str:
    """Construct the canonical podcast outro lines. Pure function so the
    sibling ``_unwrap_intro_outro`` can reproduce it for stripping."""
    _sc = _resolve_site_config(site_config)
    _pname = _sc.get("podcast_name", "the podcast")
    _domain_tts = _spoken_domain(_sc.get("site_domain", "our site"), site_config=_sc)
    return (
        f"Thanks for listening to {_pname}. "
        f"Visit {_domain_tts} for more episodes, articles, and insights. "
        "See you next time."
    )


def _wrap_with_intro_outro(
    script_body: str, spoken_title: str, *, site_config: "SiteConfig | None" = None
) -> str:
    """Prepend / append intro / outro to the spoken body for the podcast.

    Default on — the podcast IS a show, so "Welcome to {name}" makes
    sense there.

    For the video composer's narration sibling, use
    ``_unwrap_intro_outro`` (or build the body-only script directly and
    feed it to a second edge_tts pass via ``PodcastService.generate_episode``,
    which writes ``{post_id}-narration.mp3`` alongside the main file when
    ``podcast_video_narration_sibling_enabled='true'``).
    """
    _sc = _resolve_site_config(site_config)
    if _sc.get("podcast_include_intro", "true").lower() == "true":
        intro = _build_intro(spoken_title, site_config=_sc)
        script_body = f"{intro}\n\n{script_body}"

    if _sc.get("podcast_include_outro", "true").lower() == "true":
        outro = _build_outro(site_config=_sc)
        script_body = f"{script_body}\n\n{outro}"

    return script_body


def _unwrap_intro_outro(
    wrapped: str, spoken_title: str, *, site_config: "SiteConfig | None" = None
) -> str:
    """Inverse of ``_wrap_with_intro_outro`` — return the body-only
    script.

    Strips the canonical intro / outro produced by ``_build_intro`` /
    ``_build_outro`` using exact-prefix / exact-suffix matching. If the
    expected intro/outro isn't found at the boundary, leaves that side
    alone (covers pre-existing scripts that were built before the
    wrapper helper existed, or operator-curated overrides).

    Used by ``PodcastService.generate_episode`` to produce the
    body-only narration sibling MP3 that the video composer mixes in
    — keeps the video from opening with "Welcome to {podcast_name}"
    over a slideshow that isn't framed as a podcast episode.
    """
    body = wrapped or ""

    _sc = _resolve_site_config(site_config)
    if _sc.get("podcast_include_intro", "true").lower() == "true":
        intro = _build_intro(spoken_title, site_config=_sc)
        # The wrapper joins with "\n\n" — strip that separator too.
        if body.startswith(intro + "\n\n"):
            body = body[len(intro) + 2:]
        elif body.startswith(intro):
            body = body[len(intro):].lstrip("\n")

    if _sc.get("podcast_include_outro", "true").lower() == "true":
        outro = _build_outro(site_config=_sc)
        if body.endswith("\n\n" + outro):
            body = body[: -(len(outro) + 2)]
        elif body.endswith(outro):
            body = body[: -len(outro)].rstrip("\n")

    return body.strip()


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
        site_config: SiteConfig,
    ):
        self.output_dir = output_dir or PODCAST_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # SiteConfig is mandatory (#272 Phase-2f — module global deleted).
        self._site_config = _resolve_site_config(site_config)

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

    async def synthesize(
        self,
        script: str,
        *,
        output_path: "Path | str | None" = None,
        key: str = "",
    ) -> tuple[str, int]:
        """Render ``script`` to an MP3 via Kokoro/Speaches TTS with deterministic
        voice rotation. Returns ``(file_path, duration_seconds)``; raises
        ``RuntimeError`` when every voice fails.

        Pure render core for the Stage-3 ``podcast.render`` atom — it owns the
        voice-rotation loop over ``_generate_with_voice`` but NONE of the
        post_id-keyed naming, ``media_assets`` recording, or narration-sibling
        side effects that ``generate_episode`` layers on top. Voice is chosen
        deterministically from ``key`` (e.g. ``task_id``) so a re-render of the
        same task is stable ("calculated, not generated", #689).
        """
        import hashlib
        import tempfile

        if not script or not script.strip():
            raise RuntimeError("podcast.synthesize: empty script")

        if output_path is None:
            fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="podcast-render-")
            os.close(fd)
            out = Path(tmp)
        else:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)

        voice_pool = _resolve_voice_pool(self._site_config)
        rotation_key = key or script
        voice_index = int(
            hashlib.md5(rotation_key.encode(), usedforsecurity=False).hexdigest(), 16,
        ) % len(voice_pool)
        selected = voice_pool[voice_index]
        voices_to_try = [
            selected,
            *[v for v in voice_pool if v != selected],
            *VOICE_FALLBACKS,
        ]

        last_error: str | None = None
        for voice in voices_to_try:
            try:
                result = await self._generate_with_voice(script, voice, out)
                if result.success:
                    return str(out), int(result.duration_seconds or 0)
                last_error = result.error
            except Exception as e:  # noqa: BLE001 — try the next voice
                last_error = f"{voice}: {type(e).__name__}: {e}"
                logger.warning(
                    "[PODCAST] synthesize voice %s failed: %s", voice, last_error,
                )

        raise RuntimeError(
            f"podcast.synthesize: all voices failed. Last error: {last_error}"
        )

    async def generate_episode(
        self,
        post_id: str,
        title: str,
        content: str,
        *,
        force: bool = False,
        pre_generated_script: str | None = None,
        seo_description: str = "",
        seo_keywords: str = "",
    ) -> EpisodeResult:
        """Generate a podcast episode MP3 from blog post content.

        Args:
            post_id: Unique post identifier (used as filename).
            title: Post title (used in the intro).
            content: Full post content (markdown — will be stripped).
            force: Regenerate even if the episode already exists.
            pre_generated_script: If provided, skip LLM script generation and use this script directly.
            seo_description: The post's already-generated SEO meta
                description (``posts.excerpt``). Threaded into the
                ``media_assets`` row so a published episode carries the
                same SEO description as the blog post — reused, NOT
                regenerated (Glad-Labs/poindexter#539). Empty string
                when unknown.
            seo_keywords: The post's already-generated SEO keywords,
                comma-separated (``posts.seo_keywords``). Same reuse
                contract as ``seo_description``.

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
            script = await _build_script_with_llm(
                title, content, site_config=self._site_config
            )

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
        voice_pool = _resolve_voice_pool(self._site_config)
        voice_index = int(
            hashlib.md5(post_id.encode(), usedforsecurity=False).hexdigest(), 16,
        ) % len(voice_pool)
        selected_voice = voice_pool[voice_index]
        # Try selected voice first, then remaining pool voices, then fallbacks
        remaining_pool = [v for v in voice_pool if v != selected_voice]
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
                    # Glad-Labs/poindexter#161 — record media_assets row
                    # so cleanup / retention / cost-attribution find the
                    # podcast file. Best-effort; never propagates.
                    await self._record_episode_asset(
                        post_id=post_id,
                        result=result,
                        voice=voice,
                        title=title,
                        seo_description=seo_description,
                        seo_keywords=seo_keywords,
                    )
                    # Glad-Labs/glad-labs-stack#649 PR 2 — produce the
                    # video-narration sibling MP3 alongside the main
                    # podcast episode. The video composer mixes this in
                    # so the slideshow doesn't open with "Welcome to
                    # {podcast_name}". Best-effort: failure here MUST
                    # NOT take the podcast result down.
                    await self._maybe_generate_narration_sibling(
                        post_id=post_id,
                        script=script,
                        title=title,
                        voice=voice,
                    )
                    return result
                last_error = result.error
            except Exception as e:
                last_error = f"{voice}: {type(e).__name__}: {e}"
                logger.warning("[PODCAST] Voice %s failed: %s", voice, last_error)

        error_msg = f"All voices failed. Last error: {last_error}"
        logger.error("[PODCAST] %s", error_msg)
        return EpisodeResult(success=False, error=error_msg)

    async def _maybe_generate_narration_sibling(
        self,
        *,
        post_id: str,
        script: str,
        title: str,
        voice: str,
    ) -> None:
        """Emit ``{post_id}-narration.mp3`` — body-only TTS for the video
        composer.

        Gated by ``app_settings.podcast_video_narration_sibling_enabled``
        (default ``true``). The video composer in
        ``services/video_service.py::generate_video_for_post`` prefers
        this file over ``{post_id}.mp3`` when present, so the slideshow
        narration is the article body without the "Welcome to {name}"
        intro / "Visit {site} for more" outro.

        Cheap: edge-tts is local, so this is a second local TTS pass on
        already-normalized text. Same voice as the main episode to keep
        the audio identity consistent (the video isn't a different show,
        just a different framing of the same content).

        Never raises — narration sibling generation failure is non-fatal
        (the main episode is fine; the video will just fall back to the
        wrapped MP3 with the leading "Welcome to ..." per the comment in
        ``video_service.generate_video_for_post``).
        """
        try:
            enabled = (
                self._site_config.get(
                    "podcast_video_narration_sibling_enabled", "true",
                ).lower()
                == "true"
            )
        except Exception:
            enabled = True
        if not enabled:
            return

        try:
            spoken_title = _normalize_for_speech(
                title, site_config=self._site_config
            )
            body_only = _unwrap_intro_outro(
                script, spoken_title, site_config=self._site_config
            )
            if len(body_only) < 20:
                logger.debug(
                    "[PODCAST] narration sibling skipped: body-only "
                    "script too short (%d chars) — wrapper probably "
                    "didn't add intro/outro, video will reuse main MP3",
                    len(body_only),
                )
                return
            sibling_path = self.output_dir / f"{post_id}-narration.mp3"
            from services import tts_service
            await tts_service.synthesize_speech(
                body_only,
                site_config=self._site_config,
                output_path=str(sibling_path),
                voice=voice,
            )
            if (
                sibling_path.exists()
                and sibling_path.stat().st_size > 1000
            ):
                logger.info(
                    "[PODCAST] narration sibling: %s (%d bytes, voice=%s)",
                    sibling_path.name,
                    sibling_path.stat().st_size,
                    voice,
                )
            else:
                logger.warning(
                    "[PODCAST] narration sibling produced empty file at %s",
                    sibling_path,
                )
        except Exception as exc:  # noqa: BLE001 — never fatal
            logger.warning(
                "[PODCAST] narration sibling generation failed (non-fatal): %s",
                exc,
            )

    async def _record_episode_asset(
        self,
        *,
        post_id: str,
        result: "EpisodeResult",
        voice: str,
        title: str,
        seo_description: str = "",
        seo_keywords: str = "",
    ) -> None:
        """Best-effort ``media_assets`` insert for the rendered podcast.

        Closes Glad-Labs/poindexter#161 — pre-fix, the legacy podcast
        path produced an MP3 on disk but never wrote the DB row, so
        cleanup / retention / cost-attribution missed it. Failures
        here must NEVER bubble up; the episode itself is fine.

        Reads the asyncpg pool from the injected ``self._site_config``
        (set by ``site_config.load(pool)`` during app startup, threaded
        into the ctor per #272 Phase-2f). Pool is best-effort:
        ``record_media_asset`` itself no-ops cleanly when the pool is None.

        SEO metadata (Glad-Labs/poindexter#539): ``seo_description``
        (from ``posts.excerpt``) and ``seo_keywords`` (comma-separated,
        from ``posts.seo_keywords``) are stamped into the ``metadata``
        JSON so the persisted media item carries the SAME SEO fields the
        blog post already generated. These are REUSED from the stored
        post columns — no LLM regeneration. They mirror what the podcast
        RSS feed (``routes/podcast_routes.py``) surfaces as
        ``<description>`` / ``itunes:keywords`` per episode, and bring the
        podcast media row to parity with the YouTube video payload built
        in ``jobs/backfill_videos.py``. Empty strings are stored
        verbatim (they're the "unset" sentinel, consistent with
        ``posts.excerpt`` being ``''`` when null).
        """
        try:
            from services import media_asset_recorder
        except Exception as exc:  # noqa: BLE001 — defensive import guard
            logger.debug("[PODCAST] media_asset_recorder unavailable: %s", exc)
            return
        pool = getattr(self._site_config, "_pool", None)
        try:
            engine = (
                self._site_config.get("podcast_tts_engine", "speaches")
                or "speaches"
            )
        except Exception:
            engine = "speaches"
        try:
            await media_asset_recorder.record_media_asset(
                pool=pool,
                post_id=post_id,
                asset_type="podcast",
                storage_path=result.file_path or "",
                public_url="",  # podcasts upload separately via R2 sync
                mime_type="audio/mpeg",
                duration_ms=int((result.duration_seconds or 0) * 1000),
                file_size_bytes=result.file_size_bytes or 0,
                provider_plugin=f"tts.{engine}",
                source="pipeline",
                storage_provider="local",
                metadata={
                    "voice": voice,
                    "title": title,
                    "engine": engine,
                    # SEO parity with the blog post (#539) — reused from
                    # posts.excerpt / posts.seo_keywords, never regenerated.
                    "seo_description": seo_description or "",
                    "seo_keywords": seo_keywords or "",
                },
            )
        except Exception as exc:
            logger.debug(
                "[PODCAST] media_assets record failed for %s: %s",
                post_id, exc,
            )

    async def _generate_with_voice(
        self, script: str, voice: str, output_path: Path
    ) -> EpisodeResult:
        """Generate audio via Speaches/Kokoro TTS with the given voice."""
        from services import tts_service

        # Apply DB-configurable pronunciation fixes at the TTS render boundary
        # (e.g. VRAM → "Vee RAM"). The structural normalization runs at script
        # generation (generate_media_scripts); re-applying ONLY the simple
        # pronunciation map here lets operator-tuned pronunciations reach the
        # EXISTING script backlog on re-render without regeneration. Idempotent
        # — the spoken forms never contain their written keys. Uses the same
        # word-boundary helper as _normalize_for_speech so short tokens (CI,
        # MB) fire only as whole words — never inside "social" or "number".
        for written, spoken in _get_tts_replacements(site_config=self._site_config):
            script = _apply_spoken_replacement(script, written, spoken)

        audio_bytes = await tts_service.synthesize_speech(
            script,
            site_config=self._site_config,
            output_path=str(output_path),
            voice=voice,
        )
        if audio_bytes is None:
            return EpisodeResult(
                success=False,
                error=(
                    "Speaches TTS unavailable — set podcast_tts_enabled=true "
                    "and ensure poindexter-speaches container is running"
                ),
            )
        if not output_path.exists() or output_path.stat().st_size == 0:
            return EpisodeResult(
                success=False,
                error=f"Speaches produced empty file with voice {voice!r}",
            )
        size = output_path.stat().st_size
        duration = _estimate_duration_from_text(script)
        return EpisodeResult(
            success=True,
            file_path=str(output_path),
            duration_seconds=duration,
            file_size_bytes=size,
        )


# ---------------------------------------------------------------------------
# Convenience function (fire-and-forget from publish pipeline)
# ---------------------------------------------------------------------------


async def generate_podcast_episode(
    post_id: str,
    title: str,
    content: str,
    *,
    pre_generated_script: str | None = None,
    site_config: SiteConfig,
    seo_description: str = "",
    seo_keywords: str = "",
) -> None:
    """Fire-and-forget podcast generation. Logs errors but never raises.

    ``seo_description`` / ``seo_keywords`` (Glad-Labs/poindexter#539) are
    the post's already-generated SEO fields, forwarded so the episode's
    ``media_assets`` row carries them — reused, never regenerated.
    """
    try:
        svc = PodcastService(site_config=site_config)
        result = await svc.generate_episode(
            post_id, title, content,
            pre_generated_script=pre_generated_script,
            seo_description=seo_description,
            seo_keywords=seo_keywords,
        )
        if not result.success:
            logger.warning("[PODCAST] Failed for post %s: %s", post_id, result.error)
    except Exception as e:
        logger.warning("[PODCAST] Unexpected error for post %s: %s", post_id, e)
