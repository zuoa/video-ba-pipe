import pytest
import requests

from app.core.http_delivery_config import (
    HttpDeliveryConfig,
    normalize_http_delivery_config,
    validate_http_delivery_config,
)
from app.core import http_delivery_config, http_delivery_publisher


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, status_code=204, error=None):
        self.status_code = status_code
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return _Response(self.status_code)

    def close(self):
        pass


def test_http_config_defaults_to_dynamic_node_token():
    config = normalize_http_delivery_config({})
    assert config.auth_type == "bearer"
    assert config.use_node_id_as_token is True
    assert config.bearer_token == ""


def test_custom_token_and_header_secrets_are_masked_and_preserved():
    existing = HttpDeliveryConfig(
        endpoint_url="https://receiver.example/events",
        use_node_id_as_token=False,
        bearer_token="old-token",
        custom_headers={"X-API-Key": "old-key"},
    )
    config = normalize_http_delivery_config(
        {
            "endpoint_url": existing.endpoint_url,
            "auth_type": "bearer",
            "use_node_id_as_token": False,
            "bearer_token": "",
            "custom_headers": [{"name": "X-API-Key", "value": ""}],
        },
        existing=existing,
    )
    assert config.bearer_token == "old-token"
    assert config.custom_headers == {"X-API-Key": "old-key"}
    assert config.to_dict(include_secrets=False)["custom_headers"] == [
        {"name": "X-API-Key", "value": "", "value_configured": True}
    ]
    assert config.to_dict(include_secrets=False)["bearer_token"] == ""


@pytest.mark.parametrize("name", ["Authorization", "Content-Type", "X-VideoBA-Event-Id"])
def test_reserved_custom_headers_are_rejected(name):
    with pytest.raises(ValueError, match="不能覆盖"):
        normalize_http_delivery_config({"custom_headers": [{"name": name, "value": "secret"}]})


def test_ready_config_requires_endpoint_and_custom_token():
    with pytest.raises(ValueError, match="接收地址"):
        validate_http_delivery_config(HttpDeliveryConfig())
    with pytest.raises(ValueError, match="Token"):
        validate_http_delivery_config(
            HttpDeliveryConfig(
                endpoint_url="https://receiver.example/events",
                use_node_id_as_token=False,
            )
        )


@pytest.mark.parametrize("endpoint", ["http://host:abc/events", "http://host:99999/events"])
def test_invalid_endpoint_port_is_rejected(endpoint):
    with pytest.raises(ValueError, match="端口无效"):
        validate_http_delivery_config(
            HttpDeliveryConfig(endpoint_url=endpoint, auth_type="none")
        )


@pytest.mark.parametrize("value", [" leading-space", "\tleading-tab", "你好"])
def test_unsendable_custom_header_values_are_rejected(value):
    with pytest.raises(ValueError, match="请求头值|Latin-1"):
        normalize_http_delivery_config(
            {"custom_headers": [{"name": "X-Tenant", "value": value}]}
        )


def test_unsendable_node_id_is_rejected_before_http_is_enabled(monkeypatch):
    monkeypatch.setattr(http_delivery_config, "get_node_id", lambda: "盒子-01")
    with pytest.raises(ValueError, match="Latin-1"):
        validate_http_delivery_config(
            HttpDeliveryConfig(endpoint_url="https://receiver.example/events")
        )


def test_publisher_posts_json_with_dynamic_node_token(monkeypatch):
    session = _Session(status_code=202)
    config = HttpDeliveryConfig(
        endpoint_url="https://receiver.example/events",
        custom_headers={"X-Tenant": "north"},
    )
    monkeypatch.setattr(http_delivery_publisher, "get_node_id", lambda: "box-07")
    publisher = http_delivery_publisher.HttpDeliveryPublisher(lambda: config, session=session)
    event = {"event_id": "event-1", "event_type": "alert.created"}

    assert publisher.publish_alert(event) is True
    url, request = session.calls[0]
    assert url == config.endpoint_url
    assert request["json"] == event
    assert request["allow_redirects"] is False
    assert request["headers"]["Authorization"] == "Bearer box-07"
    assert request["headers"]["X-Tenant"] == "north"
    assert request["headers"]["X-VideoBA-Event-Id"] == "event-1"


def test_node_token_is_resolved_again_for_each_delivery(monkeypatch):
    session = _Session(status_code=200)
    config = HttpDeliveryConfig(endpoint_url="https://receiver.example/events")
    node_ids = iter(("box-old", "box-new"))
    monkeypatch.setattr(http_delivery_publisher, "get_node_id", lambda: next(node_ids))
    publisher = http_delivery_publisher.HttpDeliveryPublisher(lambda: config, session=session)

    assert publisher.publish_alert({"event_id": "event-1", "event_type": "alert.created"}) is True
    assert publisher.publish_alert({"event_id": "event-2", "event_type": "alert.created"}) is True
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer box-old"
    assert session.calls[1][1]["headers"]["Authorization"] == "Bearer box-new"


@pytest.mark.parametrize("status_code", [301, 400, 429, 500])
def test_non_2xx_response_fails(status_code):
    session = _Session(status_code=status_code)
    config = HttpDeliveryConfig(endpoint_url="https://receiver.example/events", auth_type="none")
    publisher = http_delivery_publisher.HttpDeliveryPublisher(lambda: config, session=session)
    assert publisher.publish_alert({"event_id": "event-1", "event_type": "alert.created"}) is False


def test_timeout_fails_for_outbox_retry():
    session = _Session(error=requests.Timeout("slow"))
    config = HttpDeliveryConfig(endpoint_url="https://receiver.example/events", auth_type="none")
    publisher = http_delivery_publisher.HttpDeliveryPublisher(lambda: config, session=session)
    assert publisher.publish_alert({"event_id": "event-1", "event_type": "alert.created"}) is False


def test_test_event_has_contract_markers(monkeypatch):
    monkeypatch.setattr(http_delivery_publisher, "get_node_id", lambda: "box-07")
    event = http_delivery_publisher.build_http_test_event()
    headers = http_delivery_publisher.build_http_headers(HttpDeliveryConfig(), event)
    assert event["event_type"] == "system.test"
    assert event["test"] is True
    assert headers["Authorization"] == "Bearer box-07"
    assert headers["X-VideoBA-Test"] == "true"
