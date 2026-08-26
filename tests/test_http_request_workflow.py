from copy import deepcopy
import threading

import requests
import pytest
from datetime import datetime
from flask import Flask
from peewee import SqliteDatabase

from app.core.http_request_workflow import (
    HttpRequestConfigError,
    build_rendered_request,
    evaluate_condition_expression,
    execute_http_request,
    extract_response_outputs,
    mask_http_test_result,
    mask_workflow_http_request_secrets,
    merge_workflow_http_request_secrets,
    validate_http_request_config,
    validate_request_destination,
    validate_http_value_conditions,
)
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_types import ConditionNodeData, HttpRequestNodeData, create_node_data
from app.core.database_models import User, VideoSource, Workflow
from app.web.api import workflows as workflows_api
from app.web.api import http_requests as http_requests_api
from app.web.api.auth import generate_token


def _config(**overrides):
    value = {
        "method": "POST",
        "url": "https://example.com/check/{{ $.source.code }}",
        "query_params": [{"name": "frame", "value": "{{ $.frame.timestamp }}"}],
        "headers": [{"name": "Authorization", "value": "Bearer secret", "sensitive": True}],
        "json_body": {
            "score": "{{ $.nodes['algorithm-1'].result.metadata.score }}",
            "label": "camera={{ $.source.code }}",
        },
        "timeout_seconds": 5,
        "interval_seconds": 1,
        "extractors": [
            {"name": "risk_score", "jsonpath": "$.data.score", "required": True},
            {"name": "tags", "jsonpath": "$.data.tags[*]", "required": False},
        ],
    }
    value.update(overrides)
    return value


def _context():
    return {
        "workflow": {"id": 1, "name": "test"},
        "source": {"id": 2, "name": "Gate", "code": "east"},
        "frame": {"timestamp": 123.5},
        "nodes": {"algorithm-1": {"result": {"metadata": {"score": 0.91}}}},
    }


@pytest.fixture
def workflow_http_api(monkeypatch):
    test_db = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
    models = [User, VideoSource, Workflow]
    with test_db.bind_ctx(models):
        test_db.connect()
        test_db.create_tables(models)
        monkeypatch.setattr(workflows_api, "db", test_db)
        user = User.create(
            username="http-owner",
            password_hash="unused",
            role="user",
            created_at=datetime.now(),
        )
        source = VideoSource.create(
            name="Gate",
            source_code="gate-1",
            source_url="rtsp://example/gate",
            created_by=user.username,
        )
        app = Flask(__name__)
        app.config["TESTING"] = True
        workflows_api.register_workflows_api(app)
        http_requests_api.register_http_requests_api(app)
        token = generate_token(user.id, user.username, user.role)
        yield app.test_client(), {"Authorization": f"Bearer {token}"}, source, monkeypatch
        test_db.close()


def _workflow_graph(source_id):
    return {
        "nodes": [
            {"id": "source-1", "type": "source", "dataId": source_id},
            {"id": "http-1", "type": "http_request", "config": _config(url="https://example.com/check")},
            {
                "id": "condition-1",
                "type": "condition",
                "data": {
                    "conditionKind": "http_value",
                    "sourceNodeId": "http-1",
                    "expression": {
                        "logic": "and",
                        "children": [{"variable": "risk_score", "operator": "gte", "value": 0.8}],
                    },
                },
            },
        ],
        "connections": [
            {"from": "source-1", "to": "http-1"},
            {"from": "http-1", "to": "condition-1"},
        ],
    }


def test_create_node_data_supports_http_request_and_http_value_condition():
    request_node = create_node_data({
        "id": "http-1",
        "type": "http_request",
        "config": _config(),
    })
    condition_node = create_node_data({
        "id": "condition-1",
        "type": "condition",
        "data": {
            "conditionKind": "http_value",
            "sourceNodeId": "http-1",
            "expression": {
                "logic": "and",
                "children": [{"variable": "risk_score", "operator": "gte", "value": 0.8}],
            },
        },
    })

    assert isinstance(request_node, HttpRequestNodeData)
    assert request_node.interval_seconds == 1
    assert isinstance(condition_node, ConditionNodeData)
    assert condition_node.expression["logic"] == "and"

    nested_request_node = create_node_data({
        "id": "http-2",
        "type": "http_request",
        "data": {"config": _config(interval_seconds=2)},
    })
    assert nested_request_node.interval_seconds == 2
    assert nested_request_node.config["url"] == _config()["url"]


def test_rendered_request_preserves_exact_template_types_and_interpolates_text():
    rendered = build_rendered_request(_config(), _context())

    assert rendered["url"] == "https://example.com/check/east"
    assert rendered["params"] == [("frame", 123.5)]
    assert rendered["json"]["score"] == 0.91
    assert isinstance(rendered["json"]["score"], float)
    assert rendered["json"]["label"] == "camera=east"


