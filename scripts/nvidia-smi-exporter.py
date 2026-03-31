"""Prometheus exporter for NVIDIA GPU + system power + AIDA64 sensor metrics.
Serves on port 9835. Scraped by Grafana Alloy every 15s.

Metrics:
  - nvidia_gpu_* — GPU utilization, memory, temp, power, clocks, fan
  - system_cpu_power_watts — Total CPU package power (AMD RAPL via Energy Meter)
  - system_cpu_core_power_watts — Per-core power draw
  - system_total_power_watts — CPU + GPU combined power estimate
  - aida64_* — All AIDA64 sensors (when shared memory is enabled)
  - psu_* — Corsair HXi PSU power/voltage/current/temp (via AIDA64)
"""
import mmap
import re
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 9835

def get_gpu_metrics():
    """Query nvidia-smi and return Prometheus-format metrics."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,clocks.current.graphics,clocks.current.memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
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
            capture_output=True, text=True, timeout=10
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

        if core_lines:
            lines.append("# HELP system_cpu_core_power_watts Per-core CPU power draw (AMD RAPL)")
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

    for match in pattern.finditer(raw):
        sensor_type, sensor_id, label, val_str = match.groups()

        try:
            value = float(val_str)
        except (ValueError, TypeError):
            continue

        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", sensor_id).lower()
        safe_label = label.replace('"', '\\"')

        if sensor_type == "sys":
            # System sensors — selective export of numeric values
            # Skip date/time/string-only values
            metric = "aida64_system"
            if metric not in seen_metrics:
                seen_metrics.add(metric)
                lines.append(f"# HELP {metric} System sensors from AIDA64")
                lines.append(f"# TYPE {metric} gauge")
            lines.append(f'{metric}{{sensor="{safe_id}",label="{safe_label}"}} {value}')
        elif sensor_type in type_map:
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


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            gpu = get_gpu_metrics()
            cpu = get_cpu_power_metrics()
            aida = get_aida64_metrics()
            total = get_total_power_metrics(gpu, cpu)
            # If AIDA64 has PSU data, the real wall power is in aida metrics
            body = (gpu + cpu + aida + total).encode()
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
