import os
import tempfile
import base64
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np
from werkzeug.utils import secure_filename

from flask import Flask, jsonify, request, render_template, send_file, abort, Response

from app.core.database_models import Algorithm, Task, TaskAlgorithm, Alert
from app.config import FRAME_SAVE_PATH, SNAPSHOT_SAVE_PATH, VIDEO_SAVE_PATH, MODEL_SAVE_PATH
from app.plugin_manager import PluginManager
from app.core.rabbitmq_publisher import publish_alert_to_rabbitmq, format_alert_message

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB for large model files

# ========== API 端点 ==========

# Plugin API
@app.route('/api/plugins/modules', methods=['GET'])
def list_plugin_modules():
    try:
        plugins_path = os.path.join(os.path.dirname(__file__), '..', 'plugins')
        plugin_manager = PluginManager(plugins_path)
        modules = sorted(list(plugin_manager.algorithms_by_module.keys()))
        return jsonify({'modules': modules})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Algorithm API
@app.route('/api/algorithms', methods=['GET'])
def get_algorithms():
    algorithms = Algorithm.select()
    return jsonify([{
        'id': a.id,
        'name': a.name,
        'model_json': a.model_json,
        'interval_seconds': a.interval_seconds,
        'ext_config_json': a.ext_config_json,
        'plugin_module': a.plugin_module,
        'label_name': a.label_name
    } for a in algorithms])

@app.route('/api/algorithms/<int:id>', methods=['GET'])
def get_algorithm(id):
    try:
        algorithm = Algorithm.get_by_id(id)
        return jsonify({
            'id': algorithm.id,
            'name': algorithm.name,
            'model_json': algorithm.model_json,
            'interval_seconds': algorithm.interval_seconds,
            'ext_config_json': algorithm.ext_config_json,
            'plugin_module': algorithm.plugin_module,
            'label_name': algorithm.label_name
        })
    except Algorithm.DoesNotExist:
        return jsonify({'error': 'Algorithm not found'}), 404

