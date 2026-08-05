from types import SimpleNamespace
import threading
import json

import pytest

from app.core.webhook_notifier import (
    WebhookConfigError,
    WebhookDeliveryError,
    apply_public_media_urls,
    build_alert_webhook_event,
    deliver_webhook_once,
    prepare_webhook_request,
    render_webhook_template,
)
from app.core.workflow_executor import WorkflowExecutor
from app.core.workflow_types import AlertNodeData, WebhookNodeData
from app.core.public_media_config import PublicMediaConfig


@pytest.fixture(autouse=True)
def _allow_test_webhook_hosts(monkeypatch):
    monkeypatch.setenv(
        "WEBHOOK_ALLOWED_HOSTS",
        "hooks.example,oapi.dingtalk.com,api.day.app",
    )


def _event():
    return {
        "event_id": "alert:12",
        "alert": {"type": "person", "level": "warning", "message": "检测到人员"},
        "source": {"name": "东门"},
        "detection": {"detections": [{"label": "person", "confidence": 0.9}]},
        "media": {"image_url": "https://video.example/api/image/frames/a.jpg", "video_url": None},
    }


def _generic_config(**overrides):
    return {
        "provider": "generic",
        "endpoint_url": "https://hooks.example/events",
        "timeout_seconds": 5,
        "max_attempts": 3,
        "retry_backoff_seconds": 1,
        **overrides,
    }


def test_render_template_preserves_types_for_full_placeholder():
    event = _event()
    rendered = render_webhook_template(
        {
            "detections": "{{detection.detections}}",
            "summary": "{{source.name}}: {{alert.message}}",
        },
        event,
    )

    assert rendered["detections"] == event["detection"]["detections"]
    assert rendered["summary"] == "东门: 检测到人员"


def test_render_template_rejects_unknown_field():
    with pytest.raises(WebhookConfigError, match="模板字段不存在"):
        render_webhook_template("{{alert.unknown}}", _event())


def test_webhook_destination_requires_admin_allowlist(monkeypatch):
    with pytest.raises(WebhookConfigError, match="WEBHOOK_ALLOWED_HOSTS"):
        prepare_webhook_request(
            _generic_config(endpoint_url="http://127.0.0.1/internal"),
            _event(),
        )

    monkeypatch.setenv("WEBHOOK_ALLOWED_HOSTS", "127.0.0.1:8080")
    prepared = prepare_webhook_request(
        _generic_config(endpoint_url="http://127.0.0.1:8080/approved"),
        _event(),
    )
    assert prepared.url == "http://127.0.0.1:8080/approved"


def test_build_event_and_apply_public_media_urls(monkeypatch):
    monkeypatch.setattr(
        "app.core.webhook_notifier.get_public_media_config",
        lambda: PublicMediaConfig(public_base_url="", sign_media_urls=False),
    )
    source = SimpleNamespace(id=1, name="东门", source_code="gate-east")
    workflow = SimpleNamespace(id=2, name="人员检测")
    alert = SimpleNamespace(
        id=12,
        video_source=source,
        workflow=workflow,
        alert_time="2026-08-05 12:00:00",
        alert_type="person",
        alert_level="warning",
        alert_message="检测到人员",
        alert_image="gate/frame.jpg",
        alert_image_ori="gate/frame.jpg.ori.jpg",
        alert_video="gate/alert.mp4",
        detection_count=1,
    )

    event = build_alert_webhook_event(
        alert,
        {"detections": [{"label": "person"}, {"label": "person"}], "metadata": {"model": "yolo"}},
    )
    resolved = apply_public_media_urls(event, public_base_url="https://video.example/base")

    assert event["event_id"] == "alert:12"
    assert event["alert"]["detection_count"] == 2
    assert event["media"]["image_url"] is None
    assert resolved["media"]["image_url"] == "https://video.example/base/api/image/frames/gate/frame.jpg"
    assert resolved["media"]["video_url"] == "https://video.example/base/api/video/gate/alert.mp4"


def test_prepare_dingtalk_and_bark_payloads():
    dingtalk = prepare_webhook_request(
        {
            "provider": "dingtalk",
            "endpoint_url": "https://oapi.dingtalk.com/robot/send?access_token=test-token",
            "title_template": "{{alert.type}}",
            "body_template": "{{alert.message}}",
            "provider_options": {"signing_secret": "SEC-test"},
        },
        _event(),
    )
    bark = prepare_webhook_request(
        {
            "provider": "bark",
            "endpoint_url": "https://api.day.app",
            "provider_options": {"device_key": "device-key", "group": "alerts"},
        },
        _event(),
    )

    assert dingtalk.payload["msgtype"] == "text"
    assert "timestamp=" in dingtalk.url and "sign=" in dingtalk.url
    assert dingtalk.payload["text"]["content"].startswith("person\n检测到人员")
    assert _event()["media"]["image_url"] in dingtalk.payload["text"]["content"]
    assert bark.url == "https://api.day.app/push"
    assert bark.payload["device_key"] == "device-key"
    assert bark.payload["image"] == _event()["media"]["image_url"]


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=8192):
        yield json.dumps(self._payload).encode("utf-8")

    def close(self):
        return None


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


def test_delivery_classifies_retryable_and_non_retryable_http_errors():
    with pytest.raises(WebhookDeliveryError) as retryable:
        deliver_webhook_once(_generic_config(), _event(), session=_Session(_Response(503)))
    assert retryable.value.retryable is True

    with pytest.raises(WebhookDeliveryError) as permanent:
        deliver_webhook_once(_generic_config(), _event(), session=_Session(_Response(400)))
    assert permanent.value.retryable is False


def test_generic_delivery_posts_rendered_json_without_redirects():
    session = _Session(_Response(204))
    config = _generic_config(payload_template={"id": "{{event_id}}", "alert": "{{alert}}"})
    deliver_webhook_once(config, _event(), session=session)

    _args, kwargs = session.calls[0]
    assert kwargs["allow_redirects"] is False
    assert kwargs["stream"] is True
    assert kwargs["json"]["id"] == "alert:12"
    assert kwargs["json"]["alert"]["type"] == "person"


def _executor_for_webhook(alert_cache):
    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor.test_mode = True
    executor.nodes = {
        "alert-1": AlertNodeData(node_id="alert-1"),
        "webhook-1": WebhookNodeData(node_id="webhook-1", config=_generic_config()),
    }
    executor.connections = [{"from": "alert-1", "to": "webhook-1"}]
    executor.node_results_cache = {"alert-1": alert_cache}
    executor._state_lock = threading.Lock()
    return executor


def test_webhook_handler_skips_untriggered_alert_and_previews_triggered_event():
    skipped = _executor_for_webhook({"alert_triggered": False, "trigger_reason": "命中抑制期"})
    skipped._handle_webhook_node("webhook-1", {})
    assert skipped.node_results_cache["webhook-1"]["delivery_status"] == "skipped"

    event = _event()
    event["media"].update({
        "image_path": "a.jpg",
        "original_image_path": None,
        "video_path": None,
    })
    preview = _executor_for_webhook({"alert_triggered": True, "alert_event": event})
    preview._handle_webhook_node("webhook-1", {})
    cached = preview.node_results_cache["webhook-1"]
    assert cached["delivery_status"] == "preview"
    assert cached["request_preview"]["payload"]["event_id"] == "alert:12"
