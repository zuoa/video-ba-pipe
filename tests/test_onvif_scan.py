import socket
from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask
from peewee import SqliteDatabase

from app.core import license_service
from app.core.database_models import SystemSetting, User, VideoSource
from app.core.license_service import LicenseEvaluation
from app.core.onvif_media import (
    default_source_code,
    fetch_device_profiles,
    infer_stream_hint,
    inject_rtsp_credentials,
    stream_identity,
)
from app.core.onvif_probe import OnvifScanError, hosts_from_subnet, parse_ports, probe_subnet
from app.core.onvif_wsdiscovery import parse_probe_matches, pick_preferred_xaddr, probe_multicast
from app.web.api import onvif_scan
from app.web.api.auth import generate_token


PROBE_MATCHES_XML = """
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing">
  <s:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <a:EndpointReference><a:Address>urn:uuid:cam-1</a:Address></a:EndpointReference>
        <d:Types>dn:NetworkVideoTransmitter</d:Types>
        <d:Scopes>onvif://www.onvif.org/name/Front%20Gate onvif://www.onvif.org/hardware/DS-2CD2143</d:Scopes>
        <d:XAddrs>http://[fe80::1]:80/onvif/device_service http://192.168.1.64:80/onvif/device_service</d:XAddrs>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </s:Body>
</s:Envelope>
"""


class FakeSocket:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def settimeout(self, _timeout):
        return None

    def sendto(self, payload, addr):
        self.sent.append((payload, addr))

    def recvfrom(self, _size):
        if self.replies:
            return self.replies.pop(0)
        raise socket.timeout()

    def close(self):
        return None


class FakeMedia:
    def __init__(self, profiles, fail=False):
        self._profiles = profiles
        self.fail = fail
        self.stream_calls = []

    def GetProfiles(self):
        if self.fail:
            raise RuntimeError('media unavailable')
        return self._profiles

    def GetStreamUri(self, payload):
        self.stream_calls.append(payload)
        token = payload.get('ProfileToken')
        return SimpleNamespace(Uri=f'rtsp://192.168.1.64:554/Streaming/{token}')


class FakeCamera:
    def __init__(self, media=None, media2=None, device=None, media_error=None):
        self.media = media
        self.media2 = media2
        self.device = device
        self.media_error = media_error

    def create_devicemgmt_service(self):
        return self.device

    def create_media_service(self):
        if self.media_error:
            raise self.media_error
        return self.media

    def create_media2_service(self):
        if self.media2 is None:
            raise RuntimeError('media2 unavailable')
        return self.media2


def _profile(token, name, encoding, width, height):
    return SimpleNamespace(
        token=token,
        Name=name,
        VideoEncoderConfiguration=SimpleNamespace(
            Encoding=encoding,
            Resolution=SimpleNamespace(Width=width, Height=height),
        ),
    )


def test_parse_probe_matches_prefers_ipv4():
    devices = parse_probe_matches(PROBE_MATCHES_XML)
    assert len(devices) == 1
    assert devices[0]['host'] == '192.168.1.64'
    assert devices[0]['port'] == 80
    assert devices[0]['name'] == 'Front Gate'
    assert devices[0]['hardware'] == 'DS-2CD2143'
    assert devices[0]['xaddr'] == 'http://192.168.1.64:80/onvif/device_service'


def test_pick_preferred_xaddr_skips_invalid():
    assert pick_preferred_xaddr(['not-a-url', 'http://10.0.0.8/onvif/device_service']) == (
        'http://10.0.0.8/onvif/device_service'
    )


def test_probe_multicast_uses_injected_socket():
    sock = FakeSocket([(PROBE_MATCHES_XML.encode('utf-8'), ('192.168.1.64', 3702))])
    devices = probe_multicast(timeout_seconds=0.2, sock=sock)
    assert len(devices) == 1
    assert sock.sent
    assert sock.sent[0][1] == ('239.255.255.250', 3702)


def test_subnet_limits():
    with pytest.raises(OnvifScanError, match='/24'):
        hosts_from_subnet('10.0.0.0/16')
    with pytest.raises(OnvifScanError, match='端口数量'):
        parse_ports([80, 81, 82, 83, 84, 85, 86, 87, 88])


