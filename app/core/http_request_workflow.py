"""Validation, rendering, execution and secret handling for HTTP request nodes."""

from __future__ import annotations

from copy import deepcopy
import ipaddress
import json
import os
import re
import socket
import time
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from jsonpath_ng.ext import parse as parse_jsonpath


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
HTTP_NODE_TYPES = {"http_request", "httprequest"}
VALUE_OPERATORS = {
    "eq", "ne", "gt", "gte", "lt", "lte",
    "contains", "not_contains", "in", "not_in",
    "exists", "not_exists", "truthy", "falsy",
}
_SENSITIVE_NAMES = {
    "authorization", "proxy-authorization", "x-api-key", "api-key",
    "apikey", "api_key", "access-token", "access_token", "token", "signature",
}
_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TEMPLATE_RE = re.compile(r"\{\{\s*(\$[^{}]*?)\s*\}\}")
_FULL_TEMPLATE_RE = re.compile(r"^\s*\{\{\s*(\$[^{}]*?)\s*\}\}\s*$")
_MAX_CONDITION_DEPTH = 5
_MAX_CONDITION_RULES = 50
_PERMANENTLY_BLOCKED_HOSTS = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.tencentyun.com",
    "instance-data",
    "instance-data.ec2.internal",
}
_METADATA_ADDRESSES = {
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


class HttpRequestConfigError(ValueError):
    """Raised when an HTTP request node configuration is invalid."""


class HttpRequestRenderError(ValueError):
    """Raised when a runtime template cannot be resolved."""


class _PinnedAddressAdapter(HTTPAdapter):
    """Keep TLS checks on the hostname while connecting to a vetted IP."""

    def __init__(self, tls_hostname: str):
        self._tls_hostname = tls_hostname
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["assert_hostname"] = self._tls_hostname
        pool_kwargs["server_hostname"] = self._tls_hostname
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


def node_type(node: Mapping[str, Any]) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    return str(node.get("type") or data.get("type") or "").strip().lower()


def node_config(node: Mapping[str, Any]) -> Dict[str, Any]:
    config = node.get("config")
    if isinstance(config, dict):
        return config
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    nested = data.get("config")
    return nested if isinstance(nested, dict) else {}


def _entries(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, dict):
        return [
            {"name": str(name), "value": item, "enabled": True}
            for name, item in value.items()
        ]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _is_sensitive(entry: Mapping[str, Any]) -> bool:
    name = str(entry.get("name") or "").strip().lower()
    return bool(entry.get("sensitive")) or name in _SENSITIVE_NAMES


def _validate_entries(raw: Any, label: str, *, unique_names: bool) -> list[Dict[str, Any]]:
    if isinstance(raw, list) and any(not isinstance(item, dict) for item in raw):
        raise HttpRequestConfigError(f"{label}数组项必须是对象")
    entries = _entries(raw)
    if raw not in (None, {}, []) and not isinstance(raw, (dict, list)):
        raise HttpRequestConfigError(f"{label}必须是数组或对象")
    seen = set()
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            raise HttpRequestConfigError(f"{label}名称不能为空")
        if "\r" in name or "\n" in name:
            raise HttpRequestConfigError(f"{label}名称不能包含换行")
        normalized = name.casefold()
        if unique_names and normalized in seen:
            raise HttpRequestConfigError(f"{label}名称重复: {name}")
        seen.add(normalized)
        entry["name"] = name
        entry["enabled"] = entry.get("enabled", True) is not False
    return entries


def _validate_concrete_url(url: Any) -> str:
    value = str(url or "").strip()
    if not value:
        raise HttpRequestConfigError("请求 URL 不能为空")
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise HttpRequestConfigError("请求 URL 必须是有效的 HTTP/HTTPS 地址")
    if parsed.username or parsed.password:
        raise HttpRequestConfigError("请求 URL 不允许包含用户名或密码，请使用请求头")
    try:
        parsed.port
    except ValueError as exc:
        raise HttpRequestConfigError("请求 URL 端口无效") from exc
    return value


def _canonical_hostname(hostname: str) -> str:
    try:
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise HttpRequestConfigError("请求目标 Host 格式无效") from exc


def _configured_destination_hosts() -> set[str]:
    return {
        item.strip().rstrip(".").lower()
        for item in os.getenv("HTTP_REQUEST_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }


def _destination_host_allowed(hostname: str, port: int, scheme: str) -> bool:
    allowed = _configured_destination_hosts()
    if not allowed:
        return True
    default_port = 443 if scheme == "https" else 80
    return (
        (hostname in allowed and port == default_port)
        or f"{hostname}:{port}" in allowed
    )


def _address_is_permanently_blocked(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    candidates = [address]
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            candidates.append(address.ipv4_mapped)
        if address.sixtofour:
            candidates.append(address.sixtofour)
        if address.teredo:
            candidates.append(address.teredo[1])
    return any(
        candidate in _METADATA_ADDRESSES
        or candidate.is_loopback
        or candidate.is_unspecified
        or candidate.is_link_local
        or candidate.is_multicast
        or candidate.is_reserved
        for candidate in candidates
    )


def validate_request_destination(url: Any, *, resolver: Any = socket.getaddrinfo) -> list[str]:
    """Resolve and enforce the administrator-owned outbound destination policy."""
    value = _validate_concrete_url(url)
    parsed = urlparse(value)
    hostname = _canonical_hostname(parsed.hostname or "")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    scheme = parsed.scheme.lower()
    if hostname in _PERMANENTLY_BLOCKED_HOSTS or hostname.endswith(".localhost"):
        raise HttpRequestConfigError("请求目标为禁止访问的回环或 metadata 主机")
    if not _destination_host_allowed(hostname, port, scheme):
        raise HttpRequestConfigError(
            "请求目标不在管理员白名单 HTTP_REQUEST_ALLOWED_HOSTS 中"
        )

    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [literal]
    except ValueError:
        try:
            resolved = resolver(hostname, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise HttpRequestConfigError(f"请求目标 DNS 解析失败: {hostname}") from exc
        addresses = []
        for item in resolved:
            try:
                address = ipaddress.ip_address(str(item[4][0]).split("%", 1)[0])
            except (IndexError, ValueError, TypeError):
                continue
            if address not in addresses:
                addresses.append(address)

    if not addresses:
        raise HttpRequestConfigError(f"请求目标没有可用 IP 地址: {hostname}")

    blocked = [
        str(address) for address in addresses
        if _address_is_permanently_blocked(address)
    ]
    if blocked:
        raise HttpRequestConfigError(
            f"请求目标解析到禁止访问的回环、链路本地或 metadata 地址: {', '.join(blocked)}"
        )
    return [str(address) for address in addresses]


def _validate_url_template(url: Any) -> str:
    value = str(url or "").strip()
    if not value:
        raise HttpRequestConfigError("请求 URL 不能为空")
    if _FULL_TEMPLATE_RE.fullmatch(value):
        return value
    _validate_concrete_url(_TEMPLATE_RE.sub("placeholder", value))
    return value


def _compile_template_expressions(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _compile_template_expressions(item)
        return
    if isinstance(value, list):
        for item in value:
            _compile_template_expressions(item)
        return
    if not isinstance(value, str):
        return
    for expression in _TEMPLATE_RE.findall(value):
        try:
            parse_jsonpath(expression)
        except Exception as exc:
            raise HttpRequestConfigError(f"模板 JSONPath 无效: {expression}: {exc}") from exc


def validate_http_request_config(config: Any) -> Dict[str, Any]:
    """Validate and normalize an inline HTTP request node config."""
    if not isinstance(config, dict):
        raise HttpRequestConfigError("HTTP 请求配置必须是对象")

    normalized = deepcopy(config)
    normalized["method"] = str(normalized.get("method") or "GET").upper()
    if normalized["method"] not in HTTP_METHODS:
        raise HttpRequestConfigError("请求方法必须是 GET/POST/PUT/PATCH/DELETE")
    normalized["url"] = _validate_url_template(normalized.get("url"))

    try:
        timeout = float(normalized.get("timeout_seconds", 30))
        interval = float(normalized.get("interval_seconds", 1))
    except (TypeError, ValueError) as exc:
        raise HttpRequestConfigError("超时和调用间隔必须是数字") from exc
    if not 1 <= timeout <= 300:
        raise HttpRequestConfigError("超时必须在 1-300 秒之间")
    if not 0.1 <= interval <= 300:
        raise HttpRequestConfigError("调用间隔必须在 0.1-300 秒之间")
    normalized["timeout_seconds"] = timeout
    normalized["interval_seconds"] = interval

    normalized["query_params"] = _validate_entries(
        normalized.get("query_params", []), "Query 参数", unique_names=False,
    )
    normalized["headers"] = _validate_entries(
        normalized.get("headers", []), "请求头", unique_names=True,
    )

    body = normalized.get("json_body")
    if body is not None and not isinstance(body, (dict, list)):
        raise HttpRequestConfigError("JSON 请求体必须是对象、数组或 null")
    if normalized["method"] == "GET" and body not in (None, {}, []):
        raise HttpRequestConfigError("GET 请求不支持 JSON 请求体")

    extractors = normalized.get("extractors", [])
    if not isinstance(extractors, list):
        raise HttpRequestConfigError("返回提取规则必须是数组")
    names = set()
    normalized_extractors = []
    for item in extractors:
        if not isinstance(item, dict):
            raise HttpRequestConfigError("返回提取规则必须是对象")
        name = str(item.get("name") or "").strip()
        path = str(item.get("jsonpath") or "").strip()
        if not _VARIABLE_RE.fullmatch(name):
            raise HttpRequestConfigError(f"提取变量名无效: {name or '（空）'}")
        if name in names:
            raise HttpRequestConfigError(f"提取变量名重复: {name}")
        if not path.startswith("$"):
            raise HttpRequestConfigError(f"JSONPath 必须以 $ 开头: {name}")
        try:
            parse_jsonpath(path)
        except Exception as exc:
            raise HttpRequestConfigError(f"JSONPath 无效: {name}: {exc}") from exc
        names.add(name)
        normalized_extractors.append({
            "name": name,
            "jsonpath": path,
            "required": bool(item.get("required", False)),
        })
    normalized["extractors"] = normalized_extractors

    _compile_template_expressions({
        "url": normalized["url"],
        "query_params": normalized["query_params"],
        "headers": normalized["headers"],
        "json_body": body,
    })
    return normalized


def _resolve_jsonpath(expression: str, context: Mapping[str, Any]) -> Any:
    try:
        matches = [match.value for match in parse_jsonpath(expression).find(context)]
    except Exception as exc:
        raise HttpRequestRenderError(f"模板 JSONPath 执行失败: {expression}: {exc}") from exc
    if not matches:
        raise HttpRequestRenderError(f"模板变量没有匹配值: {expression}")
    return matches[0] if len(matches) == 1 else matches


def render_template_value(value: Any, context: Mapping[str, Any]) -> Any:
    """Render safe JSONPath placeholders while preserving exact-value types."""
    if isinstance(value, dict):
        return {str(key): render_template_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_template_value(item, context) for item in value]
    if not isinstance(value, str):
        return value

    exact = _FULL_TEMPLATE_RE.fullmatch(value)
    if exact:
        return _resolve_jsonpath(exact.group(1), context)

    def replace(match: re.Match) -> str:
        resolved = _resolve_jsonpath(match.group(1), context)
        if isinstance(resolved, (dict, list)):
            return json.dumps(resolved, ensure_ascii=False, separators=(",", ":"))
        if resolved is None:
            return "null"
        if isinstance(resolved, bool):
            return "true" if resolved else "false"
        return str(resolved)

    return _TEMPLATE_RE.sub(replace, value)


def build_rendered_request(config: Mapping[str, Any], context: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = validate_http_request_config(config)
    renderable = deepcopy(normalized)
    for field in ("query_params", "headers"):
        renderable[field] = [
            entry for entry in renderable.get(field, [])
            if entry.get("enabled", True) is not False
        ]
    rendered = render_template_value(renderable, context)
    rendered["url"] = _validate_concrete_url(rendered.get("url"))
    query = [
        (entry["name"], entry.get("value", ""))
        for entry in rendered.get("query_params", [])
    ]
    headers = {
        entry["name"]: str(entry.get("value", ""))
        for entry in rendered.get("headers", [])
    }
    body = None if rendered["method"] == "GET" else rendered.get("json_body")
    return {
        "method": rendered["method"],
        "url": rendered["url"],
        "params": query,
        "headers": headers,
        "json": body,
        "timeout": rendered["timeout_seconds"],
        "extractors": normalized["extractors"],
    }


def _pinned_url(url: str, address: str) -> Tuple[str, str]:
    parsed = urlparse(url)
    hostname = _canonical_hostname(parsed.hostname or "")
    port = parsed.port
    address_value = f"[{address}]" if ":" in address else address
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    pinned_netloc = address_value if port in (None, default_port) else f"{address_value}:{port}"
    host_value = hostname if port in (None, default_port) else f"{hostname}:{port}"
    return urlunparse(parsed._replace(netloc=pinned_netloc)), host_value


def _send_pinned_request(rendered: Mapping[str, Any], addresses: list[str]):
    """Connect to the vetted address so DNS cannot change after validation."""
    pinned, host_header = _pinned_url(str(rendered["url"]), addresses[0])
    headers = dict(rendered["headers"])
    headers["Host"] = host_header
    session = requests.Session()
    session.trust_env = False
    if pinned.lower().startswith("https://"):
        hostname = _canonical_hostname(urlparse(str(rendered["url"])).hostname or "")
        session.mount("https://", _PinnedAddressAdapter(hostname))
    return session.request(
        method=rendered["method"],
        url=pinned,
        params=rendered["params"],
        headers=headers,
        json=rendered["json"],
        timeout=rendered["timeout"],
        allow_redirects=False,
    )


def extract_response_outputs(body: Any, extractors: Iterable[Mapping[str, Any]]) -> Tuple[dict, dict, list[str]]:
    outputs: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    missing_required = []
    for extractor in extractors:
        name = str(extractor["name"])
        matches = [match.value for match in parse_jsonpath(str(extractor["jsonpath"])).find(body)]
        outputs[name] = None if not matches else matches[0] if len(matches) == 1 else matches
        metadata[name] = {"matched": bool(matches), "match_count": len(matches)}
        if extractor.get("required") and not matches:
            missing_required.append(name)
    return outputs, metadata, missing_required


def execute_http_request(
    config: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    session: Any = None,
    destination_validator: Any = validate_request_destination,
) -> Dict[str, Any]:
    """Render and execute one request, always returning a structured result."""
    started = time.monotonic()
    rendered: Optional[Dict[str, Any]] = None
    try:
        rendered = build_rendered_request(config, context)
        addresses = destination_validator(rendered["url"])
        if session is None:
            response = _send_pinned_request(rendered, addresses)
        else:
            response = session.request(
                method=rendered["method"],
                url=rendered["url"],
                params=rendered["params"],
                headers=rendered["headers"],
                json=rendered["json"],
                timeout=rendered["timeout"],
                allow_redirects=False,
            )
        try:
            body = response.json()
            text = None
        except ValueError:
            body = None
            text = response.text

        outputs, extraction_meta, missing = extract_response_outputs(
            body, rendered["extractors"],
        ) if body is not None else (
            {item["name"]: None for item in rendered["extractors"]},
            {item["name"]: {"matched": False, "match_count": 0} for item in rendered["extractors"]},
            [item["name"] for item in rendered["extractors"] if item.get("required")],
        )
        status_ok = 200 <= int(response.status_code) < 300
        error = None
        if not status_ok:
            error = {"type": "http_status", "message": f"HTTP {response.status_code}"}
        elif missing:
            error = {
                "type": "required_output_missing",
                "message": f"必填提取变量未匹配: {', '.join(missing)}",
                "variables": missing,
            }
        return {
            "success": bool(status_ok and not missing),
            "status_code": int(response.status_code),
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "outputs": outputs,
            "extraction_meta": extraction_meta,
            "response": {
                "body": body,
                "text": text,
                "headers": dict(response.headers),
            },
            "error": error,
        }
    except HttpRequestConfigError as exc:
        error_type = "config_error"
        message = str(exc)
    except HttpRequestRenderError as exc:
        error_type = "template_error"
        message = str(exc)
    except requests.Timeout as exc:
        error_type = "timeout"
        message = str(exc) or "请求超时"
    except requests.RequestException as exc:
        error_type = "network_error"
        message = str(exc)
    except Exception as exc:
        error_type = "request_error"
        message = str(exc)
    return {
        "success": False,
        "status_code": None,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "outputs": {},
        "extraction_meta": {},
        "response": {"body": None, "text": None, "headers": {}},
        "error": {"type": error_type, "message": message},
    }


def _validate_condition_group(group: Any, variables: set[str], depth: int = 1) -> int:
    if not isinstance(group, dict):
        raise HttpRequestConfigError("API 值条件组必须是对象")
    if depth > _MAX_CONDITION_DEPTH:
        raise HttpRequestConfigError(f"API 值条件最多嵌套 {_MAX_CONDITION_DEPTH} 层")
    logic = str(group.get("logic") or "and").lower()
    if logic not in {"and", "or"}:
        raise HttpRequestConfigError("API 值条件逻辑必须是 AND 或 OR")
    children = group.get("children")
    if not isinstance(children, list) or not children:
        raise HttpRequestConfigError("API 值条件组不能为空")
    count = 0
    for child in children:
        if isinstance(child, dict) and "children" in child:
            count += _validate_condition_group(child, variables, depth + 1)
            continue
        if not isinstance(child, dict):
            raise HttpRequestConfigError("API 值条件规则必须是对象")
        variable = str(child.get("variable") or "").strip()
        operator = str(child.get("operator") or "").strip().lower()
        if variable not in variables and variable not in {"$success", "$status_code", "$error_type"}:
            raise HttpRequestConfigError(f"API 值条件引用了未知变量: {variable}")
        if operator not in VALUE_OPERATORS:
            raise HttpRequestConfigError(f"API 值条件操作符无效: {operator}")
        if operator not in {"exists", "not_exists", "truthy", "falsy"} and "value" not in child:
            raise HttpRequestConfigError(f"操作符 {operator} 必须配置比较值")
        if operator in {"gt", "gte", "lt", "lte"}:
            expected = child.get("value")
            if isinstance(expected, bool) or not isinstance(expected, (int, float)):
                raise HttpRequestConfigError(f"操作符 {operator} 的比较值必须是数字")
        count += 1
    if count > _MAX_CONDITION_RULES:
        raise HttpRequestConfigError(f"API 值条件最多包含 {_MAX_CONDITION_RULES} 条规则")
    return count


def validate_http_value_conditions(workflow_data: Any) -> Tuple[bool, str]:
    if not isinstance(workflow_data, dict):
        return False, "workflow_data 必须是对象"
    nodes = {
        str(item.get("id")): item
        for item in workflow_data.get("nodes", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    incoming: Dict[str, list[str]] = {}
    for connection in workflow_data.get("connections", []) or []:
        if not isinstance(connection, dict):
            continue
        source = str(connection.get("from") or connection.get("from_node_id") or "")
        target = str(connection.get("to") or connection.get("to_node_id") or "")
        if source and target:
            incoming.setdefault(target, []).append(source)

    try:
        for item_id, item in nodes.items():
            item_type = node_type(item)
            if item_type in HTTP_NODE_TYPES:
                validate_http_request_config(node_config(item))
                continue
            if item_type != "condition":
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            kind = data.get("conditionKind") or data.get("condition_kind") or "count"
            if kind != "http_value":
                continue
            sources = incoming.get(item_id, [])
            source_id = str(data.get("sourceNodeId") or data.get("source_node_id") or "")
            if len(sources) != 1 or not source_id or sources[0] != source_id:
                raise HttpRequestConfigError("API 值条件必须且只能连接一个与配置一致的 HTTP 请求节点")
            source = nodes.get(source_id)
            if not source or node_type(source) not in HTTP_NODE_TYPES:
                raise HttpRequestConfigError("API 值条件来源必须是 HTTP 请求节点")
            source_config = validate_http_request_config(node_config(source))
            variables = {item["name"] for item in source_config["extractors"]}
            _validate_condition_group(data.get("expression"), variables)
    except HttpRequestConfigError as exc:
        return False, str(exc)
    return True, ""


def _actual_value(rule: Mapping[str, Any], result: Mapping[str, Any]) -> Tuple[Any, bool]:
    variable = str(rule.get("variable") or "")
    if variable == "$success":
        return bool(result.get("success")), True
    if variable == "$status_code":
        return result.get("status_code"), result.get("status_code") is not None
    if variable == "$error_type":
        error = result.get("error") if isinstance(result.get("error"), dict) else {}
        return error.get("type"), bool(error.get("type"))
    outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
    meta = result.get("extraction_meta") if isinstance(result.get("extraction_meta"), dict) else {}
    matched = bool((meta.get(variable) or {}).get("matched"))
    return outputs.get(variable), matched


def _evaluate_rule(rule: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    actual, exists = _actual_value(rule, result)
    operator = str(rule.get("operator") or "").lower()
    expected = rule.get("value")
    if operator == "exists":
        return exists
    if operator == "not_exists":
        return not exists
    if operator == "truthy":
        return bool(actual)
    if operator == "falsy":
        return not bool(actual)
    if operator in {"eq", "ne", "gt", "gte", "lt", "lte"}:
        actual_numeric = isinstance(actual, (int, float)) and not isinstance(actual, bool)
        expected_numeric = isinstance(expected, (int, float)) and not isinstance(expected, bool)
        same_json_type = (actual_numeric and expected_numeric) or type(actual) is type(expected)
        if not same_json_type:
            return operator == "ne"
    try:
        if operator == "eq":
            return actual == expected
        if operator == "ne":
            return actual != expected
        if operator == "gt":
            return actual_numeric and expected_numeric and actual > expected
        if operator == "gte":
            return actual_numeric and expected_numeric and actual >= expected
        if operator == "lt":
            return actual_numeric and expected_numeric and actual < expected
        if operator == "lte":
            return actual_numeric and expected_numeric and actual <= expected
        if operator in {"contains", "not_contains"}:
            matched = expected in actual if isinstance(actual, (str, list, tuple, dict)) else False
            return not matched if operator == "not_contains" else matched
        if operator in {"in", "not_in"}:
            matched = actual in expected if isinstance(expected, (list, tuple, dict, str)) else False
            return not matched if operator == "not_in" else matched
    except (TypeError, ValueError):
        return False
    return False


def evaluate_condition_expression(group: Mapping[str, Any], result: Mapping[str, Any]) -> bool:
    logic = str(group.get("logic") or "and").lower()
    evaluations = (
        evaluate_condition_expression(child, result)
        if isinstance(child, dict) and "children" in child
        else _evaluate_rule(child, result)
        for child in group.get("children", [])
    )
    return all(evaluations) if logic == "and" else any(evaluations)


def mask_workflow_http_request_secrets(workflow_data: Any) -> Any:
    masked = deepcopy(workflow_data)
    if not isinstance(masked, dict):
        return masked
    for item in masked.get("nodes", []):
        if not isinstance(item, dict) or node_type(item) not in HTTP_NODE_TYPES:
            continue
        config = node_config(item)
        for field in ("headers", "query_params"):
            entries = _entries(config.get(field))
            config[field] = entries
            for entry in entries:
                if not _is_sensitive(entry):
                    continue
                configured = entry.get("value") not in (None, "")
                entry["value"] = ""
                entry["value_configured"] = configured
                entry["sensitive"] = True
    return masked


def merge_workflow_http_request_secrets(existing_data: Any, incoming_data: Any) -> Any:
    merged = deepcopy(incoming_data)
    if not isinstance(merged, dict):
        return merged
    old_nodes = {
        str(item.get("id")): item for item in (existing_data or {}).get("nodes", [])
        if isinstance(item, dict) and item.get("id") is not None
    } if isinstance(existing_data, dict) else {}
    for item in merged.get("nodes", []):
        if not isinstance(item, dict) or node_type(item) not in HTTP_NODE_TYPES:
            continue
        existing = old_nodes.get(str(item.get("id")))
        if not existing:
            continue
        old_config = node_config(existing)
        config = node_config(item)
        clear_fields = set(config.pop("clear_sensitive_fields", []) or [])
        for field in ("headers", "query_params"):
            old_entries = {
                str(entry.get("name") or "").casefold(): entry
                for entry in _entries(old_config.get(field)) if entry.get("name")
            }
            entries = _entries(config.get(field))
            config[field] = entries
            for entry in entries:
                entry.pop("value_configured", None)
                name = str(entry.get("name") or "").casefold()
                path = f"{field}.{name}"
                if path in clear_fields:
                    entry["value"] = ""
                elif _is_sensitive(entry) and entry.get("value") in (None, "") and name in old_entries:
                    entry["value"] = old_entries[name].get("value", "")
    return merged


def mask_http_test_result(value: Any, config: Mapping[str, Any]) -> Any:
    """Mask rendered credentials in a test payload/result copy."""
    masked = deepcopy(value)
    if not isinstance(masked, dict):
        return masked
    sensitive_values = [
        entry.get("value")
        for field in ("headers", "query_params")
        for entry in _entries(config.get(field))
        if _is_sensitive(entry) and entry.get("value") not in (None, "")
    ]

    def scalar_matches(item: Any, secret: Any) -> bool:
        if isinstance(item, bool) or isinstance(secret, bool):
            return type(item) is type(secret) and item == secret
        if isinstance(item, (int, float)) and isinstance(secret, (int, float)):
            return item == secret
        return type(item) is type(secret) and item == secret

    def replace(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: replace(child) for key, child in item.items()}
        if isinstance(item, list):
            return [replace(child) for child in item]
        if any(scalar_matches(item, secret) for secret in sensitive_values):
            return "******"
        if isinstance(item, str):
            result = item
            for secret in sensitive_values:
                if isinstance(secret, str):
                    result = result.replace(secret, "******")
            return result
        return item

    return replace(masked)
