"""The vision Ollama instance pins a default context size (2026-09-25).

The instance holds one model at one context size and reloads it (10-40 s on
the 3090) whenever a request asks for another. Poindexter's callers send 16384;
a caller that sends no num_ctx got Ollama's VRAM-derived default (32768 on a
24 GB card), which is how glad-labs-products/sanctuary's 10-minute calls
reloaded the shared judge 12 times in an hour. Pinning the instance default to
the fleet value makes an omitted num_ctx harmless.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _script() -> str:
    root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "linux" / "ollama-vision.sh").exists()
    )
    return (root / "scripts" / "linux" / "ollama-vision.sh").read_text(encoding="utf-8")


def test_the_vision_instance_pins_a_default_context():
    m = re.search(r'export OLLAMA_CONTEXT_LENGTH="\$\{OLLAMA_CONTEXT_LENGTH:-(\d+)\}"', _script())
    assert m, "ollama-vision.sh must default OLLAMA_CONTEXT_LENGTH (env-overridable)"
    assert int(m.group(1)) == 16384, "keep it equal to the num_ctx every judge caller sends"


def test_the_pin_is_set_before_ollama_starts():
    body = _script()
    assert body.index("OLLAMA_CONTEXT_LENGTH") < body.index("exec ollama serve")
