from datetime import datetime
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from peewee import SqliteDatabase

from app.core.database_models import Algorithm, SystemSetting, VideoSource, Workflow
from app.core import license_service


@pytest.fixture
def license_db(monkeypatch):
    test_db = SqliteDatabase(':memory:', pragmas={'foreign_keys': 1})
    models = [Algorithm, VideoSource, Workflow, SystemSetting]
    with test_db.bind_ctx(models):
        test_db.connect()
        test_db.create_tables(models)
        monkeypatch.setattr(license_service, 'db', test_db)
        monkeypatch.setattr(license_service, 'get_node_id', lambda: 'node-test')
        yield test_db
        test_db.close()


@pytest.fixture
def signing_keys():
    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode('ascii')
    return private_key, public_pem


def make_token(private_key, **overrides):
    now = int(time.time())
    claims = {
        'schema_version': 1,
        'license_id': 'license-test',
        'customer': 'Test Customer',
        'node_id': 'node-test',
        'iat': now - 10,
        'nbf': now - 10,
        'exp': now + 3600,
        'limits': {'video_sources': 8, 'algorithms': 12},
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm='EdDSA', headers={'kid': 'vendor-v1'})


def create_algorithm(index):
    return Algorithm.create(
        name=f'algorithm-{index}',
        script_path=f'algorithm-{index}.py',
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


def test_missing_license_uses_permanent_free_tier(license_db):
    evaluation = license_service.evaluate_license()

    assert evaluation.tier == 'free'
    assert evaluation.license_status == 'missing'
    assert evaluation.limits == {'video_sources': 1, 'algorithms': 3}


def test_signed_license_is_bound_to_node_and_enforces_time(license_db, signing_keys):
    private_key, public_pem = signing_keys
    token = make_token(private_key)

    claims = license_service.decode_and_validate_token(token, public_key=public_pem)
    assert claims['limits']['video_sources'] == 8

    with pytest.raises(license_service.LicenseError) as mismatch:
        license_service.decode_and_validate_token(
            token,
            public_key=public_pem,
            node_id='another-node',
        )
    assert mismatch.value.code == 'license_node_mismatch'

    expired = make_token(private_key, exp=int(time.time()) - 1)
    with pytest.raises(license_service.LicenseError) as expiry:
        license_service.decode_and_validate_token(expired, public_key=public_pem)
    assert expiry.value.code == 'license_expired'


def test_wildcard_node_license_is_valid_on_any_node(license_db, signing_keys):
    private_key, public_pem = signing_keys
    token = make_token(private_key, customer='*', node_id='*')

    claims = license_service.decode_and_validate_token(
        token,
        public_key=public_pem,
        node_id='any-deployment-node',
    )

    assert claims['customer'] == '*'
    assert claims['node_id'] == '*'


def test_regular_license_requires_exact_node_match(license_db, signing_keys):
    private_key, public_pem = signing_keys
    token = make_token(private_key, node_id='Node-Case-Sensitive')

    claims = license_service.decode_and_validate_token(
        token,
        public_key=public_pem,
        node_id='Node-Case-Sensitive',
    )
    assert claims['node_id'] == 'Node-Case-Sensitive'

    with pytest.raises(license_service.LicenseError) as mismatch:
        license_service.decode_and_validate_token(
            token,
            public_key=public_pem,
            node_id='node-case-sensitive',
        )
    assert mismatch.value.code == 'license_node_mismatch'


def test_customer_wildcard_does_not_override_regular_node_binding(license_db, signing_keys):
    private_key, public_pem = signing_keys
    token = make_token(private_key, customer='*', node_id='licensed-node')

    with pytest.raises(license_service.LicenseError) as mismatch:
        license_service.decode_and_validate_token(
            token,
            public_key=public_pem,
            node_id='another-node',
        )
    assert mismatch.value.code == 'license_node_mismatch'


def test_tampered_or_invalidly_signed_wildcard_license_is_rejected(license_db, signing_keys):
    private_key, public_pem = signing_keys
    different_private_key = Ed25519PrivateKey.generate()
    valid_token = make_token(private_key, customer='*', node_id='*')
    header, payload, signature = valid_token.split('.')
    replacement = 'A' if payload[0] != 'A' else 'B'
    tampered_token = '.'.join((header, replacement + payload[1:], signature))
    invalidly_signed_token = make_token(different_private_key, customer='*', node_id='*')

    for token in (tampered_token, invalidly_signed_token):
        with pytest.raises(license_service.LicenseError) as error:
            license_service.decode_and_validate_token(
                token,
                public_key=public_pem,
                node_id='any-deployment-node',
            )
        assert error.value.code == 'license_signature_invalid'


@pytest.mark.parametrize('invalid_node_id', ['', '   ', ' * '])
def test_non_exact_wildcard_node_id_cannot_bypass_validation(
    license_db,
    signing_keys,
    invalid_node_id,
):
    private_key, public_pem = signing_keys
    token = make_token(private_key, customer='*', node_id=invalid_node_id)

    with pytest.raises(license_service.LicenseError) as error:
        license_service.decode_and_validate_token(
            token,
            public_key=public_pem,
            node_id='any-deployment-node',
        )
    expected_code = (
        'license_claims_invalid' if not invalid_node_id.strip() else 'license_node_mismatch'
    )
    assert error.value.code == expected_code


def test_missing_node_id_cannot_bypass_validation(license_db, signing_keys):
    private_key, public_pem = signing_keys
    now = int(time.time())
    token = jwt.encode(
        {
            'schema_version': 1,
            'license_id': 'license-test',
            'customer': '*',
            'iat': now - 10,
            'nbf': now - 10,
            'exp': now + 3600,
            'limits': {'video_sources': 8, 'algorithms': 12},
        },
        private_key,
        algorithm='EdDSA',
        headers={'kid': 'vendor-v1'},
    )

    with pytest.raises(license_service.LicenseError) as error:
        license_service.decode_and_validate_token(
            token,
            public_key=public_pem,
            node_id='any-deployment-node',
        )
    assert error.value.code == 'license_signature_invalid'


def test_tampered_license_is_rejected(license_db, signing_keys):
    private_key, public_pem = signing_keys
    token = make_token(private_key)
    header, payload, signature = token.split('.')
    replacement = 'A' if payload[0] != 'A' else 'B'
    tampered = '.'.join((header, replacement + payload[1:], signature))

    with pytest.raises(license_service.LicenseError) as error:
        license_service.decode_and_validate_token(tampered, public_key=public_pem)
    assert error.value.code == 'license_signature_invalid'


def test_free_quota_blocks_second_source_and_fourth_algorithm(license_db):
    with license_service.quota_capacity('video_sources'):
        VideoSource.create(name='one', source_code='one', source_url='rtsp://one')
    with pytest.raises(license_service.LicenseError) as source_error:
        with license_service.quota_capacity('video_sources'):
            VideoSource.create(name='two', source_code='two', source_url='rtsp://two')
    assert source_error.value.code == 'license_quota_exceeded'

    for index in range(3):
        with license_service.quota_capacity('algorithms'):
            create_algorithm(index)
    with pytest.raises(license_service.LicenseError):
        with license_service.quota_capacity('algorithms'):
            create_algorithm(4)


def test_free_runtime_uses_earliest_eligible_source_and_algorithms(license_db):
    source_one = VideoSource.create(
        name='one', source_code='one', source_url='rtsp://one', enabled=True
    )
    source_two = VideoSource.create(
        name='two', source_code='two', source_url='rtsp://two', enabled=True
    )
    for source in (source_one, source_two):
        Workflow.create(
            name=f'workflow-{source.id}',
            workflow_data='{}',
            is_active=True,
            is_template=False,
            video_source=source,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
    algorithms = [create_algorithm(index) for index in range(4)]

    entitlements = license_service.runtime_entitlements()

    assert entitlements['source_ids'] == {source_one.id}
    assert entitlements['algorithm_ids'] == {algorithm.id for algorithm in algorithms[:3]}


def test_clock_rollback_downgrades_stored_paid_license(license_db, signing_keys, monkeypatch):
    private_key, public_pem = signing_keys
    now = int(time.time())
    SystemSetting.create(key=license_service.LICENSE_TOKEN_KEY, value=make_token(private_key))
    SystemSetting.create(key=license_service.LICENSE_CLOCK_KEY, value=str(now + 1000))
    monkeypatch.setattr(license_service, '_read_public_key', lambda: public_pem)

    evaluation = license_service.evaluate_license(now=now)

    assert evaluation.tier == 'free'
    assert evaluation.license_status == 'license_clock_rollback'


def test_paid_runtime_enforces_lower_license_limits(license_db, signing_keys, monkeypatch):
    private_key, public_pem = signing_keys
    SystemSetting.create(
        key=license_service.LICENSE_TOKEN_KEY,
        value=make_token(
            private_key,
            limits={'video_sources': 2, 'algorithms': 3},
        ),
    )
    monkeypatch.setattr(license_service, '_read_public_key', lambda: public_pem)

    sources = [
        VideoSource.create(
            name=f'source-{index}',
            source_code=f'source-{index}',
            source_url=f'rtsp://source/{index}',
            enabled=True,
        )
        for index in range(3)
    ]
    for source in sources:
        Workflow.create(
            name=f'workflow-{source.id}',
            workflow_data='{}',
            is_active=True,
            is_template=False,
            video_source=source,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
    algorithms = [create_algorithm(index) for index in range(5)]

    entitlements = license_service.runtime_entitlements()

    assert entitlements['evaluation'].paid is True
    assert entitlements['source_ids'] == {source.id for source in sources[:2]}
    assert entitlements['algorithm_ids'] == {algorithm.id for algorithm in algorithms[:3]}


def test_inactive_workflow_uses_configuration_entitlement(license_db):
    source = VideoSource.create(
        name='source', source_code='source', source_url='rtsp://source', enabled=True
    )
    workflow = Workflow.create(
        name='inactive-workflow',
        workflow_data='{}',
        is_active=False,
        is_template=False,
        video_source=source,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    assert license_service.runtime_entitlements()['source_ids'] == set()
    license_service.ensure_workflow_entitled(workflow)


def test_preview_authorization_matches_runtime_selected_source(license_db):
    inactive_source = VideoSource.create(
        name='inactive', source_code='inactive', source_url='rtsp://inactive', enabled=True
    )
    active_source = VideoSource.create(
        name='active', source_code='active', source_url='rtsp://active', enabled=True
    )
    Workflow.create(
        name='active-workflow',
        workflow_data='{}',
        is_active=True,
        is_template=False,
        video_source=active_source,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    assert license_service.runtime_entitlements()['source_ids'] == {active_source.id}
    license_service.ensure_resource_entitled('video_sources', active_source.id)
    with pytest.raises(license_service.LicenseError) as rejected:
        license_service.ensure_resource_entitled('video_sources', inactive_source.id)
    assert rejected.value.code == 'resource_not_entitled'


def test_malformed_public_key_safely_downgrades(license_db, signing_keys, monkeypatch):
    private_key, _public_pem = signing_keys
    token = make_token(private_key)
    malformed_key = '-----BEGIN PUBLIC KEY-----\ninvalid\n-----END PUBLIC KEY-----'

    with pytest.raises(license_service.LicenseError) as invalid_key:
        license_service.decode_and_validate_token(token, public_key=malformed_key)
    assert invalid_key.value.code == 'license_public_key_invalid'

    SystemSetting.create(key=license_service.LICENSE_TOKEN_KEY, value=token)
    monkeypatch.setattr(license_service, '_read_public_key', lambda: malformed_key)
    evaluation = license_service.evaluate_license()
    assert evaluation.tier == 'free'
    assert evaluation.license_status == 'license_public_key_invalid'
