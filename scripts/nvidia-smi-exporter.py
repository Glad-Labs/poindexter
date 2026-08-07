"""Prometheus exporter for NVIDIA GPU + system power + AIDA64 + liquidctl metrics.
Serves on port 9835. Scraped by the local Prometheus container every 15s.
(Was scraped by Grafana Alloy until 2026-05-03 when Cloud was retired and
Alloy with it; Prometheus took over the direct scrape.)

Cross-platform: works on Windows (AIDA64 + Energy Meter) and Linux (lm-sensors + liquidctl).

Metrics:
  - nvidia_gpu_* — GPU utilization, memory, temp, power, clocks, fan
  - system_cpu_power_watts — Total CPU package power (AMD RAPL via Energy Meter)
  - system_cpu_core_power_watts — Per-core power draw
  - system_total_power_watts — CPU + GPU combined power estimate
  - aida64_* — All AIDA64 sensors (when shared memory is enabled, Windows)
  - psu_* — Corsair HXi PSU metrics (via liquidctl or AIDA64)
  - lm_sensors_* — Linux hardware sensors (temps, voltages, fans)

Reliability:
  - Metrics are gathered by a background thread into a cached snapshot every
    ``_COLLECT_INTERVAL_SEC``; every ``/metrics`` scrape serves that snapshot in
    O(1), so a slow nvidia-smi/AIDA read can never become a scraper's timeout.
    (Before this, per-request collection intermittently took 2.5-6.7s+ and blew
    the brain's 3s electricity-cost scrape, paging a false 150W-floor alert
    ~15×/day — see brain/psu_power.py. Scrapes now get data at most one interval
    stale, which is fine for power/thermal gauges.)
  - ThreadingHTTPServer so concurrent scrapes never queue behind each other.
  - The #319 nvidia-smi watchdog (which killed the process on repeated nvidia-smi
    timeouts) is obsolete now that collection is off the request path: a hung
    nvidia-smi can't block a scrape, it only makes GPU metrics briefly stale, so
    the collector swallows the watchdog's SystemExit and keeps serving (see
    ``_collector_loop``). Genuine crashes are still caught by the host service
    manager's restart policy.
"""
import logging
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Hide console windows when spawning subprocesses on Windows
_SUBPROCESS_KWARGS = {}
if sys.platform == "win32":
    _si = subprocess.STARTUPINFO()
    _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _si.wShowWindow = 0  # SW_HIDE
    _SUBPROCESS_KWARGS = {"startupinfo": _si, "creationflags": subprocess.CREATE_NO_WINDOW}

PORT = 9835

# Watchdog state — if nvidia-smi keeps misbehaving, exit so docker restarts us.
# Bug #319: HTTP socket can stay LISTENING but stop accepting connections,
# typically because subprocess.run() is silently wedged on a stuck nvidia-smi.
_NVIDIA_SMI_TIMEOUT_SEC = 5
_WATCHDOG_SLOW_THRESHOLD_SEC = 2 * _NVIDIA_SMI_TIMEOUT_SEC  # 10s
_MAX_CONSECUTIVE_TIMEOUTS = 3
_consecutive_timeouts = 0

def _trip_watchdog(reason: str) -> None:
    """Increment consecutive-timeout counter and exit if threshold reached."""
    global _consecutive_timeouts
    _consecutive_timeouts += 1
    logger.warning(
        "nvidia-smi watchdog: %s (consecutive=%d/%d)",
        reason, _consecutive_timeouts, _MAX_CONSECUTIVE_TIMEOUTS,
    )
    if _consecutive_timeouts >= _MAX_CONSECUTIVE_TIMEOUTS:
        logger.error(
            "nvidia-smi watchdog tripped after %d consecutive bad scrapes — "
            "exiting so docker restart-policy brings us back",
            _consecutive_timeouts,
        )
        sys.exit(1)


