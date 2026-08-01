import base64
import hashlib
import hmac
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.dingtalk_notifier import (
    build_signed_webhook_url,
    send_dingtalk_text,
    validate_dingtalk_webhook_url,
)
from app.core.ops_notification_config import OpsNotificationConfig


WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=test-token"


def test_build_signed_webhook_url_uses_dingtalk_hmac_format():
    timestamp = 1234567890
    secret = "SEC-test"
    signed_url = build_signed_webhook_url(WEBHOOK, secret, timestamp)
    query = parse_qs(urlparse(signed_url).query)
    expected = base64.b64encode(
        hmac.new(
            secret.encode(),
            f"{timestamp}\n{secret}".encode(),
            hashlib.sha256,
        ).digest()
    ).decode()

    assert query["timestamp"] == [str(timestamp)]
    assert query["sign"] == [expected]
    assert query["access_token"] == ["test-token"]


@pytest.mark.parametrize(
    "url",
    [
        "http://oapi.dingtalk.com/robot/send?access_token=x",
        "https://example.com/robot/send?access_token=x",
        "https://oapi.dingtalk.com/robot/send",
        "https://oapi.dingtalk.com.evil.test/robot/send?access_token=x",
    ],
)
def test_validate_dingtalk_webhook_rejects_non_official_urls(url):
    with pytest.raises(ValueError):
        validate_dingtalk_webhook_url(url)


def test_send_dingtalk_text_posts_text_payload(monkeypatch):
    calls = []
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"errcode": 0, "errmsg": "ok"},
    )
    monkeypatch.setattr(
        "app.core.dingtalk_notifier.requests.post",
        lambda url, json, timeout: calls.append((url, json, timeout)) or response,
    )

    send_dingtalk_text(
        OpsNotificationConfig(enabled=True, webhook_url=WEBHOOK),
        "hello",
    )

    assert calls[0][1]["msgtype"] == "text"
    assert calls[0][1]["text"]["content"] == "hello"
