import json
import os
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest
from flask import Flask
from peewee import SqliteDatabase

from app.core import license_service, template_transfer
from app.core.database_models import (
    Algorithm,
    AlgorithmHook,
    ExternalApi,
    Hook,
    MLModel,
    SystemSetting,
    User,
    VideoSource,
    Workflow,
)
from app.core.script_loader import ScriptLoader
from app.web.api import template_transfers as template_transfer_api
from app.web.api.auth import generate_token


@pytest.fixture
def transfer_env(monkeypatch, tmp_path):
    test_db = SqliteDatabase(':memory:', pragmas={'foreign_keys': 1})
    models = [
        Algorithm,
        ExternalApi,
        VideoSource,
        Workflow,
        Hook,
        AlgorithmHook,
        MLModel,
        SystemSetting,
        User,
    ]
    script_root = tmp_path / 'scripts'
    script_root.mkdir()
    model_root = tmp_path / 'target-models'
    model_root.mkdir()
    package_root = tmp_path / 'packages'
    package_root.mkdir()
    loader = ScriptLoader(str(script_root))

    with test_db.bind_ctx(models):
        test_db.connect()
        test_db.create_tables(models)
        monkeypatch.setattr(template_transfer, 'db', test_db)
        monkeypatch.setattr(license_service, 'db', test_db)
        monkeypatch.setattr(template_transfer, 'DEVICE_MODEL_CODE', 'VB-TEST-BOX')
        monkeypatch.setattr(template_transfer, 'MODEL_SAVE_PATH', str(model_root))
        monkeypatch.setattr(template_transfer, 'TEMPLATE_TRANSFER_PATH', str(package_root))
        monkeypatch.setattr(template_transfer_api, 'TEMPLATE_TRANSFER_PATH', str(package_root))
        monkeypatch.setattr(template_transfer, 'get_script_loader', lambda: loader)
        monkeypatch.setattr(
            template_transfer,
            'detect_inference_capabilities',
            lambda: {
                'platform': 'rk3588',
                'system': 'linux',
                'machine': 'aarch64',
                'device_model': 'Test Box',
                'device_compatible': 'vendor,test-box',
            },
        )
        yield script_root, model_root
        test_db.close()


def _create_template_resources(script_root, model_path):
    script_path = script_root / 'detector.py'
    script_path.write_text(
        'SCRIPT_METADATA = {"name": "portable", "version": "1.0"}\n'
        'def process(frame, roi_regions=None, state=None):\n'
        '    return {"detections": []}\n',
        encoding='utf-8',
    )
    now = datetime.now()
    model = MLModel.create(
        name='人员模型',
        filename='detector.onnx',
        file_path=str(model_path),
        file_size=model_path.stat().st_size,
        model_type='ONNX',
        framework='onnx',
        version='v1.0',
        classes=json.dumps({'0': 'person'}),
        created_at=now,
        updated_at=now,
        uploaded_by='admin',
    )
    algorithm = Algorithm.create(
        name='人员算法',
        description='portable algorithm',
        script_path='detector.py',
        script_config=json.dumps({'models': [{'model_id': model.id, 'name': model.name}]}),
        ext_config_json=json.dumps({'algorithm_type': 'script', 'model_ids': [model.id]}),
        created_at=now,
        updated_at=now,
        created_by='admin',
    )
    graph = {
        'nodes': [
            {
                'id': 'template-video-source',
                'type': 'source',
                'name': '视频源（复制时绑定）',
                'dataId': None,
                'videoSourceId': None,
            },
            {
                'id': 'algorithm-node',
                'type': 'algorithm',
                'name': algorithm.name,
                'dataId': algorithm.id,
                'algorithmId': algorithm.id,
                'config': {'confidence': 0.6},
            },
        ],
        'connections': [{
            'id': 'source-algorithm',
            'from': 'template-video-source',
            'to': 'algorithm-node',
        }],
    }
    template = Workflow.create(
        name='人员告警模板',
        description='portable template',
        workflow_data=json.dumps(graph),
        is_active=False,
        is_template=True,
        created_at=now,
        updated_at=now,
        created_by='admin',
    )
    return template, algorithm, model


def _manifest_from(package_path):
    with zipfile.ZipFile(package_path) as archive:
        assert archive.infolist()[0].filename == 'manifest.json'
        assert archive.infolist()[0].compress_type == zipfile.ZIP_STORED
        return json.loads(archive.read('manifest.json'))


