"""
工作流测试 API - 模拟真实工作流执行逻辑
用于在 Web UI 中测试工作流配置，并保存测试结果（独立于 Alert）
"""
import base64
import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from flask import jsonify, request

from app import logger
from app.config import FRAME_SAVE_PATH, VIDEO_SAVE_PATH
from app.core.database_models import Workflow, VideoSource, WorkflowTestResult
from app.core.workflow_executor import WorkflowExecutor


def _safe_fromisoformat(value: str) -> Optional[datetime]:
    """兼容 ISO 时间字符串解析（含 Z 后缀）"""
    if not value:
        return None
    try:
        normalized = value.replace('Z', '+00:00')
        return datetime.fromisoformat(normalized)
    except Exception:
        return None


def _decode_base64_image(image_base64: str) -> Tuple[np.ndarray, np.ndarray]:
    """解码 base64 图片，返回 (rgb, bgr)"""
    # 处理 data URL 前缀
    if image_base64.startswith('data:image'):
        image_base64 = image_base64.split(',', 1)[1]

    image_bytes = base64.b64decode(image_base64)
    nparr = np.frombuffer(image_bytes, np.uint8)
    image_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise ValueError('图片解码失败: OpenCV 无法解码')

    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    return image_rgb, image_bgr


def _build_frame_relative_path(workflow_id: int, prefix: str = 'test') -> str:
    now = datetime.now()
    date_dir = now.strftime('%Y%m%d')
    ts = now.strftime('%H%M%S_%f')
    rel_dir = os.path.join('workflow_test', f'workflow_{workflow_id}', date_dir)
    os.makedirs(os.path.join(FRAME_SAVE_PATH, rel_dir), exist_ok=True)
    return os.path.join(rel_dir, f'{prefix}_{ts}.jpg')


def _build_video_relative_path(workflow_id: int, ext: str = '.mp4') -> str:
    now = datetime.now()
    date_dir = now.strftime('%Y%m%d')
    ts = now.strftime('%H%M%S_%f')
    rel_dir = os.path.join('workflow_test', f'workflow_{workflow_id}', date_dir)
    os.makedirs(os.path.join(VIDEO_SAVE_PATH, rel_dir), exist_ok=True)
    clean_ext = ext if ext.startswith('.') else f'.{ext}'
    return os.path.join(rel_dir, f'test_{ts}{clean_ext.lower()}')


def _save_bgr_image(workflow_id: int, image_bgr: np.ndarray, prefix: str = 'test') -> str:
    rel_path = _build_frame_relative_path(workflow_id, prefix=prefix)
    abs_path = os.path.join(FRAME_SAVE_PATH, rel_path)
    if not cv2.imwrite(abs_path, image_bgr):
        raise RuntimeError('保存测试图片失败')
    return rel_path


def _extract_source_from_workflow(workflow: Workflow) -> Optional[VideoSource]:
    """从工作流 source 节点中提取视频源（测试记录可为空）"""
    try:
        workflow_data = workflow.data_dict or {}
        for node in workflow_data.get('nodes', []):
            node_type = (node.get('type') or node.get('data', {}).get('type') or '').lower()
            if node_type not in ('source', 'videosource', 'video_source'):
                continue

            source_id = (
                node.get('dataId')
                or node.get('data_id')
                or node.get('videoSourceId')
                or node.get('data', {}).get('videoSourceId')
                or node.get('data', {}).get('dataId')
                or node.get('data', {}).get('data_id')
            )
            if not source_id:
                continue

            try:
                return VideoSource.get_by_id(int(source_id))
            except Exception:
                logger.warning(f"[WorkflowTest] 工作流 {workflow.id} source_id={source_id} 无效")
                return None
    except Exception as e:
        logger.warning(f"[WorkflowTest] 解析工作流 source 节点失败: {e}")

    return None


def _extract_detection_count(result: Dict[str, Any]) -> int:
    """从测试结果中提取检测数量（取最大值）"""
    max_count = 0
    for node in result.get('nodes', []) or []:
        data = node.get('data') or {}
        cnt = data.get('detection_count')
        if isinstance(cnt, int):
            max_count = max(max_count, cnt)
    return max_count


def _extract_alert_triggered(result: Dict[str, Any]) -> bool:
    for node in result.get('nodes', []) or []:
        node_type = (node.get('node_type') or '').lower()
        if node_type not in ('alert', 'output'):
            continue
        data = node.get('data') or {}
        debug_info = data.get('debug_info') or {}
        if bool(data.get('alert_triggered')) or bool(debug_info.get('alert_triggered')):
            return True
    return False


