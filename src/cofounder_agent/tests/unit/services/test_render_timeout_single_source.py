"""``image_render_timeout_seconds`` must have exactly one value in the tree.

2026-07-31 reconciliation. The key had accumulated four values, each a fossil of
whatever the declared default was on the day a call site was written:

- ``90``  at 4 sites  — the #1566 default (2026-06-13)
- ``240`` at 3 sites  — the #1727 default (2026-06-19, cold-load fix)
- ``300`` declared    — raised by #2386 (2026-07-13) when the OCR gate gained
                        up to ``image_ocr_gate_max_attempts`` renders per call
- ``240`` live on prod — written the day 240 became the default, then frozen by
                        ``ON CONFLICT DO NOTHING``

The repeated literal is the defect: a fallback written next to a call site has
no way to learn that the default moved. Every read site now takes its fallback
from ``settings_defaults.default_int``, so the number exists once.

This test guards the shape (no literal creeps back), not a specific number —
changing the default in ``settings_defaults`` should not break it.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from services.settings_defaults import DEFAULTS, default_int

pytestmark = pytest.mark.unit

KEY = "image_render_timeout_seconds"
_SRC = pathlib.Path(__file__).resolve().parents[3]


def _production_sources() -> list[pathlib.Path]:
    return [p for p in _SRC.rglob("*.py") if "/tests/" not in str(p)]


def test_default_int_reads_the_declared_default() -> None:
    assert default_int(KEY) == int(DEFAULTS[KEY])


def test_default_int_fails_loudly_on_an_undeclared_key() -> None:
    """A rename that loses its default must not silently yield 0."""
    with pytest.raises(KeyError, match="no entry in settings_defaults"):
        default_int("image_render_timeout_seconds_typo")


def test_no_call_site_repeats_a_literal_fallback() -> None:
    """The regression guard: a hardcoded fallback next to a read site.

    Matches ``get_int("<key>", <number>)`` — the exact shape that fossilised
    four times. Passing ``default_int(...)`` or a variable is fine.
    """
    literal_fallback = re.compile(
        rf'get_int\(\s*["\']{re.escape(KEY)}["\']\s*,\s*\d+\s*\)'
    )
    offenders = [
        f"{p.relative_to(_SRC)}:{p.read_text(errors='ignore')[:m.start()].count(chr(10)) + 1}"
        for p in _production_sources()
        for m in literal_fallback.finditer(p.read_text(errors="ignore"))
    ]
    assert not offenders, (
        f"{KEY} fallback hardcoded at {offenders} — use "
        f"default_int({KEY!r}) so it can't fossilise when the default moves"
    )


def test_every_read_site_resolves_to_the_same_number() -> None:
    """End-to-end: the value each module would fall back to is identical."""
    from modules.content.atoms._image_helpers import _default_render_timeout
    from modules.content.stages.source_featured_image import (
        _render_timeout_seconds,
    )

    expected = int(DEFAULTS[KEY])
    assert _default_render_timeout() == expected
    assert _render_timeout_seconds(None) == expected


def test_featured_stage_budget_tracks_the_declared_default() -> None:
    """The node timeout floor must move with the render timeout, not lag it.

    This is the coupling that failed in the original incident — the stage cap
    was a constant while the render budget was settings-driven.
    """
    from modules.content.stages.source_featured_image import (
        resolve_stage_timeout_seconds,
    )

    class _Cfg:
        def __init__(self, render: int) -> None:
            self._render = render

        def get_int(self, key: str, default: int = 0) -> int:
            return self._render if key == KEY else default

        def get_float(self, key: str, default: float = 0.0) -> float:
            return default

    assert resolve_stage_timeout_seconds(_Cfg(600)) > resolve_stage_timeout_seconds(
        _Cfg(300)
    )
