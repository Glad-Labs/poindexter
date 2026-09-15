"""Every metric family the host exporter emits must survive Prometheus ingestion.

The `nvidia-smi` scrape job carries a `metric_relabel_configs` **allowlist**.
A family missing from it is dropped silently and completely: the exporter
serves it, the scrape reports `up=1`, the target is green, and the series
simply never exists. There is no error anywhere.

That has now happened twice — `gpu_12vhpwr_*` shipped dark for a deploy, and
`openlinkhub_*` (stack#3763) was caught only because the deploy verification
checked *ingestion* rather than stopping at "the exporter serves it". The
config file warns about the trap in a comment; a comment is not a gate.

This test is the gate. It reads the families the exporter actually declares
(its `# HELP` lines — the exposition format requires one per family) and
asserts the allowlist regex admits each one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


def _repo_root() -> Path:
    return next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _emitted_families() -> set[str]:
    """Metric family names the exporter declares.

    Scans source text rather than importing and running the collectors,
    because those need real hardware (GPUs, i2c, an iCUE LINK bus) that CI
    does not have.

    The exporter declares families in TWO styles and both must be read, or the
    gate silently covers only part of the surface:

    1. A literal `# HELP <name>` — how every hand-written family is emitted.
    2. `# HELP {metric}` driven by `_GPU_METRIC_SPECS`, where the names live in
       a table instead of the format string. A literal-only scan misses all ten
       `nvidia_gpu_*` families.
    """
    src = (_repo_root() / "scripts" / "nvidia-smi-exporter.py").read_text(encoding="utf-8")
    families = set(re.findall(r"#\s*HELP\s+([a-zA-Z_][a-zA-Z0-9_]*)", src))
    # Style 2: the (name, help_text) table the GPU rows are generated from.
    spec_block = re.search(r"_GPU_METRIC_SPECS\s*=\s*\[(.*?)\n\]", src, re.S)
    if spec_block:
        families |= set(re.findall(r'\(\s*"([a-z][a-z0-9_]*)"', spec_block.group(1)))
    return families


def _keeplist_regex() -> str:
    cfg = yaml.safe_load(
        (_repo_root() / "infrastructure" / "prometheus" / "config" / "prometheus.yml")
        .read_text(encoding="utf-8")
    )
    for job in cfg["scrape_configs"]:
        if job.get("job_name") != "nvidia-smi":
            continue
        for rule in job.get("metric_relabel_configs", []):
            if rule.get("action") == "keep" and rule.get("source_labels") == ["__name__"]:
                return rule["regex"]
    raise AssertionError("no __name__ keep rule found on the nvidia-smi job")


# Families the exporter still *declares* but that are deliberately NOT ingested.
# Each needs a reason, so "dropped on purpose" can never be confused with
# "dropped by accident" — which is the entire failure mode this file guards.
# Verified 2026-09-14: every one of these emits ZERO series on the operator rig.
INTENTIONALLY_NOT_INGESTED = {
    # Produced from WINDOWS Energy Meter performance counters. Linux RAPL
    # energy_uj is root-only since the PLATYPUS mitigation, so both were dead
    # on Pop!_OS and their Hardware & Power panels were removed 2026-07-27.
    # Re-add here only alongside a Linux CPU-power producer.
    "system_cpu_package_power_watts": "Windows-only producer; dead on Linux",
    "system_cpu_core_power_watts": "Windows-only producer; dead on Linux",
    # Superseded by node_exporter's node_hwmon_* family, which is what the
    # Hardware & Power sensors row actually reads. lm-sensors is not even
    # configured on the operator host.
    "lm_sensors_temperature_celsius": "superseded by node_hwmon_temp_celsius",
    "lm_sensors_fan_rpm": "superseded by node_hwmon_fan_rpm",
    "lm_sensors_voltage_volts": "superseded by node_hwmon_in_volts",
}


def test_exporter_emits_families_and_they_are_discoverable():
    """Scan floor: if the extraction breaks, every other assertion here passes
    vacuously and the gate silently disarms."""
    families = _emitted_families()
    assert len(families) >= 10, f"only found {len(families)} families — extraction broke"
    # Anchors across three independent sources, so a refactor of any one of
    # them cannot blind the whole check.
    assert "nvidia_gpu_utilization_percent" in families
    assert "psu_total_power_watts" in families
    assert "openlinkhub_pump_rpm" in families


def test_every_emitted_family_survives_the_keeplist():
    regex = _keeplist_regex()
    # Prometheus anchors relabel regexes at both ends.
    compiled = re.compile(f"^(?:{regex})$")
    dropped = {f for f in _emitted_families() if not compiled.match(f)}
    undeclared = sorted(dropped - set(INTENTIONALLY_NOT_INGESTED))
    assert not undeclared, (
        "These families are emitted by the exporter but DROPPED at ingestion by "
        f"the nvidia-smi keep-list: {undeclared}\n"
        "Prometheus will report up=1, the target will look healthy, and the "
        "series will simply never exist. Either add the family to "
        "metric_relabel_configs in infrastructure/prometheus/config/prometheus.yml, "
        "or — if the drop is deliberate — declare it with a reason in "
        "INTENTIONALLY_NOT_INGESTED in this file."
    )


def test_declared_exclusions_are_still_actually_excluded():
    """Keeps INTENTIONALLY_NOT_INGESTED honest in the other direction.

    If a family gets added to the keep-list later, its entry here is stale and
    should go — otherwise the list slowly becomes a pile of untrue claims that
    future readers have to re-derive.
    """
    compiled = re.compile(f"^(?:{_keeplist_regex()})$")
    now_ingested = sorted(f for f in INTENTIONALLY_NOT_INGESTED if compiled.match(f))
    assert not now_ingested, (
        f"{now_ingested} are in the keep-list but still listed as deliberately "
        "excluded — drop them from INTENTIONALLY_NOT_INGESTED."
    )


def test_declared_exclusions_are_families_the_exporter_really_emits():
    """A stale name here would silently shrink the gate's coverage."""
    emitted = _emitted_families()
    unknown = sorted(set(INTENTIONALLY_NOT_INGESTED) - emitted)
    assert not unknown, (
        f"{unknown} are declared as excluded but the exporter no longer emits "
        "them — remove the stale entries."
    )


@pytest.mark.parametrize(
    "family",
    [
        "openlinkhub_up",
        "openlinkhub_pump_rpm",
        "openlinkhub_coolant_celsius",
        "openlinkhub_fan_rpm",
        "openlinkhub_probe_celsius",
        "openlinkhub_device_critical",
    ],
)
def test_water_loop_families_are_admitted(family):
    """Named explicitly: the loop alerts are gated on these series existing,
    so a keep-list edit that drops them would disarm pump monitoring while
    every dashboard and target still read healthy."""
    compiled = re.compile(f"^(?:{_keeplist_regex()})$")
    assert compiled.match(family), f"{family} would be dropped at ingestion"
