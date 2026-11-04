#!/usr/bin/env python3
import importlib
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class _StubNVMLError(Exception):
    pass


def _install_pynvml_stub():
    """Exercise collect()/scrape() against a fake pynvml — no GPU or driver needed."""
    stub = types.ModuleType('pynvml')
    for name in (
        'nvmlInit',
        'nvmlDeviceGetCount',
        'nvmlDeviceGetHandleByIndex',
        'nvmlDeviceGetMemoryInfo',
        'nvmlDeviceGetName',
        'nvmlDeviceGetTemperature',
        'nvmlDeviceGetUtilizationRates',
        'nvmlDeviceGetPowerUsage',
        'nvmlDeviceGetPowerManagementLimit',
        'nvmlShutdown',
    ):
        setattr(stub, name, lambda *args, **kwargs: None)
    stub.NVML_TEMPERATURE_GPU = 0
    stub.NVMLError = _StubNVMLError
    sys.modules.setdefault('pynvml', stub)


_install_pynvml_stub()

import exporter  # noqa: E402


def _healthy_device(**overrides):
    """Patches for one GPU answering every NVML call; overrides replace a call's patch."""
    patches = {
        'nvmlDeviceGetCount': patch('exporter.nvmlDeviceGetCount', return_value=1),
        'nvmlDeviceGetHandleByIndex': patch('exporter.nvmlDeviceGetHandleByIndex', return_value='handle-0'),
        'nvmlDeviceGetMemoryInfo': patch('exporter.nvmlDeviceGetMemoryInfo', return_value=SimpleNamespace(used=100, total=1000, free=900)),
        'nvmlDeviceGetName': patch('exporter.nvmlDeviceGetName', return_value='RTX 3060'),
        'nvmlDeviceGetTemperature': patch('exporter.nvmlDeviceGetTemperature', return_value=65),
        'nvmlDeviceGetUtilizationRates': patch('exporter.nvmlDeviceGetUtilizationRates', return_value=SimpleNamespace(gpu=87, memory=40)),
        'nvmlDeviceGetPowerUsage': patch('exporter.nvmlDeviceGetPowerUsage', return_value=148500),
        'nvmlDeviceGetPowerManagementLimit': patch('exporter.nvmlDeviceGetPowerManagementLimit', return_value=170000),
    }
    patches.update(overrides)
    return list(patches.values())


class _Patched:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()


class Collect(unittest.TestCase):
    def test_single_device(self):
        with _Patched(_healthy_device()):
            samples = exporter.collect()

        self.assertEqual(
            samples['nvidia_gpu_memory_used_bytes'],
            ['nvidia_gpu_memory_used_bytes{gpu="0",name="RTX 3060"} 100'],
        )
        self.assertEqual(
            samples['nvidia_gpu_temperature_celsius'],
            ['nvidia_gpu_temperature_celsius{gpu="0",name="RTX 3060"} 65'],
        )

    def test_utilisation_and_power_share_the_labels_and_are_in_watts(self):
        with _Patched(_healthy_device()):
            samples = exporter.collect()

        self.assertEqual(
            samples['nvidia_gpu_utilization_percent'],
            ['nvidia_gpu_utilization_percent{gpu="0",name="RTX 3060"} 87'],
        )
        self.assertEqual(
            samples['nvidia_gpu_power_usage_watts'],
            ['nvidia_gpu_power_usage_watts{gpu="0",name="RTX 3060"} 148.5'],
        )
        self.assertEqual(
            samples['nvidia_gpu_power_limit_watts'],
            ['nvidia_gpu_power_limit_watts{gpu="0",name="RTX 3060"} 170.0'],
        )

    def test_a_refused_optional_reading_leaves_only_that_gauge_out(self):
        refused = patch('exporter.nvmlDeviceGetPowerUsage', side_effect=_StubNVMLError('Not Supported'))
        with _Patched(_healthy_device(nvmlDeviceGetPowerUsage=refused)):
            samples = exporter.collect()

        self.assertEqual(samples['nvidia_gpu_power_usage_watts'], [])
        self.assertEqual(len(samples['nvidia_gpu_utilization_percent']), 1)
        self.assertEqual(len(samples['nvidia_gpu_power_limit_watts']), 1)
        self.assertEqual(len(samples['nvidia_gpu_memory_used_bytes']), 1)

    def test_a_refused_utilisation_reading_leaves_only_that_gauge_out(self):
        refused = patch('exporter.nvmlDeviceGetUtilizationRates', side_effect=_StubNVMLError('Not Supported'))
        with _Patched(_healthy_device(nvmlDeviceGetUtilizationRates=refused)):
            samples = exporter.collect()

        self.assertEqual(samples['nvidia_gpu_utilization_percent'], [])
        self.assertEqual(len(samples['nvidia_gpu_power_usage_watts']), 1)
        self.assertEqual(len(samples['nvidia_gpu_temperature_celsius']), 1)

    def test_a_core_reading_failure_still_propagates(self):
        failing = patch('exporter.nvmlDeviceGetTemperature', side_effect=_StubNVMLError('GPU lost'))
        with _Patched(_healthy_device(nvmlDeviceGetTemperature=failing)):
            with self.assertRaises(_StubNVMLError):
                exporter.collect()


