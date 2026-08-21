import unittest
import types
import sys
import importlib.util
import contextlib
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "app" / "user_scripts" / "common" / "yolo_backends.py"
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


def _load_yolo_output_adapter():
    cv2_stub = types.ModuleType("cv2")

    class _DnnModule:
        @staticmethod
        def NMSBoxes(bboxes, scores, score_threshold, nms_threshold):
            return [idx for idx, score in enumerate(scores) if score >= score_threshold]

    cv2_stub.dnn = _DnnModule()
    cv2_stub.INTER_LINEAR = 1
    cv2_stub.COLOR_RGB2BGR = 2
    cv2_stub.resize = lambda _frame, size, interpolation=None: np.zeros(
        (size[1], size[0], 3), dtype=np.uint8
    )
    cv2_stub.cvtColor = lambda frame, _code: frame[:, :, ::-1]

    fake_app = types.ModuleType("app")
    fake_app.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )

    with patched_sys_modules({
        "cv2": cv2_stub,
        "app": fake_app,
    }):
        spec = importlib.util.spec_from_file_location("test_yolo_backends_module", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module


YOLO_BACKENDS = _load_yolo_output_adapter()
YoloOutputAdapter = YOLO_BACKENDS.YoloOutputAdapter


class BackendConfigTests(unittest.TestCase):
    def test_runtime_modules_are_not_imported_when_adapter_is_loaded(self):
        self.assertIsNone(YOLO_BACKENDS.YOLO)
        self.assertFalse(YOLO_BACKENDS._ULTRALYTICS_IMPORT_ATTEMPTED)
        self.assertIsNone(YOLO_BACKENDS.RKNNLite)
        self.assertFalse(YOLO_BACKENDS._RKNNLITE_IMPORT_ATTEMPTED)

    def test_parse_input_shape_returns_width_height_for_non_square_shape(self):
        self.assertEqual(YOLO_BACKENDS.parse_input_shape([1, 3, 480, 640]), (640, 480))
        self.assertEqual(YOLO_BACKENDS.parse_input_shape("1x3x320x640"), (640, 320))
        self.assertEqual(YOLO_BACKENDS.parse_input_shape([1, 480, 640, 3]), (640, 480))
        self.assertEqual(YOLO_BACKENDS.parse_input_shape("1,3,height,width"), (0, 0))

    def test_backend_selection_uses_override_then_model_metadata(self):
        self.assertEqual(YOLO_BACKENDS.select_backend("model.pt", {}, {"backend": "auto"}), "ultralytics")
        self.assertEqual(YOLO_BACKENDS.select_backend("model.onnx", {}, {"backend": "auto"}), "onnxruntime")
        self.assertEqual(YOLO_BACKENDS.select_backend("model.bin", {"framework": "rknn"}, {"backend": "auto"}), "rknn")
        self.assertEqual(YOLO_BACKENDS.select_backend("model.pt", {}, {"backend": "onnxruntime"}), "onnxruntime")

    def test_explicit_input_size_overrides_model_metadata(self):
        backend = YOLO_BACKENDS.BaseYoloBackend(
            "model.onnx",
            {"input_shape": [1, 3, 480, 640]},
            {"input_width": 960, "input_height": 544},
        )

        self.assertEqual((backend.input_width, backend.input_height), (960, 544))

    def test_normalize_backend_config_handles_string_values(self):
        config = YOLO_BACKENDS.normalize_backend_config({
            "backend": "onnx",
            "confidence": "0.4",
            "nms_iou": "0.5",
            "class_filter": "0, 2",
            "onnx_normalize": "false",
            "inference_mode": "sliced",
            "sahi_slice_width": "512",
            "sahi_overlap_width_ratio": "0.25",
            "sahi_include_full_frame": "true",
        })

        self.assertEqual(config["backend"], "onnxruntime")
        self.assertEqual(config["class_filter"], [0, 2])
        self.assertFalse(config["onnx_normalize"])
        self.assertEqual(config["inference_mode"], "sahi")
        self.assertEqual(config["sahi_slice_width"], 512)
        self.assertEqual(config["sahi_overlap_width_ratio"], 0.25)
        self.assertTrue(config["sahi_include_full_frame"])

    def test_normalize_backend_config_rejects_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "confidence"):
            YOLO_BACKENDS.normalize_backend_config({"confidence": 1.5})
        with self.assertRaisesRegex(ValueError, "不支持的推理后端"):
            YOLO_BACKENDS.normalize_backend_config({"backend": "tensorrt"})
        with self.assertRaisesRegex(ValueError, "不支持的推理模式"):
            YOLO_BACKENDS.normalize_backend_config({"inference_mode": "stretch"})
        with self.assertRaisesRegex(ValueError, "sahi_overlap_width_ratio 必须小于 1"):
            YOLO_BACKENDS.normalize_backend_config({"sahi_overlap_width_ratio": 1})
        with self.assertRaisesRegex(ValueError, "sahi_max_slices 必须是正整数"):
            YOLO_BACKENDS.normalize_backend_config({"sahi_max_slices": 0})
        with self.assertRaisesRegex(ValueError, "模型路径不能为空"):
            YOLO_BACKENDS.create_backend("", {}, {})

    def test_sahi_slice_boxes_cover_far_edges(self):
        boxes = YOLO_BACKENDS.build_sahi_slice_boxes(
            (60, 100, 3),
            slice_width=60,
            slice_height=40,
            overlap_width_ratio=0.5,
            overlap_height_ratio=0.5,
        )

        self.assertEqual(len(boxes), 6)
        self.assertEqual(boxes[0], [0, 0, 60, 40])
        self.assertEqual(boxes[-1], [40, 20, 100, 60])

    def test_sahi_slice_limit_is_checked_before_materializing_windows(self):
        with self.assertRaisesRegex(ValueError, "SAHI 切片数 .* 超过限制 64"):
            YOLO_BACKENDS.build_sahi_slice_boxes(
                (2160, 3840, 3),
                slice_width=32,
                slice_height=32,
                overlap_width_ratio=0.9,
                overlap_height_ratio=0.9,
                max_slices=64,
            )

    def test_sahi_zero_merge_threshold_keeps_disjoint_same_class_boxes(self):
        detections = [
            {"box": [0, 0, 10, 10], "confidence": 0.9, "class": 0},
            {"box": [20, 20, 30, 30], "confidence": 0.8, "class": 0},
        ]
        details = [dict(item) for item in detections]

        merged_detections, merged_details = YOLO_BACKENDS.merge_sahi_detections(
            detections,
            details,
            match_threshold=0.0,
            match_metric="ios",
        )

        self.assertEqual(merged_detections, detections)
        self.assertEqual(merged_details, details)

    def test_sahi_backend_remaps_and_deduplicates_slice_detections(self):
        class Backend:
            name = "ultralytics"
            model_path = "model.pt"
            model_info = {}
            model = object()
            classes = {0: "person"}
            input_width = 60
            input_height = 60
            output_adapter = None

            def __init__(self):
                self.cleaned = False

            def infer(self, frame):
                slice_start = int(frame[0, 0, 0])
                confidence = 0.9 if slice_start == 40 else 0.8
                local_box = [45 - slice_start, 10, 55 - slice_start, 20]
                detection = {
                    "box": local_box,
                    "confidence": confidence,
                    "class": 0,
                    "label": "person",
                }
                detail = {
                    "box": local_box,
                    "confidence": confidence,
                    "class": 0,
                    "class_name": "person",
                }
                return [detection], [detail], {"inference_mode": "letterbox"}

            def cleanup(self):
                self.cleaned = True

        frame = np.zeros((60, 100, 3), dtype=np.uint8)
        frame[:, :, 0] = np.arange(100, dtype=np.uint8)
        config = YOLO_BACKENDS.normalize_backend_config({
            "inference_mode": "sahi",
            "sahi_slice_width": 60,
            "sahi_slice_height": 60,
            "sahi_overlap_width_ratio": 1 / 3,
            "sahi_overlap_height_ratio": 0,
            "sahi_merge_metric": "ios",
            "sahi_merge_threshold": 0.5,
        })
        wrapped_backend = YOLO_BACKENDS.SahiYoloBackend(Backend(), config)

        detections, details, metadata = wrapped_backend.infer(frame)

        self.assertEqual(len(detections), 1)
        self.assertEqual(len(details), 1)
        self.assertEqual(detections[0]["box"], [45.0, 10.0, 55.0, 20.0])
        self.assertEqual(detections[0]["confidence"], 0.9)
        self.assertEqual(metadata["inference_mode"], "sahi")
        self.assertEqual(metadata["sahi_slice_count"], 2)
        self.assertEqual(metadata["sahi_detections_before_merge"], 2)

        backend = wrapped_backend.backend
        wrapped_backend.cleanup()
        wrapped_backend.cleanup()
        self.assertTrue(backend.cleaned)

    def test_sahi_backend_rejects_excessive_slice_count(self):
        class Backend:
            name = "onnxruntime"
            model_path = "model.onnx"
            model_info = {}
            model = object()
            classes = {}
            input_width = 20
            input_height = 20
            output_adapter = None

            def infer(self, _frame):
                return [], [], {}

            def cleanup(self):
                pass

        config = YOLO_BACKENDS.normalize_backend_config({
            "inference_mode": "sahi",
            "sahi_slice_width": 20,
            "sahi_slice_height": 20,
            "sahi_max_slices": 2,
        })
        wrapped_backend = YOLO_BACKENDS.SahiYoloBackend(Backend(), config)

        with self.assertRaisesRegex(ValueError, "SAHI 切片数"):
            wrapped_backend.infer(np.zeros((60, 100, 3), dtype=np.uint8))

    def test_create_backend_wraps_selected_backend_in_sahi_mode(self):
        class Backend:
            name = "ultralytics"
            model_path = "model.pt"
            model_info = {}
            model = object()
            classes = {}
            input_width = 640
            input_height = 640
            output_adapter = None

            def cleanup(self):
                pass

        original_backend = YOLO_BACKENDS.UltralyticsBackend
        selected_backend = Backend()
        YOLO_BACKENDS.UltralyticsBackend = lambda *_args, **_kwargs: selected_backend
        try:
            backend = YOLO_BACKENDS.create_backend(
                "model.pt",
                {},
                {"inference_mode": "sahi"},
            )
        finally:
            YOLO_BACKENDS.UltralyticsBackend = original_backend

        self.assertIsInstance(backend, YOLO_BACKENDS.SahiYoloBackend)
        self.assertIs(backend.backend, selected_backend)

    def test_onnx_backend_detects_nhwc_uint8_input(self):
        class InputDefinition:
            name = "images"
            shape = [1, 320, 640, 3]
            type = "tensor(uint8)"

        class Session:
            def __init__(self, _path, providers=None):
                self.providers = providers or ["CPUExecutionProvider"]
                self.last_tensor = None

            def get_inputs(self):
                return [InputDefinition()]

            def get_providers(self):
                return self.providers

            def run(self, _outputs, inputs):
                self.last_tensor = inputs["images"]
                return [np.empty((1, 0, 6), dtype=np.float32)]

        YOLO_BACKENDS.ort = types.SimpleNamespace(InferenceSession=Session)
        YOLO_BACKENDS.ONNXRUNTIME_IMPORT_ERROR = None
        backend = YOLO_BACKENDS.ONNXRuntimeBackend(
            "model.onnx",
            {},
            YOLO_BACKENDS.normalize_backend_config({}),
        )

        _, _, metadata = backend.infer(np.zeros((100, 200, 3), dtype=np.uint8))

        self.assertEqual((backend.input_width, backend.input_height), (640, 320))
        self.assertEqual(backend.session.last_tensor.shape, (1, 320, 640, 3))
        self.assertEqual(backend.session.last_tensor.dtype, np.uint8)
        self.assertEqual(metadata["onnx_input_layout"], "nhwc")
        self.assertEqual(metadata["onnx_input_dtype"], "uint8")
        self.assertFalse(metadata["onnx_normalize"])
        backend.cleanup()
        self.assertIsNone(backend.model)

    def test_rknn_backend_releases_runtime_when_initialization_fails(self):
        class RKNNLite:
            NPU_CORE_AUTO = 0
            last_instance = None

            def __init__(self):
                self.released = False
                RKNNLite.last_instance = self

            def load_rknn(self, _path):
                return 0

            def init_runtime(self, core_mask=None):
                return -1

            def release(self):
                self.released = True

        original_runtime = YOLO_BACKENDS.RKNNLite
        original_error = YOLO_BACKENDS.RKNNLITE_IMPORT_ERROR
        YOLO_BACKENDS.RKNNLite = RKNNLite
        YOLO_BACKENDS.RKNNLITE_IMPORT_ERROR = None
        try:
            with self.assertRaisesRegex(RuntimeError, "init_runtime"):
                YOLO_BACKENDS.RKNNBackend(
                    "model.rknn",
                    {},
                    YOLO_BACKENDS.normalize_backend_config({}),
                )
            self.assertTrue(RKNNLite.last_instance.released)
        finally:
            YOLO_BACKENDS.RKNNLite = original_runtime
            YOLO_BACKENDS.RKNNLITE_IMPORT_ERROR = original_error

    def test_rknn_backend_pool_reuses_runtime_until_last_reference_closes(self):
        class RKNNLite:
            NPU_CORE_AUTO = 0
            instances = []

            def __init__(self):
                self.released = False
                RKNNLite.instances.append(self)

            def load_rknn(self, _path):
                return 0

            def init_runtime(self, core_mask=None):
                return 0

            def release(self):
                self.released = True

        original_runtime = YOLO_BACKENDS.RKNNLite
        original_error = YOLO_BACKENDS.RKNNLITE_IMPORT_ERROR
        YOLO_BACKENDS._reset_rknn_runtime_pool_for_tests()
        YOLO_BACKENDS.RKNNLite = RKNNLite
        YOLO_BACKENDS.RKNNLITE_IMPORT_ERROR = None
        try:
            config = YOLO_BACKENDS.normalize_backend_config({})
            first = YOLO_BACKENDS.RKNNBackend("model.rknn", {}, config)
            second = YOLO_BACKENDS.RKNNBackend("model.rknn", {}, config)

            self.assertIs(first.model, second.model)
            self.assertEqual(len(RKNNLite.instances), 1)
            first.cleanup()
            self.assertFalse(RKNNLite.instances[0].released)
            second.cleanup()
            self.assertTrue(RKNNLite.instances[0].released)
            second.cleanup()  # cleanup is idempotent
        finally:
            YOLO_BACKENDS._reset_rknn_runtime_pool_for_tests()
            YOLO_BACKENDS.RKNNLite = original_runtime
            YOLO_BACKENDS.RKNNLITE_IMPORT_ERROR = original_error

    def test_shared_client_routes_rknn_without_loading_local_runtime(self):
        original_mode = YOLO_BACKENDS._SHARED_INFERENCE_CLIENT_MODE
        original_rknn_mode = YOLO_BACKENDS._SHARED_RKNN_CLIENT_MODE
        original_proxy = YOLO_BACKENDS.SharedRKNNBackend
        sentinel = object()
        YOLO_BACKENDS._SHARED_INFERENCE_CLIENT_MODE = True
        YOLO_BACKENDS._SHARED_RKNN_CLIENT_MODE = True
        YOLO_BACKENDS.SharedRKNNBackend = lambda *_args, **_kwargs: sentinel
        try:
            backend = YOLO_BACKENDS.create_backend(
                "model.rknn",
                {"framework": "rknn"},
                {"backend": "auto"},
            )
            self.assertIs(backend, sentinel)
        finally:
            YOLO_BACKENDS._SHARED_INFERENCE_CLIENT_MODE = original_mode
            YOLO_BACKENDS._SHARED_RKNN_CLIENT_MODE = original_rknn_mode
            YOLO_BACKENDS.SharedRKNNBackend = original_proxy

    def test_shared_rknn_is_opt_in_even_when_general_shared_mode_is_enabled(self):
        original_mode = YOLO_BACKENDS._SHARED_INFERENCE_CLIENT_MODE
        original_rknn_mode = YOLO_BACKENDS._SHARED_RKNN_CLIENT_MODE
        original_local = YOLO_BACKENDS.RKNNBackend
        sentinel = object()
        YOLO_BACKENDS._SHARED_INFERENCE_CLIENT_MODE = True
        YOLO_BACKENDS._SHARED_RKNN_CLIENT_MODE = False
        YOLO_BACKENDS.RKNNBackend = lambda *_args, **_kwargs: sentinel
        try:
            backend = YOLO_BACKENDS.create_backend(
                "model.rknn",
                {"framework": "rknn"},
                {"backend": "auto"},
            )
            self.assertIs(backend, sentinel)
        finally:
            YOLO_BACKENDS._SHARED_INFERENCE_CLIENT_MODE = original_mode
            YOLO_BACKENDS._SHARED_RKNN_CLIENT_MODE = original_rknn_mode
            YOLO_BACKENDS.RKNNBackend = original_local


