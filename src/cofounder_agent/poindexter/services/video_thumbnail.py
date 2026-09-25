"""Compose a custom YouTube thumbnail: a text-free image, a few words of real type, the brand mark.

Earned 2026-09-25. Every upload so far (13 long videos, 10 Shorts) showed a
frame YouTube picked at random, because nothing ever handed the adapter a
thumbnail. A random frame can be a transition, a presenter mid-blink, or one
of the broken held frames a render can end on.

A thumbnail needs words, and diffusion cannot set type (the OCR gate exists
because it keeps trying), so this renders instead of generating, the same
way ``brand_hero.py`` does. Headless chromium lays out real HTML over a
text-free background and screenshots it. The type is perfect because it IS
type; it costs no GPU and cannot trip the OCR gate.

Three parts, each configurable in ``app_settings`` (``video_thumbnail_*``):

- **Background**: the first source in ``video_thumbnail_background_order``
  that yields an image. ``featured_image`` is the post's featured image
  (on-brand, already OCR-gated), unless it carries its own type by design
  (a composed brand card, a chart, a screenshot: the hook would land on that
  text, so it is passed over). ``presenter_portrait`` is the
  presenter persona's studio portrait (the image the talking head is animated
  from: composed, mouth closed). ``presenter_frame`` is a frame of the
  presenter's opening scene and ``video_frame`` a frame at a fixed time; both
  have the burned-in caption band cropped off. ``brand`` is the brand ground
  with no image. A person is laid out on the right with the text beside it
  (``video_thumbnail_person_layout``); a mid-speech frame under full-bleed type
  was the first test render, and it read as a paused video.
- **Hook**: a few words that ADD to the title rather than repeat it, written
  by the director model from the ``video.thumbnail_hook`` prompt. Code checks
  it. It must read at a glance (``video_thumbnail_hook_max_chars``), carry no
  number the source text does not contain, not be a fragment of the title,
  and not open with the same word as too many recent thumbnails (the first
  13-video backfill opened six with "STOP"). One corrective retry, then no
  text rather than bad text.
- **Look**: size, typeface, colours, scrim, text position and brand mark. The
  layout shrinks the type in the page itself until it fits, because text
  measured anywhere but the rendering chromium is measured wrong (the worker
  ships JetBrains Mono + Liberation only).
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from html import escape
from typing import Any

from poindexter.services.brand_hero import BRAND
from poindexter.services.image_text_scan import (
    KIND_CHART,
    KIND_COMPOSED,
    KIND_SCREENSHOT,
    infer_image_kind_from_url,
)
from poindexter.services.logger_config import get_logger

logger = get_logger(__name__)

BACKGROUND_SOURCES = ("featured_image", "presenter_portrait", "presenter_frame", "video_frame", "brand")
#: Backgrounds that show a person. They default to the right-hand layout
#: (``video_thumbnail_person_layout``): face right, text left, instead of type
#: printed across a face.
_PERSON_SOURCES = ("presenter_portrait", "presenter_frame")
#: Image kinds that carry their own type by design. The hook would be set on
#: top of that type, so such a featured image is not used as a background.
_TEXT_BEARING_KINDS = frozenset({KIND_COMPOSED, KIND_CHART, KIND_SCREENSHOT})
_IMAGE_LAYOUTS = ("cover", "right")
_TEXT_POSITIONS = ("left", "center", "bottom")
_NUMBER_RE = re.compile(r"\d[\d,.]*")
_HOOK_ECHO_STARTS = ("here", "sure", "certainly", "thumbnail", "output", "text")
#: Labels a model puts before its answer ("Thumbnail: …"). Only these are
#: stripped: a short word before a colon is usually the hook's own subject.
_HOOK_LABELS = frozenset({"thumbnail", "thumbnail text", "text", "hook", "headline", "output", "answer"})


def _sc(site_config: Any, key: str, default: Any) -> Any:
    if site_config is None:
        return default
    try:
        value = site_config.get(key, default)
    except Exception:  # noqa: BLE001  # silent-ok: a settings read must not break a render
        return default
    return default if value is None or value == "" else value


def _sc_int(site_config: Any, key: str, default: int) -> int:
    try:
        return int(float(_sc(site_config, key, default)))
    except (TypeError, ValueError):
        return default


def _sc_float(site_config: Any, key: str, default: float) -> float:
    try:
        return float(_sc(site_config, key, default))
    except (TypeError, ValueError):
        return default


def _sc_bool(site_config: Any, key: str, default: bool) -> bool:
    return str(_sc(site_config, key, "true" if default else "false")).strip().lower() in (
        "true", "1", "yes", "on",
    )


@dataclass(frozen=True)
class ThumbnailStyle:
    """Everything that decides how a thumbnail looks. See ``style_from_settings``."""

    width: int = 1280
    height: int = 720
    font_family: str = "JetBrains Mono"
    font_weight: int = 800
    max_font_px: int = 120
    min_font_px: int = 48
    text_color: str = "#f4f8fb"
    accent_color: str = BRAND["cyan"]
    accent_words: int = 1
    uppercase: bool = True
    text_position: str = "left"
    text_width_pct: int = 52
    person_layout: str = "right"
    person_width_pct: int = 58
    scrim_opacity: float = 0.78
    brand_mark: str = ""
    brand_mark_color: str = BRAND["text_muted"]
    background_color: str = BRAND["base"]
    person_focus_y_pct: int = 22
    jpeg_quality: int = 88
    max_bytes: int = 2_000_000


def style_from_settings(site_config: Any) -> ThumbnailStyle:
    """Read the ``video_thumbnail_*`` look settings (defaults = ``ThumbnailStyle``)."""
    d = ThumbnailStyle()
    position = str(_sc(site_config, "video_thumbnail_text_position", d.text_position)).strip().lower()
    layout = str(_sc(site_config, "video_thumbnail_person_layout", d.person_layout)).strip().lower()
    mark = ""
    if _sc_bool(site_config, "video_thumbnail_brand_mark_enabled", True):
        mark = str(
            _sc(site_config, "video_thumbnail_brand_mark", "") or _sc(site_config, "site_name", "")
        ).strip()
    return ThumbnailStyle(
        width=max(320, _sc_int(site_config, "video_thumbnail_width", d.width)),
        height=max(180, _sc_int(site_config, "video_thumbnail_height", d.height)),
        font_family=str(_sc(site_config, "video_thumbnail_font_family", d.font_family)).strip() or d.font_family,
        font_weight=_sc_int(site_config, "video_thumbnail_font_weight", d.font_weight),
        max_font_px=_sc_int(site_config, "video_thumbnail_max_font_px", d.max_font_px),
        min_font_px=_sc_int(site_config, "video_thumbnail_min_font_px", d.min_font_px),
        text_color=str(_sc(site_config, "video_thumbnail_text_color", d.text_color)),
        accent_color=str(_sc(site_config, "video_thumbnail_accent_color", d.accent_color)),
        accent_words=max(0, _sc_int(site_config, "video_thumbnail_accent_words", d.accent_words)),
        uppercase=_sc_bool(site_config, "video_thumbnail_uppercase", d.uppercase),
        text_position=position if position in _TEXT_POSITIONS else d.text_position,
        text_width_pct=min(90, max(25, _sc_int(site_config, "video_thumbnail_text_width_pct", d.text_width_pct))),
        person_layout=layout if layout in _IMAGE_LAYOUTS else d.person_layout,
        person_width_pct=min(80, max(30, _sc_int(site_config, "video_thumbnail_person_width_pct", d.person_width_pct))),
        scrim_opacity=min(1.0, max(0.0, _sc_float(site_config, "video_thumbnail_scrim_opacity", d.scrim_opacity))),
        brand_mark=mark,
        brand_mark_color=str(_sc(site_config, "video_thumbnail_brand_mark_color", d.brand_mark_color)),
        background_color=str(_sc(site_config, "video_thumbnail_background_color", d.background_color)),
        person_focus_y_pct=min(100, max(0, _sc_int(site_config, "video_thumbnail_person_focus_y_pct", d.person_focus_y_pct))),
        jpeg_quality=min(95, max(40, _sc_int(site_config, "video_thumbnail_jpeg_quality", d.jpeg_quality))),
        max_bytes=max(50_000, _sc_int(site_config, "video_thumbnail_max_bytes", d.max_bytes)),
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _data_uri(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext)
    if not mime:
        return None
    try:
        with open(path, "rb") as fh:
            return f"data:image/{mime};base64,{base64.b64encode(fh.read()).decode('ascii')}"
    except OSError:
        return None


def _tint(color: str, pct: float) -> str:
    """``color`` at ``pct`` % opacity, for any CSS colour syntax the operator wrote.

    The scrim, text shadow, glow and grid take their colour from the
    configured ground, accent and text colours, so changing a colour setting changes the whole
    look coherently. ``color-mix`` leaves parsing to chromium, so a named colour
    works as well as a hex one.
    """
    return f"color-mix(in srgb, {color} {max(0.0, min(100.0, pct)):.1f}%, transparent)"


def _hook_markup(hook: str, style: ThumbnailStyle) -> str:
    text = hook.upper() if style.uppercase else hook
    words = text.split()
    if not words:
        return ""
    n = min(style.accent_words, len(words) - 1) if len(words) > 1 else 0
    plain, accent = words[: len(words) - n], words[len(words) - n:]
    html = escape(" ".join(plain))
    if accent:
        html += f' <span class="accent">{escape(" ".join(accent))}</span>'
    return html


def render_thumbnail_html(
    *, hook: str, background_uri: str | None, style: ThumbnailStyle, image_layout: str = "cover",
) -> str:
    """The standalone HTML document for one thumbnail.

    ``image_layout="right"`` puts the image in the right ``person_width_pct``
    of the frame, faded into the brand ground, with the text in the left
    column; ``"cover"`` is full-bleed behind a scrim.

    The fit script shrinks the hook from ``max_font_px`` until it fits its
    box, measured by the chromium that renders it, and stamps the size it
    settled on (or ``overflow``) on ``<body data-fit>`` for callers to read.
    """
    w, h = style.width, style.height
    pad = round(w * 0.055)
    right = image_layout == "right" and bool(background_uri)
    ground, a = style.background_color, style.scrim_opacity * 100
    if right:
        text_w = round(w * (1 - style.person_width_pct / 100) + pad * 0.5)
        box = f"left:{pad}px; top:{pad}px; bottom:{pad}px; width:{text_w - pad}px; justify-content:center;"
        scrim = "transparent"
    elif style.text_position == "center":
        box = f"left:{pad}px; right:{pad}px; top:{pad}px; bottom:{pad}px; justify-content:center; text-align:center;"
        scrim = f"radial-gradient(ellipse at center, {_tint(ground, a)} 0%, {_tint(ground, a * 0.35)} 70%)"
    elif style.text_position == "bottom":
        box = f"left:{pad}px; right:{pad}px; bottom:{pad}px; height:{round(h * 0.42)}px; justify-content:flex-end;"
        scrim = f"linear-gradient(0deg, {_tint(ground, a)} 0%, {_tint(ground, a * 0.6)} 38%, transparent 70%)"
    else:
        box = f"left:{pad}px; top:{pad}px; bottom:{pad}px; width:{round(w * style.text_width_pct / 100)}px; justify-content:center;"
        scrim = f"linear-gradient(90deg, {_tint(ground, a)} 0%, {_tint(ground, a * 0.7)} 45%, transparent 75%)"
    if right:
        bg = (
            '<div class="grid"></div><div class="bloom"></div>'
            f'<img class="bg person" src="{background_uri}">'
        )
    elif background_uri:
        bg = f'<img class="bg" src="{background_uri}">'
    else:
        bg = '<div class="grid"></div><div class="bloom"></div>'
    mark = (
        f'<div class="mark"><span class="slash">//</span> {escape(style.brand_mark)}</div>'
        if style.brand_mark else ""
    )
    family = style.font_family.replace("'", "")
    return f"""<!doctype html><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:{w}px; height:{h}px; }}
