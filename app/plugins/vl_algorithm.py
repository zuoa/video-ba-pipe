"""OpenAI-compatible vision-language algorithm plugin."""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Dict, Optional

import numpy as np
import requests

from app import logger
from app.config import VIDEO_FRAME_PIXEL_FORMAT
from app.core.algorithm import BaseAlgorithm
from app.core.cv2_compat import cv2, require_cv2
from app.core.frame_utils import detect_frame_pixel_format, frame_to_rgb, infer_frame_dimensions


VL_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "has_detection": {"type": "boolean"},
        "detections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label_name": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "bbox": {
                        "anyOf": [
                            {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 4,
                                "maxItems": 4,
                            },
                            {"type": "null"},
                        ]
                    },
                },
                "required": ["label_name", "confidence", "bbox"],
                "additionalProperties": False,
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["has_detection", "detections", "reason"],
    "additionalProperties": False,
}

_PROMPT_VARIABLES = {
    "workflow_name",
    "source_id",
    "source_name",
    "source_code",
    "frame_width",
    "frame_height",
    "upstream_results_json",
    "roi_regions_json",
}


class VLResponseError(ValueError):
    """Raised when a VL response does not satisfy the algorithm contract."""


def build_chat_completions_endpoint(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _extract_response_text(response_data: Dict[str, Any]) -> str:
    choices = response_data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise VLResponseError("响应缺少 choices[0]")

    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(parts).strip()
    raise VLResponseError("响应缺少文本 content")


def _parse_json_text(text: str) -> Dict[str, Any]:
    candidate = str(text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?", "", candidate, flags=re.IGNORECASE).strip()
        candidate = re.sub(r"```$", "", candidate).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise VLResponseError(f"模型返回内容不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise VLResponseError("模型返回 JSON 必须是对象")
    return parsed


def normalize_vl_result(
    payload: Dict[str, Any],
    frame_width: Optional[int] = None,
    frame_height: Optional[int] = None,
) -> Dict[str, Any]:
    if not isinstance(payload.get("has_detection"), bool):
        raise VLResponseError("has_detection 必须是布尔值")
    detections = payload.get("detections")
    if not isinstance(detections, list):
        raise VLResponseError("detections 必须是数组")
    if not isinstance(payload.get("reason"), str):
        raise VLResponseError("reason 必须是字符串")

    normalized = []
    for index, item in enumerate(detections):
        if not isinstance(item, dict):
            raise VLResponseError(f"detections[{index}] 必须是对象")
        label_name = str(item.get("label_name") or "").strip()
        if not label_name:
            raise VLResponseError(f"detections[{index}].label_name 不能为空")
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise VLResponseError(f"detections[{index}].confidence 必须是数字") from exc
        if not 0 <= confidence <= 1:
            raise VLResponseError(f"detections[{index}].confidence 必须在 0 到 1 之间")

        bbox = item.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise VLResponseError(f"detections[{index}].bbox 必须是四元素数组或 null")
            try:
                bbox = [float(value) for value in bbox]
            except (TypeError, ValueError) as exc:
                raise VLResponseError(f"detections[{index}].bbox 必须只包含数字") from exc

            if max(abs(value) for value in bbox) <= 1.5:
                if not frame_width or not frame_height:
                    raise VLResponseError(f"detections[{index}].bbox 是归一化坐标，但缺少帧尺寸")
                bbox = [
                    bbox[0] * frame_width,
                    bbox[1] * frame_height,
                    bbox[2] * frame_width,
                    bbox[3] * frame_height,
                ]

            if frame_width and frame_height:
                bbox = [
                    max(0.0, min(bbox[0], float(frame_width))),
                    max(0.0, min(bbox[1], float(frame_height))),
                    max(0.0, min(bbox[2], float(frame_width))),
                    max(0.0, min(bbox[3], float(frame_height))),
                ]
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                raise VLResponseError(f"detections[{index}].bbox 必须是有效的 xyxy 坐标")

        detection = {
            "label_name": label_name,
            "class_name": label_name,
            "confidence": confidence,
            "semantic": bbox is None,
        }
        if bbox is not None:
            detection["bbox"] = bbox
            detection["box"] = bbox
        normalized.append(detection)

    has_detection = payload["has_detection"]
    if has_detection != bool(normalized):
        raise VLResponseError("has_detection 必须与 detections 是否为空保持一致")
    return {
        "has_detection": has_detection,
        "detections": normalized,
        "reason": payload["reason"].strip(),
    }


class VLAlgorithm(BaseAlgorithm):
    name = "vl_algorithm"

    def load_model(self):
        self.vl_config = dict(self.config.get("vl_config") or {})
        self.endpoint = build_chat_completions_endpoint(self.vl_config.get("base_url", ""))

    def _render_prompt(
        self,
        frame_width: int,
        frame_height: int,
        upstream_results: Optional[Dict[str, Any]],
        roi_regions: Optional[list],
    ) -> str:
        template = str(self.vl_config.get("prompt_template") or "").strip()
        context = {
            "workflow_name": self.config.get("workflow_name") or "",
            "source_id": self.config.get("source_id") or "",
            "source_name": self.config.get("source_name") or "",
            "source_code": self.config.get("source_code") or "",
            "frame_width": frame_width,
            "frame_height": frame_height,
            "upstream_results_json": json.dumps(upstream_results or {}, ensure_ascii=False, default=str),
            "roi_regions_json": json.dumps(roi_regions or [], ensure_ascii=False, default=str),
        }
        for key in _PROMPT_VARIABLES:
            template = template.replace("{" + key + "}", str(context[key]))

        if roi_regions:
            template += (
                "\n\n本次请求已将 ROI 区域之外的画面遮蔽。"
                "只允许判断以下 ROI 内的目标或事件，忽略区域外内容："
                f"{context['roi_regions_json']}"
            )

        contract = (
            "\n\n请只返回一个 JSON 对象，不要包含 Markdown。格式必须为："
            '{"has_detection": boolean, "detections": '
            '[{"label_name": string, "confidence": 0到1, "bbox": [x1,y1,x2,y2]或null}], '
            '"reason": string}。每个命中目标或事件返回一条 detections；不能可靠定位时 bbox 返回 null。'
        )
        return template + contract

    @staticmethod
    def _frame_to_data_url(frame_rgb: np.ndarray) -> str:
        require_cv2()
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        ok, encoded = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            raise RuntimeError("JPEG 编码失败")
        return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")

    def _empty_result(self, error: str, latency_ms: Optional[float] = None) -> Dict[str, Any]:
        metadata = {
            "error": error,
            "vl_model": self.vl_config.get("model_name"),
            "vl_checked": False,
        }
        if latency_ms is not None:
            metadata["latency_ms"] = round(latency_ms, 2)
        return {"detections": [], "metadata": metadata}

    def process(self, frame: np.ndarray, roi_regions: list = None, upstream_results: dict = None) -> dict:
        started_at = time.perf_counter()
        required = ("base_url", "api_key", "model_name", "prompt_template")
        missing = [key for key in required if not str(self.vl_config.get(key) or "").strip()]
        if missing:
            return self._empty_result(f"VL 配置缺少字段: {', '.join(missing)}")

        try:
            pixel_format = detect_frame_pixel_format(
                frame,
                pixel_format=self.config.get("pixel_format", VIDEO_FRAME_PIXEL_FORMAT),
            )
            frame_width, frame_height = infer_frame_dimensions(frame, pixel_format=pixel_format)
            frame_rgb = frame_to_rgb(
                frame,
                pixel_format=pixel_format,
                width=frame_width,
                height=frame_height,
            )
            roi_mask = None
            request_frame_rgb = frame_rgb
            if roi_regions:
                roi_mask = self.create_roi_mask(frame_rgb.shape, roi_regions)
                request_frame_rgb = self.apply_roi_mask(frame_rgb, roi_mask)
            prompt = self._render_prompt(frame_width, frame_height, upstream_results, roi_regions)
            image_detail = self.vl_config.get("image_detail") or "auto"
            payload = {
                "model": self.vl_config["model_name"],
                "temperature": float(self.vl_config.get("temperature", 0)),
                "max_tokens": int(self.vl_config.get("max_tokens", 512)),
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": self._frame_to_data_url(request_frame_rgb),
                                    "detail": image_detail,
                                },
                            },
                        ],
                    }
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "vl_detection_result",
                        "strict": True,
                        "schema": VL_RESPONSE_SCHEMA,
                    },
                },
            }
            extra_body = self.vl_config.get("extra_body")
            if isinstance(extra_body, dict):
                payload.update(extra_body)

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.vl_config['api_key']}",
            }
            extra_headers = self.vl_config.get("extra_headers")
            if isinstance(extra_headers, dict):
                headers.update({str(key): str(value) for key, value in extra_headers.items()})

            timeout = int(
                self.config.get("vl_timeout_override_seconds")
                or self.vl_config.get("timeout_seconds")
                or 30
            )
            response = requests.post(self.endpoint, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            response_data = response.json()
            response_text = _extract_response_text(response_data)
            normalized = normalize_vl_result(
                _parse_json_text(response_text),
                frame_width=frame_width,
                frame_height=frame_height,
            )
            detections = normalized["detections"]
            detections_before_roi = len(detections)
            if roi_mask is not None:
                roi_filtered = []
                for detection in detections:
                    box = detection.get("box")
                    if box is None:
                        # 无框语义结果来自已经遮蔽 ROI 外区域的图像，可以继续参与条件判断。
                        roi_filtered.append(detection)
                        continue
                    center_x = min(max(int((box[0] + box[2]) / 2), 0), frame_width - 1)
                    center_y = min(max(int((box[1] + box[3]) / 2), 0), frame_height - 1)
                    if roi_mask[center_y, center_x] > 0:
                        roi_filtered.append(detection)
                detections = roi_filtered
            latency_ms = (time.perf_counter() - started_at) * 1000
            return {
                "detections": detections,
                "metadata": {
                    "vl_checked": True,
                    "vl_reason": normalized["reason"],
                    "vl_model": self.vl_config.get("model_name"),
                    "latency_ms": round(latency_ms, 2),
                    "usage": response_data.get("usage") or {},
                    "raw_response": response_text[:4000],
                    "roi_applied": roi_mask is not None,
                    "roi_filtered_count": detections_before_roi - len(detections),
                },
                "exec_time_ms": latency_ms,
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "VL algorithm request failed: algorithm=%s endpoint=%s error=%s",
                self.config.get("name"),
                self.endpoint,
                exc,
            )
            return self._empty_result(str(exc), latency_ms)
