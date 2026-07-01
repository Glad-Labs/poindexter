"""Guard: OSS seed files ship generic defaults, free of operator identity.

Two leak classes this guards — both shipped to the public ``poindexter`` mirror
before the 2026-06-30 scrub:

1. **Operator-private model tags.** A fresh ``git clone`` + ``poindexter setup``
   can only ``ollama pull`` models on the public registry. Glad Labs prod runs
   custom local fine-tunes — ``glm-4.7-5090``, ``gemma-4-31B-it-qat``,
   ``gemma-4-E2B`` — that no other operator can pull. If one leaks into a seeded
   ``*_model`` default, the first pipeline run on a fresh install dies
   dispatching to a model Ollama can't find (boot-time validation only *warns*).

2. **Operator identity + brand.** The operator's name (``Matt``), the Glad Labs
   brand as a seeded default value, and personal accounts/hardware are not
   generic OSS defaults — they belong in the private ``services.operator_overrides``
   overlay (stripped from the mirror), which re-applies them on the operator rig.

Matt's real values live in the overlay; the seeds ship empty / generic. New
custom tags go in ``_OPERATOR_PRIVATE_MODEL_TAGS``.
"""

from __future__ import annotations

import re
from pathlib import Path

from services import settings_defaults

# Operator-private / custom model tags that are NOT on the public Ollama
# registry, so a fresh install cannot pull them.
_OPERATOR_PRIVATE_MODEL_TAGS = ("glm-4.7-5090", "gemma-4-31B-it-qat", "gemma-4-E2B")

# Operator personal identity that must never appear ANYWHERE in a shipping seed
# file — value, description, or comment. Brand-in-prose is handled separately.
_OPERATOR_IDENTITY_RE = re.compile(
    r"\bmatt\b|gladding|" + "|".join(re.escape(t) for t in _OPERATOR_PRIVATE_MODEL_TAGS),
    re.IGNORECASE,
)

# The operator brand, forbidden as a seeded VALUE default (each OSS install is
# its own brand). Bare brand mentions in prose/comments stay policy-acceptable,
# so this is matched against the value field only.
_BRAND_VALUE_RE = re.compile(r"glad\s*labs", re.IGNORECASE)

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


def test_baseline_seeds_have_no_operator_identity() -> None:
    """No ``Matt`` / ``Gladding`` / private model tag anywhere in the shipping
    seed file — value, description, or comment."""
    text = _BASELINE_SEEDS.read_text(encoding="utf-8")
    hits = sorted({m.lower() for m in _OPERATOR_IDENTITY_RE.findall(text)})
    assert not hits, (
        "0000_baseline.seeds.sql leaks operator identity to the public mirror: "
        f"{hits}. Move the real value into services.operator_overrides and seed a "
        "generic default (see test docstring)."
    )


def test_baseline_seeds_have_no_brand_value_defaults() -> None:
    """The Glad Labs brand must not be a seeded VALUE default. Bare mentions in
    prose/comments are policy-acceptable, so only the value field is checked."""
    text = _BASELINE_SEEDS.read_text(encoding="utf-8")
    offenders = {
        key: value
        for key, value in _SEED_KEY_VALUE_RE.findall(text)
        if _BRAND_VALUE_RE.search(value)
    }
    assert not offenders, (
        "0000_baseline.seeds.sql seeds the operator brand as a default value: "
        f"{offenders}. Seed '' / a generic default and put the brand in "
        "services.operator_overrides."
    )
