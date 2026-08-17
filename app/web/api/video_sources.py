from flask import jsonify, request
from app.core.database_models import VideoSource
from app.core.video_probe import normalize_video_codec
from app.core.mediamtx_client import mediamtx_client
from app.core.license_service import LicenseError, quota_capacity


def _sync_mediamtx_create(source):
    """创建/更新视频源后，尽力同步 MediaMTX 按需拉流路径（失败不影响业务）。"""
    try:
        mediamtx_client.register_path(source.source_code, source.source_url)
    except Exception as e:
        app.logger.warning(f"MediaMTX 注册路径失败（忽略）source={source.source_code}: {e}")


def _sync_mediamtx_delete(source_code):
    """删除视频源后，尽力注销 MediaMTX 路径（失败不影响业务）。"""
    try:
        mediamtx_client.unregister_path(source_code)
    except Exception as e:
        app.logger.warning(f"MediaMTX 注销路径失败（忽略）source={source_code}: {e}")


def register_video_sources_api(app):
    """注册视频源管理 API"""
    
    @app.route('/api/video-sources', methods=['GET'])
    def get_video_sources():
        sources = VideoSource.select()
        return jsonify([{
            'id': s.id,
            'name': s.name,
            'enabled': s.enabled,
            'source_code': s.source_code,
            'source_url': s.source_url,
            'source_decode_width': s.source_decode_width,
            'source_decode_height': s.source_decode_height,
            'source_fps': s.source_fps,
            'source_codec': getattr(s, 'source_codec', 'unknown'),
            'buffer_name': s.buffer_name,
            'status': s.status,
            'decoder_pid': s.decoder_pid
        } for s in sources])
    
    @app.route('/api/video-sources/<int:id>', methods=['GET'])
    def get_video_source(id):
        try:
            source = VideoSource.get_by_id(id)
            return jsonify({
                'id': source.id,
                'name': source.name,
                'enabled': source.enabled,
                'source_code': source.source_code,
                'source_url': source.source_url,
                'source_decode_width': source.source_decode_width,
                'source_decode_height': source.source_decode_height,
                'source_fps': source.source_fps,
                'source_codec': getattr(source, 'source_codec', 'unknown'),
                'buffer_name': source.buffer_name,
                'status': source.status,
                'decoder_pid': source.decoder_pid
            })
        except VideoSource.DoesNotExist:
            return jsonify({'error': '视频源不存在'}), 404
    
    @app.route('/api/video-sources', methods=['POST'])
    def create_video_source():
        data = request.json
        try:
            with quota_capacity('video_sources'):
                source = VideoSource.create(
                    name=data['name'],
                    enabled=data.get('enabled', True),
                    source_code=data['source_code'],
                    source_url=data['source_url'],
                    source_decode_width=data.get('source_decode_width', 960),
                    source_decode_height=data.get('source_decode_height', 540),
                    source_fps=data.get('source_fps', 10),
                    source_codec=normalize_video_codec(
                        data.get('source_codec'),
                        allow_unknown=True,
                    ),
                    status=data.get('status', 'STOPPED'),
                    decoder_pid=data.get('decoder_pid')
                )
            _sync_mediamtx_create(source)
            return jsonify({'id': source.id, 'message': '视频源创建成功'}), 201
        except LicenseError as e:
            return jsonify(e.to_dict()), 403
        except Exception as e:
            return jsonify({'error': str(e)}), 400
    
    @app.route('/api/video-sources/<int:id>', methods=['PUT'])
    def update_video_source(id):
        try:
            source = VideoSource.get_by_id(id)
            data = request.json
            source.name = data.get('name', source.name)
            source.enabled = data.get('enabled', source.enabled)
            previous_source_code = source.source_code
            source.source_code = data.get('source_code', source.source_code)
            previous_source_url = source.source_url
            source.source_url = data.get('source_url', source.source_url)
            source.source_decode_width = data.get('source_decode_width', source.source_decode_width)
            source.source_decode_height = data.get('source_decode_height', source.source_decode_height)
            source.source_fps = data.get('source_fps', source.source_fps)
            if 'source_codec' in data:
                source.source_codec = normalize_video_codec(
                    data.get('source_codec'),
                    allow_unknown=True,
                )
            elif source.source_url != previous_source_url:
                source.source_codec = 'unknown'
            source.status = data.get('status', source.status)
            source.decoder_pid = data.get('decoder_pid', source.decoder_pid)
            source.save()

            # 同步 MediaMTX：source_code 变更则注销旧路径；code 或 url 变更则注册新路径
            if source.source_code != previous_source_code:
                _sync_mediamtx_delete(previous_source_code)
            if source.source_code != previous_source_code or source.source_url != previous_source_url:
                _sync_mediamtx_create(source)

            return jsonify({'message': '视频源更新成功'})
        except VideoSource.DoesNotExist:
            return jsonify({'error': '视频源不存在'}), 404
    
    @app.route('/api/video-sources/<int:id>', methods=['DELETE'])
    def delete_video_source(id):
        try:
            source = VideoSource.get_by_id(id)
            deleted_source_code = source.source_code
            source.delete_instance(recursive=True)
            _sync_mediamtx_delete(deleted_source_code)
            return jsonify({'message': '视频源删除成功'})
        except VideoSource.DoesNotExist:
            return jsonify({'error': '视频源不存在'}), 404
        except Exception as e:
            app.logger.error(f"删除视频源失败 (ID={id}): {e}")
            return jsonify({'error': str(e)}), 500
