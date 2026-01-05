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

from app.core.database_models import Algorithm, Task, TaskAlgorithm, Alert, MLModel
from app.config import FRAME_SAVE_PATH, SNAPSHOT_SAVE_PATH, VIDEO_SAVE_PATH, MODEL_SAVE_PATH
from app.plugin_manager import PluginManager
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
    try:
        plugins_path = os.path.join(os.path.dirname(__file__), '..', 'plugins')
        plugin_manager = PluginManager(plugins_path)
        modules = sorted(list(plugin_manager.algorithms_by_module.keys()))

        # 确保 script_algorithm 始终在列表中
        if 'script_algorithm' not in modules:
            modules.append('script_algorithm')
            modules = sorted(modules)

        return jsonify({'modules': modules})
    except Exception as e:
        # 即使出错也返回 script_algorithm
        return jsonify({'modules': ['script_algorithm']})

# Algorithm API
@app.route('/api/algorithms', methods=['GET'])
def get_algorithms():
    algorithms = Algorithm.select()
    return jsonify([{
        'id': a.id,
        'name': a.name,
        'model_json': a.model_json,
        'model_ids': getattr(a, 'model_ids', '[]'),  # 模型ID列表
        'interval_seconds': a.interval_seconds,
        'ext_config_json': a.ext_config_json,
        'plugin_module': a.plugin_module,
        'label_name': a.label_name,
        'enable_window_check': a.enable_window_check,
        'window_size': a.window_size,
        'window_mode': a.window_mode,
        'window_threshold': a.window_threshold,
        # 脚本相关字段
        'script_type': getattr(a, 'script_type', 'plugin'),
        'script_path': getattr(a, 'script_path', None),
        'entry_function': getattr(a, 'entry_function', 'process'),
        'runtime_timeout': getattr(a, 'runtime_timeout', 30),
        'memory_limit_mb': getattr(a, 'memory_limit_mb', 512)
    } for a in algorithms])

@app.route('/api/algorithms/<int:id>', methods=['GET'])
def get_algorithm(id):
    try:
        algorithm = Algorithm.get_by_id(id)
        return jsonify({
            'id': algorithm.id,
            'name': algorithm.name,
            'model_json': algorithm.model_json,
            'model_ids': getattr(algorithm, 'model_ids', '[]'),  # 模型ID列表
            'interval_seconds': algorithm.interval_seconds,
            'ext_config_json': algorithm.ext_config_json,
            'plugin_module': algorithm.plugin_module,
            'label_name': algorithm.label_name,
            'enable_window_check': algorithm.enable_window_check,
            'window_size': algorithm.window_size,
            'window_mode': algorithm.window_mode,
            'window_threshold': algorithm.window_threshold,
            # 脚本相关字段
            'script_type': getattr(algorithm, 'script_type', 'plugin'),
            'script_path': getattr(algorithm, 'script_path', None),
            'entry_function': getattr(algorithm, 'entry_function', 'process'),
            'runtime_timeout': getattr(algorithm, 'runtime_timeout', 30),
            'memory_limit_mb': getattr(algorithm, 'memory_limit_mb', 512)
        })
    except Algorithm.DoesNotExist:
        return jsonify({'error': 'Algorithm not found'}), 404

