"""Cross-platform 1:N face recognition workflow node."""

from __future__ import annotations

import json
import time
from collections import Counter, deque
from datetime import datetime, timedelta

import cv2

from app.core.database_models import (
    FaceEvent,
    FaceGallery,
    FaceModelBundle,
)
from app.core.face_event_storage import (
    remove_face_event_snapshot,
    write_encrypted_face_event_snapshot,
)
from app.core.face_gallery import gallery_index_cache
from app.core.face_inference import create_face_backend
from app.core.face_settings import get_face_recognition_config
from app.user_scripts.common.iou_tracker import IoUTracker
from app.user_scripts.common.roi import filter_items_by_regions
from app.user_scripts.common.result import build_result


SCRIPT_METADATA = {
    'name': '跨平台人脸识别',
    'version': 'v1.0',
    'description': '统一使用 ONNX/CUDA/TensorRT/RKNN 人脸模型包完成千人级 1:N 识别',
    'author': 'system',
    'category': 'face',
    'tags': ['face', '1:n', 'onnx', 'cuda', 'tensorrt', 'rknn'],
    'config_schema': {
        'gallery_id': {
            'type': 'face_gallery_select',
            'label': '人脸库',
            'required': True,
        },
        'backend': {
            'type': 'select',
            'label': '推理后端',
            'default': 'auto',
            'options': [
                {'value': 'auto', 'label': '继承系统设置（推荐）'},
                {'value': 'onnxruntime', 'label': 'ONNX Runtime'},
                {'value': 'tensorrt', 'label': 'TensorRT EP'},
                {'value': 'torchscript', 'label': 'TorchScript'},
                {'value': 'rknn', 'label': 'RKNNLite'},
            ],
        },
        'min_face_size': {
            'type': 'int',
            'label': '最小人脸短边',
            'default': 80,
            'min': 32,
            'max': 1024,
        },
        'face_detection_confidence': {
            'type': 'float',
            'label': '人脸检测阈值',
            'default': 0.6,
            'min': 0.0,
            'max': 1.0,
            'step': 0.05,
        },
        'face_nms_iou': {
            'type': 'float',
            'label': '人脸 NMS 阈值',
            'default': 0.4,
            'min': 0.0,
            'max': 1.0,
            'step': 0.05,
        },
        'confirmation_window': {
            'type': 'int',
            'label': '多帧确认窗口',
            'default': 3,
            'min': 3,
            'max': 10,
        },
        'confirmation_hits': {
            'type': 'int',
            'label': '灰区命中次数',
            'default': 2,
            'min': 2,
            'max': 10,
        },
        'save_events': {
            'type': 'boolean',
            'label': '记录识别事件',
            'default': True,
        },
    },
    'performance': {
        'timeout': 30,
        'memory_limit_mb': 768,
        'gpu_required': False,
        'estimated_time_ms': 200,
    },
}


def _bounded_float(value, default):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


def init(config):
    gallery_id = int(config.get('gallery_id') or 0)
    if gallery_id <= 0:
        raise ValueError('gallery_id 必须是正整数')
    gallery = FaceGallery.get_by_id(gallery_id)
    execution_owner = str(config.get('_execution_owner') or '').strip()
    execution_role = str(config.get('_execution_role') or 'user').strip().lower()
    if not execution_owner:
        raise ValueError('人脸识别缺少可信执行身份')
    if execution_role != 'admin' and gallery.created_by != execution_owner:
        raise ValueError('无权访问选择的人脸库')
    if not gallery.enabled:
        raise ValueError('选择的人脸库已禁用')
    if not gallery.model_bundle_id:
        raise ValueError('选择的人脸库尚未配置模型包')
    bundle = FaceModelBundle.get_by_id(gallery.model_bundle_id)
    backend_config = {
        'face_detection_confidence': _bounded_float(
            config.get('face_detection_confidence'), 0.6
        ),
        'face_nms_iou': _bounded_float(config.get('face_nms_iou'), 0.4),
        'min_face_size': max(32, int(config.get('min_face_size') or 80)),
    }
    return {
        'gallery_id': gallery.id,
        'bundle_id': bundle.id,
        'model_contract': bundle.contract_id,
        'requested_backend': str(config.get('backend') or 'auto'),
        'backend': create_face_backend(
            bundle, str(config.get('backend') or 'auto'), backend_config
        ),
        'backend_config': backend_config,
        'tracker': IoUTracker(match_iou=0.3, max_misses=3, min_hits=1, max_tracks=128),
        'gallery_version': int(gallery.gallery_version),
        'execution_owner': execution_owner,
        'preview_mode': bool(config.get('_preview_mode', False)),
        'decisions': {},
        'confirmed': {},
    }