@app.route('/api/algorithms', methods=['POST'])
def create_algorithm():
    data = request.json
    try:
        algorithm = Algorithm.create(
            name=data['name'],
            model_json=data.get('model_json', '{}'),
            interval_seconds=data.get('interval_seconds', 1),
            ext_config_json=data.get('ext_config_json', '{}'),
            plugin_module=data.get('plugin_module'),
            label_name=data.get('label_name', 'Object')
        )
        return jsonify({'id': algorithm.id, 'message': 'Algorithm created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/algorithms/<int:id>', methods=['PUT'])
def update_algorithm(id):
    try:
        algorithm = Algorithm.get_by_id(id)
        data = request.json
        algorithm.name = data.get('name', algorithm.name)
        algorithm.model_json = data.get('model_json', algorithm.model_json)
        algorithm.interval_seconds = data.get('interval_seconds', algorithm.interval_seconds)
        algorithm.ext_config_json = data.get('ext_config_json', algorithm.ext_config_json)
        algorithm.plugin_module = data.get('plugin_module', algorithm.plugin_module)
        algorithm.label_name = data.get('label_name', algorithm.label_name)
        algorithm.save()
        return jsonify({'message': 'Algorithm updated'})
    except Algorithm.DoesNotExist:
        return jsonify({'error': 'Algorithm not found'}), 404

@app.route('/api/algorithms/<int:id>', methods=['DELETE'])
def delete_algorithm(id):
    try:
        algorithm = Algorithm.get_by_id(id)
        algorithm.delete_instance()
        return jsonify({'message': 'Algorithm deleted'})
    except Algorithm.DoesNotExist:
        return jsonify({'error': 'Algorithm not found'}), 404

@app.route('/api/algorithms/test', methods=['POST'])
def test_algorithm():
    """算法测试API端点"""
    try:
        # 检查是否有上传的图片
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': '没有上传图片'}), 400
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'success': False, 'error': '没有选择文件'}), 400
        
        # 检查算法ID
        algorithm_id = request.form.get('algorithm_id')
        if not algorithm_id:
            return jsonify({'success': False, 'error': '缺少算法ID'}), 400
        
        try:
            algorithm_id = int(algorithm_id)
        except ValueError:
            return jsonify({'success': False, 'error': '无效的算法ID'}), 400
        
        # 获取算法配置
        try:
            algorithm = Algorithm.get_by_id(algorithm_id)
        except Algorithm.DoesNotExist:
            return jsonify({'success': False, 'error': '算法不存在'}), 404
        
        # 验证文件类型
        if not file.content_type.startswith('image/'):
            return jsonify({'success': False, 'error': '只支持图片文件'}), 400
        
        # 保存上传的图片到临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            file.save(temp_file.name)
            temp_image_path = temp_file.name
        
        try:
            # 读取图片
            image = cv2.imread(temp_image_path)
            if image is None:
                return jsonify({'success': False, 'error': '无法读取图片文件'}), 400

            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

            # 调整图片大小，宽高至少640
            h, w = image.shape[:2]
            if w < 640 or h < 640:
                scale = max(640 / w, 640 / h)
                new_w = int(w * scale)
                new_h = int(h * scale)
                image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                app.logger.info(f"已调整图片大小为: {new_w}x{new_h}")


            # 获取插件管理器实例
            plugins_path = os.path.join(os.path.dirname(__file__), '..', 'plugins')
            plugin_manager = PluginManager(plugins_path)
            
            # 获取算法类（通过模块名查找）
            algorithm_class = plugin_manager.get_algorithm_class_by_module(algorithm.plugin_module)
            if not algorithm_class:
                return jsonify({'success': False, 'error': f'无法找到算法插件: {algorithm.plugin_module}'}), 400


            full_config = {
                "name": algorithm.name,
                "label_name": algorithm.label_name,
                "label_color": algorithm.label_color,
                "ext_config": algorithm.ext_config_json,
                "models_config": algorithm.models_config,
                "interval_seconds": algorithm.interval_seconds,
            }
            
            # 创建算法实例并处理图片
            algo_instance = algorithm_class(full_config)
            results = algo_instance.process(image)
            
            # 处理结果
            detections = results.get('detections', [])
            
            # 生成可视化结果（无论是否有检测结果都生成）
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_result:
                result_image_path = temp_result.name
            
            # 生成可视化结果
            algo_instance.visualize(
                image,
                detections, 
                save_path=result_image_path,
                label_color=full_config.get('label_color', '#FF0000')
            )
            
            # 准备返回结果
            response_data = {
                'success': True,
                'detections': detections,
                'detection_count': len(detections)
            }
            
            # 转换结果图片为base64
            if result_image_path and os.path.exists(result_image_path):
                with open(result_image_path, 'rb') as f:
                    result_image_data = base64.b64encode(f.read()).decode('utf-8')
                    response_data['result_image'] = f'data:image/jpeg;base64,{result_image_data}'
                
                # 清理临时结果文件
                os.unlink(result_image_path)
            
            return jsonify(response_data)
            
        finally:
            # 清理临时上传文件
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
                
    except Exception as e:
        app.logger.error(f"算法测试失败: {str(e)}")
        return jsonify({'success': False, 'error': f'测试失败: {str(e)}'}), 500

# Task API
@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    tasks = Task.select()
    return jsonify([{
        'id': t.id,
        'name': t.name,
        'enabled': t.enabled,
        'source_code': t.source_code,
        'source_name': t.source_name,
        'source_url': t.source_url,
        'buffer_name': t.buffer_name,
        'status': t.status,
        'decoder_pid': t.decoder_pid,
        'ai_pid': t.ai_pid
    } for t in tasks])

@app.route('/api/tasks/<int:id>', methods=['GET'])
def get_task(id):
    try:
        task = Task.get_by_id(id)
        return jsonify({
            'id': task.id,
            'name': task.name,
            'enabled': task.enabled,
            'source_code': task.source_code,
            'source_name': task.source_name,
            'source_url': task.source_url,
            'buffer_name': task.buffer_name,
            'status': task.status,
            'decoder_pid': task.decoder_pid,
            'ai_pid': task.ai_pid
        })
    except Task.DoesNotExist:
        return jsonify({'error': 'Task not found'}), 404

