"""Tests for the Glad Labs operator overlay.

``apply_operator_overrides`` re-pins the operator's custom local models,
personal settings, AND branded niches over the public OSS defaults on a fresh
install / settings reset, but only when a row still holds the OSS default (so
live ``poindexter settings set`` tuning and hand-edited niche prompts survive
a reboot). On OSS installs the private ``services.operator_overrides`` module
is absent and the overlay is a no-op.

Settings restore via a conditional UPSERT on ``app_settings``; niches restore
via a conditional UPDATE on ``niches`` guarded by the OSS-default
``writer_prompt_override`` text (``OPERATOR_NICHE_OVERRIDES``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import settings_defaults as _sd
from services.settings_defaults import (
    DEFAULTS,
    NICHE_OVERRIDE_COLUMNS,
    apply_operator_overrides,
)

_BASELINE_SEEDS = (
    Path(_sd.__file__).resolve().parent / "migrations" / "0000_baseline.seeds.sql"
)
_SEED_KV_RE = re.compile(r"VALUES\s*\(\s*'((?:[^']|'')*)'\s*,\s*'((?:[^']|'')*)'")

# Identity fields of every niches seed row:
#   VALUES ('id', 'slug', 'name', active, '{tags}'::text[], 'writer_prompt', ...)
_NICHES_SEED_RE = re.compile(
    r"INSERT INTO niches \([^)]*\) VALUES \('(?P<id>[^']*)', '(?P<slug>[^']*)', "
    r"'(?P<name>[^']*)', (?P<active>\w+), '(?P<tags>[^']*)'::text\[\], "
    r"'(?P<prompt>(?:[^']|'')*)',",
    re.DOTALL,
)


def _baseline_seed_map() -> dict[str, str]:
    """key -> value for every app_settings seed row (SQL '' unescaped to ')."""
    text = _BASELINE_SEEDS.read_text(encoding="utf-8")
    return {
        k.replace("''", "'"): v.replace("''", "'")
        for k, v in _SEED_KV_RE.findall(text)
    }


def _baseline_niche_map() -> dict[str, dict[str, str]]:
    """slug -> {name, writer_prompt_override} for every seeded niche row."""
    text = _BASELINE_SEEDS.read_text(encoding="utf-8")
    return {
        m["slug"]: {
            "name": m["name"],
            "writer_prompt_override": m["prompt"].replace("''", "'"),
        }
        for m in _NICHES_SEED_RE.finditer(text)
    }


def _mock_pool(fetchval_side_effect):
    conn = AsyncMock()
    conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def _all_overrides(oo):
    return {**oo.OPERATOR_MODEL_PINS, **oo.OPERATOR_SETTING_OVERRIDES}


@pytest.mark.asyncio
async def test_apply_is_noop_when_overlay_absent(monkeypatch):
    """OSS install: importing the private overlay fails -> no DB writes."""
    monkeypatch.setitem(sys.modules, "services.operator_overrides", None)
    pool, conn = _mock_pool(fetchval_side_effect=lambda *a, **k: None)

    applied = await apply_operator_overrides(pool)

    assert applied == 0
    conn.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_is_noop_for_none_pool():
    assert await apply_operator_overrides(None) == 0


@pytest.mark.asyncio
async def test_apply_compares_against_oss_default_and_counts_changes():
    """Overlay present: each setting issues a conditional UPSERT keyed on the
    OSS default, each niche a conditional UPDATE, and only rows that actually
    changed are counted."""
    oo = pytest.importorskip("services.operator_overrides")
    overrides = _all_overrides(oo)
    niche_entries = oo.OPERATOR_NICHE_OVERRIDES

    calls = []

    def _fetchval(query, *args):
        calls.append((query, args))
        # Pretend every other row was already operator-tuned (conditional
        # WHERE missed -> RETURNING yields nothing -> None).
        return "row" if len(calls) % 2 else None

    pool, _conn = _mock_pool(fetchval_side_effect=_fetchval)
    applied = await apply_operator_overrides(pool)

    setting_calls = [
        (q, a) for q, a in calls if "INSERT INTO app_settings" in q
    ]
    niche_calls = [(q, a) for q, a in calls if q.lstrip().startswith("UPDATE niches")]

    assert len(setting_calls) == len(overrides)
    assert len(niche_calls) == len(niche_entries)
    assert len(calls) == len(setting_calls) + len(niche_calls)
    for _query, (key, value, _desc, oss_default) in setting_calls:
        # The overlay only overwrites the public OSS default for that key...
        assert oss_default == DEFAULTS.get(key)
        # ...with the operator's private value.
        assert value == overrides[key]
    assert applied == sum(1 for i in range(1, len(calls) + 1) if i % 2)


@pytest.mark.asyncio
async def test_niche_overrides_issue_conditional_updates():
    """Each niche override UPDATEs only the row whose slug matches AND whose
    writer_prompt_override still equals the OSS-seeded default — a hand-tuned
    prompt (or an already-restored row) never gets clobbered."""
    oo = pytest.importorskip("services.operator_overrides")
    entries = oo.OPERATOR_NICHE_OVERRIDES
    assert entries, "operator overlay should carry branded niche overrides"

    calls = []

    def _fetchval(query, *args):
        calls.append((query, args))
        return "some-id"

    pool, _conn = _mock_pool(fetchval_side_effect=_fetchval)
    applied = await apply_operator_overrides(pool)

    niche_calls = [(q, a) for q, a in calls if q.lstrip().startswith("UPDATE niches")]
    assert len(niche_calls) == len(entries)
    for (query, args), entry in zip(niche_calls, entries, strict=True):
        assert "WHERE slug =" in query
        assert "AND writer_prompt_override =" in query
        assert "RETURNING id" in query
        # Args: the SET values (entry ordering) then the two guard params.
        assert list(args[:-2]) == [entry["set"][c] for c in entry["set"]]
        assert args[-2] == entry["match_slug"]
        assert args[-1] == entry["expect_writer_prompt_override"]
    # Every conditional matched in this scenario, so every entry counts.
    assert applied == len(calls)


def test_niche_override_set_uses_allowlisted_columns_only():
    """The public machinery interpolates SET column names into SQL, so entries
    may only use the allowlisted columns."""
    oo = pytest.importorskip("services.operator_overrides")

    for entry in oo.OPERATOR_NICHE_OVERRIDES:
        unknown = set(entry["set"]) - set(NICHE_OVERRIDE_COLUMNS)
        assert not unknown, (
            f"niche override for {entry['match_slug']!r} sets non-allowlisted "
            f"columns {unknown} — extend NICHE_OVERRIDE_COLUMNS deliberately "
            "or drop them."
        )


def test_niche_override_expect_matches_baseline_seed():
    """The conditional UPDATE fires only when the row still carries the
    OSS-seeded prompt, so each entry's expect text must equal what
    ``0000_baseline.seeds.sql`` actually seeds for that slug — otherwise the
    restore silently no-ops on a fresh operator install."""
    oo = pytest.importorskip("services.operator_overrides")
    seeds = _baseline_niche_map()

    for entry in oo.OPERATOR_NICHE_OVERRIDES:
        slug = entry["match_slug"]
        assert slug in seeds, (
            f"niche override matches slug {slug!r} but the baseline never seeds "
            f"it (seeded slugs: {sorted(seeds)}) — the restore would never fire."
        )
        assert entry["expect_writer_prompt_override"] == seeds[slug]["writer_prompt_override"], (
            f"expect_writer_prompt_override for {slug!r} drifted from the "
            "baseline-seeded prompt — the conditional UPDATE will never match "
            "on a fresh operator install. Re-sync the overlay entry with "
            "0000_baseline.seeds.sql."
        )


def test_niche_override_set_differs_from_expect():
    """A niche override that sets the same prompt it expects is dead weight
    (and a sign the genericised seed leaked back to the branded text)."""
    oo = pytest.importorskip("services.operator_overrides")

    for entry in oo.OPERATOR_NICHE_OVERRIDES:
        new_prompt = entry["set"].get("writer_prompt_override")
        if new_prompt is not None:
            assert new_prompt != entry["expect_writer_prompt_override"], (
                f"niche override for {entry['match_slug']!r} sets the exact "
                "prompt it expects — drop the entry or fix the seed drift."
            )


def test_every_override_key_has_a_public_oss_default():
    """The overlay skips a key whose OSS default it can't see, so every override
    key must exist in DEFAULTS (else the operator silently loses that value)."""
    oo = pytest.importorskip("services.operator_overrides")

    missing = [k for k in _all_overrides(oo) if k not in DEFAULTS]
    assert not missing, (
        f"operator overlay keys {missing} have no OSS default in "
        "settings_defaults.DEFAULTS — apply_operator_overrides would skip them, "
        "so a fresh operator install would NOT get those values."
    )


def test_overrides_differ_from_oss_default():
    """An overlay entry only makes sense when it differs from the public OSS
    default — an equal value is dead weight (and a sign the seed drifted)."""
    oo = pytest.importorskip("services.operator_overrides")

    redundant = {k: v for k, v in _all_overrides(oo).items() if v == DEFAULTS.get(k)}
    assert not redundant, (
        f"operator overlay entries {redundant} equal the public OSS default — "
        "drop them from operator_overrides or fix the seed drift."
    )


def test_overlaid_keys_seed_matches_defaults():
    """The overlay overwrites a row only WHERE it still equals ``DEFAULTS[key]``,
    so the value ``0000_baseline.seeds.sql`` actually seeds must equal
    ``DEFAULTS[key]`` — otherwise the overlay silently no-ops on a fresh operator
    install and the operator loses that value. Guards hand-transcribed seeds like
    the multi-line voice prompt against DEFAULTS/baseline drift."""
    oo = pytest.importorskip("services.operator_overrides")
    seeds = _baseline_seed_map()
    drift = {
        key: {"baseline": seeds[key], "defaults": DEFAULTS.get(key)}
        for key in _all_overrides(oo)
        if key in seeds and seeds[key] != DEFAULTS.get(key)
    }
    assert not drift, (
        "baseline.seeds.sql and settings_defaults.DEFAULTS disagree on the OSS "
        f"default for overlaid keys, so the overlay won't re-apply on reset: {drift}"
    )
