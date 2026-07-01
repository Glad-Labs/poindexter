"""Guard: OSS seed defaults must pin only publicly-pullable Ollama models.

A fresh ``git clone`` + ``poindexter setup`` install can only ``ollama pull``
models that exist on the public registry. Glad Labs production runs custom local
fine-tunes — ``glm-4.7-5090`` (a 5090-specific build) and ``gemma-4-31B-it-qat``
(a local quant) — that no other operator can pull. If either leaks into a seeded
``*_model`` default (the ``settings_defaults.DEFAULTS`` dict or the squashed
``0000_baseline.seeds.sql``), the first pipeline run on a fresh install dies
dispatching to a model Ollama can't find — the worst possible onboarding moment,
and one boot-time validation only *warns* about (it never hard-fails).

New custom tags Matt runs in prod go in ``_OPERATOR_PRIVATE_MODEL_TAGS`` so they
can never silently become OSS defaults.
"""

from __future__ import annotations

import re
from pathlib import Path

from services import settings_defaults

# Operator-private / custom model tags that are NOT on the public Ollama
# registry, so a fresh install cannot pull them.
_OPERATOR_PRIVATE_MODEL_TAGS = ("glm-4.7-5090", "gemma-4-31B-it-qat")

_BASELINE_SEEDS = (
    Path(settings_defaults.__file__).resolve().parent
    / "migrations"
    / "0000_baseline.seeds.sql"
)

# Capture (key, value) for every app_settings seed row:
#   VALUES ('key', 'value', 'category', ...)
_SEED_KEY_VALUE_RE = re.compile(r"VALUES\s*\(\s*'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'")


def _has_private_tag(value: str) -> bool:
    return any(tag in value for tag in _OPERATOR_PRIVATE_MODEL_TAGS)


def test_settings_defaults_pin_only_public_models() -> None:
    offenders = {
        key: value
        for key, value in settings_defaults.DEFAULTS.items()
        if key.endswith("_model") and _has_private_tag(value)
    }
    assert not offenders, (
        "settings_defaults.DEFAULTS pins operator-private models a fresh "
        f"`ollama pull` cannot fetch: {offenders}"
    )


def test_baseline_seeds_pin_only_public_models() -> None:
    text = _BASELINE_SEEDS.read_text(encoding="utf-8")
    offenders = {
        key: value
        for key, value in _SEED_KEY_VALUE_RE.findall(text)
        if key.endswith("_model") and _has_private_tag(value)
    }
    assert not offenders, (
        "0000_baseline.seeds.sql pins operator-private models a fresh "
        f"`ollama pull` cannot fetch: {offenders}"
    )