class Configuration(unittest.TestCase):
    def _reloaded_with(self, env):
        with patch.dict(os.environ, env, clear=False):
            for key in ('SCRAPE_INTERVAL_S', 'PORT'):
                if key not in env:
                    os.environ.pop(key, None)
            reloaded = importlib.reload(exporter)
            values = (reloaded.SCRAPE_INTERVAL, reloaded.PORT)
        importlib.reload(exporter)
        return values

    def test_defaults_to_two_seconds_on_9835(self):
        self.assertEqual(self._reloaded_with({}), (2, 9835))

    def test_reads_scrape_interval_s(self):
        interval, _ = self._reloaded_with({'SCRAPE_INTERVAL_S': '5'})
        self.assertEqual(interval, 5)

    def test_reads_port(self):
        _, port = self._reloaded_with({'PORT': '9400'})
        self.assertEqual(port, 9400)


class CollectEdges(unittest.TestCase):
    def test_no_devices_returns_empty_sample_lists(self):
        with patch('exporter.nvmlDeviceGetCount', return_value=0):
            samples = exporter.collect()
        for name, _ in exporter.DEVICE_METRICS:
            self.assertEqual(samples[name], [])

    def test_propagates_nvml_errors(self):
        with patch('exporter.nvmlDeviceGetCount', side_effect=RuntimeError('NVML driver mismatch')):
            with self.assertRaises(RuntimeError):
                exporter.collect()

    def test_device_name_is_escaped_in_labels(self):
        named = patch('exporter.nvmlDeviceGetName', return_value='Rogue "GPU" \\ 1')
        with _Patched(_healthy_device(nvmlDeviceGetName=named)):
            samples = exporter.collect()

        line = samples['nvidia_gpu_temperature_celsius'][0]
        self.assertIn('name="Rogue \\"GPU\\" \\\\ 1"', line)


class Render(unittest.TestCase):
    def test_every_metric_has_help_and_type(self):
        body = exporter.render({}, 1)
        for name, _ in exporter.DEVICE_METRICS:
            self.assertIn(f'# HELP {name}', body)
            self.assertIn(f'# TYPE {name} gauge', body)
        self.assertIn(f'# HELP {exporter.COLLECTOR_UP_METRIC}', body)
        self.assertIn(f'# TYPE {exporter.COLLECTOR_UP_METRIC} gauge', body)

    def test_collector_up_value_is_rendered(self):
        self.assertIn('nvidia_gpu_collector_up 1', exporter.render({}, 1))
        self.assertIn('nvidia_gpu_collector_up 0', exporter.render({}, 0))

    def test_error_comment_is_included_when_given(self):
        body = exporter.render({}, 0, error=RuntimeError('boom'))
        self.assertIn('# error: boom', body)

    def test_no_error_comment_when_none_given(self):
        body = exporter.render({}, 1)
        self.assertNotIn('# error:', body)

    def test_device_samples_are_included(self):
        samples = {'nvidia_gpu_temperature_celsius': ['nvidia_gpu_temperature_celsius{gpu="0",name="x"} 42']}
        body = exporter.render(samples, 1)
        self.assertIn('nvidia_gpu_temperature_celsius{gpu="0",name="x"} 42', body)


class Scrape(unittest.TestCase):
    def test_healthy_scrape_reports_collector_up_and_device_samples(self):
        with _Patched(_healthy_device()):
            body = exporter.scrape()

        self.assertIn('nvidia_gpu_collector_up 1', body)
        self.assertIn('nvidia_gpu_temperature_celsius{gpu="0",name="RTX 3060"} 65', body)
        self.assertIn('nvidia_gpu_utilization_percent{gpu="0",name="RTX 3060"} 87', body)
        self.assertIn('nvidia_gpu_power_usage_watts{gpu="0",name="RTX 3060"} 148.5', body)

    def test_failed_scrape_reports_collector_down_with_no_device_samples(self):
        with patch('exporter.nvmlDeviceGetCount', side_effect=RuntimeError('GPU fell off the bus')):
            body = exporter.scrape()

        self.assertIn('nvidia_gpu_collector_up 0', body)
        self.assertIn('# error: GPU fell off the bus', body)
        for name, _ in exporter.DEVICE_METRICS:
            self.assertNotIn(f'{name}{{', body)

    def test_recovers_on_next_successful_scrape(self):
        with patch('exporter.nvmlDeviceGetCount', side_effect=RuntimeError('driver reload')):
            failed_body = exporter.scrape()
        self.assertIn('nvidia_gpu_collector_up 0', failed_body)

        with _Patched(_healthy_device()):
            recovered_body = exporter.scrape()

        self.assertIn('nvidia_gpu_collector_up 1', recovered_body)
        self.assertIn('nvidia_gpu_temperature_celsius{gpu="0",name="RTX 3060"} 65', recovered_body)


class EscapeLabelValue(unittest.TestCase):
    def test_escapes_backslash_quote_and_newline(self):
        self.assertEqual(
            exporter.escape_label_value('a\\b"c\nd'),
            'a\\\\b\\"c\\nd',
        )

    def test_plain_value_is_unchanged(self):
        self.assertEqual(exporter.escape_label_value('NVIDIA RTX 3060'), 'NVIDIA RTX 3060')


if __name__ == '__main__':
    unittest.main()
