"""Prometheus exporter for NVIDIA GPU + system power + AIDA64 + liquidctl metrics.
Serves on port 9835. Scraped by Grafana Alloy every 15s.

Cross-platform: works on Windows (AIDA64 + Energy Meter) and Linux (lm-sensors + liquidctl).

Metrics:
  - nvidia_gpu_* — GPU utilization, memory, temp, power, clocks, fan
  - system_cpu_power_watts — Total CPU package power (AMD RAPL via Energy Meter)
  - system_cpu_core_power_watts — Per-core power draw
  - system_total_power_watts — CPU + GPU combined power estimate
  - aida64_* — All AIDA64 sensors (when shared memory is enabled, Windows)
  - psu_* — Corsair HXi PSU metrics (via liquidctl or AIDA64)
  - lm_sensors_* — Linux hardware sensors (temps, voltages, fans)
"""
import re
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

# Hide console windows when spawning subprocesses on Windows
_SUBPROCESS_KWARGS = {}
if sys.platform == "win32":
    _si = subprocess.STARTUPINFO()
    _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _si.wShowWindow = 0  # SW_HIDE
    _SUBPROCESS_KWARGS = {"startupinfo": _si, "creationflags": subprocess.CREATE_NO_WINDOW}

PORT = 9835

def get_gpu_metrics():
    """Query nvidia-smi and return Prometheus-format metrics."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,clocks.current.graphics,clocks.current.memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, **_SUBPROCESS_KWARGS
        )
        if result.returncode != 0:
            return "# nvidia-smi failed\n"

        values = [v.strip() for v in result.stdout.strip().split(",")]
        if len(values) < 10:
            return "# nvidia-smi returned incomplete data\n"

        gpu_util, mem_util, mem_used, mem_total, temp, power, power_limit, fan, clock_gpu, clock_mem = values

        lines = [
            "# HELP nvidia_gpu_utilization_percent GPU utilization percentage",
            "# TYPE nvidia_gpu_utilization_percent gauge",
            f'nvidia_gpu_utilization_percent{{gpu="0"}} {gpu_util}',
            "# HELP nvidia_gpu_memory_utilization_percent GPU memory utilization percentage",
            "# TYPE nvidia_gpu_memory_utilization_percent gauge",
            f'nvidia_gpu_memory_utilization_percent{{gpu="0"}} {mem_util}',
            "# HELP nvidia_gpu_memory_used_mib GPU memory used in MiB",
            "# TYPE nvidia_gpu_memory_used_mib gauge",
            f'nvidia_gpu_memory_used_mib{{gpu="0"}} {mem_used}',
            "# HELP nvidia_gpu_memory_total_mib GPU memory total in MiB",
            "# TYPE nvidia_gpu_memory_total_mib gauge",
            f'nvidia_gpu_memory_total_mib{{gpu="0"}} {mem_total}',
            "# HELP nvidia_gpu_temperature_celsius GPU temperature in Celsius",
            "# TYPE nvidia_gpu_temperature_celsius gauge",
            f'nvidia_gpu_temperature_celsius{{gpu="0"}} {temp}',
            "# HELP nvidia_gpu_power_draw_watts GPU power draw in watts",
            "# TYPE nvidia_gpu_power_draw_watts gauge",
            f'nvidia_gpu_power_draw_watts{{gpu="0"}} {power}',
            "# HELP nvidia_gpu_power_limit_watts GPU power limit in watts",
            "# TYPE nvidia_gpu_power_limit_watts gauge",
            f'nvidia_gpu_power_limit_watts{{gpu="0"}} {power_limit}',
            "# HELP nvidia_gpu_fan_speed_percent GPU fan speed percentage",
            "# TYPE nvidia_gpu_fan_speed_percent gauge",
            f'nvidia_gpu_fan_speed_percent{{gpu="0"}} {fan}',
            "# HELP nvidia_gpu_clock_graphics_mhz GPU graphics clock in MHz",
            "# TYPE nvidia_gpu_clock_graphics_mhz gauge",
            f'nvidia_gpu_clock_graphics_mhz{{gpu="0"}} {clock_gpu}',
            "# HELP nvidia_gpu_clock_memory_mhz GPU memory clock in MHz",
            "# TYPE nvidia_gpu_clock_memory_mhz gauge",
            f'nvidia_gpu_clock_memory_mhz{{gpu="0"}} {clock_mem}',
        ]
        return "\n".join(lines) + "\n"
    except Exception as e:
        return f"# error: {e}\n"


def _escape_label_value(value: str) -> str:
    """Escape a Prometheus label value: backslash, quote, newline."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def get_compute_apps_metrics():
    """Query nvidia-smi for per-process VRAM use and emit metrics.

    Lets the worker's gpu_scheduler distinguish "we're using our own
    GPU" from "an external process (like a game) is using the GPU."
    Without this, the scheduler pauses the pipeline whenever
    wan-server / sdxl-server / ollama hold VRAM idle, which is a
    false positive — they're ours, we asked for them.

    Output is one ``nvidia_gpu_compute_apps_used_memory_mib`` gauge
    per active GPU compute process, labeled with the PID and the
    process name. The scheduler sums the memory of processes whose
    name isn't in its whitelist; only a non-zero external sum
    triggers a pause.
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5, **_SUBPROCESS_KWARGS,
        )
        if result.returncode != 0:
            return ""

        lines = [
            "# HELP nvidia_gpu_compute_apps_used_memory_mib VRAM in MiB used by each GPU compute process",
            "# TYPE nvidia_gpu_compute_apps_used_memory_mib gauge",
        ]
        for entry in result.stdout.strip().splitlines():
            entry = entry.strip()
            if not entry:
                continue
            # CSV: "<pid>, <process_name>, <used_memory_mib>"
            parts = [p.strip() for p in entry.split(",")]
            if len(parts) < 3:
                continue
            pid, name, mem = parts[0], parts[1], parts[2]
            try:
                mem_val = float(mem)
            except (TypeError, ValueError):
                continue
            lines.append(
                f'nvidia_gpu_compute_apps_used_memory_mib{{pid="{_escape_label_value(pid)}",name="{_escape_label_value(name)}"}} {mem_val}'
            )
        return "\n".join(lines) + "\n"
    except Exception:
        return ""


def get_cpu_power_metrics():
    """Read CPU power from Windows Energy Meter performance counters (AMD RAPL).
    Returns Prometheus-format metrics string.
    """
    if sys.platform != "win32":
        return ""
    # 2026-04-25: this function used to shell out to
    # ``powershell.exe Get-Counter '\Energy Meter(*)\Power'`` every
    # scrape. Despite passing CREATE_NO_WINDOW + STARTUPINFO with
    # SW_HIDE, the powershell.exe child still flashed a brief black
    # console window in the top-left of the screen on every Prometheus
    # scrape (~15s). Confirmed via process monitoring:
    # ``powershell.exe parent=pythonw.exe`` was the only repeating
    # offender. The CPU energy-meter readout is useful but not
    # critical, and a long-lived ``Get-Counter`` over a separate
    # background pipe would be more code than this earns. Disabled
    # for now; revisit by reading RAPL counters via PDH/pywin32 if
    # we want them back without the popup tax.
    return "# cpu power: disabled (see comment in nvidia-smi-exporter.py)\n"
    try:
        ps_script = (
            "Get-Counter '\\Energy Meter(*)\\Power' -ErrorAction Stop | "
            "ForEach-Object { $_.CounterSamples | ForEach-Object { "
            "\"$($_.InstanceName)=$([math]::Round($_.CookedValue / 1000, 2))\" } }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=10, **_SUBPROCESS_KWARGS
        )
        if result.returncode != 0:
            return "# cpu power: counter read failed\n"

        lines = []
        pkg_power = 0.0
        core_lines = []

        for entry in result.stdout.strip().split("\n"):
            entry = entry.strip()
            if not entry or "=" not in entry:
                continue
            name, val = entry.rsplit("=", 1)
            try:
                watts = float(val)
            except ValueError:
                continue

            if name == "_total":
                continue
            elif "pkg" in name:
                pkg_power = watts
            elif "core" in name:
                # Extract core number: rapl_package0_core5_core -> 5
                parts = name.split("_")
                core_num = "0"
                for p in parts:
                    if p.startswith("core") and p[4:].isdigit():
                        core_num = p[4:]
                        break
                core_lines.append(
                    f'system_cpu_core_power_watts{{core="{core_num}"}} {watts}'
                )

        lines.append("# HELP system_cpu_package_power_watts Total CPU package power draw (AMD RAPL)")
        lines.append("# TYPE system_cpu_package_power_watts gauge")
        lines.append(f"system_cpu_package_power_watts {pkg_power}")

        # Per-core power dropped to reduce metric series count (Grafana free tier)
        # Package total is sufficient for monitoring

        return "\n".join(lines) + "\n"
    except Exception as e:
        return f"# cpu power error: {e}\n"


def get_total_power_metrics(gpu_text, cpu_text):
    """Calculate combined system power estimate from GPU + CPU metrics."""
    gpu_watts = 0.0
    cpu_watts = 0.0
    try:
        for line in gpu_text.split("\n"):
            if line.startswith("nvidia_gpu_power_draw_watts{"):
                gpu_watts = float(line.split()[-1])
                break
    except (ValueError, IndexError):
        pass
    try:
        for line in cpu_text.split("\n"):
            if line.startswith("system_cpu_package_power_watts "):
                cpu_watts = float(line.split()[-1])
                break
    except (ValueError, IndexError):
        pass

    # Estimate: CPU + GPU + ~50W for mobo/RAM/drives/fans (typical desktop overhead)
    overhead_watts = 50.0
    total = cpu_watts + gpu_watts + overhead_watts

    lines = [
        "# HELP system_total_power_estimate_watts Estimated total system power (CPU + GPU + 50W overhead)",
        "# TYPE system_total_power_estimate_watts gauge",
        f"system_total_power_estimate_watts {total:.1f}",
        "# HELP system_overhead_power_estimate_watts Estimated non-CPU/GPU power (mobo, RAM, drives, fans)",
        "# TYPE system_overhead_power_estimate_watts gauge",
        f"system_overhead_power_estimate_watts {overhead_watts}",
    ]
    return "\n".join(lines) + "\n"


def _read_aida64_shm():
    """Read raw data from AIDA64 shared memory using Windows API."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.MapViewOfFile.restype = ctypes.c_void_p
    kernel32.MapViewOfFile.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t,
    ]
    kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel32.UnmapViewOfFile.restype = wintypes.BOOL

    handle = kernel32.OpenFileMappingW(0x0004, False, "AIDA64_SensorValues")
    if not handle:
        return None

    buf = kernel32.MapViewOfFile(handle, 0x0004, 0, 0, 0)
    if not buf:
        kernel32.CloseHandle(handle)
        return None

    raw = ctypes.cast(buf, ctypes.c_char_p).value
    data = raw.decode("utf-8", errors="ignore").strip() if raw else ""
    kernel32.UnmapViewOfFile(buf)
    kernel32.CloseHandle(handle)
    return data


