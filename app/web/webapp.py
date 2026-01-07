import os
import tempfile
import base64
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np
from werkzeug.utils import secure_filename
import subprocess
import json
import threading
import time

from flask import Flask, jsonify, request, render_template, send_file, abort, Response

from app.core.database_models import Algorithm, VideoSource, Alert, MLModel
from app.config import FRAME_SAVE_PATH, SNAPSHOT_SAVE_PATH, VIDEO_SAVE_PATH, MODEL_SAVE_PATH
from app.core.rabbitmq_publisher import publish_alert_to_rabbitmq, format_alert_message
from app.core.window_detector import get_window_detector
from app.setup_database import setup_database

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB for large model files

# 初始化数据库（如果不存在则创建所有表）
try:
    setup_database()
    app.logger.info("数据库初始化完成")
except Exception as e:
    app.logger.error(f"数据库初始化失败: {e}")
    # 不阻止应用启动，让应用继续运行并在后续操作中报告错误

# ========== API 端点 ==========

# Plugin API
@app.route('/api/plugins/modules', methods=['GET'])
def list_plugin_modules():
    """
    返回可用的插件模块列表
    由于系统已迁移到统一脚本接口，始终返回 script_algorithm
    """
    return jsonify({'modules': ['script_algorithm']})

# Algorithm API
@app.route('/api/algorithms', methods=['GET'])
def get_algorithms():
    algorithms = Algorithm.select()
    result = []
    for a in algorithms:
        algo_dict = {
            'id': a.id,
            'name': a.name,
            'script_path': a.script_path,
            'script_config': a.script_config,
            'detector_template_id': a.detector_template_id,
            'interval_seconds': a.interval_seconds,
            'runtime_timeout': a.runtime_timeout,
            'memory_limit_mb': a.memory_limit_mb,
            'label_name': a.label_name,
            'label_color': a.label_color,
            'enable_window_check': a.enable_window_check,
            'window_size': a.window_size,
            'window_mode': a.window_mode,
            'window_threshold': a.window_threshold
        }
        # 安全地添加可选字段
        algo_dict['plugin_module'] = getattr(a, 'plugin_module', 'script_algorithm')
        algo_dict['ext_config_json'] = getattr(a, 'ext_config_json', '{}')
        algo_dict['entry_function'] = getattr(a, 'entry_function', 'process')
        result.append(algo_dict)
    return jsonify(result)

@app.route('/api/algorithms/<int:id>', methods=['GET'])
def get_algorithm(id):
    try:
        algorithm = Algorithm.get_by_id(id)
        algo_dict = {
            'id': algorithm.id,
            'name': algorithm.name,
            'script_path': algorithm.script_path,
            'script_config': algorithm.script_config,
            'detector_template_id': algorithm.detector_template_id,
            'interval_seconds': algorithm.interval_seconds,
            'runtime_timeout': algorithm.runtime_timeout,
            'memory_limit_mb': algorithm.memory_limit_mb,
            'label_name': algorithm.label_name,
            'label_color': algorithm.label_color,
            'enable_window_check': algorithm.enable_window_check,
            'window_size': algorithm.window_size,
            'window_mode': algorithm.window_mode,
            'window_threshold': algorithm.window_threshold
        }
        # 安全地添加可选字段
        algo_dict['plugin_module'] = getattr(algorithm, 'plugin_module', 'script_algorithm')
        algo_dict['ext_config_json'] = getattr(algorithm, 'ext_config_json', '{}')
        algo_dict['entry_function'] = getattr(algorithm, 'entry_function', 'process')
        return jsonify(algo_dict)
    except Algorithm.DoesNotExist:
        return jsonify({'error': 'Algorithm not found'}), 404

