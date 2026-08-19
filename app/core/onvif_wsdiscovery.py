import socket
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET


WS_DISCOVERY_ADDR = '239.255.255.250'
WS_DISCOVERY_PORT = 3702

_PROBE_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
    ' xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
    ' xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    '<e:Header>'
    '<w:MessageID>uuid:{message_id}</w:MessageID>'
    '<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
    '<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
    '</e:Header>'
    '<e:Body>'
    '<d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe>'
    '</e:Body>'
    '</e:Envelope>'
)


def _local_name(tag: str) -> str:
    return tag.split('}', 1)[-1] if '}' in tag else tag


def _first_text(element: ET.Element, names: Iterable[str]) -> Optional[str]:
    wanted = set(names)
    for child in element.iter():
        if _local_name(child.tag) in wanted and child.text and child.text.strip():
            return child.text.strip()
    return None


def _is_ipv4(host: Optional[str]) -> bool:
    if not host:
        return False
    parts = host.split('.')
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def parse_scopes(text: Optional[str]) -> Dict[str, Optional[str]]:
    parsed: Dict[str, Optional[str]] = {
        'name': None,
        'hardware': None,
        'location': None,
    }
    for token in (text or '').split():
        marker = 'onvif://www.onvif.org/'
        if not token.startswith(marker):
            continue
        remainder = token[len(marker):]
        if '/' not in remainder:
            continue
        key, value = remainder.split('/', 1)
        if key in parsed and value:
            parsed[key] = unquote(value)
    return parsed


def pick_preferred_xaddr(xaddrs: Iterable[str]) -> Optional[str]:
    candidates: List[str] = []
    for raw in xaddrs:
        for part in str(raw or '').split():
            url = part.strip()
            parsed = urlparse(url)
            if parsed.scheme in {'http', 'https'} and parsed.hostname:
                candidates.append(url)
    if not candidates:
        return None
    ipv4 = [url for url in candidates if _is_ipv4(urlparse(url).hostname)]
    return (ipv4 or candidates)[0]


def device_from_xaddr(
    xaddr: str,
    *,
    name: Optional[str] = None,
    hardware: Optional[str] = None,
    scopes: str = '',
) -> Optional[Dict[str, Any]]:
    parsed = urlparse(xaddr)
    if not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    display_name = name or parsed.hostname
    return {
        'host': parsed.hostname,
        'port': port,
        'xaddr': xaddr,
        'name': display_name,
        'hardware': hardware,
        'scopes': scopes,
    }


def parse_probe_matches(xml_text: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    devices: List[Dict[str, Any]] = []
    for element in root.iter():
        if _local_name(element.tag) != 'ProbeMatch':
            continue
        xaddrs_text = _first_text(element, ['XAddrs']) or ''
        xaddr = pick_preferred_xaddr([xaddrs_text])
        if not xaddr:
            continue
        scopes_text = _first_text(element, ['Scopes']) or ''
        scopes = parse_scopes(scopes_text)
        device = device_from_xaddr(
            xaddr,
            name=scopes.get('name'),
            hardware=scopes.get('hardware'),
            scopes=scopes_text,
        )
        if device:
            devices.append(device)
    return devices


def probe_multicast(
    timeout_seconds: float = 5.0,
    *,
    sock: Optional[socket.socket] = None,
) -> List[Dict[str, Any]]:
    payload = _PROBE_TEMPLATE.format(message_id=uuid.uuid4()).encode('utf-8')
    owned_socket = sock is None
    if sock is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind(('', 0))
    sock.settimeout(0.3)

    try:
        sock.sendto(payload, (WS_DISCOVERY_ADDR, WS_DISCOVERY_PORT))
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        seen = set()
        devices: List[Dict[str, Any]] = []
        while time.monotonic() < deadline:
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            for device in parse_probe_matches(data.decode('utf-8', errors='ignore')):
                key = (device['host'], device['port'])
                if key in seen:
                    continue
                seen.add(key)
                devices.append(device)
        return devices
    finally:
        if owned_socket:
            sock.close()
