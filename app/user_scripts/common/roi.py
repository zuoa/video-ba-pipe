"""
ROI handling helpers.

Rules:
- mode == "pre_mask": apply mask before inference
- mode == "post_filter": filter detections after inference
"""

from typing import Any, Dict, List, Tuple

import numpy as np

from app.core.algorithm import BaseAlgorithm


def _split_regions(roi_regions: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    pre_mask = []
    post_filter = []
    for region in roi_regions or []:
        mode = (region.get('mode') or 'post_filter').lower()
        if mode == 'pre_mask':
            pre_mask.append(region)
        else:
            post_filter.append(region)
    return pre_mask, post_filter


def apply_roi(frame: np.ndarray, detections: List[Dict[str, Any]], roi_regions: List[Dict[str, Any]]):
    """
    Apply ROI rules to frame and detections.
    Returns (frame, detections).
    """
    if roi_regions is None:
        return frame, detections

    pre_mask_regions, post_filter_regions = _split_regions(roi_regions)

    if pre_mask_regions:
        mask = BaseAlgorithm.create_roi_mask(frame.shape, pre_mask_regions)
        frame = BaseAlgorithm.apply_roi_mask(frame, mask)

    if post_filter_regions and detections:
        mask = BaseAlgorithm.create_roi_mask(frame.shape, post_filter_regions)
        detections = BaseAlgorithm.filter_detections_by_roi(detections, mask)

    return frame, detections

