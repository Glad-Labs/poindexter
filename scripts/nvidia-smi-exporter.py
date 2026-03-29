"""Lightweight Prometheus exporter for NVIDIA GPU metrics via nvidia-smi.
Serves on port 9835. Scraped by Grafana Alloy every 15s.
"""
import subprocess
import time
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


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            body = get_gpu_metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            body = b"nvidia-smi exporter. /metrics for Prometheus metrics.\n"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Silence request logging


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), MetricsHandler)
    print(f"nvidia-smi exporter listening on :{PORT}/metrics")
    server.serve_forever()
