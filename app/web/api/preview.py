"""视频源实时预览相关接口（WebRTC / MediaMTX + 最新检测帧配置）。"""

from flask import jsonify, request

from app.core.database_models import VideoSource, db
from app.web.api.auth import require_auth
from app.core.mediamtx_client import mediamtx_client
from app.config import (
    MEDIAMTX_ENABLED,
    MEDIAMTX_WEBRTC_PORT,
    MEDIAMTX_WEBRTC_PUBLIC_HOST,
    DETECTION_SNAPSHOT_ENABLED,
)


def _whep_base_url() -> str:
    """返回浏览器侧访问 MediaMTX WebRTC 的基础 URL。

    MEDIAMTX_WEBRTC_PUBLIC_HOST 非空时用它（部署侧显式指定浏览器可达主机/IP）；
    否则返回空串，由前端用页面所在 hostname 兜底。
    """
    host = (MEDIAMTX_WEBRTC_PUBLIC_HOST or '').strip()
    if not host:
        return ''
    return f'http://{host}:{MEDIAMTX_WEBRTC_PORT}'


def register_preview_api(app):
    @app.route('/api/preview/config', methods=['GET'])
    @require_auth
    def preview_config():
        """返回实时预览相关配置，供前端决定按钮可用性与 WHEP 连接地址。"""
        available = mediamtx_client.is_available() if MEDIAMTX_ENABLED else False
        return jsonify({
            'webrtc_enabled': bool(MEDIAMTX_ENABLED),
            'webrtc_available': bool(available),
            'whep_base_url': _whep_base_url(),
            'webrtc_port': MEDIAMTX_WEBRTC_PORT,
            'detection_snapshot_enabled': bool(DETECTION_SNAPSHOT_ENABLED),
        })

    @app.route('/api/preview/ensure/<int:source_id>', methods=['POST'])
    @require_auth
    def preview_ensure(source_id):
        """点击「实时预览」时懒注册 MediaMTX 按需路径（CRUD 同步的兜底），返回 WHEP 地址。"""
        try:
            source = VideoSource.get_by_id(source_id)
        except VideoSource.DoesNotExist:
            return jsonify({'success': False, 'message': '视频源不存在'}), 404

        if not MEDIAMTX_ENABLED:
            return jsonify({
                'success': False,
                'message': '未启用 MediaMTX WebRTC 实时预览（MEDIAMTX_ENABLED=false）',
            }), 400

        registered = mediamtx_client.register_path(source.source_code, source.source_url)
        if not registered and not mediamtx_client.is_available(force=True):
            return jsonify({
                'success': False,
                'message': 'MediaMTX 服务不可达，请检查 mediamtx 容器与 MEDIAMTX_* 配置',
            }), 503

        base = _whep_base_url()
        return jsonify({
            'success': True,
            'source_code': source.source_code,
            'whep_base_url': base,
            'webrtc_port': MEDIAMTX_WEBRTC_PORT,
            'whep_url': f'{base}/{source.source_code}/whep' if base else '',
        })