@app.route('/api/algorithms', methods=['POST'])
def create_algorithm():
    data = request.json
    try:
        # 验证必填字段
        if not data.get('script_path'):
            return jsonify({'error': '缺少必填字段: script_path'}), 400
        
        # 创建算法
        algorithm = Algorithm.create(
            name=data['name'],
            script_path=data['script_path'],
            script_config=data.get('script_config', '{}'),
            detector_template_id=data.get('detector_template_id'),
            interval_seconds=data.get('interval_seconds', 1),
            runtime_timeout=data.get('runtime_timeout', 30),
            memory_limit_mb=data.get('memory_limit_mb', 512),
            enable_window_check=data.get('enable_window_check', False),
            window_size=data.get('window_size', 30),
            window_mode=data.get('window_mode', 'ratio'),
            window_threshold=data.get('window_threshold', 0.3),
            label_name=data.get('label_name', 'Object'),
            label_color=data.get('label_color', '#FF0000')
        )
        
        return jsonify({'id': algorithm.id, 'message': 'Algorithm created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/algorithms/<int:id>', methods=['PUT'])
def update_algorithm(id):
    try:
        algorithm = Algorithm.get_by_id(id)
        data = request.json

        import json

        # 更新基本字段
        algorithm.name = data.get('name', algorithm.name)
        algorithm.interval_seconds = data.get('interval_seconds', algorithm.interval_seconds)
        algorithm.label_name = data.get('label_name', algorithm.label_name)
        algorithm.enable_window_check = data.get('enable_window_check', algorithm.enable_window_check)
        algorithm.window_size = data.get('window_size', algorithm.window_size)
        algorithm.window_mode = data.get('window_mode', algorithm.window_mode)
        algorithm.window_threshold = data.get('window_threshold', algorithm.window_threshold)

        # 脚本相关字段
        if 'script_path' in data:
            algorithm.script_path = data['script_path']
        if 'runtime_timeout' in data:
            algorithm.runtime_timeout = data['runtime_timeout']
        if 'memory_limit_mb' in data:
            algorithm.memory_limit_mb = data['memory_limit_mb']

        # 处理扩展字段（如果数据库中有这些字段就更新，否则跳过）
        ext_fields = ['plugin_module', 'ext_config_json', 'model_json', 'model_ids', 'script_type', 'entry_function']
        for field in ext_fields:
            if field in data and hasattr(algorithm, field):
                try:
                    setattr(algorithm, field, data[field])
                except Exception as e:
                    app.logger.warning(f"无法更新字段 {field}: {e}")

        algorithm.save()

        return jsonify({'message': 'Algorithm updated'})
    except Algorithm.DoesNotExist:
        return jsonify({'error': 'Algorithm not found'}), 404
    except Exception as e:
        app.logger.error(f"更新算法失败 (ID={id}): {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/algorithms/<int:id>', methods=['DELETE'])
def delete_algorithm(id):
    try:
        algorithm = Algorithm.get_by_id(id)
        
        # 减少关联模型的使用计数
        try:
            for model_id in algorithm.model_id_list:
                try:
                    model = MLModel.get_by_id(model_id)
                    model.decrement_usage()
                except MLModel.DoesNotExist:
                    app.logger.warning(f"模型 {model_id} 不存在")
        except Exception as e:
            app.logger.error(f"减少模型使用计数失败: {e}")
        
        # 使用 recursive=True 确保级联删除相关记录
        algorithm.delete_instance(recursive=True)
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


            # 使用统一的脚本算法类
            from app.plugins.script_algorithm import ScriptAlgorithm
            
            # 准备算法配置
            script_config = algorithm.config_dict  # 从 script_config 字段解析
            
            full_config = {
                "id": algorithm.id,
                "name": algorithm.name,
                "label_name": algorithm.label_name,
                "label_color": algorithm.label_color,
                "interval_seconds": algorithm.interval_seconds,
                "source_id": 0,  # 测试模式，使用虚拟视频源ID
                
                # 脚本执行相关配置
                "script_path": algorithm.script_path,
                "entry_function": 'process',
                "runtime_timeout": algorithm.runtime_timeout,
                "memory_limit_mb": algorithm.memory_limit_mb,
            }
            
            # 合并脚本配置
            full_config.update(script_config)
            
            # 创建算法实例并处理图片
            algo_instance = ScriptAlgorithm(full_config)
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

# VideoSource API
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

# Alert API
@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    source_id = request.args.get('source_id') or request.args.get('task_id')  # 兼容旧参数
    alert_type = request.args.get('alert_type')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 30))
    
    # 构建查询
    query = Alert.select()
    if source_id:
        query = query.where(Alert.video_source == source_id)
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
            'source_id': a.video_source.id,
            'task_id': a.video_source.id,  # 兼容旧字段
            'workflow_id': a.workflow.id if a.workflow else None,
            'workflow_name': a.workflow.name if a.workflow else None,
            'alert_time': a.alert_time.isoformat(),
            'alert_type': a.alert_type,
            'alert_message': a.alert_message,
            'alert_image': a.alert_image,
            'alert_image_ori': a.alert_image_ori,
            'alert_video': a.alert_video,
            'detection_count': a.detection_count,
            'window_stats': a.window_stats,
            'detection_images': a.detection_images
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
        source_id = data.get('source_id') or data.get('task_id')  # 兼容旧参数
        workflow_id = data.get('workflow_id')  # 可选的 workflow_id

        # 准备 Alert 创建参数
        alert_params = {
            'video_source': source_id,
            'alert_time': datetime.fromisoformat(data.get('alert_time', datetime.now().isoformat())),
            'alert_type': data['alert_type'],
            'alert_message': data['alert_message'],
            'alert_image': data.get('alert_image'),
            'alert_video': data.get('alert_video')
        }

        # 如果提供了 workflow_id，添加到参数中
        if workflow_id is not None:
            from app.core.database_models import Workflow
            try:
                workflow = Workflow.get_by_id(workflow_id)
                alert_params['workflow'] = workflow
            except Workflow.DoesNotExist:
                return jsonify({'error': f'Workflow {workflow_id} does not exist'}), 400

        alert = Alert.create(**alert_params)
        
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

