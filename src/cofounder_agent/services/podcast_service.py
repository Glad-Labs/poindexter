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

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.image_markers import strip_unresolved_image_markers
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

    A disabled flag OR an empty ``tts_voice_pool`` falls through to the module
    ``VOICE_POOL`` constant. This resolves *which* pool to use; whether to rotate
    over it or pin the single ``podcast_tts_voice`` is decided by
    ``_select_voice`` (rotation is opt-in). An operator supplies engine-
    appropriate voice names via ``tts_voice_pool`` without touching code.
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


def _select_voice(site_config: "SiteConfig | None", rotation_key: str) -> str:
    """Pick the narration voice. **Rotation is opt-in.**

    ``tts_voice_rotation_enabled`` (default ``false``) is the master switch:

    - disabled (the default) → the single ``podcast_tts_voice`` (falling back to
      the first pool entry when unset). No rotation — one stable brand voice.
    - enabled → deterministically hash-rotate ``rotation_key`` over
      ``_resolve_voice_pool`` for variety across episodes.

    This is the seam that makes ``podcast_tts_voice`` actually take effect. The
    callers pass the returned voice explicitly to ``_generate_with_voice``, so
    before this helper a *disabled* flag still rotated — the flag only ever gated
    the pool *source* (``_resolve_voice_pool``), never the rotation itself, and
    ``podcast_tts_voice`` was dead config. The video narration reuses the podcast
    voice, so honoring the flag here fixes both surfaces.
    """
    rotate = False
    fixed = VOICE_POOL[0]
    if site_config is not None:
        try:
            rotate = bool(site_config.get_bool("tts_voice_rotation_enabled", False))
        except Exception:  # noqa: BLE001 — defensive; any read failure → no rotation
            rotate = False
        if not rotate:
            try:
                fixed = str(site_config.get("podcast_tts_voice", "") or "").strip() or VOICE_POOL[0]
            except Exception:  # noqa: BLE001
                fixed = VOICE_POOL[0]
    if not rotate:
        return fixed
    pool = _resolve_voice_pool(site_config)
    # usedforsecurity=False — MD5 here picks a stable index from rotation_key,
    # not an integrity check; bandit B324 is a false positive on this path.
    index = int(
        hashlib.md5(rotation_key.encode(), usedforsecurity=False).hexdigest(), 16,
    ) % len(pool)
    return pool[index]


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
    # file.ext — the stem MUST contain a letter and the extension MUST be
    # purely alphabetic, or this rule silently eats decimal numbers. The
    # original `[\w/\\]+\.\w{2,4}` matched the "1.65 " inside "$1.65 trillion"
    # (stem "1", "extension" "65") and deleted it, so a podcast announced
    # "The $ Trillion Secret" and read the body line "hidden debt at around
    # trillion dollars" — the episode's central figure, gone. Money, versions
    # and stats are the whole point of a sentence; a filename is noise. Prefer
    # leaving a stray "2024.csv" spoken over deleting a number.
    (re.compile(r"[\w/\\]*[A-Za-z][\w/\\]*\.[A-Za-z]{2,4}(?:\s|$)"), " "),
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
        # Surface the silent breakage. A single typo disables the WHOLE table
        # (every written→spoken pronunciation skipped), which reads as "the TTS
        # is just bad" until someone digs through logs. Emit a deduped finding so
        # it reaches the findings board / Discord instead of rotting behind a log
        # line (feedback_self_heal_not_suppress / feedback_no_silent_defaults).
        from utils.findings import emit_finding

        emit_finding(
            source="podcast_service",
            kind="tts_pronunciations_invalid_json",
            title="tts_pronunciations is not valid JSON — pronunciation table disabled",
            body=(
                "The tts_pronunciations app_setting failed to parse, so every "
                "written→spoken pronunciation is being skipped at TTS time. Fix "
                "the JSON via `poindexter settings set tts_pronunciations '{...}'`."
            ),
            severity="warning",
            dedup_key="tts_pronunciations_invalid_json",
        )
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


# Model-identifier families whose version/quant tails get normalized for
# speech. A pin like ``gemma-4-31B-it-qat:latest`` reads awful token-by-token
# ("gemma dash four dash thirty-one B dash it dash qat colon latest"); we want
# just "gemma four thirty-one B" — family + version/size, dropping the quant
# (it-qat), tag (:latest) and GPU (-5090) noise. DB-tunable via
# ``tts_model_name_families`` so an operator running other models can extend
# the list without a code change (feedback_db_first_config).
_DEFAULT_MODEL_FAMILIES: tuple[str, ...] = (
    "gemma", "glm", "qwen", "phi", "llama", "mistral", "mixtral",
    "deepseek", "codellama", "qwq", "granite", "nemotron", "smollm", "kokoro",
)


