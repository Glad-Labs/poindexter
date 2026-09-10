"""Compose an on-brand hero image from the brand tokens — no diffusion.

A hero for a post about Poindexter wants the brand mark, and a mark means
type. Diffusion cannot set type: asked for a branded hero it produced
"Poindexter Philosophy" in mangled letterforms over two people with six-fingered
hands — breaking the no-text/no-people rules in
``skills/content/blog-generation/SKILL.md`` in one shot. The OCR gate exists
precisely because that keeps happening.

So this renders instead of generating. Headless chromium (the same one
``services/preview_screenshot.py`` drives for the vision QA rail) lays out real
HTML using the real brand tokens, and screenshots it. Type is perfect because
it *is* type. Output is deterministic, costs no GPU, needs no VRAM, and can't
trip the OCR gate — the text is intentional.

Operator preference this follows: prefer calculated over generated.

Typeface: JetBrains Mono, the brand's ``--gl-font-mono``. Space Grotesk
(``--gl-font-display``) is not installed in the worker image, and mono is the
better fit anyway — it is the face the operator console itself uses, so the
hero looks like the product rather than like stock art.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from html import escape

from services.logger_config import get_logger

logger = get_logger(__name__)

# Mirrored from packages/brand/src/tokens/colors.css. That file is a JS-package
# asset outside src/cofounder_agent, so it is NOT on the worker container's
# filesystem (only src/cofounder_agent is mounted at /app) and cannot be read at
# runtime. Drift is caught instead by
# tests/unit/services/test_brand_hero.py::test_palette_matches_brand_tokens,
# which parses the real CSS whenever the repo is present (host + CI).
BRAND: dict[str, str] = {
    "base": "#070a0f",
    "surface_2": "#182030",
    "text": "#d8e0e8",
    "text_muted": "#7a8a92",
    "text_dim": "#4e5a62",
    "cyan": "#00e5ff",
    "cyan_dim": "#00b3cc",
    "amber": "#ffb74d",
}

HERO_WIDTH = 1200
HERO_HEIGHT = 630

#: Pipeline stages drawn under the wordmark. ``state`` drives the node style:
#: ``done`` = filled cyan, ``gate`` = filled amber + larger, ``pending`` =
#: hollow. Cyan/amber rather than green/red keeps it legible for red-green
#: colourblind readers, matching the brand tokens' own stated rule.
DEFAULT_STAGES: tuple[tuple[str, str], ...] = (
    ("Research", "done"),
    ("Draft", "done"),
    ("Review", "done"),
    ("Gate", "gate"),
    ("Publish", "pending"),
)


@dataclass(frozen=True)
class HeroSpec:
    """Everything that varies between heroes."""

    title: str = "Poindexter"
    #: Leading characters of ``title`` rendered in cyan (the accent split).
    accent_chars: int = 4
    tagline: str = "An open-source AI content pipeline. Every draft clears a gate."
    #: Substring of ``tagline`` to pick out in amber. Empty = no highlight.
    tagline_accent: str = "gate"
    eyebrow_org: str = "Glad Labs"
    eyebrow_mark: str = "GL"
    stages: tuple[tuple[str, str], ...] = DEFAULT_STAGES
    footer_left: str = "github.com/Glad-Labs/poindexter"
    footer_right: str = "gladlabs.io"


def _accent_title(title: str, accent_chars: int) -> str:
    """Split the wordmark into a cyan head and a plain tail."""
    n = max(0, min(int(accent_chars), len(title)))
    head, tail = escape(title[:n]), escape(title[n:])
    return f'<span class="gl">{head}</span>{tail}' if head else tail


def _accent_tagline(tagline: str, accent: str) -> str:
    """Wrap the first occurrence of ``accent`` in the amber span."""
    safe = escape(tagline)
    if not accent:
        return safe
    safe_accent = escape(accent)
    if safe_accent not in safe:
        return safe
    return safe.replace(safe_accent, f'<span class="em">{safe_accent}</span>', 1)


def _stage_html(stages: tuple[tuple[str, str], ...]) -> str:
    """Render stage columns joined by edges.

    Each stage is its own column with the node in a fixed-height slot, so a
    larger gate node cannot push its label off the shared baseline.
    """
    parts: list[str] = []
    for i, (label, state) in enumerate(stages):
        if i:
            # Edges after the gate are dim — nothing has flowed past it yet.
            dim = " dim" if any(s == "gate" for _, s in stages[:i]) else ""
            parts.append(f'<div class="edge{dim}"></div>')
        node_cls = {"done": "node on", "gate": "node gate"}.get(state, "node")
        label_cls = "label lit" if state == "gate" else "label"
        parts.append(
            f'<div class="stage"><div class="slot"><div class="{node_cls}"></div></div>'
            f'<div class="{label_cls}">{escape(label)}</div></div>'
        )
    return "".join(parts)


def render_hero_html(spec: HeroSpec | None = None) -> str:
    """Return the standalone HTML document for a hero."""
    s = spec or HeroSpec()
    b = BRAND
    return f"""<!doctype html><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:{HERO_WIDTH}px; height:{HERO_HEIGHT}px; }}
body {{ background:{b["base"]}; color:{b["text"]};
  font-family:'JetBrains Mono', ui-monospace, monospace;
  position:relative; overflow:hidden; }}