def test_rendered_request_does_not_resolve_disabled_entries():
    config = _config(
        query_params=[
            {"name": "disabled", "value": "{{ $.missing.query }}", "enabled": False},
            {"name": "active", "value": "{{ $.source.code }}", "enabled": True},
        ],
        headers=[
            {"name": "X-Disabled", "value": "{{ $.missing.header }}", "enabled": False},
            {"name": "X-Source", "value": "{{ $.source.code }}", "enabled": True},
        ],
    )

    rendered = build_rendered_request(config, _context())

    assert rendered["params"] == [("active", "east")]
    assert rendered["headers"] == {"X-Source": "east"}


def test_extract_response_outputs_uses_scalar_array_and_required_missing_semantics():
    outputs, metadata, missing = extract_response_outputs(
        {"data": {"score": 0.9, "tags": ["person", "helmet"]}},
        _config()["extractors"],
    )
    assert outputs == {"risk_score": 0.9, "tags": ["person", "helmet"]}
    assert metadata["risk_score"] == {"matched": True, "match_count": 1}
    assert metadata["tags"] == {"matched": True, "match_count": 2}
    assert missing == []

    outputs, metadata, missing = extract_response_outputs(
        {"data": {}}, _config()["extractors"],
    )
    assert outputs == {"risk_score": None, "tags": None}
    assert metadata["risk_score"]["matched"] is False
    assert missing == ["risk_score"]


class _Response:
    status_code = 200
    headers = {"Content-Type": "application/json"}
    text = ""

    def json(self):
        return {"data": {"score": 0.93, "tags": ["person"]}}


class _Session:
    def __init__(self):
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return _Response()


class _TimeoutSession:
    @staticmethod
    def request(**_kwargs):
        raise requests.Timeout("slow")


def test_execute_http_request_returns_structured_success_and_failure():
    session = _Session()

    def validator(_url):
        return ["93.184.216.34"]

    result = execute_http_request(
        _config(), _context(), session=session, destination_validator=validator,
    )
    assert result["success"] is True
    assert result["status_code"] == 200
    assert result["outputs"]["risk_score"] == 0.93
    assert session.calls[0]["json"]["score"] == 0.91
    assert session.calls[0]["allow_redirects"] is False

    failed = execute_http_request(
        _config(), _context(), session=_TimeoutSession(), destination_validator=validator,
    )
    assert failed["success"] is False
    assert failed["error"]["type"] == "timeout"


def test_execute_http_request_does_not_follow_redirects():
    class RedirectResponse(_Response):
        status_code = 302
        headers = {"Location": "https://attacker.example/steal"}

    session = _Session()
    session_response = RedirectResponse()
    session.request = lambda **kwargs: (session.calls.append(kwargs) or session_response)

    result = execute_http_request(
        _config(),
        _context(),
        session=session,
        destination_validator=lambda _url: ["93.184.216.34"],
    )

    assert result["success"] is False
    assert result["error"]["type"] == "http_status"
    assert session.calls[0]["allow_redirects"] is False


def test_destination_policy_allows_public_hosts_without_allowlist(monkeypatch):
    monkeypatch.delenv("HTTP_REQUEST_ALLOWED_HOSTS", raising=False)

    addresses = validate_request_destination(
        "https://api.example/data",
        resolver=lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )

    assert addresses == ["93.184.216.34"]


def test_destination_policy_keeps_optional_strict_host_allowlist(monkeypatch):
    monkeypatch.setenv("HTTP_REQUEST_ALLOWED_HOSTS", "trusted.example")

    with pytest.raises(HttpRequestConfigError, match="HTTP_REQUEST_ALLOWED_HOSTS"):
        validate_request_destination(
            "https://api.example/data",
            resolver=lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
        )


def test_destination_policy_permanently_rejects_loopback_and_metadata(monkeypatch):
    monkeypatch.delenv("HTTP_REQUEST_ALLOWED_HOSTS", raising=False)

    with pytest.raises(HttpRequestConfigError, match="回环或 metadata"):
        validate_request_destination("http://localhost/admin")

    def mixed_resolver(_host, _port, **_kwargs):
        return [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ]

    with pytest.raises(HttpRequestConfigError, match="回环、链路本地或 metadata"):
        validate_request_destination("https://api.example/data", resolver=mixed_resolver)

    with pytest.raises(HttpRequestConfigError, match="metadata"):
        validate_request_destination("http://169.254.169.254/latest/meta-data")

    with pytest.raises(HttpRequestConfigError, match="metadata"):
        validate_request_destination("http://100.100.100.200/latest/meta-data")


