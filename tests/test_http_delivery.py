import hashlib
import hmac
import json

import pytest
import requests

from app.core.http_delivery_config import (
    HttpDeliveryConfig,
    normalize_http_delivery_config,
    validate_http_delivery_config,
)
from app.core import http_delivery_config, http_delivery_publisher

TEST_HMAC_SECRET = "a-strong-shared-secret-value"


class _Response:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text
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


def test_http_config_ignores_removed_auth_modes():
    config = normalize_http_delivery_config({
        "auth_type": "none",
        "use_node_id_as_token": True,
        "bearer_token": "legacy-token",
    })
    assert set(config.to_dict()) == {
        "endpoint_url",
        "hmac_secret",
        "custom_headers",
        "timeout_seconds",
    }


def test_hmac_and_header_secrets_are_masked_and_preserved():
    existing = HttpDeliveryConfig(
        endpoint_url="https://receiver.example/events",
        hmac_secret="old-hmac-secret-value",
        custom_headers={"X-API-Key": "old-key"},
    )
    config = normalize_http_delivery_config(
        {
            "endpoint_url": existing.endpoint_url,
            "hmac_secret": "",
            "custom_headers": [{"name": "X-API-Key", "value": ""}],
        },
        existing=existing,
    )
    assert config.hmac_secret == "old-hmac-secret-value"
    assert config.custom_headers == {"X-API-Key": "old-key"}
    assert config.to_dict(include_secrets=False)["custom_headers"] == [
        {"name": "X-API-Key", "value": "", "value_configured": True}
    ]
    assert config.to_dict(include_secrets=False)["hmac_secret"] == ""
    assert config.to_dict(include_secrets=False)["hmac_secret_configured"] is True


@pytest.mark.parametrize(
    "name",
    [
        "Authorization",
        "Content-Type",
        "X-VideoBA-Event-Id",
        "X-VideoBA-Event-Type",
        "X-VideoBA-Node-Id",
        "X-VideoBA-Timestamp",
        "X-VideoBA-Nonce",
        "X-VideoBA-Signature",
        "X-VideoBA-Test",
    ],
)
def test_reserved_custom_headers_are_rejected(name):
    with pytest.raises(ValueError, match="不能覆盖"):
        normalize_http_delivery_config({"custom_headers": [{"name": name, "value": "secret"}]})


def test_ready_config_requires_endpoint_and_hmac_secret():
    with pytest.raises(ValueError, match="接收地址"):
        validate_http_delivery_config(HttpDeliveryConfig())
    with pytest.raises(ValueError, match="至少需要 16"):
        validate_http_delivery_config(
            HttpDeliveryConfig(endpoint_url="https://receiver.example/events")
        )

    with pytest.raises(ValueError, match="至少需要 16"):
        validate_http_delivery_config(
            HttpDeliveryConfig(
                endpoint_url="https://receiver.example/events",
                hmac_secret="CVAB",
            )
        )


@pytest.mark.parametrize("endpoint", ["http://host:abc/events", "http://host:99999/events"])
def test_invalid_endpoint_port_is_rejected(endpoint):
    with pytest.raises(ValueError, match="端口无效"):
        validate_http_delivery_config(
            HttpDeliveryConfig(endpoint_url=endpoint)
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
            HttpDeliveryConfig(
                endpoint_url="https://receiver.example/events",
                hmac_secret=TEST_HMAC_SECRET,
            )
        )


def test_publisher_posts_signed_json_with_custom_headers(monkeypatch):
    session = _Session(status_code=202)
    config = HttpDeliveryConfig(
        endpoint_url="https://receiver.example/events",
        hmac_secret=TEST_HMAC_SECRET,
        custom_headers={"X-Tenant": "north"},
    )
    monkeypatch.setattr(http_delivery_publisher, "get_node_id", lambda: "box-07")
    publisher = http_delivery_publisher.HttpDeliveryPublisher(lambda: config, session=session)
    event = {"event_id": "event-1", "event_type": "alert.created"}

    assert publisher.publish_alert(event) is True
    url, request = session.calls[0]
    assert url == config.endpoint_url
    assert json.loads(request["data"].decode("utf-8")) == event
    assert request["allow_redirects"] is False
    assert "Authorization" not in request["headers"]
    assert request["headers"]["X-VideoBA-Node-Id"] == "box-07"
    assert request["headers"]["X-VideoBA-Signature"].startswith("sha256=")
    assert request["headers"]["X-Tenant"] == "north"
    assert request["headers"]["X-VideoBA-Event-Id"] == "event-1"
    assert request["headers"]["X-VideoBA-Event-Type"] == "alert.created"
    assert request["headers"]["X-VideoBA-Test"] == "false"