.grid {{ position:absolute; inset:0;
  background-image:
    linear-gradient(rgba(255,255,255,0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.08) 1px, transparent 1px);
  background-size:40px 40px;
  -webkit-mask-image:radial-gradient(ellipse 75% 70% at 50% 50%, #000 40%, transparent 100%);
          mask-image:radial-gradient(ellipse 75% 70% at 50% 50%, #000 40%, transparent 100%); }}
.bloom {{ position:absolute; left:50%; top:54%; width:900px; height:420px;
  transform:translate(-50%,-50%);
  background:radial-gradient(ellipse at center, rgba(0,229,255,0.13) 0%, transparent 65%); }}
.frame {{ position:absolute; inset:0; padding:44px 56px; display:flex; flex-direction:column; }}
.top {{ display:flex; align-items:baseline; gap:14px; font-size:15px; letter-spacing:0.14em; }}
.slash {{ color:{b["cyan_dim"]}; }}
.org {{ color:{b["text_muted"]}; text-transform:uppercase; }}
.dot {{ color:{b["text_dim"]}; }}
.mark {{ color:{b["cyan"]}; font-weight:700; letter-spacing:0.05em; text-transform:uppercase; }}
.middle {{ flex:1; display:flex; flex-direction:column; justify-content:center; }}
.wordmark {{ font-size:76px; font-weight:700; letter-spacing:0.06em;
  text-transform:uppercase; line-height:1; color:{b["text"]}; }}
.wordmark .gl {{ color:{b["cyan"]}; text-shadow:0 0 26px rgba(0,229,255,0.35); }}
.tagline {{ margin-top:20px; font-size:19px; color:{b["text_muted"]}; letter-spacing:0.03em; }}
.tagline .em {{ color:{b["amber"]}; }}
.pipe {{ margin-top:48px; display:flex; align-items:flex-start; }}
.stage {{ width:132px; flex:none; display:flex; flex-direction:column; align-items:center; }}
.slot {{ height:22px; display:flex; align-items:center; justify-content:center; }}
.node {{ width:15px; height:15px; border-radius:3px; background:{b["surface_2"]};
  border:1.5px solid {b["cyan_dim"]}; transform:rotate(45deg); }}
.node.on {{ background:{b["cyan"]}; border-color:{b["cyan"]};
  box-shadow:0 0 16px rgba(0,229,255,0.35); }}
.node.gate {{ background:{b["amber"]}; border-color:{b["amber"]};
  box-shadow:0 0 18px rgba(255,183,77,0.45); width:19px; height:19px; }}
.label {{ margin-top:16px; font-size:12.5px; letter-spacing:0.11em;
  text-transform:uppercase; color:{b["text_dim"]}; white-space:nowrap; }}
.label.lit {{ color:{b["amber"]}; }}
.edge {{ flex:1; height:1.5px; margin-top:10px; background:{b["cyan_dim"]}; opacity:0.5; }}
.edge.dim {{ background:rgba(255,255,255,0.08); opacity:1; }}
.rule {{ position:absolute; left:56px; right:56px; bottom:92px; height:1px;
  background:rgba(255,255,255,0.08); }}
.bottom {{ display:flex; justify-content:space-between; align-items:flex-end;
  font-size:13.5px; letter-spacing:0.08em; }}
.bottom .left {{ color:{b["text_dim"]}; }}
.bottom .right {{ color:{b["text_muted"]}; }}
</style>
<div class="grid"></div><div class="bloom"></div>
<div class="frame">
  <div class="top"><span class="slash">//</span>
    <span class="org">{escape(s.eyebrow_org)}</span>
    <span class="dot">·</span>
    <span class="mark">{escape(s.eyebrow_mark)}</span></div>
  <div class="middle">
    <div class="wordmark">{_accent_title(s.title, s.accent_chars)}</div>
    <div class="tagline">{_accent_tagline(s.tagline, s.tagline_accent)}</div>
    <div class="pipe">{_stage_html(s.stages)}</div>
  </div>
  <div class="rule"></div>
  <div class="bottom"><span class="left">{escape(s.footer_left)}</span>
    <span class="right">{escape(s.footer_right)}</span></div>
</div>"""


async def render_hero_png(spec: HeroSpec | None = None) -> bytes | None:
    """Render a hero to PNG bytes, or ``None`` if the capture failed.

    Returns None (never raises) on a chromium failure, matching
    ``capture_preview_screenshot``'s contract so callers treat it as "no image"
    exactly like a failed generation.
    """
    from services.preview_screenshot import capture_preview_screenshot

    html = render_hero_html(spec)
    with tempfile.NamedTemporaryFile(
        suffix=".html", prefix="brand-hero-", delete=False, mode="w", encoding="utf-8",
    ) as tmp:
        tmp.write(html)
        path = tmp.name
    try:
        png = await capture_preview_screenshot(
            f"file://{path}",
            viewport_width=HERO_WIDTH,
            viewport_height=HERO_HEIGHT,
            full_page=False,
            wait_after_load_ms=800,
            timeout_ms=30000,
        )
    finally:
        try:
            os.remove(path)
        except OSError:  # silent-ok: temp cleanup, render already done
            pass
    if not png:
        logger.warning("[brand_hero] chromium returned no image")
    return png


__all__ = [
    "BRAND",
    "DEFAULT_STAGES",
    "HERO_HEIGHT",
    "HERO_WIDTH",
    "HeroSpec",
    "render_hero_html",
    "render_hero_png",
]