# ========== 时间窗口检测API ==========

@app.route('/api/window/stats/<int:source_id>/<algorithm_id>', methods=['GET'])
def get_window_stats(source_id, algorithm_id):
    """获取指定视频源和算法的窗口统计信息"""
    try:
        window_detector = get_window_detector()
        stats = window_detector.get_stats(source_id, algorithm_id)
        
        # 判断当前状态
        if stats['config']['enable']:
            mode = stats['config']['mode']
            threshold = stats['config']['threshold']
            
            if mode == 'count':
                passed = stats['detection_count'] >= threshold
            elif mode == 'ratio':
                passed = stats['detection_ratio'] >= threshold
            elif mode == 'consecutive':
                passed = stats['max_consecutive'] >= threshold
            else:
                passed = False
            
            stats['status'] = 'above_threshold' if passed else 'below_threshold'
        else:
            stats['status'] = 'disabled'
        
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/window/stats/<int:source_id>', methods=['GET'])
def get_source_window_stats(source_id):
    """获取指定视频源的所有算法窗口统计信息"""
    try:
        # 注意：此API需要工作流系统来管理视频源和算法的关联
        # 目前返回空列表
        return jsonify([])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/window/memory', methods=['GET'])
def get_window_memory_usage():
    """获取窗口检测器的内存使用情况"""
    try:
        window_detector = get_window_detector()
        memory_info = window_detector.get_memory_usage()
        return jsonify(memory_info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        abort(500, description=f"Error serving video {file_path}: {str(e)}")

# ========== 流检测API ==========

@app.route('/api/stream/detect', methods=['POST'])
def detect_stream_info():
    """检测视频流的分辨率和帧率"""
    try:
        data = request.json
        url = data.get('url')
        
        if not url:
            return jsonify({'success': False, 'error': '缺少URL参数'}), 400
        
        # 使用ffprobe检测流信息
        try:
            # 根据URL类型构建不同的ffprobe命令
            if url.startswith('rtsp://'):
                # RTSP流需要特殊参数
                cmd = [
                    'ffprobe',
                    '-print_format', 'json',
                    '-show_streams',
                    '-select_streams', 'v:0',
                    '-rtsp_transport', 'tcp',  # 使用TCP传输
                    '-timeout', '5000000',   # 5秒超时（微秒）
                    '-analyzeduration', '2000000',  # 分析时长2秒
                    '-probesize', '2000000',  # 探测大小2MB
                    url
                ]
            elif url.startswith('rtmp://'):
                # RTMP流
                cmd = [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_streams',
                    '-select_streams', 'v:0',
                    '-analyzeduration', '1000000',
                    '-probesize', '1000000',
                    url
                ]
            else:
                # HTTP/HTTPS/文件等其他类型
                cmd = [
                    'ffprobe',
                    '-v', 'quiet',
                    '-print_format', 'json',
                    '-show_streams',
                    '-select_streams', 'v:0',
                    '-analyzeduration', '2000000',  # 分析时长2秒
                    '-probesize', '2000000',  # 探测大小2MB
                    url
                ]
            
            # 设置超时时间（15秒，给RTSP流更多时间）
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=15,
                check=False  # 不自动抛出异常，我们手动处理
            )
            
            # 检查stderr是否有错误信息
            if result.returncode != 0:
                error_output = result.stderr.strip() if result.stderr else '未知错误'
                app.logger.error(f"ffprobe失败 (返回码 {result.returncode}): {error_output}")
                
                # 解析常见错误并提供更友好的提示
                if '503 ServerUnavailable' in error_output:
                    return jsonify({'success': False, 'error': 'RTSP服务器不可用（503），请检查摄像头是否在线或是否被其他客户端占用'}), 400
                elif '401 Unauthorized' in error_output or 'Authentication' in error_output:
                    return jsonify({'success': False, 'error': '认证失败，请检查用户名和密码是否正确'}), 400
                elif 'Connection refused' in error_output:
                    return jsonify({'success': False, 'error': '连接被拒绝，请检查IP地址和端口是否正确'}), 400
                elif 'Connection timed out' in error_output or 'timeout' in error_output.lower():
                    return jsonify({'success': False, 'error': '连接超时，请检查网络连接或摄像头是否可达'}), 400
                elif 'No route to host' in error_output:
                    return jsonify({'success': False, 'error': '无法到达主机，请检查网络配置'}), 400
                else:
                    return jsonify({'success': False, 'error': f'ffprobe执行失败: {error_output}'}), 400
            
            # 解析JSON输出
            if not result.stdout.strip():
                return jsonify({'success': False, 'error': 'ffprobe未返回任何数据，请检查URL是否正确'}), 400
                
            probe_data = json.loads(result.stdout)
            
            # 调试信息
            app.logger.info(f"ffprobe成功，流数量: {len(probe_data.get('streams', []))}")
            if result.stderr:
                app.logger.debug(f"ffprobe stderr: {result.stderr}")
            
            if not probe_data.get('streams'):
                # 如果没有找到视频流，尝试获取所有流信息
                if probe_data.get('format'):
                    format_info = probe_data['format']
                    return jsonify({
                        'success': False, 
                        'error': f'未找到视频流。格式信息: {format_info.get("format_name", "unknown")}, 时长: {format_info.get("duration", "unknown")}秒'
                    }), 400
                else:
                    return jsonify({'success': False, 'error': '无法解析流信息，请检查URL是否可访问'}), 400
            
            stream = probe_data['streams'][0]
            
            # 提取分辨率和帧率信息
            width = stream.get('width', 0)
            height = stream.get('height', 0)
            
            # 尝试获取帧率
            fps = 0
            if 'r_frame_rate' in stream:
                # 解析分数形式的帧率，如 "30/1"
                fps_str = stream['r_frame_rate']
                if '/' in fps_str:
                    num, den = fps_str.split('/')
                    fps = float(num) / float(den) if float(den) != 0 else 0
                else:
                    fps = float(fps_str)
            
            # 如果无法获取帧率，尝试使用avg_frame_rate
            if fps == 0 and 'avg_frame_rate' in stream:
                fps_str = stream['avg_frame_rate']
                if '/' in fps_str:
                    num, den = fps_str.split('/')
                    fps = float(num) / float(den) if float(den) != 0 else 0
                else:
                    fps = float(fps_str)
            
            # 如果仍然无法获取帧率，尝试使用fps
            if fps == 0 and 'fps' in stream:
                fps = float(stream['fps'])
            
            if width == 0 or height == 0:
                return jsonify({'success': False, 'error': '无法获取视频分辨率'}), 400
            
            return jsonify({
                'success': True,
                'width': int(width),
                'height': int(height),
                'fps': round(fps, 2) if fps > 0 else 0,
                'codec': stream.get('codec_name', 'unknown'),
                'duration': stream.get('duration', 0)
            })
            
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'error': '检测超时（15秒），请检查URL是否可访问或网络连接是否正常'}), 400
        except FileNotFoundError:
            return jsonify({'success': False, 'error': 'ffprobe未安装，请安装FFmpeg: brew install ffmpeg'}), 400
        except json.JSONDecodeError as e:
            return jsonify({'success': False, 'error': f'解析ffprobe输出失败: {str(e)}'}), 400
        except Exception as e:
            return jsonify({'success': False, 'error': f'检测过程中发生错误: {str(e)}'}), 400
            
    except Exception as e:
        app.logger.error(f"流检测失败: {str(e)}")
        return jsonify({'success': False, 'error': f'检测失败: {str(e)}'}), 500

