"""Contract tests for the OpenLinkHub water-loop source in the host exporter.

The loop is the only subsystem on this machine with no redundant instrument:
if these series are wrong, a failing pump is invisible until CPU/GPU
temperatures climb, which lags badly on a ~1500mm loop because the thermal
mass is so large.

The distinctions pinned here are the ones that would silently destroy the
signal rather than break it loudly:

1. **Coolant vs air.** QX fans carry their own probes reading hub AIR. Only
   the pump/reservoir and CPU-block probes are liquid. Labelling fan air as
   coolant would make every loop threshold meaningless while still looking
   like working telemetry.
2. **Pump identified by ``description``, not product name**, so an XD5→XD6→AIO
   swap cannot reclassify the loop's only pump as a fan and blank the alert.
3. **``openlinkhub_up`` is emitted on every path, including failure.** A dead
   exporter and a dead pump must not look identical; the pump alert is
   ``up == 1 and pump_rpm == 0``, which is unwritable if failure emits nothing.
"""

from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return next(
        p
        for p in Path(__file__).resolve().parents
        if (p / "pyproject.toml").exists() and (p / "src").exists()
    )


def _load_exporter():
    script = _repo_root() / "scripts" / "nvidia-smi-exporter.py"
    spec = spec_from_file_location("nvidia_smi_exporter_olh", script)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXPORTER = _load_exporter()


def _series(text: str) -> list[str]:
    """Sample lines only — HELP/TYPE comments stripped."""
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


def _names(text: str) -> set[str]:
    return {ln.split("{")[0].split(" ")[0] for ln in _series(text)}


def _value(text: str, metric: str, must_contain: str = "") -> float:
    for ln in _series(text):
        if ln.startswith(metric) and must_contain in ln:
            return float(ln.rsplit(" ", 1)[1])
    raise AssertionError(f"{metric} ({must_contain!r}) not found in:\n{text}")


# --- payload shaped like the real /api/devices response --------------------

def _payload(**overrides) -> dict:
    """Trimmed capture of a real two-hub iCUE LINK tree.

    Keeps one of each thing that behaves differently: a QX fan with an air
    probe, an LX fan with no probe (temperature 0.0 filler), the link adapter
    (rpm 0 and no HasSpeed), the XD6 pump/res, and a peripheral whose
    ``GetDevice`` is null.
    """
    payload = {
        "code": 200,
        "devices": {
            "MOUSE0001": {
                "Product": "M65 PRO RGB",
                "GetDevice": None,
            },
            "HUB0001": {
                "Product": "iCUE LINK System Hub",
                "GetDevice": {
                    "product": "iCUE LINK System Hub",
                    "IsCritical": False,
                    "devices": {
                        "1": {
                            "name": "iCUE LINK QX RGB",
                            "description": "Fan",
                            "label": "Set Label",
                            "rpm": 711,
                            "temperature": 33.1,
                            "HasSpeed": True,
                            "HasTemps": True,
                            "IsTemperatureProbe": True,
                        },
                        "3": {
                            "name": "iCUE LINK LX RGB",
                            "description": "Fan",
                            "label": "",
                            "rpm": 783,
                            "temperature": 0,
                            "HasSpeed": True,
                            "HasTemps": True,
                        },
                        "2": {
                            "name": "iCUE LINK ADAPTER",
                            "description": "Adapter",
                            "rpm": 0,
                            "temperature": 0,
                            "HasSpeed": False,
                            "IsLinkAdapter": True,
                        },
                        "13": {
                            "name": "iCUE LINK XD6 ELITE",
                            "description": "Pump/Res",
                            "label": "Set Label",
                            "rpm": 4618,
                            "temperature": 32.8,
                            "HasSpeed": True,
                            "HasTemps": True,
                            "IsTemperatureProbe": True,
                        },
                    },
                },
            },
            "BLOCK0001": {
                "Product": "XC7 ELITE LCD",
                "GetDevice": {
                    "product": "XC7 ELITE LCD",
                    "Temperature": 37.1,
                    "HasTemps": True,
                },
            },
        },
    }
    payload.update(overrides)
    return payload


