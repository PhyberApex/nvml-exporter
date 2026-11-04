"""Minimal NVML-based Prometheus exporter for GPU memory, temperature, utilisation and power."""

import http.server
import os
import time
import threading

from pynvml import (
    nvmlInit,
    nvmlDeviceGetCount,
    nvmlDeviceGetHandleByIndex,
    nvmlDeviceGetMemoryInfo,
    nvmlDeviceGetName,
    nvmlDeviceGetPowerManagementLimit,
    nvmlDeviceGetPowerUsage,
    nvmlDeviceGetTemperature,
    nvmlDeviceGetUtilizationRates,
    nvmlShutdown,
    NVMLError,
    NVML_TEMPERATURE_GPU,
)

PORT = int(os.environ.get("PORT", "9835"))
# A device polling every 2 s sees a fresh sample each time; Prometheus scrapes far less often.
SCRAPE_INTERVAL = float(os.environ.get("SCRAPE_INTERVAL_S", "2"))

DEVICE_METRICS = (
    ("nvidia_gpu_memory_used_bytes", "GPU memory used, in bytes."),
    ("nvidia_gpu_memory_total_bytes", "GPU memory total, in bytes."),
    ("nvidia_gpu_memory_free_bytes", "GPU memory free, in bytes."),
    ("nvidia_gpu_temperature_celsius", "GPU temperature, in degrees Celsius."),
    ("nvidia_gpu_utilization_percent", "GPU utilisation over the last sample period, in percent."),
    ("nvidia_gpu_power_usage_watts", "GPU power draw, in watts."),
    ("nvidia_gpu_power_limit_watts", "GPU power-management limit, in watts."),
)
COLLECTOR_UP_METRIC = "nvidia_gpu_collector_up"
COLLECTOR_UP_HELP = "Whether the last scrape collected GPU metrics successfully (1) or failed (0)."

metrics_output = ""


def escape_label_value(value):
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def optional_readings(handle):
    """The gauges a GPU may refuse: NVML raises on hardware that does not report them.

    Each is read on its own, so one refusal leaves that gauge out for that GPU and the
    others present. Returns metric name -> value for the readings that worked.
    """
    readers = {
        "nvidia_gpu_utilization_percent": lambda: nvmlDeviceGetUtilizationRates(handle).gpu,
        "nvidia_gpu_power_usage_watts": lambda: nvmlDeviceGetPowerUsage(handle) / 1000,
        "nvidia_gpu_power_limit_watts": lambda: nvmlDeviceGetPowerManagementLimit(handle) / 1000,
    }
    values = {}
    for metric_name, read in readers.items():
        try:
            values[metric_name] = read()
        except NVMLError:
            continue
    return values


def collect():
    """Return device sample lines for a single scrape, keyed by metric name.

    Raises whatever NVML raises for the core readings; callers decide how to render a
    failure. The optional readings (utilisation, power) never raise past this point.
    """
    samples = {name: [] for name, _ in DEVICE_METRICS}
    count = nvmlDeviceGetCount()
    for i in range(count):
        handle = nvmlDeviceGetHandleByIndex(i)
        mem = nvmlDeviceGetMemoryInfo(handle)
        name = escape_label_value(nvmlDeviceGetName(handle))
        temp = nvmlDeviceGetTemperature(handle, NVML_TEMPERATURE_GPU)
        labels = f'gpu="{i}",name="{name}"'
        values = {
            "nvidia_gpu_memory_used_bytes": mem.used,
            "nvidia_gpu_memory_total_bytes": mem.total,
            "nvidia_gpu_memory_free_bytes": mem.free,
            "nvidia_gpu_temperature_celsius": temp,
        }
        values.update(optional_readings(handle))
        for metric_name, value in values.items():
            samples[metric_name].append(f'{metric_name}{{{labels}}} {value}')
    return samples


def render(samples, collector_up, error=None):
    lines = []
    if error is not None:
        lines.append(f"# error: {error}")
    for name, help_text in DEVICE_METRICS:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.extend(samples.get(name, []))
    lines.append(f"# HELP {COLLECTOR_UP_METRIC} {COLLECTOR_UP_HELP}")
    lines.append(f"# TYPE {COLLECTOR_UP_METRIC} gauge")
    lines.append(f"{COLLECTOR_UP_METRIC} {collector_up}")
    return "\n".join(lines) + "\n"


def scrape():
    """Run one collection pass and return the exposition body for it.

    A failure renders collector_up=0 with no device samples, so a dead GPU
    is never read back as stale-but-healthy readings.
    """
    try:
        samples = collect()
        return render(samples, 1)
    except Exception as e:
        return render({}, 0, error=e)


def scrape_loop():
    global metrics_output
    while True:
        metrics_output = scrape()
        time.sleep(SCRAPE_INTERVAL)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(metrics_output.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # silence request logs


if __name__ == "__main__":
    nvmlInit()
    print(f"NVML initialized, {nvmlDeviceGetCount()} GPU(s) found", flush=True)
    threading.Thread(target=scrape_loop, daemon=True).start()
    time.sleep(0.5)  # let first scrape complete
    print(f"Serving metrics on :{PORT}/metrics every {SCRAPE_INTERVAL:g}s", flush=True)
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    finally:
        nvmlShutdown()
