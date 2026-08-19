import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse, urlunparse

from app.core.onvif_probe import OnvifScanError
from app.core.video_probe import normalize_video_codec


CameraFactory = Callable[[str, int, str, str], Any]


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    current = obj
    for name in names:
        if current is None:
            return default
        if isinstance(current, dict) and name in current:
            current = current[name]
            continue
        if hasattr(current, name):
            current = getattr(current, name)
            continue
        return default
    return current


def inject_rtsp_credentials(rtsp_url: str, username: str, password: str) -> str:
    parsed = urlparse((rtsp_url or '').strip())
    if parsed.scheme not in {'rtsp', 'rtsps'} or not parsed.hostname:
        raise OnvifScanError('设备返回的不是有效 RTSP 地址')

    host = parsed.hostname
    if parsed.port:
        host = f'{host}:{parsed.port}'
    auth = ''
    if username:
        auth = f'{quote(username, safe="")}:{quote(password or "", safe="")}@'
    return urlunparse((
        parsed.scheme,
        f'{auth}{host}',
        parsed.path or '',
        '',
        parsed.query,
        '',
    ))


def stream_identity(source_url: str) -> Optional[Tuple[str, str]]:
    parsed = urlparse(source_url or '')
    if not parsed.hostname:
        return None
    return parsed.hostname.lower(), (parsed.path or '').rstrip('/')


def default_source_code(host: str, profile_token: Optional[str] = None) -> str:
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', host or '').strip('-').lower() or 'device'
    if not profile_token:
        return f'onvif-{slug}'
    token_slug = re.sub(r'[^a-zA-Z0-9]+', '-', str(profile_token)).strip('-').lower()
    if not token_slug:
        return f'onvif-{slug}'
    return f'onvif-{slug}-{token_slug}'


def infer_stream_hint(
    profile_name: Optional[str],
    width: Optional[int],
    height: Optional[int],
    profiles: List[Dict[str, Any]],
) -> str:
    name = (profile_name or '').lower()
    if any(token in name for token in ('sub', 'extra', 'second', 'third', 'mobile')):
        return 'sub'
    if any(token in name for token in ('main', 'primary')):
        return 'main'

    areas = [
        int(item.get('width') or 0) * int(item.get('height') or 0)
        for item in profiles
    ]
    max_area = max(areas) if areas else 0
    area = int(width or 0) * int(height or 0)
    if max_area and area and area < max_area:
        return 'sub'
    return 'main'


