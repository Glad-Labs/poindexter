"""Sanctioned import seam for the content module's image helpers.

The image machinery (Image Decision Agent planning, image-gen + Pexels
two-strategy generation, HTML injection, R2 upload) grew up inside
``modules/content/stages/replace_inline_images.py`` and is still implemented
there. Four atoms plus the image-rebuild path used to reach straight into that
stage module for private ``_underscore`` helpers — an atom→stage-internal
tangle flagged by the 2026-07-10 tech-debt audit.

This module is the fix at the CONTRACT level: it re-exports those helpers
under public names, and **every consumer outside the stage file imports from
here**. The stage-resident implementation is a rented detail behind this seam
(per ``feedback_everything_behind_a_seam_is_disposable``) — the physical move
of the ~700-line closure into this module can happen later without touching a
single caller.

Import from here, never from ``modules.content.stages.replace_inline_images``:

    from modules.content.image_helpers import try_image_gen, try_pexels
"""
from __future__ import annotations

from modules.content.stages.replace_inline_images import (
    _cleanup_leaked_descriptions as cleanup_leaked_descriptions,
)
from modules.content.stages.replace_inline_images import (
    _inject_html_image as inject_html_image,
)
from modules.content.stages.replace_inline_images import (
    _normalize_from_router as normalize_from_router,
)
from modules.content.stages.replace_inline_images import (
    _plan_and_inject_placeholders as plan_and_inject_placeholders,
)
from modules.content.stages.replace_inline_images import (
    _record_inline_image_asset as record_inline_image_asset,
)
from modules.content.stages.replace_inline_images import (
    _try_image_gen as try_image_gen,
)
from modules.content.stages.replace_inline_images import (
    _try_pexels as try_pexels,
)

__all__ = [
    "cleanup_leaked_descriptions",
    "inject_html_image",
    "normalize_from_router",
    "plan_and_inject_placeholders",
    "record_inline_image_asset",
    "try_image_gen",
    "try_pexels",
]
