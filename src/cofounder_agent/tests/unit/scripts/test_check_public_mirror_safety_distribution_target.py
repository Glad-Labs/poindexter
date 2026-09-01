"""Contract test for the "operator domain as a distribution target" leak rule.

``pipeline_distributions.target`` names WHERE an artifact of a task landed.
Every value is a platform, with one exception: the row saying the post itself
went live on the site this install publishes. Until poindexter#1038 that row was
stamped with the source operator's own domain — hardcoded at three write sites
and matched by name at five read sites (two view bodies, the yield query, two
Grafana panels).

The existing ``gladlabs.io`` rule never saw it. That rule is scoped to a SQL
``VALUES`` tuple, on the theory that a leaked operator domain looks like a
seeded default; a Python kwarg (``target="<domain>"``) and a ``WHERE``
predicate (``target = '<domain>'``) are neither of them a seed tuple. And
because the literal matched on BOTH sides, a fresh OSS install was never broken
by it — there was no failing behaviour to notice, only every fork labelling its
own publishes with someone else's brand.

The replacement rule is deliberately not ``gladlabs.io``-specific: any hostname
in this position is the same bug, because the read side is a SQL view and a
view cannot consult ``app_settings.site_domain`` to learn the real one.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _load_check_module():
    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )
    script = repo_root / "scripts" / "ci" / "check_public_mirror_safety.py"
    spec = spec_from_file_location(
        "check_public_mirror_safety_distribution_target", script,
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = _load_check_module()


def _target_pattern():
    for lp in CHECK._LEAK_PATTERNS:
        if "distribution target" in lp.label:
            return lp
    raise AssertionError(
        "expected a leak pattern guarding operator domains used as a "
        "distribution target — see poindexter#1038"
    )


# ---------------------------------------------------------------------------
# The shapes that actually leaked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "line",
    [
        # routes/task_publishing_routes.py, both call sites.
        '                            target="gladlabs.io",',
        # modules/content/auto_publish.py.
        "            target='gladlabs.io',",
        # The view bodies + the yield query's exclusion.
        "WHERE (pd.task_id = pt.task_id AND pd.target = 'gladlabs.io')",
        "       AND target <> 'gladlabs.io'",
        # Not domain-specific: a fork's own hardcoded domain is the same bug.
        'target = "example.co.uk"',
        "target='my-blog.dev'",
    ],
)
def test_flags_a_hostname_used_as_a_target(line):
    assert _target_pattern().regex.search(line), (
        f"{line!r} puts a hostname in a distribution target and must be flagged"
    )


# ---------------------------------------------------------------------------
# The shapes that must keep passing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "line",
    [
        # A platform name is the normal case and is not operator identity.
        '                target="youtube",',
        "     WHERE pd.target = 'youtube'",
        # The fix itself: a sentinel, and the constant that carries it.
        "            target=SITE_TARGET,",
        "target = 'site'",
        # The widened readers — the legacy value appears, but as a back-compat
        # list member rather than as the thing a target is set to.
        "AND target NOT IN ('site', 'gladlabs.io')",
        "((pd.target)::text = ANY (ARRAY['site'::text, 'gladlabs.io'::text]))",
        "       AND target <> ALL ($2::text[])",
        # medium, not target — same table, different column vocabulary.
        "self.target = 'video_short'",
        # \b must not fire inside a longer identifier.
        'build_target = "linux"',
    ],
)
def test_does_not_flag_a_legitimate_target(line):
    assert not _target_pattern().regex.search(line), (
        f"{line!r} is a legitimate target and must not be flagged"
    )


def test_the_rule_names_the_sentinel_in_its_remediation():
    """A guard that says "don't" without saying "instead, this" gets worked
    around. The remediation text has to name the constant to reach for."""
    assert "SITE_TARGET" in _target_pattern().why


def test_the_shipped_tree_is_clean():
    """The rule is only worth having if the tree it guards passes it — and this
    is what catches a future write site re-introducing the literal."""
    from services.pipeline_db import LEGACY_SITE_TARGETS

    repo_root = next(
        p for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )
    pattern = _target_pattern()
    offenders = []
    for rel in (
        "src/cofounder_agent/routes/task_publishing_routes.py",
        "src/cofounder_agent/modules/content/auto_publish.py",
        "src/cofounder_agent/services/pipeline_db.py",
        "src/cofounder_agent/services/distribution_yield.py",
        "src/cofounder_agent/services/migrations/0000_baseline.schema.sql",
        "infrastructure/grafana/dashboards/cost-analytics.json",
    ):
        path = repo_root / rel
        assert path.exists(), f"{rel} moved — update this test's file list"
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if pattern.regex.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "a distribution target is set to a hostname again:\n  "
        + "\n  ".join(offenders)
    )
    # The legacy value is still referenced on the READ side, deliberately —
    # back-compat for rows written before the cutover. Guard that it is only
    # ever a member of the accepted-values list, never something a target is
    # assigned to.
    assert LEGACY_SITE_TARGETS, (
        "dropping the legacy target list would make pre-cutover rows read as "
        "unpublished — see services/pipeline_db.SITE_TARGETS"
    )