def test_destination_policy_allows_private_networks_without_allowlist(monkeypatch):
    monkeypatch.delenv("HTTP_REQUEST_ALLOWED_HOSTS", raising=False)

    addresses = validate_request_destination(
        "http://internal.example:8080/check",
        resolver=lambda *_args, **_kwargs: [(2, 1, 6, "", ("10.20.1.8", 8080))],
    )

    assert addresses == ["10.20.1.8"]


def test_config_validation_rejects_get_body_and_invalid_extractor_name():
    try:
        validate_http_request_config(_config(method="GET"))
        assert False, "GET body should be rejected"
    except HttpRequestConfigError as exc:
        assert "GET" in str(exc)

    invalid = _config(extractors=[{"name": "bad-name", "jsonpath": "$.x"}])
    try:
        validate_http_request_config(invalid)
        assert False, "invalid variable should be rejected"
    except HttpRequestConfigError as exc:
        assert "变量名" in str(exc)


def test_mask_and_merge_preserve_sensitive_header_and_query_values():
    stored = {
        "nodes": [{
            "id": "http-1",
            "type": "http_request",
            "config": {
                **_config(),
                "query_params": [{"name": "api_key", "value": "query-secret", "sensitive": True}],
            },
        }],
        "connections": [],
    }
    masked = mask_workflow_http_request_secrets(stored)
    config = masked["nodes"][0]["config"]
    assert config["headers"][0]["value"] == ""
    assert config["headers"][0]["value_configured"] is True
    assert config["query_params"][0]["value"] == ""

    merged = merge_workflow_http_request_secrets(stored, deepcopy(masked))
    merged_config = merged["nodes"][0]["config"]
    assert merged_config["headers"][0]["value"] == "Bearer secret"
    assert merged_config["query_params"][0]["value"] == "query-secret"


def test_mask_http_test_result_masks_numeric_and_boolean_sensitive_values():
    masked = mask_http_test_result(
        {
            "rendered_request": {"query_params": [["api_key", 1234], ["flag", True]]},
            "response": {"body": {"numeric_echo": 1234, "boolean_echo": True, "other": 1235}},
        },
        {
            "query_params": [
                {"name": "api_key", "value": 1234, "sensitive": True},
                {"name": "flag", "value": True, "sensitive": True},
            ],
        },
    )

    assert masked["rendered_request"]["query_params"][0][1] == "******"
    assert masked["rendered_request"]["query_params"][1][1] == "******"
    assert masked["response"]["body"]["numeric_echo"] == "******"
    assert masked["response"]["body"]["boolean_echo"] == "******"
    assert masked["response"]["body"]["other"] == 1235


def test_validate_and_evaluate_nested_http_value_condition_with_strict_types():
    workflow = {
        "nodes": [
            {"id": "http-1", "type": "http_request", "config": _config()},
            {
                "id": "condition-1",
                "type": "condition",
                "data": {
                    "conditionKind": "http_value",
                    "sourceNodeId": "http-1",
                    "expression": {
                        "logic": "and",
                        "children": [
                            {"variable": "$success", "operator": "eq", "value": True},
                            {"variable": "risk_score", "operator": "gte", "value": 0.8},
                            {
                                "logic": "or",
                                "children": [
                                    {"variable": "tags", "operator": "contains", "value": "person"},
                                    {"variable": "$status_code", "operator": "eq", "value": 201},
                                ],
                            },
                        ],
                    },
                },
            },
        ],
        "connections": [{"from": "http-1", "to": "condition-1"}],
    }
    valid, error = validate_http_value_conditions(workflow)
    assert valid is True, error

    result = {
        "success": True,
        "status_code": 200,
        "outputs": {"risk_score": 0.9, "tags": ["person"]},
        "extraction_meta": {
            "risk_score": {"matched": True},
            "tags": {"matched": True},
        },
        "error": None,
    }
    expression = workflow["nodes"][1]["data"]["expression"]
    assert evaluate_condition_expression(expression, result) is True

    strict = {"logic": "and", "children": [{"variable": "risk_score", "operator": "eq", "value": "0.9"}]}
    assert evaluate_condition_expression(strict, result) is False