def test_probe_subnet_collects_http_hits():
    calls = []

    def fake_get(url, timeout=0.5, verify=False):
        calls.append(url)
        status = 401 if '://192.168.1.10:80/' in url else 599
        return SimpleNamespace(status_code=status)

    devices = probe_subnet(
        '192.168.1.10/32',
        ports=[80, 8000],
        timeout_seconds=2,
        http_get=fake_get,
    )
    assert [device['host'] for device in devices] == ['192.168.1.10']
    assert devices[0]['port'] == 80
    assert any(url.endswith(':80/onvif/device_service') for url in calls)


def test_inject_rtsp_credentials_encodes_special_chars():
    url = inject_rtsp_credentials(
        'rtsp://192.168.1.64:554/Streaming/Channels/101',
        'admin',
        'p@ss:word/1',
    )
    assert url == 'rtsp://admin:p%40ss%3Aword%2F1@192.168.1.64:554/Streaming/Channels/101'
    assert stream_identity(url) == ('192.168.1.64', '/Streaming/Channels/101')


def test_stream_hint_prefers_named_sub_then_smaller_resolution():
    profiles = [
        {'name': 'MainStream', 'width': 1920, 'height': 1080},
        {'name': 'Profile_2', 'width': 640, 'height': 360},
    ]
    assert infer_stream_hint('subStream', 1920, 1080, profiles) == 'sub'
    assert infer_stream_hint('Profile_2', 640, 360, profiles) == 'sub'
    assert infer_stream_hint('MainStream', 1920, 1080, profiles) == 'main'
    assert default_source_code('192.168.1.64', 'Profile_1') == 'onvif-192-168-1-64-profile-1'


def test_fetch_profiles_falls_back_to_media2():
    media2 = FakeMedia([
        _profile('Profile_1', 'MainStream', 'H264', 1920, 1080),
        _profile('Profile_2', 'SubStream', 'H265', 640, 360),
    ])
    camera = FakeCamera(
        media_error=RuntimeError('no media1'),
        media2=media2,
        device=SimpleNamespace(
            GetDeviceInformation=lambda: SimpleNamespace(
                Manufacturer='Hikvision',
                Model='DS-2CD',
                FirmwareVersion='1.0',
                SerialNumber='ABC',
            )
        ),
    )

    result = fetch_device_profiles(
        host='192.168.1.64',
        port=80,
        username='admin',
        password='secret',
        camera_factory=lambda host, port, username, password: camera,
    )

    assert result['device']['manufacturer'] == 'Hikvision'
    assert [item['stream_hint'] for item in result['profiles']] == ['main', 'sub']
    assert result['profiles'][1]['encoding'] == 'h265'
    assert result['profiles'][1]['rtsp_url'].startswith('rtsp://admin:secret@')
    assert result['profiles'][0]['default_source_code'].endswith('profile-1')


@pytest.fixture
def onvif_api(monkeypatch):
    test_db = SqliteDatabase(':memory:', pragmas={'foreign_keys': 1})
    models = [User, VideoSource, SystemSetting]

    with test_db.bind_ctx(models):
        test_db.connect()
        test_db.create_tables(models)
        monkeypatch.setattr(onvif_scan, 'db', test_db)
        monkeypatch.setattr(license_service, 'db', test_db)
        monkeypatch.setattr(
            license_service,
            'evaluate_license',
            lambda: LicenseEvaluation(
                tier='licensed',
                license_status='valid',
                license_message='',
                limits={'video_sources': 100, 'algorithms': 100},
            ),
        )

        user = User.create(
            username='admin',
            password_hash='unused',
            role='admin',
            created_at=datetime.now(),
        )
        VideoSource.create(
            name='Existing',
            source_code='onvif-existing',
            source_url='rtsp://admin:pass@192.168.1.20:554/Streaming/Channels/101',
            created_by=user.username,
        )

        app = Flask(__name__)
        app.config['TESTING'] = True
        onvif_scan.register_onvif_scan_api(app)
        token = generate_token(user.id, user.username, user.role)
        headers = {'Authorization': f'Bearer {token}'}
        yield app.test_client(), headers
        test_db.close()