def get_aida64_metrics():
    """Read all sensors from AIDA64 shared memory.

    AIDA64 shared memory uses XML-like format with typed sections:
      <temp>  — Temperature (Celsius)
      <fan>   — Fan speed (RPM)
      <volt>  — Voltage (V)
      <pwr>   — Power (W)
      <curr>  — Current (A)
      <duty>  — Duty cycle (%)
      <sys>   — System info (clocks, utilization, memory, etc.)

    Each entry: <type><id>ID</id><label>Label</label><value>Value</value></type>
    """
    if sys.platform != "win32":
        return ""

    raw = _read_aida64_shm()
    if raw is None:
        return "# aida64: shared memory not available (enable in AIDA64 Preferences > External Applications)\n"
    if not raw:
        return "# aida64: shared memory empty\n"

    # Map AIDA64 XML tag types to Prometheus metric names
    type_map = {
        "temp": ("aida64_temperature_celsius", "Temperature sensors from AIDA64"),
        "fan": ("aida64_fan_rpm", "Fan speed sensors from AIDA64"),
        "volt": ("aida64_voltage_volts", "Voltage sensors from AIDA64"),
        "pwr": ("aida64_power_watts", "Power sensors from AIDA64"),
        "curr": ("aida64_current_amps", "Current sensors from AIDA64"),
        "duty": ("aida64_duty_percent", "Duty cycle sensors from AIDA64"),
    }

    # Parse XML-like entries: <type><id>X</id><label>Y</label><value>Z</value></type>
    pattern = re.compile(
        r"<(temp|fan|volt|pwr|curr|duty|sys)>"
        r"<id>([^<]+)</id>"
        r"<label>([^<]+)</label>"
        r"<value>([^<]*)</value>"
        r"</\1>"
    )

    lines = []
    seen_metrics = set()
    psu_total_power = None

    # Only export important sensors to stay within Grafana Cloud free tier limits.
    # Skip: sys sensors (clocks, dates — not useful), most voltages, per-pin GPU power.
    # Keep: temps, fan RPMs, total power, duty cycles, PSU data.
    SKIP_SENSORS = {
        "syear", "smonth", "sdayofmonth", "sdow", "sweekofyear",
        "shour12", "shour24", "smin", "ssec",  # date/time — useless
        "scpumul",  # CPU multiplier — not needed
    }
    # Skip per-pin GPU power (6 pins), keep total GPU power
    SKIP_PREFIXES = ("scc_1_", "vgpu1p", "pgpu1p")  # per-core clocks, per-pin voltage/power

    for match in pattern.finditer(raw):
        sensor_type, sensor_id, label, val_str = match.groups()

        try:
            value = float(val_str)
        except (ValueError, TypeError):
            continue

        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", sensor_id).lower()
        safe_label = label.replace('"', '\\"')

        # Skip unimportant sensors
        if sensor_id.lower() in SKIP_SENSORS:
            continue
        if any(safe_id.startswith(p) for p in SKIP_PREFIXES):
            continue
        # Skip all sys sensors (clocks, utilization — duplicated by nvidia-smi/RAPL)
        if sensor_type == "sys":
            continue

        if sensor_type in type_map:
            metric, help_text = type_map[sensor_type]
            if metric not in seen_metrics:
                seen_metrics.add(metric)
                lines.append(f"# HELP {metric} {help_text}")
                lines.append(f"# TYPE {metric} gauge")
            lines.append(f'{metric}{{sensor="{safe_id}",label="{safe_label}"}} {value}')

            # Track PSU power if present
            if sensor_type == "pwr" and "psu" in safe_id.lower():
                psu_total_power = value

    if not lines:
        return "# aida64: no numeric sensors found\n"

    # If we got PSU total power, expose it as a dedicated metric
    if psu_total_power is not None:
        lines.append("# HELP psu_total_power_watts Total system power from Corsair HXi PSU")
        lines.append("# TYPE psu_total_power_watts gauge")
        lines.append(f"psu_total_power_watts {psu_total_power}")

    return "\n".join(lines) + "\n"


