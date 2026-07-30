"""Collect lightweight host resource metrics for the dashboard."""

from __future__ import annotations

import csv
import functools
import io
import json
import os
import platform
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil


_network_lock = threading.Lock()
_previous_network_sample: Optional[Dict[str, float]] = None


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 1)


def _percentage(used: Optional[float], total: Optional[float]) -> Optional[float]:
    if used is None or total is None or total <= 0:
        return None
    return round(used / total * 100, 1)


def _collect_cpu() -> Dict[str, Any]:
    load_average = None
    if hasattr(os, "getloadavg"):
        try:
            load_average = [round(value, 2) for value in os.getloadavg()]
        except OSError:
            pass

    frequency = psutil.cpu_freq()
    return {
        "usage_percent": round(psutil.cpu_percent(interval=0.1), 1),
        "logical_cores": psutil.cpu_count(logical=True) or 0,
        "physical_cores": psutil.cpu_count(logical=False) or 0,
        "frequency_mhz": round(frequency.current, 0) if frequency else None,
        "load_average": load_average,
    }


def _collect_memory() -> Dict[str, Any]:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total_bytes": memory.total,
        "used_bytes": memory.used,
        "available_bytes": memory.available,
        "usage_percent": round(memory.percent, 1),
        "swap_total_bytes": swap.total,
        "swap_used_bytes": swap.used,
        "swap_usage_percent": round(swap.percent, 1),
    }


def _collect_disks() -> List[Dict[str, Any]]:
    disks: List[Dict[str, Any]] = []
    seen_mountpoints = set()

    for partition in psutil.disk_partitions(all=False):
        if partition.mountpoint in seen_mountpoints:
            continue
        if not os.path.isdir(partition.mountpoint):
            continue
        if (
            platform.system() == "Darwin"
            and partition.mountpoint.startswith("/System/")
            and partition.mountpoint != "/System/Volumes/Data"
        ):
            continue
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (OSError, PermissionError):
            continue
        if usage.total <= 0:
            continue

        seen_mountpoints.add(partition.mountpoint)
        disks.append({
            "device": partition.device,
            "mountpoint": partition.mountpoint,
            "filesystem": partition.fstype,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "usage_percent": round(usage.percent, 1),
        })

    if not disks:
        try:
            usage = psutil.disk_usage("/")
            disks.append({
                "device": "/",
                "mountpoint": "/",
                "filesystem": "",
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "usage_percent": round(usage.percent, 1),
            })
        except (OSError, PermissionError):
            pass

    disks.sort(
        key=lambda disk: (
            0 if disk["mountpoint"] == "/System/Volumes/Data" else
            1 if disk["mountpoint"] == "/" else
            2,
            disk["mountpoint"],
        )
    )
    return disks


def _collect_network() -> Dict[str, Any]:
    global _previous_network_sample

    counters = psutil.net_io_counters()
    now = time.monotonic()
    upload_bps = 0.0
    download_bps = 0.0

    with _network_lock:
        previous = _previous_network_sample
        if previous:
            elapsed = max(now - previous["timestamp"], 0.001)
            upload_bps = max(counters.bytes_sent - previous["bytes_sent"], 0) / elapsed
            download_bps = max(counters.bytes_recv - previous["bytes_recv"], 0) / elapsed
        _previous_network_sample = {
            "timestamp": now,
            "bytes_sent": float(counters.bytes_sent),
            "bytes_recv": float(counters.bytes_recv),
        }

    interface_stats = psutil.net_if_stats()
    active_interfaces = sorted(
        name
        for name, stats in interface_stats.items()
        if stats.isup and not name.lower().startswith(("lo", "loopback"))
    )
    return {
        "bytes_sent": counters.bytes_sent,
        "bytes_received": counters.bytes_recv,
        "upload_bytes_per_second": round(upload_bps),
        "download_bytes_per_second": round(download_bps),
        "active_interfaces": active_interfaces,
    }


def _nvml_value(callable_value, default=None):
    try:
        return callable_value()
    except Exception:
        return default


def _collect_nvidia_gpus_with_nvml() -> List[Dict[str, Any]]:
    try:
        import pynvml
    except ImportError:
        return []

    try:
        pynvml.nvmlInit()
    except Exception:
        return []

    gpus = []
    try:
        count = pynvml.nvmlDeviceGetCount()
        for index in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            raw_name = _nvml_value(lambda: pynvml.nvmlDeviceGetName(handle), "NVIDIA GPU")
            name = raw_name.decode("utf-8", errors="replace") if isinstance(raw_name, bytes) else str(raw_name)
            utilization = _nvml_value(lambda: pynvml.nvmlDeviceGetUtilizationRates(handle))
            memory = _nvml_value(lambda: pynvml.nvmlDeviceGetMemoryInfo(handle))
            temperature = _nvml_value(
                lambda: pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            )
            power_mw = _nvml_value(lambda: pynvml.nvmlDeviceGetPowerUsage(handle))
            memory_total = getattr(memory, "total", None)
            memory_used = getattr(memory, "used", None)
            gpus.append({
                "index": index,
                "name": name,
                "vendor": "NVIDIA",
                "usage_percent": _safe_float(getattr(utilization, "gpu", None)),
                "memory_total_bytes": memory_total,
                "memory_used_bytes": memory_used,
                "memory_usage_percent": _percentage(memory_used, memory_total),
                "temperature_c": _safe_float(temperature),
                "power_watts": round(power_mw / 1000, 1) if power_mw is not None else None,
            })
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
    return gpus


