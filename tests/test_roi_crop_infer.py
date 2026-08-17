import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALGORITHM_PATH = PROJECT_ROOT / "app" / "core" / "algorithm.py"
ROI_PATH = PROJECT_ROOT / "app" / "user_scripts" / "common" / "roi.py"
ADAPTIVE_PATH = PROJECT_ROOT / "app" / "user_scripts" / "templates" / "adaptive_yolo_detector.py"

_MISSING = object()


@contextlib.contextmanager
def patched_sys_modules(overrides):
    original = {}
    for name, value in overrides.items():
        original[name] = sys.modules.get(name, _MISSING)
        sys.modules[name] = value
    try:
        yield
    finally:
        for name, value in original.items():
            if value is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def make_cv2_stub():
    cv2_stub = types.ModuleType("cv2")

    def _fill_poly(mask, polygons, value):
        for polygon in polygons:
            polygon = np.asarray(polygon)
            x1 = int(np.min(polygon[:, 0]))
            y1 = int(np.min(polygon[:, 1]))
            x2 = int(np.max(polygon[:, 0]))
            y2 = int(np.max(polygon[:, 1]))
            mask[y1:y2 + 1, x1:x2 + 1] = value

    def _bitwise_and(frame_a, frame_b, mask=None):
        if mask is None:
            return np.bitwise_and(frame_a, frame_b)
        expanded_mask = (mask > 0).astype(frame_a.dtype)
        if frame_a.ndim == 3:
            expanded_mask = expanded_mask[..., None]
        return frame_a * expanded_mask

    class _DnnModule:
        @staticmethod
        def NMSBoxes(bboxes, scores, score_threshold, nms_threshold):
            kept = []
            candidates = [
                (idx, bboxes[idx], scores[idx])
                for idx in range(len(bboxes))
                if scores[idx] >= score_threshold
            ]
            candidates.sort(key=lambda item: item[2], reverse=True)

            def _iou(box_a, box_b):
                ax1, ay1, aw, ah = box_a
                bx1, by1, bw, bh = box_b
                ax2, ay2 = ax1 + aw, ay1 + ah
                bx2, by2 = bx1 + bw, by1 + bh
                inter_x1 = max(ax1, bx1)
                inter_y1 = max(ay1, by1)
                inter_x2 = min(ax2, bx2)
                inter_y2 = min(ay2, by2)
                inter_w = max(0.0, inter_x2 - inter_x1)
                inter_h = max(0.0, inter_y2 - inter_y1)
                inter_area = inter_w * inter_h
                area_a = aw * ah
                area_b = bw * bh
                denom = max(area_a + area_b - inter_area, 1e-6)
                return inter_area / denom

            while candidates:
                current = candidates.pop(0)
                kept.append(current[0])
                candidates = [
                    item for item in candidates
                    if _iou(current[1], item[1]) <= nms_threshold
                ]
            return kept

    cv2_stub.fillPoly = _fill_poly
    cv2_stub.bitwise_and = _bitwise_and
    cv2_stub.dnn = _DnnModule()
    return cv2_stub


def load_roi_module():
    fake_app = types.ModuleType("app")
    fake_app.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )
    fake_app_core = types.ModuleType("app.core")
    cv2_stub = make_cv2_stub()
    fake_cv2_compat = types.ModuleType("app.core.cv2_compat")
    fake_cv2_compat.cv2 = cv2_stub
    fake_cv2_compat.require_cv2 = lambda: cv2_stub
    overrides = {
        "cv2": cv2_stub,
        "app": fake_app,
        "app.core": fake_app_core,
        "app.core.cv2_compat": fake_cv2_compat,
    }

    with patched_sys_modules(overrides):
        algorithm_spec = importlib.util.spec_from_file_location("app.core.algorithm", ALGORITHM_PATH)
        algorithm_module = importlib.util.module_from_spec(algorithm_spec)
        assert algorithm_spec.loader is not None
        sys.modules["app.core.algorithm"] = algorithm_module
        algorithm_spec.loader.exec_module(algorithm_module)

        roi_spec = importlib.util.spec_from_file_location("test_roi_module", ROI_PATH)
        roi_module = importlib.util.module_from_spec(roi_spec)
        assert roi_spec.loader is not None
        roi_spec.loader.exec_module(roi_module)

    return roi_module


