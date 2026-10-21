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
from app.core.vl_algorithm_config import VL_RESPONSE_FORMAT_MODES
from app.user_scripts.common.roi import filter_items_by_regions, split_regions


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
    if not isinstance(response_data, dict):
        raise VLResponseError("接口响应必须是 JSON 对象")
    choices = response_data.get("choices") or []
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise VLResponseError("响应缺少 choices[0]")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise VLResponseError("响应缺少 message")
    content = message.get("content")
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
        self._compatible_response_format = None

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
            "\n\n【系统统一输出协议】\n"
            "检测类别、命中条件和需要忽略的情况，以本算法上面的业务判断要求为准。"
            "以下示例只说明输出结构，不代表实际检测结论；若上文的输出格式要求与本协议冲突，以本协议为准。\n"
            "1. 只返回一个 JSON 对象，不要包含 Markdown、代码块或对象之外的文字。"
            "顶层不能是数组、字符串、true、false 或 null。\n"
            "2. 必须且仅包含 has_detection、detections、reason 三个字段。"
            "has_detection 必须是布尔值，detections 必须是数组，reason 必须是简要说明画面依据的字符串。\n"
            "3. 每个命中目标或事件返回一条 detections，检测项必须且仅包含 label_name、confidence、bbox。"
            "label_name 使用本算法业务要求中的目标或事件名称，不要照抄示例名称；"
            "confidence 是 0 到 1 的数字；不能可靠定位的语义事件，bbox 返回 null。\n"
            f"4. bbox 使用原图像素坐标 [x1,y1,x2,y2]，画面宽 {frame_width}、高 {frame_height}，"
            "必须满足 0<=x1<x2<=画面宽、0<=y1<y2<=画面高。\n"
            "5. has_detection 必须与 detections 是否非空一致。未命中时返回 false 和空数组，"
            "不要为未命中结果创建检测项。无法确认的目标不要猜测。\n"
            "命中时的结构示例（名称、置信度、位置及原因均需按实际画面填写）：\n"
            '{"has_detection":true,"detections":[{"label_name":"业务目标或事件名称",'
            '"confidence":0.9,"bbox":null}],"reason":"画面中存在符合业务条件的目标或事件"}\n'
            "未命中时的结构示例：\n"
            '{"has_detection":false,"detections":[],"reason":"未发现符合业务条件的目标或事件"}'
        )
        return template + contract

    def _response_preview(self, value: Any) -> str:
        text = str(value or "")
        api_key = str(self.vl_config.get("api_key") or "")
        if api_key:
            text = text.replace(api_key, "[REDACTED]")
        return text[:4000]

    @staticmethod
    def _format_not_supported(response) -> bool:
        if response is None or response.status_code not in (400, 422):
            return False
        detail = str(response.text or "").lower()
        format_fields = ("response_format", "json_schema", "json_object", "structured output", "结构化输出")
        rejection_terms = (
            "not support", "unsupported", "not available", "not implemented", "only support",
            "invalid", "unknown", "unrecognized", "unexpected", "not allowed", "not permitted",
            "not recognized", "not compatible", "must be", "不支持", "无效", "未知参数",
        )
        return any(field in detail for field in format_fields) and any(term in detail for term in rejection_terms)

    def _request_result(
        self,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        timeout: int,
        frame_width: int,
        frame_height: int,
        diagnostics: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        configured_mode = self.vl_config.get("response_format_mode") or "auto"
        if configured_mode not in VL_RESPONSE_FORMAT_MODES:
            raise VLResponseError(f"不支持的 VL 输出模式: {configured_mode}")
        modes = ["json_schema", "json_object", "text"] if configured_mode == "auto" else [configured_mode]
        mode_index = modes.index(self._compatible_response_format) if self._compatible_response_format in modes else 0
        diagnostics["response_format_requested"] = configured_mode
        diagnostics["request_attempts"] = 0
        deadline = time.perf_counter() + timeout
        correction = None

        while True:
            mode = modes[mode_index]
            request_payload = dict(payload)
            request_payload.pop("response_format", None)
            if mode == "json_schema":
                request_payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "vl_detection_result", "strict": True, "schema": VL_RESPONSE_SCHEMA},
                }
            elif mode == "json_object":
                request_payload["response_format"] = {"type": "json_object"}
            if correction:
                message = payload["messages"][0]
                request_payload["messages"] = [{
                    **message,
                    "content": [*message["content"], {"type": "text", "text": correction}],
                }]

            # Fallbacks and the single format correction share one timeout budget.
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise requests.Timeout("VL 请求及格式兼容重试已超过接口超时限制")
            diagnostics["response_format"] = mode
            diagnostics["request_attempts"] += 1
            response = requests.post(self.endpoint, headers=headers, json=request_payload, timeout=remaining)
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                detail = self._response_preview(response.text)
                diagnostics["http_error"] = detail
                if configured_mode == "auto" and mode_index < len(modes) - 1 and self._format_not_supported(response):
                    mode_index += 1
                    logger.info(
                        "VL response format fallback: algorithm=%s model=%s from=%s to=%s detail=%r",
                        self.config.get("name"), self.vl_config.get("model_name"), mode, modes[mode_index], detail,
                    )
                    continue
                raise VLResponseError(f"VL API HTTP {response.status_code}: {detail or exc}") from exc

            diagnostics.pop("http_error", None)
            diagnostics.pop("raw_response", None)
            try:
                response_data = response.json()
            except ValueError as exc:
                diagnostics["raw_response"] = self._response_preview(response.text)
                raise VLResponseError("接口响应不是合法 JSON") from exc
            try:
                response_text = _extract_response_text(response_data)
                diagnostics["raw_response"] = self._response_preview(response_text)
                normalized = normalize_vl_result(
                    _parse_json_text(response_text), frame_width=frame_width, frame_height=frame_height,
                )
            except VLResponseError as exc:
                if "raw_response" not in diagnostics:
                    diagnostics["raw_response"] = self._response_preview(json.dumps(response_data, ensure_ascii=False))
                if correction is not None:
                    raise
                correction = (
                    f"上一次返回结果不符合系统输出协议：{exc}。"
                    "请重新检查同一张图像，按本算法业务要求判断，严格遵守系统统一输出协议，"
                    "只返回包含 has_detection、detections、reason 的 JSON 对象。"
                )
                diagnostics["retry_reason"] = str(exc)
                # Some compatible endpoints silently ignore json_schema. Try JSON mode.
                if configured_mode == "auto" and mode == "json_schema":
                    mode_index += 1
                logger.warning(
                    "VL response validation failed; retrying once: algorithm=%s model=%s mode=%s error=%s raw_response=%r",
                    self.config.get("name"), self.vl_config.get("model_name"), mode,
                    exc, diagnostics.get("raw_response", ""),
                )
                continue
            if configured_mode == "auto":
                self._compatible_response_format = mode
            return normalized, response_data

    @staticmethod
    def _frame_to_data_url(frame_rgb: np.ndarray) -> str:
        require_cv2()
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        ok, encoded = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            raise RuntimeError("JPEG 编码失败")
        return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")

    def _empty_result(self, error: str, latency_ms: Optional[float] = None, diagnostics: Optional[dict] = None) -> Dict[str, Any]:
        metadata = {
            **(diagnostics or {}),
            "error": error,
            "vl_model": self.vl_config.get("model_name"),
            "vl_checked": False,
        }
        if latency_ms is not None:
            metadata["latency_ms"] = round(latency_ms, 2)
        return {"detections": [], "metadata": metadata}

    def process(self, frame: np.ndarray, roi_regions: list = None, upstream_results: dict = None) -> dict:
        started_at = time.perf_counter()
        diagnostics = {}
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
            roi_filter_regions = []
            request_frame_rgb = frame_rgb
            if roi_regions:
                roi_mask = self.create_roi_mask(frame_rgb.shape, roi_regions)
                request_frame_rgb = self.apply_roi_mask(frame_rgb, roi_mask)
                _, crop_infer_regions, post_filter_regions = split_regions(roi_regions)
                roi_filter_regions = crop_infer_regions + post_filter_regions
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
            normalized, response_data = self._request_result(
                payload, headers, timeout, frame_width, frame_height, diagnostics,
            )
            detections = normalized["detections"]
            detections_before_roi = len(detections)
            if roi_filter_regions:
                detections = filter_items_by_regions(
                    detections,
                    frame_shape=frame_rgb.shape,
                    roi_regions=roi_filter_regions,
                    metric="center",
                    # 无框语义结果来自已经遮蔽 ROI 外区域的图像，可以继续参与条件判断。
                    keep_unboxed=True,
                )
            latency_ms = (time.perf_counter() - started_at) * 1000
            return {
                "detections": detections,
                "metadata": {
                    **diagnostics,
                    "vl_checked": True,
                    "vl_reason": normalized["reason"],
                    "vl_model": self.vl_config.get("model_name"),
                    "latency_ms": round(latency_ms, 2),
                    "usage": response_data.get("usage") or {},
                    "roi_applied": roi_mask is not None,
                    "roi_filtered_count": detections_before_roi - len(detections),
                },
                "exec_time_ms": latency_ms,
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "VL algorithm request failed: algorithm=%s endpoint=%s model=%s mode=%s attempts=%s error=%s raw_response=%r",
                self.config.get("name"),
                self.endpoint,
                self.vl_config.get("model_name"),
                diagnostics.get("response_format"),
                diagnostics.get("request_attempts", 0),
                self._response_preview(exc),
                diagnostics.get("raw_response", diagnostics.get("http_error", "")),
            )
            return self._empty_result(self._response_preview(exc), latency_ms, diagnostics)