# nvidia-smi --query-gpu order, AFTER the leading `index` field. Each CSV row is
# therefore `index` + these 10 metric fields.
_GPU_METRIC_SPECS = [
    ("nvidia_gpu_utilization_percent", "GPU utilization percentage"),
    ("nvidia_gpu_memory_utilization_percent", "GPU memory utilization percentage"),
    ("nvidia_gpu_memory_used_mib", "GPU memory used in MiB"),
    ("nvidia_gpu_memory_total_mib", "GPU memory total in MiB"),
    ("nvidia_gpu_temperature_celsius", "GPU temperature in Celsius"),
    ("nvidia_gpu_power_draw_watts", "GPU power draw in watts"),
    ("nvidia_gpu_power_limit_watts", "GPU power limit in watts"),
    ("nvidia_gpu_fan_speed_percent", "GPU fan speed percentage"),
    ("nvidia_gpu_clock_graphics_mhz", "GPU graphics clock in MHz"),
    ("nvidia_gpu_clock_memory_mhz", "GPU memory clock in MHz"),
]
_GPU_ROW_FIELDS = len(_GPU_METRIC_SPECS) + 1  # + leading index

# Cardinality canary. NVIDIA device nodes are injected into a container at
# CREATE time, so a card added (or re-enumerated) after `docker create` is
# invisible until the container is recreated — the per-GPU series simply stop
# at gpu="0" with no error anywhere. That failure hid an entire RTX 3090 for
# 7+ days (2026-07-26). Exporting the count makes the absence assertable.
_COUNT_HEADER = (
    "# HELP nvidia_gpu_count Number of GPUs nvidia-smi enumerated this scrape\n"
    "# TYPE nvidia_gpu_count gauge\n"
)


def _format_gpu_rows(stdout: str) -> str:
    """Turn nvidia-smi CSV (one row per GPU) into Prometheus exposition text.

    Pure function (no subprocess) so it's directly unit-testable. Parses
    row-by-row and labels every series with nvidia-smi's own ``index`` — so a
    multi-GPU rig yields one series per card. A malformed row is logged and
    skipped rather than blanking the whole block: telemetry degrades
    gracefully (one card glitching shouldn't take down monitoring for both),
    while genuinely-stuck nvidia-smi is still caught by the watchdog upstream.

    The pre-multi-GPU version unpacked a single 10-tuple and crashed with
    "too many values to unpack" the instant a second GPU appeared.
    """
    rows = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        values = [v.strip() for v in line.split(",")]
        if len(values) != _GPU_ROW_FIELDS:
            logger.warning(
                "nvidia-smi row has %d fields, expected %d — skipping: %r",
                len(values), _GPU_ROW_FIELDS, line,
            )
            continue
        rows.append(values)

    if not rows:
        # Still emit the count so a total enumeration failure is a VISIBLE 0
        # rather than an absent series (absent = "no data", which alerts on
        # `< expected` cannot see — the 2026-07-26 silent-missing-GPU case).
        return _COUNT_HEADER + "nvidia_gpu_count 0\n"

    lines = [_COUNT_HEADER.rstrip("\n"), f"nvidia_gpu_count {len(rows)}"]
    for field_idx, (metric, help_text) in enumerate(_GPU_METRIC_SPECS):
        lines.append(f"# HELP {metric} {help_text}")
        lines.append(f"# TYPE {metric} gauge")
        for values in rows:
            gpu_index = values[0]
            lines.append(f'{metric}{{gpu="{gpu_index}"}} {values[field_idx + 1]}')

    return "\n".join(lines) + "\n"


def get_gpu_metrics():
    """Query nvidia-smi and return Prometheus-format metrics (one series per GPU)."""
    global _consecutive_timeouts
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,clocks.current.graphics,clocks.current.memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_NVIDIA_SMI_TIMEOUT_SEC,
            **_SUBPROCESS_KWARGS,
        )
        elapsed = time.monotonic() - start
        if elapsed > _WATCHDOG_SLOW_THRESHOLD_SEC:
            _trip_watchdog(f"nvidia-smi took {elapsed:.1f}s (threshold {_WATCHDOG_SLOW_THRESHOLD_SEC}s)")
        else:
            _consecutive_timeouts = 0

        if result.returncode != 0:
            return "# nvidia-smi failed\n"

        return _format_gpu_rows(result.stdout)
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        _trip_watchdog(f"nvidia-smi TimeoutExpired after {elapsed:.1f}s")
        return "# nvidia-smi timeout\n"
    except Exception as e:
        return f"# error: {e}\n"


