"""Helpers for rendering event information attached to detections."""

from typing import Any, Mapping


EVENT_LABELS = {
    "loiter": "徘徊",
    "stay": "停留",
    "region_cross": "穿越",
}


def get_detection_event_label(detection: Any, default: str = "停留") -> str:
    """Return the display label for a detection's event.

    Built-in event identifiers are the source of truth. ``event_label`` keeps
    custom events displayable, while ``default`` preserves legacy detections
    that only carried a dwell duration.
    """
    if not isinstance(detection, Mapping):
        return default

    attributes = detection.get("attributes")
    if not isinstance(attributes, Mapping):
        attributes = {}

    event = detection.get("event")
    if event is None:
        event = attributes.get("event")
    normalized_event = str(event or "").strip().lower()
    if normalized_event in EVENT_LABELS:
        return EVENT_LABELS[normalized_event]

    event_label = detection.get("event_label")
    if event_label is None:
        event_label = attributes.get("event_label")
    event_label = str(event_label or "").replace("\n", " ").strip()
    return event_label or default
