"""What an image costs the vision judge, in context tokens.

The QA vision judge (``qa_vision_model`` / ``qa_preview_vision_model``,
``ollama/qwen3-vl:30b-a3b-instruct`` on the GPU-pinned :11435 instance) runs at
one fixed context, ``pinned_llm_endpoint_num_ctx`` (16384). Every image in a
request spends part of it, so how many images a call may carry is a budget, not
a preference.

The cost is decided by the server, not by the caller. Ollama 0.32.1 serves this
model through llama.cpp's ``llama-server`` with ``--mmproj --image-min-tokens
1024``, and llama.cpp resizes each image before encoding it (the runner logs
``image_min_pixels: 1048576 (custom value)`` and ``image_max_pixels: 4194304``
at model load):

- The image is rounded to a multiple of 32 px on each side (patch 16 x merge 2).
- Below 1,048,576 px (1024 tokens) it is scaled UP to that floor. A thumbnail
  costs as much as a megapixel image.
- Above 4,194,304 px (4096 tokens) it is scaled DOWN to that ceiling, keeping
  its aspect ratio. This is what turned a 1280x13141 full-page screenshot into a
  ~640x6560 image at half scale, so 16 px body text reached the model as 8 px.
- Each 32x32 block is one token.

``estimate_image_tokens`` reproduces that rule so the tiler can spend the
context it has. The constants belong to the judge deployed today: if Ollama or
its bundled llama.cpp changes ``--image-min-tokens`` or the pixel ceiling,
recalibrate them against a real ``prompt_tokens`` (``docs/architecture/
preview-links.md``, "Calibrating the estimate").
"""

from __future__ import annotations

import math

# Qwen3-VL: 16 px patches merged 2x2, so one token covers a 32x32 px block.
IMAGE_ALIGN_PX = 32
# llama-server load log on ollama-vision.service (2026-09-28): --image-min-tokens 1024.
IMAGE_MIN_TOKENS = 1024
# The loader's own ceiling for this model family (4096 tokens = 4,194,304 px).
IMAGE_MAX_TOKENS = 4096
# Chat-template tokens around each image (vision start/end markers).
IMAGE_MARKER_TOKENS = 2

_BLOCK_PX = IMAGE_ALIGN_PX * IMAGE_ALIGN_PX


def _resized_side(value: float) -> int:
    # Round HALF UP, like llama.cpp's std::round. Python's round() goes to the
    # nearest EVEN integer, so round(62.5) is 62 where the judge computes 63:
    # a 2000 px side is 62.5 blocks, and the estimate for a 2000x655 image was 20
    # tokens short of what the judge reported (1262).
    return max(IMAGE_ALIGN_PX, math.floor(value / IMAGE_ALIGN_PX + 0.5) * IMAGE_ALIGN_PX)


def resized_dimensions(
    width: int,
    height: int,
    *,
    min_tokens: int = IMAGE_MIN_TOKENS,
    max_tokens: int = IMAGE_MAX_TOKENS,
) -> tuple[int, int]:
    """The (width, height) the judge's image encoder actually receives.

    Mirrors llama.cpp's Qwen2/3-VL ``calc_size_preserved_ratio``: round each side
    to the 32 px grid, then pull the result inside ``[min_tokens, max_tokens]``
    blocks, preserving the aspect ratio.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"image dimensions must be positive, got {width}x{height}")
    min_pixels = min_tokens * _BLOCK_PX
    max_pixels = max_tokens * _BLOCK_PX
    w_bar = _resized_side(width)
    h_bar = _resized_side(height)
    if w_bar * h_bar > max_pixels:
        beta = math.sqrt((width * height) / max_pixels)
        w_bar = max(IMAGE_ALIGN_PX, math.floor(width / beta / IMAGE_ALIGN_PX) * IMAGE_ALIGN_PX)
        h_bar = max(IMAGE_ALIGN_PX, math.floor(height / beta / IMAGE_ALIGN_PX) * IMAGE_ALIGN_PX)
    elif w_bar * h_bar < min_pixels:
        beta = math.sqrt(min_pixels / (width * height))
        w_bar = math.ceil(width * beta / IMAGE_ALIGN_PX) * IMAGE_ALIGN_PX
        h_bar = math.ceil(height * beta / IMAGE_ALIGN_PX) * IMAGE_ALIGN_PX
    return w_bar, h_bar


def estimate_image_tokens(
    width: int,
    height: int,
    *,
    min_tokens: int = IMAGE_MIN_TOKENS,
    max_tokens: int = IMAGE_MAX_TOKENS,
) -> int:
    """Context tokens one ``width`` x ``height`` image costs the judge."""
    w_bar, h_bar = resized_dimensions(
        width, height, min_tokens=min_tokens, max_tokens=max_tokens,
    )
    return (w_bar // IMAGE_ALIGN_PX) * (h_bar // IMAGE_ALIGN_PX) + IMAGE_MARKER_TOKENS


def tiles_that_fit(
    context_tokens: int,
    reserve_tokens: int,
    tile_width: int,
    tile_height: int,
) -> int:
    """How many ``tile_width`` x ``tile_height`` images fit in ``context_tokens``.

    ``reserve_tokens`` is what the request needs besides its images: the prompt
    text and the answer. Never below one, so a tiny context degrades to a single
    image instead of no verdict.
    """
    per_tile = estimate_image_tokens(tile_width, tile_height)
    return max(1, (context_tokens - reserve_tokens) // per_tile)


__all__ = [
    "IMAGE_ALIGN_PX",
    "IMAGE_MAX_TOKENS",
    "IMAGE_MIN_TOKENS",
    "estimate_image_tokens",
    "resized_dimensions",
    "tiles_that_fit",
]
