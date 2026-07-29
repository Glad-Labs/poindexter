"""Contract tests for the CLI's colourblind-safe status palette.

The defect these guard against (Glad-Labs/poindexter#938) is not a crash — it
is a rendering choice that reads fine to a normal-vision developer and carries
no information for a red-green colourblind operator. Nothing about running the
CLI surfaces the regression, so the palette needs an explicit contract.

The load-bearing assertion is :func:`test_no_semantic_map_uses_green`: every
map below independently reached for the traffic-light palette before the fix,
which is exactly the shape that comes back the next time someone adds a status
without reading the module docstring.
"""

from __future__ import annotations

import pytest

from poindexter.cli import _status_style as style
from poindexter.cli._status_style import (
    BUDGET_STATUS,
    HEALTH_STATUS,
    POST_STATUS,
    TASK_STATUS,
    color_for,
)

# Every map whose colour encodes a category, named for readable failures.
SEMANTIC_MAPS = {
    "POST_STATUS": POST_STATUS,
    "TASK_STATUS": TASK_STATUS,
    "HEALTH_STATUS": HEALTH_STATUS,
    "BUDGET_STATUS": BUDGET_STATUS,
}

# The declared roles — the only values a semantic map may hold.
ROLES = {
    style.SUCCESS,
    style.LIVE,
    style.ACTIVE,
    style.ATTENTION,
    style.FAILURE,
    style.NEUTRAL,
    style.INACTIVE,
}


@pytest.mark.parametrize("name,mapping", SEMANTIC_MAPS.items())
def test_no_semantic_map_uses_green(name: str, mapping: dict[str, str]) -> None:
    """Green is banned from maps where colour distinguishes categories.

    Under deuteranomaly green converges with red (and muddies against yellow),
    so a map holding green plus either of those encodes a distinction the
    operator cannot see.
    """
    greens = {
        status: colour
        for status, colour in mapping.items()
        if "green" in colour
    }
    assert not greens, (
        f"{name} maps {greens} to a green — see _status_style docstring; "
        f"pick a role (SUCCESS/LIVE/ACTIVE/...) instead of a raw colour"
    )


@pytest.mark.parametrize("name,mapping", SEMANTIC_MAPS.items())
def test_semantic_maps_only_use_declared_roles(
    name: str, mapping: dict[str, str]
) -> None:
    """Raw colour names bypass the palette's guarantees — roles only."""
    stray = {s: c for s, c in mapping.items() if c not in ROLES}
    assert not stray, f"{name} uses non-role colours {stray}"


def test_roles_are_mutually_distinct() -> None:
    """Two roles sharing a colour would silently collapse a distinction."""
    assert len(ROLES) == 7, f"expected 7 distinct role colours, got {sorted(ROLES)}"


@pytest.mark.parametrize(
    "mapping,left,right",
    [
        # The pair that motivated the issue: both were blue-ish before.
        (POST_STATUS, "published", "scheduled"),
        # Was green vs bright_green — near-identical even with normal vision.
        (TASK_STATUS, "approved", "published"),
        # Was green vs red — the canonical confusion pair.
        (TASK_STATUS, "published", "failed"),
        (TASK_STATUS, "approved", "rejected"),
        # The state needing operator action must stand apart from in-flight.
        (TASK_STATUS, "awaiting_approval", "in_progress"),
        (HEALTH_STATUS, "ok", "fail"),
        (HEALTH_STATUS, "ok", "warn"),
        (BUDGET_STATUS, "healthy", "critical"),
    ],
)
def test_confusable_states_render_differently(
    mapping: dict[str, str], left: str, right: str
) -> None:
    """States an operator must tell apart get different colours."""
    assert mapping[left] != mapping[right], (
        f"{left!r} and {right!r} both render as {mapping[left]!r}"
    )


def test_color_for_unknown_status_is_neutral() -> None:
    """An unrecognised status is inert, never guessed as success or failure."""
    assert color_for(POST_STATUS, "wat") == style.NEUTRAL
    assert color_for(TASK_STATUS, "") == style.NEUTRAL
    assert color_for(HEALTH_STATUS, None) == style.NEUTRAL


def test_color_for_known_status_resolves() -> None:
    assert color_for(POST_STATUS, "published") == style.LIVE
    assert color_for(TASK_STATUS, "awaiting_approval") == style.ATTENTION
    assert color_for(BUDGET_STATUS, "critical") == style.FAILURE


# ---------------------------------------------------------------------------
# Call sites — the palette only helps if the renderers actually use it.
# ---------------------------------------------------------------------------


def _captured_colors(monkeypatch, module, call) -> list[str | None]:
    """Run ``call`` with ``module.click.secho`` stubbed; return fg values."""
    seen: list[str | None] = []

    def _fake_secho(*args, **kwargs):
        seen.append(kwargs.get("fg"))

    monkeypatch.setattr(module.click, "secho", _fake_secho)
    call()
    return seen


def test_posts_summary_uses_shared_palette(monkeypatch) -> None:
    from poindexter.cli import posts

    colors = _captured_colors(
        monkeypatch,
        posts,
        lambda: posts._print_post_summary(
            {"status": "published", "id": "abc12345", "slug": "x", "title": "T"}
        ),
    )
    assert colors[0] == style.LIVE


def test_tasks_line_uses_shared_palette(monkeypatch) -> None:
    from poindexter.cli import tasks

    colors = _captured_colors(
        monkeypatch,
        tasks,
        lambda: tasks._print_task_one_line(
            {"id": "abc12345", "status": "awaiting_approval", "title": "T"}
        ),
    )
    assert colors[0] == style.ATTENTION


def test_doctor_score_bands_use_roles() -> None:
    from poindexter.cli.doctor import _score_color

    assert _score_color(95) == style.SUCCESS
    assert _score_color(75) == style.ACTIVE
    assert _score_color(10) == style.FAILURE
