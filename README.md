# nvml-exporter

A minimal Prometheus exporter for NVIDIA GPUs: one Python file, one dependency
([`nvidia-ml-py`](https://pypi.org/project/nvidia-ml-py/)), seven gauges per GPU and a
collector-health gauge, served on `:9835/metrics`.

It talks to NVML directly through the official Python bindings. There is no `nvidia-smi`
subprocess to parse, no DCGM daemon to run, and no Prometheus client library: the exposition
body is rendered by hand from a background thread that samples every few seconds.

## Why another GPU exporter?

- [DCGM-exporter](https://github.com/NVIDIA/dcgm-exporter) is the thorough option and pulls
  in DCGM, a 500 MB image and a profiling stack. If you want ECC counters, NVLink and per-process
  accounting, use it.
- [`nvidia_gpu_exporter`](https://github.com/utkuozdemir/nvidia_gpu_exporter) shells out to
  `nvidia-smi` and parses its CSV on every scrape. It works well and exports far more fields;
  it also depends on the CLI's output format.
- **This one** is for the homelab box with one or two consumer cards where you want memory,
  temperature, utilisation and power in Grafana and nothing else. It is ~150 lines you can
  read in full, uses the same `:9835` port convention as `nvidia_gpu_exporter`, and runs in a
  `python:slim` image.

## Metrics

Every per-GPU gauge carries the labels `gpu` (the NVML device index) and `name` (the product
name as NVML reports it, e.g. `NVIDIA GeForce RTX 3060`).

| Metric | Unit | Notes |
|---|---|---|
| `nvidia_gpu_memory_used_bytes` | bytes | |
| `nvidia_gpu_memory_total_bytes` | bytes | |
| `nvidia_gpu_memory_free_bytes` | bytes | |
| `nvidia_gpu_temperature_celsius` | °C | GPU die sensor |
| `nvidia_gpu_utilization_percent` | % | GPU busy time over NVML's last sample period |
| `nvidia_gpu_power_usage_watts` | W | optional, see below |
| `nvidia_gpu_power_limit_watts` | W | optional, see below |
| `nvidia_gpu_collector_up` | 0/1 | **the gauge to alert on** |

Memory and temperature are core readings: if NVML refuses one of them the whole scrape is
treated as failed. Utilisation and the two power gauges are *optional*: some cards and some
virtualised GPUs refuse them, and a refusal leaves that gauge out for that GPU while the
others stay present. A missing series is the honest answer; a zero would be a lie.

`nvidia_gpu_collector_up` is emitted on every scrape, success or failure. When a scrape fails
(driver reload, GPU fell off the bus, NVML library mismatch after a host upgrade) the body
carries `collector_up 0`, an `# error:` comment with the exception, and **no device samples**,
so a dead GPU is never read back as stale-but-healthy readings. The HTTP server keeps
answering throughout, which is exactly why a container health check cannot see this and the
gauge exists.

Sample output:

```text
# HELP nvidia_gpu_memory_used_bytes GPU memory used, in bytes.
# TYPE nvidia_gpu_memory_used_bytes gauge
nvidia_gpu_memory_used_bytes{gpu="0",name="NVIDIA GeForce RTX 3060"} 10526195712
# HELP nvidia_gpu_temperature_celsius GPU temperature, in degrees Celsius.
# TYPE nvidia_gpu_temperature_celsius gauge
nvidia_gpu_temperature_celsius{gpu="0",name="NVIDIA GeForce RTX 3060"} 33
...
# HELP nvidia_gpu_collector_up Whether the last scrape collected GPU metrics successfully (1) or failed (0).
# TYPE nvidia_gpu_collector_up gauge
nvidia_gpu_collector_up 1
```

## Running

The image is published to GHCR for `linux/amd64` only. NVML on arm64 means Jetson, which I
cannot test; the Dockerfile has nothing architecture-specific in it, so building it yourself
on a Jetson may well work, and a report either way is welcome.

Tags: `:latest` and `:x.y.z` for releases, `:next` for the current `main`.

### Docker Compose

The container needs the NVIDIA Container Toolkit on the host so the driver library is mounted
in. Either the modern `deploy.resources` form:

```yaml
services:
  nvml-exporter:
    image: ghcr.io/phyberapex/nvml-exporter:latest
    restart: unless-stopped
    ports:
      - "9835:9835"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [utility]
```

or the older `runtime:` form, which is what I run:

```yaml
services:
  nvml-exporter:
    image: ghcr.io/phyberapex/nvml-exporter:latest
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
    ports:
      - "9835:9835"
    restart: unless-stopped
```

`capabilities: [utility]` (or `NVIDIA_DRIVER_CAPABILITIES=utility`) is enough: the exporter
needs `libnvidia-ml.so` and nothing from the compute or graphics sets.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `9835` | TCP port the `/metrics` server binds on `0.0.0.0` |
| `SCRAPE_INTERVAL_S` | `2` | Seconds between NVML sampling passes |
| `NVIDIA_VISIBLE_DEVICES` | (toolkit default) | Which GPUs the container sees, and therefore which it exports |

Sampling runs on its own thread independent of Prometheus. Two seconds means every scrape
gets a fresh reading at well under 1 % of one CPU core; the interval mostly matters for how
quickly `collector_up` flips after a fault.

### Prometheus

```yaml
scrape_configs:
  - job_name: gpu
    static_configs:
      - targets: ['gpu-host:9835']
```

Alert on the collector, not on the container:

```yaml
groups:
  - name: gpu
    rules:
      - alert: GpuCollectorDown
        expr: nvidia_gpu_collector_up == 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: NVML collection is failing on {{ $labels.instance }}
      - alert: GpuCollectorAbsent
        expr: absent(nvidia_gpu_collector_up)
        for: 5m
        labels:
          severity: warning
```

### Grafana

Useful panel queries:

```promql
# VRAM in use, per GPU
nvidia_gpu_memory_used_bytes

# VRAM in use, as a share of the card
nvidia_gpu_memory_used_bytes / nvidia_gpu_memory_total_bytes

# Power draw against the card's limit
nvidia_gpu_power_usage_watts / nvidia_gpu_power_limit_watts

# Utilisation and temperature as plain gauges
nvidia_gpu_utilization_percent
nvidia_gpu_temperature_celsius
```

Use `name` for the legend and `gpu` for the series key: the name repeats when two identical
cards sit in one host, the index does not.

## Running outside Docker

```bash
pip install -r requirements.txt
PORT=9835 SCRAPE_INTERVAL_S=2 python exporter.py
```

The host needs the NVIDIA driver installed; `nvidia-ml-py` loads `libnvidia-ml.so` from it.

## Developing

```bash
python -m unittest discover -p 'test_*.py' -v
```

The tests stub `pynvml` in-process, so they run anywhere, with no GPU and no driver. CI runs
them on every pull request and builds the image.

## License

[MIT](LICENSE)
