from copy import deepcopy

from app.core.webhook_workflow_config import (
    mask_workflow_webhook_secrets,
    merge_workflow_webhook_secrets,
    validate_workflow_webhook_nodes,
)
from app.core.workflow_types import WebhookNodeData, create_node_data


def _workflow():
    return {
        "nodes": [
            {"id": "source-1", "type": "source", "dataId": 1},
            {"id": "alert-1", "type": "alert", "data": {}},
            {
                "id": "webhook-1",
                "type": "webhook",
                "config": {
                    "provider": "dingtalk",
                    "endpoint_url": "https://oapi.dingtalk.com/robot/send?access_token=token-value",
                    "headers": [
                        {"name": "Authorization", "value": "Bearer token", "sensitive": True},
                        {"name": "X-Tenant", "value": "tenant-a"},
                    ],
                    "provider_options": {"signing_secret": "SEC-value"},
                    "timeout_seconds": 5,
                    "max_attempts": 3,
                    "retry_backoff_seconds": 1,
                },
            },
        ],
        "connections": [
            {"from": "source-1", "to": "alert-1"},
            {"from": "alert-1", "to": "webhook-1"},
        ],
    }


def test_create_webhook_node_data():
    node = create_node_data(_workflow()["nodes"][2])
    assert isinstance(node, WebhookNodeData)
    assert node.config["provider"] == "dingtalk"


def test_validate_webhook_requires_one_alert_upstream_and_terminal_position():
    valid, message = validate_workflow_webhook_nodes(_workflow())
    assert valid is True, message

    invalid_upstream = _workflow()
    invalid_upstream["connections"][1]["from"] = "source-1"
    valid, message = validate_workflow_webhook_nodes(invalid_upstream)
    assert valid is False
    assert "只能直接连接告警输出节点" in message

    invalid_outgoing = _workflow()
    invalid_outgoing["connections"].append({"from": "webhook-1", "to": "source-1"})
    valid, message = validate_workflow_webhook_nodes(invalid_outgoing)
    assert valid is False
    assert "终端节点" in message


def test_mask_and_merge_preserve_secrets_without_returning_them():
    stored = _workflow()
    masked = mask_workflow_webhook_secrets(stored)
    masked_config = masked["nodes"][2]["config"]

    assert masked_config["endpoint_url"] == ""
    assert "token-value" not in masked_config["endpoint_display"]
    assert "/robot/send" not in masked_config["endpoint_display"]
    assert masked_config["provider_options"]["signing_secret_configured"] is True
    assert "signing_secret" not in masked_config["provider_options"]
    assert masked_config["headers"][0]["value"] == ""
    assert masked_config["headers"][1]["value"] == "tenant-a"

    merged = merge_workflow_webhook_secrets(stored, deepcopy(masked))
    merged_config = merged["nodes"][2]["config"]
    assert merged_config["endpoint_url"].endswith("access_token=token-value")
    assert merged_config["provider_options"]["signing_secret"] == "SEC-value"
    assert merged_config["headers"][0]["value"] == "Bearer token"
    assert "endpoint_display" not in merged_config


def test_merge_allows_replacing_credentials():
    incoming = mask_workflow_webhook_secrets(_workflow())
    config = incoming["nodes"][2]["config"]
    config["endpoint_url"] = "https://oapi.dingtalk.com/robot/send?access_token=new-token"
    config["provider_options"]["signing_secret"] = "SEC-new"
    config["headers"][0]["value"] = "Bearer new"

    merged = merge_workflow_webhook_secrets(_workflow(), incoming)
    merged_config = merged["nodes"][2]["config"]
    assert merged_config["endpoint_url"].endswith("access_token=new-token")
    assert merged_config["provider_options"]["signing_secret"] == "SEC-new"
    assert merged_config["headers"][0]["value"] == "Bearer new"


def test_mask_and_merge_dictionary_headers_and_endpoint_path_tokens():
    stored = _workflow()
    config = stored["nodes"][2]["config"]
    config["endpoint_url"] = "https://hooks.example/services/team/secret-token"
    config["headers"] = {
        "Authorization": "Bearer dictionary-token",
        "X-Api-Key": "dictionary-api-key",
        "X-Tenant": "tenant-a",
    }

    masked = mask_workflow_webhook_secrets(stored)
    masked_config = masked["nodes"][2]["config"]

    assert "services" not in masked_config["endpoint_display"]
    assert "secret-token" not in masked_config["endpoint_display"]
    assert isinstance(masked_config["headers"], list)
    assert masked_config["headers"][0]["value"] == ""
    assert masked_config["headers"][0]["value_configured"] is True
    assert masked_config["headers"][1]["value"] == ""
    assert masked_config["headers"][2]["value"] == "tenant-a"

    merged = merge_workflow_webhook_secrets(stored, deepcopy(masked))
    merged_headers = {
        entry["name"]: entry["value"]
        for entry in merged["nodes"][2]["config"]["headers"]
    }
    assert merged_headers["Authorization"] == "Bearer dictionary-token"
    assert merged_headers["X-Api-Key"] == "dictionary-api-key"