class YoloOutputAdapterTests(unittest.TestCase):
    def test_dense_profile_auto(self):
        adapter = YoloOutputAdapter(
            model_info={},
            config={"confidence": 0.5},
            classes={0: "person", 1: "helmet"},
            input_width=64,
            input_height=64,
        )
        outputs = [
            np.array([[[0.5, 0.5, 0.25, 0.25, 0.9, 0.1, 0.9]]], dtype=np.float32),
        ]

        detections, details, metadata = adapter.parse(
            outputs=outputs,
            frame_shape=(64, 64, 3),
            input_width=64,
            input_height=64,
            scale=1.0,
            pad_x=0,
            pad_y=0,
        )

        self.assertEqual(metadata["postprocess_profile"], "dense")
        self.assertEqual(len(detections), 1)
        self.assertEqual(details[0]["class"], 1)

    def test_dense_profile_auto_transposes_c_by_n_output(self):
        adapter = YoloOutputAdapter(
            model_info={},
            config={"confidence": 0.5},
            classes={0: "person", 1: "helmet"},
            input_width=64,
            input_height=64,
        )
        output = np.array(
            [[0.5], [0.5], [0.25], [0.25], [0.9], [0.1], [0.9]],
            dtype=np.float32,
        )

        detections, details, metadata = adapter.parse(
            outputs=[output],
            frame_shape=(64, 64, 3),
            input_width=64,
            input_height=64,
            scale=1.0,
            pad_x=0,
            pad_y=0,
        )

        self.assertEqual(metadata["postprocess_profile"], "dense")
        self.assertEqual(len(detections), 1)
        self.assertEqual(details[0]["class"], 1)

    def test_dense_profile_auto_transposes_single_class_channels_first_output(self):
        adapter = YoloOutputAdapter(
            model_info={},
            config={"confidence": 0.5},
            classes={},
            input_width=64,
            input_height=64,
        )
        output = np.full((1, 5, 6), -8.0, dtype=np.float32)
        output[0, :, 2] = np.array([0.5, 0.5, 0.25, 0.25, 8.0], dtype=np.float32)

        detections, details, metadata = adapter.parse(
            outputs=[output],
            frame_shape=(64, 64, 3),
            input_width=64,
            input_height=64,
            scale=1.0,
            pad_x=0,
            pad_y=0,
        )

        self.assertEqual(metadata["postprocess_profile"], "dense")
        self.assertEqual(len(detections), 1)
        self.assertEqual(details[0]["class"], 0)
        self.assertGreater(details[0]["confidence"], 0.9)

    def test_head_decoded_profile_channels_first(self):
        adapter = YoloOutputAdapter(
            model_info={},
            config={
                "confidence": 0.5,
                "postprocess_profile": "head_decoded",
                "postprocess_layout": "channels_first",
            },
            classes={0: "person", 1: "helmet"},
            input_width=64,
            input_height=64,
        )
        output = np.zeros((1, 7, 1, 1), dtype=np.float32)
        output[0, :, 0, 0] = np.array([0.5, 0.5, 0.25, 0.25, 0.95, 0.05, 0.95], dtype=np.float32)

        detections, details, metadata = adapter.parse(
            outputs=[output],
            frame_shape=(64, 64, 3),
            input_width=64,
            input_height=64,
            scale=1.0,
            pad_x=0,
            pad_y=0,
        )

        self.assertEqual(metadata["postprocess_profile"], "head_decoded")
        self.assertEqual(len(detections), 1)
        self.assertEqual(details[0]["class"], 1)

    def test_head_anchor_based_profile(self):
        adapter = YoloOutputAdapter(
            model_info={},
            config={
                "confidence": 0.5,
                "postprocess_profile": "head_anchor_based",
                "postprocess_anchor_count": 3,
                "model_postprocess": {
                    "anchors": [[[10, 13], [16, 30], [33, 23]]],
                    "strides": [8],
                    "layout": "channels_first",
                },
            },
            classes={0: "person"},
            input_width=64,
            input_height=64,
        )

        output = np.full((1, 18, 1, 1), -8.0, dtype=np.float32)
        output[0, 0:6, 0, 0] = np.array([0.0, 0.0, 0.0, 0.0, 8.0, 8.0], dtype=np.float32)

        detections, details, metadata = adapter.parse(
            outputs=[output],
            frame_shape=(64, 64, 3),
            input_width=64,
            input_height=64,
            scale=1.0,
            pad_x=0,
            pad_y=0,
        )

        self.assertEqual(metadata["postprocess_profile"], "head_anchor_based")
        self.assertEqual(len(detections), 1)
        self.assertEqual(details[0]["class"], 0)
        self.assertGreater(details[0]["confidence"], 0.9)

    def test_auto_profile_detects_dfl_split_head(self):
        adapter = YoloOutputAdapter(
            model_info={},
            config={
                "confidence": 0.5,
                "model_postprocess": {
                    "strides": [32],
                    "reg_max": 4,
                    "layout": "channels_first",
                },
            },
            classes={0: "person", 1: "helmet"},
            input_width=64,
            input_height=64,
        )

        box_output = np.full((1, 16, 2, 2), -8.0, dtype=np.float32)
        cls_output = np.full((1, 2, 2, 2), -8.0, dtype=np.float32)

        for side in range(4):
            channel_offset = side * 4
            box_output[0, channel_offset + 1, 0, 0] = 8.0
        cls_output[0, 1, 0, 0] = 8.0

        detections, details, metadata = adapter.parse(
            outputs=[box_output, cls_output],
            frame_shape=(64, 64, 3),
            input_width=64,
            input_height=64,
            scale=1.0,
            pad_x=0,
            pad_y=0,
        )

        self.assertEqual(metadata["postprocess_profile"], "head_dfl")
        self.assertEqual(len(detections), 1)
        self.assertEqual(details[0]["class"], 1)
        self.assertGreater(details[0]["confidence"], 0.9)

    def test_auto_profile_warns_for_raw_multi_anchor_head_without_adapter(self):
        adapter = YoloOutputAdapter(
            model_info={},
            config={"confidence": 0.5},
            classes={0: "person", 1: "helmet"},
            input_width=64,
            input_height=64,
        )

        output = np.zeros((1, 21, 2, 2), dtype=np.float32)
        detections, details, metadata = adapter.parse(
            outputs=[output],
            frame_shape=(64, 64, 3),
            input_width=64,
            input_height=64,
            scale=1.0,
            pad_x=0,
            pad_y=0,
        )

        self.assertEqual(metadata["postprocess_profile"], "unsupported")
        self.assertIn("postprocess_warning", metadata)
        self.assertEqual(detections, [])
        self.assertEqual(details, [])


if __name__ == "__main__":
    unittest.main()
