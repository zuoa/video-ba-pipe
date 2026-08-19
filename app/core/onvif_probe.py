import ipaddress
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Sequence

import requests

from app.core.onvif_wsdiscovery import device_from_xaddr


class OnvifScanError(ValueError):
    pass


DEFAULT_ONVIF_PORTS = (80, 8000, 8080, 8899, 2020)
MAX_SUBNET_ADDRESSES = 256
MAX_PORTS = 8
DEFAULT_CONNECT_TIMEOUT = 0.5


def parse_ports(ports: Optional[Any] = None) -> List[int]:
    if ports is None or ports == '':
        values: Sequence[Any] = DEFAULT_ONVIF_PORTS
    elif isinstance(ports, str):
        values = [item.strip() for item in ports.split(',') if item.strip()]
    elif isinstance(ports, Iterable):
        values = list(ports)
    else:
        values = [ports]

    parsed: List[int] = []
    seen = set()
    for raw in values:
        try:
            port = int(raw)
        except (TypeError, ValueError) as exc:
            raise OnvifScanError(f'无效端口: {raw}') from exc
        if port < 1 or port > 65535:
            raise OnvifScanError(f'无效端口: {port}')
        if port in seen:
            continue
        seen.add(port)
        parsed.append(port)

    if not parsed:
        raise OnvifScanError('缺少探测端口')
    if len(parsed) > MAX_PORTS:
        raise OnvifScanError(f'端口数量不能超过 {MAX_PORTS} 个')
    return parsed


def hosts_from_subnet(cidr: str) -> List[str]:
    text = (cidr or '').strip()
    if not text:
        raise OnvifScanError('缺少网段')
    try:
        network = ipaddress.ip_network(text, strict=False)
    except ValueError as exc:
        raise OnvifScanError('网段格式无效，请使用 CIDR，例如 192.168.1.0/24') from exc
    if network.version != 4:
        raise OnvifScanError('仅支持 IPv4 网段')
    if network.num_addresses > MAX_SUBNET_ADDRESSES:
        raise OnvifScanError('网段最大支持 /24')

    if network.prefixlen >= 31:
        return [str(ip) for ip in network]
    return [str(ip) for ip in network.hosts()]


def looks_like_onvif_endpoint(
    host: str,
    port: int,
    *,
    timeout: float = DEFAULT_CONNECT_TIMEOUT,
    http_get=None,
) -> bool:
    getter = http_get or requests.get
    url = f'http://{host}:{port}/onvif/device_service'
    try:
        response = getter(url, timeout=timeout, verify=False)
    except requests.RequestException:
        return False
    return 200 <= int(getattr(response, 'status_code', 0)) < 500


def probe_host(
    host: str,
    port: int = 80,
    *,
    verify: bool = False,
    timeout: float = DEFAULT_CONNECT_TIMEOUT,
    http_get=None,
) -> Dict[str, Any]:
    address = (host or '').strip()
    if not address:
        raise OnvifScanError('缺少设备地址')
    try:
        port_number = int(port or 80)
    except (TypeError, ValueError) as exc:
        raise OnvifScanError('无效端口') from exc
    if port_number < 1 or port_number > 65535:
        raise OnvifScanError('无效端口')

    xaddr = f'http://{address}:{port_number}/onvif/device_service'
    device = device_from_xaddr(xaddr, name=address)
    if device is None:
        raise OnvifScanError('设备地址无效')

    if verify and not looks_like_onvif_endpoint(
        address,
        port_number,
        timeout=timeout,
        http_get=http_get,
    ):
        raise OnvifScanError(f'无法访问 {address}:{port_number} 的 ONVIF 服务')
    return device


def probe_subnet(
    cidr: str,
    ports: Optional[Any] = None,
    timeout_seconds: float = 8.0,
    *,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    http_get=None,
    max_workers: int = 32,
) -> List[Dict[str, Any]]:
    hosts = hosts_from_subnet(cidr)
    port_list = parse_ports(ports)
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    found: List[Dict[str, Any]] = []
    seen = set()

    def check(host: str, port: int) -> Optional[Dict[str, Any]]:
        if time.monotonic() > deadline:
            return None
        if not looks_like_onvif_endpoint(
            host,
            port,
            timeout=connect_timeout,
            http_get=http_get,
        ):
            return None
        return device_from_xaddr(
            f'http://{host}:{port}/onvif/device_service',
            name=host,
        )

    workers = max(1, min(max_workers, len(hosts) * len(port_list)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(check, host, port)
            for host in hosts
            for port in port_list
        ]
        for future in as_completed(futures):
            device = future.result()
            if not device:
                continue
            key = (device['host'], device['port'])
            if key in seen:
                continue
            seen.add(key)
            found.append(device)

    found.sort(key=lambda item: (item['host'], item['port']))
    return found
