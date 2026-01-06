from flask import jsonify, request
from app.core.database_models import VideoSource


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
            source = VideoSource.create(
                name=data['name'],
                enabled=data.get('enabled', True),
                source_code=data['source_code'],
                source_url=data['source_url'],
                source_decode_width=data.get('source_decode_width', 960),
                source_decode_height=data.get('source_decode_height', 540),
                source_fps=data.get('source_fps', 10),
                status=data.get('status', 'STOPPED'),
                decoder_pid=data.get('decoder_pid')
            )
            return jsonify({'id': source.id, 'message': '视频源创建成功'}), 201
        except Exception as e:
            return jsonify({'error': str(e)}), 400
    
    @app.route('/api/video-sources/<int:id>', methods=['PUT'])
    def update_video_source(id):
        try:
            source = VideoSource.get_by_id(id)
            data = request.json
            source.name = data.get('name', source.name)
            source.enabled = data.get('enabled', source.enabled)
            source.source_code = data.get('source_code', source.source_code)
            source.source_url = data.get('source_url', source.source_url)
            source.source_decode_width = data.get('source_decode_width', source.source_decode_width)
            source.source_decode_height = data.get('source_decode_height', source.source_decode_height)
            source.source_fps = data.get('source_fps', source.source_fps)
            source.status = data.get('status', source.status)
            source.decoder_pid = data.get('decoder_pid', source.decoder_pid)
            source.save()
            
            return jsonify({'message': '视频源更新成功'})
        except VideoSource.DoesNotExist:
            return jsonify({'error': '视频源不存在'}), 404
    
    @app.route('/api/video-sources/<int:id>', methods=['DELETE'])
    def delete_video_source(id):
        try:
            source = VideoSource.get_by_id(id)
            source.delete_instance(recursive=True)
            return jsonify({'message': '视频源删除成功'})
        except VideoSource.DoesNotExist:
            return jsonify({'error': '视频源不存在'}), 404

