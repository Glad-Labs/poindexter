"""Contract test: embeddings_collapse policies must not be TTL-starved.

Regression guard for the dead-policy race first fixed on prod in
Glad-Labs/poindexter#2228 and synced into the baseline seed on 2026-07-10.

An ``embeddings_collapse`` retention policy clusters rows older than its
``config.age_days`` into summary rows and deletes the raw originals. The
sibling ``embeddings.ttl_prune.<source>`` policy hard-deletes non-summary rows
older than ``ttl_days``. If a collapse policy's ``age_days`` is >= its source's
``ttl_days``, ttl_prune destroys the raw feedstock before collapse can ever
cluster it — the collapse policy is structurally dead. Both collapse rows ship
``enabled=false`` (opt-in), so the bug stays latent until an operator enables
one, which is exactly when it bites and is hardest to notice.

This pins the invariant for every seeded (collapse, ttl_prune) source pair:
``collapse.age_days < ttl_prune.ttl_days``. Summary rows are exempt from
ttl_prune (its ``filter_sql`` carries ``COALESCE(is_summary, FALSE) = FALSE``),
so the collapse *output* survives regardless — only the raw feedstock is on the
clock, which is why the strict inequality is what matters.

Operators can still tune the live rows (``ON CONFLICT DO NOTHING`` preserves
runtime overrides); this only pins the shipped fresh-install default.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def baseline_seeds_text() -> str:
    seeds_path = (
        Path(__file__).resolve().parents[4]
        / "services"
        / "migrations"
        / "0000_baseline.seeds.sql"
    )
    return seeds_path.read_text(encoding="utf-8")


def _ttl_prune_days(seeds_text: str) -> dict[str, int]:
    """Map ``{source_table: ttl_days}`` for every embeddings.ttl_prune.* seed.

    ``ttl_days`` is the bare integer following the ``'created_at'`` age_column
    on each INSERT line; the ttl_prune ``filter_sql`` never contains the token
    ``created_at``, so the first match on the line is unambiguous.
    """
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(
            r"embeddings\.ttl_prune\.(\w+)'.*?'created_at',\s*(\d+),",
            seeds_text,
        )
    }


def _collapse_age_days(seeds_text: str) -> dict[str, int]:
    """Map ``{source_table: age_days}`` for every embeddings.collapse.* seed."""
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(
            r"embeddings\.collapse\.(\w+)'.*?\"age_days\":\s*(\d+)",
            seeds_text,
        )
    }


def test_seed_parser_finds_all_three_sources(baseline_seeds_text: str) -> None:
    """Guard the parser itself: if the seed row shape drifts and these regexes
    silently match nothing, the invariant test below would pass vacuously."""
    ttl = _ttl_prune_days(baseline_seeds_text)
    collapse = _collapse_age_days(baseline_seeds_text)
    expected = {"audit", "brain", "claude_sessions"}
    assert expected <= set(ttl), (
        f"ttl_prune parser found {sorted(ttl)} — seed row shape may have drifted"
    )
    assert expected <= set(collapse), (
        f"collapse parser found {sorted(collapse)} — seed row shape may have drifted"
    )


def test_collapse_age_strictly_below_sibling_ttl(baseline_seeds_text: str) -> None:
    """Every collapse policy must compress raw rows before ttl_prune deletes them."""
    ttl = _ttl_prune_days(baseline_seeds_text)
    collapse = _collapse_age_days(baseline_seeds_text)
    offenders = {
        src: {"collapse_age_days": age, "ttl_prune_ttl_days": ttl[src]}
        for src, age in collapse.items()
        if src in ttl and age >= ttl[src]
    }
    assert not offenders, (
        "embeddings_collapse policy is TTL-starved (age_days >= sibling "
        f"ttl_prune ttl_days) — collapse would be structurally dead: {offenders}. "
        "Lower the collapse age_days or raise the ttl_prune ttl_days so raw rows "
        "survive long enough to be clustered."
    )


def test_claude_sessions_windows_pinned(baseline_seeds_text: str) -> None:
    """Pin the 2026-07-10 decision (match prod): collapse at 90d, ttl at 180d."""
    assert _collapse_age_days(baseline_seeds_text).get("claude_sessions") == 90
    assert _ttl_prune_days(baseline_seeds_text).get("claude_sessions") == 180