def _collect_nvidia_gpus_with_smi() -> List[Dict[str, Any]]:
    fields = [
        "index",
        "name",
        "utilization.gpu",
        "memory.total",
        "memory.used",
        "temperature.gpu",
        "power.draw",
    ]
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={','.join(fields)}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []

    gpus = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) != len(fields):
            continue
        index, name, usage, memory_total_mb, memory_used_mb, temperature, power = [
            value.strip() for value in row
        ]
        total_mb = _safe_float(memory_total_mb)
        used_mb = _safe_float(memory_used_mb)
        total_bytes = int(total_mb * 1024 * 1024) if total_mb is not None else None
        used_bytes = int(used_mb * 1024 * 1024) if used_mb is not None else None
        gpus.append({
            "index": int(index) if index.isdigit() else len(gpus),
            "name": name,
            "vendor": "NVIDIA",
            "usage_percent": _safe_float(usage),
            "memory_total_bytes": total_bytes,
            "memory_used_bytes": used_bytes,
            "memory_usage_percent": _percentage(used_bytes, total_bytes),
            "temperature_c": _safe_float(temperature),
            "power_watts": _safe_float(power),
        })
    return gpus


def _collect_jetson_gpu() -> List[Dict[str, Any]]:
    model_path = Path("/proc/device-tree/model")
    try:
        model = model_path.read_text(errors="ignore").replace("\x00", "").strip()
    except OSError:
        return []
    if "jetson" not in model.lower() and "nvidia" not in model.lower():
        return []

    usage = None
    temperature = None
    try:
        result = subprocess.run(
            ["tegrastats", "--interval", "100", "--count", "1"],
            capture_output=True,
            check=True,
            text=True,
            timeout=2,
        )
        import re

        usage_match = re.search(r"GR3D_FREQ\s+(\d+(?:\.\d+)?)%", result.stdout)
        temperature_match = re.search(r"GPU@(\d+(?:\.\d+)?)C", result.stdout)
        usage = _safe_float(usage_match.group(1)) if usage_match else None
        temperature = _safe_float(temperature_match.group(1)) if temperature_match else None
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    return [{
        "index": 0,
        "name": model,
        "vendor": "NVIDIA",
        "usage_percent": usage,
        "memory_total_bytes": None,
        "memory_used_bytes": None,
        "memory_usage_percent": None,
        "temperature_c": temperature,
        "power_watts": None,
    }]


def _parse_capacity_bytes(value: Any) -> Optional[int]:
    if not isinstance(value, str):
        return None
    parts = value.strip().upper().split()
    if len(parts) != 2:
        return None
    amount = _safe_float(parts[0])
    multipliers = {
        "KB": 1024,
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
        "TB": 1024 ** 4,
    }
    multiplier = multipliers.get(parts[1])
    if amount is None or multiplier is None:
        return None
    return int(amount * multiplier)


@functools.lru_cache(maxsize=1)
def _collect_macos_gpus() -> List[Dict[str, Any]]:
    if platform.system() != "Darwin":
        return []
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
        displays = json.loads(result.stdout).get("SPDisplaysDataType", [])
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        return []

    vendor_names = {
        "sppci_vendor_amd": "AMD",
        "sppci_vendor_intel": "Intel",
        "sppci_vendor_nvidia": "NVIDIA",
        "spdisplays_vendor_apple": "Apple",
    }
    gpus = []
    for display in displays:
        if display.get("sppci_device_type") != "spdisplays_gpu":
            continue
        total_bytes = _parse_capacity_bytes(display.get("spdisplays_vram"))
        name = display.get("sppci_model") or display.get("_name") or "GPU"
        vendor = vendor_names.get(display.get("spdisplays_vendor"))
        if not vendor:
            vendor = next(
                (candidate for candidate in ("Apple", "AMD", "Intel", "NVIDIA") if candidate in name),
                "Unknown",
            )
        gpus.append({
            "index": len(gpus),
            "name": name,
            "vendor": vendor,
            "usage_percent": None,
            "memory_total_bytes": total_bytes,
            "memory_used_bytes": None,
            "memory_usage_percent": None,
            "temperature_c": None,
            "power_watts": None,
        })
    return gpus


def _collect_gpus() -> List[Dict[str, Any]]:
    gpus = _collect_nvidia_gpus_with_nvml()
    if gpus:
        return gpus
    gpus = _collect_nvidia_gpus_with_smi()
    if gpus:
        return gpus
    gpus = _collect_jetson_gpu()
    if gpus:
        return gpus
    return _collect_macos_gpus()


def collect_system_metrics() -> Dict[str, Any]:
    """Return a serializable snapshot of the machine running the web service."""
    return {
        "timestamp": int(time.time()),
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "uptime_seconds": max(int(time.time() - psutil.boot_time()), 0),
        "cpu": _collect_cpu(),
        "memory": _collect_memory(),
        "disks": _collect_disks(),
        "network": _collect_network(),
        "gpus": _collect_gpus(),
    }
