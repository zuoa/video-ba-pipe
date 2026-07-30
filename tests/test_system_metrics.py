from types import SimpleNamespace

from app.core import system_metrics


def test_nvidia_smi_metrics_are_normalized(monkeypatch):
    output = "0, NVIDIA RTX 4090, 42, 24564, 12282, 61, 320.5\n"

    monkeypatch.setattr(
        system_metrics.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=output),
    )

    gpus = system_metrics._collect_nvidia_gpus_with_smi()

    assert gpus == [{
        "index": 0,
        "name": "NVIDIA RTX 4090",
        "vendor": "NVIDIA",
        "usage_percent": 42.0,
        "memory_total_bytes": 24564 * 1024 * 1024,
        "memory_used_bytes": 12282 * 1024 * 1024,
        "memory_usage_percent": 50.0,
        "temperature_c": 61.0,
        "power_watts": 320.5,
    }]


def test_macos_gpu_is_reported_when_live_utilization_is_unavailable(monkeypatch):
    output = """
    {
      "SPDisplaysDataType": [{
        "sppci_device_type": "spdisplays_gpu",
        "sppci_model": "AMD Radeon RX 5500 XT",
        "spdisplays_vendor": "sppci_vendor_amd",
        "spdisplays_vram": "8 GB"
      }]
    }
    """
    monkeypatch.setattr(system_metrics.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        system_metrics.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=output),
    )
    system_metrics._collect_macos_gpus.cache_clear()

    gpus = system_metrics._collect_macos_gpus()

    assert len(gpus) == 1
    assert gpus[0]["name"] == "AMD Radeon RX 5500 XT"
    assert gpus[0]["vendor"] == "AMD"
    assert gpus[0]["memory_total_bytes"] == 8 * 1024 ** 3
    assert gpus[0]["usage_percent"] is None
    system_metrics._collect_macos_gpus.cache_clear()


def test_capacity_parser_rejects_unknown_values():
    assert system_metrics._parse_capacity_bytes("16 GB") == 16 * 1024 ** 3
    assert system_metrics._parse_capacity_bytes("N/A") is None
    assert system_metrics._parse_capacity_bytes(None) is None