@app.route('/api/tasks', methods=['POST'])
def create_task():
    data = request.json
    try:
        task = Task.create(
            name=data['name'],
            enabled=data.get('enabled', True),
            source_code=data['source_code'],
            source_name=data.get('source_name'),
            source_url=data['source_url'],
            buffer_name=data.get('buffer_name', 'video_buffer'),
            status=data.get('status', 'STOPPED'),
            decoder_pid=data.get('decoder_pid'),
            ai_pid=data.get('ai_pid')
        )
        return jsonify({'id': task.id, 'message': 'Task created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/tasks/<int:id>', methods=['PUT'])
def update_task(id):
    try:
        task = Task.get_by_id(id)
        data = request.json
        task.name = data.get('name', task.name)
        task.enabled = data.get('enabled', task.enabled)
        task.source_code = data.get('source_code', task.source_code)
        task.source_name = data.get('source_name', task.source_name)
        task.source_url = data.get('source_url', task.source_url)
        task.buffer_name = data.get('buffer_name', task.buffer_name)
        task.status = data.get('status', task.status)
        task.decoder_pid = data.get('decoder_pid', task.decoder_pid)
        task.ai_pid = data.get('ai_pid', task.ai_pid)
        task.save()
        return jsonify({'message': 'Task updated'})
    except Task.DoesNotExist:
        return jsonify({'error': 'Task not found'}), 404

@app.route('/api/tasks/<int:id>', methods=['DELETE'])
def delete_task(id):
    try:
        task = Task.get_by_id(id)
        task.delete_instance()
        return jsonify({'message': 'Task deleted'})
    except Task.DoesNotExist:
        return jsonify({'error': 'Task not found'}), 404

# TaskAlgorithm API
@app.route('/api/task-algorithms', methods=['GET'])
def get_task_algorithms():
    tas = TaskAlgorithm.select()
    return jsonify([{
        'id': ta.id,
        'task_id': ta.task.id,
        'algorithm_id': ta.algorithm.id,
        'priority': ta.priority,
        'config_override_json': ta.config_override_json
    } for ta in tas])

@app.route('/api/task-algorithms', methods=['POST'])
def create_task_algorithm():
    data = request.json
    try:
        ta = TaskAlgorithm.create(
            task=data['task_id'],
            algorithm=data['algorithm_id'],
            priority=data.get('priority', 0),
            config_override_json=data.get('config_override_json', '{}')
        )
        return jsonify({'id': ta.id, 'message': 'TaskAlgorithm created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/task-algorithms/<int:id>', methods=['DELETE'])
def delete_task_algorithm(id):
    try:
        ta = TaskAlgorithm.get_by_id(id)
        ta.delete_instance()
        return jsonify({'message': 'TaskAlgorithm deleted'})
    except TaskAlgorithm.DoesNotExist:
        return jsonify({'error': 'TaskAlgorithm not found'}), 404

# Alert API
@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    task_id = request.args.get('task_id')
    alert_type = request.args.get('alert_type')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 30))
    
    # 构建查询
    query = Alert.select()
    if task_id:
        query = query.where(Alert.task == task_id)
    if alert_type:
        query = query.where(Alert.alert_type == alert_type)
    
    # 获取总数
    total = query.count()
    
    # 计算分页
    total_pages = (total + per_page - 1) // per_page
    offset = (page - 1) * per_page
    
    # 获取分页数据
    alerts = query.order_by(Alert.alert_time.desc()).limit(per_page).offset(offset)
    
    return jsonify({
        'data': [{
            'id': a.id,
            'task_id': a.task.id,
            'alert_time': a.alert_time.isoformat(),
            'alert_type': a.alert_type,
            'alert_message': a.alert_message,
            'alert_image': a.alert_image,
            'alert_image_ori': a.alert_image_ori,
            'alert_video': a.alert_video
        } for a in alerts],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': total_pages
        }
    })