def _read_hwinfo_shm():
    """Read sensor data from HWiNFO64 shared memory (binary format).

    HWiNFO exposes sensors via a memory-mapped file named
    'Global\\HWiNFO_SENS_SM2'. Requires SensorsSM=1 in HWiNFO64.INI.

    Returns list of dicts: {type, sensor_index, id, label, unit, value}
    or None if unavailable.
    """
    import ctypes
    import struct
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.MapViewOfFile.restype = ctypes.c_void_p
    kernel32.MapViewOfFile.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t,
    ]
    kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel32.UnmapViewOfFile.restype = wintypes.BOOL

    handle = kernel32.OpenFileMappingW(0x0004, False, "Global\\HWiNFO_SENS_SM2")
    if not handle:
        return None

    buf = kernel32.MapViewOfFile(handle, 0x0004, 0, 0, 0)
    if not buf:
        kernel32.CloseHandle(handle)
        return None

    try:
        # Read header (first 40 bytes)
        header_raw = (ctypes.c_char * 48).from_address(buf)
        header = bytes(header_raw)

        sig = struct.unpack_from("<I", header, 0)[0]
        if sig != 0x53695748:  # "HWiS" (little-endian)
            return None

        # Header: sig(4) ver(4) rev(4) poll_time(8) sensor_off(4) sensor_sz(4)
        #         num_sensors(4) reading_off(4) reading_sz(4) num_readings(4)
        fields = struct.unpack_from("<IIIqIIIIII", header, 0)
        offset_reading = fields[7]
        size_reading = fields[8]
        num_readings = fields[9]

        # Reading type map: HWiNFO type enum → (prometheus_suffix, unit_label)
        TYPE_MAP = {
            1: "temperature_celsius",
            2: "voltage_volts",
            3: "fan_rpm",
            4: "current_amps",
            5: "power_watts",
            6: "clock_mhz",
            7: "usage_percent",
            8: "other",
        }

        readings = []
        for i in range(num_readings):
            offset = offset_reading + i * size_reading
            elem_raw = (ctypes.c_char * min(size_reading, 512)).from_address(buf + offset)
            elem = bytes(elem_raw)

            reading_type = struct.unpack_from("<I", elem, 0)[0]
            sensor_index = struct.unpack_from("<I", elem, 4)[0]
            reading_id = struct.unpack_from("<I", elem, 8)[0]

            label_orig = elem[12:140].split(b"\x00")[0].decode("utf-8", errors="ignore")
            label_user = elem[140:268].split(b"\x00")[0].decode("utf-8", errors="ignore")
            unit = elem[268:284].split(b"\x00")[0].decode("utf-8", errors="ignore")

            value = struct.unpack_from("<d", elem, 284)[0]

            label = label_user if label_user else label_orig
            type_name = TYPE_MAP.get(reading_type)
            if type_name and label:
                readings.append({
                    "type": type_name,
                    "sensor_index": sensor_index,
                    "id": reading_id,
                    "label": label,
                    "unit": unit,
                    "value": value,
                })

        return readings
    finally:
        kernel32.UnmapViewOfFile(buf)
        kernel32.CloseHandle(handle)


