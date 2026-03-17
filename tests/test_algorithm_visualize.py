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