@app.route('/api/alerts', methods=['POST'])
def create_alert():
    data = request.json
    try:
        alert = Alert.create(
            task=data['task_id'],
            alert_time=datetime.fromisoformat(data.get('alert_time', datetime.now().isoformat())),
            alert_type=data['alert_type'],
            alert_message=data['alert_message'],
            alert_image=data.get('alert_image'),
            alert_video=data.get('alert_video')
        )
        
        # 发布预警消息到RabbitMQ
        try:
            alert_message = format_alert_message(alert)
            if publish_alert_to_rabbitmq(alert_message):
                print(f"预警消息已成功发布到RabbitMQ: {alert.id}")
            else:
                print(f"预警消息发布到RabbitMQ失败: {alert.id}")
        except Exception as e:
            print(f"发布预警消息到RabbitMQ时发生错误: {e}")
        
        return jsonify({'id': alert.id, 'message': 'Alert created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/alert-types', methods=['GET'])
def get_alert_types():
    """获取所有可用的告警类型"""
    alert_types = Alert.select(Alert.alert_type).distinct().order_by(Alert.alert_type)
    return jsonify([at.alert_type for at in alert_types])

@app.route('/api/alerts/today-count', methods=['GET'])
def get_today_alerts_count():
    """获取今日告警数量"""
    from datetime import datetime, date
    
    # 获取今天的开始和结束时间
    today = date.today()
    start_of_day = datetime.combine(today, datetime.min.time())
    end_of_day = datetime.combine(today, datetime.max.time())
    
    # 查询今日告警数量
    count = Alert.select().where(
        (Alert.alert_time >= start_of_day) & 
        (Alert.alert_time <= end_of_day)
    ).count()
    
    return jsonify({'count': count})

# ========== 图片服务接口 ==========

@app.route('/api/image/<path:file_path>', methods=['GET'])
def get_image(file_path):
    """
    安全的图片返回接口
    支持的图片路径：
    - frames/ 下的帧图片
    - snapshots/ 下的快照图片
    """
    try:
        # 定义允许访问的基础路径
        allowed_bases = {
            'frames': FRAME_SAVE_PATH,
            'snapshots': SNAPSHOT_SAVE_PATH
        }
        
        # 解析路径的第一部分作为基础目录类型
        path_parts = file_path.split('/', 1)
        if len(path_parts) < 2:
            abort(400, description="Invalid path format")
            
        base_type, relative_path = path_parts
        
        # 检查基础路径是否被允许
        if base_type not in allowed_bases:
            abort(403, description="Access to this directory is not allowed")
            
        base_path = allowed_bases[base_type]
        
        # 构建完整的文件路径
        full_path = os.path.join(base_path, relative_path)
        
        # 规范化路径，防止路径遍历攻击
        full_path = os.path.abspath(full_path)
        base_path = os.path.abspath(base_path)
        
        # 确保请求的文件在允许的基础路径内
        if not full_path.startswith(base_path + os.sep) and full_path != base_path:
            abort(403, description="Path traversal detected")
            
        # 检查文件是否存在
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            abort(404, description="Image not found")
            
        # 验证文件扩展名（只允许常见的图片格式）
        allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff'}
        file_ext = Path(full_path).suffix.lower()
        if file_ext not in allowed_extensions:
            abort(400, description="File type not supported")
            
        # 返回图片文件
        return send_file(full_path, as_attachment=False)
        
    except Exception as e:
        app.logger.error(f"Error serving image {file_path}: {str(e)}")
        abort(500, description="Internal server error")

@app.route('/api/video/<path:file_path>', methods=['GET'])
def get_video(file_path):
    """
    安全的视频返回接口
    支持的视频路径：
    - videos/ 下的视频文件
    支持 Range 请求以便视频播放器可以 seek
    """
    try:
        # 定义允许访问的基础路径（主要是 videos 目录）
        allowed_bases = {
            'videos': VIDEO_SAVE_PATH
        }
        
        # 解析路径的第一部分作为基础目录类型
        path_parts = file_path.split('/', 1)
        if len(path_parts) < 2:
            abort(400, description="Invalid path format")
            
        base_type, relative_path = path_parts
        
        # 检查基础路径是否被允许
        if base_type not in allowed_bases:
            abort(403, description="Access to this directory is not allowed")
            
        base_path = allowed_bases[base_type]
        
        # 构建完整的文件路径
        full_path = os.path.join(base_path, relative_path)
        
        # 规范化路径，防止路径遍历攻击
        full_path = os.path.abspath(full_path)
        base_path = os.path.abspath(base_path)
        
        # 确保请求的文件在允许的基础路径内
        if not full_path.startswith(base_path + os.sep) and full_path != base_path:
            abort(403, description="Path traversal detected")
            
        # 检查文件是否存在
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            abort(404, description="Video not found")
            
        # 验证文件扩展名（只允许常见的视频格式）
        allowed_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
        file_ext = Path(full_path).suffix.lower()
        if file_ext not in allowed_extensions:
            abort(400, description="File type not supported")
        
        # 获取文件大小
        file_size = os.path.getsize(full_path)
        
        # 设置 MIME 类型
        mime_type = 'video/mp4' if file_ext == '.mp4' else f'video/{file_ext[1:]}'
        
        # 处理 Range 请求
        range_header = request.headers.get('Range', None)
        
        if not range_header:
            # 没有 Range 请求，返回整个文件
            with open(full_path, 'rb') as f:
                data = f.read()
            
            response = Response(data, 200, mimetype=mime_type)
            response.headers.add('Content-Length', str(file_size))
            response.headers.add('Accept-Ranges', 'bytes')
            return response
        
        # 解析 Range 请求头
        # Range: bytes=start-end
        byte_range = range_header.replace('bytes=', '').strip()
        byte_range_parts = byte_range.split('-')
        
        start = int(byte_range_parts[0]) if byte_range_parts[0] else 0
        end = int(byte_range_parts[1]) if byte_range_parts[1] else file_size - 1
        
        # 确保 end 不超过文件大小
        if end >= file_size:
            end = file_size - 1
        
        # 计算内容长度
        content_length = end - start + 1
        
        # 读取指定范围的数据
        with open(full_path, 'rb') as f:
            f.seek(start)
            data = f.read(content_length)
        
        # 创建 206 Partial Content 响应
        response = Response(data, 206, mimetype=mime_type)
        response.headers.add('Content-Range', f'bytes {start}-{end}/{file_size}')
        response.headers.add('Accept-Ranges', 'bytes')
        response.headers.add('Content-Length', str(content_length))
        response.headers.add('Cache-Control', 'no-cache')
        
        return response
        
    except Exception as e:
        app.logger.error(f"Error serving video {file_path}: {str(e)}")
        abort(500, description="Internal server error")

# ========== 管理后台页面路由 ==========

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/admin/algorithms')
def admin_algorithms():
    return render_template('algorithms.html')

@app.route('/admin/tasks')
def admin_tasks():
    return render_template('tasks.html')

@app.route('/admin/alerts')
def admin_alerts():
    return render_template('alerts.html')

@app.route('/api/upload/model', methods=['POST'])
def upload_model_file():
    try:
        if 'model_file' not in request.files:
            return jsonify({'success': False, 'error': '缺少文件字段 model_file'}), 400
        file = request.files['model_file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '未选择文件'}), 400

        # 安全的文件名
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({'success': False, 'error': '无效的文件名'}), 400

        # 允许的扩展名
        allowed_exts = {'.pt', '.onnx', '.engine', '.bin', '.tflite', '.xml', '.param', '.json'}
        ext = os.path.splitext(filename)[1].lower()
        if ext not in allowed_exts:
            return jsonify({'success': False, 'error': '不支持的文件类型'}), 400

        # 创建存储路径（按日期分目录）
        date_dir = datetime.now().strftime('%Y%m%d')
        save_dir = os.path.join(MODEL_SAVE_PATH, date_dir)
        os.makedirs(save_dir, exist_ok=True)

        # 防止重名覆盖
        base, extname = os.path.splitext(filename)
        counter = 0
        final_name = filename
        while os.path.exists(os.path.join(save_dir, final_name)):
            counter += 1
            final_name = f"{base}_{counter}{extname}"

        save_path = os.path.join(save_dir, final_name)
        file.save(save_path)

        return jsonify({'success': True, 'saved_path': save_path})
    except Exception as e:
        app.logger.error(f"模型上传失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)