def _get_model_families(*, site_config: "SiteConfig | None" = None) -> tuple[str, ...]:
    """Model families for the speech normalizer, from ``tts_model_name_families``.

    A CSV in the DB; falls back to :data:`_DEFAULT_MODEL_FAMILIES` when unset so
    a fresh install still speaks model names cleanly before the seed lands.
    """
    _sc = _resolve_site_config(site_config)
    raw = (_sc.get("tts_model_name_families", "") or "").strip()
    if not raw:
        return _DEFAULT_MODEL_FAMILIES
    fams = tuple(f.strip().lower() for f in raw.split(",") if f.strip())
    return fams or _DEFAULT_MODEL_FAMILIES


def _normalize_model_names(text: str, *, families: tuple[str, ...]) -> str:
    """Speak model identifiers as family + version/size, dropping config noise.

    ``gemma-4-31B-it-qat:latest`` → ``gemma 4 31B``, ``glm-4.7-5090`` →
    ``glm 4.7``, ``qwen3:30b`` → ``qwen 3 30b``, ``phi4`` → ``phi 4``.

    Only tokens anchored on a known *family* AND carrying a real version/size
    (a ``\\d+B`` size, a ``\\d.\\d`` decimal, or a family-glued version) are
    rewritten — prose that merely reuses a family word ("llama farm", "phi
    coefficient", "Philadelphia", the compiler term "phi-node-2") is left
    untouched. Runs before the pronunciation map so a split-off family (``glm``)
    still picks up its ``GLM → G L M`` spoken form.
    """
    if not families:
        return text

    fam_alt = "|".join(re.escape(f) for f in sorted(families, key=len, reverse=True))
    # family, then an optional family-glued version (phi"4", qwen"3"), then an
    # optional [-:]-delimited config tail. Version/tail segments must END in an
    # alphanumeric so a trailing sentence period ("gemma-4-31B.") isn't consumed.
    pattern = re.compile(
        rf"\b(?P<fam>{fam_alt})"
        r"(?P<ver>\d(?:[\w.]*[A-Za-z0-9])?)?"
        r"(?P<tail>(?:[-:][A-Za-z0-9._]*[A-Za-z0-9])+)?",
        re.IGNORECASE,
    )

    def _keep(seg: str) -> bool:
        # Keep a size (31B/30b/7B), a decimal version (4.7/2.5), or a short
        # integer (4/31) — never a 4+-digit GPU model number (5090) or a quant
        # format (q4_K_M, fp16), which either exceed the digit run or don't
        # start with a bare digit run at all.
        if re.fullmatch(r"\d{4,}", seg):
            return False
        return re.fullmatch(r"\d+[Bb]|\d+\.\d+|\d+", seg) is not None

    def _repl(m: "re.Match[str]") -> str:
        fam = m.group("fam")
        ver = m.group("ver") or ""
        segs = [s for s in re.split(r"[-:]", m.group("tail") or "") if s]
        # A model identifier needs a real version/size signal; without one this
        # is a same-spelled English word — leave the whole match untouched.
        model_like = bool(ver) or any(re.fullmatch(r"\d+[Bb]|\d+\.\d+", s) for s in segs)
        if not model_like:
            return m.group(0)
        kept = ([ver] if ver else []) + [s for s in segs if _keep(s)]
        return fam + "".join(f" {k}" for k in kept)

    return pattern.sub(_repl, text)


# Emoji / pictograph ranges — models decorate short-form scripts with these
# (clock/laptop/rocket pictographs observed frozen into a short, then read
# into the render); TTS either skips them or names them aloud, and captions
# show them verbatim. Scripts are spoken artifacts, so strip at generation.
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"  # symbols, pictographs, transport, supplemental
    "\U00002700-\U000027bf"  # dingbats
    "\U0001f1e6-\U0001f1ff"  # regional indicators
    "\u2600-\u26ff"          # misc symbols
    "\ufe0f\u200d"           # variation selector + ZWJ (emoji glue)
    "]+",
)


