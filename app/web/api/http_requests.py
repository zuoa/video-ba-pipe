"""Authenticated test endpoint for inline workflow HTTP request nodes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from copy import deepcopy
import os
import threading

from flask import jsonify, request

from app.core.database_models import Workflow
from app.core.http_request_workflow import (
    HttpRequestConfigError,
    HttpRequestRenderError,
    build_rendered_request,
    execute_http_request,
    mask_http_test_result,
    merge_workflow_http_request_secrets,
    node_config,
    node_type,
    validate_http_request_config,
)
from app.web.api.auth import require_auth, require_resource_owner


def _bounded_env_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


_HTTP_TEST_MAX_CONCURRENCY = _bounded_env_int(
    "HTTP_REQUEST_TEST_MAX_CONCURRENCY", 2, 1, 8,
)
_HTTP_TEST_TOTAL_TIMEOUT_SECONDS = _bounded_env_int(
    "HTTP_REQUEST_TEST_TOTAL_TIMEOUT_SECONDS", 10, 1, 30,
)
_HTTP_TEST_EXECUTOR = ThreadPoolExecutor(
    max_workers=_HTTP_TEST_MAX_CONCURRENCY,
    thread_name_prefix="http-request-test",
)
_HTTP_TEST_SLOTS = threading.BoundedSemaphore(_HTTP_TEST_MAX_CONCURRENCY)


def _find_node(workflow_data, node_id):
    return next(
        (
            item for item in (workflow_data or {}).get("nodes", [])
            if isinstance(item, dict) and str(item.get("id")) == str(node_id)
        ),
        None,
    )


def _base_context(workflow):
    source = getattr(workflow, "video_source", None)
    return {
        "workflow": {
            "id": workflow.id if workflow else None,
            "name": workflow.name if workflow else None,
        },
        "source": {
            "id": getattr(source, "id", None),
            "name": getattr(source, "name", None),
            "code": getattr(source, "source_code", None),
        },
        "frame": {"timestamp": None},
        "nodes": {},
    }


def register_http_requests_api(app):
    @app.route("/api/http-requests/test", methods=["POST"])
    @require_auth
    def test_http_request():
        data = request.json or {}
        workflow = None
        incoming_config = data.get("config")
        if not isinstance(incoming_config, dict):
            return jsonify({"error": "config 必须是对象"}), 400

        try:
            workflow_id = data.get("workflow_id")
            node_id = data.get("node_id")
            config = deepcopy(incoming_config)
            if workflow_id not in (None, ""):
                try:
                    workflow = Workflow.get_by_id(int(workflow_id))
                except (Workflow.DoesNotExist, TypeError, ValueError):
                    return jsonify({"error": "工作流不存在"}), 404
                owner_response = require_resource_owner(workflow)
                if owner_response:
                    return owner_response

                if node_id:
                    stored_node = _find_node(workflow.data_dict, node_id)
                    if stored_node and node_type(stored_node) in {"http_request", "httprequest"}:
                        incoming_node = {
                            "id": str(node_id),
                            "type": "http_request",
                            "config": config,
                        }
                        merged = merge_workflow_http_request_secrets(
                            {"nodes": [stored_node]}, {"nodes": [incoming_node]},
                        )
                        config = node_config(merged["nodes"][0])

            normalized = validate_http_request_config(config)
            normalized["timeout_seconds"] = min(
                normalized["timeout_seconds"],
                float(_HTTP_TEST_TOTAL_TIMEOUT_SECONDS),
            )
            runtime_context = _base_context(workflow)
            supplied_context = data.get("context")
            if isinstance(supplied_context, dict):
                runtime_context.update(deepcopy(supplied_context))

            rendered = build_rendered_request(normalized, runtime_context)
            rendered_public = {
                "method": rendered["method"],
                "url": rendered["url"],
                "query_params": rendered["params"],
                "headers": rendered["headers"],
                "json_body": rendered["json"],
                "timeout_seconds": rendered["timeout"],
            }
            if not _HTTP_TEST_SLOTS.acquire(blocking=False):
                return jsonify({"error": "HTTP 请求测试繁忙，请稍后重试"}), 429
            try:
                future = _HTTP_TEST_EXECUTOR.submit(
                    execute_http_request, normalized, runtime_context,
                )
            except Exception:
                _HTTP_TEST_SLOTS.release()
                raise
            future.add_done_callback(lambda _future: _HTTP_TEST_SLOTS.release())
            try:
                result = future.result(timeout=_HTTP_TEST_TOTAL_TIMEOUT_SECONDS)
            except FutureTimeoutError:
                future.cancel()
                return jsonify({
                    "error": f"HTTP 请求测试超过总时限（{_HTTP_TEST_TOTAL_TIMEOUT_SECONDS} 秒）",
                }), 504
            rendered_query = {}
            for name, value in rendered["params"]:
                rendered_query.setdefault(str(name).casefold(), value)
            mask_config = {
                "headers": [
                    {
                        **entry,
                        "value": rendered["headers"].get(entry["name"], entry.get("value")),
                    }
                    for entry in normalized.get("headers", [])
                ],
                "query_params": [
                    {
                        **entry,
                        "value": rendered_query.get(str(entry["name"]).casefold(), entry.get("value")),
                    }
                    for entry in normalized.get("query_params", [])
                ],
            }
            payload = mask_http_test_result(
                {"rendered_request": rendered_public, "result": result}, mask_config,
            )
            return jsonify(payload)
        except (HttpRequestConfigError, HttpRequestRenderError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            app.logger.error(f"HTTP 请求测试失败: {exc}", exc_info=True)
            return jsonify({"error": str(exc)}), 500
