import json
import uuid

import pytest

from app.core import node_identity


@pytest.fixture(autouse=True)
def reset_node_identity_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(node_identity, 'NODE_ID', '')
    monkeypatch.setattr(node_identity, 'NODE_ID_FILE', str(tmp_path / 'node_id.json'))
    monkeypatch.setattr(node_identity, '_cached_node_id', None)
    monkeypatch.setattr(node_identity, '_cached_node_id_source', None)
    monkeypatch.setattr(node_identity, '_cached_hostname', 'test-host')


def test_explicit_node_id_has_highest_priority(monkeypatch):
    monkeypatch.setattr(node_identity, 'NODE_ID', ' edge.box.01 ')

    assert node_identity.get_node_identity() == {
        'node_id': 'edge-box-01',
        'source': 'environment',
        'hostname': 'test-host',
    }


def test_first_start_uses_and_persists_mac_address(monkeypatch):
    monkeypatch.setattr(node_identity.uuid, 'getnode', lambda: int('001122334455', 16))

    assert node_identity.get_node_id() == '00:11:22:33:44:55'
    with open(node_identity.NODE_ID_FILE, 'r', encoding='utf-8') as identity_file:
        persisted = json.load(identity_file)
    assert persisted == {
        'node_id': '00:11:22:33:44:55',
        'source': 'mac',
        'hostname': 'test-host',
    }

    monkeypatch.setattr(node_identity, '_cached_node_id', None)
    monkeypatch.setattr(node_identity, '_cached_node_id_source', None)
    monkeypatch.setattr(node_identity.uuid, 'getnode', lambda: int('00aabbccddee', 16))

    assert node_identity.get_node_identity()['node_id'] == '00:11:22:33:44:55'
    assert node_identity.get_node_identity()['source'] == 'mac'


def test_existing_identity_file_is_kept_for_backward_compatibility():
    existing = '786ce03d-294d-45bb-9d79-86edbcee263a'
    with open(node_identity.NODE_ID_FILE, 'w', encoding='utf-8') as identity_file:
        json.dump({'node_id': existing, 'hostname': 'old-host'}, identity_file)

    assert node_identity.get_node_identity() == {
        'node_id': existing,
        'source': 'persistent_file',
        'hostname': 'test-host',
    }


def test_random_getnode_fallback_uses_persisted_uuid(monkeypatch):
    # multicast 位表示 uuid.getnode 没有取得真实网卡地址，而是生成了随机值。
    monkeypatch.setattr(node_identity.uuid, 'getnode', lambda: int('010000000001', 16))
    generated = uuid.UUID('12345678-1234-5678-1234-567812345678')
    monkeypatch.setattr(node_identity.uuid, 'uuid4', lambda: generated)

    assert node_identity.get_node_identity()['node_id'] == str(generated)
    assert node_identity.get_node_identity()['source'] == 'uuid'