# ========== 管理后台页面路由 ==========

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/admin/algorithms')
def admin_algorithms():
    return render_template('algorithms.html')

@app.route('/admin/tasks')
@app.route('/admin/video-sources')
def admin_video_sources():
    return render_template('video_sources.html')

@app.route('/admin/alerts')
def admin_alerts():
    return render_template('alerts.html')

@app.route('/admin/gpu-calculator')
def admin_gpu_calculator():
    return render_template('gpu_benchmark.html')

@app.route('/admin/roi-config')
def admin_roi_config():
    return render_template('roi_config.html')

# 添加简短的路由别名
@app.route('/tasks')
@app.route('/video-sources')
def video_sources():
    return render_template('video_sources.html')

@app.route('/algorithms')
def algorithms():
    return render_template('algorithms.html')

@app.route('/algorithm-wizard')
def algorithm_wizard():
    """算法配置向导"""
    return render_template('algorithm_wizard.html')

@app.route('/test-templates')
def test_templates():
    """测试检测器模板API"""
    return render_template('test_templates.html')

@app.route('/alerts')
def alerts():
    return render_template('alerts.html')

@app.route('/roi-config')
def roi_config():
    return render_template('roi_config.html')

@app.route('/alert-wall')
def alert_wall():
    """告警大屏页面"""
    return render_template('alert_wall.html')