def load_adaptive_module(roi_module):
    fake_app = types.ModuleType("app")
    fake_app.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )
    fake_app_core = types.ModuleType("app.core")
    fake_config = types.ModuleType("app.config")
    fake_config.WORKFLOW_FRAME_LOGS_ENABLED = False
    fake_frame_utils = types.ModuleType("app.core.frame_utils")
    fake_frame_utils.nv12_to_rgb = lambda frame, width=None, height=None: frame
    fake_model_resolver = types.ModuleType("app.core.model_resolver")
    fake_model_resolver.get_model_resolver = lambda: None
    fake_user_scripts = types.ModuleType("app.user_scripts")
    fake_common = types.ModuleType("app.user_scripts.common")
    fake_result = types.ModuleType("app.user_scripts.common.result")
    fake_result.build_result = lambda detections, metadata=None, **kwargs: {
        "detections": list(detections or []),
        "metadata": metadata or {},
        **kwargs,
    }
    fake_backends = types.ModuleType("app.user_scripts.common.yolo_backends")
    fake_backends.create_backend = lambda *args, **kwargs: None

    overrides = {
        "app": fake_app,
        "app.config": fake_config,
        "app.core": fake_app_core,
        "app.core.frame_utils": fake_frame_utils,
        "app.core.model_resolver": fake_model_resolver,
        "app.user_scripts": fake_user_scripts,
        "app.user_scripts.common": fake_common,
        "app.user_scripts.common.result": fake_result,
        "app.user_scripts.common.roi": roi_module,
        "app.user_scripts.common.yolo_backends": fake_backends,
    }

    with patched_sys_modules(overrides):
        adaptive_spec = importlib.util.spec_from_file_location("test_adaptive_module", ADAPTIVE_PATH)
        adaptive_module = importlib.util.module_from_spec(adaptive_spec)
        assert adaptive_spec.loader is not None
        adaptive_spec.loader.exec_module(adaptive_module)

    return adaptive_module


def _square_region(x1, y1, x2, y2, mode="crop_infer"):
    return {
        "mode": mode,
        "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
    }


@pytest.mark.parametrize(
    ("anchor", "point"),
    [
        ("top_left", (20, 20)),
        ("top_center", (40, 20)),
        ("top_right", (60, 20)),
        ("center_left", (20, 40)),
        ("center", (40, 40)),
        ("center_right", (60, 40)),
        ("bottom_left", (20, 60)),
        ("bottom_center", (40, 60)),
        ("bottom_right", (60, 60)),
    ],
)
def test_filter_items_by_regions_supports_all_anchor_presets(anchor, point):
    roi_module = load_roi_module()
    point_x, point_y = point
    region = _square_region(
        point_x - 1,
        point_y - 1,
        point_x + 1,
        point_y + 1,
        mode="post_filter",
    )
    region["anchor"] = anchor
    detection = {"box": [20, 20, 60, 60], "label": anchor}

    filtered = roi_module.filter_items_by_regions(
        [detection],
        frame_shape=(100, 100, 3),
        roi_regions=[region],
        metric="ioa",
        threshold=0.9,
    )

    assert filtered == [detection]


def test_split_regions_supports_crop_infer():
    roi_module = load_roi_module()
    pre_mask, crop_infer, post_filter = roi_module.split_regions([
        _square_region(0, 0, 10, 10, mode="pre_mask"),
        _square_region(20, 20, 30, 30, mode="crop_infer"),
        _square_region(40, 40, 50, 50, mode="post_filter"),
    ])

    assert len(pre_mask) == 1
    assert len(crop_infer) == 1
    assert len(post_filter) == 1


def test_build_crop_plans_auto_merges_small_regions():
    roi_module = load_roi_module()
    plans = roi_module.build_crop_plans(
        frame_shape=(100, 100, 3),
        roi_regions=[
            _square_region(10, 10, 20, 20),
            _square_region(30, 30, 40, 40),
        ],
        padding=0,
        strategy="auto",
    )

    assert len(plans) == 1
    assert plans[0]["box"] == [10, 10, 41, 41]
    assert len(plans[0]["regions"]) == 2


