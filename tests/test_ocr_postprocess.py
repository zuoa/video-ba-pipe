import numpy as np
import pytest

from app.core.ocr_postprocess import (
    ctc_greedy_decode,
    db_detect_polygons,
    parse_input_size,
    prepare_recognition_image,
    sort_text_polygons,
)


def test_parse_input_size_accepts_width_height_and_chw():
    assert parse_input_size("480x480", (640, 640)) == (480, 480)
    assert parse_input_size("320x48", (320, 48)) == (320, 48)
    assert parse_input_size("48x320", (320, 48)) == (320, 48)
    assert parse_input_size("3,48,320", (320, 48)) == (320, 48)
    assert parse_input_size((320, 48), (1, 1)) == (320, 48)
    assert parse_input_size((1, 3, 480, 480), (1, 1)) == (480, 480)
    assert parse_input_size((1, 48, 320, 3), (1, 1)) == (320, 48)


def test_ctc_greedy_decode_collapses_blanks_and_repeats():
    characters = ["A", "B"]
    logits = np.array(
        [
            [0.1, 0.8, 0.1],  # A
            [0.1, 0.8, 0.1],  # A repeat
            [0.9, 0.05, 0.05],  # blank
            [0.1, 0.1, 0.8],  # B
        ],
        dtype=np.float32,
    )

    text, score = ctc_greedy_decode(logits, characters)

    assert text == "AB"
    assert score == pytest.approx(0.8)


def test_db_detect_polygons_returns_scaled_box():
    heatmap = np.zeros((32, 32), dtype=np.float32)
    heatmap[8:24, 6:26] = 0.95

    detections = db_detect_polygons(
        heatmap,
        source_width=64,
        source_height=64,
        thresh=0.3,
        box_thresh=0.5,
        unclip_ratio=1.5,
        max_candidates=8,
        min_size=2,
    )

    assert detections
    polygon, score = detections[0]
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    assert score > 0.5
    assert min(xs) < 20
    assert max(xs) > 40
    assert min(ys) < 24
    assert max(ys) > 40


def test_prepare_recognition_image_pads_to_target_width():
    image = np.full((20, 40, 3), 7, dtype=np.uint8)
    prepared = prepare_recognition_image(image, width=32, height=8)

    assert prepared.shape == (8, 32, 3)
    assert np.count_nonzero(prepared[:, :16]) > 0
    assert np.all(prepared[:, 16:] == 0)


def test_db_detect_polygons_accepts_nhwc_output():
    heatmap = np.zeros((1, 32, 32, 1), dtype=np.float32)
    heatmap[:, 8:24, 6:26, :] = 0.95

    detections = db_detect_polygons(
        heatmap,
        source_width=64,
        source_height=64,
        box_thresh=0.5,
        min_size=2,
    )

    assert detections


def test_sort_text_polygons_uses_reading_order():
    detections = [
        ([[40, 5], [50, 5], [50, 9], [40, 9]], 0.9),
        ([[5, 6], [15, 6], [15, 10], [5, 10]], 0.8),
        ([[3, 30], [13, 30], [13, 34], [3, 34]], 0.7),
    ]

    ordered = sort_text_polygons(detections)

    assert [item[0][0][0] for item in ordered] == [5, 40, 3]
