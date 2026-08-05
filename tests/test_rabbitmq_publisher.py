"""RabbitMQ 预警消息体测试。

聚焦 format_alert_message 在集群 / MQ 推送场景下的来源标识字段：
node_id / host / external_alert_id，以及向后兼容字段 alert_id / source。
"""

import datetime
import types

import pytest

from app.core import rabbitmq_publisher


def _make_alert(alert_id=42, workflow=None):
    """构造最小 mock alert，覆盖 format_alert_message 访问的属性。"""
    video_source = types.SimpleNamespace(
        id=1, name="入口摄像头", source_code="CAM001"
    )
    return types.SimpleNamespace(
        id=alert_id,
        video_source=video_source,
        alert_time=datetime.datetime(2026, 8, 5, 12, 0, 0),
        alert_type="person",
        alert_message="检测到人员",
        alert_image="a.jpg",
        alert_image_ori="a_ori.jpg",
        alert_video="a.mp4",
        workflow=workflow,
    )


@pytest.fixture(autouse=True)
def _stub_external_deps(monkeypatch):
    """隔离 media URL 与节点身份，使测试只关心消息体字段契约。"""
    monkeypatch.setattr(rabbitmq_publisher, "get_public_media_config", lambda: None)
    monkeypatch.setattr(
        rabbitmq_publisher,
        "build_public_media_url",
        lambda kind, path, config=None: f"/stub/{kind}/{path}",
    )
    monkeypatch.setattr(rabbitmq_publisher, "get_node_id", lambda: "box-07")
    monkeypatch.setattr(rabbitmq_publisher, "get_hostname", lambda: "host-07")


def test_format_alert_message_includes_node_identity():
    """集群 / MQ 推送时，消息体必须带来源机器标识。"""
    msg = rabbitmq_publisher.format_alert_message(_make_alert())

    assert msg["node_id"] == "box-07"
    assert msg["host"] == "host-07"
    # external_alert_id = {node_id}-{alert.id}，集群下去重的全局唯一键
    assert msg["external_alert_id"] == "box-07-42"


def test_format_alert_message_external_id_is_unique_across_nodes(monkeypatch):
    """两台机器 alert.id 相同（各自独立 DB 自增），external_alert_id 必须不同。"""
    alert = _make_alert(alert_id=100)

    monkeypatch.setattr(rabbitmq_publisher, "get_node_id", lambda: "box-a")
    msg_a = rabbitmq_publisher.format_alert_message(alert)

    monkeypatch.setattr(rabbitmq_publisher, "get_node_id", lambda: "box-b")
    msg_b = rabbitmq_publisher.format_alert_message(alert)

    assert msg_a["external_alert_id"] == "box-a-100"
    assert msg_b["external_alert_id"] == "box-b-100"
    assert msg_a["external_alert_id"] != msg_b["external_alert_id"]


def test_format_alert_message_preserves_backward_compatible_fields():
    """alert_id（DB 自增）与 source（产品标识）保留，不破坏现有下游消费端。"""
    msg = rabbitmq_publisher.format_alert_message(_make_alert())

    assert msg["alert_id"] == 42
    assert msg["source"] == "video-ba-pipe"
    # 其余既有的视频源字段不受影响
    assert msg["source_code"] == "CAM001"
    assert msg["source_name"] == "入口摄像头"
    assert msg["alert_type"] == "person"


def test_format_alert_message_includes_workflow_when_present():
    """alert.workflow 存在时附带 workflow_id / workflow_name。"""
    workflow = types.SimpleNamespace(id=9, name="人员检测")
    msg = rabbitmq_publisher.format_alert_message(_make_alert(workflow=workflow))

    assert msg["workflow_id"] == 9
    assert msg["workflow_name"] == "人员检测"
