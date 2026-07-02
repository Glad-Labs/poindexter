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
# so this is matched against the value field only. Every spelling is banned —
# space ("Glad Labs"), hyphen ("glad-labs-website" compose project /
# "glad-labs" GlitchTip org, which slipped past the space-only regex until
# 2026-07), and underscore ("glad_labs_claim", the pre-rename validator-rule
# name) — EXCEPT a value that is exactly a ``Glad-Labs/<repo>`` GitHub
# owner/name ref, the one legitimate carrier (gh_repo / branch_drift_repo /
# pr_staleness_repo).
_BRAND_VALUE_RE = re.compile(r"glad[\s_-]*labs", re.IGNORECASE)
_GH_REPO_REF_RE = re.compile(r"^Glad-Labs/[A-Za-z0-9._-]+$")


def _is_brand_value(value: str) -> bool:
    return bool(_BRAND_VALUE_RE.search(value)) and not _GH_REPO_REF_RE.match(value)

_BASELINE_SEEDS = (
    Path(settings_defaults.__file__).resolve().parent
    / "migrations"
    / "0000_baseline.seeds.sql"
)

# Capture (key, value) for every app_settings seed row:
#   VALUES ('key', 'value', 'category', ...)
_SEED_KEY_VALUE_RE = re.compile(r"VALUES\s*\(\s*'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'")

# Capture the identity fields of every niches seed row:
#   VALUES ('id', 'slug', 'name', active, '{tags}'::text[], 'writer_prompt', ...)
_NICHES_SEED_RE = re.compile(
    r"INSERT INTO niches \([^)]*\) VALUES \('(?P<id>[^']*)', '(?P<slug>[^']*)', "
    r"'(?P<name>[^']*)', (?P<active>\w+), '(?P<tags>[^']*)'::text\[\], "
    r"'(?P<prompt>(?:[^']|'')*)',",
    re.DOTALL,
)

# Niche slug/name/prompt fields reuse _BRAND_VALUE_RE without the gh-repo
# exemption — a niche field has no legitimate reason to carry an owner/name ref.


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
    """The Glad Labs brand must not be a seeded VALUE default (any spelling —
    space, hyphen, or underscore — except a bare ``Glad-Labs/<repo>`` ref).
    Bare mentions in prose/comments are policy-acceptable, so only the value
    field is checked."""
    text = _BASELINE_SEEDS.read_text(encoding="utf-8")
    offenders = {
        key: value
        for key, value in _SEED_KEY_VALUE_RE.findall(text)
        if _is_brand_value(value.replace("''", "'"))
    }
    assert not offenders, (
        "0000_baseline.seeds.sql seeds the operator brand as a default value: "
        f"{offenders}. Seed '' / a generic default and put the brand in "
        "services.operator_overrides."
    )


def test_settings_defaults_have_no_brand_value_defaults() -> None:
    """Same brand-value ban over ``settings_defaults.DEFAULTS`` — the seeder's
    other public surface. Keys drift between the two files (only overlaid keys
    are lockstep-checked), so a branded default could ship here alone."""
    offenders = {
        key: value
        for key, value in settings_defaults.DEFAULTS.items()
        if _is_brand_value(value)
    }
    assert not offenders, (
        "settings_defaults.DEFAULTS ships the operator brand as a default "
        f"value: {offenders}. Use a generic default and put the brand in "
        "services.operator_overrides."
    )


def test_niches_seed_rows_carry_no_operator_brand() -> None:
    """The seeded niches are the product's example content — slug, name, and
    writer prompt must ship brand-free. The operator's branded niches
    (``glad-labs`` slug, Glad Labs prompt text) restore on the operator rig via
    ``operator_overrides.OPERATOR_NICHE_OVERRIDES``."""
    text = _BASELINE_SEEDS.read_text(encoding="utf-8")
    rows = list(_NICHES_SEED_RE.finditer(text))
    assert rows, (
        "expected INSERT INTO niches rows in 0000_baseline.seeds.sql — if the "
        "seed shape changed, update _NICHES_SEED_RE to match it"
    )
    offenders: dict[str, list[str]] = {}
    for m in rows:
        hits = [
            f"{field}: …{m[field][max(0, hit.start() - 30):hit.end() + 30]!r}…"
            for field in ("slug", "name", "prompt")
            for hit in _BRAND_VALUE_RE.finditer(m[field])
        ]
        if hits:
            offenders[m["slug"]] = hits
    assert not offenders, (
        "0000_baseline.seeds.sql seeds operator-branded niches to the public "
        f"mirror: {offenders}. Ship a generic niche and restore the branded one "
        "via operator_overrides.OPERATOR_NICHE_OVERRIDES."
    )
