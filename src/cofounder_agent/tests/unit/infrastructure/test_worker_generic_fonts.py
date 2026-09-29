"""The worker image's generic font families resolve to Liberation, not JetBrains Mono.

The qa.vision rendered-preview screenshot renders the operator's preview page,
whose body is ``font-family: -apple-system, system-ui, sans-serif``. fontconfig's
default preference lists for those generic names put DejaVu / Noto first and
neither is installed, so once ``fonts-jetbrains-mono`` arrived for VHS (#937) it
won every generic family: ``fc-match sans-serif`` answered JetBrains Mono, and the
whole page rendered in monospace, 21% wider than a phone draws it (13,141 px
tall, against 10,861 px in Roboto, the Android UI font, and 10,918 px in
Liberation Sans). ``fonts-liberation`` was installed the whole time.

Measured 2026-09-28 with Chromium's own ``CSS.getPlatformFontsForNode``. The
alias is INLINE in ``Dockerfile.worker`` on purpose: deploy-checkout-sync
rebuilds this image only when ``Dockerfile.worker`` (or the dependency locks)
changes, so a separate conf file would be a merged edit that never reaches the
container.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "src" / "cofounder_agent" / "Dockerfile.worker").exists()
)
_DOCKERFILE = (_ROOT / "src" / "cofounder_agent" / "Dockerfile.worker").read_text(encoding="utf-8")


def _alias_run() -> str:
    """The RUN instruction that writes the fontconfig alias, continuations joined."""
    m = re.search(r"^RUN printf '%s\\n'.*?(?=\n\n|\Z)", _DOCKERFILE, re.S | re.M)
    assert m, "Dockerfile.worker must write the generic-family fontconfig alias in a RUN printf"
    return m.group(0)


def _aliases() -> dict[str, tuple[str, str]]:
    """{generic family: (preferred family, binding)} from the XML the RUN writes."""
    lines = re.findall(r"^\s+'(\s*<[^']*)'", _alias_run(), re.M)
    assert lines and lines[0].startswith("<?xml"), "the RUN must write a complete fontconfig document"
    root = ET.fromstring("\n".join(line.strip() for line in lines))
    return {
        alias.findtext("family"): (alias.findtext("prefer/family"), alias.get("binding"))
        for alias in root.findall("alias")
    }


def test_liberation_is_installed_for_the_alias_to_point_at():
    assert re.search(r"^\s+fonts-liberation\s*\\?$", _DOCKERFILE, re.M)


def test_the_generic_sans_families_map_to_liberation_sans_and_serif_to_liberation_serif():
    aliases = _aliases()
    assert aliases == {
        "sans-serif": ("Liberation Sans", "same"),
        "sans": ("Liberation Sans", "same"),
        "system-ui": ("Liberation Sans", "same"),
        "serif": ("Liberation Serif", "same"),
    }


def test_monospace_is_left_alone_for_vhs_and_the_explicit_jetbrains_mono_consumers():
    """The brand hero, video thumbnails and demo clips ask for JetBrains Mono by name."""
    assert "monospace" not in _aliases()
    assert "<family>monospace</family>" not in _alias_run()


def test_the_alias_is_written_where_fontconfig_reads_it():
    assert "> /etc/fonts/conf.d/61-poindexter-generic-families.conf" in _alias_run()


def test_the_build_asserts_the_mapping_took_effect():
    """A conf that fontconfig ignores must fail the build, not ship silently."""
    run = _alias_run()
    for family, wants in (
        ("sans-serif", "Liberation Sans"), ("system-ui", "Liberation Sans"),
        ("serif", "Liberation Serif"), ("monospace", "JetBrains Mono"),
    ):
        assert f"fc-match {family} | grep -q '{wants}'" in run


def test_the_alias_comes_after_the_font_packages_it_checks():
    """The build-time check needs JetBrains Mono installed, so the alias RUN must follow it."""
    assert _DOCKERFILE.index("fonts-jetbrains-mono") < _DOCKERFILE.index("61-poindexter-generic-families.conf")
    assert _DOCKERFILE.index("fonts-liberation") < _DOCKERFILE.index("61-poindexter-generic-families.conf")


def test_the_alias_is_inline_not_a_copied_file():
    """deploy-checkout-sync rebuilds the worker images only when Dockerfile.worker (or a
    lock) changes. A COPY'd conf edit would merge and never reach the container."""
    assert not re.search(r"^COPY .*\.conf\b", _DOCKERFILE, re.M)
    assert not (_ROOT / "src" / "cofounder_agent" / "fonts").exists()