_PROCESS_METRIC_HEADER = (
    "# HELP nvidia_gpu_process_memory_mib Per-process GPU memory (compute apps)\n"
    "# TYPE nvidia_gpu_process_memory_mib gauge\n"
)


def _format_process_rows(uuid_map_stdout: str, apps_stdout: str) -> str:
    """Join --query-compute-apps rows to gpu indices → per-process series.

    ``--query-compute-apps`` labels rows by GPU **UUID** (it has no index
    field), so a uuid→index map from ``--query-gpu=index,uuid`` joins them
    back onto the ``gpu="N"`` label convention every other nvidia series uses.
    Pure function (no subprocess) so it's directly unit-testable, mirroring
    ``_format_gpu_rows``. A malformed or unmappable row is logged and skipped
    rather than blanking the block.

    The ``process`` label is the executable basename (full paths differ
    between host and container views of the same process and would fragment
    the series). Consumed by ``services/gpu_registry.evictable_ollama_gb`` —
    the admission fit gate's per-card eviction credit (poindexter#914 P1),
    which this per-card metric feeds precisely BECAUSE Ollama's own ``/api/ps``
    size is a cross-card total.
    """
    uuid_to_index = {}
    for line in uuid_map_stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1]:
            uuid_to_index[parts[1]] = parts[0]

    lines = [_PROCESS_METRIC_HEADER.rstrip("\n")]
    for line in apps_stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            logger.warning(
                "compute-apps row has %d fields, expected 4 — skipping: %r",
                len(parts), line,
            )
            continue
        gpu_uuid, pid, name, mem = parts
        gpu_index = uuid_to_index.get(gpu_uuid)
        if gpu_index is None:
            logger.warning("compute-apps row has unknown GPU uuid %r — skipping", gpu_uuid)
            continue
        try:
            mem_val = float(mem)
        except ValueError:
            continue
        # process_name is the FULL COMMAND LINE on current Linux drivers
        # ("/usr/lib/claude-desktop/claude-desktop --type=gpu-process
        # --gpu-preferences=<base64> …"), not just an executable path. Naive
        # basename-of-last-slash over that produced garbage labels ('Claude
        # --gpu-preferences=UAAA…', hundreds of chars, unbounded cardinality —
        # verified live 2026-07-26). Strip the flag tail, basename the
        # executable, then drop any subcommand token ("ollama runner" →
        # "ollama").
        head = name.split(" --", 1)[0]
        base = head.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        tokens = base.split()
        process = (tokens[0] if tokens else "unknown") or "unknown"
        process = process.replace('"', "_")
        lines.append(
            f'nvidia_gpu_process_memory_mib{{gpu="{gpu_index}",pid="{pid}",'
            f'process="{process}"}} {mem_val}'
        )

    return "\n".join(lines) + "\n"


def get_gpu_process_metrics():
    """Per-process GPU memory series via nvidia-smi --query-compute-apps.

    Secondary telemetry: failures return a comment line and never trip the
    #319 watchdog (that guards the primary gauge query — a broken compute-apps
    read shouldn't restart the whole exporter while gauges still work).
    """
    try:
        uuid_map = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_NVIDIA_SMI_TIMEOUT_SEC,
            **_SUBPROCESS_KWARGS,
        )
        apps = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_NVIDIA_SMI_TIMEOUT_SEC,
            **_SUBPROCESS_KWARGS,
        )
        if uuid_map.returncode != 0 or apps.returncode != 0:
            return "# nvidia-smi compute-apps query failed\n"
        return _format_process_rows(uuid_map.stdout, apps.stdout)
    except subprocess.TimeoutExpired:
        return "# nvidia-smi compute-apps timeout\n"
    except Exception as e:
        return f"# compute-apps error: {e}\n"


