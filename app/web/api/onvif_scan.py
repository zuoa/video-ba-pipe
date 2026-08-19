from typing import Any, Dict, Iterable, List, Set, Tuple
from urllib.parse import urlparse

from flask import jsonify, request

from app.core.database_models import VideoSource, db
from app.core.license_service import LicenseError, quota_capacity
from app.core.onvif_media import (
    default_source_code,
    fetch_device_profiles,
    stream_identity,
)
from app.core.onvif_probe import OnvifScanError, probe_host, probe_subnet
from app.core.onvif_wsdiscovery import probe_multicast
from app.core.video_probe import normalize_video_codec
from app.web.api.auth import current_username, require_auth


def _timeout_seconds(data: Dict[str, Any], default: int = 5) -> int:
    try:
        timeout = int(data.get('timeout_seconds') or default)
    except (TypeError, ValueError) as exc:
        raise OnvifScanError('扫描超时无效') from exc
    if timeout < 1 or timeout > 15:
        raise OnvifScanError('扫描超时需在 1–15 秒之间')
    return timeout


def _existing_source_index() -> Tuple[Set[str], Set[Tuple[str, str]]]:
    hosts: Set[str] = set()
    identities: Set[Tuple[str, str]] = set()
    for source in VideoSource.select(VideoSource.source_url):
        parsed = urlparse(source.source_url or '')
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
            identities.add((parsed.hostname.lower(), (parsed.path or '').rstrip('/')))
    return hosts, identities


def _annotate_devices(devices: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hosts, _identities = _existing_source_index()
    annotated = []
    for device in devices:
        item = dict(device)
        host = str(item.get('host') or '').lower()
        item['already_imported'] = host in hosts
        item['default_source_code'] = default_source_code(item.get('host') or '')
        annotated.append(item)
    return annotated


def _scan_devices(data: Dict[str, Any]) -> Dict[str, Any]:
    mode = (data.get('mode') or 'multicast').strip()
    if mode not in {'multicast', 'subnet', 'host'}:
        raise OnvifScanError('不支持的扫描方式')
    timeout = _timeout_seconds(data)

    if mode == 'multicast':
        devices = probe_multicast(timeout)
    elif mode == 'subnet':
        devices = probe_subnet(
            data.get('subnet') or '',
            data.get('ports'),
            timeout_seconds=timeout,
        )
    else:
        devices = [probe_host(data.get('host') or '', data.get('port') or 80)]

    return {
        'mode': mode,
        'devices': _annotate_devices(devices),
    }


def _annotate_profiles(result: Dict[str, Any]) -> Dict[str, Any]:
    _hosts, identities = _existing_source_index()
    for profile in result.get('profiles') or []:
        identity = stream_identity(profile.get('rtsp_url') or '')
        profile['already_imported'] = identity in identities if identity else False
    return result


def _fetch_profiles(data: Dict[str, Any]) -> Dict[str, Any]:
    username = (data.get('username') or '').strip()
    if not username:
        raise OnvifScanError('请输入用户名')
    result = fetch_device_profiles(
        xaddr=data.get('xaddr'),
        host=data.get('host'),
        port=data.get('port'),
        username=username,
        password=data.get('password') or '',
        timeout_seconds=_timeout_seconds(data),
    )
    return _annotate_profiles(result)


def _commit_import(data: Dict[str, Any], owner_username: str) -> Dict[str, Any]:
    sources = data.get('sources') or []
    if not isinstance(sources, list) or not sources:
        raise OnvifScanError('请选择至少一条码流')

    created: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    with quota_capacity('video_sources', requested=len(sources)):
        for item in sources:
            source_code = (item.get('source_code') or '').strip()
            try:
                name = (item.get('name') or '').strip()
                source_url = (item.get('source_url') or '').strip()
                if not source_code:
                    raise OnvifScanError('缺少 source_code')
                if not name:
                    raise OnvifScanError('缺少名称')
                if not source_url:
                    raise OnvifScanError('缺少 source_url')

                decode_keyframes_only = item.get('decode_keyframes_only')
                if (
                    decode_keyframes_only is not None
                    and not isinstance(decode_keyframes_only, bool)
                ):
                    raise OnvifScanError('decode_keyframes_only 必须是布尔值或 null')

                with db.atomic():
                    source = VideoSource.create(
                        name=name,
                        enabled=item.get('enabled', True),
                        source_code=source_code,
                        source_url=source_url,
                        source_decode_width=int(item.get('source_decode_width') or 640),
                        source_decode_height=int(item.get('source_decode_height') or 480),
                        source_fps=int(item.get('source_fps') or 5),
                        source_codec=normalize_video_codec(
                            item.get('source_codec'),
                            allow_unknown=True,
                        ),
                        decode_keyframes_only=decode_keyframes_only,
                        status='STOPPED',
                        created_by=owner_username,
                    )
                created.append({
                    'id': source.id,
                    'source_code': source.source_code,
                    'name': source.name,
                })
            except Exception as exc:
                errors.append({
                    'source_code': source_code or item.get('source_code'),
                    'error': str(exc),
                })

    return {
        'created_count': len(created),
        'created': created,
        'errors': errors,
    }


def register_onvif_scan_api(app):
    @app.route('/api/onvif/scan', methods=['POST'])
    @require_auth
    def scan_onvif_devices():
        try:
            result = _scan_devices(request.json or {})
            return jsonify({'success': True, **result})
        except OnvifScanError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        except Exception as exc:
            app.logger.error(f'ONVIF 扫描失败: {exc}')
            return jsonify({'success': False, 'error': str(exc)}), 500

    @app.route('/api/onvif/profiles', methods=['POST'])
    @require_auth
    def fetch_onvif_profiles():
        try:
            result = _fetch_profiles(request.json or {})
            return jsonify({'success': True, **result})
        except OnvifScanError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        except Exception as exc:
            app.logger.error(f'ONVIF 拉取码流失败: {exc}')
            return jsonify({'success': False, 'error': str(exc)}), 500

    @app.route('/api/onvif/import', methods=['POST'])
    @require_auth
    def import_onvif_sources():
        try:
            result = _commit_import(request.json or {}, current_username('admin'))
            return jsonify({'success': True, **result})
        except OnvifScanError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        except LicenseError as exc:
            return jsonify({'success': False, **exc.to_dict()}), 403
        except Exception as exc:
            app.logger.error(f'ONVIF 导入失败: {exc}')
            return jsonify({'success': False, 'error': str(exc)}), 500
