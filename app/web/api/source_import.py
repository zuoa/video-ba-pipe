import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests
from flask import jsonify, request
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from app.core.database_models import VideoSource, db


PROVIDERS = [
    {
        'type': 'hikvision_nvr',
        'label': '海康 NVR',
        'description': '通过 Hikvision ISAPI 枚举 NVR 下的视频通道并批量导入',
    }
]


class SourceImportError(Exception):
    pass


def _local_name(tag: str) -> str:
    return tag.split('}', 1)[-1] if '}' in tag else tag


def _first_text(element: ET.Element, names: List[str]) -> Optional[str]:
    wanted = set(names)
    for child in element.iter():
        if _local_name(child.tag) in wanted and child.text:
            value = child.text.strip()
            if value:
                return value
    return None


def _parse_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {'true', '1', 'yes', 'online'}:
        return True
    if normalized in {'false', '0', 'no', 'offline'}:
        return False
    return None


def _build_base_url(data: Dict[str, Any]) -> str:
    scheme = data.get('scheme', 'http')
    host = (data.get('host') or '').strip()
    port = int(data.get('port') or (443 if scheme == 'https' else 80))
    if not host:
        raise SourceImportError('缺少设备地址')
    return f'{scheme}://{host}:{port}'


def _request_isapi(
    method: str,
    base_url: str,
    path: str,
    username: str,
    password: str,
    timeout: int = 8,
    verify_tls: bool = False,
) -> requests.Response:
    errors = []
    url = f'{base_url}{path}'
    headers = {'Accept': 'application/xml, text/xml, application/json'}

    for auth in (HTTPDigestAuth(username, password), HTTPBasicAuth(username, password)):
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                auth=auth,
                timeout=timeout,
                verify=verify_tls,
            )
        except requests.RequestException as exc:
            errors.append(str(exc))
            continue

        if response.status_code == 401:
            errors.append(f'认证失败: {response.status_code}')
            continue
        if response.status_code >= 400:
            raise SourceImportError(f'请求 {path} 失败: HTTP {response.status_code}')
        return response

    raise SourceImportError(errors[-1] if errors else f'请求 {path} 失败')


def _get_device_name(base_url: str, username: str, password: str, verify_tls: bool) -> Optional[str]:
    try:
        response = _request_isapi(
            'GET',
            base_url,
            '/ISAPI/System/deviceInfo',
            username,
            password,
            verify_tls=verify_tls,
        )
        root = ET.fromstring(response.text)
        return _first_text(root, ['deviceName', 'model', 'deviceID'])
    except Exception:
        return None


def _iter_channel_elements(root: ET.Element) -> List[ET.Element]:
    candidates = []
    allowed = {
        'InputProxyChannel',
        'VideoInputChannel',
        'StreamingChannel',
        'ProxyChannel',
    }
    for element in root.iter():
        if _local_name(element.tag) in allowed:
            candidates.append(element)
    return candidates


def _normalize_channel(element: ET.Element, host: str, username: str, password: str, rtsp_port: int) -> Optional[Dict[str, Any]]:
    raw_channel_no = _first_text(
        element,
        ['id', 'channelID', 'videoInputChannelID', 'proxyProtocolChannelID', 'inputProxyChannelID'],
    )
    if not raw_channel_no:
        return None

    channel_text = raw_channel_no.strip()
    if not channel_text.isdigit():
        digits = re.findall(r'\d+', channel_text)
        if not digits:
            return None
        channel_text = digits[-1]

    channel_no = int(channel_text)
    name = _first_text(element, ['name', 'channelName', 'videoInputChannelName']) or f'通道 {channel_no}'
    enabled = _parse_bool(_first_text(element, ['enabled']))
    online = _parse_bool(_first_text(element, ['online', 'status']))
    if online is None:
        online = enabled if enabled is not None else True

    host_slug = re.sub(r'[^a-zA-Z0-9]+', '-', host).strip('-').lower() or 'nvr'
    auth_part = f'{quote(username, safe="")}:{quote(password, safe="")}@' if username else ''

    return {
        'channel_no': channel_no,
        'channel_name': name,
        'online': online,
        'default_stream': 'sub',
        'default_source_code': f'hik-{host_slug}-ch{channel_no:02d}',
        'rtsp_url_main': f'rtsp://{auth_part}{host}:{rtsp_port}/Streaming/channels/{channel_no}01',
        'rtsp_url_sub': f'rtsp://{auth_part}{host}:{rtsp_port}/Streaming/channels/{channel_no}02',
    }