@app.route('/api/algorithms', methods=['POST'])
def create_algorithm():
    data = request.json
    try:
        # 创建算法
        algorithm = Algorithm.create(
            name=data['name'],
            model_json=data.get('model_json', '{}'),
            model_ids=data.get('model_ids', '[]'),  # 模型ID列表
            interval_seconds=data.get('interval_seconds', 1),
            ext_config_json=data.get('ext_config_json', '{}'),
            plugin_module=data.get('plugin_module'),
            label_name=data.get('label_name', 'Object'),
            enable_window_check=data.get('enable_window_check', False),
            window_size=data.get('window_size', 30),
            window_mode=data.get('window_mode', 'ratio'),
            window_threshold=data.get('window_threshold', 0.3),
            # 脚本相关字段
            script_type=data.get('script_type', 'plugin'),
            script_path=data.get('script_path'),
            entry_function=data.get('entry_function', 'process'),
            runtime_timeout=data.get('runtime_timeout', 30),
            memory_limit_mb=data.get('memory_limit_mb', 512)
        )
        
        # 更新模型使用计数
        try:
            import json
            model_ids = json.loads(data.get('model_ids', '[]'))
            for model_id in model_ids:
                try:
                    model = MLModel.get_by_id(model_id)
                    model.increment_usage()
                except MLModel.DoesNotExist:
                    app.logger.warning(f"模型 {model_id} 不存在")
        except Exception as e:
            app.logger.error(f"更新模型使用计数失败: {e}")
        
        return jsonify({'id': algorithm.id, 'message': 'Algorithm created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/algorithms/<int:id>', methods=['PUT'])
def update_algorithm(id):
    try:
        algorithm = Algorithm.get_by_id(id)
        data = request.json
        
        # 处理模型ID的变更
        import json
        if 'model_ids' in data:
            old_model_ids = set(algorithm.model_id_list)
            new_model_ids = set(json.loads(data.get('model_ids', '[]')))
            
            # 计算需要增加和减少使用计数的模型
            added_models = new_model_ids - old_model_ids
            removed_models = old_model_ids - new_model_ids
            
            # 增加新添加模型的使用计数
            for model_id in added_models:
                try:
                    model = MLModel.get_by_id(model_id)
                    model.increment_usage()
                except MLModel.DoesNotExist:
                    app.logger.warning(f"模型 {model_id} 不存在")
            
            # 减少移除模型的使用计数
            for model_id in removed_models:
                try:
                    model = MLModel.get_by_id(model_id)
                    model.decrement_usage()
                except MLModel.DoesNotExist:
                    app.logger.warning(f"模型 {model_id} 不存在")
            
            algorithm.model_ids = data['model_ids']
        
        # 更新其他字段
        algorithm.name = data.get('name', algorithm.name)
        algorithm.model_json = data.get('model_json', algorithm.model_json)
        algorithm.interval_seconds = data.get('interval_seconds', algorithm.interval_seconds)
        algorithm.ext_config_json = data.get('ext_config_json', algorithm.ext_config_json)
        algorithm.plugin_module = data.get('plugin_module', algorithm.plugin_module)
        algorithm.label_name = data.get('label_name', algorithm.label_name)
        algorithm.enable_window_check = data.get('enable_window_check', algorithm.enable_window_check)
        algorithm.window_size = data.get('window_size', algorithm.window_size)
        algorithm.window_mode = data.get('window_mode', algorithm.window_mode)
        algorithm.window_threshold = data.get('window_threshold', algorithm.window_threshold)
        # 脚本相关字段
        if 'script_type' in data:
            algorithm.script_type = data['script_type']
        if 'script_path' in data:
            algorithm.script_path = data['script_path']
        if 'entry_function' in data:
            algorithm.entry_function = data['entry_function']
        if 'runtime_timeout' in data:
            algorithm.runtime_timeout = data['runtime_timeout']
        if 'memory_limit_mb' in data:
            algorithm.memory_limit_mb = data['memory_limit_mb']
        algorithm.save()
        
        # 通知WindowDetector重新加载配置
        try:
            window_detector = get_window_detector()
            # 为使用该算法的所有任务重新加载配置
            task_algorithms = TaskAlgorithm.select().where(TaskAlgorithm.algorithm == id)
            for ta in task_algorithms:
                window_detector.reload_config(ta.task.id, str(id))
        except Exception as e:
            app.logger.warning(f"重新加载窗口配置失败: {e}")
        
        return jsonify({'message': 'Algorithm updated'})
    except Algorithm.DoesNotExist:
        return jsonify({'error': 'Algorithm not found'}), 404

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
        'source_decode_width': t.source_decode_width,
        'source_decode_height': t.source_decode_height,
        'source_fps': t.source_fps,
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
            'source_decode_width': task.source_decode_width,
            'source_decode_height': task.source_decode_height,
            'source_fps': task.source_fps,
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
            source_decode_width=data.get('source_decode_width', 960),
            source_decode_height=data.get('source_decode_height', 540),
            source_fps=data.get('source_fps', 10),
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
        task.source_decode_width = data.get('source_decode_width', task.source_decode_width)
        task.source_decode_height = data.get('source_decode_height', task.source_decode_height)
        task.source_fps = data.get('source_fps', task.source_fps)
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
        # 使用 recursive=True 确保级联删除相关记录（Alert、TaskAlgorithm等）
        task.delete_instance(recursive=True)
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
        'config_override_json': ta.config_override_json,
        'roi_regions': ta.roi_regions
    } for ta in tas])