def _normalize_for_script(text: str, *, site_config: "SiteConfig | None" = None) -> str:
    """Structural cleanup for a STORED narration script (2026-08-01 split).

    Everything here improves the script in every medium — the stored artifact,
    the QA fidelity reference, and the audio: URL/filename removal, spoken
    version numbers, parenthetical-to-pause, emoji strip, dash/semicolon to
    comma, quote/ellipsis/whitespace hygiene.

    What it deliberately does NOT do is apply pronunciation opinions — the
    ``tts_pronunciations`` / ``tts_acronym_replacements`` maps and the
    model-name collapse. Those used to run at generation too, which FROZE
    phonetic spellings into the stored scripts ("See Eye See Dee pipeline",
    "git hub Actions", "Vee RAM") with zero audio benefit: the TTS render
    boundary (``_generate_with_voice``) applies the speech pass itself, so
    baking phonetics in only made the scripts and everything derived from
    them read wrong. Pronunciations now live exclusively at the TTS boundary.
    """
    # Structural regex patterns (static)
    for pattern, replacement in _SPOKEN_REGEX_STATIC:
        text = pattern.sub(replacement, text)  # type: ignore[call-overload]
    # Emoji/pictographs — never part of a spoken script.
    text = _EMOJI_RE.sub("", text)
    # Smart quotes → straight quotes (TTS handles these better)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    # Ellipsis → pause
    text = text.replace("\u2026", "...")
    # Dashes and semicolons → comma pauses. Models lean hard on em-dashes and
    # semicolons; TTS treats them all as roughly the same pause, and in the
    # stored script a comma reads naturally where "giants; Alphabet,
    # Microsoft" does not. Spoken prose has no semicolon semantics to lose.
    text = re.sub(r"\s*[\u2014\u2013]\s*", ", ", text)
    text = re.sub(r"\s*;\s*", ", ", text)
    # Clean up double spaces and comma-space issues
    text = re.sub(r"  +", " ", text)
    text = re.sub(r",\s*,", ",", text)
    return text


def _normalize_for_speech(text: str, *, site_config: "SiteConfig | None" = None) -> str:
    """Convert written English conventions to natural spoken form.

    The full TTS-input pass: the structural script cleanup plus every
    pronunciation opinion (model-name collapse, ``tts_pronunciations``,
    ``tts_acronym_replacements``). Applied at the TTS render boundary; safe
    (idempotent) on the frozen backlog of scripts that were generated when
    the pronunciation maps still ran at generation time.
    """
    # Model identifiers first — collapse gemma-4-31B-it-qat:latest → "gemma 4
    # 31B" BEFORE the pronunciation map, so the split-off family still gets its
    # spoken form (glm → G L M) instead of being stranded in a config token.
    text = _normalize_model_names(text, families=_get_model_families(site_config=site_config))
    # Simple replacements (DB-configurable via tts_pronunciations).
    for written, spoken in _get_tts_replacements(site_config=site_config):
        text = _apply_spoken_replacement(text, written, spoken)
    # Shared structural pass (URLs/filenames, versions, parentheticals, emoji,
    # dashes, quotes, whitespace).
    text = _normalize_for_script(text, site_config=site_config)
    # Acronym replacements (DB-configurable via tts_acronym_replacements)
    for pattern, replacement in _get_acronym_regex(site_config=site_config):
        text = pattern.sub(replacement, text)  # type: ignore[call-overload]
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
    # Narration must never speak a marker aloud — all forms, not just
    # the bare numbered one.
    text = strip_unresolved_image_markers(text)
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


# ---------------------------------------------------------------------------
# Scaffold-dump guard — reject an LLM script that leaks its own prompt/plan
# ---------------------------------------------------------------------------

# A clean podcast script is pure spoken prose: the ``podcast.script_rewrite``
# prompt explicitly forbids markdown, asterisks, and brackets. So a leading
# block dominated by bullet / outline / checklist lines is unambiguously the
# model dumping its prompt-echo + planning outline + self-QA checklist ahead of
# the narration instead of "just output the script" — a known gemma-class
# instruction-following failure that TTS would otherwise read aloud verbatim
# (the podcast sibling of the blog planning_dump the writer guards via #2036).
# Structure alone is the signal here: unlike a blog body (which can open with a
# legitimate list, so the validator needs a vocabulary gate), the podcast
# script has NO legitimate bullets to protect.
_SCAFFOLD_BULLET_RE = re.compile(r"^[ \t]*(?:[*+•\-]|\d+[.)])[ \t]+\S")
_SCAFFOLD_SCAN_LINES = 30          # opening window, in non-blank lines
_SCAFFOLD_MIN_BULLETS = 5          # a real block, not one incidental dash
_SCAFFOLD_MIN_BULLET_SHARE = 0.5   # bullets dominate the opening


def _looks_like_scaffold_dump(script_body: str) -> bool:
    """True when the LLM podcast script OPENS with a bullet/outline/checklist
    dump instead of narration.

    The ``podcast.script_rewrite`` prompt forbids markdown, so a clean script is
    pure prose; a bullet-dominated opening is the model echoing the prompt rules
    + its planning outline + a self-QA checklist ahead of the real script. The
    caller falls back to the deterministic regex script rather than narrate the
    scaffold aloud. Structure-only (no vocabulary gate) because the podcast
    script has no legitimate bullets, unlike a blog body.
    """
    window = [ln for ln in (script_body or "").splitlines() if ln.strip()][
        :_SCAFFOLD_SCAN_LINES
    ]
    if len(window) < _SCAFFOLD_MIN_BULLETS:
        return False
    bullets = sum(1 for ln in window if _SCAFFOLD_BULLET_RE.match(ln))
    return (
        bullets >= _SCAFFOLD_MIN_BULLETS
        and bullets / len(window) >= _SCAFFOLD_MIN_BULLET_SHARE
    )


