import numpy as np

from app.core.algorithm import BaseAlgorithm


def test_visualize_draws_bbox_alias_detection():
    frame_rgb = np.full((80, 80, 3), 255, dtype=np.uint8)
    detections = [{
        "bbox": [10, 10, 50, 50],
        "score": 0.92,
        "class_name": "person",
    }]

    rendered = BaseAlgorithm.visualize(
        frame_rgb,
        detections,
        label_color="#00FF00",
    )

    assert rendered is not None
    assert tuple(rendered[10, 10]) == (0, 255, 0)


def test_visualize_draws_main_box_even_with_stages():
    frame_rgb = np.full((80, 80, 3), 255, dtype=np.uint8)
    detections = [{
        "box": [5, 5, 40, 40],
        "confidence": 0.88,
        "label_name": "phone",
        "stages": [
            {
                "box": [12, 12, 24, 24],
                "confidence": 0.81,
                "label_name": "head",
                "label_color": "#0000FF",
            }
        ],
    }]

    rendered = BaseAlgorithm.visualize(
        frame_rgb,
        detections,
        label_color="#FF0000",
    )

    assert rendered is not None
    assert tuple(rendered[5, 5]) == (0, 0, 255)


def test_visualize_accepts_normalized_xywh_boxes():
    frame_rgb = np.full((80, 80, 3), 255, dtype=np.uint8)
    detections = [{
        "box": [0.5, 0.5, 0.4, 0.4],
        "confidence": 0.91,
        "label_name": "person",
    }]

    rendered = BaseAlgorithm.visualize(
        frame_rgb,
        detections,
        label_color="#0000FF",
    )

    assert rendered is not None
    assert tuple(rendered[24, 24]) == (255, 0, 0)