def get_hwinfo_metrics():
    """Read all sensors from HWiNFO64 shared memory.

    Exposes Corsair HXi PSU telemetry (via iCUE → HWiNFO) plus any other
    hardware sensors HWiNFO provides. Focuses on PSU data since GPU/CPU
    metrics come from nvidia-smi and RAPL.
    """
    if sys.platform != "win32":
        return ""

    try:
        readings = _read_hwinfo_shm()
    except Exception as e:
        return f"# hwinfo: error reading shared memory: {e}\n"

    if readings is None:
        return "# hwinfo: shared memory not available (enable in HWiNFO Settings > Shared Memory)\n"
    if not readings:
        return "# hwinfo: no sensor readings found\n"

    lines = []
    headers_emitted = set()

    for r in readings:
        metric_type = r["type"]
        metric_name = f"hwinfo_{metric_type}"
        safe_label = r["label"].replace('"', '\\"')
        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", r["label"]).lower()

        if metric_name not in headers_emitted:
            headers_emitted.add(metric_name)
            lines.append(f"# HELP {metric_name} HWiNFO64 {metric_type} sensors")
            lines.append(f"# TYPE {metric_name} gauge")

        lines.append(f'{metric_name}{{sensor="{safe_id}",label="{safe_label}"}} {r["value"]}')

    return "\n".join(lines) + "\n" if lines else ""


