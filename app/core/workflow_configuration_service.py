"""Shared validation, optimistic locking and atomic batch persistence."""

import json
from datetime import datetime

from app.core.database_models import Workflow
from app.core.workflow_batch_config import BatchConfigValidationError, apply_batch_node_changes
from app.core.workflow_configuration import (
    ConfigurationError, dry_run_value, positive_integer, validate_fields,
)
from app.core.workflow_validation import validate_workflow_configuration


class WorkflowAccessDenied(Exception):
    def __init__(self, response):
        self.response = response


def runtime_workflow(workflow_id):
    workflow = Workflow.get_or_none(Workflow.id == workflow_id)
    if workflow is None:
        raise ConfigurationError('workflow_not_found', '编排不存在', 404,
                                 [{'workflow_id': workflow_id}])
    if workflow.is_template:
        raise ConfigurationError('workflow_template_not_configurable', '编排模板不可通过运行配置接口修改',
                                 details=[{'workflow_id': workflow_id}])
    return workflow


def check_version(workflow, expected):
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise ConfigurationError('invalid_field', 'expected_version 必须是非负整数')
    if workflow.config_version != expected:
        raise ConfigurationError('version_conflict', '配置已发生变化，请刷新后重试', 409,
                                 [{'workflow_id': workflow.id, 'config_version': workflow.config_version}])


def validate_configuration(graph, workflow_id, validator=validate_workflow_configuration):
    valid, error = validator(graph)
    if not valid:
        raise ConfigurationError('invalid_field', error, details=[{'workflow_id': workflow_id}])


def save_configurations(pending, versions, database):
    """All graphs commit together; a late version conflict rolls everything back."""
    with database.atomic():
        for workflow_id in sorted(pending):
            count = (
                Workflow.update(
                    workflow_data=json.dumps(pending[workflow_id], allow_nan=False),
                    config_version=Workflow.config_version + 1,
                    updated_at=datetime.now(),
                )
                .where((Workflow.id == workflow_id)
                       & (Workflow.config_version == versions[workflow_id])
                       & (Workflow.is_template == False))
                .execute()
            )
            if count != 1:
                raise ConfigurationError('version_conflict', '配置已发生变化，请刷新后重试', 409,
                                         [{'workflow_id': workflow_id}])
    return {str(key): versions[key] + 1 for key in sorted(pending)}


def configure_batch(data, database, *, authorize=None, patcher=apply_batch_node_changes,
                    validator=validate_workflow_configuration):
    validate_fields(data, {'workflow_ids', 'expected_versions', 'targets', 'dry_run'},
                    {'workflow_ids', 'expected_versions', 'targets'})
    workflow_ids = data['workflow_ids']
    targets = data['targets']
    raw_versions = data['expected_versions']
    dry_run = dry_run_value(data)
    if not isinstance(workflow_ids, list) or not workflow_ids:
        raise ConfigurationError('invalid_field', '请选择要配置的编排')
    for workflow_id in workflow_ids:
        positive_integer(workflow_id, 'workflow_ids')
    if len(set(workflow_ids)) != len(workflow_ids):
        raise ConfigurationError('invalid_field', 'workflow_ids 包含重复项')
    if not isinstance(targets, list) or not targets:
        raise ConfigurationError('invalid_field', '至少选择一个要应用的参数')
    if not isinstance(raw_versions, dict):
        raise ConfigurationError('invalid_field', 'expected_versions 必须是对象')
    if set(raw_versions) != {str(key) for key in workflow_ids}:
        raise ConfigurationError('invalid_field', 'expected_versions 必须与 workflow_ids 一一对应')
    workflows = {}
    versions = {}
    pending = {}
    failures = []
    for workflow_id in workflow_ids:
        try:
            workflow = runtime_workflow(workflow_id)
            if authorize:
                response = authorize(workflow)
                if response:
                    raise WorkflowAccessDenied(response)
            expected = raw_versions[str(workflow_id)]
            check_version(workflow, expected)
            workflows[workflow_id] = workflow
            versions[workflow_id] = expected
            pending[workflow_id] = workflow.data_dict
        except ConfigurationError as exc:
            failures.append({'workflow_id': workflow_id, 'code': exc.code, 'error': str(exc)})
    changes = []
    seen = set()
    for target in targets:
        validate_fields(target, {'workflow_ids', 'node_id', 'node_type', 'changes'},
                        {'workflow_ids', 'node_id', 'node_type', 'changes'})
        ids = target['workflow_ids']
        node_id = target['node_id']
        if not isinstance(ids, list) or not ids or not isinstance(node_id, str) or not node_id:
            raise ConfigurationError('invalid_field', '目标节点缺少有效的 workflow_ids 或 node_id')
        if not isinstance(target['node_type'], str):
            raise ConfigurationError('invalid_field', 'node_type 必须是字符串')
        for workflow_id in ids:
            positive_integer(workflow_id, 'targets.workflow_ids')
            if workflow_id not in workflow_ids:
                raise ConfigurationError('invalid_field', '目标不在已选编排中')
            key = (workflow_id, node_id)
            if key in seen:
                raise ConfigurationError('invalid_field', f'节点 {node_id} 被重复配置')
            seen.add(key)
            if workflow_id not in pending:
                continue
            try:
                graph, fields, name = patcher(
                    pending[workflow_id], node_id=node_id, node_type=target['node_type'],
                    changes=target['changes'],
                )
                pending[workflow_id] = graph
                changes.append({
                    'workflow_id': workflow_id, 'workflow_name': workflows[workflow_id].name,
                    'node_id': node_id, 'node_name': name, 'node_type': target['node_type'],
                    'fields': fields, 'is_active': workflows[workflow_id].is_active,
                })
            except BatchConfigValidationError as exc:
                failures.append({'workflow_id': workflow_id, 'node_id': node_id,
                                 'code': 'invalid_field', 'error': str(exc),
                                 'fields': sorted(target['changes']) if isinstance(target['changes'], dict) else []})
    targeted = {change['workflow_id'] for change in changes}
    for workflow_id in workflows:
        if workflow_id not in targeted:
            failures.append({'workflow_id': workflow_id, 'code': 'invalid_field',
                             'error': '没有匹配到可配置节点'})
            continue
        valid, error = validator(pending[workflow_id])
        if not valid:
            failures.append({'workflow_id': workflow_id, 'code': 'invalid_field', 'error': error})
    if failures:
        codes = {failure['code'] for failure in failures}
        code, status = ('version_conflict', 409) if 'version_conflict' in codes else (
            ('workflow_not_found', 404) if 'workflow_not_found' in codes else ('invalid_field', 400)
        )
        raise ConfigurationError(code, '批量配置预检失败', status, failures)
    summary = {
        'workflow_count': len(targeted),
        'active_count': sum(workflows[key].is_active for key in targeted),
        'node_change_count': len(changes),
    }
    result_versions = {str(key): versions[key] for key in sorted(versions)}
    if not dry_run:
        result_versions = save_configurations(pending, versions, database)
    return {
        'dry_run': dry_run, 'summary': summary, 'changes': changes,
        'config_versions': result_versions,
        'message': '预检通过' if dry_run else f"已更新 {summary['workflow_count']} 个编排",
    }