def _decision_for_track(state, track_id, match, gallery, config):
    window_size = max(3, int(config.get('confirmation_window') or 3))
    required_hits = max(2, min(window_size, int(config.get('confirmation_hits') or 2)))
    history = state['decisions'].setdefault(track_id, deque(maxlen=window_size))
    if match is None or match.similarity < gallery.low_threshold:
        history.append((None, float(match.similarity) if match else None))
    else:
        history.append((match.person_id, float(match.similarity)))

    if match is not None and match.similarity >= gallery.high_threshold:
        return 'known', match

    known_ids = [person_id for person_id, _score in history if person_id is not None]
    if known_ids:
        person_id, count = Counter(known_ids).most_common(1)[0]
        if count >= required_hits and match is not None and match.person_id == person_id:
            return 'known', match

    if len(history) == window_size and all(person_id is None for person_id, _ in history):
        return 'unknown', match
    return 'pending', match


def _save_event_snapshot(frame_rgb, box, track_id):
    x1, y1, x2, y2 = [int(round(value)) for value in box]
    height, width = frame_rgb.shape[:2]
    pad_x = int(max(0, x2 - x1) * 0.25)
    pad_y = int(max(0, y2 - y1) * 0.25)
    crop = frame_rgb[
        max(0, y1 - pad_y):min(height, y2 + pad_y),
        max(0, x1 - pad_x):min(width, x2 + pad_x),
    ]
    if crop.size == 0:
        return None
    ok, encoded = cv2.imencode('.jpg', cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    if not ok:
        return None
    return write_encrypted_face_event_snapshot(encoded.tobytes(), track_id)


def _record_event(
    frame_rgb, detection, status, match, gallery, state, config,
    inference_backend=None,
):
    if state.get('preview_mode'):
        return
    if not bool(config.get('save_events', True)):
        return
    system_config = get_face_recognition_config()
    retention_days = (
        system_config.known_retention_days
        if status == 'known'
        else system_config.unknown_retention_days
    )
    if retention_days <= 0:
        return
    snapshot_path = _save_event_snapshot(
        frame_rgb, detection['box'], detection['track_id']
    )
    occurred_at = datetime.now()
    try:
        FaceEvent.create(
            video_source=int(config.get('source_id')) if config.get('source_id') else None,
            workflow=int(config.get('workflow_id')) if config.get('workflow_id') else None,
            gallery=gallery,
            person=match.person_id if status == 'known' and match else None,
            track_id=str(detection['track_id']),
            identity_status=status,
            person_code_snapshot=match.person_code if status == 'known' and match else None,
            person_name_snapshot=match.person_name if status == 'known' and match else None,
            similarity=float(match.similarity) if match else None,
            threshold=(gallery.high_threshold if status == 'known' else gallery.low_threshold),
            quality_json=json.dumps(
                (detection.get('attributes') or {}).get('quality') or {}, ensure_ascii=False
            ),
            snapshot_path=snapshot_path,
            liveness_status='not_checked',
            model_contract=state['model_contract'],
            inference_backend=inference_backend,
            occurred_at=occurred_at,
            expires_at=occurred_at + timedelta(days=retention_days),
            created_by=state['execution_owner'],
        )
    except Exception:
        if snapshot_path:
            remove_face_event_snapshot(snapshot_path)
        raise


def _refresh_backend_for_gallery(state, gallery):
    if not gallery.model_bundle_id:
        return False
    bundle = FaceModelBundle.get_by_id(gallery.model_bundle_id)
    if (
        int(state.get('bundle_id') or 0) == int(bundle.id)
        and state.get('model_contract') == bundle.contract_id
    ):
        return False

    replacement = create_face_backend(
        bundle,
        state.get('requested_backend') or 'auto',
        state.get('backend_config') or {},
    )
    previous = state.get('backend')
    state['backend'] = replacement
    state['bundle_id'] = bundle.id
    state['model_contract'] = bundle.contract_id
    state['tracker'].reset()
    state['decisions'].clear()
    state['confirmed'].clear()
    if previous is not None:
        try:
            previous.cleanup()
        except Exception:
            pass
    return True


def _prune_track_state(state):
    live_track_ids = {
        int(track.track_id) for track in state['tracker'].tracks
    }
    stale = (set(state['decisions']) | set(state['confirmed'])) - live_track_ids
    for track_id in stale:
        state['decisions'].pop(track_id, None)
        state['confirmed'].pop(track_id, None)


def _refresh_gallery_version(state, gallery):
    current = int(gallery.gallery_version)
    if int(state.get('gallery_version') or 0) == current:
        return False
    # A version bump may revoke a person, rename an identity, or change a
    # template.  Existing confirmations must therefore pass through the new
    # index instead of bypassing search for the rest of a long-lived track.
    state['gallery_version'] = current
    state['decisions'].clear()
    state['confirmed'].clear()
    return True


def process(
    frame_rgb,
    config,
    state,
    roi_regions=None,
    frame_timestamp=None,
    **_kwargs,
):
    if state is None:
        return build_result([], metadata={'error': '人脸识别状态未初始化'})
    started = time.monotonic()
    gallery = FaceGallery.get_by_id(state['gallery_id'])
    if not gallery.enabled:
        return build_result([], metadata={'degraded': True, 'reason': 'gallery_disabled'})
    if not gallery.model_bundle_id:
        return build_result([], metadata={'degraded': True, 'reason': 'gallery_model_missing'})

    _refresh_backend_for_gallery(state, gallery)
    _refresh_gallery_version(state, gallery)

    raw_detections, _details, inference_metadata = state['backend'].infer(frame_rgb)
    detections_before_roi = len(raw_detections)
    if roi_regions:
        raw_detections = filter_items_by_regions(
            raw_detections,
            frame_rgb.shape,
            roi_regions,
            metric='center',
        )
    roi_filtered_count = detections_before_roi - len(raw_detections)
    tracks = state['tracker'].update(raw_detections, timestamp=frame_timestamp)
    index = gallery_index_cache.get(gallery.id)
    output = []
    for track in tracks:
        if track.misses > 0:
            continue
        detection = track.to_detection('face_iou')
        track_id = int(track.track_id)
        attributes = dict(detection.get('attributes') or {})
        embedding = attributes.pop('embedding', None)
        quality = attributes.get('quality') or {}
        status = 'low_quality' if not quality.get('accepted') else 'pending'
        match = None

        if track_id in state['confirmed']:
            status, confirmed = state['confirmed'][track_id]
            match = confirmed
        elif embedding is not None and index.template_count > 0:
            matches = index.search(embedding, top_k=3)
            match = matches[0] if matches else None
            status, match = _decision_for_track(state, track_id, match, gallery, config)
            attributes['top_matches'] = [
                {
                    'person_id': item.person_id,
                    'person_code': item.person_code,
                    'person_name': item.person_name,
                    'similarity': item.similarity,
                }
                for item in matches
            ]
            if status in {'known', 'unknown'}:
                _record_event(
                    frame_rgb, detection, status, match, gallery, state, config,
                    inference_backend=inference_metadata.get('backend'),
                )
                # A confirmed track bypasses gallery search on later frames.
                # Publish that terminal state only after event persistence (or
                # an intentional no-save policy) has completed successfully.
                state['confirmed'][track_id] = (status, match)
        elif embedding is not None and index.template_count == 0:
            status = 'gallery_empty'

        attributes.update({
            'identity_status': status,
            'similarity': float(match.similarity) if match else None,
            'person_id': match.person_id if status == 'known' and match else None,
            'person_code': match.person_code if status == 'known' and match else None,
            'person_name': match.person_name if status == 'known' and match else None,
            'gallery_id': gallery.id,
            'gallery_version': gallery.gallery_version,
            'model_contract': state['model_contract'],
            'inference_backend': inference_metadata.get('backend'),
            'liveness_status': 'not_checked',
        })
        detection['attributes'] = attributes
        detection['label'] = (
            match.person_name if status == 'known' and match else (
                '陌生人' if status == 'unknown' else '人脸'
            )
        )
        detection['label_name'] = detection['label']
        output.append(detection)

    _prune_track_state(state)

    return build_result(output, metadata={
        **inference_metadata,
        'gallery_id': gallery.id,
        'gallery_version': gallery.gallery_version,
        'gallery_template_count': index.template_count,
        'roi_regions_count': len(roi_regions or []),
        'detections_before_roi': detections_before_roi,
        'roi_filtered_count': roi_filtered_count,
        'identity_count': sum(
            1 for item in output
            if (item.get('attributes') or {}).get('identity_status') == 'known'
        ),
        'unknown_count': sum(
            1 for item in output
            if (item.get('attributes') or {}).get('identity_status') == 'unknown'
        ),
        'total_time_ms': (time.monotonic() - started) * 1000.0,
    })


def cleanup(state):
    if state and state.get('backend') is not None:
        state['backend'].cleanup()
