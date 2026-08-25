from types import SimpleNamespace

from app.core import system_metrics


def test_host_network_counters_use_lowest_metric_default_route(tmp_path):
    route_path = tmp_path / "route"
    route_path.write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 00000000 0100000A 0003 0 0 100 00000000 0 0 0\n"
        "wlan0 00000000 0100000A 0003 0 0 600 00000000 0 0 0\n"
        "docker0 000011AC 00000000 0001 0 0 0 0000FFFF 0 0 0\n",
        encoding="utf-8",
    )
    dev_path = tmp_path / "dev"
    dev_path.write_text(
        "Inter-| Receive | Transmit\n"
        " face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
        " eth0: 4096 1 0 0 0 0 0 0 8192 1 0 0 0 0 0 0\n"
        " wlan0: 1000 1 0 0 0 0 0 0 2000 1 0 0 0 0 0 0\n"
        " docker0: 9999 1 0 0 0 0 0 0 9999 1 0 0 0 0 0 0\n",
        encoding="utf-8",
    )

    interfaces = system_metrics._read_default_route_interfaces(route_path)
    counters = system_metrics._read_proc_network_counters(dev_path, interfaces)

    assert interfaces == ["eth0"]
    assert counters == {
        "bytes_sent": 8192,
        "bytes_received": 4096,
        "active_interfaces": ["eth0"],
    }


def test_collect_network_falls_back_to_container_scope(monkeypatch):
    monkeypatch.setattr(system_metrics, "_read_host_network_counters", lambda: None)
    monkeypatch.setattr(
        system_metrics.psutil,
        "net_io_counters",
        lambda: SimpleNamespace(bytes_sent=1200, bytes_recv=3400),
    )
    monkeypatch.setattr(
        system_metrics.psutil,
        "net_if_stats",
        lambda: {
            "lo": SimpleNamespace(isup=True),
            "eth0": SimpleNamespace(isup=True),
            "eth1": SimpleNamespace(isup=False),
        },
    )
    monkeypatch.setattr(system_metrics.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(system_metrics, "_previous_network_sample", None)

    network = system_metrics._collect_network()

    assert network["scope"] == "container"
    assert network["active_interfaces"] == ["eth0"]
    assert network["bytes_sent"] == 1200
    assert network["bytes_received"] == 3400


def test_collect_network_uses_host_counters_and_calculates_rate(monkeypatch):
    samples = iter([
        {
            "bytes_sent": 1000,
            "bytes_received": 2000,
            "active_interfaces": ["eth0"],
        },
        {
            "bytes_sent": 1500,
            "bytes_received": 3000,
            "active_interfaces": ["eth0"],
        },
    ])
    timestamps = iter([10.0, 12.0])
    monkeypatch.setattr(
        system_metrics,
        "_read_host_network_counters",
        lambda: next(samples),
    )
    monkeypatch.setattr(
        system_metrics.time,
        "monotonic",
        lambda: next(timestamps),
    )
    monkeypatch.setattr(system_metrics, "_previous_network_sample", None)

    first = system_metrics._collect_network()
    second = system_metrics._collect_network()

    assert first["scope"] == "host"
    assert first["download_bytes_per_second"] == 0
    assert second["upload_bytes_per_second"] == 250
    assert second["download_bytes_per_second"] == 500


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


def test_collect_accelerator_metrics_is_a_serializable_worker_snapshot(monkeypatch):
    monkeypatch.setattr(system_metrics, "_collect_gpus", lambda: [{"index": 0}])
    monkeypatch.setattr(system_metrics, "_collect_npus", lambda: [{"index": 1}])
    monkeypatch.setattr(system_metrics.time, "time", lambda: 123.9)

    assert system_metrics.collect_accelerator_metrics() == {
        "timestamp": 123,
        "gpus": [{"index": 0}],
        "npus": [{"index": 1}],
    }