def test_hmac_publisher_signs_exact_request_body(monkeypatch):
    class _Uuid:
        hex = "nonce-123"

    session = _Session(status_code=202)
    config = HttpDeliveryConfig(
        endpoint_url="https://receiver.example/events",
        hmac_secret=TEST_HMAC_SECRET,
    )
    event = {
        "event_id": "event-1",
        "event_type": "alert.created",
        "node_id": "box-07",
        "alert_message": "检测到人员",
    }
    monkeypatch.setattr(http_delivery_publisher, "get_node_id", lambda: "box-07")
    monkeypatch.setattr(http_delivery_publisher.time, "time", lambda: 1786953600)
    monkeypatch.setattr(http_delivery_publisher.uuid, "uuid4", lambda: _Uuid())
    publisher = http_delivery_publisher.HttpDeliveryPublisher(lambda: config, session=session)

    assert publisher.publish_alert(event) is True

    _, request = session.calls[0]
    body = request["data"]
    headers = request["headers"]
    body_digest = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(
        (
            "box-07",
            "1786953600",
            "nonce-123",
            "event-1",
            "alert.created",
            "false",
            body_digest,
        )
    )
    expected = hmac.new(
        config.hmac_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert headers["X-VideoBA-Node-Id"] == "box-07"
    assert headers["X-VideoBA-Timestamp"] == "1786953600"
    assert headers["X-VideoBA-Nonce"] == "nonce-123"
    assert headers["X-VideoBA-Event-Type"] == "alert.created"
    assert headers["X-VideoBA-Test"] == "false"
    assert headers["X-VideoBA-Signature"] == f"sha256={expected}"
    assert json.loads(body.decode("utf-8")) == event


@pytest.mark.parametrize(
    ("changed_event_type", "changed_test", "changed_header"),
    [
        ("alert.media.ready", False, "X-VideoBA-Event-Type"),
        ("alert.created", True, "X-VideoBA-Test"),
    ],
)
def test_behavior_headers_are_covered_by_signature(
    monkeypatch,
    changed_event_type,
    changed_test,
    changed_header,
):
    config = HttpDeliveryConfig(hmac_secret=TEST_HMAC_SECRET)
    event = {"event_id": "event-1", "event_type": "alert.created", "test": False}
    body = http_delivery_publisher.serialize_http_event(event)
    monkeypatch.setattr(http_delivery_publisher, "get_node_id", lambda: "box-07")

    original = http_delivery_publisher.build_http_headers(
        config,
        event,
        body=body,
        timestamp=1786953600,
        nonce="nonce-123",
    )
    changed_event = {
        **event,
        "event_type": changed_event_type,
        "test": changed_test,
    }
    changed = http_delivery_publisher.build_http_headers(
        config,
        changed_event,
        body=body,
        timestamp=1786953600,
        nonce="nonce-123",
    )

    assert changed[changed_header] != original[changed_header]
    assert changed["X-VideoBA-Signature"] != original["X-VideoBA-Signature"]


def test_node_id_is_resolved_again_for_each_delivery(monkeypatch):
    session = _Session(status_code=200)
    config = HttpDeliveryConfig(
        endpoint_url="https://receiver.example/events",
        hmac_secret=TEST_HMAC_SECRET,
    )
    node_ids = iter(("box-old", "box-new"))
    monkeypatch.setattr(http_delivery_publisher, "get_node_id", lambda: next(node_ids))
    publisher = http_delivery_publisher.HttpDeliveryPublisher(lambda: config, session=session)

    assert publisher.publish_alert({"event_id": "event-1", "event_type": "alert.created"}) is True
    assert publisher.publish_alert({"event_id": "event-2", "event_type": "alert.created"}) is True
    assert session.calls[0][1]["headers"]["X-VideoBA-Node-Id"] == "box-old"
    assert session.calls[1][1]["headers"]["X-VideoBA-Node-Id"] == "box-new"


@pytest.mark.parametrize("status_code", [301, 400, 429, 500])
def test_non_2xx_response_fails(status_code):
    session = _Session(status_code=status_code)
    config = HttpDeliveryConfig(
        endpoint_url="https://receiver.example/events",
        hmac_secret=TEST_HMAC_SECRET,
    )
    publisher = http_delivery_publisher.HttpDeliveryPublisher(lambda: config, session=session)
    assert publisher.publish_alert({"event_id": "event-1", "event_type": "alert.created"}) is False


def test_timeout_fails_for_outbox_retry():
    session = _Session(error=requests.Timeout("slow"))
    config = HttpDeliveryConfig(
        endpoint_url="https://receiver.example/events",
        hmac_secret=TEST_HMAC_SECRET,
    )
    publisher = http_delivery_publisher.HttpDeliveryPublisher(lambda: config, session=session)
    assert publisher.publish_alert({"event_id": "event-1", "event_type": "alert.created"}) is False


def test_test_event_has_contract_markers(monkeypatch):
    monkeypatch.setattr(http_delivery_publisher, "get_node_id", lambda: "box-07")
    event = http_delivery_publisher.build_http_test_event()
    headers = http_delivery_publisher.build_http_headers(
        HttpDeliveryConfig(hmac_secret=TEST_HMAC_SECRET),
        event,
    )
    assert event["event_type"] == "system.test"
    assert event["test"] is True
    assert "Authorization" not in headers
    assert headers["X-VideoBA-Node-Id"] == "box-07"
    assert headers["X-VideoBA-Signature"].startswith("sha256=")
    assert headers["X-VideoBA-Test"] == "true"