@app.route('/api/task-algorithms/<int:id>', methods=['GET'])
def get_task_algorithm(id):
    try:
        ta = TaskAlgorithm.get_by_id(id)
        return jsonify({
            'id': ta.id,
            'task_id': ta.task.id,
            'algorithm_id': ta.algorithm.id,
            'priority': ta.priority,
            'config_override_json': ta.config_override_json,
            'roi_regions': ta.roi_regions
        })
    except TaskAlgorithm.DoesNotExist:
        return jsonify({'error': 'TaskAlgorithm not found'}), 404

@app.route('/api/task-algorithms', methods=['POST'])
def create_task_algorithm():
    data = request.json
    try:
        ta = TaskAlgorithm.create(
            task=data['task_id'],
            algorithm=data['algorithm_id'],
            priority=data.get('priority', 0),
            config_override_json=data.get('config_override_json', '{}'),
            roi_regions=data.get('roi_regions', '[]')
        )
        return jsonify({'id': ta.id, 'message': 'TaskAlgorithm created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/task-algorithms/<int:id>', methods=['PUT'])
def update_task_algorithm(id):
    data = request.json
    try:
        ta = TaskAlgorithm.get_by_id(id)
        if 'priority' in data:
            ta.priority = data['priority']
        if 'config_override_json' in data:
            ta.config_override_json = data['config_override_json']
        if 'roi_regions' in data:
            ta.roi_regions = data['roi_regions']
        ta.save()
        return jsonify({'message': 'TaskAlgorithm updated'})
    except TaskAlgorithm.DoesNotExist:
        return jsonify({'error': 'TaskAlgorithm not found'}), 404

@app.route('/api/task-algorithms/<int:id>', methods=['DELETE'])
def delete_task_algorithm(id):
    try:
        ta = TaskAlgorithm.get_by_id(id)
        # 使用 recursive=True 确保级联删除相关记录
        ta.delete_instance(recursive=True)
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

# ========== 时间窗口检测API ==========

@app.route('/api/window/stats/<int:task_id>/<algorithm_id>', methods=['GET'])
def get_window_stats(task_id, algorithm_id):
    """获取指定任务和算法的窗口统计信息"""
    try:
        window_detector = get_window_detector()
        stats = window_detector.get_stats(task_id, algorithm_id)
        
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

@app.route('/api/window/stats/<int:task_id>', methods=['GET'])
def get_task_window_stats(task_id):
    """获取指定任务的所有算法窗口统计信息"""
    try:
        window_detector = get_window_detector()
        
        # 获取该任务的所有算法
        task_algorithms = TaskAlgorithm.select().where(TaskAlgorithm.task == task_id)
        
        result = []
        for ta in task_algorithms:
            algorithm_id = str(ta.algorithm.id)
            stats = window_detector.get_stats(task_id, algorithm_id)
            
            # 添加算法信息
            stats['algorithm_id'] = ta.algorithm.id
            stats['algorithm_name'] = ta.algorithm.name
            
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
            
            result.append(stats)
        
        return jsonify(result)
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
def admin_tasks():
    return render_template('tasks.html')

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
def tasks():
    return render_template('tasks.html')

@app.route('/algorithms')
def algorithms():
    return render_template('algorithms.html')

@app.route('/alerts')
def alerts():
    return render_template('alerts.html')

@app.route('/roi-config')
def roi_config():
    return render_template('roi_config.html')

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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)