# ========== 脚本管理页面路由 ==========

@app.route('/scripts')
def scripts():
    """脚本管理页面"""
    return render_template('scripts.html')

@app.route('/scripts/templates')
def script_templates():
    """脚本模板页面"""
    return render_template('script_templates.html')

@app.route('/admin/scripts')
def admin_scripts():
    """脚本管理页面（管理后台路径）"""
    return render_template('scripts.html')

# ========== 模型管理页面路由 ==========

@app.route('/models')
def models():
    """模型管理页面"""
    return render_template('models.html')

@app.route('/admin/models')
def admin_models():
    """模型管理页面（管理后台路径）"""
    return render_template('models.html')

@app.route('/workflows')
def workflows():
    """工作流管理页面"""
    return render_template('workflows.html')

@app.route('/admin/workflows')
def admin_workflows():
    """工作流管理页面（管理后台路径）"""
    return render_template('workflows.html')

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

# ========== 注册脚本管理API ==========
try:
    from app.web.api.scripts import register_scripts_api
    register_scripts_api(app)
    app.logger.info("脚本管理API已注册")
except ImportError as e:
    app.logger.warning(f"脚本管理API注册失败: {e}")

# ========== 注册模型管理API ==========
try:
    from app.web.api.models import register_models_api
    register_models_api(app)
    app.logger.info("模型管理API已注册")
except ImportError as e:
    app.logger.warning(f"模型管理API注册失败: {e}")

# ========== 注册检测器模板API ==========
try:
    from app.web.api.detector_templates import register_detector_templates_api
    register_detector_templates_api(app)
    app.logger.info("检测器模板API已注册")
except ImportError as e:
    app.logger.warning(f"检测器模板API注册失败: {e}")

# ========== 注册工作流管理API ==========
try:
    from app.web.api.workflows import register_workflows_api
    register_workflows_api(app)
    app.logger.info("工作流管理API已注册")
    
    # 自动初始化系统模板（如果不存在）
    try:
        from app.core.database_models import DetectorTemplate
        
        # 检查是否已有系统模板
        existing_templates = DetectorTemplate.select().where(DetectorTemplate.is_system == True).count()
        
        if existing_templates == 0:
            app.logger.info("检测到没有系统模板，开始自动初始化...")
            
            # 调用初始化逻辑（为了避免循环导入，这里使用 with app.test_client()）
            with app.test_client() as client:
                response = client.post('/api/detector-templates/init-system-templates')
                if response.status_code == 200:
                    data = response.get_json()
                    app.logger.info(f"系统模板初始化成功: 创建 {data.get('created', 0)} 个, 更新 {data.get('updated', 0)} 个")
                else:
                    app.logger.warning(f"系统模板初始化失败: {response.status_code}")
        else:
            app.logger.info(f"已存在 {existing_templates} 个系统模板，跳过初始化")
    except Exception as e:
        app.logger.warning(f"系统模板自动初始化失败: {e}")
        
except ImportError as e:
    app.logger.warning(f"检测器模板API注册失败: {e}")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)