def get_cpu_power_metrics():
    """Read CPU power from Windows Energy Meter performance counters (AMD RAPL).
    Returns Prometheus-format metrics string.
    """
    if sys.platform != "win32":
        return ""
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
            # AMD Energy Meter instance names look like:
            #   rapl_package0_pkg     → package total
            #   rapl_package0_core0_core → per-core
            # The old logic checked ``"pkg" in name`` which matched both
            # (because "pkg" is also a substring of "package"), so each
            # core sample overwrote pkg_power. The values landed
            # accidentally-correct on systems where the pkg entry came
            # last alphabetically, but on other locales the metric
            # silently held a per-core value. Match the suffix
            # explicitly instead.
            elif name.endswith("_pkg"):
                pkg_power = watts
            elif name.endswith("_core"):
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

        # Per-core power (capture-everything, 2026-06-03): the old "drop to reduce
        # series count" rationale was the Grafana Cloud free-tier cardinality cap,
        # retired 2026-05-03. Local Prometheus has no such limit, so expose the
        # per-core RAPL series too. (core_lines was previously built but discarded.)
        if core_lines:
            lines.append("# HELP system_cpu_core_power_watts Per-core power draw (AMD RAPL)")
            lines.append("# TYPE system_cpu_core_power_watts gauge")
            lines.extend(core_lines)

        return "\n".join(lines) + "\n"
    except Exception as e:
        return f"# cpu power error: {e}\n"


def get_total_power_metrics(gpu_text, cpu_text):
    """Calculate combined system power estimate from GPU + CPU metrics."""
    gpu_watts = 0.0
    cpu_watts = 0.0
    try:
        # Sum power across every GPU (one nvidia_gpu_power_draw_watts series per
        # card). The old code broke after the first match and silently ignored
        # additional GPUs, undercounting the system-power estimate.
        for line in gpu_text.split("\n"):
            if line.startswith("nvidia_gpu_power_draw_watts{"):
                gpu_watts += float(line.split()[-1])
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

    # Capture-everything posture (2026-06-03): the previous filtering existed to
    # stay under the Grafana Cloud free-tier cardinality cap, retired 2026-05-03.
    # Local Prometheus has no such limit, so export every numeric sensor except
    # the literal calendar date/time fields, which aren't telemetry.
    SKIP_SENSORS = {
        "syear", "smonth", "sdayofmonth", "sdow", "sweekofyear",
        "shour12", "shour24", "smin", "ssec",  # date/time — not telemetry
    }

    for match in pattern.finditer(raw):
        sensor_type, sensor_id, label, val_str = match.groups()

        try:
            value = float(val_str)
        except (ValueError, TypeError):
            continue

        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", sensor_id).lower()
        safe_label = label.replace('"', '\\"')

        # Skip only the literal date/time fields; everything else is captured.
        if sensor_id.lower() in SKIP_SENSORS:
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

    # Canonical wall-power metric for the brain's electricity-cost calc.
    # HWiNFO reads the Corsair HXi PSU natively (via iCUE). "PSU Power In (est)"
    # is the AC draw from the wall — what the utility bills — so it's the right
    # source for $/kWh; fall back to Out / sum if the input estimate is absent.
    # brain_daemon.log_electricity_cost greps for psu_total_power_watts; without
    # this it silently falls back to the CPU+GPU+50W software estimate. (HWiNFO is
    # the canonical PSU source — the AIDA64 path's psu_total_power_watts only fires
    # if AIDA exposes a Corsair PSU sensor, which it doesn't on this hardware.)
    _psu_by_label = {
        r["label"]: r["value"] for r in readings
        if r["label"] in ("PSU Power In (est)", "PSU Power Out", "PSU Power (sum)")
    }
    for _psu_label in ("PSU Power In (est)", "PSU Power Out", "PSU Power (sum)"):
        if _psu_label in _psu_by_label:
            lines.append("# HELP psu_total_power_watts Wall power draw from the Corsair HXi PSU (HWiNFO)")
            lines.append("# TYPE psu_total_power_watts gauge")
            lines.append(f"psu_total_power_watts {_psu_by_label[_psu_label]}")
            break

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


