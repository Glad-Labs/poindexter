"""Contract tests for ``scripts/nvidia-smi-exporter.py`` GPU parsing.

The host exporter parses ``nvidia-smi --query-gpu`` CSV into Prometheus
exposition text. It was originally written for a single GPU: it unpacked one
10-tuple and crashed with ``too many values to unpack (expected 10)`` the
instant a second card appeared (an RTX 3090 added alongside the 5090,
2026-06-27), silently blanking every ``nvidia_gpu_*`` series while the HTTP
endpoint kept returning 200 (so ``up`` stayed 1 and nothing alerted).

These tests pin the multi-GPU contract on the pure helpers:

1. ``_format_gpu_rows`` — one series per GPU, labelled with nvidia-smi's own
   ``index``; single-GPU still works; a malformed row is skipped not fatal.
2. ``get_total_power_metrics`` — sums power across *all* GPUs (the old code
   broke after the first match and undercounted the system-power estimate).
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _repo_root() -> Path:
    return next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _load_exporter():
    script = _repo_root() / "scripts" / "nvidia-smi-exporter.py"
    spec = spec_from_file_location("nvidia_smi_exporter", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXPORTER = _load_exporter()

# index, util, mem-util, mem-used, mem-total, temp, power, power-limit, fan, gclk, mclk
GPU0 = "0, 2, 0, 3198, 32607, 31, 43.57, 600.00, 0, 630, 13801"
GPU1 = "1, 0, 0, 111, 24576, 28, 17.28, 390.00, 0, 210, 405"


def _series(text: str) -> dict[str, str]:
    """Parse exposition text into {full-series-key: value}, ignoring comments."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.rpartition(" ")
        out[key] = val
    return out


# ---------------------------------------------------------------------------
# Multi-GPU: one series per card, labelled by nvidia-smi's own index.
# ---------------------------------------------------------------------------
def test_two_gpus_emit_one_series_each():
    series = _series(EXPORTER._format_gpu_rows(f"{GPU0}\n{GPU1}"))
    assert series['nvidia_gpu_temperature_celsius{gpu="0"}'] == "31"
    assert series['nvidia_gpu_temperature_celsius{gpu="1"}'] == "28"
    assert series['nvidia_gpu_memory_total_mib{gpu="0"}'] == "32607"
    assert series['nvidia_gpu_memory_total_mib{gpu="1"}'] == "24576"
    assert series['nvidia_gpu_power_draw_watts{gpu="1"}'] == "17.28"


def test_help_and_type_emitted_once_per_metric():
    text = EXPORTER._format_gpu_rows(f"{GPU0}\n{GPU1}")
    assert text.count("# TYPE nvidia_gpu_temperature_celsius gauge") == 1
    assert text.count("# HELP nvidia_gpu_temperature_celsius") == 1


def test_label_uses_smi_index_not_row_position():
    # Feed rows out of order — the label must follow the index field, not the
    # position in the output (survives CUDA_VISIBLE_DEVICES reordering).
    series = _series(EXPORTER._format_gpu_rows(f"{GPU1}\n{GPU0}"))
    assert series['nvidia_gpu_memory_total_mib{gpu="0"}'] == "32607"
    assert series['nvidia_gpu_memory_total_mib{gpu="1"}'] == "24576"


# ---------------------------------------------------------------------------
# Single GPU still works (regression guard for the original happy path).
# ---------------------------------------------------------------------------
def test_single_gpu_unchanged():
    series = _series(EXPORTER._format_gpu_rows(GPU0))
    assert series['nvidia_gpu_utilization_percent{gpu="0"}'] == "2"
    assert 'nvidia_gpu_utilization_percent{gpu="1"}' not in series


# ---------------------------------------------------------------------------
# Resilience: a malformed row is skipped, good rows survive; all-bad is benign.
# ---------------------------------------------------------------------------
def test_malformed_row_skipped_good_row_survives():
    series = _series(EXPORTER._format_gpu_rows(f"garbage,row\n{GPU1}"))
    assert series['nvidia_gpu_temperature_celsius{gpu="1"}'] == "28"
    assert 'nvidia_gpu_temperature_celsius{gpu="0"}' not in series


def test_no_parseable_rows_returns_comment_not_crash():
    out = EXPORTER._format_gpu_rows("garbage,row\n\n   ")
    assert out.startswith("# ")
    assert "nvidia_gpu_" not in out


# ---------------------------------------------------------------------------
# Total power sums every GPU, not just the first.
# ---------------------------------------------------------------------------
def test_total_power_sums_all_gpus():
    gpu = EXPORTER._format_gpu_rows(f"{GPU0}\n{GPU1}")
    series = _series(EXPORTER.get_total_power_metrics(gpu, "system_cpu_package_power_watts 100.0\n"))
    # 43.57 + 17.28 (GPUs) + 100.0 (CPU) + 50.0 (overhead) = 210.85
    assert float(series["system_total_power_estimate_watts"]) == 210.8