def resolve_endpoint(
    xaddr: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> Tuple[str, int, str]:
    if xaddr:
        parsed = urlparse(xaddr)
        if not parsed.hostname:
            raise OnvifScanError('ONVIF 地址无效')
        resolved_port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        return parsed.hostname, resolved_port, xaddr

    address = (host or '').strip()
    if not address:
        raise OnvifScanError('缺少设备地址')
    try:
        resolved_port = int(port or 80)
    except (TypeError, ValueError) as exc:
        raise OnvifScanError('无效端口') from exc
    return address, resolved_port, f'http://{address}:{resolved_port}/onvif/device_service'


def _normalize_encoding(raw: Any) -> str:
    try:
        return normalize_video_codec(raw, allow_unknown=True)
    except ValueError:
        return 'unknown'


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == '':
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _video_encoder(profile: Any) -> Any:
    encoder = _attr(profile, 'VideoEncoderConfiguration')
    if encoder is not None:
        return encoder
    encoder = _attr(profile, 'VideoEncoder2Configuration')
    if encoder is not None:
        return encoder

    configurations = _attr(profile, 'Configurations')
    if isinstance(configurations, (list, tuple)):
        for item in configurations:
            if _attr(item, 'Encoding') is not None or _attr(item, 'Resolution') is not None:
                return item
        return configurations[0] if configurations else None
    return configurations


def _extract_uri(stream: Any) -> str:
    if isinstance(stream, str):
        return stream.strip()
    uri = _attr(stream, 'Uri') or _attr(stream, 'uri') or ''
    return str(uri).strip()


def _profile_dict(
    profile: Any,
    rtsp_url: str,
    username: str,
    password: str,
) -> Optional[Dict[str, Any]]:
    token = _attr(profile, 'token') or _attr(profile, 'Token')
    if not token:
        return None
    encoder = _video_encoder(profile)
    resolution = _attr(encoder, 'Resolution')
    return {
        'token': str(token),
        'name': str(_attr(profile, 'Name', default=token) or token),
        'encoding': _normalize_encoding(_attr(encoder, 'Encoding')),
        'width': _as_int(_attr(resolution, 'Width') or _attr(resolution, 'width')),
        'height': _as_int(_attr(resolution, 'Height') or _attr(resolution, 'height')),
        'rtsp_url': inject_rtsp_credentials(rtsp_url, username, password),
    }


def _try_profile(
    media: Any,
    profile: Any,
    username: str,
    password: str,
    request: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    try:
        stream = media.GetStreamUri(request)
        return _profile_dict(profile, _extract_uri(stream), username, password)
    except Exception:
        return None


def _profiles_from_media(camera: Any, username: str, password: str) -> List[Dict[str, Any]]:
    media = camera.create_media_service()
    raw_profiles = media.GetProfiles() or []
    collected: List[Dict[str, Any]] = []
    for profile in raw_profiles:
        token = _attr(profile, 'token') or _attr(profile, 'Token')
        item = _try_profile(
            media,
            profile,
            username,
            password,
            {
                'StreamSetup': {
                    'Stream': 'RTP-Unicast',
                    'Transport': {'Protocol': 'RTSP'},
                },
                'ProfileToken': token,
            },
        )
        if item:
            collected.append(item)
    return collected


def _profiles_from_media2(camera: Any, username: str, password: str) -> List[Dict[str, Any]]:
    media = camera.create_media2_service()
    raw_profiles = media.GetProfiles() or []
    collected: List[Dict[str, Any]] = []
    for profile in raw_profiles:
        token = _attr(profile, 'token') or _attr(profile, 'Token')
        item = _try_profile(
            media,
            profile,
            username,
            password,
            {
                'Protocol': 'RTSP',
                'ProfileToken': token,
            },
        )
        if item:
            collected.append(item)
    return collected


def _onvif_transport(timeout_seconds: float):
    try:
        from zeep.transports import Transport
    except ImportError as exc:
        raise OnvifScanError('服务器未安装 onvif-zeep，无法拉取码流') from exc

    timeout = max(1.0, float(timeout_seconds))
    return Transport(timeout=timeout, operation_timeout=timeout)


def _default_camera_factory(
    host: str,
    port: int,
    username: str,
    password: str,
    timeout_seconds: float = 5,
) -> Any:
    try:
        from onvif import ONVIFCamera
    except ImportError as exc:
        raise OnvifScanError('服务器未安装 onvif-zeep，无法拉取码流') from exc
    return ONVIFCamera(
        host,
        port,
        username,
        password,
        transport=_onvif_transport(timeout_seconds),
    )


def fetch_device_profiles(
    *,
    xaddr: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    username: str = '',
    password: str = '',
    timeout_seconds: float = 5,
    camera_factory: Optional[CameraFactory] = None,
) -> Dict[str, Any]:
    resolved_host, resolved_port, resolved_xaddr = resolve_endpoint(xaddr, host, port)
    timeout = max(1.0, float(timeout_seconds))
    factory = camera_factory or (
        lambda host_, port_, user, passwd: _default_camera_factory(
            host_, port_, user, passwd, timeout
        )
    )
    camera = factory(resolved_host, resolved_port, username, password)

    device = {
        'manufacturer': None,
        'model': None,
        'firmware': None,
        'serial': None,
    }
    try:
        info = camera.create_devicemgmt_service().GetDeviceInformation()
        device = {
            'manufacturer': _attr(info, 'Manufacturer'),
            'model': _attr(info, 'Model'),
            'firmware': _attr(info, 'FirmwareVersion'),
            'serial': _attr(info, 'SerialNumber'),
        }
    except Exception:
        pass

    last_error: Optional[Exception] = None
    profiles: List[Dict[str, Any]] = []
    try:
        profiles = _profiles_from_media(camera, username, password)
    except Exception as exc:
        last_error = exc
    if not profiles:
        try:
            profiles = _profiles_from_media2(camera, username, password)
        except Exception as exc:
            last_error = last_error or exc

    if not profiles:
        message = '设备未返回可用码流'
        if last_error:
            message = f'无法获取码流: {last_error}'
        raise OnvifScanError(message)

    for profile in profiles:
        profile['stream_hint'] = infer_stream_hint(
            profile.get('name'),
            profile.get('width'),
            profile.get('height'),
            profiles,
        )
        profile['default_source_code'] = default_source_code(
            resolved_host,
            profile['token'] if len(profiles) > 1 else None,
        )

    return {
        'host': resolved_host,
        'port': resolved_port,
        'xaddr': resolved_xaddr,
        'device': device,
        'profiles': profiles,
    }