def _emit_scaffold_dump_finding(*, title: str) -> None:
    """Loud-but-recovered canary: the podcast script LLM opened with a scaffold/
    planning dump and we fell back to the deterministic script. Self-heal is not
    silent (feedback_self_heal_not_suppress) — the episode still renders clean
    (from the article body) while the model-quality signal stays visible on the
    Findings dashboard. ``severity='warn'`` → Discord."""
    try:
        from utils.findings import emit_finding
    except Exception:  # noqa: BLE001  # silent-ok: emit is best-effort; the WARNING log already surfaced the dump
        return
    try:
        emit_finding(
            source="services.podcast_service",
            kind="podcast_scaffold_dump",
            title=(
                "Podcast script LLM emitted a scaffold/planning dump — "
                "used deterministic fallback"
            ),
            body=(
                "The podcast.script_rewrite model opened its output with a "
                "bullet/outline/checklist dump (prompt-echo + planning outline "
                "+ self-QA checklist) instead of the narration. TTS would have "
                "read the whole scaffold aloud. podcast_service fell back to the "
                "deterministic script (the article body read as speech) so the "
                "episode is clean. If this recurs, the podcast_script_model "
                "(gemma-class) is dumping its plan on long prompts — consider a "
                "stronger script model."
            ),
            severity="warn",
            dedup_key=f"podcast_scaffold_dump:{title[:80]}",
        )
    except Exception:  # noqa: BLE001  # silent-ok: finding emission must never raise; the WARNING log is the durable signal
        pass


def _resolve_podcast_think(site_config: "SiteConfig | None") -> bool | None:
    """``think`` flag for the podcast script LLM dispatch.

    Returns ``False`` (disable the reasoning channel) when
    ``podcast_disable_thinking`` is on — the default. The gemma-class script
    model (``podcast_script_model``) is thinking-capable: with the channel live
    it writes a prompt-echo + planning outline + self-QA checklist into its
    reasoning on every run, and ~71% of the time that plan LEAKS into the visible
    ``content`` instead of the reasoning channel, so TTS reads the scaffold aloud
    (the ``podcast_scaffold_dump`` finding; 2026-07-07 reproduced in-container).
    Disabling thinking removes the plan at the source — nothing to leak — the
    same fix the writer (#2163) and video director (#2191) paths took. The #2186
    scaffold guard stays as the safety net.

    Returns ``None`` (leave the backend default — thinking on) only when an
    operator explicitly sets the flag off. ``think`` is forwarded to Ollama for
    local models and dropped for cloud targets by the LiteLLM provider, so
    ``False`` is safe for a non-thinking or cloud script model too.
    """
    if site_config is None:
        return False
    try:
        raw = site_config.get("podcast_disable_thinking", "true")
        return False if str(raw).lower() in ("true", "1", "yes") else None
    except Exception:  # noqa: BLE001 — optional feature flag, never load-bearing
        # silent-ok: default to disabling thinking (the safe, reliable path)
        return False


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
        # Disable the script model's reasoning channel (default) so its planning
        # outline + self-QA checklist never leaks into the spoken narration — the
        # podcast_scaffold_dump root cause (mirrors the writer #2163 / director
        # #2191 paths). Only forward ``think`` when resolved; skip on None so an
        # operator opt-out leaves the backend default rather than pinning it.
        think = _resolve_podcast_think(_sc)
        think_kwargs: dict[str, Any] = {} if think is None else {"think": think}
        messages = [{"role": "user", "content": prompt}]
        # ``phase`` is load-bearing beyond attribution: dispatch_complete
        # back-fills num_ctx as ``<phase>_num_ctx`` -> ``ollama_num_ctx``, so
        # without one this call inherited the 8192 global — a TOTAL window that
        # prompt + output share. Asking for max_tokens=8192 inside it was
        # unsatisfiable with an article-sized prompt, and 16 scripts were cut
        # off at exactly 8192. Tune via ``podcast_script_num_ctx``.
        result = await dispatch_complete(
            pool=pool,
            messages=messages,
            model=model,
            tier="standard",
            phase="podcast_script",
            timeout_s=180,
            temperature=0.4,
            max_tokens=8192,
            **think_kwargs,
        )
        script_body = (getattr(result, "text", "") or "").strip()

        if len(script_body) < 200:
            logger.warning(
                "[PODCAST] LLM script too short (%d chars), falling back to regex",
                len(script_body),
            )
            return _build_script_fallback(title, content, site_config=_sc)

        # Scaffold-dump guard: gemma-class models sometimes emit their
        # prompt-echo + planning outline + self-QA checklist AHEAD of the
        # narration instead of "just output the script" — TTS would read the
        # whole scaffold aloud. A clean podcast script is pure prose, so a
        # bullet-dominated opening is an unambiguous dump: discard it and use
        # the deterministic regex script (the already-clean article body read
        # as speech). Not silent — emit a finding so recurring dumps surface
        # the model-quality signal (feedback_self_heal_not_suppress).
        if _looks_like_scaffold_dump(script_body):
            logger.warning(
                "[PODCAST] LLM emitted a scaffold/planning dump for '%s' — "
                "falling back to the deterministic script",
                title[:50],
            )
            _emit_scaffold_dump_finding(title=title)
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


