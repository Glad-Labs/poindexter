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
import threading
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


# ---------------------------------------------------------------------------
# Shelly smart plug — true wall power via the local RPC (outlet meter).
# ---------------------------------------------------------------------------
def test_shelly_emits_wall_power_from_apower():
    fake = {"apower": 350.42, "voltage": 121.3, "current": 2.918}
    series = _series(
        EXPORTER.get_shelly_psu_metrics("http://10.0.0.5", _fetch=lambda url: fake)
    )
    assert series["psu_total_power_watts"] == "350.42"
    assert series["psu_line_voltage_volts"] == "121.3"
    assert series["psu_line_current_amps"] == "2.918"


def test_shelly_unconfigured_is_noop():
    assert EXPORTER.get_shelly_psu_metrics("") == ""


def test_shelly_fetch_error_is_graceful():
    def boom(url):
        raise OSError("connection refused")

    assert EXPORTER.get_shelly_psu_metrics("http://10.0.0.5", _fetch=boom) == ""


def test_shelly_missing_apower_no_sample():
    out = EXPORTER.get_shelly_psu_metrics("http://10.0.0.5", _fetch=lambda url: {})
    assert "psu_total_power_watts " not in out
    assert out.startswith("# ")


def test_shelly_queries_switch_getstatus_endpoint():
    captured = {}

    def spy(url):
        captured["url"] = url
        return {"apower": 100.0}

    EXPORTER.get_shelly_psu_metrics("http://10.0.0.5", _fetch=spy)
    assert captured["url"] == "http://10.0.0.5/rpc/Switch.GetStatus?id=0"


# ---------------------------------------------------------------------------
# Duplicate PSU sources collapse to a single psu_total_power_watts block.
# ---------------------------------------------------------------------------
def test_dedupe_keeps_first_psu_block_highest_priority_wins():
    shelly = (
        "# HELP psu_total_power_watts Wall power draw metered at the outlet (Shelly smart plug)\n"
        "# TYPE psu_total_power_watts gauge\n"
        "psu_total_power_watts 350.00\n"
    )
    hwinfo = (
        "# HELP psu_total_power_watts Wall power draw from the Corsair HXi PSU (HWiNFO)\n"
        "# TYPE psu_total_power_watts gauge\n"
        "psu_total_power_watts 342.00\n"
    )
    out = EXPORTER._dedupe_psu_metric(shelly + hwinfo)
    assert out.count("# TYPE psu_total_power_watts gauge") == 1
    assert out.count("# HELP psu_total_power_watts") == 1
    # Shelly is concatenated first, so its value survives.
    assert _series(out)["psu_total_power_watts"] == "350.00"


def test_dedupe_is_noop_for_single_source():
    single = (
        "# HELP psu_total_power_watts x\n"
        "# TYPE psu_total_power_watts gauge\n"
        "psu_total_power_watts 342.00\n"
    )
    out = EXPORTER._dedupe_psu_metric(single)
    assert out.count("# TYPE psu_total_power_watts gauge") == 1
    assert _series(out)["psu_total_power_watts"] == "342.00"


# ---------------------------------------------------------------------------
# Background collector — /metrics serves a CACHED snapshot refreshed off the
# request path, so a slow nvidia-smi/AIDA read can never blow a scraper's
# timeout (incident 2026-07-12: per-request collection intermittently took
# 2.5-6.7s+, exceeding the brain's 3s scrape timeout → false 150W-floor pages).
# ---------------------------------------------------------------------------
def test_refresh_snapshot_caches_body_for_read():
    """The collector writes the snapshot; a scrape reads it back verbatim,
    with no metric collection on the read path."""
    body = b"# cached\npsu_total_power_watts 321.0\n"
    EXPORTER._refresh_snapshot(_collect=lambda: body)
    assert EXPORTER._read_snapshot() == body


def test_collector_loop_refreshes_then_stops():
    """One refresh per iteration; a set stop-event ends the loop cleanly."""
    stop = threading.Event()
    calls = []

    def fake_refresh():
        calls.append(1)
        stop.set()  # end after a single iteration

    EXPORTER._collector_loop(
        0.0, _refresh=fake_refresh, _sleep=lambda _: None, _stop=stop
    )
    assert len(calls) == 1


def test_collector_loop_swallows_cycle_error_and_keeps_looping():
    """A per-cycle collector exception is logged, not fatal — the snapshot just
    goes stale for a cycle rather than killing the refresh thread."""
    stop = threading.Event()
    calls = []

    def fake_refresh():
        calls.append(1)
        stop.set()
        raise RuntimeError("collector boom")

    # Must return normally (no exception propagates out of the loop).
    EXPORTER._collector_loop(
        0.0, _refresh=fake_refresh, _sleep=lambda _: None, _stop=stop
    )
    assert len(calls) == 1


def test_collector_loop_escalates_systemexit_to_hard_exit():
    """The nvidia-smi watchdog raises SystemExit from get_gpu_metrics. In this
    non-main thread that would silently kill just the collector (leaving a
    frozen snapshot), so it must escalate to a real process exit for the host
    service manager to restart a clean instance."""
    stop = threading.Event()
    exits = []

    def fake_refresh():
        raise SystemExit(1)

    EXPORTER._collector_loop(
        0.0,
        _refresh=fake_refresh,
        _sleep=lambda _: None,
        _stop=stop,
        _hard_exit=lambda code: exits.append(code),
    )
    assert exits == [1]