def test_filter_items_by_regions_uses_ioa_threshold():
    roi_module = load_roi_module()
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    roi_regions = [_square_region(20, 20, 60, 60, mode="post_filter")]
    detections = [
        {"box": [25, 25, 40, 40], "confidence": 0.9, "label": "inside"},
        {"box": [10, 10, 35, 35], "confidence": 0.8, "label": "partial"},
        {"box": [0, 0, 10, 10], "confidence": 0.7, "label": "outside"},
    ]

    filtered = roi_module.filter_items_by_regions(
        detections,
        frame_shape=frame.shape,
        roi_regions=roi_regions,
        metric="ioa",
        threshold=0.5,
    )

    assert [item["label"] for item in filtered] == ["inside"]


def test_region_anchor_overrides_legacy_metric_and_mixed_regions_use_or_semantics():
    roi_module = load_roi_module()
    anchored_region = _square_region(19, 59, 41, 61, mode="post_filter")
    anchored_region["anchor"] = "bottom_center"
    legacy_region = _square_region(70, 70, 90, 90, mode="post_filter")
    detections = [
        {"box": [20, 20, 60, 60], "label": "anchor-hit"},
        {"box": [72, 72, 80, 80], "label": "legacy-hit"},
        {"box": [0, 0, 10, 10], "label": "outside"},
    ]

    filtered = roi_module.filter_items_by_regions(
        detections,
        frame_shape=(100, 100, 3),
        roi_regions=[anchored_region, legacy_region],
        metric="ioa",
        threshold=0.9,
    )

    assert [item["label"] for item in filtered] == ["anchor-hit", "legacy-hit"]


def test_invalid_anchor_uses_legacy_fallback_and_unboxed_items_can_be_preserved():
    roi_module = load_roi_module()
    region = _square_region(20, 20, 60, 60, mode="post_filter")
    region["anchor"] = "not-a-real-anchor"
    unboxed = {"label": "semantic"}
    center_hit = {"box": [30, 30, 40, 40], "label": "center-hit"}

    filtered = roi_module.filter_items_by_regions(
        [unboxed, center_hit],
        frame_shape=(100, 100, 3),
        roi_regions=[region],
        metric="center",
        keep_unboxed=True,
    )

    assert filtered == [unboxed, center_hit]


def test_pre_mask_anchor_does_not_post_filter_detections():
    roi_module = load_roi_module()
    region = _square_region(20, 20, 60, 60, mode="pre_mask")
    region["anchor"] = "bottom_center"
    outside = {"box": [70, 70, 90, 90], "label": "outside"}

    _, detections = roi_module.apply_roi(
        np.zeros((100, 100, 3), dtype=np.uint8),
        [outside],
        [region],
    )

    assert detections == [outside]


def test_global_nms_deduplicates_same_class_crop_detections():
    roi_module = load_roi_module()
    crop_box = [100, 50, 200, 150]
    detections = [
        {"box": [10, 10, 50, 50], "confidence": 0.95, "label": "person", "class": 0},
        {"box": [12, 12, 52, 52], "confidence": 0.8, "label": "person", "class": 0},
    ]
    details = [
        {"box": [10, 10, 50, 50], "confidence": 0.95, "class_name": "person", "class": 0},
        {"box": [12, 12, 52, 52], "confidence": 0.8, "class_name": "person", "class": 0},
    ]

    remapped_detections = roi_module.remap_detections_to_full_frame(detections, crop_box)
    remapped_details = roi_module.remap_detections_to_full_frame(details, crop_box)
    merged_detections, merged_details = roi_module.global_nms(
        remapped_detections,
        remapped_details,
        score_threshold=0.1,
        nms_threshold=0.5,
    )

    assert len(merged_detections) == 1
    assert len(merged_details) == 1
    assert merged_detections[0]["box"] == [110.0, 60.0, 150.0, 100.0]


def test_global_nms_keeps_overlapping_different_classes():
    roi_module = load_roi_module()
    detections = [
        {"box": [10, 10, 50, 50], "confidence": 0.95, "label": "person", "class": 0},
        {"box": [12, 12, 52, 52], "confidence": 0.9, "label": "bicycle", "class": 1},
    ]
    details = [
        {"box": [10, 10, 50, 50], "confidence": 0.95, "class_name": "person", "class": 0},
        {"box": [12, 12, 52, 52], "confidence": 0.9, "class_name": "bicycle", "class": 1},
    ]

    merged_detections, merged_details = roi_module.global_nms(
        detections,
        details,
        score_threshold=0.1,
        nms_threshold=0.5,
    )

    assert len(merged_detections) == 2
    assert len(merged_details) == 2