def _append_podcast_cta(
    script: str, *, site_config: "SiteConfig | None" = None
) -> str:
    """Append the per-medium podcast CTA (``media.cta.podcast``) to a finished
    podcast script.

    The Stage-3 ``podcast.render`` path adds this "rate & review" ask via
    ``_narration_render.compose_narration_text``; ``PodcastService.generate_episode``
    (the manual-regenerate path) historically did NOT, so a regenerated episode
    silently lost the CTA the original render carried. This restores parity.

    Idempotent — never double-appends. Returns ``script`` unchanged when the CTA
    is unset/empty. The generic ``_build_outro`` (already baked into the script)
    is preserved ahead of the CTA, matching the original Stage-3 output.
    """
    _sc = _resolve_site_config(site_config)
    cta = (_sc.get("media.cta.podcast", "") or "").strip()
    if not cta:
        return script
    if script.rstrip().endswith(cta):
        return script
    return f"{script.rstrip()}\n\n{cta}"


# ---------------------------------------------------------------------------
# Duplicate-title guard — the show intro announces the episode exactly ONCE
# ---------------------------------------------------------------------------

# ``_build_intro`` already says "Welcome to {show}. Today's episode: {title}.",
# but the script model is handed ``ARTICLE TITLE:`` and routinely opens with its
# own greeting ("Welcome to today's episode, titled X.") or a bare title echo
# ("X.") — so prepending the canonical intro named the episode twice in a row.
# 3 of the 4 episodes rendered 2026-08-06..08 opened that way, so the prompt
# rule added alongside this is the soft half of the fix and THIS is the
# guarantee: a deterministic strip, applied where the intro is prepended AND
# again at the render boundary (Stage-1 scripts persist for days before Stage-3
# renders them, so a generator-only fix would leave the backlog stuttering).
# Idempotent by construction — running it on an already-clean script is a no-op.

_TITLE_ECHO_MAX_CHARS = 240        # an echo is a line, never a paragraph
_TITLE_ECHO_MAX_STRIPS = 2         # greeting line + bare title line, at most
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?:])\s+")
_INTRO_GREETING_FALLBACK = (
    "welcome to,today's episode,in this episode,in today's episode,"
    "hello and welcome,you're listening to,this episode,on this episode"
)


def _echo_key(text: str) -> str:
    """Comparison key for title matching — case/punctuation/space-insensitive.

    "The Tell in the Pipeline." and "the tell in the pipeline" collapse to the
    same key, so a model that re-cases or re-punctuates the title is still
    caught.
    """
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _get_intro_greetings(*, site_config: "SiteConfig | None" = None) -> tuple[str, ...]:
    """Greeting openers that mark a line as the model's own episode intro.

    DB-configurable (``podcast_intro_echo_phrases``) because the phrasing is
    show/locale flavour, not logic — a Spanish-language show needs its own list.
    """
    _sc = _resolve_site_config(site_config)
    raw = _sc.get("podcast_intro_echo_phrases", _INTRO_GREETING_FALLBACK) or ""
    return tuple(k for k in (_echo_key(p) for p in raw.split(",")) if k)


def _strip_one_title_echo(
    body: str, title_key: str, greetings: tuple[str, ...]
) -> str | None:
    """Drop ONE leading title-echo sentence from ``body``; ``None`` if absent.

    Only the first line is considered, and only its first sentence — so a
    paragraph that merely happens to mention the title survives untouched. Two
    shapes are stripped: the bare echo ("The Gap Nobody Names.") and a greeting
    that names the episode ("Welcome to today's episode, titled X.").
    """
    leading = body.lstrip("\n")
    line, _, rest = leading.partition("\n")
    line = line.strip()
    if not line or len(line) > _TITLE_ECHO_MAX_CHARS:
        return None

    parts = _SENTENCE_SPLIT_RE.split(line, maxsplit=1)
    head = parts[0].strip()
    tail = parts[1].strip() if len(parts) > 1 else ""
    head_key = _echo_key(head)
    if not head_key:
        return None

    is_bare_echo = head_key == title_key
    is_greeting = title_key in head_key and any(g in head_key for g in greetings)
    if not (is_bare_echo or is_greeting):
        return None

    if tail:
        # The echo shared a line with real narration — keep the remainder in
        # place, including whatever followed the line break.
        return f"{tail}\n{rest}" if rest else tail
    return rest.lstrip("\n")