# --- the load-bearing distinction: coolant is not air ----------------------

def test_pump_probe_is_coolant_and_fan_probe_is_not():
    """QX fan probes read hub AIR; only pump/res is liquid.

    Conflating them is the failure that leaves the telemetry looking healthy
    while every loop threshold is measuring the wrong fluid.
    """
    out = EXPORTER._format_openlinkhub_rows(_payload())

    coolant = [ln for ln in _series(out) if ln.startswith("openlinkhub_coolant_celsius")]
    assert any('source="pump_res"' in ln and "32.8" in ln for ln in coolant)
    # The 33.1 QX air reading must NOT appear as coolant...
    assert not any("33.1" in ln for ln in coolant)
    # ...it belongs to the air-probe series.
    assert any(
        ln.startswith("openlinkhub_probe_celsius") and "33.1" in ln
        for ln in _series(out)
    )


def test_cpu_block_temperature_is_coolant_with_its_own_source_label():
    """The block outlet is the hot end of the loop; delta against the
    reservoir is what reveals falling flow before an absolute threshold trips.
    """
    out = EXPORTER._format_openlinkhub_rows(_payload())
    assert _value(out, "openlinkhub_coolant_celsius", 'source="cpu_block"') == 37.1
    assert _value(out, "openlinkhub_coolant_celsius", 'source="pump_res"') == 32.8


# --- pump identification ---------------------------------------------------

def test_pump_is_its_own_metric_not_a_fan():
    out = EXPORTER._format_openlinkhub_rows(_payload())
    assert _value(out, "openlinkhub_pump_rpm") == 4618
    assert not any(
        ln.startswith("openlinkhub_fan_rpm") and "XD6" in ln for ln in _series(out)
    )


@pytest.mark.parametrize("description", ["Pump/Res", "AIO Pump", "pump"])
def test_pump_detected_by_description_across_hardware_variants(description):
    """Keyed on the semantic field, so an XD5→XD6→AIO swap cannot silently
    demote the only pump to a fan and blank the alert."""
    payload = _payload()
    payload["devices"]["HUB0001"]["GetDevice"]["devices"]["13"]["description"] = description
    payload["devices"]["HUB0001"]["GetDevice"]["devices"]["13"]["name"] = "Some Other Cooler"
    out = EXPORTER._format_openlinkhub_rows(payload)
    assert _value(out, "openlinkhub_pump_rpm") == 4618


def test_stopped_pump_reports_zero_rather_than_vanishing():
    """A stopped pump must emit rpm 0, not disappear — an absent series is
    indistinguishable from a dead exporter and cannot be alerted on."""
    payload = _payload()
    payload["devices"]["HUB0001"]["GetDevice"]["devices"]["13"]["rpm"] = 0
    out = EXPORTER._format_openlinkhub_rows(payload)
    assert _value(out, "openlinkhub_pump_rpm") == 0


# --- filler and non-hardware rows ------------------------------------------

def test_zero_temperature_filler_is_not_emitted_as_a_reading():
    """OpenLinkHub reports 0.0 for 'no probe fitted'. Emitting that would drag
    any average down and read as a freezing sensor."""
    out = EXPORTER._format_openlinkhub_rows(_payload())
    assert not any(ln.endswith(" 0.0") or ln.endswith(" 0") for ln in _series(out)
                   if "celsius" in ln)


def test_link_adapter_is_not_counted_as_a_fan():
    out = EXPORTER._format_openlinkhub_rows(_payload())
    assert not any("ADAPTER" in ln for ln in _series(out))


def test_peripheral_without_telemetry_is_skipped():
    out = EXPORTER._format_openlinkhub_rows(_payload())
    assert "M65" not in out


def test_fan_without_probe_still_reports_rpm():
    """The LX fan has no temperature probe but is still a real fan."""
    out = EXPORTER._format_openlinkhub_rows(_payload())
    assert _value(out, "openlinkhub_fan_rpm", "LX RGB") == 783