def get_liquidctl_psu_metrics():
    """Read Corsair HXi PSU metrics via liquidctl (cross-platform).

    Returns Prometheus-format metrics for PSU power, voltages, temps, fan.
    Works on both Windows and Linux — no AIDA64 dependency.
    """
    try:
        from liquidctl import find_liquidctl_devices
    except ImportError:
        return ""  # liquidctl not installed, skip silently

    lines = []
    headers_emitted = set()
    try:
        devices = find_liquidctl_devices()
        for dev in devices:
            # Only process Corsair HXi/RMi PSUs
            desc = str(dev.description).lower()
            if "hx" not in desc and "rm" not in desc:
                continue

            with dev.connect():
                status = dev.get_status()

            for key, value, unit in status:
                key_lower = key.lower().replace(" ", "_")
                safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", key_lower)

                if unit == "W":
                    if "power" not in headers_emitted:
                        lines.append("# HELP psu_power_watts Corsair HXi PSU power metrics (via liquidctl)")
                        lines.append("# TYPE psu_power_watts gauge")
                        headers_emitted.add("power")
                    lines.append(f'psu_power_watts{{sensor="{safe_key}",label="{key}"}} {value}')
                elif unit == "V":
                    if "voltage" not in headers_emitted:
                        lines.append("# HELP psu_voltage_volts Corsair HXi PSU voltage metrics (via liquidctl)")
                        lines.append("# TYPE psu_voltage_volts gauge")
                        headers_emitted.add("voltage")
                    lines.append(f'psu_voltage_volts{{sensor="{safe_key}",label="{key}"}} {value}')
                elif unit == "A":
                    if "current" not in headers_emitted:
                        lines.append("# HELP psu_current_amps Corsair HXi PSU current metrics (via liquidctl)")
                        lines.append("# TYPE psu_current_amps gauge")
                        headers_emitted.add("current")
                    lines.append(f'psu_current_amps{{sensor="{safe_key}",label="{key}"}} {value}')
                elif unit == "°C":
                    if "temp" not in headers_emitted:
                        lines.append("# HELP psu_temperature_celsius Corsair HXi PSU temperature (via liquidctl)")
                        lines.append("# TYPE psu_temperature_celsius gauge")
                        headers_emitted.add("temp")
                    lines.append(f'psu_temperature_celsius{{sensor="{safe_key}",label="{key}"}} {value}')
                elif unit == "rpm":
                    if "fan" not in headers_emitted:
                        lines.append("# HELP psu_fan_rpm Corsair HXi PSU fan speed (via liquidctl)")
                        lines.append("# TYPE psu_fan_rpm gauge")
                        headers_emitted.add("fan")
                    lines.append(f'psu_fan_rpm{{sensor="{safe_key}",label="{key}"}} {value}')

    except Exception:
        pass  # Non-critical — other sources provide data

    return "\n".join(lines) + "\n" if lines else ""


