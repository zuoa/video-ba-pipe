"""
工作流测试 API - 模拟真实工作流执行逻辑
用于在Web UI中测试工作流配置
"""
import base64
import json
import time
import traceback
from io import BytesIO
from typing import Dict, List, Any, Optional

import cv2
import numpy as np
from flask import jsonify, request

from app import logger
from app.core.database_models import Workflow, Algorithm, VideoSource
from app.core.workflow_executor import WorkflowExecutor


def register_workflow_test_api(app):
    """注册工作流测试 API 路由"""

    @app.route('/api/workflows/<int:workflow_id>/test', methods=['POST'])
    def test_workflow(workflow_id):
        """
        测试工作流执行

        请求体:
        {
            "image": "base64_encoded_image",
            "format": "base64"  // 可选，默认base64
        }

        返回:
        {
            "success": true,
            "execution_time": 1234,
            "nodes": [
                {
                    "node_id": "node-1",
                    "node_name": "算法节点1",
                    "node_type": "algorithm",
                    "success": true,
                    "execution_time": 100,
                    "data": {
                        "detections": [...],
                        "detection_count": 2,
                        "roi_regions": [...],
                        "roi_applied": true,
                        "logs": [...]
                    },
                    "visual_image": "base64..."
                },
                ...
            ],
            "final_result": {
                "alert_triggered": true,
                "alert_message": "...",
                "total_detections": 2
            },
            "logs": [...]
        }
        """
        try:
            # 获取请求数据
            data = request.json
            if not data:
                return jsonify({'error': '缺少请求体'}), 400

            image_base64 = data.get('image')
            if not image_base64:
                return jsonify({'error': '缺少图片数据'}), 400

            # 加载工作流
            try:
                workflow = Workflow.get_by_id(workflow_id)
            except Workflow.DoesNotExist:
                return jsonify({'error': f'工作流 {workflow_id} 不存在'}), 404

            # 解码图片
            try:
                logger.info(f"[WorkflowTest] 开始解码图片，base64 长度: {len(image_base64)}")

                # 处理可能的 data URL 前缀
                if image_base64.startswith('data:image'):
                    # 格式: data:image/png;base64,iVBORw0KG...
                    image_base64 = image_base64.split(',', 1)[1]
                    logger.info(f"[WorkflowTest] 移除 data URL 前缀后长度: {len(image_base64)}")

                image_bytes = base64.b64decode(image_base64)
                logger.info(f"[WorkflowTest] base64 解码成功，字节长度: {len(image_bytes)}")

                nparr = np.frombuffer(image_bytes, np.uint8)
                logger.info(f"[WorkflowTest] numpy 数组形状: {nparr.shape}")

                image_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if image_bgr is None:
                    logger.error(f"[WorkflowTest] OpenCV 解码失败，无法解码图片数据")
                    return jsonify({'error': '图片解码失败: OpenCV 无法解码'}), 400

                logger.info(f"[WorkflowTest] 图片解码成功，尺寸: {image_bgr.shape}")

                image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
                logger.info(f"[WorkflowTest] 颜色空间转换完成")

            except Exception as e:
                logger.error(f"[WorkflowTest] 图片解码异常: {e}")
                logger.error(traceback.format_exc())
                return jsonify({'error': f'图片解码失败: {str(e)}'}), 400

            # 创建 WorkflowExecutor 并执行测试（测试模式）
            executor = WorkflowExecutor(workflow_id, test_mode=True)

            # 执行测试
            start_time = time.time()
            test_result = executor.test_execute(image_rgb, image_bgr)
            execution_time = int((time.time() - start_time) * 1000)

            test_result['execution_time'] = execution_time
            test_result['workflow_id'] = workflow_id
            test_result['workflow_name'] = workflow.name

            return jsonify(test_result)

        except Exception as e:
            logger.error(f"工作流测试失败: {e}")
            logger.error(traceback.format_exc())
            return jsonify({
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc()
            }), 500


