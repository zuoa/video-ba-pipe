"""
工作流管理 API
"""
import json
from datetime import datetime
from copy import deepcopy
from flask import jsonify, request

from app.core.database_models import Workflow, WorkflowNode, VideoSource, Algorithm


def register_workflows_api(app):
    """注册工作流管理 API 路由"""
    
    @app.route('/api/workflows', methods=['GET'])
    def get_workflows():
        """获取所有工作流"""
        try:
            workflows = Workflow.select()
            return jsonify([{
                'id': w.id,
                'name': w.name,
                'description': w.description,
                'workflow_data': w.data_dict,
                'is_active': w.is_active,
                'created_at': w.created_at.isoformat() if w.created_at else None,
                'updated_at': w.updated_at.isoformat() if w.updated_at else None,
                'created_by': w.created_by
            } for w in workflows])
        except Exception as e:
            app.logger.error(f"获取工作流列表失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/<int:id>', methods=['GET'])
    def get_workflow(id):
        """获取单个工作流"""
        try:
            workflow = Workflow.get_by_id(id)
            data_dict = workflow.data_dict
            
            # 确保 workflow_data 包含必需的字段
            if 'nodes' not in data_dict:
                data_dict['nodes'] = []
            if 'connections' not in data_dict:
                data_dict['connections'] = []
            
            app.logger.info(f"加载工作流 {id} 数据: nodes={len(data_dict.get('nodes', []))}, connections={len(data_dict.get('connections', []))}")
            app.logger.debug(f"原始数据: {workflow.workflow_data[:500] if workflow.workflow_data else 'None'}")
            
            return jsonify({
                'id': workflow.id,
                'name': workflow.name,
                'description': workflow.description,
                'workflow_data': data_dict,
                'is_active': workflow.is_active,
                'created_at': workflow.created_at.isoformat() if workflow.created_at else None,
                'updated_at': workflow.updated_at.isoformat() if workflow.updated_at else None,
                'created_by': workflow.created_by
            })
        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"获取工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows', methods=['POST'])
    def create_workflow():
        """创建工作流"""
        try:
            data = request.json
            
            if not data.get('name'):
                return jsonify({'error': '缺少必填字段: name'}), 400
            
            workflow = Workflow.create(
                name=data['name'],
                description=data.get('description', ''),
                workflow_data=json.dumps(data.get('workflow_data', {})),
                is_active=data.get('is_active', False),
                created_at=datetime.now(),
                updated_at=datetime.now(),
                created_by=data.get('created_by', 'admin')
            )
            
            return jsonify({
                'id': workflow.id,
                'message': '工作流创建成功'
            }), 201
        except Exception as e:
            app.logger.error(f"创建工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/<int:id>', methods=['PUT'])
    def update_workflow(id):
        """更新工作流"""
        try:
            workflow = Workflow.get_by_id(id)
            data = request.json
            
            if 'name' in data:
                workflow.name = data['name']
            if 'description' in data:
                workflow.description = data['description']
            if 'workflow_data' in data:
                workflow_data_str = json.dumps(data['workflow_data'])
                app.logger.info(f"保存工作流 {id} 数据: nodes={len(data['workflow_data'].get('nodes', []))}, connections={len(data['workflow_data'].get('connections', []))}")
                app.logger.debug(f"工作流数据内容: {workflow_data_str[:500]}")
                workflow.workflow_data = workflow_data_str
            if 'is_active' in data:
                workflow.is_active = data['is_active']
            
            workflow.updated_at = datetime.now()
            workflow.save()
            
            return jsonify({'message': '工作流更新成功'})
        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"更新工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/<int:id>', methods=['DELETE'])
    def delete_workflow(id):
        """删除工作流"""
        try:
            workflow = Workflow.get_by_id(id)
            workflow.delete_instance(recursive=True)
            return jsonify({'message': '工作流删除成功'})
        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"删除工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/<int:id>/activate', methods=['POST'])
    def activate_workflow(id):
        """激活工作流（将工作流配置应用到实际任务）"""
        try:
            workflow = Workflow.get_by_id(id)
            workflow_data = workflow.data_dict
            
            # 这里可以添加逻辑：根据工作流配置创建实际的Task和Algorithm关联
            # 暂时只是标记为激活
            workflow.is_active = True
            workflow.save()
            
            return jsonify({'message': '工作流激活成功'})
        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"激活工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/<int:id>/deactivate', methods=['POST'])
    def deactivate_workflow(id):
        """停用工作流"""
        try:
            workflow = Workflow.get_by_id(id)
            workflow.is_active = False
            workflow.save()
            
            return jsonify({'message': '工作流已停用'})
        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"停用工作流失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/resources', methods=['GET'])
    def get_workflow_resources():
        """获取可用于工作流的资源（视频源、算法等）"""
        try:
            sources = VideoSource.select()
            algorithms = Algorithm.select()
            
            return jsonify({
                'sources': [{
                    'id': s.id,
                    'name': s.name,
                    'source_code': s.source_code,
                    'source_url': s.source_url,
                    'status': s.status
                } for s in sources],
                'algorithms': [{
                    'id': a.id,
                    'name': a.name,
                    'label_name': a.label_name,
                    'script_path': a.script_path
                } for a in algorithms]
            })
        except Exception as e:
            app.logger.error(f"获取工作流资源失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/workflows/capture_frame/<int:source_id>', methods=['GET'])
    def capture_frame_from_source(source_id):
        """从指定视频源捕获当前帧"""
        import os
        import base64
        import cv2
        
        def capture_with_timeout(source_url, timeout=10):
            """带超时的视频帧捕获"""
            cap = cv2.VideoCapture(source_url)
            
            # 设置超时参数（毫秒）
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout * 1000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout * 1000)
            
            if not cap.isOpened():
                return None, None, "无法打开视频源"
            
            ret, frame = cap.read()
            cap.release()
            
            if not ret or frame is None:
                return None, None, "无法读取视频帧"
            
            return ret, frame, None
        
        try:
            source = VideoSource.get_by_id(source_id)
            
            # 优先级1: 如果视频源正在运行，优先读取快照（最快且不占用连接）
            if source.status == 'RUNNING':
                snapshot_path = os.path.join('app/data/snapshots', f'{source.source_code}.jpg')
                if os.path.exists(snapshot_path):
                    try:
                        with open(snapshot_path, 'rb') as f:
                            image_data = f.read()
                            image_base64 = base64.b64encode(image_data).decode('utf-8')
                            
                        app.logger.info(f"成功读取快照: {source.name}")
                        return jsonify({
                            'success': True,
                            'image': f'data:image/jpeg;base64,{image_base64}',
                            'source': 'snapshot',
                            'source_name': source.name,
                            'message': '从运行中的视频源快照读取'
                        })
                    except Exception as e:
                        app.logger.warning(f"读取快照失败: {e}，尝试直接连接")
            
            # 优先级2: 视频源未运行或快照不存在，尝试直接连接（带超时和重试）
            app.logger.info(f"尝试直接连接视频源: {source.name} ({source.source_url})")
            
            # 检查是否为RTSP流
            is_rtsp = source.source_url.startswith('rtsp://')
            timeout = 5 if is_rtsp else 10
            
            ret, frame, error = capture_with_timeout(source.source_url, timeout)
            
            if error:
                error_msg = error
                if is_rtsp:
                    error_msg = (
                        f"RTSP连接失败: {error}\n"
                        f"建议: \n"
                        f"1. 如果该视频源配置正确，请启动它\n"
                        f"2. 启动后可从快照读取（更快且不占用连接数）\n"
                        f"3. 或使用'上传图片'方式进行测试"
                    )
                
                app.logger.error(f"捕获帧失败 [{source.name}]: {error}")
                return jsonify({
                    'error': error_msg,
                    'source_status': source.status,
                    'is_rtsp': is_rtsp
                }), 400
            
            # 成功捕获，编码并返回
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            image_base64 = base64.b64encode(buffer).decode('utf-8')
            
            app.logger.info(f"成功捕获帧: {source.name} [{frame.shape[1]}x{frame.shape[0]}]")
            return jsonify({
                'success': True,
                'image': f'data:image/jpeg;base64,{image_base64}',
                'source': 'direct',
                'source_name': source.name,
                'resolution': f'{frame.shape[1]}x{frame.shape[0]}',
                'message': '通过临时连接获取'
            })
            
        except VideoSource.DoesNotExist:
            return jsonify({'error': '视频源不存在'}), 404
        except cv2.error as e:
            error_detail = str(e)
            app.logger.error(f"OpenCV错误: {error_detail}")
            
            # 识别常见的RTSP错误
            suggestion = ""
            if "503" in error_detail or "ServerUnavailable" in error_detail:
                suggestion = (
                    "RTSP服务器不可用，可能原因:\n"
                    "1. 连接数已满（请先启动任务，从快照读取）\n"
                    "2. 服务器正在重启或维护\n"
                    "3. 认证失败（检查URL中的用户名密码）\n"
                    "4. 网络问题（检查网络连接）"
                )
            elif "timeout" in error_detail.lower():
                suggestion = "连接超时，请检查网络和RTSP服务器状态"
            
            return jsonify({
                'error': f'捕获失败: {error_detail}',
                'suggestion': suggestion
            }), 500
        except Exception as e:
            app.logger.error(f"捕获帧失败: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'捕获帧失败: {str(e)}'}), 500

    # ==================== 批量复制相关 API ====================

    def generate_workflow_name(source: VideoSource, workflow_data: dict, template_name: str = '') -> str:
        """
        智能生成工作流名称

        规则：
        1. 提取算法节点信息
        2. 组合：{视频源名称}-{算法描述}
        3. 如果名称过长，截断算法部分
        """
        # 提取所有 algorithm 节点
        algo_nodes = [n for n in workflow_data.get('nodes', []) if n.get('type') == 'algorithm']

        if not algo_nodes:
            return f"{source.name}-检测流程"

        # 提取算法名称
        algo_names = []
        for node in algo_nodes:
            # 优先级：label > name > data.name > data.algorithmName
            label = (node.get('label', '') or node.get('name', '') or '').strip()
            if not label:
                data = node.get('data', {})
                label = data.get('name', data.get('algorithmName', '算法'))
            algo_names.append(label)

        # 组合算法名称
        if len(algo_names) == 1:
            algo_desc = algo_names[0]
        elif len(algo_names) == 2:
            algo_desc = "+".join(algo_names)
        else:
            algo_desc = f"{algo_names[0]}等{len(algo_names)}个算法"

        # 生成最终名称
        full_name = f"{source.name}-{algo_desc}"

        # 长度限制
        if len(full_name) > 50:
            max_algo_len = 50 - len(source.name) - 1
            algo_desc = algo_desc[:max_algo_len] + "..."
            full_name = f"{source.name}-{algo_desc}"

        return full_name

    @app.route('/api/workflows/<int:workflow_id>/batch-copy', methods=['POST'])
    def batch_copy_workflow(workflow_id):
        """批量复制工作流到多个视频源"""
        try:
            data = request.json
            source_ids = data.get('source_ids', [])

            if not source_ids:
                return jsonify({'error': '请选择要应用的视频源'}), 400

            # 读取模板工作流
            template = Workflow.get_by_id(workflow_id)
            template_data = template.data_dict

            results = []
            errors = []

            for source_id in source_ids:
                try:
                    source = VideoSource.get_by_id(source_id)

                    # 深拷贝 workflow_data
                    new_data = deepcopy(template_data)

                    # 替换 source 节点的 dataId
                    for node in new_data.get('nodes', []):
                        if node.get('type') == 'source':
                            # 同时更新两个可能的位置，确保兼容性
                            node['dataId'] = source_id
                            if 'data' in node and isinstance(node['data'], dict):
                                node['data']['dataId'] = source_id

                    # 生成名称
                    name = generate_workflow_name(source, new_data, template.name)

                    # 创建新工作流（默认不激活）
                    new_workflow = Workflow.create(
                        name=name,
                        description=f"从 '{template.name}' 复制",
                        workflow_data=json.dumps(new_data),
                        is_active=False,  # 默认不激活
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                        created_by='admin'
                    )

                    results.append({
                        'workflow_id': new_workflow.id,
                        'source_id': source.id,
                        'name': name,
                        'source_name': source.name,
                        'is_active': False
                    })

                except Exception as e:
                    errors.append({
                        'source_id': source_id,
                        'error': str(e)
                    })

            return jsonify({
                'success': True,
                'template': {
                    'id': template.id,
                    'name': template.name
                },
                'created': results,
                'errors': errors,
                'summary': {
                    'total': len(source_ids),
                    'success': len(results),
                    'failed': len(errors)
                }
            })

        except Workflow.DoesNotExist:
            return jsonify({'error': '工作流不存在'}), 404
        except Exception as e:
            app.logger.error(f"批量复制工作流失败: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workflows/batch-activate', methods=['POST'])
    def batch_activate_workflows():
        """批量激活工作流"""
        try:
            data = request.json
            workflow_ids = data.get('workflow_ids', [])

            if not workflow_ids:
                return jsonify({'error': '请选择要激活的工作流'}), 400

            success_count = 0
            failed = []

            for workflow_id in workflow_ids:
                try:
                    workflow = Workflow.get_by_id(workflow_id)
                    workflow.is_active = True
                    workflow.updated_at = datetime.now()
                    workflow.save()
                    success_count += 1
                except Exception as e:
                    failed.append({
                        'workflow_id': workflow_id,
                        'error': str(e)
                    })

            return jsonify({
                'success': True,
                'activated': success_count,
                'failed': failed,
                'message': f'成功激活 {success_count} 个工作流'
            })

        except Exception as e:
            app.logger.error(f"批量激活工作流失败: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workflows/batch-deactivate', methods=['POST'])
    def batch_deactivate_workflows():
        """批量停用工作流"""
        try:
            data = request.json
            workflow_ids = data.get('workflow_ids', [])

            if not workflow_ids:
                return jsonify({'error': '请选择要停用的工作流'}), 400

            success_count = 0
            failed = []

            for workflow_id in workflow_ids:
                try:
                    workflow = Workflow.get_by_id(workflow_id)
                    workflow.is_active = False
                    workflow.updated_at = datetime.now()
                    workflow.save()
                    success_count += 1
                except Exception as e:
                    failed.append({
                        'workflow_id': workflow_id,
                        'error': str(e)
                    })

            return jsonify({
                'success': True,
                'deactivated': success_count,
                'failed': failed,
                'message': f'成功停用 {success_count} 个工作流'
            })

        except Exception as e:
            app.logger.error(f"批量停用工作流失败: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/workflows/batch-delete', methods=['POST'])
    def batch_delete_workflows():
        """批量删除工作流"""
        try:
            data = request.json
            workflow_ids = data.get('workflow_ids', [])

            if not workflow_ids:
                return jsonify({'error': '请选择要删除的工作流'}), 400

            success_count = 0
            failed = []

            for workflow_id in workflow_ids:
                try:
                    workflow = Workflow.get_by_id(workflow_id)
                    workflow.delete_instance(recursive=True)
                    success_count += 1
                except Exception as e:
                    failed.append({
                        'workflow_id': workflow_id,
                        'error': str(e)
                    })

            return jsonify({
                'success': True,
                'deleted': success_count,
                'failed': failed,
                'message': f'成功删除 {success_count} 个工作流'
            })

        except Exception as e:
            app.logger.error(f"批量删除工作流失败: {e}")
            return jsonify({'error': str(e)}), 500