def _build_result_message(result: Dict[str, Any], media_type: str) -> str:
    if not result.get('success', False):
        return result.get('error') or '测试失败'

    if media_type == 'video':
        summary = result.get('video_summary') or {}
        sampled = summary.get('sampled_frames', 0)
        detected_frames = summary.get('detected_frames', 0)
        total_detections = summary.get('total_detections', 0)
        return f"视频测试完成：抽样 {sampled} 帧，命中 {detected_frames} 帧，总检测 {total_detections}"

    detection_count = _extract_detection_count(result)
    return f"图片测试完成：检测数量 {detection_count}"


def _persist_test_record(
    workflow: Workflow,
    media_type: str,
    result_payload: Dict[str, Any],
    execution_time_ms: int,
    image_path: Optional[str] = None,
    image_ori_path: Optional[str] = None,
    video_path: Optional[str] = None,
    detection_images: Optional[List[Dict[str, Any]]] = None,
    window_stats: Optional[Dict[str, Any]] = None,
) -> WorkflowTestResult:
    """保存测试记录，不写入 Alert 表"""
    source = _extract_source_from_workflow(workflow)

    success = bool(result_payload.get('success', False))
    detection_count = _extract_detection_count(result_payload)

    if media_type == 'video':
        summary = result_payload.get('video_summary') or {}
        detection_count = max(
            detection_count,
            int(summary.get('detected_frames', 0) or 0),
            int(summary.get('total_detections', 0) or 0),
        )

    alert_level = 'info' if success else 'error'
    alert_type = 'workflow_test_video' if media_type == 'video' else 'workflow_test_image'
    alert_message = _build_result_message(result_payload, media_type)

    return WorkflowTestResult.create(
        workflow=workflow,
        video_source=source,
        test_time=datetime.now(),
        media_type=media_type,
        success=success,
        execution_time_ms=execution_time_ms,
        alert_type=alert_type,
        alert_level=alert_level,
        alert_message=alert_message,
        alert_image=image_path,
        alert_image_ori=image_ori_path,
        alert_video=video_path,
        detection_count=detection_count,
        window_stats=json.dumps(window_stats, ensure_ascii=False) if window_stats else None,
        detection_images=json.dumps(detection_images, ensure_ascii=False) if detection_images else None,
        result_json=json.dumps(result_payload, ensure_ascii=False, default=str),
    )


