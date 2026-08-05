"""Validation and secret-safe API transforms for workflow webhook nodes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.core.webhook_notifier import WebhookConfigError, validate_webhook_config


_SENSITIVE_PROVIDER_FIELDS = {"signing_secret", "device_key"}
_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
}


def _node_type(node: Mapping[str, Any]) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    return str(node.get("type") or data.get("type") or "").strip().lower()


def _masked_endpoint_display(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if not parsed.scheme or not parsed.hostname:
        return ""
    query = [(key, "******") for key, _value in parse_qsl(parsed.query, keep_blank_values=True)]
    netloc = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port:
        netloc = f"{netloc}:{port}"
    masked_path = "/******" if parsed.path and parsed.path != "/" else parsed.path
    return urlunparse((parsed.scheme, netloc, masked_path, "", urlencode(query), ""))


def _is_sensitive_header(entry: Mapping[str, Any]) -> bool:
    name = str(entry.get("name") or "").strip().lower()
    return bool(entry.get("sensitive")) or name in _SENSITIVE_HEADER_NAMES


def _header_entries(raw_headers: Any) -> list[Dict[str, Any]]:
    if isinstance(raw_headers, dict):
        return [
            {"name": str(name), "value": value}
            for name, value in raw_headers.items()
        ]
    if isinstance(raw_headers, list):
        return [entry for entry in raw_headers if isinstance(entry, dict)]
    return []


def mask_workflow_webhook_secrets(workflow_data: Any) -> Any:
    """Return an API-safe workflow copy without webhook credentials."""
    masked = deepcopy(workflow_data)
    if not isinstance(masked, dict):
        return masked
    for node in masked.get("nodes", []):
        if not isinstance(node, dict) or _node_type(node) != "webhook":
            continue
        config = node.get("config")
        if not isinstance(config, dict):
            continue

        endpoint_url = str(config.get("endpoint_url") or "")
        config["endpoint_url"] = ""
        config["endpoint_url_configured"] = bool(endpoint_url)
        config["endpoint_display"] = _masked_endpoint_display(endpoint_url)

        provider_options = config.get("provider_options")
        if isinstance(provider_options, dict):
            for field in _SENSITIVE_PROVIDER_FIELDS:
                value = provider_options.get(field)
                provider_options.pop(field, None)
                provider_options[f"{field}_configured"] = bool(value)

        headers = _header_entries(config.get("headers"))
        config["headers"] = headers
        for entry in headers:
            if not _is_sensitive_header(entry):
                continue
            configured = bool(entry.get("value"))
            entry["value"] = ""
            entry["value_configured"] = configured
            entry["sensitive"] = True
    return masked


def _merge_webhook_node_secrets(existing: Mapping[str, Any], incoming: Dict[str, Any]) -> None:
    old_config = existing.get("config") if isinstance(existing.get("config"), dict) else {}
    config = incoming.setdefault("config", {})
    if not isinstance(config, dict):
        return

    clear_fields = set(config.pop("clear_sensitive_fields", []) or [])
    incoming_endpoint = str(config.get("endpoint_url") or "").strip()
    if not incoming_endpoint and "endpoint_url" not in clear_fields:
        config["endpoint_url"] = old_config.get("endpoint_url", "")
    config.pop("endpoint_url_configured", None)
    config.pop("endpoint_display", None)

    old_options = old_config.get("provider_options") if isinstance(old_config.get("provider_options"), dict) else {}
    options = config.setdefault("provider_options", {})
    if not isinstance(options, dict):
        return
    for field in _SENSITIVE_PROVIDER_FIELDS:
        options.pop(f"{field}_configured", None)
        if field in clear_fields:
            options[field] = ""
        elif not str(options.get(field) or "").strip() and old_options.get(field):
            options[field] = old_options[field]

    old_headers = {
        str(entry.get("name") or "").strip().lower(): entry
        for entry in _header_entries(old_config.get("headers"))
        if entry.get("name")
    }
    headers = _header_entries(config.get("headers"))
    config["headers"] = headers
    for entry in headers:
        entry.pop("value_configured", None)
        name = str(entry.get("name") or "").strip().lower()
        field_path = f"headers.{name}"
        if field_path in clear_fields:
            entry["value"] = ""
        elif _is_sensitive_header(entry) and not str(entry.get("value") or "") and name in old_headers:
            entry["value"] = old_headers[name].get("value", "")


def merge_workflow_webhook_secrets(existing_data: Any, incoming_data: Any) -> Any:
    """Merge masked/omitted webhook secrets into an incoming workflow copy."""
    merged = deepcopy(incoming_data)
    if not isinstance(merged, dict):
        return merged
    old_nodes = {
        str(node.get("id")): node
        for node in (existing_data or {}).get("nodes", [])
        if isinstance(node, dict) and node.get("id") is not None
    } if isinstance(existing_data, dict) else {}
    for node in merged.get("nodes", []):
        if not isinstance(node, dict) or _node_type(node) != "webhook":
            continue
        existing = old_nodes.get(str(node.get("id")))
        if existing:
            _merge_webhook_node_secrets(existing, node)
    return merged


def validate_workflow_webhook_nodes(workflow_data: Any) -> Tuple[bool, str]:
    if not isinstance(workflow_data, dict):
        return False, "workflow_data 必须是对象"
    nodes = {
        str(node.get("id")): node
        for node in workflow_data.get("nodes", [])
        if isinstance(node, dict) and node.get("id") is not None
    }
    incoming: Dict[str, list] = {}
    outgoing: Dict[str, list] = {}
    for connection in workflow_data.get("connections", []) or []:
        if not isinstance(connection, dict):
            continue
        source = str(connection.get("from") or connection.get("from_node_id") or "")
        target = str(connection.get("to") or connection.get("to_node_id") or "")
        if source and target:
            incoming.setdefault(target, []).append(source)
            outgoing.setdefault(source, []).append(target)

    for node_id, node in nodes.items():
        if _node_type(node) != "webhook":
            continue
        predecessors = incoming.get(node_id, [])
        if len(predecessors) != 1:
            return False, f"Webhook 节点 {node.get('name') or node_id} 必须且只能连接一个告警输出节点"
        upstream = nodes.get(predecessors[0])
        if not upstream or _node_type(upstream) not in {"alert", "output"}:
            return False, f"Webhook 节点 {node.get('name') or node_id} 只能直接连接告警输出节点"
        if outgoing.get(node_id):
            return False, f"Webhook 节点 {node.get('name') or node_id} 必须是终端节点"
        try:
            validate_webhook_config(node.get("config") or {})
        except (WebhookConfigError, ValueError, TypeError) as exc:
            return False, f"Webhook 节点 {node.get('name') or node_id} 配置无效: {exc}"
    return True, ""