def test_profiles_marks_existing_rtsp_identity(onvif_api, monkeypatch):
    client, headers = onvif_api

    def fake_fetch(**kwargs):
        return {
            'host': '192.168.1.20',
            'port': 80,
            'xaddr': 'http://192.168.1.20:80/onvif/device_service',
            'device': {'manufacturer': 'Hikvision', 'model': 'IPC'},
            'profiles': [
                {
                    'token': 'Profile_1',
                    'name': 'MainStream',
                    'encoding': 'h264',
                    'width': 1920,
                    'height': 1080,
                    'stream_hint': 'main',
                    'rtsp_url': 'rtsp://admin:secret@192.168.1.20:554/Streaming/Channels/101',
                    'default_source_code': 'onvif-192-168-1-20',
                }
            ],
        }

    monkeypatch.setattr(onvif_scan, 'fetch_device_profiles', fake_fetch)
    response = client.post(
        '/api/onvif/profiles',
        json={'xaddr': 'http://192.168.1.20/onvif/device_service', 'username': 'admin', 'password': 'secret'},
        headers=headers,
    )
    assert response.status_code == 200
    profiles = response.get_json()['profiles']
    assert profiles[0]['already_imported'] is True


def test_scan_rejects_unknown_mode(onvif_api):
    client, headers = onvif_api
    response = client.post('/api/onvif/scan', json={'mode': 'ssdp'}, headers=headers)
    assert response.status_code == 400
    assert '扫描方式' in response.get_json()['error']


def test_scan_marks_already_imported_hosts(onvif_api, monkeypatch):
    client, headers = onvif_api
    monkeypatch.setattr(
        onvif_scan,
        'probe_multicast',
        lambda timeout: [
            {
                'host': '192.168.1.20',
                'port': 80,
                'xaddr': 'http://192.168.1.20:80/onvif/device_service',
                'name': 'Lobby',
                'hardware': 'IPC',
                'scopes': '',
            },
            {
                'host': '192.168.1.30',
                'port': 80,
                'xaddr': 'http://192.168.1.30:80/onvif/device_service',
                'name': 'Yard',
                'hardware': 'IPC',
                'scopes': '',
            },
        ],
    )

    response = client.post('/api/onvif/scan', json={'mode': 'multicast'}, headers=headers)
    assert response.status_code == 200
    devices = response.get_json()['devices']
    by_host = {item['host']: item for item in devices}
    assert by_host['192.168.1.20']['already_imported'] is True
    assert by_host['192.168.1.30']['already_imported'] is False


def test_import_keeps_going_after_source_code_conflict(onvif_api):
    client, headers = onvif_api
    response = client.post(
        '/api/onvif/import',
        json={
            'sources': [
                {
                    'name': 'Dup',
                    'source_code': 'onvif-existing',
                    'source_url': 'rtsp://192.168.1.20/new',
                },
                {
                    'name': 'New Cam',
                    'source_code': 'onvif-192-168-1-30',
                    'source_url': 'rtsp://admin:secret@192.168.1.30/Streaming/Channels/102',
                    'source_codec': 'h264',
                },
            ]
        },
        headers=headers,
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload['created_count'] == 1
    assert payload['created'][0]['source_code'] == 'onvif-192-168-1-30'
    assert payload['errors'][0]['source_code'] == 'onvif-existing'
    assert VideoSource.select().count() == 2


def test_import_enforces_license_quota(onvif_api, monkeypatch):
    client, headers = onvif_api
    monkeypatch.setattr(
        license_service,
        'evaluate_license',
        lambda: LicenseEvaluation(
            tier='free',
            license_status='unlicensed',
            license_message='',
            limits={'video_sources': 1, 'algorithms': 3},
        ),
    )
    response = client.post(
        '/api/onvif/import',
        json={
            'sources': [
                {
                    'name': 'Overflow',
                    'source_code': 'onvif-overflow',
                    'source_url': 'rtsp://192.168.1.40/stream',
                }
            ]
        },
        headers=headers,
    )
    assert response.status_code == 403
    assert response.get_json()['code'] == 'license_quota_exceeded'
