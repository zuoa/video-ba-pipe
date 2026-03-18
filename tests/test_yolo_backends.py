import unittest
import types
import sys

import numpy as np

if "cv2" not in sys.modules:
    cv2_stub = types.ModuleType("cv2")

    class _DnnModule:
        @staticmethod
        def NMSBoxes(bboxes, scores, score_threshold, nms_threshold):
            return [idx for idx, score in enumerate(scores) if score >= score_threshold]

    cv2_stub.dnn = _DnnModule()
    sys.modules["cv2"] = cv2_stub

from app.user_scripts.common.yolo_backends import YoloOutputAdapter


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