def _split_canonical_intro(
    script: str, *, site_config: "SiteConfig | None" = None
) -> tuple[str, str, str]:
    """Split an already-wrapped script into (prefix, title, body).

    A Stage-1 artifact arrives at render time with ``_build_intro`` already
    prepended, so the title it announced is recoverable from the line itself —
    which is what lets the render boundary dedupe without being told the title.
    Returns ``("", "", script)`` when no canonical intro leads the script.
    """
    _sc = _resolve_site_config(site_config)
    head = f"Welcome to {_sc.get('podcast_name', 'the podcast')}. Today's episode: "
    if not script.startswith(head):
        return "", "", script

    idx = script.find("\n")
    if idx == -1:
        return "", "", script
    end = idx
    while end < len(script) and script[end] == "\n":
        end += 1
    intro_line = script[:idx]
    return script[:end], intro_line[len(head):].strip().rstrip("."), script[end:]


def dedupe_episode_title(
    script: str,
    *,
    spoken_title: str | None = None,
    site_config: "SiteConfig | None" = None,
) -> str:
    """Remove a redundant episode-title announcement from the script opening.

    Accepts either shape:

    * a body with no intro yet (generation time) — pass ``spoken_title``;
    * an already-wrapped script (render time) — the title is recovered from the
      canonical intro line, so persisted Stage-1 scripts self-heal on re-render.

    Returns ``script`` unchanged when nothing matches, when the title can't be
    determined, or when stripping would empty the script.
    """
    if not (script or "").strip():
        return script
    if site_config is None:
        # Deliberately NOT the fail-loud ``_resolve_site_config`` path: this is
        # a cosmetic pass on the way to the TTS boundary, and its only caller
        # without a SiteConfig is the fail-soft ``podcast.render`` atom, which
        # is already returning an empty audio path for that same reason. Raising
        # here would convert "no episode" into "graph crash" and hide nothing —
        # the missing config is surfaced by the render no-op itself.
        return script

    prefix, recovered, body = _split_canonical_intro(script, site_config=site_config)
    title_key = _echo_key(spoken_title if spoken_title is not None else recovered)
    if not title_key:
        return script

    greetings = _get_intro_greetings(site_config=site_config)
    cleaned = body
    for _ in range(_TITLE_ECHO_MAX_STRIPS):
        candidate = _strip_one_title_echo(cleaned, title_key, greetings)
        if candidate is None or not candidate.strip():
            break
        cleaned = candidate

    if cleaned == body:
        return script
    logger.info(
        "[PODCAST] stripped duplicate episode-title opening (%d chars)",
        len(body) - len(cleaned),
    )
    return prefix + cleaned


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
        # Strip the model's own greeting / title echo FIRST — otherwise the
        # canonical intro below announces the episode name a second time.
        script_body = dedupe_episode_title(
            script_body, spoken_title=spoken_title, site_config=_sc,
        )
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
        selected = _select_voice(self._site_config, key or script)
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

        # Render boundary: a script persisted before the duplicate-title guard
        # (or handed in by an operator) can still open by naming the episode
        # twice. No-op on a clean script.
        script = dedupe_episode_title(script, site_config=self._site_config)

        if not script.strip():
            return EpisodeResult(success=False, error="Empty content after markdown stripping")

        logger.info(
            "[PODCAST] Generating episode for '%s' (%d chars script)",
            title[:60],
            len(script),
        )

        # Voice selection honors tts_voice_rotation_enabled (opt-in): off (the
        # default) pins the single podcast_tts_voice; on hash-rotates the pool by
        # post_id for variety. The narration sibling below reuses selected_voice,
        # so the video narration follows the same choice.
        voice_pool = _resolve_voice_pool(self._site_config)
        selected_voice = _select_voice(self._site_config, post_id)
        # Try selected voice first, then remaining pool voices, then fallbacks
        remaining_pool = [v for v in voice_pool if v != selected_voice]
        voices_to_try = [selected_voice, *remaining_pool, *VOICE_FALLBACKS]
        last_error = None
        logger.info("[PODCAST] Voice selected '%s' for post %s",
                    selected_voice, post_id[:12])

        # Append the per-medium review CTA (media.cta.podcast) to the MAIN
        # podcast episode only — restoring parity with the Stage-3 podcast.render
        # path. The video-narration sibling below keeps the plain `script`: video
        # carries its own media.cta.video ("like & subscribe"), never the
        # podcast's "rate & review on Spotify/Apple".
        episode_script = _append_podcast_cta(script, site_config=self._site_config)

        for voice in voices_to_try:
            try:
                result = await self._generate_with_voice(episode_script, voice, output_path)
                if result.success:
                    # Intro/outro sting — parity with the Stage-3 podcast.render
                    # atom. Deliberately BEFORE _record_episode_asset: that stamps
                    # duration_ms / file_size_bytes straight off `result`, and the
                    # mixed cut is both longer and larger than the dry one.
                    result = await self._maybe_mix_sting(
                        result, post_id=post_id, output_path=output_path,
                    )
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
                    # Glad-Labs/poindexter#649 PR 2 — produce the
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

    async def _maybe_mix_sting(
        self,
        result: "EpisodeResult",
        *,
        post_id: str,
        output_path: Path,
    ) -> "EpisodeResult":
        """Mix the show's intro/outro sting over a freshly rendered episode.

        Parity with the Stage-3 ``podcast.render`` atom (#690): the sting used
        to be mixed ONLY on the graph path, so regenerating an episode from the
        console / API silently produced a dry cut — the same parity gap
        ``_append_podcast_cta`` closed for the per-medium CTA.

        There is no Stage-1 snapshot here, so the resolver is asked for the
        curated show theme directly: a per-episode generated sting belongs to
        the pipeline run that made it, and its temp path would be long gone.

        Fail-soft — returns ``result`` untouched on any problem, because losing
        polish must never lose the episode. On success returns a REFRESHED
        result: the caller stamps ``media_assets`` from these numbers, and the
        mixed file is both longer and larger than the dry cut.
        """
        sc = self._site_config
        if str(sc.get("podcast_sting_mix_enabled", "true") or "").lower() != "true":
            return result

        from services.podcast_sting_mixer import (
            mix_intro_outro,
            probe_duration_s,
            resolve_sting_path,
        )
        from utils.findings import emit_finding

        sting = resolve_sting_path(None, sc)
        if not sting.path:
            if sting.expected:
                # Configured but unusable. Never silent: a dry episode where
                # the operator asked for a theme is a quality downgrade
                # (feedback_flag_quality_downgrades).
                emit_finding(
                    source="podcast_service",
                    kind="podcast_sting_missing",
                    title="podcast: no usable sting — regenerated episode has no music",
                    body=(
                        f"Regenerating post {post_id} produced a dry cut: "
                        f"{sting.detail}."
                    ),
                    severity="warn",
                    dedup_key=f"podcast_sting_missing:{post_id}",
                    extra={"post_id": str(post_id)},
                )
            return result

        mixed = await mix_intro_outro(
            str(output_path), sting.path, site_config=sc, task_id=post_id,
        )
        if not mixed:
            emit_finding(
                source="podcast_service",
                kind="podcast_sting_mix_failed",
                title="podcast: sting mix failed — regenerated episode has no music",
                body=(
                    f"A sting ({sting.source}) was available for post {post_id} "
                    "but the ffmpeg mix failed (see log '[sting_mixer]'); the "
                    "dry narration was kept."
                ),
                severity="warn",
                dedup_key=f"podcast_sting_mix_failed:{post_id}",
                extra={"post_id": str(post_id), "sting": sting.path},
            )
            return result

        try:
            # shutil.move, NOT os.replace: the mix lands in a tempdir that is a
            # different mount from the podcast volume, where os.replace raises
            # EXDEV. mix_intro_outro never touches the original, so a failure
            # here leaves the dry episode intact at output_path.
            import shutil

            shutil.move(mixed, str(output_path))
        except OSError as exc:
            logger.warning(
                "[PODCAST] could not install mixed episode for %s: %s", post_id, exc,
            )
            try:
                os.unlink(mixed)
            except OSError:  # silent-ok: best-effort temp cleanup on a path
                # that already failed and is being reported above.
                pass
            return result

        duration = await probe_duration_s(str(output_path))
        logger.info(
            "[PODCAST] mixed %s sting into episode %s", sting.source, post_id,
        )
        return EpisodeResult(
            success=True,
            file_path=str(output_path),
            # Probed, not estimated — the dry-cut estimate is wrong by the
            # sting's intro + outro windows. Falls back to the old estimate
            # only if ffprobe can't read the file we just wrote.
            duration_seconds=(
                int(duration) if duration else result.duration_seconds
            ),
            file_size_bytes=output_path.stat().st_size,
        )

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
        (default ``true``). The video renderer prefers this file over
        ``{post_id}.mp3`` when present, so the video narration is the
        article body without the "Welcome to {name}" intro / "Visit
        {site} for more" outro.

        Cheap: edge-tts is local, so this is a second local TTS pass on
        already-normalized text. Same voice as the main episode to keep
        the audio identity consistent (the video isn't a different show,
        just a different framing of the same content).

        Never raises — narration sibling generation failure is non-fatal
        (the main episode is fine; the video path just falls back to the
        wrapped MP3 with the leading "Welcome to ...").
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
            # Same guard as _image_helpers._record_inline_image_asset and
            # source_featured_image._record_featured_image_asset — shared
            # dedup_key, one cooldown for one underlying import break.
            from utils.findings import emit_finding

            emit_finding(
                source="podcast_service",
                kind="media_asset_recorder_unavailable",
                title="media_asset_recorder unavailable — podcast asset not recorded",
                body=f"_record_episode_asset: {exc}. This podcast episode's media row was never persisted.",
                dedup_key="media_asset_recorder_unavailable",
            )
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
        """Generate audio via the configured ``podcast_tts_engine``.

        Default (unset/``speaches``) uses Speaches/Kokoro. ``chatterbox``
        delegates to :func:`_generate_with_chatterbox` (Phase 2 cutover —
        the emotion-capable voice-clone engine).
        """
        from services import tts_service

        # Apply the FULL speech pass at the TTS render boundary (2026-08-01
        # split): model-name collapse + tts_pronunciations + structural pass +
        # tts_acronym_replacements. Scripts are now stored CLEAN
        # (_normalize_for_script only — no phonetics baked in), so this is the
        # single place written English becomes spoken English. Idempotent on
        # the frozen backlog of pre-split scripts that already carry phonetic
        # spellings — the spoken forms never contain their written keys.
        script = _normalize_for_speech(script, site_config=self._site_config)

        engine = str(self._site_config.get("podcast_tts_engine", "") or "").strip()
        if engine == "chatterbox":
            return await self._generate_with_chatterbox(script, voice, output_path)

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

    async def _generate_with_chatterbox(
        self, script: str, voice: str, output_path: Path
    ) -> EpisodeResult:
        """Generate audio via the Chatterbox sidecar (Phase 2 engine cutover).

        Config comes entirely from ``plugin.tts_provider.chatterbox.*``
        app_settings — including ``audio_prompt_path``, the zero-shot voice
        clone pinned by the operator (empty on OSS = the sidecar's own
        built-in voice). Never raises: a provider failure (sidecar down, bad
        config) becomes ``EpisodeResult(success=False)``, the same failure
        contract the Speaches path uses, so ``generate_episode`` doesn't need
        to know which engine ran.

        Also forwards the shared ``podcast_tts_remux_bitrate`` /
        ``podcast_tts_loudnorm_*`` settings (audio-fidelity fix) — previously
        read for the Speaches path only, so tuning them had zero effect on
        Chatterbox, the engine actually live in production.
        """
        from services.tts_providers.chatterbox import ChatterboxTTSProvider

        sc = self._site_config
        config = {
            "base_url": sc.get(
                "plugin.tts_provider.chatterbox.base_url",
                "http://chatterbox:8000/v1",
            ),
            "model": sc.get("plugin.tts_provider.chatterbox.model", "chatterbox"),
            "response_format": sc.get("podcast_tts_format", "mp3") or "mp3",
            "exaggeration": sc.get("plugin.tts_provider.chatterbox.exaggeration", "0.5"),
            "cfg_weight": sc.get("plugin.tts_provider.chatterbox.cfg_weight", "0.5"),
            "timeout_s": sc.get("plugin.tts_provider.chatterbox.timeout_s", "600"),
            "audio_prompt_path": sc.get(
                "plugin.tts_provider.chatterbox.audio_prompt_path", "",
            ),
            "atempo": sc.get("plugin.tts_provider.chatterbox.atempo", ""),
            "chunk_gap_seconds": sc.get(
                "plugin.tts_provider.chatterbox.chunk_gap_seconds", "",
            ),
            "remux_bitrate": sc.get("podcast_tts_remux_bitrate", ""),
            "loudnorm_enabled": sc.get_bool("podcast_tts_loudnorm_enabled", True),
            "loudnorm_i": sc.get("podcast_tts_loudnorm_i", ""),
            "loudnorm_tp": sc.get("podcast_tts_loudnorm_tp", ""),
            "loudnorm_lra": sc.get("podcast_tts_loudnorm_lra", ""),
            "loudnorm_ar": sc.get("podcast_tts_loudnorm_ar", ""),
        }
        try:
            result = await ChatterboxTTSProvider().synthesize(
                script, output_path, voice=voice, config=config,
            )
        except Exception as exc:  # noqa: BLE001 — mirrors the Speaches path's
            # never-raise failure contract; generate_episode only branches on
            # EpisodeResult.success, so a raised provider error must convert.
            logger.warning("[PODCAST] Chatterbox TTS failed: %s", exc)
            return EpisodeResult(success=False, error=f"Chatterbox TTS failed: {exc}")

        return EpisodeResult(
            success=True,
            file_path=str(result.audio_path or output_path),
            duration_seconds=result.duration_seconds,
            file_size_bytes=result.file_size_bytes,
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