def test_process_crop_infer_uses_original_frame_when_mixed_with_pre_mask():
    roi_module = load_roi_module()
    adaptive_module = load_adaptive_module(roi_module)
    fake_app = types.ModuleType("app")
    fake_app.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )

    class Backend:
        name = "rknn"

        def __init__(self):
            self.frames = []

        def infer(self, frame):
            self.frames.append(frame.copy())
            return [], [], {}

    backend = Backend()
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame[12:18, 12:18] = 255

    fake_app_core = types.ModuleType("app.core")
    fake_frame_utils = types.ModuleType("app.core.frame_utils")
    fake_frame_utils.nv12_to_rgb = lambda value, width=None, height=None: value
    with patched_sys_modules({
        "app": fake_app,
        "app.core": fake_app_core,
        "app.core.frame_utils": fake_frame_utils,
    }):
        result = adaptive_module.process(
            frame=frame,
            config={},
            roi_regions=[
                _square_region(0, 0, 4, 4, mode="pre_mask"),
                _square_region(12, 12, 18, 18, mode="crop_infer"),
            ],
            state={
                "backend": backend,
                "model_path": "dummy.rknn",
                "model_info": {"model_type": "RKNN", "framework": "rknn"},
            },
        )

    assert result["detections"] == []
    assert len(backend.frames) == 1
    assert int(np.sum(backend.frames[0])) > 0


def test_process_returns_structured_error_when_backend_inference_fails():
    roi_module = load_roi_module()
    adaptive_module = load_adaptive_module(roi_module)
    fake_app = types.ModuleType("app")
    fake_app.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )

    class Backend:
        name = "onnxruntime"
        config = {"confidence": 0.4, "class_filter": [1]}

        def infer(self, _frame):
            raise RuntimeError("output shape mismatch")

    fake_app_core = types.ModuleType("app.core")
    fake_frame_utils = types.ModuleType("app.core.frame_utils")
    fake_frame_utils.nv12_to_rgb = lambda value, width=None, height=None: value
    with patched_sys_modules({
        "app": fake_app,
        "app.core": fake_app_core,
        "app.core.frame_utils": fake_frame_utils,
    }):
        result = adaptive_module.process(
            frame=np.zeros((20, 20, 3), dtype=np.uint8),
            config={},
            state={"backend": Backend(), "model_path": "dummy.onnx"},
        )

    assert result["detections"] == []
    assert result["metadata"]["error_code"] == "inference_failed"
    assert "output shape mismatch" in result["metadata"]["error"]
    assert result["metadata"]["backend"] == "onnxruntime"


def test_process_marks_unsupported_model_output_as_error():
    roi_module = load_roi_module()
    adaptive_module = load_adaptive_module(roi_module)
    fake_app = types.ModuleType("app")
    fake_app.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )

    class Backend:
        name = "rknn"
        config = {"confidence": 0.6, "class_filter": []}

        def infer(self, _frame):
            return [], [], {
                "postprocess_profile": "unsupported",
                "postprocess_warning": "缺少模型级后处理配置",
            }

    fake_app_core = types.ModuleType("app.core")
    fake_frame_utils = types.ModuleType("app.core.frame_utils")
    fake_frame_utils.nv12_to_rgb = lambda value, width=None, height=None: value
    with patched_sys_modules({
        "app": fake_app,
        "app.core": fake_app_core,
        "app.core.frame_utils": fake_frame_utils,
    }):
        result = adaptive_module.process(
            frame=np.zeros((20, 20, 4), dtype=np.uint8),
            config={},
            state={"backend": Backend(), "model_path": "dummy.rknn"},
        )

    assert result["metadata"]["error_code"] == "unsupported_model_output"
    assert result["metadata"]["error"] == "缺少模型级后处理配置"
    assert result["metadata"]["image_size"] == {"height": 20, "width": 20}


def test_cleanup_is_idempotent_and_removes_backend_reference():
    roi_module = load_roi_module()
    adaptive_module = load_adaptive_module(roi_module)

    class Backend:
        def __init__(self):
            self.cleanup_calls = 0

        def cleanup(self):
            self.cleanup_calls += 1

    backend = Backend()
    state = {"backend": backend}

    adaptive_module.cleanup(state)
    adaptive_module.cleanup(state)

    assert backend.cleanup_calls == 1
    assert "backend" not in state