# --- label hygiene ---------------------------------------------------------

def test_placeholder_label_is_dropped():
    """OpenLinkHub's unset-label placeholder would otherwise put 'Set Label'
    on a dozen series and read as a real operator name."""
    out = EXPORTER._format_openlinkhub_rows(_payload())
    assert 'label="Set Label"' not in out


def test_operator_label_is_preserved_and_escaped():
    payload = _payload()
    payload["devices"]["HUB0001"]["GetDevice"]["devices"]["13"]["label"] = 'Front "Rad"'
    out = EXPORTER._format_openlinkhub_rows(payload)
    assert 'label="Front \\"Rad\\""' in out


# --- failure modes: up must always be answerable ---------------------------

def test_fetch_error_reports_down_rather_than_silence():
    def boom(url):
        raise OSError("connection refused")

    out = EXPORTER.get_openlinkhub_metrics("http://olh.test", _fetch=boom)
    assert _value(out, "openlinkhub_up") == 0
    assert "openlinkhub_pump_rpm" not in out


def test_malformed_payload_reports_down_rather_than_raising():
    out = EXPORTER.get_openlinkhub_metrics(
        "http://olh.test", _fetch=lambda url: {"devices": "not-a-dict"}
    )
    assert _value(out, "openlinkhub_up") == 0


def test_healthy_scrape_reports_up():
    out = EXPORTER.get_openlinkhub_metrics(
        "http://olh.test", _fetch=lambda url: _payload()
    )
    assert _value(out, "openlinkhub_up") == 1
    assert _value(out, "openlinkhub_pump_rpm") == 4618


def test_fetch_targets_the_devices_endpoint():
    seen = {}

    def fake(url):
        seen["url"] = url
        return _payload()

    EXPORTER.get_openlinkhub_metrics("http://olh.test/", _fetch=fake)
    assert seen["url"] == "http://olh.test/api/devices"


# --- URL resolution --------------------------------------------------------

def test_url_prefers_env_for_containerized_run(monkeypatch):
    """The container cannot use localhost (that is the container), so compose
    passes host.docker.internal through env — env must win."""
    monkeypatch.setenv("OPENLINKHUB_URL", "http://host.docker.internal:27003/")
    assert EXPORTER._resolve_openlinkhub_url() == "http://host.docker.internal:27003"


def test_url_falls_back_to_bootstrap_then_localhost_default(monkeypatch):
    monkeypatch.delenv("OPENLINKHUB_URL", raising=False)
    monkeypatch.setattr(EXPORTER, "_read_openlinkhub_url_from_bootstrap", lambda: "")
    assert EXPORTER._resolve_openlinkhub_url() == "http://localhost:27003"

    monkeypatch.setattr(
        EXPORTER, "_read_openlinkhub_url_from_bootstrap", lambda: "http://10.0.0.9:27003"
    )
    assert EXPORTER._resolve_openlinkhub_url() == "http://10.0.0.9:27003"


# --- wiring ----------------------------------------------------------------

def test_source_is_wired_into_the_collector():
    """A source that is never collected exports nothing, no matter how correct
    its formatter is."""
    import inspect

    src = inspect.getsource(EXPORTER._collect_all_metrics)
    assert "get_openlinkhub_metrics()" in src
    assert "olh" in src


def test_no_flow_rate_series_is_invented():
    """The inline impeller meter is not on the iCUE LINK bus, so there is no
    flow reading to publish. A fabricated one would be worse than the gap."""
    out = EXPORTER._format_openlinkhub_rows(_payload())
    assert "flow" not in out.lower()


def test_empty_device_tree_reports_down_not_healthy_silence():
    """The hub answering with no telemetry device is a fault (lost HID /
    unplugged hub), not an idle state. Reporting up=1 with no pump series is
    exactly the healthy-looking blindness this gauge exists to prevent."""
    out = EXPORTER.get_openlinkhub_metrics(
        "http://olh.test", _fetch=lambda url: {"code": 200, "devices": {}}
    )
    assert _value(out, "openlinkhub_up") == 0