body {{ background:{style.background_color}; position:relative; overflow:hidden;
  font-family:'{family}', 'JetBrains Mono', 'Liberation Sans', monospace; }}
.bg {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
.bg.person {{ left:auto; right:0; width:{style.person_width_pct}%; object-position:center {style.person_focus_y_pct}%;
  -webkit-mask-image:linear-gradient(90deg, transparent 0%, #000 26%);
          mask-image:linear-gradient(90deg, transparent 0%, #000 26%); }}
.grid {{ position:absolute; inset:0;
  background-image:linear-gradient({_tint(style.text_color, 8)} 1px, transparent 1px),
    linear-gradient(90deg, {_tint(style.text_color, 8)} 1px, transparent 1px);
  background-size:40px 40px; }}
.bloom {{ position:absolute; left:62%; top:50%; width:{round(w * 0.7)}px; height:{round(h * 0.7)}px;
  transform:translate(-50%,-50%);
  background:radial-gradient(ellipse at center, {_tint(style.accent_color, 16)} 0%, transparent 65%); }}
.scrim {{ position:absolute; inset:0; background:{scrim}; }}
.box {{ position:absolute; {box} display:flex; flex-direction:column; }}
.hook {{ color:{style.text_color}; font-weight:{style.font_weight}; line-height:1.02;
  letter-spacing:0.01em; font-size:{style.max_font_px}px; text-wrap:balance;
  overflow-wrap:normal; word-break:keep-all;
  text-shadow:0 4px 18px {_tint(ground, 55)}; }}
.hook .accent {{ color:{style.accent_color}; }}
.mark {{ position:absolute; left:{pad}px; top:{round(pad * 0.7)}px; color:{style.brand_mark_color};
  font-size:{max(16, round(h * 0.03))}px; letter-spacing:0.14em; text-transform:uppercase;
  font-weight:700; text-shadow:0 2px 8px {_tint(ground, 60)}; }}
.mark .slash {{ color:{style.accent_color}; }}
</style>
{bg}<div class="scrim"></div>{mark}
<div class="box"><div class="hook" id="hook">{_hook_markup(hook, style)}</div></div>
<script>
(function () {{
  var el = document.getElementById('hook'), box = el.parentElement;
  var size = {style.max_font_px}, min = {style.min_font_px};
  function fits() {{
    return el.scrollWidth <= box.clientWidth + 1 && el.scrollHeight <= box.clientHeight + 1;
  }}
  while (size > min && !fits()) {{ size -= 2; el.style.fontSize = size + 'px'; }}
  document.body.setAttribute('data-fit', fits() ? String(size) : 'overflow');
}})();
</script>"""


def jpeg_under(png: bytes, *, quality: int, max_bytes: int) -> bytes:
    """PNG bytes → baseline JPEG no larger than ``max_bytes`` (quality stepped down)."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(png)) as img:
        rgb = img.convert("RGB")
        q = quality
        while True:
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=q, optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes or q <= 40:
                return data
            q -= 6


async def render_thumbnail_jpeg(
    *, hook: str, background_path: str | None, style: ThumbnailStyle, image_layout: str = "cover",
) -> bytes | None:
    """Render one thumbnail to JPEG bytes, or ``None`` if chromium failed."""
    from poindexter.services.preview_screenshot import capture_preview_screenshot

    uri = _data_uri(background_path) if background_path else None
    html = render_thumbnail_html(hook=hook, background_uri=uri, style=style, image_layout=image_layout)
    with tempfile.NamedTemporaryFile(
        suffix=".html", prefix="yt-thumb-", delete=False, mode="w", encoding="utf-8",
    ) as tmp:
        tmp.write(html)
        page = tmp.name
    try:
        png = await capture_preview_screenshot(
            f"file://{page}", viewport_width=style.width, viewport_height=style.height,
            full_page=False, wait_after_load_ms=700, timeout_ms=30000,
        )
    finally:
        try:
            os.remove(page)
        except OSError:  # silent-ok: temp cleanup, the render is done
            pass
    if not png:
        logger.warning("[video_thumbnail] chromium returned no image")
        return None
    return await asyncio.to_thread(
        jpeg_under, png, quality=style.jpeg_quality, max_bytes=style.max_bytes,
    )


# ---------------------------------------------------------------------------
# Hook text
# ---------------------------------------------------------------------------


def _opener(text: str) -> str:
    """The hook's first word, normalised: what a run of thumbnails repeats."""
    words = str(text or "").split()
    return words[0].lower().strip(".,:;!?'\"") if words else ""


def clean_thumbnail_hook(
    raw: str,
    *,
    title: str,
    source_text: str,
    max_chars: int,
    recent_hooks: tuple[str, ...] | list[str] = (),
    max_opener_repeats: int = 0,
) -> tuple[str, str]:
    """Reduce a model reply to thumbnail text. Returns ``(hook, "")`` or ``("", reason)``.

    Rejected: an empty reply or an instruction echo; text longer than
    ``max_chars`` (it would not read at thumbnail size); a hook that is only
    a fragment of the title (the thumbnail sits next to the title and should
    add to it); a number the title and the video's own text do not contain
    (an invented statistic in the most visible spot the video has); a hook
    that opens with the same word as ``max_opener_repeats`` or more of
    ``recent_hooks`` (a channel page of "STOP …" thumbnails reads as a
    template). ``max_opener_repeats=0`` switches that last rule off.
    """
    line = ""
    for candidate in (raw or "").splitlines():
        candidate = candidate.strip()
        if candidate:
            line = candidate
            break
    head, sep, tail = line.partition(":")
    if sep and head.strip().strip("\"'`*").lower() in _HOOK_LABELS:
        # "Thumbnail: …" is a label; "RAG: …" is the subject and stays.
        line = tail
    hook = " ".join(line.strip().strip("\"'`*").split()).rstrip(".,;:").strip()
    if not hook:
        return "", "empty reply"
    if hook.split()[0].lower().strip(",") in _HOOK_ECHO_STARTS:
        return "", f"instruction echo: {hook[:60]!r}"
    if len(hook) > max_chars:
        return "", f"{len(hook)} characters; the limit is {max_chars}"
    title_words = {w.lower().strip(".,:;!?'\"") for w in title.split()}
    hook_words = [w.lower().strip(".,:;!?'\"") for w in hook.split()]
    if hook_words and all(w in title_words for w in hook_words):
        return "", "only repeats words from the title"
    haystack = f"{title} {source_text}".replace(",", "")
    for number in _NUMBER_RE.findall(hook):
        if number.replace(",", "").rstrip(".") not in haystack:
            return "", f"number {number!r} is not in the video's text"
    if max_opener_repeats > 0 and recent_hooks:
        opener = _opener(hook)
        repeats = sum(1 for h in recent_hooks if _opener(h) == opener)
        if opener and repeats >= max_opener_repeats:
            return "", (
                f"it opens with {opener!r}, like {repeats} of the last "
                f"{len(recent_hooks)} thumbnails; open with what this video is about"
            )
    return hook, ""


async def generate_thumbnail_hook(
    *,
    title: str,
    summary: str,
    source_text: str,
    site_config: Any,
    pool: Any,
    recent_hooks: tuple[str, ...] | list[str] = (),
) -> tuple[str, str]:
    """Ask the director model for thumbnail text. Returns ``(hook, note)``.

    One corrective retry (the ``video.thumbnail_hook_fix`` prompt, carrying
    the rejection reason); after that ``("", why)`` and the thumbnail carries
    the brand mark and image alone. Temperature, token budget and timeout are
    the ``video_thumbnail_hook_*`` settings.
    """
    if site_config is None or pool is None:
        return "", "no site_config or pool"
    model = str(
        _sc(site_config, "video_thumbnail_hook_model", "")
        or _sc(site_config, "video_director_model", "")
    ).strip()
    if not model:
        return "", "no video_thumbnail_hook_model / video_director_model set"
    max_chars = max(8, _sc_int(site_config, "video_thumbnail_hook_max_chars", 32))
    temperature = _sc_float(site_config, "video_thumbnail_hook_temperature", 0.7)
    max_tokens = max(16, _sc_int(site_config, "video_thumbnail_hook_max_tokens", 256))
    timeout_s = max(5.0, _sc_float(site_config, "video_thumbnail_hook_timeout_seconds", 90.0))
    max_opener_repeats = max(0, _sc_int(site_config, "video_thumbnail_hook_opener_max_repeats", 2))

    from poindexter.services.llm_providers.dispatcher import dispatch_complete
    from poindexter.services.prompt_manager import get_prompt_manager
    from poindexter.utils.exception_format import describe_exception

    prompt = get_prompt_manager().get_prompt(
        "video.thumbnail_hook", title=title, summary=summary or "(no summary)",
    )
    kwargs: dict[str, Any] = {}
    if _sc_bool(site_config, "video_director_disable_thinking", True):
        kwargs["think"] = False
    messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    reason = ""
    for attempt in range(2):
        try:
            completion = await dispatch_complete(
                pool, messages, model, tier="standard", phase="video_thumbnail_hook",
                temperature=temperature, max_tokens=max_tokens, timeout_s=timeout_s, **kwargs,
            )
            raw = getattr(completion, "text", "") or ""
        except Exception as exc:  # noqa: BLE001 — a hook failure must not cost the render
            logger.warning(
                "[video_thumbnail] hook call to %s failed (%s) — the thumbnail ships "
                "without text", model, describe_exception(exc),
            )
            return "", f"hook call failed: {describe_exception(exc)}"
        hook, reason = clean_thumbnail_hook(
            raw, title=title, source_text=source_text, max_chars=max_chars,
            recent_hooks=recent_hooks, max_opener_repeats=max_opener_repeats,
        )
        if hook:
            return hook, "retry" if attempt else ""
        if attempt:
            break
        try:
            fix = get_prompt_manager().get_prompt(
                "video.thumbnail_hook_fix", reason=reason, max_chars=max_chars,
            )
        except Exception as exc:  # noqa: BLE001 — no retry prompt means no retry, not a failed render
            logger.warning(
                "[video_thumbnail] video.thumbnail_hook_fix prompt unavailable (%s) — "
                "no corrective retry", describe_exception(exc),
            )
            return "", f"rejected ({reason}); no retry prompt"
        messages = [
            *messages,
            {"role": "assistant", "content": raw.strip()[:400]},
            {"role": "user", "content": fix},
        ]
    return "", f"rejected twice ({reason})"


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------


async def _download(url: str, dest_dir: str) -> str | None:
    import httpx

    ext = os.path.splitext(url.split("?", 1)[0])[1].lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        return None
    out = os.path.join(dest_dir, f"thumb-bg{ext}")
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200 or not resp.content:
            logger.warning(
                "[video_thumbnail] background image HTTP %s: %s — trying the next source",
                resp.status_code, url,
            )
            return None
        with open(out, "wb") as fh:
            fh.write(resp.content)
        return out
    except Exception as exc:  # noqa: BLE001 — a missing background falls through to the next source
        from poindexter.utils.exception_format import describe_exception

        logger.warning(
            "[video_thumbnail] background image fetch failed (%s): %s — trying the next source",
            describe_exception(exc), url,
        )
        return None


async def _video_duration(path: str) -> float | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return float(out.decode().strip())
    except Exception:  # noqa: BLE001  # silent-ok: an unknown duration just means no scaled offset
        return None


async def _frame(video_path: str, at_s: float, crop_bottom: float, dest_dir: str, tag: str) -> str | None:
    """One frame of ``video_path`` at ``at_s``, bottom ``crop_bottom`` cropped off.

    The crop removes the burned-in caption band: a thumbnail with a caption
    line across it reads as a paused video, not a cover.
    """
    out = os.path.join(dest_dir, f"thumb-{tag}.png")
    keep = min(1.0, max(0.5, 1.0 - crop_bottom))
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, at_s):.2f}", "-i", video_path,
        "-frames:v", "1", "-vf", f"crop=iw:ih*{keep:.3f}:0:0", out,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception:  # noqa: BLE001  # silent-ok: no frame means the next background source
        return None
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else None


def presenter_window_start(shot_list: dict[str, Any] | None, video_s: float | None) -> float | None:
    """When the first presenter shot starts in the RENDERED video, or ``None``.

    The render stretches the director's plan to the real narration, so the
    planned offset is scaled by rendered/planned length.
    """
    shots = list((shot_list or {}).get("shots") or [])
    planned = sum(float(s.get("duration_s") or 0) for s in shots)
    for s in shots:
        if s.get("source") == "presenter":
            offset = float(s.get("narration_offset_s") or 0.0)
            if video_s and planned > 0:
                offset *= video_s / planned
            return offset
    return None


def _persona_portrait_url(site_config: Any, niche_slug: str | None) -> str:
    try:
        from poindexter.services.persona_service import resolve_persona_for_niche

        persona = resolve_persona_for_niche(site_config, niche_slug)
    except Exception:  # noqa: BLE001  # silent-ok: no persona means the next background source
        return ""
    return str(getattr(persona, "portrait_url", "") or "") if persona else ""


async def resolve_background(
    *,
    order: list[str],
    featured_image_url: str,
    video_path: str,
    shot_list: dict[str, Any] | None,
    site_config: Any,
    dest_dir: str,
    niche_slug: str | None = None,
) -> tuple[str | None, str]:
    """``(image_path_or_None, source_name)`` for the first source that yields an image.

    ``brand`` (or running out of sources) is ``(None, "brand")``: the brand
    ground with no image, which is always available.
    """
    crop = min(0.45, max(0.0, _sc_float(site_config, "video_thumbnail_frame_crop_bottom", 0.22)))
    video_ok = bool(video_path) and os.path.exists(video_path)
    duration: float | None = None
    for source in order:
        if source == "brand":
            return None, "brand"
        if source == "featured_image" and featured_image_url:
            kind = infer_image_kind_from_url(featured_image_url)
            if kind in _TEXT_BEARING_KINDS:
                logger.info(
                    "[video_thumbnail] featured image is a %s image with its own type "
                    "(%s) — trying the next source", kind, featured_image_url,
                )
                continue
            path = await _download(featured_image_url, dest_dir)
            if path:
                return path, source
        elif source == "presenter_portrait":
            url = _persona_portrait_url(site_config, niche_slug)
            path = (
                await _download(url, dest_dir) if url.startswith(("http://", "https://"))
                else (url if url and os.path.exists(url) else None)
            )
            if path:
                return path, source
        elif source == "presenter_frame" and video_ok:
            duration = duration or await _video_duration(video_path)
            start = presenter_window_start(shot_list, duration)
            if start is not None:
                offset = _sc_float(site_config, "video_thumbnail_presenter_offset_s", 1.5)
                path = await _frame(video_path, start + offset, crop, dest_dir, "presenter")
                if path:
                    return path, source
        elif source == "video_frame" and video_ok:
            at = _sc_float(site_config, "video_thumbnail_frame_at_s", 5.0)
            path = await _frame(video_path, at, crop, dest_dir, "frame")
            if path:
                return path, source
    return None, "brand"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThumbnailResult:
    path: str
    hook: str
    hook_note: str
    background: str
    size_bytes: int


async def load_post_context(pool: Any, task_id: str) -> dict[str, str]:
    """Title, summary and featured image for a task's post (``""`` when unknown)."""
    ctx = {"title": "", "summary": "", "featured_image_url": "", "slug": ""}
    if pool is None:
        return ctx
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT title, excerpt, featured_image_url, slug FROM posts
                 WHERE metadata->>'pipeline_task_id' = $1
                 ORDER BY created_at DESC LIMIT 1
                """,
                str(task_id),
            )
            if row is None:
                topic = await conn.fetchval(
                    "SELECT topic FROM pipeline_tasks WHERE task_id::text = $1", str(task_id),
                )
                ctx["title"] = str(topic or "")
                return ctx
    except Exception as exc:  # noqa: BLE001 — context is best-effort; the render decides what it can do
        logger.warning("[video_thumbnail] post context lookup failed for %s: %s", task_id, exc)
        return ctx
    ctx.update(
        title=str(row["title"] or ""), summary=str(row["excerpt"] or ""),
        featured_image_url=str(row["featured_image_url"] or ""), slug=str(row["slug"] or ""),
    )
    return ctx


async def load_recent_hooks(pool: Any, task_id: str, window: int) -> list[str]:
    """Hook text of the ``window`` most recent OTHER thumbnails, newest first.

    Feeds the opener-variety rule. In a backfill each thumbnail stored becomes
    "recent" for the next, so a batch diversifies itself.
    """
    if pool is None or window <= 0:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT metadata->>'hook' AS hook FROM media_assets
                 WHERE type = 'video_thumbnail' AND task_id::text <> $1
                   AND COALESCE(metadata->>'hook', '') <> ''
                 ORDER BY created_at DESC LIMIT $2
                """,
                str(task_id), int(window),
            )
    except Exception as exc:  # noqa: BLE001 — variety is a refinement; the hook still gets its other checks
        logger.warning(
            "[video_thumbnail] recent hook lookup failed for %s (%s) — no opener "
            "variety check this time", task_id, exc,
        )
        return []
    return [str(r["hook"]) for r in rows]


async def compose_video_thumbnail(
    *,
    task_id: str,
    pool: Any,
    site_config: Any,
    video_path: str = "",
    shot_list: dict[str, Any] | None = None,
    source_text: str = "",
    out_path: str | None = None,
    niche_slug: str | None = None,
) -> ThumbnailResult | None:
    """Compose the long video's thumbnail and write it as a JPEG.

    ``None`` when ``video_thumbnail_enabled`` is off or chromium fails. A
    missing hook or background degrades (brand ground, no text) rather than
    failing: a plain branded thumbnail still beats a random frame.
    """
    if not _sc_bool(site_config, "video_thumbnail_enabled", True):
        return None
    ctx = await load_post_context(pool, task_id)
    style = style_from_settings(site_config)

    hook, note = "", "video_thumbnail_hook_enabled is off"
    if _sc_bool(site_config, "video_thumbnail_hook_enabled", True):
        recent = await load_recent_hooks(
            pool, task_id, max(0, _sc_int(site_config, "video_thumbnail_hook_opener_window", 12)),
        )
        hook, note = await generate_thumbnail_hook(
            title=ctx["title"], summary=ctx["summary"],
            source_text=f"{ctx['summary']} {source_text}",
            site_config=site_config, pool=pool, recent_hooks=recent,
        )
        if not hook:
            logger.info("[video_thumbnail] task %s: no hook text (%s)", task_id, note)

    order = [
        s.strip() for s in str(
            _sc(site_config, "video_thumbnail_background_order", "featured_image,presenter_portrait,brand")
        ).split(",") if s.strip() in BACKGROUND_SOURCES
    ] or ["brand"]
    with tempfile.TemporaryDirectory(prefix="yt-thumb-") as work:
        background, source = await resolve_background(
            order=order, featured_image_url=ctx["featured_image_url"], video_path=video_path,
            shot_list=shot_list, site_config=site_config, dest_dir=work, niche_slug=niche_slug,
        )
        layout = style.person_layout if source in _PERSON_SOURCES else "cover"
        data = await render_thumbnail_jpeg(
            hook=hook, background_path=background, style=style, image_layout=layout,
        )
    if not data:
        return None
    path = out_path or os.path.join(tempfile.gettempdir(), f"media_{task_id}_thumbnail.jpg")
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o644)
    logger.info(
        "[video_thumbnail] task %s: %s background, hook %r (%d bytes)",
        task_id, source, hook, len(data),
    )
    return ThumbnailResult(path=path, hook=hook, hook_note=note, background=source, size_bytes=len(data))


# ---------------------------------------------------------------------------
# Durable storage
# ---------------------------------------------------------------------------

#: ``media_assets.type`` of a composed thumbnail.
THUMBNAIL_ASSET_TYPE = "video_thumbnail"


async def store_thumbnail_asset(
    pool: Any,
    *,
    task_id: str,
    src_path: str,
    meta: dict[str, Any] | None,
    post_id: str | None,
    video_recorded_now: bool,
    video_dir: Any = None,
) -> str | None:
    """Make the composed thumbnail durable as a ``video_thumbnail`` asset.

    The thumbnail FOLLOWS its video. A run that persisted the long video
    replaces any older thumbnail (a re-render gets a fresh one). A replay
    whose video was skipped by the idempotency guard keeps an existing
    thumbnail, so the local file can never drift from one already uploaded.
    Best-effort like the rest of this node.
    """
    from poindexter.services.media_asset_recorder import record_media_asset

    if video_dir is None:
        from poindexter.services.video_service import VIDEO_DIR as video_dir
    src = src_path
    if not src or not os.path.exists(src):
        return None
    try:
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT count(*) FROM media_assets WHERE task_id::text = $1 AND type = $2",
                task_id, THUMBNAIL_ASSET_TYPE,
            )
    except Exception as exc:  # noqa: BLE001 — a lookup failure must not block the node
        logger.warning("[video_thumbnail] thumbnail lookup failed for %s: %s", task_id, exc)
        return None
    if existing and not video_recorded_now:
        logger.info(
            "[video_thumbnail] task %s already has a thumbnail and its video was not "
            "re-persisted — keeping the existing one", task_id,
        )
        return None
    durable = video_dir / f"{task_id}_thumbnail.jpg"
    try:
        video_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(src, durable)
        os.chmod(durable, 0o644)
    except OSError as exc:
        logger.warning("[video_thumbnail] thumbnail move failed (%s): %s", src, exc)
        return None
    width = height = None
    try:
        from PIL import Image

        with Image.open(durable) as img:
            width, height = img.size
    except Exception:  # noqa: BLE001  # silent-ok: dimensions are informational
        pass
    if existing:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM media_assets WHERE task_id::text = $1 AND type = $2",
                    task_id, THUMBNAIL_ASSET_TYPE,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[video_thumbnail] stale thumbnail row cleanup failed for %s: %s", task_id, exc)
    asset_id = await record_media_asset(
        pool=pool,
        post_id=post_id,
        task_id=task_id,
        asset_type=THUMBNAIL_ASSET_TYPE,
        storage_path=str(durable),
        storage_provider="local",
        source="pipeline",
        provider_plugin="compositor.chromium_thumbnail",
        mime_type="image/jpeg",
        width=width,
        height=height,
        file_size_bytes=os.path.getsize(durable),
        metadata=dict(meta) if meta else None,
    )
    if asset_id:
        logger.info("[video_thumbnail] recorded video_thumbnail %s for task %s", asset_id, task_id)
    return asset_id


__all__ = [
    "BACKGROUND_SOURCES",
    "THUMBNAIL_ASSET_TYPE",
    "ThumbnailResult",
    "ThumbnailStyle",
    "clean_thumbnail_hook",
    "compose_video_thumbnail",
    "generate_thumbnail_hook",
    "jpeg_under",
    "load_post_context",
    "presenter_window_start",
    "render_thumbnail_html",
    "render_thumbnail_jpeg",
    "resolve_background",
    "store_thumbnail_asset",
    "style_from_settings",
]
