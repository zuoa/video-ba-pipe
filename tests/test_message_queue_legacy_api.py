from types import SimpleNamespace

import pytest

from app.core import message_queue_admin


@pytest.mark.parametrize("enabled", [True, False])
def test_legacy_rabbitmq_update_synchronizes_provider_selector(monkeypatch, enabled):
    selector_updates = []
    config = SimpleNamespace(
        enabled=enabled,
        to_dict=lambda **kwargs: {"enabled": enabled, "password": ""},
    )
    monkeypatch.setattr(
        message_queue_admin,
        "save_rabbitmq_config",
        lambda data, updated_by: config,
    )
    monkeypatch.setattr(
        message_queue_admin,
        "save_message_queue_config",
        lambda data, updated_by: selector_updates.append(data),
    )

    result = message_queue_admin.save_legacy_rabbitmq_config(
        {"enabled": enabled},
        updated_by="admin",
    )

    assert result is config
    assert selector_updates == [{"enabled": enabled, "provider": "rabbitmq"}]