def test_workflow_executor_http_node_and_condition_use_structured_cache():
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.workflow_id = 1
    executor.workflow = type("Workflow", (), {"name": "test", "video_source": None})()
    executor.video_source = None
    executor.connections = [{"from": "http-1", "to": "condition-1"}]
    executor.nodes = {
        "http-1": HttpRequestNodeData(node_id="http-1", config=_config()),
        "condition-1": ConditionNodeData(
            node_id="condition-1",
            condition_kind="http_value",
            source_node_id="http-1",
            expression={
                "logic": "and",
                "children": [{"variable": "risk_score", "operator": "gte", "value": 0.8}],
            },
        ),
    }
    executor.node_results_cache = {
        "http-1": {
            "node_id": "http-1",
            "has_detection": True,
            "result": {
                "success": True,
                "status_code": 200,
                "outputs": {"risk_score": 0.9},
                "extraction_meta": {"risk_score": {"matched": True}},
                "error": None,
            },
        }
    }
    executor._state_lock = threading.Lock()

    context = {}
    returned = executor._handle_condition_node("condition-1", context)
    assert returned["has_detection"] is True
    assert executor.node_results_cache["condition-1"]["has_detection"] is True


def test_workflow_api_validates_masks_and_preserves_http_secrets(workflow_http_api):
    client, headers, source, _monkeypatch = workflow_http_api
    graph = _workflow_graph(source.id)
    response = client.post(
        "/api/workflows",
        json={"name": "HTTP workflow", "workflow_data": graph},
        headers=headers,
    )
    assert response.status_code == 201, response.get_json()
    workflow_id = response.get_json()["id"]

    fetched = client.get(f"/api/workflows/{workflow_id}", headers=headers)
    assert fetched.status_code == 200
    masked_graph = fetched.get_json()["workflow_data"]
    masked_header = masked_graph["nodes"][1]["config"]["headers"][0]
    assert masked_header["value"] == ""
    assert masked_header["value_configured"] is True

    updated = client.put(
        f"/api/workflows/{workflow_id}",
        json={"workflow_data": masked_graph},
        headers=headers,
    )
    assert updated.status_code == 200, updated.get_json()
    stored_header = Workflow.get_by_id(workflow_id).data_dict["nodes"][1]["config"]["headers"][0]
    assert stored_header["value"] == "Bearer secret"


def test_authenticated_http_test_endpoint_masks_rendered_credentials(workflow_http_api):
    client, headers, _source, monkeypatch = workflow_http_api
    config = _config(url="https://example.com/check")

    monkeypatch.setattr(
        http_requests_api,
        "execute_http_request",
        lambda _config, _context: {
            "success": True,
            "status_code": 200,
            "duration_ms": 1,
            "outputs": {},
            "extraction_meta": {},
            "response": {"body": {"echo": "Bearer secret"}, "text": None, "headers": {}},
            "error": None,
        },
    )
    response = client.post(
        "/api/http-requests/test",
        json={"config": config, "context": _context()},
        headers=headers,
    )
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload["rendered_request"]["headers"]["Authorization"] == "******"
    assert payload["result"]["response"]["body"]["echo"] == "******"


def test_http_test_endpoint_rejects_when_concurrency_slots_are_full(workflow_http_api):
    client, headers, _source, monkeypatch = workflow_http_api

    class FullSlots:
        @staticmethod
        def acquire(blocking=False):
            assert blocking is False
            return False

    monkeypatch.setattr(http_requests_api, "_HTTP_TEST_SLOTS", FullSlots())
    response = client.post(
        "/api/http-requests/test",
        json={"config": _config(url="https://example.com/check"), "context": _context()},
        headers=headers,
    )

    assert response.status_code == 429
    assert "繁忙" in response.get_json()["error"]


def test_http_test_endpoint_enforces_total_deadline(workflow_http_api):
    client, headers, _source, monkeypatch = workflow_http_api

    class Slots:
        releases = 0

        @staticmethod
        def acquire(blocking=False):
            assert blocking is False
            return True

        @classmethod
        def release(cls):
            cls.releases += 1

    class NeverCompletes:
        cancelled = False

        @staticmethod
        def add_done_callback(_callback):
            return None

        @staticmethod
        def result(timeout):
            assert timeout == 3
            raise http_requests_api.FutureTimeoutError()

        @classmethod
        def cancel(cls):
            cls.cancelled = True

    class Executor:
        @staticmethod
        def submit(*_args, **_kwargs):
            return NeverCompletes()

    monkeypatch.setattr(http_requests_api, "_HTTP_TEST_TOTAL_TIMEOUT_SECONDS", 3)
    monkeypatch.setattr(http_requests_api, "_HTTP_TEST_SLOTS", Slots())
    monkeypatch.setattr(http_requests_api, "_HTTP_TEST_EXECUTOR", Executor())
    response = client.post(
        "/api/http-requests/test",
        json={"config": _config(url="https://example.com/check"), "context": _context()},
        headers=headers,
    )

    assert response.status_code == 504
    assert "3 秒" in response.get_json()["error"]
    assert NeverCompletes.cancelled is True
