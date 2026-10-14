"""Model sheets: each cartoon character drawn from the front, three-quarter and side.

A studio gives every animator a model sheet, the character turned around on
one page, so the character stays on-model from any angle. We drew every
picture from one front-facing portrait, and side views and wide shots drifted
off-model (Matt, 2026-10-14: recreate a studio's process step by step).

:func:`draw_turnaround` redraws the portrait as a turnaround sheet with the
image-edit model (``story_turnaround_template``, ``story_turnaround_width`` x
``story_turnaround_height``), uploads it and stores it on the persona
(``turnaround_url``). :func:`ensure_turnarounds` does that for every cast
member who has a portrait and no sheet, so the existing cast gets one the
first time a story uses them.

The renderer never hands the image model the whole sheet: given three views
of Cinder, it drew two Cinders walking side by side (live test, 2026-10-14).
It cuts out one panel (:func:`crop_sheet_panel`; ``story_turnaround_panels``
across, panel ``video_reference_sheet_panel``, 0 = the front view the
template draws first) and draws stills and wide shots from that full-body
view (``shot_list_renderer._reference_portraits``). Talking close-ups keep
the portrait, which faces the viewer. Best-effort: a sheet that cannot be
drawn or cut leaves the portrait in use.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _setting(site_config: Any, key: str) -> str:
    from poindexter.services.settings_defaults import DEFAULTS

    value = site_config.get(key, None) if site_config is not None else None
    return str(DEFAULTS[key] if value is None or str(value).strip() == "" else value)


def turnaround_prompt(name: str, appearance: str, site_config: Any) -> str:
    return (
        _setting(site_config, "story_turnaround_template")
        .replace("{name}", name).replace("{appearance}", appearance or name)
    ).strip()


def crop_sheet_panel(src: str, dest: str, *, panel: int, panels: int) -> str | None:
    """Cut panel ``panel`` of ``panels`` side by side out of the sheet at ``src``.

    The cut is trimmed to the character (anything unlike the sheet's
    background, which the corners show) with a margin, so the reference is
    the figure and not a strip of empty page. Returns ``dest``, or ``None``
    when the sheet cannot be read.
    """
    from PIL import Image, ImageChops

    panels = max(1, panels)
    panel = min(max(0, panel), panels - 1)
    try:
        with Image.open(src) as img:
            sheet = img.convert("RGB")
    except Exception as exc:  # noqa: BLE001 — an unreadable sheet falls back to the portrait
        logger.warning("[model_sheets] sheet %s unreadable: %s", src, exc)
        return None
    w, h = sheet.size
    left, right = w * panel // panels, w * (panel + 1) // panels
    cut = sheet.crop((left, 0, right, h))
    page = Image.new("RGB", cut.size, sheet.getpixel((0, 0)))
    diff = ImageChops.difference(cut, page).convert("L").point(lambda v: 255 if v > 24 else 0)
    box = diff.getbbox()
    if box:
        margin = max(8, min(cut.size) // 20)
        cut = cut.crop((max(0, box[0] - margin), max(0, box[1] - margin),
                        min(cut.width, box[2] + margin), min(cut.height, box[3] + margin)))
    cut.save(dest)
    return dest


def sheet_panel_settings(site_config: Any) -> tuple[int, int]:
    """(panel, panels): which view of the sheet the renderer draws from."""
    return (int(float(_setting(site_config, "video_reference_sheet_panel"))),
            int(float(_setting(site_config, "story_turnaround_panels"))))


async def draw_turnaround(persona: Any, *, site_config: Any, pool: Any = None) -> str | None:
    """Draw, upload and store ``persona``'s model sheet; returns its URL or ``None``."""
    from poindexter.services import reference_frames as rf
    from poindexter.services.persona_service import upsert_persona
    from poindexter.services.r2_upload_service import R2UploadService
    from poindexter.services.settings_service import SettingsService
    from poindexter.services.video_renderers.shot_list_renderer import _fetch_presenter_portrait

    if not getattr(persona, "portrait_url", ""):
        return None
    work = Path(tempfile.mkdtemp(prefix="turnaround-"))
    portrait = await _fetch_presenter_portrait(persona.portrait_url, work / "portrait.png", None)
    if not portrait:
        logger.warning("[model_sheets] %s: portrait %s could not be fetched", persona.slug, persona.portrait_url)
        return None
    name = persona.display_name or persona.slug
    out = str(work / f"{persona.slug}-turnaround.png")
    ok, why = await rf.render_reference_frame(
        prompt=turnaround_prompt(name, persona.appearance, site_config),
        reference_paths=[portrait], output_path=out,
        width=int(float(_setting(site_config, "story_turnaround_width"))),
        height=int(float(_setting(site_config, "story_turnaround_height"))),
        site_config=site_config,
    )
    if not ok:
        logger.warning("[model_sheets] %s: turnaround draw failed: %s", persona.slug, why)
        return None
    url = await R2UploadService(site_config=site_config).upload_to_r2(
        out, f"personas/{persona.slug}-turnaround.png", "image/png",
    )
    if not url:
        logger.warning("[model_sheets] %s: turnaround upload failed", persona.slug)
        return None
    if pool is not None:
        await upsert_persona(SettingsService(pool), persona.slug, {"turnaround_url": url})
    logger.info("[model_sheets] %s: turnaround drawn -> %s", persona.slug, url)
    return url


async def ensure_turnarounds(slugs: list[str], *, site_config: Any, pool: Any) -> dict[str, str]:
    """A model sheet for every one of ``slugs`` with a portrait and none yet; returns slug -> URL."""
    from poindexter.services.persona_service import get_persona

    if _setting(site_config, "story_turnarounds_enabled").strip().lower() not in ("1", "true", "yes", "on"):
        return {}
    made: dict[str, str] = {}
    for slug in dict.fromkeys(slugs):
        persona = get_persona(site_config, slug)
        if persona is None or not persona.portrait_url or persona.turnaround_url:
            continue
        try:
            url = await draw_turnaround(persona, site_config=site_config, pool=pool)
        except Exception as exc:  # noqa: BLE001 — a sheet is a refinement; the portrait still works
            logger.warning("[model_sheets] %s: no turnaround: %s", slug, exc)
            continue
        if url:
            made[slug] = url
    if made and site_config is not None and hasattr(site_config, "reload") and pool is not None:
        try:
            await site_config.reload(pool)
        except Exception as exc:  # noqa: BLE001 — the next settings refresh picks them up
            logger.warning("[model_sheets] settings reload after turnarounds failed: %s", exc)
    return made


__all__ = ["crop_sheet_panel", "draw_turnaround", "ensure_turnarounds", "sheet_panel_settings", "turnaround_prompt"]