def _discover_hikvision_channels(data: Dict[str, Any]) -> Dict[str, Any]:
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    host = (data.get('host') or '').strip()
    rtsp_port = int(data.get('rtsp_port') or 554)
    verify_tls = bool(data.get('verify_tls', False))
    if not username or not password:
        raise SourceImportError('请输入用户名和密码')

    base_url = _build_base_url(data)
    device_name = _get_device_name(base_url, username, password, verify_tls)

    paths = [
        '/ISAPI/ContentMgmt/InputProxy/channels',
        '/ISAPI/System/Video/inputs/channels',
    ]
    channels_by_no: Dict[int, Dict[str, Any]] = {}
    last_error = None

    for path in paths:
        try:
            response = _request_isapi(
                'GET',
                base_url,
                path,
                username,
                password,
                verify_tls=verify_tls,
            )
            root = ET.fromstring(response.text)
            for element in _iter_channel_elements(root):
                channel = _normalize_channel(element, host, username, password, rtsp_port)
                if channel:
                    channels_by_no[channel['channel_no']] = channel
        except Exception as exc:
            last_error = exc

    if not channels_by_no:
        message = '未发现可导入通道'
        if last_error:
            message = f'{message}: {last_error}'
        raise SourceImportError(message)

    channels = sorted(channels_by_no.values(), key=lambda item: item['channel_no'])
    return {
        'provider_type': 'hikvision_nvr',
        'device_name': device_name or host,
        'channels': channels,
    }


def _discover_channels(data: Dict[str, Any]) -> Dict[str, Any]:
    provider_type = data.get('provider_type')
    if provider_type == 'hikvision_nvr':
        return _discover_hikvision_channels(data)
    raise SourceImportError(f'暂不支持的导入类型: {provider_type}')


def _build_hikvision_source_url(host: str, username: str, password: str, rtsp_port: int, channel_no: int, stream: str) -> str:
    suffix = '01' if stream == 'main' else '02'
    auth_part = f'{quote(username, safe="")}:{quote(password, safe="")}@' if username else ''
    return f'rtsp://{auth_part}{host}:{rtsp_port}/Streaming/channels/{channel_no}{suffix}'


def _validate_commit_payload(data: Dict[str, Any]) -> Tuple[str, int, List[Dict[str, Any]]]:
    host = (data.get('host') or '').strip()
    if not host:
        raise SourceImportError('缺少设备地址')
    channels = data.get('channels') or []
    if not channels:
        raise SourceImportError('请选择至少一个通道')
    rtsp_port = int(data.get('rtsp_port') or 554)
    return host, rtsp_port, channels


def _commit_hikvision_import(data: Dict[str, Any]) -> Dict[str, Any]:
    host, rtsp_port, channels = _validate_commit_payload(data)
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    created = []
    errors = []

    with db.atomic():
        for item in channels:
            try:
                channel_no = int(item['channel_no'])
                stream = item.get('stream', 'sub')
                source_code = (item.get('source_code') or '').strip()
                if not source_code:
                    raise SourceImportError('缺少 source_code')

                source_name = (item.get('name') or item.get('channel_name') or f'通道 {channel_no}').strip()
                source_url = item.get('source_url') or _build_hikvision_source_url(
                    host,
                    username,
                    password,
                    rtsp_port,
                    channel_no,
                    stream,
                )

                source = VideoSource.create(
                    name=source_name,
                    enabled=item.get('enabled', True),
                    source_code=source_code,
                    source_url=source_url,
                    source_decode_width=int(item.get('source_decode_width') or 960),
                    source_decode_height=int(item.get('source_decode_height') or 540),
                    source_fps=int(item.get('source_fps') or 10),
                    status='STOPPED',
                )
                created.append({
                    'id': source.id,
                    'source_code': source.source_code,
                    'name': source.name,
                    'channel_no': channel_no,
                })
            except Exception as exc:
                errors.append({
                    'channel_no': item.get('channel_no'),
                    'source_code': item.get('source_code'),
                    'error': str(exc),
                })

    return {
        'created_count': len(created),
        'created': created,
        'errors': errors,
    }


def _commit_import(data: Dict[str, Any]) -> Dict[str, Any]:
    provider_type = data.get('provider_type')
    if provider_type == 'hikvision_nvr':
        return _commit_hikvision_import(data)
    raise SourceImportError(f'暂不支持的导入类型: {provider_type}')


def register_source_import_api(app):
    @app.route('/api/source-import/providers', methods=['GET'])
    def get_source_import_providers():
        return jsonify({'providers': PROVIDERS})

    @app.route('/api/source-import/discover', methods=['POST'])
    def discover_source_import_channels():
        try:
            data = request.json or {}
            result = _discover_channels(data)
            return jsonify({'success': True, **result})
        except SourceImportError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        except Exception as exc:
            app.logger.error(f'发现导入通道失败: {exc}')
            return jsonify({'success': False, 'error': str(exc)}), 500

    @app.route('/api/source-import/commit', methods=['POST'])
    def commit_source_import_channels():
        try:
            data = request.json or {}
            result = _commit_import(data)
            return jsonify({'success': True, **result})
        except SourceImportError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        except Exception as exc:
            app.logger.error(f'提交批量导入失败: {exc}')
            return jsonify({'success': False, 'error': str(exc)}), 500
