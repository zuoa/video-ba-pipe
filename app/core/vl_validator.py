import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app import logging
from app.config import (
    VL_MODEL_BASE_URL,
    VL_MODEL_KEY,
    VL_MODEL_NAME,
    VL_MODEL_TIMEOUT_SECONDS,
)
from app.core.cv2_compat import cv2, require_cv2

try:
    from app.core.database_models import SystemSetting
except ImportError as exc:  # pragma: no cover - optional in lightweight test envs
    _VL_IMPORT_ERROR = exc

    class _MissingSystemSetting:
        key = None

        @classmethod
        def get_or_none(cls, *args, **kwargs):
            raise ImportError("SystemSetting requires peewee/database dependencies") from _VL_IMPORT_ERROR

        @classmethod
        def get_or_create(cls, *args, **kwargs):
            raise ImportError("SystemSetting requires peewee/database dependencies") from _VL_IMPORT_ERROR

    SystemSetting = _MissingSystemSetting

logger = logging.getLogger("vl_validator")

VL_SETTING_KEY = "vl_service_config"


@dataclass
class VLValidationResult:
    allowed: bool
    checked: bool
    reason: str = ""
    confidence: Optional[float] = None
    raw_response: Optional[str] = None


def get_vl_service_config() -> Dict[str, Any]:
    config = {
        "enabled": False,
        "base_url": VL_MODEL_BASE_URL,
        "model_name": VL_MODEL_NAME,
        "api_key": VL_MODEL_KEY,
        "timeout_seconds": VL_MODEL_TIMEOUT_SECONDS,
    }

    try:
        record = SystemSetting.get_or_none(SystemSetting.key == VL_SETTING_KEY)
        if record and record.value:
            stored = json.loads(record.value)
            if isinstance(stored, dict):
                config.update(
                    {
                        "enabled": bool(stored.get("enabled", config["enabled"])),
                        "base_url": (stored.get("base_url") or config["base_url"] or "").strip(),
                        "model_name": (stored.get("model_name") or config["model_name"] or "").strip(),
                        "api_key": (stored.get("api_key") or config["api_key"] or "").strip(),
                        "timeout_seconds": _safe_int(
                            stored.get("timeout_seconds"),
                            config["timeout_seconds"],
                        ),
                    }
                )
    except Exception as exc:
        logger.warning(f"读取 VL 配置失败，将回退到环境变量: {exc}")

    config["has_required_fields"] = bool(
        config["base_url"]
        and config["model_name"]
        and config["api_key"]
    )
    config["configured"] = bool(config["enabled"] and config["has_required_fields"])
    return config


def save_vl_service_config(data: Dict[str, Any], updated_by: str = "system") -> Dict[str, Any]:
    config = {
        "enabled": bool(data.get("enabled", False)),
        "base_url": (data.get("base_url") or "").strip(),
        "model_name": (
            data.get("model_name")
            or data.get("modalname")
            or data.get("modelName")
            or ""
        ).strip(),
        "api_key": (data.get("api_key") or data.get("key") or "").strip(),
        "timeout_seconds": _safe_int(data.get("timeout_seconds"), VL_MODEL_TIMEOUT_SECONDS),
    }

    record, _ = SystemSetting.get_or_create(
        key=VL_SETTING_KEY,
        defaults={
            "value": "",
            "description": "VL 模型核验配置",
            "updated_at": datetime.now(),
            "updated_by": updated_by,
        },
    )
    record.value = json.dumps(config, ensure_ascii=False)
    record.description = "VL 模型核验配置"
    record.updated_at = datetime.now()
    record.updated_by = updated_by
    record.save()
    return get_vl_service_config()


def validate_frame_with_vl(
    frame_rgb,
    alert_type: str,
    alert_message: str,
    result: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    prompt_template: Optional[str] = None,
    extra_context: Optional[Dict[str, Any]] = None,
) -> VLValidationResult:
    config = config or get_vl_service_config()

    if not config.get("enabled"):
        return VLValidationResult(allowed=True, checked=False, reason="VL 服务未启用")

    if not config.get("configured"):
        return VLValidationResult(allowed=True, checked=False, reason="VL 服务未完成配置")

    if frame_rgb is None:
        return VLValidationResult(allowed=True, checked=False, reason="缺少图像帧，跳过 VL 核验")

    try:
        image_data_url = _frame_to_data_url(frame_rgb)
    except Exception as exc:
        logger.warning(f"编码 VL 图像失败: {exc}")
        return VLValidationResult(allowed=True, checked=False, reason=f"图像编码失败: {exc}")

    prompt = _build_prompt(
        prompt_template=prompt_template,
        alert_type=alert_type,
        alert_message=alert_message,
        result=result or {},
        extra_context=extra_context or {},
    )

    payload = {
        "model": config["model_name"],
        "temperature": 0,
        "max_tokens": 300,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
    }

    endpoint = _build_endpoint(config["base_url"])
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=config["timeout_seconds"]) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        logger.warning(f"VL 请求失败 HTTP {exc.code}: {detail}")
        return VLValidationResult(
            allowed=True,
            checked=False,
            reason=f"VL 请求失败 HTTP {exc.code}",
            raw_response=detail or None,
        )
    except URLError as exc:
        logger.warning(f"VL 请求失败: {exc}")
        return VLValidationResult(
            allowed=True,
            checked=False,
            reason=f"VL 请求失败: {exc}",
        )
    except Exception as exc:
        logger.warning(f"VL 调用异常: {exc}")
        return VLValidationResult(allowed=True, checked=False, reason=f"VL 调用异常: {exc}")

    response_text = _extract_response_text(response_data)
    parsed = _parse_validation_response(response_text)
    if parsed is None:
        logger.warning(f"VL 响应无法解析，默认放行: {response_text}")
        return VLValidationResult(
            allowed=True,
            checked=False,
            reason="VL 响应无法解析，已跳过核验",
            raw_response=response_text,
        )

    return VLValidationResult(
        allowed=bool(parsed.get("is_alert")),
        checked=True,
        reason=(parsed.get("reason") or "").strip(),
        confidence=_safe_float(parsed.get("confidence")),
        raw_response=response_text,
    )


