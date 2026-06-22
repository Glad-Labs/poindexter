"""Unit tests for the vision-model image normalization helper.

Root cause of the cold ``vision_gate`` (Glad-Labs/poindexter#563): the vision
model (``qwen3-vl:30b`` via Ollama) cannot decode WebP, and the SDXL-generated
inline images are stored as WebP on R2 (``r2_upload_service`` converts
PNG/JPEG -> WebP before upload). So ``_check_image_relevance`` downloaded a
WebP, the model received no decodable image, and the rail returned ``None`` --
which, once ``vision_gate`` is ``required_to_pass``, fails the whole post closed.

``_normalize_image_for_vision`` converts WebP (and any other non-JPEG/PNG
format) to JPEG in-memory so the model can actually see the image. JPEG and
PNG -- both confirmed decodable by the model -- pass through untouched (no
re-encode, no quality loss). Undecodable bytes are returned unchanged
(best-effort: the rail still degrades gracefully).
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from modules.content.multi_model_qa import (
    _normalize_image_for_vision,
    extract_inline_image_urls,
)


def _img_bytes(fmt: str, *, mode: str = "RGB", color=(123, 50, 200)) -> bytes:
    im = Image.new(mode, (8, 8), color)
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue()


@pytest.mark.unit
class TestNormalizeImageForVision:
    def test_webp_is_converted_to_jpeg(self):
        """The model can't decode WebP, so it must come out as JPEG."""
        out = _normalize_image_for_vision(_img_bytes("WEBP"))
        assert Image.open(io.BytesIO(out)).format == "JPEG"

    def test_png_passes_through_unchanged(self):
        """PNG is decodable by the model -> no re-encode (bytes identical)."""
        png = _img_bytes("PNG")
        assert _normalize_image_for_vision(png) == png

    def test_jpeg_passes_through_unchanged(self):
        """JPEG is decodable by the model -> no re-encode (bytes identical)."""
        jpg = _img_bytes("JPEG")
        assert _normalize_image_for_vision(jpg) == jpg

    def test_rgba_webp_is_flattened_to_jpeg(self):
        """A WebP with alpha must still convert (JPEG has no alpha channel)."""
        out = _normalize_image_for_vision(_img_bytes("WEBP", mode="RGBA", color=(10, 20, 30, 128)))
        assert Image.open(io.BytesIO(out)).format == "JPEG"

    def test_undecodable_bytes_returned_unchanged(self):
        """Best-effort: garbage in -> same bytes out, never raises."""
        junk = b"this is not an image"
        assert _normalize_image_for_vision(junk) == junk


@pytest.mark.unit
class TestExtractInlineImageUrls:
    """Shared inline-image-URL extraction. Both _check_image_relevance (which
    assesses the images) and qa.vision (which decides 'were there images to
    assess?' for the case-C/case-D split) must agree on what counts as an
    inline image, so the regex lives in one place."""

    def test_extracts_html_img_src(self):
        c = 'Body.\n<img src="https://r2.dev/a.webp" alt="x" width="1024" />\nmore'
        assert extract_inline_image_urls(c) == ["https://r2.dev/a.webp"]

    def test_extracts_markdown_image(self):
        c = "intro ![alt text](https://images.pexels.com/p/1.jpeg) outro"
        assert extract_inline_image_urls(c) == ["https://images.pexels.com/p/1.jpeg"]

    def test_dedups_repeated_urls(self):
        c = '<img src="https://r2.dev/a.webp"/>\n<img src="https://r2.dev/a.webp"/>'
        assert extract_inline_image_urls(c) == ["https://r2.dev/a.webp"]

    def test_ignores_relative_and_data_urls(self):
        c = '<img src="/media/local.png"/> and ![x](data:image/png;base64,zzz)'
        assert extract_inline_image_urls(c) == []

    def test_no_images_returns_empty(self):
        assert extract_inline_image_urls("just prose, no images at all") == []
