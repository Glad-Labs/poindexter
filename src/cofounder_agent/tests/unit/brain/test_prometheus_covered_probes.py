"""A Prometheus-covered brain probe must hand off to a rule that pages.

``health_probes.PROMETHEUS_COVERING_RULES`` lists the probes the brain keeps
quiet about while Alertmanager is healthy, each with the Prometheus rule(s)
that notify in its place. Nothing checked that hand-off, and it failed
without an error anywhere: ``PoindexterOllamaDown`` shipped as
``severity: warning`` (Discord only, per ``alert_dispatcher._channels_for``)
from 2026-04-19 to 2026-09-28, while ``probe_severity`` classified
``ollama_models`` as critical. Each side was working as written. They just
disagreed, so an unreachable Ollama never reached the phone.

The hand-off breaks in two ways, and both are checked against what ships:

* a covering rule is renamed, deleted or disabled by default, so the brain
  defers to nothing;
* a probe that ``probe_severity`` says pages is covered only by rules that
  don't.

"Ships" means the static alert files as written plus
``prometheus_rule_builder.DEFAULT_RULES``. An install can still override a
DB-rendered rule (``app_settings`` ``prometheus.rule.<name>``) or a probe
(``brain_probe_severity_overrides``). That is the operator's decision, not
drift, so it is out of scope here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from poindexter.brain import health_probes as hp
from poindexter.brain import probe_severity as ps
from poindexter.services import prometheus_rule_builder as rb
from tests.unit._nonempty import nonempty
from tests.unit.conftest import find_repo_root


def _static_rule_severities() -> dict[str, str | None]:
    """``alert name -> severity`` for every rule in the static alert files."""
    alerts_dir = (
        find_repo_root(Path(__file__)) / "infrastructure" / "prometheus" / "alerts"
    )
    severities: dict[str, str | None] = {}
    for path in nonempty(sorted(alerts_dir.glob("*.yml")), f"{alerts_dir}/*.yml"):
        # A comment-only placeholder file loads as None.
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for group in doc.get("groups") or []:
            for rule in group.get("rules") or []:
                if "alert" in rule:
                    severities[rule["alert"]] = (rule.get("labels") or {}).get("severity")
    return severities


@pytest.fixture(scope="module")
def shipped_severity() -> dict[str, str | None]:
    """Every rule that renders on a fresh install, with its severity. A
    DB-rendered rule disabled by default does not render, so it is left out
    and cannot count as coverage."""
    db_rendered = {
        name: rule.get("severity")
        for name, rule in rb.DEFAULT_RULES.items()
        if rule.get("enabled", True)
    }
    return {**db_rendered, **_static_rule_severities()}


_COVERING_PAIRS = sorted(
    (probe, rule)
    for probe, rules in hp.PROMETHEUS_COVERING_RULES.items()
    for rule in rules
)


@pytest.mark.unit
def test_covered_set_is_the_mapping():
    """The suppression check reads ``PROMETHEUS_COVERED_PROBES``; keep it
    derived from the mapping so a probe can't be suppressed without naming
    what covers it."""
    assert frozenset(hp.PROMETHEUS_COVERING_RULES) == hp.PROMETHEUS_COVERED_PROBES


@pytest.mark.unit
def test_covered_probes_are_real_probes():
    """A stale entry suppresses nothing but still reads as coverage."""
    unknown = set(hp.PROMETHEUS_COVERING_RULES) - set(hp.PROBES)
    assert not unknown, (
        f"PROMETHEUS_COVERING_RULES names probes that don't exist: {sorted(unknown)}"
    )


@pytest.mark.unit
def test_every_covered_probe_names_a_rule():
    for probe, rules in nonempty(
        hp.PROMETHEUS_COVERING_RULES.items(), "PROMETHEUS_COVERING_RULES"
    ):
        assert rules, f"{probe} is Prometheus-covered but names no covering rule"


@pytest.mark.unit
@pytest.mark.parametrize(("probe", "rule"), _COVERING_PAIRS)
def test_covering_rule_ships(probe, rule, shipped_severity):
    assert rule in shipped_severity, (
        f"{probe} defers to {rule!r}, but no static alert file defines it and "
        "prometheus_rule_builder.DEFAULT_RULES doesn't enable it. While "
        "Alertmanager is healthy the brain says nothing about this probe, so "
        "its failures would reach no one."
    )


@pytest.mark.unit
@pytest.mark.parametrize("probe", sorted(hp.PROMETHEUS_COVERING_RULES))
def test_a_paging_probe_is_covered_by_a_paging_rule(probe, shipped_severity):
    severity = ps.severity_for(probe, {})
    if not ps.is_paging_severity(severity):
        pytest.skip(f"{probe} is classified {severity}, so a non-paging rule matches it")
    rules = hp.PROMETHEUS_COVERING_RULES[probe]
    shipped = {rule: shipped_severity.get(rule) for rule in rules}
    assert any(ps.is_paging_severity(sev or "") for sev in shipped.values()), (
        f"probe_severity classifies {probe} as {severity} (it pages), but its "
        f"covering rules ship as {shipped}. While Alertmanager is healthy the "
        "brain defers to those rules, so this failure would reach Discord "
        "only. Raise a covering rule to critical, or reclassify the probe."
    )


@pytest.mark.unit
def test_an_ollama_outage_pages(shipped_severity):
    """The case that produced this file, pinned by name so it can't pass by
    skipping: ``ollama_models`` pages, and so does the rule it defers to."""
    assert ps.is_paging_severity(ps.severity_for("ollama_models", {}))
    assert hp.PROMETHEUS_COVERING_RULES["ollama_models"] == ("PoindexterOllamaDown",)
    assert shipped_severity["PoindexterOllamaDown"] == "critical"
