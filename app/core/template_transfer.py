"""Portable workflow-template export/import services.

The transfer format intentionally separates portable resource identities from
database primary keys.  A package is a regular ZIP whose first, uncompressed
entry is ``manifest.json`` so browsers can inspect compatibility before
uploading a potentially large model bundle.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit

from werkzeug.utils import secure_filename

from app.config import DEVICE_MODEL_CODE, MODEL_SAVE_PATH, TEMPLATE_TRANSFER_PATH
from app.core.database_models import (
    Algorithm,
    AlgorithmHook,
    ExternalApi,
    Hook,
    MLModel,
    Workflow,
    db,
)
from app.core.inference_resource_config import detect_inference_capabilities
from app.core.license_service import quota_capacity
from app.core.ocr_runtime import is_ocr_runtime_available, ocr_backend_family
from app.core.script_loader import get_script_loader
from app.core.webhook_workflow_config import (
    mask_workflow_webhook_secrets,
    merge_workflow_webhook_secrets,
    validate_workflow_webhook_nodes,
)
from app.core.http_request_workflow import (
    mask_workflow_http_request_secrets,
    merge_workflow_http_request_secrets,
    validate_http_value_conditions,
)
from app.core.workflow_runtime import get_node_type, validate_template_source_node
from app.version import get_app_version


FORMAT_NAME = "video-ba-workflow-template"
FORMAT_VERSION = 1
MAX_ARCHIVE_ENTRIES = 20_000
MAX_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_PACKAGE_BYTES = 9 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SCRIPT_DEPENDENCIES = 200
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "api-key",
}
SENSITIVE_URL_PARAMETER_NAMES = {
    "access_key",
    "access_token",
    "api-key",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "key",
    "password",
    "secret",
    "signature",
    "token",
}


class TemplateTransferError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"code": self.code, "error": self.message, **self.details}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_sensitive_header_name(name: Any) -> bool:
    normalized = str(name or "").strip().lower()
    if normalized in SENSITIVE_HEADER_NAMES:
        return True
    return any(marker in normalized for marker in ("authorization", "api-key", "api_key", "secret", "token"))


def _url_contains_credentials(value: Any) -> bool:
    url = str(value or "").strip()
    if not url:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return True
    if parsed.username is not None or parsed.password is not None:
        return True
    for name, parameter_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = str(name or "").strip().lower()
        if parameter_value and (
            normalized in SENSITIVE_URL_PARAMETER_NAMES
            or any(
                marker in normalized
                for marker in (
                    "api-key",
                    "api_key",
                    "apikey",
                    "credential",
                    "password",
                    "secret",
                    "signature",
                    "token",
                )
            )
        ):
            return True
    return False


def _hook_fingerprint(payload: dict) -> str:
    value = deepcopy(payload)
    for key in (
        "portable_id",
        "name",
        "script_path",
        "relation_enabled",
        "hook_config",
        "fingerprint",
    ):
        value.pop(key, None)
    value["scripts"] = sorted(
        script.get("sha256")
        for script in value.get("scripts", [])
        if isinstance(script, dict) and script.get("sha256")
    )
    return _sha256_bytes(_canonical_json(value))


def _content_fingerprint(value: Any) -> str:
    """Hash semantic JSON content while treating absent and null fields alike."""
    if isinstance(value, dict):
        normalized = {
            key: _normalize_fingerprint_value(item)
            for key, item in value.items()
            if item is not None
        }
    else:
        normalized = _normalize_fingerprint_value(value)
    return _sha256_bytes(_canonical_json(normalized))


def _normalize_fingerprint_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_fingerprint_value(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_normalize_fingerprint_value(item) for item in value]
    return value


def _template_content_fingerprint(
    graph: dict,
    algorithms: list[dict],
    external_apis: list[dict],
    models: list[dict],
) -> str:
    def identities(items: list[dict], content_key: str) -> list[dict]:
        return sorted(
            (
                {
                    "portable_id": str(item.get("portable_id") or ""),
                    content_key: str(item.get(content_key) or ""),
                }
                for item in items
            ),
            key=lambda item: item["portable_id"],
        )

    return _content_fingerprint({
        "graph": graph,
        "algorithms": identities(algorithms, "fingerprint"),
        "external_apis": identities(external_apis, "fingerprint"),
        "models": sorted(
            (
                {
                    "portable_id": str(item.get("portable_id") or ""),
                    "artifact_sha256": str(item.get("artifact_sha256") or ""),
                    "metadata_fingerprint": str(item.get("metadata_fingerprint") or ""),
                }
                for item in models
            ),
            key=lambda item: item["portable_id"],
        ),
    })


def _model_metadata_fingerprint(payload: dict) -> str:
    return _content_fingerprint({
        "model_type": payload.get("model_type"),
        "model_role": payload.get("model_role"),
        "framework": payload.get("framework"),
        "input_shape": payload.get("input_shape"),
        "classes": payload.get("classes") or {},
        "model_postprocess": payload.get("model_postprocess"),
        "version": payload.get("version"),
        "tags": payload.get("tags") or [],
        "enabled": bool(payload.get("enabled", True)),
    })


def artifact_sha256(path: str | Path) -> str:
    """Hash a file or a directory deterministically."""
    root = Path(path)
    if root.is_file():
        return _sha256_file(root)
    if not root.is_dir():
        raise TemplateTransferError("model_artifact_missing", f"模型文件不存在: {root}")
    digest = hashlib.sha256()
    for child in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = child.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with child.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    return digest.hexdigest()


def _ensure_portable_id(resource: Any) -> str:
    current = str(getattr(resource, "portable_id", "") or "").strip()
    if current:
        return current
    current = str(uuid.uuid4())
    resource.portable_id = current
    resource.save(only=[type(resource).portable_id])
    return current


def transfer_profile() -> dict:
    capabilities = detect_inference_capabilities()
    return {
        "device_model_code": DEVICE_MODEL_CODE,
        "app_version": get_app_version(),
        "platform": capabilities.get("platform") or "unknown",
        "system": capabilities.get("system") or "unknown",
        "machine": capabilities.get("machine") or "unknown",
        "device_model": capabilities.get("device_model") or "",
        "device_compatible": capabilities.get("device_compatible") or "",
    }


def _require_device_model_code() -> str:
    code = str(DEVICE_MODEL_CODE or "").strip()
    if not code:
        raise TemplateTransferError(
            "device_model_code_missing",
            "当前设备未配置 DEVICE_MODEL_CODE，不能导出或导入编排模板",
        )
    return code


def _node_values(node: dict, keys: Iterable[str]):
    nested = node.get("data") if isinstance(node.get("data"), dict) else {}
    for key in keys:
        yield node, key, node.get(key)
        yield nested, key, nested.get(key)


def _referenced_algorithms(graph: dict) -> list[Algorithm]:
    ids: set[int] = set()
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or get_node_type(node) != "algorithm":
            continue
        for _container, _key, value in _node_values(
            node, ("dataId", "data_id", "algorithmId", "algorithm_id")
        ):
            try:
                ids.add(int(value))
                break
            except (TypeError, ValueError):
                continue
    if not ids:
        return []
    found = {item.id: item for item in Algorithm.select().where(Algorithm.id.in_(ids))}
    missing = sorted(ids - set(found))
    if missing:
        raise TemplateTransferError(
            "algorithm_dependency_missing",
            "模板引用了不存在的算法",
            {"algorithm_ids": missing},
        )
    return [found[item_id] for item_id in sorted(ids)]


def _referenced_external_apis(graph: dict) -> list[ExternalApi]:
    ids: set[int] = set()
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or get_node_type(node) not in {"externalapi", "external_api"}:
            continue
        for _container, _key, value in _node_values(
            node, ("dataId", "data_id", "externalApiId", "external_api_id")
        ):
            try:
                ids.add(int(value))
                break
            except (TypeError, ValueError):
                continue
    if not ids:
        return []
    found = {item.id: item for item in ExternalApi.select().where(ExternalApi.id.in_(ids))}
    missing = sorted(ids - set(found))
    if missing:
        raise TemplateTransferError(
            "external_api_dependency_missing",
            "模板引用了不存在的外部 API",
            {"external_api_ids": missing},
        )
    return [found[item_id] for item_id in sorted(ids)]


def _collect_model_ids(value: Any, *, parent_key: str = "") -> tuple[set[int], set[str]]:
    ids: set[int] = set()
    names: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized == "model_ids":
                raw_ids = item
                if isinstance(raw_ids, str):
                    try:
                        raw_ids = json.loads(raw_ids)
                    except json.JSONDecodeError:
                        raw_ids = []
                for model_id in raw_ids if isinstance(raw_ids, list) else []:
                    try:
                        ids.add(int(model_id))
                    except (TypeError, ValueError):
                        pass
                continue
            if normalized == "models":
                model_ids, model_names = _collect_models_container(item)
                ids.update(model_ids)
                names.update(model_names)
                continue
            if normalized == "model_id" or normalized.endswith("_model_id"):
                try:
                    ids.add(int(item))
                except (TypeError, ValueError):
                    pass
            if parent_key == "models" and normalized == "name" and item:
                names.add(str(item))
            child_ids, child_names = _collect_model_ids(item, parent_key=normalized)
            ids.update(child_ids)
            names.update(child_names)
    elif isinstance(value, list):
        for item in value:
            if parent_key == "models" and isinstance(item, str):
                names.add(item)
            child_ids, child_names = _collect_model_ids(item, parent_key=parent_key)
            ids.update(child_ids)
            names.update(child_names)
    return ids, names


def _collect_models_container(value: Any) -> tuple[set[int], set[str]]:
    ids: set[int] = set()
    names: set[str] = set()
    if isinstance(value, str):
        names.add(value)
    elif isinstance(value, list):
        for item in value:
            child_ids, child_names = _collect_models_container(item)
            ids.update(child_ids)
            names.update(child_names)
    elif isinstance(value, dict):
        if value.get("name"):
            names.add(str(value["name"]))
        try:
            ids.add(int(value.get("model_id")))
        except (TypeError, ValueError):
            pass
        for key, item in value.items():
            if key not in {"name", "model_id"}:
                child_ids, child_names = _collect_models_container(item)
                ids.update(child_ids)
                names.update(child_names)
    return ids, names


def _algorithm_node_configs(graph: dict | None) -> Iterable[dict]:
    if not isinstance(graph, dict):
        return
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or get_node_type(node) != "algorithm":
            continue
        for container in (
            node,
            node.get("data") if isinstance(node.get("data"), dict) else {},
        ):
            config = container.get("config")
            if isinstance(config, dict):
                yield config


def _referenced_models(
    algorithms: list[Algorithm],
    graph: dict | None = None,
) -> list[MLModel]:
    ids: set[int] = set()
    names: set[str] = set()
    for algorithm in algorithms:
        for config in (algorithm.config_dict, algorithm.ext_config):
            config_ids, config_names = _collect_model_ids(config)
            ids.update(config_ids)
            names.update(config_names)
    for config in _algorithm_node_configs(graph):
        config_ids, config_names = _collect_model_ids(config)
        ids.update(config_ids)
        names.update(config_names)
    query = MLModel.select()
    selected = {
        model.id: model
        for model in query
        if model.id in ids or model.name in names
    }
    missing_ids = sorted(ids - set(selected))
    found_names = {model.name for model in selected.values()}
    missing_names = sorted(names - found_names)
    if missing_ids or missing_names:
        raise TemplateTransferError(
            "model_dependency_missing",
            "算法引用了不存在的模型",
            {"model_ids": missing_ids, "model_names": missing_names},
        )
    return sorted(selected.values(), key=lambda item: item.id)


def _portable_model_refs(
    value: Any,
    models_by_id: dict[int, MLModel],
    models_by_name: dict[str, MLModel],
    *,
    parent_key: str = "",
) -> Any:
    if isinstance(value, dict):
        result = {}
        if parent_key == "models" and value.get("name") in models_by_name:
            result["$model"] = _ensure_portable_id(models_by_name[value["name"]])
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized == "model_ids":
                raw_ids = item
                if isinstance(raw_ids, str):
                    try:
                        raw_ids = json.loads(raw_ids)
                    except json.JSONDecodeError:
                        raw_ids = []
                portable_ids = []
                for model_id in raw_ids if isinstance(raw_ids, list) else []:
                    try:
                        model = models_by_id[int(model_id)]
                    except (KeyError, TypeError, ValueError):
                        portable_ids.append(model_id)
                    else:
                        portable_ids.append({"$model": _ensure_portable_id(model)})
                result[key] = portable_ids
            elif normalized == "models":
                result[key] = _portable_models_container(item, models_by_id, models_by_name)
            elif normalized == "gallery_id":
                # Face galleries contain biometric identities and are not part
                # of workflow transfer packages.  Never preserve a destination-
                # local numeric ID, which could silently select another gallery.
                raise TemplateTransferError(
                    "face_gallery_transfer_unsupported",
                    "引用人脸库的算法不能直接迁移；请在目标设备重新创建并绑定人脸识别算法",
                )
            elif normalized == "reid_model_bundle_id":
                # ReID bundles are not packaged as generic MLModel artifacts.
                # Refuse the transfer instead of preserving a database-local
                # integer that can resolve to an unrelated bundle remotely.
                raise TemplateTransferError(
                    "reid_bundle_transfer_unsupported",
                    "引用 ReID 模型包的算法暂不能直接迁移；请在目标设备重新创建并绑定 ReID 模型包",
                )
            elif normalized == "model_id" or normalized.endswith("_model_id"):
                try:
                    model = models_by_id[int(item)]
                except (KeyError, TypeError, ValueError):
                    result[key] = _portable_model_refs(
                        item, models_by_id, models_by_name, parent_key=normalized
                    )
                else:
                    result[key] = {"$model": _ensure_portable_id(model)}
            else:
                result[key] = _portable_model_refs(
                    item, models_by_id, models_by_name, parent_key=normalized
                )
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            if parent_key == "models" and isinstance(item, str) and item in models_by_name:
                result.append({"$model": _ensure_portable_id(models_by_name[item]), "reference": "name"})
            else:
                result.append(
                    _portable_model_refs(item, models_by_id, models_by_name, parent_key=parent_key)
                )
        return result
    return value


def _portable_models_container(
    value: Any,
    models_by_id: dict[int, MLModel],
    models_by_name: dict[str, MLModel],
) -> Any:
    if isinstance(value, str):
        model = models_by_name.get(value)
        return {"$model": _ensure_portable_id(model), "reference": "name"} if model else value
    if isinstance(value, list):
        return [_portable_models_container(item, models_by_id, models_by_name) for item in value]
    if isinstance(value, dict):
        result = {}
        model = None
        try:
            model = models_by_id.get(int(value.get("model_id")))
        except (TypeError, ValueError):
            pass
        if model is None and value.get("name"):
            model = models_by_name.get(str(value["name"]))
        if model:
            result["$model"] = _ensure_portable_id(model)
        for key, item in value.items():
            normalized = str(key).lower()
            if model and normalized == "model_id":
                result[key] = {"$model": _ensure_portable_id(model)}
            elif model and normalized == "name":
                result[key] = {
                    "$model": _ensure_portable_id(model),
                    "reference": "name",
                }
            else:
                result[key] = _portable_models_container(item, models_by_id, models_by_name)
        return result
    return value


def _restore_model_refs(value: Any, model_map: dict[str, MLModel]) -> Any:
    if isinstance(value, dict):
        if any(str(key).lower() == "reid_model_bundle_id" for key in value):
            # Also reject packages produced before this guard was introduced;
            # they may contain an unsafe destination-local numeric bundle ID.
            raise TemplateTransferError(
                "reid_bundle_transfer_unsupported",
                "导入包包含不可移植的 ReID 模型包引用；请在目标设备重新创建并绑定 ReID 模型包",
            )
        portable_id = value.get("$model")
        if portable_id and portable_id in model_map:
            model = model_map[portable_id]
            if set(value).issubset({"$model", "reference"}):
                return model.name if value.get("reference") == "name" else model.id
            restored = {
                key: _restore_model_refs(item, model_map)
                for key, item in value.items()
                if key != "$model"
            }
            if "name" in restored:
                restored["name"] = model.name
            if "model_id" in restored:
                restored["model_id"] = model.id
            return restored
        return {key: _restore_model_refs(item, model_map) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_model_refs(item, model_map) for item in value]
    return value


def _portable_graph(
    graph: dict,
    algorithms: dict[int, Algorithm],
    external_apis: dict[int, ExternalApi],
    models_by_id: dict[int, MLModel],
    models_by_name: dict[str, MLModel],
) -> dict:
    portable = mask_workflow_http_request_secrets(
        mask_workflow_webhook_secrets(deepcopy(graph))
    )
    for node in portable.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_type = get_node_type(node)
        if node_type == "algorithm":
            for container in (
                node,
                node.get("data") if isinstance(node.get("data"), dict) else {},
            ):
                if isinstance(container.get("config"), dict):
                    container["config"] = _portable_model_refs(
                        container["config"], models_by_id, models_by_name
                    )
            keys = ("dataId", "data_id", "algorithmId", "algorithm_id")
            resources = algorithms
            token_name = "$algorithm"
        elif node_type in {"externalapi", "external_api"}:
            keys = ("dataId", "data_id", "externalApiId", "external_api_id")
            resources = external_apis
            token_name = "$external_api"
        else:
            continue
        for container, key, value in _node_values(node, keys):
            try:
                resource = resources[int(value)]
            except (KeyError, TypeError, ValueError):
                continue
            container[key] = {token_name: _ensure_portable_id(resource)}
    return portable


def _restore_graph(
    graph: dict,
    algorithm_map: dict[str, Algorithm],
    external_api_map: dict[str, ExternalApi],
    model_map: dict[str, MLModel],
) -> dict:
    restored = deepcopy(graph)
    for node in restored.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if get_node_type(node) == "algorithm":
            for container in (
                node,
                node.get("data") if isinstance(node.get("data"), dict) else {},
            ):
                if isinstance(container.get("config"), dict):
                    container["config"] = _restore_model_refs(
                        container["config"], model_map
                    )
        for container, key, value in _node_values(
            node,
            (
                "dataId", "data_id", "algorithmId", "algorithm_id",
                "externalApiId", "external_api_id",
            ),
        ):
            if not isinstance(value, dict):
                continue
            if value.get("$algorithm") in algorithm_map:
                container[key] = algorithm_map[value["$algorithm"]].id
            elif value.get("$external_api") in external_api_map:
                container[key] = external_api_map[value["$external_api"]].id
    return restored


def _script_dependencies(script_path: str) -> list[tuple[str, str]]:
    if not script_path:
        return []
    loader = get_script_loader()
    pending = [script_path]
    collected: dict[str, str] = {}
    while pending:
        relative = pending.pop()
        if relative in collected:
            continue
        absolute = loader.resolve_path(relative)
        if not os.path.isfile(absolute):
            raise TemplateTransferError("script_missing", f"算法脚本不存在: {relative}")
        collected[relative] = absolute
        if len(collected) > MAX_SCRIPT_DEPENDENCIES:
            raise TemplateTransferError("script_dependency_limit", "脚本依赖文件数量超过限制")
        try:
            tree = ast.parse(Path(absolute).read_text(encoding="utf-8"), filename=absolute)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            raise TemplateTransferError("script_invalid", f"无法解析脚本 {relative}: {exc}") from exc
        base = PurePosixPath(relative).parent
        candidates: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules = []
                if node.module:
                    modules.append(node.module.replace(".", "/"))
                else:
                    modules.extend(
                        alias.name.replace(".", "/")
                        for alias in node.names
                        if alias.name != "*"
                    )
                if node.level:
                    parent = base
                    for _ in range(max(0, node.level - 1)):
                        parent = parent.parent
                else:
                    parent = PurePosixPath()
                for module in modules:
                    candidate = (parent / module).as_posix()
                    if candidate:
                        candidates.update({f"{candidate}.py", f"{candidate}/__init__.py"})
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    candidate = alias.name.replace(".", "/")
                    candidates.update({f"{candidate}.py", f"{candidate}/__init__.py"})
        for candidate in candidates:
            if candidate in collected:
                continue
            resolved = loader.resolve_path(candidate)
            if os.path.isfile(resolved):
                pending.append(candidate)
    return sorted(collected.items())


def _required_workflow_inputs(graph: dict) -> list[dict]:
    inputs: list[dict] = []
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or get_node_type(node) != "webhook":
            continue
        node_id = str(node.get("id") or "webhook")
        label = str(node.get("name") or "Webhook")
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        if config.get("endpoint_url_configured"):
            inputs.append({
                "key": f"workflow.{node_id}.endpoint_url",
                "label": f"{label} 请求地址",
                "secret": True,
            })
        options = config.get("provider_options") if isinstance(config.get("provider_options"), dict) else {}
        for field in ("signing_secret", "device_key"):
            if options.get(f"{field}_configured"):
                inputs.append({
                    "key": f"workflow.{node_id}.provider_options.{field}",
                    "label": f"{label} {field}",
                    "secret": True,
                })
        headers = config.get("headers") if isinstance(config.get("headers"), list) else []
        for entry in headers:
            if isinstance(entry, dict) and entry.get("value_configured"):
                name = str(entry.get("name") or "header")
                inputs.append({
                    "key": f"workflow.{node_id}.header.{name.lower()}",
                    "label": f"{label} Header：{name}",
                    "secret": True,
                })
    return inputs


def _redact_external_api(item: ExternalApi) -> tuple[dict, list[dict]]:
    headers = deepcopy(item.headers)
    required: list[dict] = []
    portable_id = _ensure_portable_id(item)
    endpoint_url = item.endpoint_url
    if _url_contains_credentials(endpoint_url):
        endpoint_url = ""
        required.append({
            "key": f"external_api.{portable_id}.endpoint_url",
            "label": f"外部 API「{item.name}」请求地址（含凭据）",
            "secret": True,
        })
    for name in list(headers):
        if _is_sensitive_header_name(name) and headers[name]:
            headers[name] = ""
            required.append({
                "key": f"external_api.{portable_id}.header.{str(name).lower()}",
                "label": f"外部 API「{item.name}」Header：{name}",
                "secret": True,
            })
    payload = {
        "portable_id": portable_id,
        "name": item.name,
        "description": item.description,
        "endpoint_url": endpoint_url,
        "method": item.method,
        "headers": headers,
        "request_template": item.request_template,
        "input_schema": item.input_schema,
        "output_schema": item.output_schema,
        "output_mapping": item.output_mapping,
        "timeout_seconds": item.timeout_seconds,
        "enabled": item.enabled,
    }
    fingerprint_value = deepcopy(payload)
    fingerprint_value.pop("name", None)
    payload["fingerprint"] = _sha256_bytes(_canonical_json(fingerprint_value))
    return payload, required


def _algorithm_payload(
    algorithm: Algorithm,
    models_by_id: dict[int, MLModel],
    models_by_name: dict[str, MLModel],
    script_entries: dict[str, tuple[str, str]],
    required_inputs: list[dict],
) -> dict:
    portable_id = _ensure_portable_id(algorithm)
    script_paths = []
    for relative, absolute in _script_dependencies(algorithm.script_path):
        archive_path = f"scripts/{portable_id}/{relative}"
        script_entries.setdefault(archive_path, (relative, absolute))
        script_paths.append({
            "path": relative,
            "archive_path": archive_path,
            "sha256": _sha256_file(absolute),
        })

    ext_config = deepcopy(algorithm.ext_config)
    algorithm_type = ext_config.get("algorithm_type") or "script"
    ocr_backend = None
    if algorithm_type == "ocr":
        ocr_config = (
            ext_config.get("ocr_config")
            if isinstance(ext_config.get("ocr_config"), dict)
            else {}
        )
        try:
            detection_model = models_by_id[int(ocr_config.get("detection_model_id"))]
        except (KeyError, TypeError, ValueError):
            detection_model = None
        if detection_model is not None:
            ocr_backend = ocr_backend_family(
                detection_model.file_path,
                detection_model.framework,
            )
    if algorithm_type == "vl":
        vl_config = ext_config.get("vl_config") if isinstance(ext_config.get("vl_config"), dict) else {}
        if vl_config.get("api_key"):
            vl_config["api_key"] = ""
            required_inputs.append({
                "key": f"algorithm.{portable_id}.vl_api_key",
                "label": f"视觉语言算法「{algorithm.name}」API Key",
                "secret": True,
            })

    script_config = _portable_model_refs(
        algorithm.config_dict, models_by_id, models_by_name
    )
    ext_config = _portable_model_refs(ext_config, models_by_id, models_by_name)

    hooks = []
    for relation in algorithm.algorithm_hooks:
        hook = relation.hook
        hook_portable_id = _ensure_portable_id(hook)
        hook_scripts = []
        for relative, absolute in _script_dependencies(hook.script_path):
            archive_path = f"hooks/{hook_portable_id}/{relative}"
            script_entries.setdefault(archive_path, (relative, absolute))
            hook_scripts.append({
                "path": relative,
                "archive_path": archive_path,
                "sha256": _sha256_file(absolute),
            })
        hook_payload = {
            "portable_id": hook_portable_id,
            "name": hook.name,
            "hook_point": hook.hook_point,
            "script_path": hook.script_path,
            "scripts": hook_scripts,
            "entry_function": hook.entry_function,
            "priority": hook.priority,
            "condition": hook.condition,
            "enabled": hook.enabled,
            "relation_enabled": relation.enabled,
            "hook_config": relation.config,
        }
        hook_payload["fingerprint"] = _hook_fingerprint(hook_payload)
        hooks.append(hook_payload)

    payload = {
        "portable_id": portable_id,
        "name": algorithm.name,
        "description": algorithm.description,
        "algorithm_type": algorithm_type,
        "script_path": algorithm.script_path,
        "scripts": script_paths,
        "script_config": script_config,
        "ext_config": ext_config,
        "enabled_hooks": algorithm.enabled_hooks,
        "hooks": hooks,
    }
    if ocr_backend:
        payload["ocr_backend"] = ocr_backend
    fingerprint_value = deepcopy(payload)
    fingerprint_value.pop("name", None)
    fingerprint_value.pop("script_path", None)
    fingerprint_value.pop("enabled_hooks", None)
    fingerprint_value["scripts"] = sorted(
        script.get("sha256") for script in payload["scripts"] if script.get("sha256")
    )
    for hook in fingerprint_value.get("hooks", []):
        hook.pop("name", None)
        hook.pop("script_path", None)
        hook["scripts"] = sorted(
            script.get("sha256") for script in hook.get("scripts", []) if script.get("sha256")
        )
    payload["fingerprint"] = _sha256_bytes(_canonical_json(fingerprint_value))
    return payload


def _model_payload(model: MLModel, include_models: bool) -> dict:
    portable_id = _ensure_portable_id(model)
    digest = artifact_sha256(model.file_path)
    if model.artifact_sha256 != digest:
        model.artifact_sha256 = digest
        model.save(only=[MLModel.artifact_sha256])
    artifact = Path(model.file_path)
    archive_root = f"models/{portable_id}"
    payload = {
        "portable_id": portable_id,
        "name": model.name,
        "filename": model.filename,
        "artifact_name": artifact.name,
        "artifact_is_directory": artifact.is_dir(),
        "artifact_root": archive_root if include_models else None,
        "artifact_sha256": digest,
        "file_size": model.file_size,
        "model_type": model.model_type,
        "model_role": model.model_role,
        "framework": model.framework,
        "input_shape": model.input_shape,
        "classes": model.classes_dict,
        "model_postprocess": model.model_postprocess_dict,
        "description": model.description,
        "version": model.version,
        "tags": model.tags_list,
        "enabled": model.enabled,
        "included": include_models,
    }
    payload["metadata_fingerprint"] = _model_metadata_fingerprint(payload)
    return payload


def build_export_package(template: Workflow, *, include_models: bool) -> tuple[str, str]:
    _require_device_model_code()
    if not template.is_template:
        raise TemplateTransferError("not_a_template", "只有编排模板可以导出")
    graph = template.data_dict
    valid, error = validate_template_source_node(graph)
    if not valid:
        raise TemplateTransferError("invalid_template", error or "模板结构无效")

    algorithms = _referenced_algorithms(graph)
    external_apis = _referenced_external_apis(graph)
    models = _referenced_models(algorithms, graph)
    models_by_id = {item.id: item for item in models}
    models_by_name = {item.name: item for item in models}
    script_entries: dict[str, tuple[str, str]] = {}
    required_inputs: list[dict] = []
    algorithm_payloads = [
        _algorithm_payload(
            item, models_by_id, models_by_name, script_entries, required_inputs
        )
        for item in algorithms
    ]
    external_payloads = []
    for item in external_apis:
        payload, inputs = _redact_external_api(item)
        external_payloads.append(payload)
        required_inputs.extend(inputs)

    portable_graph = _portable_graph(
        graph,
        {item.id: item for item in algorithms},
        {item.id: item for item in external_apis},
        models_by_id,
        models_by_name,
    )
    required_inputs.extend(_required_workflow_inputs(portable_graph))
    model_payloads = [_model_payload(item, include_models) for item in models]

    byte_entries: dict[str, bytes] = {
        "workflow.json": _canonical_json(portable_graph),
    }
    file_entries: dict[str, str] = {
        path: absolute for path, (_relative, absolute) in script_entries.items()
    }
    if include_models:
        for model, payload in zip(models, model_payloads):
            artifact = Path(model.file_path)
            root = str(payload["artifact_root"])
            if artifact.is_dir():
                for child in sorted(item for item in artifact.rglob("*") if item.is_file()):
                    file_entries[f"{root}/{child.relative_to(artifact).as_posix()}"] = str(child)
            else:
                file_entries[f"{root}/{artifact.name}"] = str(artifact)

    entries = []
    for path, value in sorted(byte_entries.items()):
        entries.append({"path": path, "size": len(value), "sha256": _sha256_bytes(value)})
    for path, source in sorted(file_entries.items()):
        entries.append({"path": path, "size": os.path.getsize(source), "sha256": _sha256_file(source)})

    manifest = {
        "format": FORMAT_NAME,
        "schema_version": FORMAT_VERSION,
        "created_at": datetime.now().isoformat(),
        "source": transfer_profile(),
        "template": {
            "portable_id": _ensure_portable_id(template),
            "name": template.name,
            "description": template.description,
            "workflow_path": "workflow.json",
            "fingerprint": _template_content_fingerprint(
                portable_graph,
                algorithm_payloads,
                external_payloads,
                model_payloads,
            ),
        },
        "options": {"models_included": include_models},
        "dependencies": {
            "models": model_payloads,
            "algorithms": algorithm_payloads,
            "external_apis": external_payloads,
        },
        "required_inputs": required_inputs,
        "entries": entries,
    }
    manifest_bytes = _canonical_json(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise TemplateTransferError("manifest_too_large", "迁移清单超过大小限制")
    if len(entries) + 1 > MAX_ARCHIVE_ENTRIES:
        raise TemplateTransferError("archive_entry_limit", "迁移包文件数量超过限制")
    if len(manifest_bytes) + sum(int(entry["size"]) for entry in entries) > MAX_UNCOMPRESSED_BYTES:
        raise TemplateTransferError(
            "archive_size_limit",
            "迁移包解压后大小超过 8 GiB 限制",
        )

    os.makedirs(TEMPLATE_TRANSFER_PATH, exist_ok=True)
    handle, package_path = tempfile.mkstemp(
        prefix="workflow-template-", suffix=".vbt.zip", dir=TEMPLATE_TRANSFER_PATH
    )
    os.close(handle)
    try:
        with zipfile.ZipFile(package_path, "w", allowZip64=True) as archive:
            archive.writestr("manifest.json", manifest_bytes, compress_type=zipfile.ZIP_STORED)
            for path, value in sorted(byte_entries.items()):
                archive.writestr(path, value, compress_type=zipfile.ZIP_DEFLATED)
            for path, source in sorted(file_entries.items()):
                archive.write(source, path, compress_type=zipfile.ZIP_DEFLATED)
    except Exception:
        if os.path.exists(package_path):
            os.remove(package_path)
        raise
    filename = f"{secure_filename(template.name) or 'workflow-template'}.vbt.zip"
    return package_path, filename


def _validate_manifest(manifest: Any) -> dict:
    if not isinstance(manifest, dict):
        raise TemplateTransferError("invalid_manifest", "迁移清单必须是 JSON 对象")
    if manifest.get("format") != FORMAT_NAME:
        raise TemplateTransferError("unsupported_format", "不是 Video BA 编排模板迁移包")
    if manifest.get("schema_version") != FORMAT_VERSION:
        raise TemplateTransferError(
            "unsupported_schema_version",
            f"不支持迁移包格式版本: {manifest.get('schema_version')}",
        )
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    source_code = str(source.get("device_model_code") or "").strip()
    target_code = _require_device_model_code()
    if not source_code:
        raise TemplateTransferError("source_device_model_missing", "迁移包没有设备型号代码")
    if source_code != target_code:
        raise TemplateTransferError(
            "device_model_mismatch",
            "导出设备与当前设备型号不一致，禁止导入",
            {"source_device_model_code": source_code, "target_device_model_code": target_code},
        )
    template = manifest.get("template") if isinstance(manifest.get("template"), dict) else {}
    dependencies = (
        manifest.get("dependencies")
        if isinstance(manifest.get("dependencies"), dict)
        else {}
    )

    def require_portable_id(value: Any, label: str) -> str:
        portable_id = str(value or "").strip()
        try:
            parsed = uuid.UUID(portable_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise TemplateTransferError(
                "invalid_manifest", f"{label} 缺少有效的可迁移标识"
            ) from exc
        return str(parsed)

    def require_digest(value: Any, label: str) -> str:
        digest = str(value or "").strip().lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise TemplateTransferError("invalid_manifest", f"{label} 的 SHA256 无效")
        return digest

    require_portable_id(template.get("portable_id"), "编排模板")
    require_digest(template.get("fingerprint"), "编排模板")
    workflow_path = str(template.get("workflow_path") or "")
    if workflow_path != "workflow.json":
        raise TemplateTransferError("invalid_manifest", "编排模板路径必须为 workflow.json")

    seen_resources: set[tuple[str, str]] = set()
    for resource_name in ("models", "algorithms", "external_apis"):
        items = dependencies.get(resource_name, [])
        if not isinstance(items, list):
            raise TemplateTransferError("invalid_manifest", f"{resource_name} 依赖清单无效")
        for item in items:
            if not isinstance(item, dict):
                raise TemplateTransferError("invalid_manifest", f"{resource_name} 依赖项无效")
            portable_id = require_portable_id(item.get("portable_id"), resource_name)
            identity = (resource_name, portable_id)
            if identity in seen_resources:
                raise TemplateTransferError("invalid_manifest", f"{resource_name} 包含重复可迁移标识")
            seen_resources.add(identity)
            if resource_name == "models":
                require_digest(item.get("artifact_sha256"), "模型")
                require_digest(item.get("metadata_fingerprint"), "模型元数据")
            else:
                require_digest(item.get("fingerprint"), resource_name)
            if resource_name == "algorithms":
                hooks = item.get("hooks", [])
                if not isinstance(hooks, list):
                    raise TemplateTransferError("invalid_manifest", "Hook 依赖清单无效")
                for hook in hooks:
                    if not isinstance(hook, dict):
                        raise TemplateTransferError("invalid_manifest", "Hook 依赖项无效")
                    hook_id = require_portable_id(hook.get("portable_id"), "Hook")
                    require_digest(hook.get("fingerprint"), "Hook")
                    seen_resources.add(("hooks", hook_id))

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise TemplateTransferError("invalid_manifest", "迁移包文件清单无效")
    seen_entry_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise TemplateTransferError("invalid_manifest", "迁移包文件项无效")
        path = str(entry.get("path") or "")
        parsed_path = PurePosixPath(path)
        if (
            not path
            or path == "manifest.json"
            or parsed_path.is_absolute()
            or ".." in parsed_path.parts
            or "\\" in path
            or path in seen_entry_paths
        ):
            raise TemplateTransferError("invalid_manifest", f"迁移包文件路径无效: {path}")
        seen_entry_paths.add(path)
        require_digest(entry.get("sha256"), f"文件 {path}")
        try:
            size = int(entry.get("size"))
        except (TypeError, ValueError) as exc:
            raise TemplateTransferError("invalid_manifest", f"文件大小无效: {path}") from exc
        if size < 0:
            raise TemplateTransferError("invalid_manifest", f"文件大小无效: {path}")

    return manifest


def _validate_imported_workflow(graph: dict) -> None:
    """Run the same graph validators used by normal workflow creation."""
    from app.core.detection_filter import validate_workflow_detection_filter_nodes
    from app.core.time_schedule import validate_workflow_time_schedule_nodes
    from app.web.api.workflows import (
        _sanitize_workflow_edge_conditions,
        _validate_count_change_conditions,
        _validate_ocr_crop_nodes,
        _validate_ocr_text_conditions,
    )

    _sanitize_workflow_edge_conditions(graph)
    validators = (
        _validate_ocr_text_conditions,
        _validate_count_change_conditions,
        validate_workflow_webhook_nodes,
        validate_http_value_conditions,
        validate_workflow_detection_filter_nodes,
        validate_workflow_time_schedule_nodes,
    )
    for validator in validators:
        valid, error = validator(graph)
        if not valid:
            raise TemplateTransferError("invalid_template", error or "模板配置无效")
    valid, error, _warnings = _validate_ocr_crop_nodes(graph)
    if not valid:
        raise TemplateTransferError("invalid_template", error or "OCR 裁剪配置无效")


def _resolution_target_id(resolutions: dict, resource: str, portable_id: str) -> int | None:
    resource_resolutions = resolutions.get(resource)
    if not isinstance(resource_resolutions, dict):
        return None
    raw = resource_resolutions.get(portable_id)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, dict) and raw.get("target_id") is not None:
        try:
            return int(raw["target_id"])
        except (TypeError, ValueError):
            return None
    return None


def _resolution_entry(resolutions: dict, resource: str, portable_id: str) -> dict:
    resource_resolutions = resolutions.get(resource)
    if not isinstance(resource_resolutions, dict):
        return {}
    value = resource_resolutions.get(portable_id)
    return value if isinstance(value, dict) else {}


def _ensure_model_digest(model: MLModel) -> str:
    digest = str(model.artifact_sha256 or "")
    if digest:
        return digest
    digest = artifact_sha256(model.file_path)
    model.artifact_sha256 = digest
    model.save(only=[MLModel.artifact_sha256])
    return digest


def _find_model_by_digest(digest: str, metadata_fingerprint: str) -> MLModel | None:
    if not digest:
        return None
    candidates = list(MLModel.select().where(MLModel.artifact_sha256 == digest))
    candidates.extend(MLModel.select().where(MLModel.artifact_sha256.is_null(True)))
    for candidate in candidates:
        if not candidate.enabled:
            continue
        try:
            if (
                _ensure_model_digest(candidate) == digest
                and _model_payload(candidate, False).get("metadata_fingerprint")
                == metadata_fingerprint
            ):
                return candidate
        except TemplateTransferError:
            continue
    return None


def _packaged_ocr_backend(item: dict, dependencies: dict) -> str | None:
    explicit = str(item.get("ocr_backend") or "").strip()
    if explicit:
        return explicit
    ext_config = item.get("ext_config") if isinstance(item.get("ext_config"), dict) else {}
    ocr_config = (
        ext_config.get("ocr_config")
        if isinstance(ext_config.get("ocr_config"), dict)
        else {}
    )
    detection_ref = ocr_config.get("detection_model_id")
    portable_id = (
        str(detection_ref.get("$model") or "")
        if isinstance(detection_ref, dict)
        else ""
    )
    if not portable_id:
        return None
    model_payload = next(
        (
            model
            for model in dependencies.get("models", []) or []
            if str(model.get("portable_id") or "") == portable_id
        ),
        None,
    )
    if not model_payload:
        return None
    return ocr_backend_family(
        model_payload.get("artifact_name") or model_payload.get("filename"),
        model_payload.get("framework"),
    )


def _workflow_fingerprint(workflow: Workflow) -> str:
    graph = workflow.data_dict
    algorithms = _referenced_algorithms(graph)
    external_apis = _referenced_external_apis(graph)
    models = _referenced_models(algorithms, graph)
    models_by_id = {item.id: item for item in models}
    models_by_name = {item.name: item for item in models}
    algorithm_payloads = [
        _algorithm_payload(item, models_by_id, models_by_name, {}, [])
        for item in algorithms
    ]
    external_payloads = [_redact_external_api(item)[0] for item in external_apis]
    model_payloads = [_model_payload(item, False) for item in models]
    portable = _portable_graph(
        graph,
        {item.id: item for item in algorithms},
        {item.id: item for item in external_apis},
        models_by_id,
        models_by_name,
    )
    return _template_content_fingerprint(
        portable,
        algorithm_payloads,
        external_payloads,
        model_payloads,
    )


def preflight_manifest(manifest: dict, resolutions: dict | None = None) -> dict:
    manifest = _validate_manifest(manifest)
    resolutions = resolutions if isinstance(resolutions, dict) else {}
    dependencies = manifest.get("dependencies") if isinstance(manifest.get("dependencies"), dict) else {}
    result = {"models": [], "algorithms": [], "external_apis": [], "hooks": []}
    blockers: list[dict] = []

    for item in dependencies.get("models", []) or []:
        portable_id = str(item.get("portable_id") or "")
        digest = str(item.get("artifact_sha256") or "")
        metadata_fingerprint = str(item.get("metadata_fingerprint") or "")
        resolution = _resolution_entry(resolutions, "models", portable_id)
        renamed_name = str(resolution.get("name") or "").strip()
        renamed_version = str(resolution.get("version") or item.get("version") or "v1.0").strip()
        rename_available = bool(
            resolution.get("action") == "rename"
            and renamed_name
            and not MLModel.get_or_none(
                (MLModel.name == renamed_name) & (MLModel.version == renamed_version)
            )
        )
        portable_target = (
            MLModel.get_or_none(MLModel.portable_id == portable_id)
            if portable_id
            else None
        )
        mapped_id = _resolution_target_id(resolutions, "models", portable_id)
        mapped = MLModel.get_or_none(MLModel.id == mapped_id) if mapped_id else None

        def model_matches(candidate: MLModel | None) -> bool:
            return bool(
                candidate
                and candidate.enabled
                and _ensure_model_digest(candidate) == digest
                and _model_payload(candidate, False).get("metadata_fingerprint")
                == metadata_fingerprint
            )

        if mapped_id:
            if model_matches(mapped):
                target, status = mapped, "mapped"
            else:
                target = None
                status = "conflict" if item.get("included") else "missing"
        elif portable_target and model_matches(portable_target):
            target, status = portable_target, "reuse"
        elif portable_target:
            target = None
            if item.get("included") and rename_available:
                status = "import"
            else:
                status = "conflict"
        else:
            target = _find_model_by_digest(digest, metadata_fingerprint)
            if target:
                status = "reuse_by_hash"
            else:
                if item.get("included"):
                    duplicate = MLModel.get_or_none(
                        (MLModel.name == item.get("name")) & (MLModel.version == item.get("version"))
                    )
                    if resolution.get("action") == "rename":
                        status = "import" if rename_available else "conflict"
                    else:
                        status = "import" if not duplicate else "conflict"
                else:
                    status = "missing"
        row = {
            "portable_id": portable_id,
            "name": item.get("name"),
            "version": item.get("version"),
            "artifact_sha256": digest,
            "metadata_fingerprint": metadata_fingerprint,
            "included": bool(item.get("included")),
            "status": status,
            "target_id": target.id if target else None,
            "target_name": target.name if target else None,
        }
        result["models"].append(row)
        if status in {"missing", "conflict"}:
            blockers.append({"resource": "model", **row})

    for resource_name, model, manifest_key in (
        ("algorithms", Algorithm, "algorithms"),
        ("external_apis", ExternalApi, "external_apis"),
    ):
        for item in dependencies.get(manifest_key, []) or []:
            portable_id = str(item.get("portable_id") or "")
            resolution = _resolution_entry(resolutions, resource_name, portable_id)
            renamed_name = str(resolution.get("name") or "").strip()
            rename_available = bool(
                resolution.get("action") == "rename"
                and renamed_name
                and not model.get_or_none(model.name == renamed_name)
            )
            target = model.get_or_none(model.portable_id == portable_id) if portable_id else None
            if target:
                status = "reuse"
                try:
                    if resource_name == "algorithms":
                        local_models = _referenced_models([target])
                        local_payload = _algorithm_payload(
                            target,
                            {model_item.id: model_item for model_item in local_models},
                            {model_item.name: model_item for model_item in local_models},
                            {},
                            [],
                        )
                    else:
                        local_payload, _required = _redact_external_api(target)
                    if (
                        item.get("fingerprint")
                        and local_payload.get("fingerprint") != item.get("fingerprint")
                    ):
                        status = "conflict"
                except TemplateTransferError:
                    status = "conflict"
                if status == "conflict":
                    if rename_available:
                        target, status = None, "import"
            else:
                mapped_id = _resolution_target_id(resolutions, resource_name, portable_id)
                target = model.get_or_none(model.id == mapped_id) if mapped_id else None
                if target:
                    status = "mapped"
                else:
                    duplicate = model.get_or_none(model.name == item.get("name"))
                    if resolution.get("action") == "rename":
                        status = "import" if rename_available else "conflict"
                    else:
                        status = "import" if not duplicate else "conflict"
            row = {
                "portable_id": portable_id,
                "name": item.get("name"),
                "status": status,
                "target_id": target.id if target else None,
                "target_name": target.name if target else None,
            }
            result[resource_name].append(row)
            if (
                resource_name == "algorithms"
                and str(item.get("algorithm_type") or "script") == "ocr"
                and status == "import"
                and not is_ocr_runtime_available(
                    required_backend=_packaged_ocr_backend(item, dependencies)
                )
            ):
                row["status"] = "unsupported"
                status = "unsupported"
                blockers.append({"resource": "algorithm", **row})
            if status == "conflict":
                blockers.append({"resource": resource_name.rstrip("s"), **row})

    seen_hooks: set[str] = set()
    for algorithm_item in dependencies.get("algorithms", []) or []:
        for item in algorithm_item.get("hooks", []) or []:
            portable_id = str(item.get("portable_id") or "")
            if not portable_id or portable_id in seen_hooks:
                continue
            seen_hooks.add(portable_id)
            resolution = _resolution_entry(resolutions, "hooks", portable_id)
            renamed_name = str(resolution.get("name") or "").strip()
            rename_available = bool(
                resolution.get("action") == "rename"
                and renamed_name
                and not Hook.get_or_none(Hook.name == renamed_name)
            )
            target = Hook.get_or_none(Hook.portable_id == portable_id)
            mapped_id = _resolution_target_id(resolutions, "hooks", portable_id)
            if not target and mapped_id:
                target = Hook.get_or_none(Hook.id == mapped_id)
            if target:
                status = "mapped" if mapped_id else "reuse"
                if not mapped_id:
                    try:
                        local_payload = {
                            "hook_point": target.hook_point,
                            "scripts": [
                                {"sha256": _sha256_file(absolute)}
                                for _relative, absolute in _script_dependencies(target.script_path)
                            ],
                            "entry_function": target.entry_function,
                            "priority": target.priority,
                            "condition": target.condition,
                            "enabled": target.enabled,
                        }
                        if (
                            item.get("fingerprint")
                            and _hook_fingerprint(local_payload) != item.get("fingerprint")
                        ):
                            status = "conflict"
                    except TemplateTransferError:
                        status = "conflict"
                    if status == "conflict" and rename_available:
                        target, status = None, "import"
            else:
                duplicate = Hook.get_or_none(Hook.name == item.get("name"))
                if resolution.get("action") == "rename":
                    status = "import" if rename_available else "conflict"
                else:
                    status = "import" if not duplicate else "conflict"
            row = {
                "portable_id": portable_id,
                "name": item.get("name"),
                "status": status,
                "target_id": target.id if target else None,
                "target_name": target.name if target else None,
            }
            result["hooks"].append(row)
            if status == "conflict":
                blockers.append({"resource": "hook", **row})

    template_info = manifest.get("template") or {}
    existing_template = Workflow.get_or_none(
        Workflow.portable_id == str(template_info.get("portable_id") or "")
    )
    template_resolution = (
        resolutions.get("template")
        if isinstance(resolutions.get("template"), dict)
        else {}
    )
    renamed_template_name = str(template_resolution.get("name") or "").strip()
    template_rename_available = bool(
        template_resolution.get("action") == "rename"
        and renamed_template_name
        and not Workflow.get_or_none(
            (Workflow.name == renamed_template_name) & Workflow.is_template
        )
    )
    template_status = "import"
    if existing_template:
        source_fingerprint = str(template_info.get("fingerprint") or "")
        try:
            same_content = bool(
                source_fingerprint
                and _workflow_fingerprint(existing_template) == source_fingerprint
            )
        except TemplateTransferError:
            same_content = False
        if same_content:
            template_status = "already_imported"
        elif template_rename_available:
            template_status = "import"
        else:
            template_status = "conflict"
            blockers.append({
                "resource": "template",
                "portable_id": template_info.get("portable_id"),
                "name": template_info.get("name"),
            })
    else:
        same_name = Workflow.get_or_none(
            (Workflow.name == template_info.get("name")) & Workflow.is_template
        )
        if same_name:
            if template_rename_available:
                template_status = "import"
            else:
                template_status = "conflict"
                blockers.append({"resource": "template", "name": template_info.get("name")})

    dependency_statuses = {
        (resource, str(row.get("portable_id") or "")): row.get("status")
        for resource, rows in (
            ("algorithm", result["algorithms"]),
            ("external_api", result["external_apis"]),
        )
        for row in rows
    }
    required_inputs = []
    for item in manifest.get("required_inputs") or []:
        key = str(item.get("key") or "")
        parts = key.split(".", 2)
        if len(parts) >= 2 and parts[0] in {"algorithm", "external_api"}:
            if dependency_statuses.get((parts[0], parts[1])) in {"reuse", "mapped"}:
                continue
        required_inputs.append(item)
    supplied_secrets = resolutions.get("secrets") if isinstance(resolutions.get("secrets"), dict) else {}
    missing_inputs = [item for item in required_inputs if not str(supplied_secrets.get(item.get("key")) or "").strip()]
    return {
        "compatible": True,
        "ready": not blockers and not missing_inputs,
        "source": manifest.get("source"),
        "target": transfer_profile(),
        "template": {
            "portable_id": template_info.get("portable_id"),
            "name": template_info.get("name"),
            "status": template_status,
            "existing_id": existing_template.id if existing_template else None,
        },
        "dependencies": result,
        "required_inputs": required_inputs,
        "missing_inputs": missing_inputs,
        "blockers": blockers,
    }


def read_package_manifest(package_path: str) -> tuple[zipfile.ZipFile, dict]:
    try:
        archive = zipfile.ZipFile(package_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise TemplateTransferError("invalid_archive", "迁移包不是有效的 ZIP 文件") from exc
    infos = archive.infolist()
    if not infos or infos[0].filename != "manifest.json":
        archive.close()
        raise TemplateTransferError("invalid_archive", "迁移包第一项必须是 manifest.json")
    if infos[0].compress_type != zipfile.ZIP_STORED:
        archive.close()
        raise TemplateTransferError("invalid_archive", "manifest.json 必须使用未压缩格式")
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        archive.close()
        raise TemplateTransferError("archive_entry_limit", "迁移包文件数量超过限制")
    filenames = [info.filename for info in infos]
    if len(filenames) != len(set(filenames)):
        archive.close()
        raise TemplateTransferError("duplicate_archive_entry", "迁移包包含重复文件名")
    total_size = 0
    for info in infos:
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
            archive.close()
            raise TemplateTransferError("unsafe_archive_path", f"迁移包包含不安全路径: {info.filename}")
        total_size += info.file_size
        if total_size > MAX_UNCOMPRESSED_BYTES:
            archive.close()
            raise TemplateTransferError("archive_size_limit", "迁移包解压后大小超过限制")
        if info.compress_size and info.file_size / info.compress_size > 500:
            archive.close()
            raise TemplateTransferError("archive_ratio_limit", "迁移包包含异常压缩率文件")
    manifest_info = infos[0]
    if manifest_info.file_size > MAX_MANIFEST_BYTES:
        archive.close()
        raise TemplateTransferError("manifest_too_large", "迁移清单超过大小限制")
    try:
        manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        archive.close()
        raise TemplateTransferError("invalid_manifest", "迁移清单 JSON 无效") from exc
    return archive, _validate_manifest(manifest)


def _verify_entries(archive: zipfile.ZipFile, manifest: dict) -> None:
    declared = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
    actual = {info.filename for info in archive.infolist() if not info.is_dir()}
    expected = {"manifest.json"}
    for entry in declared:
        path = str(entry.get("path") or "")
        if not path or path not in actual:
            raise TemplateTransferError("package_entry_missing", f"迁移包缺少文件: {path}")
        expected.add(path)
        digest = hashlib.sha256()
        size = 0
        with archive.open(path) as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        if size != int(entry.get("size", -1)) or digest.hexdigest() != entry.get("sha256"):
            raise TemplateTransferError("package_checksum_mismatch", f"迁移包文件校验失败: {path}")
    undeclared = actual - expected
    if undeclared:
        raise TemplateTransferError(
            "undeclared_package_entry",
            "迁移包包含未声明文件",
            {"entries": sorted(undeclared)[:20]},
        )
    workflow_path = str((manifest.get("template") or {}).get("workflow_path") or "")
    try:
        graph = json.loads(archive.read(workflow_path).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemplateTransferError("invalid_template", "迁移包的编排模板 JSON 无效") from exc
    dependencies = manifest.get("dependencies") or {}
    fingerprint = _template_content_fingerprint(
        graph,
        dependencies.get("algorithms", []) or [],
        dependencies.get("external_apis", []) or [],
        dependencies.get("models", []) or [],
    )
    if fingerprint != (manifest.get("template") or {}).get("fingerprint"):
        raise TemplateTransferError(
            "package_fingerprint_mismatch",
            "迁移包模板与依赖指纹校验失败",
        )


def _apply_secret_inputs(manifest: dict, graph: dict, secrets: dict) -> None:
    algorithms = manifest.get("dependencies", {}).get("algorithms", []) or []
    for item in algorithms:
        key = f"algorithm.{item.get('portable_id')}.vl_api_key"
        if key in secrets:
            item.setdefault("ext_config", {}).setdefault("vl_config", {})["api_key"] = secrets[key]
    external_apis = manifest.get("dependencies", {}).get("external_apis", []) or []
    for item in external_apis:
        endpoint_key = f"external_api.{item.get('portable_id')}.endpoint_url"
        if endpoint_key in secrets:
            item["endpoint_url"] = secrets[endpoint_key]
        prefix = f"external_api.{item.get('portable_id')}.header."
        headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
        for name in list(headers):
            if f"{prefix}{str(name).lower()}" in secrets:
                headers[name] = secrets[f"{prefix}{str(name).lower()}"]
    nodes = {str(node.get("id")): node for node in graph.get("nodes", []) if isinstance(node, dict)}
    for node_id, node in nodes.items():
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        prefix = f"workflow.{node_id}."
        if f"{prefix}endpoint_url" in secrets:
            config["endpoint_url"] = secrets[f"{prefix}endpoint_url"]
        options = config.get("provider_options") if isinstance(config.get("provider_options"), dict) else {}
        for field in ("signing_secret", "device_key"):
            if f"{prefix}provider_options.{field}" in secrets:
                options[field] = secrets[f"{prefix}provider_options.{field}"]
        headers = config.get("headers") if isinstance(config.get("headers"), list) else []
        for entry in headers:
            name = str(entry.get("name") or "").lower() if isinstance(entry, dict) else ""
            if name and f"{prefix}header.{name}" in secrets:
                entry["value"] = secrets[f"{prefix}header.{name}"]


def _extract_scripts(archive: zipfile.ZipFile, payload: dict, prefix: str) -> str:
    scripts = payload.get("scripts") or []
    if not scripts:
        return ""
    loader = get_script_loader()
    main = str(payload.get("script_path") or "")
    declared_paths = {str(entry.get("path") or "") for entry in scripts}
    if not main:
        raise TemplateTransferError("script_missing", "迁移包脚本清单缺少主脚本路径")
    if main and main not in declared_paths:
        raise TemplateTransferError("script_missing", f"迁移包缺少主脚本: {main}")
    root = Path(loader.resolve_path(f"imports/{prefix}/placeholder.py", writable=True)).parent
    if root.exists():
        raise TemplateTransferError(
            "script_target_exists",
            f"目标脚本目录已存在，请清理失败的历史导入后重试: {root}",
        )
    root.mkdir(parents=True, exist_ok=False)
    try:
        for entry in scripts:
            source_path = str(entry.get("archive_path") or "")
            relative = str(entry.get("path") or "")
            relative_path = PurePosixPath(relative)
            if (
                not relative
                or relative_path.is_absolute()
                or ".." in relative_path.parts
                or "\\" in relative
            ):
                raise TemplateTransferError("unsafe_script_path", f"脚本路径不安全: {relative}")
            target_relative = f"imports/{prefix}/{relative}"
            target = Path(loader.resolve_path(target_relative, writable=True))
            if root not in target.parents:
                raise TemplateTransferError("unsafe_script_path", f"脚本路径越界: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(source_path) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            if _sha256_file(target) != entry.get("sha256"):
                raise TemplateTransferError("script_checksum_mismatch", f"脚本校验失败: {relative}")
            loader.validate_syntax(str(target))
            loader.validate_security(str(target))
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return f"imports/{prefix}/{main}" if main else ""


def _create_model_from_package(
    archive: zipfile.ZipFile,
    item: dict,
    *,
    username: str,
    resolution: dict,
) -> MLModel:
    source_portable_id = str(item["portable_id"])
    portable_id = (
        str(uuid.uuid4())
        if resolution.get("action") == "rename"
        and MLModel.get_or_none(MLModel.portable_id == source_portable_id)
        else source_portable_id
    )
    root = str(item.get("artifact_root") or "")
    if not root:
        raise TemplateTransferError("model_artifact_missing", f"模型未携带: {item.get('name')}")
    name = str(resolution.get("name") or item.get("name") or "导入模型").strip()
    version = str(resolution.get("version") or item.get("version") or "v1.0").strip()
    duplicate = MLModel.get_or_none((MLModel.name == name) & (MLModel.version == version))
    if duplicate:
        raise TemplateTransferError("model_name_conflict", f"模型名称和版本已存在: {name} {version}")
    model_type = str(item.get("model_type") or "model")
    type_dir = Path(MODEL_SAVE_PATH) / secure_filename(model_type.lower())
    type_dir.mkdir(parents=True, exist_ok=True)
    artifact_name = secure_filename(str(item.get("artifact_name") or item.get("filename") or "model")) or "model"
    target = type_dir / f"{portable_id}-{artifact_name}"
    if target.exists():
        raise TemplateTransferError(
            "model_artifact_target_exists",
            f"目标模型路径已存在，请清理失败的历史导入后重试: {target}",
        )
    prefix = f"{root.rstrip('/')}/"
    members = [info for info in archive.infolist() if info.filename.startswith(prefix) and not info.is_dir()]
    if not members:
        raise TemplateTransferError("model_artifact_missing", f"模型文件未包含在迁移包中: {name}")
    is_directory = bool(item.get("artifact_is_directory"))
    try:
        if is_directory:
            target.mkdir(parents=True, exist_ok=False)
            for info in members:
                relative = PurePosixPath(info.filename[len(prefix):])
                destination = target.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
        else:
            if len(members) != 1:
                raise TemplateTransferError("model_artifact_invalid", f"模型文件数量无效: {name}")
            with archive.open(members[0]) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
        digest = artifact_sha256(target)
        if digest != item.get("artifact_sha256"):
            raise TemplateTransferError("model_checksum_mismatch", f"模型内容校验失败: {name}")
        file_size = (
            sum(child.stat().st_size for child in target.rglob("*") if child.is_file())
            if target.is_dir()
            else target.stat().st_size
        )
        return MLModel.create(
            portable_id=portable_id,
            artifact_sha256=digest,
            name=name,
            filename=str(item.get("filename") or artifact_name),
            file_path=str(target),
            file_size=file_size,
            model_type=model_type,
            model_role=item.get("model_role") or None,
            framework=str(item.get("framework") or "unknown"),
            input_shape=item.get("input_shape") or None,
            classes=json.dumps(item.get("classes") or {}, ensure_ascii=False),
            model_postprocess=json.dumps(item.get("model_postprocess"), ensure_ascii=False) if item.get("model_postprocess") is not None else None,
            description=item.get("description") or None,
            version=version,
            tags=json.dumps(item.get("tags") or [], ensure_ascii=False),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            uploaded_by=username,
            enabled=bool(item.get("enabled", True)),
        )
    except Exception:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
        raise


def import_package(package_path: str, resolutions: dict | None, *, username: str) -> dict:
    resolutions = resolutions if isinstance(resolutions, dict) else {}
    archive, manifest = read_package_manifest(package_path)
    created_paths: list[str] = []
    try:
        _verify_entries(archive, manifest)
        preflight = preflight_manifest(manifest, resolutions)
        if preflight["template"]["status"] == "already_imported":
            return {
                "success": True,
                "already_imported": True,
                "workflow_id": preflight["template"]["existing_id"],
                "name": preflight["template"]["name"],
            }
        if not preflight["ready"]:
            raise TemplateTransferError(
                "import_requirements_unresolved",
                "仍有未解决的依赖、冲突或必填配置",
                {"preflight": preflight},
            )
        secrets = resolutions.get("secrets") if isinstance(resolutions.get("secrets"), dict) else {}
        graph_path = str((manifest.get("template") or {}).get("workflow_path") or "workflow.json")
        graph = json.loads(archive.read(graph_path).decode("utf-8"))
        _apply_secret_inputs(manifest, graph, secrets)

        dependencies = manifest.get("dependencies") or {}
        model_map: dict[str, MLModel] = {}
        algorithm_map: dict[str, Algorithm] = {}
        external_map: dict[str, ExternalApi] = {}
        hook_map: dict[str, Hook] = {}
        created = {"models": [], "algorithms": [], "external_apis": [], "hooks": []}

        algorithm_items = dependencies.get("algorithms", []) or []
        new_algorithm_count = sum(
            1 for item in preflight["dependencies"]["algorithms"]
            if item.get("status") == "import"
        )
        quota_context = quota_capacity("algorithms", requested=new_algorithm_count) if new_algorithm_count else db.atomic()
        with quota_context:
            with db.atomic():
                for item in dependencies.get("models", []) or []:
                    portable_id = str(item.get("portable_id") or "")
                    model_resolution = _resolution_entry(
                        resolutions, "models", portable_id
                    )
                    fork_resource = model_resolution.get("action") == "rename"
                    mapped_id = _resolution_target_id(resolutions, "models", portable_id)
                    target = MLModel.get_by_id(mapped_id) if mapped_id else None
                    if not target and not fork_resource:
                        target = MLModel.get_or_none(MLModel.portable_id == portable_id)
                    if not target and not fork_resource:
                        target = _find_model_by_digest(
                            str(item.get("artifact_sha256") or ""),
                            str(item.get("metadata_fingerprint") or ""),
                        )
                    if not target:
                        target = _create_model_from_package(
                            archive,
                            item,
                            username=username,
                            resolution=model_resolution,
                        )
                        created_paths.append(target.file_path)
                        created["models"].append(target.id)
                    model_map[portable_id] = target

                for item in dependencies.get("external_apis", []) or []:
                    portable_id = str(item.get("portable_id") or "")
                    resolution = _resolution_entry(
                        resolutions, "external_apis", portable_id
                    )
                    existing_portable = ExternalApi.get_or_none(ExternalApi.portable_id == portable_id)
                    fork_resource = bool(resolution.get("action") == "rename" and existing_portable)
                    target = None if fork_resource else existing_portable
                    mapped_id = _resolution_target_id(resolutions, "external_apis", portable_id)
                    if not target and mapped_id:
                        target = ExternalApi.get_by_id(mapped_id)
                    if not target:
                        target = ExternalApi.create(
                            portable_id=str(uuid.uuid4()) if fork_resource else portable_id,
                            name=resolution.get("name") or item.get("name"),
                            description=item.get("description"),
                            endpoint_url=item.get("endpoint_url"),
                            method=item.get("method") or "POST",
                            headers_json=json.dumps(item.get("headers") or {}, ensure_ascii=False),
                            request_template_json=json.dumps(item.get("request_template") or {}, ensure_ascii=False),
                            input_schema_json=json.dumps(item.get("input_schema") or [], ensure_ascii=False),
                            output_schema_json=json.dumps(item.get("output_schema") or [], ensure_ascii=False),
                            output_mapping_json=json.dumps(item.get("output_mapping") or {}, ensure_ascii=False),
                            timeout_seconds=int(item.get("timeout_seconds") or 30),
                            enabled=bool(item.get("enabled", True)),
                            created_at=datetime.now(),
                            updated_at=datetime.now(),
                            created_by=username,
                        )
                        created["external_apis"].append(target.id)
                    external_map[portable_id] = target

                for item in algorithm_items:
                    portable_id = str(item.get("portable_id") or "")
                    resolution = _resolution_entry(
                        resolutions, "algorithms", portable_id
                    )
                    existing_portable = Algorithm.get_or_none(Algorithm.portable_id == portable_id)
                    fork_resource = bool(resolution.get("action") == "rename" and existing_portable)
                    target = None if fork_resource else existing_portable
                    mapped_id = _resolution_target_id(resolutions, "algorithms", portable_id)
                    if not target and mapped_id:
                        target = Algorithm.get_by_id(mapped_id)
                    if not target:
                        target_portable_id = str(uuid.uuid4()) if fork_resource else portable_id
                        prefix = f"algorithm-{target_portable_id}"
                        script_path = _extract_scripts(archive, item, prefix)
                        if script_path:
                            created_paths.append(str(Path(
                                get_script_loader().resolve_path(
                                    f"imports/{prefix}/placeholder.py", writable=True
                                )
                            ).parent))
                        script_config = _restore_model_refs(item.get("script_config") or {}, model_map)
                        ext_config = _restore_model_refs(item.get("ext_config") or {}, model_map)
                        target = Algorithm.create(
                            portable_id=target_portable_id,
                            name=resolution.get("name") or item.get("name"),
                            description=item.get("description"),
                            script_path=script_path,
                            script_config=json.dumps(script_config, ensure_ascii=False),
                            ext_config_json=json.dumps(ext_config, ensure_ascii=False),
                            enabled_hooks=item.get("enabled_hooks"),
                            created_at=datetime.now(),
                            updated_at=datetime.now(),
                            created_by=username,
                        )
                        created["algorithms"].append(target.id)
                        imported_hook_ids = []
                        for hook_item in item.get("hooks", []) or []:
                            hook_portable_id = str(hook_item.get("portable_id") or "")
                            hook = hook_map.get(hook_portable_id)
                            if not hook:
                                hook_resolution = _resolution_entry(
                                    resolutions, "hooks", hook_portable_id
                                )
                                existing_hook = Hook.get_or_none(Hook.portable_id == hook_portable_id)
                                fork_hook = bool(
                                    hook_resolution.get("action") == "rename" and existing_hook
                                )
                                hook = None if fork_hook else existing_hook
                                mapped_hook_id = _resolution_target_id(
                                    resolutions, "hooks", hook_portable_id
                                )
                                if not hook and mapped_hook_id:
                                    hook = Hook.get_by_id(mapped_hook_id)
                                if not hook:
                                    target_hook_portable_id = (
                                        str(uuid.uuid4()) if fork_hook else hook_portable_id
                                    )
                                    hook_prefix = f"hook-{target_hook_portable_id}"
                                    hook_script_path = _extract_scripts(
                                        archive, hook_item, hook_prefix
                                    )
                                    if hook_script_path:
                                        created_paths.append(
                                            str(Path(
                                                get_script_loader().resolve_path(
                                                    f"imports/{hook_prefix}/placeholder.py",
                                                    writable=True,
                                                )
                                            ).parent)
                                        )
                                    hook = Hook.create(
                                        portable_id=target_hook_portable_id,
                                        name=hook_resolution.get("name") or hook_item.get("name"),
                                        hook_point=hook_item.get("hook_point"),
                                        script_path=hook_script_path,
                                        entry_function=hook_item.get("entry_function") or "execute",
                                        priority=int(hook_item.get("priority") or 100),
                                        condition_json=json.dumps(hook_item.get("condition") or {}, ensure_ascii=False),
                                        enabled=bool(hook_item.get("enabled", True)),
                                        created_at=datetime.now(),
                                    )
                                    created["hooks"].append(hook.id)
                                hook_map[hook_portable_id] = hook
                            AlgorithmHook.get_or_create(
                                algorithm=target,
                                hook=hook,
                                defaults={
                                    "enabled": bool(hook_item.get("relation_enabled", True)),
                                    "hook_config": json.dumps(hook_item.get("hook_config") or {}, ensure_ascii=False),
                                },
                            )
                            imported_hook_ids.append(hook.id)
                        if imported_hook_ids:
                            target.enabled_hooks = json.dumps(imported_hook_ids)
                            target.save(only=[Algorithm.enabled_hooks])
                    algorithm_map[portable_id] = target

                graph = _restore_graph(graph, algorithm_map, external_map, model_map)
                graph = merge_workflow_webhook_secrets({}, graph)
                graph = merge_workflow_http_request_secrets({}, graph)
                valid, error = validate_template_source_node(graph)
                if not valid:
                    raise TemplateTransferError("invalid_template", error or "模板结构无效")
                _validate_imported_workflow(graph)

                template_info = manifest.get("template") or {}
                resolution = resolutions.get("template") if isinstance(resolutions.get("template"), dict) else {}
                existing_portable_template = Workflow.get_or_none(
                    Workflow.portable_id == str(template_info.get("portable_id") or "")
                )
                fork_template = bool(
                    resolution.get("action") == "rename" and existing_portable_template
                )
                workflow = Workflow.create(
                    portable_id=(
                        str(uuid.uuid4()) if fork_template else template_info.get("portable_id")
                    ),
                    name=resolution.get("name") or template_info.get("name"),
                    description=template_info.get("description"),
                    workflow_data=json.dumps(graph, ensure_ascii=False),
                    is_active=False,
                    is_template=True,
                    source_template=None,
                    video_source=None,
                    config_version=1,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                    created_by=username,
                )
        return {
            "success": True,
            "already_imported": False,
            "workflow_id": workflow.id,
            "name": workflow.name,
            "created": created,
            "mapping": {
                "models": {key: value.id for key, value in model_map.items()},
                "algorithms": {key: value.id for key, value in algorithm_map.items()},
                "external_apis": {key: value.id for key, value in external_map.items()},
                "hooks": {key: value.id for key, value in hook_map.items()},
            },
        }
    except Exception:
        for path in sorted(set(created_paths), key=len, reverse=True):
            candidate = Path(path)
            if candidate.is_dir():
                shutil.rmtree(candidate, ignore_errors=True)
            elif candidate.exists():
                candidate.unlink()
        raise
    finally:
        archive.close()