def _sample_video_frames(video_path: str, max_samples: int = 12) -> List[Tuple[int, float, np.ndarray]]:
    """从视频中抽样帧，返回 [(frame_index, second, bgr_frame), ...]"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError('无法打开视频文件')

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = 1.0

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frames: List[Tuple[int, float, np.ndarray]] = []

    try:
        if frame_count > 0:
            sample_count = min(max_samples, frame_count)
            indices = np.linspace(0, frame_count - 1, num=sample_count, dtype=int).tolist()
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                frames.append((idx, float(idx / fps), frame))
        else:
            # 回退策略：顺序读取，每秒取1帧，最多 max_samples
            sample_interval = max(int(round(fps)), 1)
            idx = 0
            while len(frames) < max_samples:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                if idx % sample_interval == 0:
                    frames.append((idx, float(idx / fps), frame))
                idx += 1
    finally:
        cap.release()

    if not frames:
        raise ValueError('视频中未读取到有效帧')

    return frames


def _run_video_test(workflow: Workflow, video_rel_path: str, video_abs_path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """执行视频测试（抽样多帧）"""
    sampled_frames = _sample_video_frames(video_abs_path, max_samples=12)
    executor = WorkflowExecutor(workflow.id, test_mode=True)

    frame_results: List[Dict[str, Any]] = []
    detection_images: List[Dict[str, Any]] = []

    best_result: Optional[Dict[str, Any]] = None
    best_score = -1

    total_start = time.time()

    for index, (frame_idx, second, frame_bgr) in enumerate(sampled_frames, start=1):
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        frame_result = executor.test_execute(frame_rgb, frame_bgr)

        det_count = _extract_detection_count(frame_result)
        triggered = _extract_alert_triggered(frame_result)

        # 保存抽样帧图片，用于“测试结果中心”回看
        frame_rel_path = _save_bgr_image(workflow.id, frame_bgr, prefix=f'video_frame_{index:02d}')
        detection_images.append({
            'image_path': frame_rel_path,
            'timestamp': second,
            'detection_time': datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S'),
            'frame_index': frame_idx,
            'detection_count': det_count,
            'alert_triggered': triggered,
        })

        frame_results.append({
            'frame_index': frame_idx,
            'second': round(second, 3),
            'success': bool(frame_result.get('success', False)),
            'detection_count': det_count,
            'alert_triggered': triggered,
            'image_path': frame_rel_path,
        })

        score = det_count * 2 + (1 if triggered else 0)
        if score > best_score:
            best_score = score
            best_result = frame_result

    execution_time = int((time.time() - total_start) * 1000)
    detected_frames = sum(1 for item in frame_results if item['detection_count'] > 0)
    total_detections = sum(int(item['detection_count']) for item in frame_results)

    if best_result is None:
        best_result = {
            'success': False,
            'nodes': [],
            'logs': [],
            'error': '视频抽样帧执行失败'
        }

    response = {
        'success': bool(best_result.get('success', False)),
        'media_type': 'video',
        'workflow_id': workflow.id,
        'workflow_name': workflow.name,
        'execution_time': execution_time,
        'nodes': best_result.get('nodes', []),
        'logs': best_result.get('logs', []),
        'video_summary': {
            'video_path': video_rel_path,
            'sampled_frames': len(frame_results),
            'detected_frames': detected_frames,
            'total_detections': total_detections,
            'frames': frame_results,
        },
        'message': f"视频测试完成：抽样 {len(frame_results)} 帧，命中 {detected_frames} 帧",
    }

    return response, detection_images


def register_workflow_test_api(app):
    """注册工作流测试 API 路由"""

    @app.route('/api/workflows/<int:workflow_id>/test', methods=['POST'])
    def test_workflow(workflow_id):
        """
        测试工作流执行（支持图片与视频）

        支持两种请求方式：
        1) JSON（兼容旧版）
           { "image": "base64..." }
        2) multipart/form-data
           - media: 上传文件（image/* 或 video/*）
        """
        try:
            try:
                workflow = Workflow.get_by_id(workflow_id)
            except Workflow.DoesNotExist:
                return jsonify({'error': f'工作流 {workflow_id} 不存在'}), 404

            media_type = 'image'
            result_payload: Dict[str, Any] = {}
            detection_images: List[Dict[str, Any]] = []
            image_path: Optional[str] = None
            image_ori_path: Optional[str] = None
            video_path: Optional[str] = None
            window_stats: Optional[Dict[str, Any]] = None

            # ========== 路径1：multipart 上传（图片 / 视频） ==========
            if 'media' in request.files:
                media_file = request.files['media']
                if not media_file or not media_file.filename:
                    return jsonify({'error': '缺少上传文件'}), 400

                filename = Path(media_file.filename)
                suffix = filename.suffix.lower()
                content_type = (media_file.content_type or '').lower()
                file_bytes = media_file.read()

                if not file_bytes:
                    return jsonify({'error': '上传文件为空'}), 400

                is_video = content_type.startswith('video/') or suffix in {
                    '.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'
                }
                is_image = content_type.startswith('image/') or suffix in {
                    '.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.gif'
                }

                if is_video:
                    media_type = 'video'
                    video_rel_path = _build_video_relative_path(workflow_id, ext=suffix or '.mp4')
                    video_abs_path = os.path.join(VIDEO_SAVE_PATH, video_rel_path)
                    with open(video_abs_path, 'wb') as f:
                        f.write(file_bytes)

                    video_path = video_rel_path
                    result_payload, detection_images = _run_video_test(workflow, video_rel_path, video_abs_path)

                    # 代表图取首帧
                    if detection_images:
                        image_path = detection_images[0].get('image_path')
                        image_ori_path = image_path

                    summary = result_payload.get('video_summary') or {}
                    window_stats = {
                        'sampled_frames': summary.get('sampled_frames', 0),
                        'detected_frames': summary.get('detected_frames', 0),
                        'total_detections': summary.get('total_detections', 0),
                    }

                elif is_image:
                    media_type = 'image'
                    nparr = np.frombuffer(file_bytes, np.uint8)
                    image_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if image_bgr is None:
                        return jsonify({'error': '图片解码失败'}), 400

                    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

                    # 保存输入图片用于结果中心回看
                    image_path = _save_bgr_image(workflow_id, image_bgr, prefix='upload_image')
                    image_ori_path = image_path
                    detection_images = [{
                        'image_path': image_path,
                        'detection_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'timestamp': time.time(),
                    }]

                    executor = WorkflowExecutor(workflow_id, test_mode=True)
                    start_time = time.time()
                    result_payload = executor.test_execute(image_rgb, image_bgr)
                    result_payload['execution_time'] = int((time.time() - start_time) * 1000)
                    result_payload['workflow_id'] = workflow_id
                    result_payload['workflow_name'] = workflow.name

                else:
                    return jsonify({'error': f'不支持的文件类型: {content_type or suffix or "unknown"}'}), 400

            # ========== 路径2：JSON base64 图片（兼容旧版） ==========
            else:
                data = request.json
                if not data:
                    return jsonify({'error': '缺少请求体'}), 400

                image_base64 = data.get('image')
                if not image_base64:
                    return jsonify({'error': '缺少图片数据'}), 400

                media_type = 'image'
                image_rgb, image_bgr = _decode_base64_image(image_base64)

                image_path = _save_bgr_image(workflow_id, image_bgr, prefix='base64_image')
                image_ori_path = image_path
                detection_images = [{
                    'image_path': image_path,
                    'detection_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'timestamp': time.time(),
                }]

                executor = WorkflowExecutor(workflow_id, test_mode=True)
                start_time = time.time()
                result_payload = executor.test_execute(image_rgb, image_bgr)
                result_payload['execution_time'] = int((time.time() - start_time) * 1000)
                result_payload['workflow_id'] = workflow_id
                result_payload['workflow_name'] = workflow.name

            # ========== 落库（独立测试表） ==========
            execution_time_ms = int(result_payload.get('execution_time') or 0)
            test_record = _persist_test_record(
                workflow=workflow,
                media_type=media_type,
                result_payload=result_payload,
                execution_time_ms=execution_time_ms,
                image_path=image_path,
                image_ori_path=image_ori_path,
                video_path=video_path,
                detection_images=detection_images,
                window_stats=window_stats,
            )

            result_payload['test_record_id'] = test_record.id
            result_payload['media_type'] = media_type
            result_payload['alert_image'] = image_path
            result_payload['alert_video'] = video_path
            result_payload['detection_images'] = detection_images

            return jsonify(result_payload)

        except Exception as e:
            logger.error(f"工作流测试失败: {e}")
            logger.error(traceback.format_exc())
            return jsonify({
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc()
            }), 500

    @app.route('/api/workflow-test-results', methods=['GET'])
    def get_workflow_test_results():
        """获取工作流测试结果列表（类似 Alert 中心，但独立数据源）"""
        try:
            page = max(int(request.args.get('page', 1)), 1)
            per_page = max(min(int(request.args.get('per_page', 20)), 100), 1)
            workflow_id = request.args.get('workflow_id')
            media_type = request.args.get('media_type')
            start_time = request.args.get('start_time')
            end_time = request.args.get('end_time')

            query = WorkflowTestResult.select()

            if workflow_id:
                query = query.where(WorkflowTestResult.workflow == int(workflow_id))
            if media_type:
                query = query.where(WorkflowTestResult.media_type == media_type)

            start_dt = _safe_fromisoformat(start_time) if start_time else None
            end_dt = _safe_fromisoformat(end_time) if end_time else None

            if start_dt:
                query = query.where(WorkflowTestResult.test_time >= start_dt)
            if end_dt:
                query = query.where(WorkflowTestResult.test_time <= end_dt)

            total = query.count()
            total_pages = (total + per_page - 1) // per_page if total > 0 else 1
            offset = (page - 1) * per_page

            records = query.order_by(WorkflowTestResult.test_time.desc()).limit(per_page).offset(offset)

            data = []
            for r in records:
                workflow = r.workflow
                source = r.video_source
                data.append({
                    'id': r.id,
                    'task_id': source.id if source else 0,
                    'source_id': source.id if source else None,
                    'source_name': source.name if source else None,
                    'workflow_id': workflow.id if workflow else None,
                    'workflow_name': workflow.name if workflow else None,
                    'alert_time': r.test_time.isoformat() if r.test_time else None,
                    'alert_type': r.alert_type,
                    'alert_level': r.alert_level,
                    'alert_message': r.alert_message,
                    'alert_image': r.alert_image,
                    'alert_image_ori': r.alert_image_ori,
                    'alert_video': r.alert_video,
                    'detection_count': r.detection_count,
                    'detection_images': r.detection_images,
                    'window_stats': r.window_stats,
                    'media_type': r.media_type,
                    'success': r.success,
                    'execution_time_ms': r.execution_time_ms,
                    'result_json': r.result_json,
                })

            return jsonify({
                'data': data,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'total_pages': total_pages,
                }
            })

        except Exception as e:
            logger.error(f"获取工作流测试结果失败: {e}")
            logger.error(traceback.format_exc())
            return jsonify({'error': str(e)}), 500