def _build_endpoint(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _frame_to_data_url(frame_rgb) -> str:
    require_cv2()
    frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
    ok, encoded = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise RuntimeError("JPEG 编码失败")
    import base64

    return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")


def render_prompt_template(
    prompt_template: str,
    alert_type: str,
    alert_message: str,
    result: Dict[str, Any],
    extra_context: Optional[Dict[str, Any]] = None,
) -> str:
    detections = result.get("detections") or []
    detection_lines = []
    for index, det in enumerate(detections[:20], start=1):
        detection_lines.append(
            f"{index}. class={det.get('class_name') or det.get('class_id') or 'unknown'}, "
            f"conf={det.get('confidence', 0):.3f}, bbox={det.get('bbox') or det.get('box') or det.get('xyxy')}"
        )

    if not detection_lines:
        detection_summary = "无检测框摘要"
    else:
        detection_summary = "\n".join(detection_lines)

    context = {
        "alert_type": alert_type or "detection",
        "alert_message": alert_message or "",
        "detection_count": len(detections),
        "detection_summary": detection_summary,
        "detections_json": json.dumps(detections, ensure_ascii=False),
    }
    if extra_context:
        context.update(extra_context)

    return prompt_template.format_map(_SafeFormatDict(context))


def _build_prompt(
    prompt_template: Optional[str],
    alert_type: str,
    alert_message: str,
    result: Dict[str, Any],
    extra_context: Optional[Dict[str, Any]] = None,
) -> str:
    normalized_template = (prompt_template or "").strip()
    if not normalized_template:
        normalized_template = (
            "你是视频告警复核助手。请基于图像内容和算法摘要判断当前场景是否应该触发告警。\n\n"
            "当前候选告警类型: {alert_type}\n"
            "当前候选告警消息: {alert_message}\n"
            "检测数量: {detection_count}\n"
            "算法摘要:\n{detection_summary}\n\n"
            "请只返回 JSON，格式严格如下："
            '{"is_alert": true 或 false, "confidence": 0 到 1 之间的小数, "reason": "简短原因"}。'
        )

    return render_prompt_template(
        prompt_template=normalized_template,
        alert_type=alert_type,
        alert_message=alert_message,
        result=result,
        extra_context=extra_context,
    )


def _extract_response_text(response_data: Dict[str, Any]) -> str:
    choices = response_data.get("choices") or []
    if not choices:
        return ""

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts).strip()

    return ""


def _parse_validation_response(text: str) -> Optional[Dict[str, Any]]:
    candidate = (text or "").strip()
    if not candidate:
        return None

    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?", "", candidate).strip()
        candidate = re.sub(r"```$", "", candidate).strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return _normalize_validation_response(parsed)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return _normalize_validation_response(parsed)
        except json.JSONDecodeError:
            pass

    lowered = candidate.lower()
    if "false" in lowered or "否" in candidate or "不告警" in candidate:
        return {"is_alert": False, "confidence": None, "reason": candidate[:200]}
    if "true" in lowered or "是" in candidate:
        return {"is_alert": True, "confidence": None, "reason": candidate[:200]}
    return None


def _normalize_validation_response(data: Dict[str, Any]) -> Dict[str, Any]:
    raw_flag = data.get("is_alert")
    if isinstance(raw_flag, str):
        normalized = raw_flag.strip().lower()
        if normalized in {"true", "yes", "1", "y", "是"}:
            raw_flag = True
        elif normalized in {"false", "no", "0", "n", "否"}:
            raw_flag = False

    return {
        "is_alert": bool(raw_flag),
        "confidence": _safe_float(data.get("confidence")),
        "reason": data.get("reason") or "",
    }


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"
