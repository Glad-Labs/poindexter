"""The vision Ollama instance pins two defaults: a context size (2026-09-25)
and a prompt-cache RAM cap (2026-09-26).

The instance holds one model at one context size and reloads it (10-40 s on
the 3090) whenever a request asks for another. Poindexter runs every call routed
there at ``pinned_llm_endpoint_num_ctx``; a caller that sends no num_ctx got
Ollama's VRAM-derived default (32768 on a 24 GB card), which is how
glad-labs-products/sanctuary's 10-minute calls reloaded the shared judge 12
times in an hour. Pinning the instance default to the same value makes an
omitted num_ctx harmless — but only while the two agree, so the script's
default is checked against the setting's declared default, not a literal.

Separately, llama-server keeps a host-RAM prompt cache of finished requests'
KV state and ollama runs it with no ``--cache-ram`` of its own, so the
upstream 8192 MiB default applied — a vision-heavy mix plateaued at 10.5-10.7
GiB (poindexter#3434) and the brain's ``ollama_runner_ram_watch`` had to
recycle the runner to get the memory back. ``LLAMA_ARG_CACHE_RAM`` caps it at
the source instead, at ``pinned_llm_endpoint_cache_ram_mib``'s declared
default — checked the same way and for the same reason: the two must agree,
or a code change to one silently stops matching the other.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from poindexter.services.settings_defaults import DEFAULTS

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
    assert int(m.group(1)) == int(DEFAULTS["pinned_llm_endpoint_num_ctx"]), (
        "the instance default must equal pinned_llm_endpoint_num_ctx, the size "
        "every Poindexter call to the pinned endpoint runs at — otherwise a "
        "caller that omits num_ctx loads the judge at a size the next rail call "
        "reloads away from"
    )


def test_the_pin_is_set_before_ollama_starts():
    body = _script()
    assert body.index("OLLAMA_CONTEXT_LENGTH") < body.index("exec ollama serve")


def test_the_vision_instance_caps_its_prompt_cache_at_the_source():
    m = re.search(r'export LLAMA_ARG_CACHE_RAM="\$\{LLAMA_ARG_CACHE_RAM:-(\d+)\}"', _script())
    assert m, "ollama-vision.sh must default LLAMA_ARG_CACHE_RAM (env-overridable)"
    assert int(m.group(1)) == int(DEFAULTS["pinned_llm_endpoint_cache_ram_mib"]), (
        "the instance's cache cap must equal pinned_llm_endpoint_cache_ram_mib "
        "— that setting, and the brain's ollama_runner_ram_recycle_targets "
        "watermark, both reason about the plateau this literal actually "
        "produces, so a drifted script default makes both wrong"
    )


def test_the_cache_cap_is_set_before_ollama_starts():
    body = _script()
    assert body.index("LLAMA_ARG_CACHE_RAM") < body.index("exec ollama serve")