def test_script_dependency_discovery_includes_relative_imports(transfer_env):
    script_root, _model_root = transfer_env
    package = script_root / 'portable_pkg'
    package.mkdir()
    (package / 'main.py').write_text(
        'from . import helper\n',
        encoding='utf-8',
    )
    (package / 'helper.py').write_text('VALUE = 1\n', encoding='utf-8')

    dependencies = template_transfer._script_dependencies('portable_pkg/main.py')
    assert [relative for relative, _absolute in dependencies] == [
        'portable_pkg/helper.py',
        'portable_pkg/main.py',
    ]


def test_imported_script_keeps_sibling_absolute_imports_working(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'script-model.onnx'
    source_model.write_bytes(b'script-model')
    template, algorithm, model = _create_template_resources(script_root, source_model)
    (script_root / 'helper.py').write_text('VALUE = 42\n', encoding='utf-8')
    (script_root / 'detector.py').write_text(
        'import helper\n'
        'SCRIPT_METADATA = {"name": "portable", "version": "1.0"}\n'
        'def process(frame=None, roi_regions=None, state=None):\n'
        '    return {"value": helper.VALUE}\n',
        encoding='utf-8',
    )

    package_path, _filename = template_transfer.build_export_package(
        template,
        include_models=True,
    )
    template.delete_instance()
    algorithm.delete_instance()
    model.delete_instance()
    (script_root / 'detector.py').unlink()
    (script_root / 'helper.py').unlink()

    result = template_transfer.import_package(package_path, {}, username='admin')
    imported_algorithm = Algorithm.get_by_id(result['created']['algorithms'][0])
    module, _metadata = template_transfer.get_script_loader().load(
        imported_algorithm.script_path,
        reload=True,
    )
    assert module.process()['value'] == 42


def test_export_import_round_trip_rewrites_local_ids(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'source-detector.onnx'
    source_model.write_bytes(b'portable-model-content')
    template, algorithm, model = _create_template_resources(script_root, source_model)

    package_path, filename = template_transfer.build_export_package(
        template,
        include_models=True,
    )
    assert filename.endswith('.vbt.zip')
    manifest = _manifest_from(package_path)
    assert manifest['source']['device_model_code'] == 'VB-TEST-BOX'
    assert manifest['options']['models_included'] is True
    old_algorithm_id = algorithm.id
    old_model_id = model.id

    template.delete_instance()
    algorithm.delete_instance()
    model.delete_instance()
    Algorithm.create(
        name='目标盒子已有算法',
        script_path='detector.py',
        script_config='{}',
        ext_config_json='{}',
        created_at=datetime.now(),
        updated_at=datetime.now(),
        created_by='admin',
    )
    dummy_model = tmp_path / 'existing.onnx'
    dummy_model.write_bytes(b'different-target-model')
    MLModel.create(
        name='目标盒子已有模型',
        filename='existing.onnx',
        file_path=str(dummy_model),
        file_size=dummy_model.stat().st_size,
        model_type='ONNX',
        framework='onnx',
        version='v0',
        created_at=datetime.now(),
        updated_at=datetime.now(),
        uploaded_by='admin',
    )

    preview = template_transfer.preflight_manifest(manifest, {})
    assert preview['ready'] is True
    assert preview['dependencies']['models'][0]['status'] == 'import'

    result = template_transfer.import_package(package_path, {}, username='admin')
    imported = Workflow.get_by_id(result['workflow_id'])
    imported_algorithm = Algorithm.get_by_id(result['created']['algorithms'][0])
    imported_model = MLModel.get_by_id(result['created']['models'][0])

    assert imported.is_template is True
    assert imported.is_active is False
    assert imported.video_source_id is None
    assert imported.data_dict['nodes'][1]['dataId'] == imported_algorithm.id
    assert imported_algorithm.config_dict['models'][0]['model_id'] == imported_model.id
    assert imported_algorithm.ext_config['model_ids'] == [imported_model.id]
    assert imported_algorithm.id != old_algorithm_id or imported_model.id != old_model_id
    assert os.path.exists(imported_model.file_path)
    assert template_transfer.artifact_sha256(imported_model.file_path) == manifest['dependencies']['models'][0]['artifact_sha256']

    repeated = template_transfer.import_package(package_path, {}, username='admin')
    assert repeated['already_imported'] is True
    assert repeated['workflow_id'] == imported.id

    imported_algorithm.description = 'target-only dependency change'
    imported_algorithm.save(only=[Algorithm.description])
    dependency_conflict = template_transfer.preflight_manifest(manifest, {})
    assert dependency_conflict['dependencies']['algorithms'][0]['status'] == 'conflict'
    assert dependency_conflict['template']['status'] == 'conflict'
    imported_algorithm.description = 'portable algorithm'
    imported_algorithm.save(only=[Algorithm.description])

    changed_graph = imported.data_dict
    changed_graph['local_note'] = 'target-only change'
    imported.workflow_data = json.dumps(changed_graph, ensure_ascii=False)
    imported.save(only=[Workflow.workflow_data])
    conflict = template_transfer.preflight_manifest(manifest, {})
    assert conflict['template']['status'] == 'conflict'
    resolutions = {'template': {'action': 'rename', 'name': '人员告警模板（再次导入）'}}
    assert template_transfer.preflight_manifest(manifest, resolutions)['ready'] is True
    forked_result = template_transfer.import_package(package_path, resolutions, username='admin')
    forked = Workflow.get_by_id(forked_result['workflow_id'])
    assert forked.id != imported.id
    assert forked.portable_id != imported.portable_id


def test_preflight_rejects_different_device_model(transfer_env, tmp_path, monkeypatch):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'model.onnx'
    source_model.write_bytes(b'model')
    template, _algorithm, _model = _create_template_resources(script_root, source_model)
    package_path, _filename = template_transfer.build_export_package(template, include_models=False)
    manifest = _manifest_from(package_path)

    monkeypatch.setattr(template_transfer, 'DEVICE_MODEL_CODE', 'VB-OTHER-BOX')
    with pytest.raises(template_transfer.TemplateTransferError) as error:
        template_transfer.preflight_manifest(manifest, {})
    assert error.value.code == 'device_model_mismatch'


def test_config_only_package_requires_model_mapping(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'model.onnx'
    source_model.write_bytes(b'model-without-bundle')
    template, algorithm, model = _create_template_resources(script_root, source_model)
    package_path, _filename = template_transfer.build_export_package(template, include_models=False)
    manifest = _manifest_from(package_path)

    template.delete_instance()
    algorithm.delete_instance()
    model.delete_instance()

    candidate = MLModel.create(
        name='同文件不同元数据模型',
        filename='same-content.onnx',
        file_path=str(source_model),
        file_size=source_model.stat().st_size,
        model_type='ONNX',
        framework='onnx',
        version='v1.0',
        classes=json.dumps({'0': 'vehicle'}),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        uploaded_by='admin',
    )

    preview = template_transfer.preflight_manifest(manifest, {})
    assert preview['ready'] is False
    assert preview['dependencies']['models'][0]['status'] == 'missing'
    assert preview['blockers'][0]['resource'] == 'model'

    candidate.classes = json.dumps({'0': 'person'})
    candidate.save(only=[MLModel.classes])
    matched = template_transfer.preflight_manifest(manifest, {})
    assert matched['ready'] is True
    assert matched['dependencies']['models'][0]['status'] == 'reuse_by_hash'


def test_preflight_does_not_reuse_disabled_model(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'enabled-source.onnx'
    source_model.write_bytes(b'same-artifact')
    template, algorithm, model = _create_template_resources(script_root, source_model)
    package_path, _filename = template_transfer.build_export_package(
        template,
        include_models=False,
    )
    manifest = _manifest_from(package_path)
    template.delete_instance()
    algorithm.delete_instance()
    model.delete_instance()

    candidate = MLModel.create(
        name='禁用的同内容模型',
        filename='enabled-source.onnx',
        file_path=str(source_model),
        file_size=source_model.stat().st_size,
        model_type='ONNX',
        framework='onnx',
        version='v1.0',
        classes=json.dumps({'0': 'person'}),
        enabled=False,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        uploaded_by='admin',
    )

    disabled_preview = template_transfer.preflight_manifest(manifest, {})
    assert disabled_preview['ready'] is False
    assert disabled_preview['dependencies']['models'][0]['status'] == 'missing'

    candidate.enabled = True
    candidate.save(only=[MLModel.enabled])
    enabled_preview = template_transfer.preflight_manifest(manifest, {})
    assert enabled_preview['ready'] is True
    assert enabled_preview['dependencies']['models'][0]['status'] == 'reuse_by_hash'


def test_node_model_override_is_packaged_and_restored(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'base.onnx'
    source_model.write_bytes(b'base-model')
    override_path = tmp_path / 'override.onnx'
    override_path.write_bytes(b'override-model')
    template, algorithm, base_model = _create_template_resources(script_root, source_model)
    override_model = MLModel.create(
        name='节点覆盖模型',
        filename='override.onnx',
        file_path=str(override_path),
        file_size=override_path.stat().st_size,
        model_type='ONNX',
        framework='onnx',
        version='v2.0',
        classes=json.dumps({'0': 'vehicle'}),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        uploaded_by='admin',
    )
    graph = template.data_dict
    graph['nodes'][1]['config']['models'] = [{
        'model_id': override_model.id,
        'name': override_model.name,
    }]
    template.workflow_data = json.dumps(graph, ensure_ascii=False)
    template.save(only=[Workflow.workflow_data])

    package_path, _filename = template_transfer.build_export_package(
        template,
        include_models=True,
    )
    manifest = _manifest_from(package_path)
    assert len(manifest['dependencies']['models']) == 2
    with zipfile.ZipFile(package_path) as archive:
        portable_graph = json.loads(archive.read('workflow.json'))
    portable_override = portable_graph['nodes'][1]['config']['models'][0]
    assert portable_override['$model'] == override_model.portable_id
    assert portable_override['model_id']['$model'] == override_model.portable_id

    template.delete_instance()
    algorithm.delete_instance()
    base_model.delete_instance()
    override_model.delete_instance()
    result = template_transfer.import_package(package_path, {}, username='admin')
    imported = Workflow.get_by_id(result['workflow_id'])
    mapped_override_id = result['mapping']['models'][portable_override['$model']]
    restored_override = imported.data_dict['nodes'][1]['config']['models'][0]
    assert restored_override['model_id'] == mapped_override_id
    assert restored_override['name'] == MLModel.get_by_id(mapped_override_id).name


def test_raw_integer_model_resolution_imports_without_500(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'integer-map.onnx'
    source_model.write_bytes(b'integer-mapping')
    template, algorithm, model = _create_template_resources(script_root, source_model)
    package_path, _filename = template_transfer.build_export_package(
        template,
        include_models=False,
    )
    manifest = _manifest_from(package_path)
    model_portable_id = manifest['dependencies']['models'][0]['portable_id']
    template.delete_instance()
    algorithm.delete_instance()
    model.delete_instance()

    target_model = MLModel.create(
        name='整数映射目标模型',
        filename='integer-map.onnx',
        file_path=str(source_model),
        file_size=source_model.stat().st_size,
        model_type='ONNX',
        framework='onnx',
        version='v1.0',
        classes=json.dumps({'0': 'person'}),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        uploaded_by='admin',
    )
    resolutions = {'models': {model_portable_id: target_model.id}}
    assert template_transfer.preflight_manifest(manifest, resolutions)['ready'] is True

    result = template_transfer.import_package(
        package_path,
        resolutions,
        username='admin',
    )
    assert result['mapping']['models'][model_portable_id] == target_model.id


def test_raw_integer_algorithm_resolution_imports_without_500(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'algorithm-map.onnx'
    source_model.write_bytes(b'algorithm-mapping')
    template, algorithm, model = _create_template_resources(script_root, source_model)
    package_path, _filename = template_transfer.build_export_package(
        template,
        include_models=False,
    )
    manifest = _manifest_from(package_path)
    model_portable_id = manifest['dependencies']['models'][0]['portable_id']
    algorithm_portable_id = manifest['dependencies']['algorithms'][0]['portable_id']
    template.delete_instance()
    algorithm.delete_instance()
    model.delete_instance()

    target_model = MLModel.create(
        name='算法映射目标模型',
        filename='algorithm-map.onnx',
        file_path=str(source_model),
        file_size=source_model.stat().st_size,
        model_type='ONNX',
        framework='onnx',
        version='v1.0',
        classes=json.dumps({'0': 'person'}),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        uploaded_by='admin',
    )
    target_algorithm = Algorithm.create(
        name='算法映射目标算法',
        script_path='detector.py',
        script_config='{}',
        ext_config_json='{}',
        created_at=datetime.now(),
        updated_at=datetime.now(),
        created_by='admin',
    )
    resolutions = {
        'models': {model_portable_id: target_model.id},
        'algorithms': {algorithm_portable_id: target_algorithm.id},
    }
    assert template_transfer.preflight_manifest(manifest, resolutions)['ready'] is True

    result = template_transfer.import_package(package_path, resolutions, username='admin')
    imported = Workflow.get_by_id(result['workflow_id'])
    assert result['mapping']['algorithms'][algorithm_portable_id] == target_algorithm.id
    assert imported.data_dict['nodes'][1]['dataId'] == target_algorithm.id


def test_raw_integer_external_api_and_hook_resolutions_import(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'resource-map.onnx'
    source_model.write_bytes(b'resource-mapping')
    template, algorithm, model = _create_template_resources(script_root, source_model)
    now = datetime.now()
    external_api = ExternalApi.create(
        name='源外部 API',
        endpoint_url='https://source.example.com/detect',
        created_at=now,
        updated_at=now,
        created_by='admin',
    )
    (script_root / 'source_hook.py').write_text(
        'def execute(context):\n    return context\n',
        encoding='utf-8',
    )
    hook = Hook.create(
        name='源 Hook',
        hook_point='pre_alert',
        script_path='source_hook.py',
        entry_function='execute',
        created_at=now,
    )
    AlgorithmHook.create(algorithm=algorithm, hook=hook)
    graph = template.data_dict
    graph['nodes'].append({
        'id': 'external-node',
        'type': 'externalApi',
        'dataId': external_api.id,
        'externalApiId': external_api.id,
    })
    template.workflow_data = json.dumps(graph, ensure_ascii=False)
    template.save(only=[Workflow.workflow_data])
    package_path, _filename = template_transfer.build_export_package(
        template,
        include_models=False,
    )
    manifest = _manifest_from(package_path)
    model_portable_id = manifest['dependencies']['models'][0]['portable_id']
    external_portable_id = manifest['dependencies']['external_apis'][0]['portable_id']
    hook_portable_id = manifest['dependencies']['algorithms'][0]['hooks'][0]['portable_id']
    template.delete_instance()
    algorithm.delete_instance()
    model.delete_instance()
    external_api.delete_instance()
    hook.delete_instance()

    target_model = MLModel.create(
        name='资源映射目标模型',
        filename='resource-map.onnx',
        file_path=str(source_model),
        file_size=source_model.stat().st_size,
        model_type='ONNX',
        framework='onnx',
        version='v1.0',
        classes=json.dumps({'0': 'person'}),
        created_at=now,
        updated_at=now,
        uploaded_by='admin',
    )
    target_external = ExternalApi.create(
        name='目标外部 API',
        endpoint_url='https://target.example.com/detect',
        created_at=now,
        updated_at=now,
        created_by='admin',
    )
    (script_root / 'target_hook.py').write_text(
        'def execute(context):\n    return context\n',
        encoding='utf-8',
    )
    target_hook = Hook.create(
        name='目标 Hook',
        hook_point='pre_alert',
        script_path='target_hook.py',
        entry_function='execute',
        created_at=now,
    )
    resolutions = {
        'models': {model_portable_id: target_model.id},
        'external_apis': {external_portable_id: target_external.id},
        'hooks': {hook_portable_id: target_hook.id},
    }
    assert template_transfer.preflight_manifest(manifest, resolutions)['ready'] is True

    result = template_transfer.import_package(package_path, resolutions, username='admin')
    imported = Workflow.get_by_id(result['workflow_id'])
    imported_algorithm = Algorithm.get_by_id(result['created']['algorithms'][0])
    relation = AlgorithmHook.get(AlgorithmHook.algorithm == imported_algorithm)
    external_node = next(
        node for node in imported.data_dict['nodes'] if node['id'] == 'external-node'
    )
    assert external_node['dataId'] == target_external.id
    assert relation.hook_id == target_hook.id


def test_import_rejects_modified_declared_entry(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'model.onnx'
    source_model.write_bytes(b'model')
    template, _algorithm, _model = _create_template_resources(script_root, source_model)
    package_path, _filename = template_transfer.build_export_package(template, include_models=True)

    modified_path = tmp_path / 'modified-package.zip'
    with zipfile.ZipFile(package_path) as source, zipfile.ZipFile(modified_path, 'w') as output:
        for info in source.infolist():
            content = b'{"nodes": []}' if info.filename == 'workflow.json' else source.read(info)
            output.writestr(info, content)

    with pytest.raises(template_transfer.TemplateTransferError) as error:
        template_transfer.import_package(str(modified_path), {}, username='admin')
    assert error.value.code == 'package_checksum_mismatch'


def test_export_redacts_credentials_and_import_requires_reentry(transfer_env):
    _script_root, _model_root = transfer_env
    now = datetime.now()
    external_api = ExternalApi.create(
        name='园区识别服务',
        endpoint_url=(
            'https://service-user:raw-url-password@api.example.com/detect'
            '?api_key=raw-query-token&region=cn'
        ),
        headers_json=json.dumps({'Authorization': 'raw-external-token', 'X-Trace': 'kept'}),
        created_at=now,
        updated_at=now,
        created_by='admin',
    )
    graph = {
        'nodes': [
            {
                'id': 'template-video-source',
                'type': 'source',
                'name': '视频源（复制时绑定）',
                'dataId': None,
                'videoSourceId': None,
            },
            {
                'id': 'external-node',
                'type': 'externalApi',
                'dataId': external_api.id,
                'externalApiId': external_api.id,
            },
            {'id': 'alert-node', 'type': 'alert', 'name': '告警'},
            {
                'id': 'webhook-node',
                'type': 'webhook',
                'name': '通知中心',
                'config': {
                    'provider': 'generic',
                    'endpoint_url': 'https://oapi.dingtalk.com/robot/send?token=raw-webhook-token',
                    'headers': [{'name': 'Authorization', 'value': 'raw-webhook-header', 'sensitive': True}],
                },
            },
        ],
        'connections': [{
            'id': 'alert-webhook',
            'from': 'alert-node',
            'to': 'webhook-node',
        }],
    }
    template = Workflow.create(
        name='带凭据模板',
        workflow_data=json.dumps(graph),
        is_template=True,
        is_active=False,
        created_at=now,
        updated_at=now,
        created_by='admin',
    )

    package_path, _filename = template_transfer.build_export_package(template, include_models=False)
    package_bytes = Path(package_path).read_bytes()
    assert b'raw-external-token' not in package_bytes
    assert b'raw-url-password' not in package_bytes
    assert b'raw-query-token' not in package_bytes
    assert b'raw-webhook-token' not in package_bytes
    assert b'raw-webhook-header' not in package_bytes
    manifest = _manifest_from(package_path)
    required = {item['key'] for item in manifest['required_inputs']}
    external_portable_id = manifest['dependencies']['external_apis'][0]['portable_id']
    external_key = f'external_api.{external_portable_id}.header.authorization'
    external_endpoint_key = f'external_api.{external_portable_id}.endpoint_url'
    assert external_key in required
    assert external_endpoint_key in required
    assert 'workflow.webhook-node.endpoint_url' in required
    assert 'workflow.webhook-node.header.authorization' in required

    template.delete_instance()
    external_api.delete_instance()
    preview = template_transfer.preflight_manifest(manifest, {})
    assert preview['ready'] is False
    secrets = {
        external_key: 'target-external-token',
        external_endpoint_key: 'https://api.example.com/detect?region=cn',
        'workflow.webhook-node.endpoint_url': 'https://oapi.dingtalk.com/robot/send',
        'workflow.webhook-node.header.authorization': 'target-webhook-header',
    }
    resolutions = {'secrets': secrets}
    assert template_transfer.preflight_manifest(manifest, resolutions)['ready'] is True

    result = template_transfer.import_package(package_path, resolutions, username='admin')
    imported = Workflow.get_by_id(result['workflow_id'])
    imported_external = ExternalApi.get_by_id(result['created']['external_apis'][0])
    assert imported_external.headers['Authorization'] == 'target-external-token'
    assert imported_external.endpoint_url == 'https://api.example.com/detect?region=cn'
    webhook = next(node for node in imported.data_dict['nodes'] if node['id'] == 'webhook-node')
    assert webhook['config']['endpoint_url'] == 'https://oapi.dingtalk.com/robot/send'
    assert webhook['config']['headers'][0]['value'] == 'target-webhook-header'


def test_ocr_preflight_checks_packaged_model_backend(transfer_env, tmp_path, monkeypatch):
    _script_root, _model_root = transfer_env
    detection_path = tmp_path / 'ocr-det.rknn'
    recognition_path = tmp_path / 'ocr-rec.rknn'
    detection_path.write_bytes(b'rknn-detection')
    recognition_path.write_bytes(b'rknn-recognition')
    now = datetime.now()
    detection_model = MLModel.create(
        name='RKNN OCR 检测',
        filename=detection_path.name,
        file_path=str(detection_path),
        file_size=detection_path.stat().st_size,
        model_type='OCR',
        model_role='detection',
        framework='rknn',
        version='v1.0',
        created_at=now,
        updated_at=now,
        uploaded_by='admin',
    )
    recognition_model = MLModel.create(
        name='RKNN OCR 识别',
        filename=recognition_path.name,
        file_path=str(recognition_path),
        file_size=recognition_path.stat().st_size,
        model_type='OCR',
        model_role='recognition',
        framework='rknn',
        version='v1.0',
        created_at=now,
        updated_at=now,
        uploaded_by='admin',
    )
    algorithm = Algorithm.create(
        name='RKNN OCR 算法',
        script_path='',
        script_config='{}',
        ext_config_json=json.dumps({
            'algorithm_type': 'ocr',
            'ocr_config': {
                'detection_model_id': detection_model.id,
                'recognition_model_id': recognition_model.id,
            },
        }),
        created_at=now,
        updated_at=now,
        created_by='admin',
    )
    template = Workflow.create(
        name='RKNN OCR 模板',
        workflow_data=json.dumps({
            'nodes': [
                {
                    'id': 'template-video-source',
                    'type': 'source',
                    'name': '视频源（复制时绑定）',
                    'dataId': None,
                    'videoSourceId': None,
                },
                {
                    'id': 'ocr-node',
                    'type': 'algorithm',
                    'dataId': algorithm.id,
                    'algorithmId': algorithm.id,
                },
            ],
            'connections': [{
                'id': 'source-ocr',
                'from': 'template-video-source',
                'to': 'ocr-node',
            }],
        }),
        is_template=True,
        is_active=False,
        created_at=now,
        updated_at=now,
        created_by='admin',
    )
    package_path, _filename = template_transfer.build_export_package(
        template,
        include_models=True,
    )
    manifest = _manifest_from(package_path)
    assert manifest['dependencies']['algorithms'][0]['ocr_backend'] == 'rknn_ocr'
    template.delete_instance()
    algorithm.delete_instance()
    detection_model.delete_instance()
    recognition_model.delete_instance()

    checked_backends = []

    def only_paddle_available(required_backend=None):
        checked_backends.append(required_backend)
        return required_backend == 'paddleocr'

    monkeypatch.setattr(
        template_transfer,
        'is_ocr_runtime_available',
        only_paddle_available,
    )
    preview = template_transfer.preflight_manifest(manifest, {})
    assert checked_backends == ['rknn_ocr']
    assert preview['ready'] is False
    assert preview['dependencies']['algorithms'][0]['status'] == 'unsupported'


def test_hook_conflict_can_fork_without_overwriting_existing_script(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'hook-model.onnx'
    source_model.write_bytes(b'hook-model')
    template, algorithm, model = _create_template_resources(script_root, source_model)
    hook_script = script_root / 'hook.py'
    hook_script.write_text(
        'def execute(context):\n    return context\n',
        encoding='utf-8',
    )
    hook = Hook.create(
        name='告警增强 Hook',
        hook_point='pre_alert',
        script_path='hook.py',
        entry_function='execute',
        priority=20,
        condition_json='{}',
        enabled=True,
        created_at=datetime.now(),
    )
    AlgorithmHook.create(
        algorithm=algorithm,
        hook=hook,
        enabled=True,
        hook_config='{}',
    )

    package_path, _filename = template_transfer.build_export_package(template, include_models=True)
    manifest = _manifest_from(package_path)
    hook_portable_id = manifest['dependencies']['algorithms'][0]['hooks'][0]['portable_id']

    template.delete_instance()
    algorithm.delete_instance()
    model.delete_instance()
    hook_script.write_text(
        'def execute(context):\n    return {"changed": True}\n',
        encoding='utf-8',
    )
    resolutions = {
        'hooks': {
            hook_portable_id: {'action': 'rename', 'name': '告警增强 Hook（导入）'},
        },
    }
    preview = template_transfer.preflight_manifest(manifest, resolutions)
    assert preview['ready'] is True
    assert preview['dependencies']['hooks'][0]['status'] == 'import'

    result = template_transfer.import_package(package_path, resolutions, username='admin')
    imported_hook = Hook.get_by_id(result['created']['hooks'][0])
    imported_algorithm = Algorithm.get_by_id(result['created']['algorithms'][0])
    relation = AlgorithmHook.get(AlgorithmHook.algorithm == imported_algorithm)
    assert imported_hook.portable_id != hook.portable_id
    assert relation.hook_id == imported_hook.id
    assert hook_script.read_text(encoding='utf-8').endswith('{"changed": True}\n')
    assert Path(template_transfer.get_script_loader().resolve_path(imported_hook.script_path)).read_text(
        encoding='utf-8'
    ).endswith('return context\n')


def test_failed_import_rolls_back_database_and_created_files(transfer_env, tmp_path):
    script_root, model_root = transfer_env
    source_model = tmp_path / 'rollback-model.onnx'
    source_model.write_bytes(b'rollback-model')
    template, algorithm, model = _create_template_resources(script_root, source_model)
    package_path, _filename = template_transfer.build_export_package(template, include_models=True)

    invalid_graph = {'nodes': [], 'connections': []}
    invalid_graph_bytes = template_transfer._canonical_json(invalid_graph)
    invalid_package = tmp_path / 'invalid-graph-package.zip'
    with zipfile.ZipFile(package_path) as source:
        manifest = json.loads(source.read('manifest.json'))
        dependencies = manifest['dependencies']
        manifest['template']['fingerprint'] = template_transfer._template_content_fingerprint(
            invalid_graph,
            dependencies['algorithms'],
            dependencies['external_apis'],
            dependencies['models'],
        )
        workflow_entry = next(item for item in manifest['entries'] if item['path'] == 'workflow.json')
        workflow_entry['size'] = len(invalid_graph_bytes)
        workflow_entry['sha256'] = template_transfer._sha256_bytes(invalid_graph_bytes)
        with zipfile.ZipFile(invalid_package, 'w', allowZip64=True) as output:
            output.writestr(
                'manifest.json',
                template_transfer._canonical_json(manifest),
                compress_type=zipfile.ZIP_STORED,
            )
            for info in source.infolist()[1:]:
                content = invalid_graph_bytes if info.filename == 'workflow.json' else source.read(info)
                output.writestr(info.filename, content, compress_type=info.compress_type)

    template.delete_instance()
    algorithm.delete_instance()
    model.delete_instance()
    with pytest.raises(template_transfer.TemplateTransferError) as error:
        template_transfer.import_package(str(invalid_package), {}, username='admin')
    assert error.value.code == 'invalid_template'
    assert Workflow.select().count() == 0
    assert Algorithm.select().count() == 0
    assert MLModel.select().count() == 0
    assert not any(path.is_file() for path in model_root.rglob('*'))
    assert not any(path.is_file() for path in (script_root / 'imports').rglob('*'))


def test_transfer_api_is_admin_only_and_returns_model_mismatch(transfer_env, tmp_path):
    script_root, _model_root = transfer_env
    source_model = tmp_path / 'api-model.onnx'
    source_model.write_bytes(b'api-model')
    template, _algorithm, _model = _create_template_resources(script_root, source_model)
    package_path, _filename = template_transfer.build_export_package(template, include_models=False)
    manifest = _manifest_from(package_path)

    admin = User.create(
        username='transfer-admin',
        password_hash='unused',
        role='admin',
        created_at=datetime.now(),
    )
    operator = User.create(
        username='transfer-operator',
        password_hash='unused',
        role='user',
        created_at=datetime.now(),
    )
    app = Flask(__name__)
    app.config['TESTING'] = True
    template_transfer_api.register_template_transfer_api(app)
    client = app.test_client()
    admin_headers = {
        'Authorization': f'Bearer {generate_token(admin.id, admin.username, admin.role)}',
    }
    operator_headers = {
        'Authorization': f'Bearer {generate_token(operator.id, operator.username, operator.role)}',
    }

    assert client.get(
        '/api/workflow-template-transfers/capabilities', headers=operator_headers
    ).status_code == 403
    capabilities = client.get(
        '/api/workflow-template-transfers/capabilities', headers=admin_headers
    )
    assert capabilities.status_code == 200
    assert capabilities.get_json()['device_model_code'] == 'VB-TEST-BOX'

    exported = client.post(
        f'/api/workflow-templates/{template.id}/export',
        headers=admin_headers,
        json={'include_models': False},
    )
    assert exported.status_code == 200
    with zipfile.ZipFile(BytesIO(exported.data)) as archive:
        assert archive.infolist()[0].filename == 'manifest.json'

    # 迁移导入路由必须覆盖应用的普通上传上限，否则大模型包会在进入处理器前被拒绝。
    app.config['MAX_CONTENT_LENGTH'] = 1
    imported = client.post(
        '/api/workflow-template-imports',
        headers=admin_headers,
        data={
            'file': (BytesIO(exported.data), 'template.vbt.zip'),
            'resolutions': '{}',
        },
        content_type='multipart/form-data',
    )
    assert imported.status_code == 200
    assert imported.get_json()['already_imported'] is True

    app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024
    manifest['source']['device_model_code'] = 'VB-OTHER-BOX'
    mismatch = client.post(
        '/api/workflow-template-imports/preflight',
        headers=admin_headers,
        json={'manifest': manifest, 'resolutions': {}},
    )
    assert mismatch.status_code == 409
    assert mismatch.get_json()['code'] == 'device_model_mismatch'