def _resolve_shelly_url() -> str:
    """Shelly plug base URL: ``SHELLY_PSU_URL`` env var first, then the
    ``shelly_psu_url`` key in ~/.poindexter/bootstrap.toml, else "".

    Two launch modes need two sources (Pop!_OS migration):
    - **Containerized** (the ``gpu-exporter`` service): no bootstrap.toml
      inside the container, so compose passes ``SHELLY_PSU_URL`` through —
      start-stack.sh already exports every bootstrap key as an uppercase
      env var, so the value still has a single home in bootstrap.toml.
    - **Host script** (the legacy Windows scheduled task): no inherited
      user env, so it falls back to reading bootstrap.toml directly.
    """
    import os

    env_url = os.environ.get("SHELLY_PSU_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    return _read_shelly_url_from_bootstrap()


def _read_shelly_url_from_bootstrap() -> str:
    """Shelly plug base URL from ~/.poindexter/bootstrap.toml key
    ``shelly_psu_url`` (e.g. ``http://192.168.1.50``), or "" if absent.

    The exporter is a stdlib-only HOST script with no DB access, and the
    scheduled task that launches it does not inherit the user env — so the
    plug's LAN address lives in bootstrap.toml, the one sanctioned on-disk
    config (alongside database_url). Unset/unreadable → "" (a no-op: the
    software estimate covers wall power until the plug is configured).
    """
    try:
        import tomllib
        from pathlib import Path

        path = Path.home() / ".poindexter" / "bootstrap.toml"
        if not path.is_file():
            return ""
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return str(data.get("shelly_psu_url", "") or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001 — defensive; never break the scrape
        return ""


def _default_fetch_json(url: str, timeout: float = 3.0):
    """GET ``url`` and parse JSON. Isolated so tests can inject a fake."""
    import json
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_shelly_psu_metrics(base_url: str | None = None, *, _fetch=None) -> str:
    """True wall power from a Shelly Gen2+ smart plug's LOCAL RPC API.

    The plug meters actual AC draw at the OUTLET — physically independent of
    the HX1500i's USB (which iCUE owns), so it never fights iCUE/HWiNFO for the
    HID the way a second USB reader does. Emits ``psu_total_power_watts`` — the
    canonical wall-power metric ``brain.psu_power`` and the Grafana Hardware &
    Power board already consume — from the plug's ``apower`` reading.

    Config: ``SHELLY_PSU_URL`` env var (containerized gpu-exporter) or
    ``shelly_psu_url`` in bootstrap.toml (host script) — see
    ``_resolve_shelly_url``. Unset → "" (no-op; the software estimate covers
    it). Any error → "" so a plug reboot / network blip never breaks the
    whole /metrics scrape.
    """
    if base_url is None:
        base_url = _resolve_shelly_url()
    if not base_url:
        return ""

    url = f"{base_url}/rpc/Switch.GetStatus?id=0"
    fetch = _fetch or _default_fetch_json
    try:
        data = fetch(url)
    except Exception as exc:  # noqa: BLE001 — a plug hiccup must not fail the scrape
        logger.warning("shelly: poll failed (%s: %s)", type(exc).__name__, exc)
        return ""

    apower = data.get("apower") if isinstance(data, dict) else None
    if apower is None:
        return "# shelly: reachable but no 'apower' in Switch.GetStatus response\n"

    lines = [
        "# HELP psu_total_power_watts Wall power draw metered at the outlet (Shelly smart plug)",
        "# TYPE psu_total_power_watts gauge",
        f"psu_total_power_watts {float(apower):.2f}",
    ]
    voltage = data.get("voltage")
    if voltage is not None:
        lines += [
            "# HELP psu_line_voltage_volts Mains line voltage (Shelly smart plug)",
            "# TYPE psu_line_voltage_volts gauge",
            f"psu_line_voltage_volts {float(voltage):.1f}",
        ]
    current = data.get("current")
    if current is not None:
        lines += [
            "# HELP psu_line_current_amps Mains current draw (Shelly smart plug)",
            "# TYPE psu_line_current_amps gauge",
            f"psu_line_current_amps {float(current):.3f}",
        ]
    return "\n".join(lines) + "\n"


def _dedupe_psu_metric(text: str) -> str:
    """Keep only the first ``psu_total_power_watts`` block (HELP/TYPE/sample).

    More than one source can emit this metric — the Shelly outlet meter
    (preferred), HWiNFO's HXi read, AIDA64's. Concatenated verbatim they would
    produce duplicate ``# HELP``/``# TYPE`` lines, which is malformed exposition
    that breaks the Prometheus parser. The handler orders sources by priority
    (Shelly first), so first-block-wins == highest-priority-wins.
    """
    metric = "psu_total_power_watts"
    seen_sample = False
    out = []
    for line in text.split("\n"):
        s = line.strip()
        is_meta = s.startswith(f"# HELP {metric} ") or s.startswith(f"# TYPE {metric} ")
        is_sample = s.startswith(f"{metric} ")
        if is_meta or is_sample:
            if seen_sample:
                continue
            if is_sample:
                seen_sample = True
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# ASUS Astral per-pin 12V-2x6 connector telemetry
#
# ROG Astral cards carry an ITE IT8915FN that reports per-pin voltage and
# current for all six 12V pins of the GPU power connector — the data that
# predicts the connector-melt failure mode (current concentrating on a few
# pins while the total looks normal). The chip answers at 0x2B on one of the
# card's NVIDIA I2C buses; the block at register 0x80 is 24 bytes, 4 per pin
# (mV then mA, big-endian), pins in reverse order. Read protocol per the
# open-source readers (eugeneoh04/vhpwr-guard, humza-khalid/12vhpwr-guard):
# SMBus byte-data reads — the Astral does not serve a combined rdwr block.
# ---------------------------------------------------------------------------

_ASTRAL_ADDR = 0x2B
_ASTRAL_REG = 0x80
_ASTRAL_PINS = 6
# Cached bus number. I2C bus numbering ROTATES across boots (same trap as
# the corsair-psu hwmon chip label), so any failure resets the cache and the
# next collector cycle re-scans.
_astral_bus: int | None = None
_astral_warned = False


def _astral_read_block(bus: int) -> bytes:
    """24-byte SMBus byte-data read from the IT8915FN.

    Imports are local so the module still imports on Windows hosts (fcntl is
    POSIX-only); on such hosts the auto-detect simply never finds a bus.
    """
    import ctypes
    import fcntl
    import os

    I2C_SLAVE = 0x0703
    I2C_SMBUS = 0x0720
    I2C_SMBUS_READ = 1
    I2C_SMBUS_BYTE_DATA = 2

    class _SmbusData(ctypes.Union):
        _fields_ = [
            ("byte", ctypes.c_uint8),
            ("word", ctypes.c_uint16),
            ("block", ctypes.c_uint8 * 34),
        ]

    class _SmbusIoctl(ctypes.Structure):
        _fields_ = [
            ("read_write", ctypes.c_uint8),
            ("command", ctypes.c_uint8),
            ("size", ctypes.c_uint32),
            ("data", ctypes.POINTER(_SmbusData)),
        ]

    fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
    try:
        fcntl.ioctl(fd, I2C_SLAVE, _ASTRAL_ADDR)
        raw = bytearray()
        for i in range(_ASTRAL_PINS * 4):
            data = _SmbusData()
            args = _SmbusIoctl(
                read_write=I2C_SMBUS_READ,
                command=_ASTRAL_REG + i,
                size=I2C_SMBUS_BYTE_DATA,
                data=ctypes.pointer(data),
            )
            fcntl.ioctl(fd, I2C_SMBUS, args)
            raw.append(data.byte)
        return bytes(raw)
    finally:
        os.close(fd)


def _astral_decode(raw: bytes) -> list[tuple[int, float, float]]:
    """(pin, volts, amps) triples, pin-sorted. 4 bytes per pin — mV then mA,
    both big-endian — and the chip returns pins in reverse order."""
    pins = []
    for i in range(_ASTRAL_PINS):
        o = i * 4
        mv = (raw[o] << 8) | raw[o + 1]
        ma = (raw[o + 2] << 8) | raw[o + 3]
        pins.append(((_ASTRAL_PINS - 1) - i, mv / 1000.0, ma / 1000.0))
    return sorted(pins)


def _astral_plausible(pins: list[tuple[int, float, float]]) -> bool:
    """At least 5 pins on a real ~12V rail under 25A — rejects any stray
    device that happens to ACK address 0x2B on some other bus."""
    return sum(1 for _p, v, a in pins if 11.0 <= v <= 13.5 and 0.0 <= a < 25.0) >= 5


def _astral_find_bus() -> int | None:
    """Probe every NVIDIA I2C adapter for a plausible IT8915FN, read-only."""
    import glob

    for name_path in sorted(glob.glob("/sys/bus/i2c/devices/i2c-*/name")):
        try:
            with open(name_path) as fh:
                if "nvidia" not in fh.read().lower():
                    continue
            bus = int(name_path.split("i2c-")[-1].split("/")[0])
            if _astral_plausible(_astral_decode(_astral_read_block(bus))):
                return bus
        except (OSError, ValueError):
            continue
    return None


def get_astral_pin_metrics() -> str:
    """Per-pin ``gpu_12vhpwr_pin_volts`` / ``gpu_12vhpwr_pin_current_amps``
    from an ASUS Astral card, labelled ``pin="0".."5"``.

    Zero-config: auto-detects the chip and emits nothing when the hardware
    (or /dev/i2c access) is absent — absent series keep the GpuPowerPin*
    alert rules naturally inert, same posture as the profile-gated NUT rules.
    Any error degrades to empty output so a flaky bus can never break the
    whole /metrics snapshot (same contract as the Shelly poll).
    """
    global _astral_bus, _astral_warned
    try:
        if _astral_bus is None:
            _astral_bus = _astral_find_bus()
            if _astral_bus is None:
                return ""
            logger.info("astral: per-pin connector sensor found on i2c-%d", _astral_bus)
        pins = _astral_decode(_astral_read_block(_astral_bus))
        if not _astral_plausible(pins):
            _astral_bus = None
            return ""
    except Exception as exc:  # noqa: BLE001 — a bus hiccup must not fail the scrape
        if not _astral_warned:
            logger.warning("astral: pin read failed (%s: %s)", type(exc).__name__, exc)
            _astral_warned = True
        _astral_bus = None
        return ""
    lines = [
        "# HELP gpu_12vhpwr_pin_volts Per-pin voltage at the GPU 12V-2x6 connector (ASUS Astral IT8915FN)",
        "# TYPE gpu_12vhpwr_pin_volts gauge",
    ]
    for pin, volts, _amps in pins:
        lines.append(f'gpu_12vhpwr_pin_volts{{pin="{pin}"}} {volts:.3f}')
    lines += [
        "# HELP gpu_12vhpwr_pin_current_amps Per-pin current at the GPU 12V-2x6 connector (ASUS Astral IT8915FN)",
        "# TYPE gpu_12vhpwr_pin_current_amps gauge",
    ]
    for pin, _volts, amps in pins:
        lines.append(f'gpu_12vhpwr_pin_current_amps{{pin="{pin}"}} {amps:.3f}')
    return "\n".join(lines) + "\n"


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


# ---------------------------------------------------------------------------
# Background collector — /metrics serves a cached snapshot refreshed off the
# request path (2026-07-12). Per-request collection intermittently took
# 2.5-6.7s+ (Prometheus saw multi-minute wedges), blowing the brain's 3s
# electricity-cost scrape timeout: it then lost BOTH the Shelly reading and the
# software estimate at once, fell to the 150W floor, and paged ~15×/day on
# transient blips. Collecting off the request path makes a scrape O(1), so a
# slow producer can never become a scraper timeout.
_COLLECT_INTERVAL_SEC = 10

_snapshot_lock = threading.Lock()
_snapshot_body: bytes = b"# nvidia-smi exporter warming up\n"


def _collect_all_metrics() -> bytes:
    """Gather every source once and return deduped Prometheus exposition bytes.

    Shelly (outlet meter) is concatenated first so it wins ``psu_total_power_watts``
    de-duplication (highest-priority source). AIDA64/HWiNFO/lm-sensors complement
    each other. liquidctl was removed: it fought iCUE/AIDA64 for the Corsair USB.
    Runs in the background collector thread, never on the request path.
    """
    gpu = get_gpu_metrics()
    procs = get_gpu_process_metrics()
    cpu = get_cpu_power_metrics()
    shelly = get_shelly_psu_metrics()
    astral = get_astral_pin_metrics()
    aida = get_aida64_metrics()
    hwinfo = get_hwinfo_metrics()
    lm = get_lm_sensors_metrics()
    total = get_total_power_metrics(gpu, cpu)
    combined = gpu + procs + cpu + shelly + astral + aida + hwinfo + lm + total
    return _dedupe_psu_metric(combined).encode()


def _refresh_snapshot(*, _collect=_collect_all_metrics) -> None:
    """Collect once and atomically swap the cached snapshot."""
    global _snapshot_body
    body = _collect()
    with _snapshot_lock:
        _snapshot_body = body


def _read_snapshot() -> bytes:
    """Return the cached snapshot bytes — what every scrape serves."""
    with _snapshot_lock:
        return _snapshot_body


def _collector_loop(
    interval: float = _COLLECT_INTERVAL_SEC,
    *,
    _refresh=_refresh_snapshot,
    _sleep=time.sleep,
    _stop=None,
) -> None:
    """Refresh the cached snapshot forever (background thread).

    Resilient by design: a per-cycle collector error is logged and retried next
    cycle — one bad read must not kill the refresh thread and freeze the
    snapshot. The #319 nvidia-smi watchdog raises ``SystemExit`` after repeated
    nvidia-smi timeouts; that watchdog existed to kill a process whose *request
    thread* was wedged on a stuck nvidia-smi, but collection is off the request
    path now — a hung nvidia-smi only makes GPU metrics briefly stale while
    psu/estimate/AIDA keep serving from the cached snapshot. Exiting there
    dropped ALL metrics over a transient GPU-busy spell (2026-07-12), so the
    watchdog's exit is swallowed and the loop keeps collecting.
    """
    while _stop is None or not _stop.is_set():
        try:
            _refresh()
        except SystemExit:
            logger.warning(
                "collector: nvidia-smi watchdog tripped (repeated nvidia-smi "
                "timeouts) — ignoring; GPU metrics stale this cycle, other "
                "sources unaffected"
            )
        except Exception as exc:  # noqa: BLE001 — one bad cycle must not stop refresh
            logger.warning(
                "collector cycle failed (%s: %s) — snapshot stale this cycle",
                type(exc).__name__, exc,
            )
        if _stop is not None:
            _stop.wait(interval)
        else:
            _sleep(interval)


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            # Serve the cached snapshot — collection happens off-request in
            # _collector_loop, so this stays O(1) regardless of producer latency.
            body = _read_snapshot()
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


class _SingletonExporter(ThreadingHTTPServer):
    # ThreadingHTTPServer (issue #319): the prior single-threaded HTTPServer
    # would silently stop accepting connections when a single scrape wedged.
    #
    # allow_reuse_address=False (2026-06-03): Python's HTTPServer defaults this
    # to True -> SO_REUSEADDR, which on Windows lets a SECOND host exporter
    # silently co-bind 0.0.0.0:9835. Scrapes then round-robin between the two
    # and a stale instance (old code, no HWiNFO/AIDA sensors) can answer half of
    # them. With reuse disabled a duplicate launch fails loudly at bind() instead
    # of stacking, so whatever is running is always the authoritative instance.
    allow_reuse_address = False
    daemon_threads = True


if __name__ == "__main__":
    try:
        server = _SingletonExporter(("0.0.0.0", PORT), MetricsHandler)
    except OSError as exc:
        logger.error(
            "nvidia-smi exporter failed to bind :%d (%s) — another instance is "
            "already running. Refusing to start a duplicate; exiting.",
            PORT, exc,
        )
        sys.exit(1)
    # Prime the cache synchronously so the first scrape gets real data (not the
    # "warming up" placeholder), then refresh it off the request path forever.
    _refresh_snapshot()
    threading.Thread(
        target=_collector_loop, name="metrics-collector", daemon=True
    ).start()
    logger.info(
        "nvidia-smi exporter listening on :%d/metrics (background collector "
        "refreshing every %ds)", PORT, _COLLECT_INTERVAL_SEC,
    )
    server.serve_forever()