def get_lm_sensors_metrics():
    """Read hardware sensors via lm-sensors on Linux.

    Returns Prometheus-format metrics for CPU temps, voltages, fan speeds.
    Only runs on Linux — returns empty string on other platforms.
    """
    if sys.platform != "linux":
        return ""

    try:
        import json as _json
        result = subprocess.run(
            ["sensors", "-j"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return ""

        data = _json.loads(result.stdout)
        lines = []
        header_added = {"temp": False, "fan": False, "volt": False}

        for chip_name, chip_data in data.items():
            if not isinstance(chip_data, dict):
                continue
            safe_chip = re.sub(r"[^a-zA-Z0-9_]", "_", chip_name)

            for sensor_name, sensor_data in chip_data.items():
                if not isinstance(sensor_data, dict):
                    continue

                for key, value in sensor_data.items():
                    if not isinstance(value, (int, float)):
                        continue

                    safe_sensor = re.sub(r"[^a-zA-Z0-9_]", "_", sensor_name)

                    if "temp" in key and "input" in key:
                        if not header_added["temp"]:
                            lines.append("# HELP lm_sensors_temperature_celsius Temperature from lm-sensors")
                            lines.append("# TYPE lm_sensors_temperature_celsius gauge")
                            header_added["temp"] = True
                        lines.append(f'lm_sensors_temperature_celsius{{chip="{safe_chip}",sensor="{safe_sensor}"}} {value}')
                    elif "fan" in key and "input" in key:
                        if not header_added["fan"]:
                            lines.append("# HELP lm_sensors_fan_rpm Fan speed from lm-sensors")
                            lines.append("# TYPE lm_sensors_fan_rpm gauge")
                            header_added["fan"] = True
                        lines.append(f'lm_sensors_fan_rpm{{chip="{safe_chip}",sensor="{safe_sensor}"}} {value}')
                    elif "in" in key and "input" in key:
                        if not header_added["volt"]:
                            lines.append("# HELP lm_sensors_voltage_volts Voltage from lm-sensors")
                            lines.append("# TYPE lm_sensors_voltage_volts gauge")
                            header_added["volt"] = True
                        lines.append(f'lm_sensors_voltage_volts{{chip="{safe_chip}",sensor="{safe_sensor}"}} {value}')

        return "\n".join(lines) + "\n" if lines else ""
    except Exception:
        return ""


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            gpu = get_gpu_metrics()
            apps = get_compute_apps_metrics()
            cpu = get_cpu_power_metrics()
            aida = get_aida64_metrics()
            hwinfo = get_hwinfo_metrics()
            lm = get_lm_sensors_metrics()
            total = get_total_power_metrics(gpu, cpu)
            # Combine all sources — AIDA64/HWiNFO/lm-sensors complement each other
            # liquidctl removed: fights iCUE/AIDA64 for Corsair USB; HWiNFO covers PSU via shared memory
            body = (gpu + apps + cpu + aida + hwinfo + lm + total).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            body = b"GPU + system power exporter. /metrics for Prometheus metrics.\n"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Silence request logging


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), MetricsHandler)
    print(f"nvidia-smi exporter listening on :{PORT}/metrics")
    server.serve_forever()
