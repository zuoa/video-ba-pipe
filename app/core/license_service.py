"""Offline license validation and effective entitlement calculation."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

import jwt
import peewee as pw

from app.config import LICENSE_PUBLIC_KEY_PATH
from app.core.database_models import Algorithm, SystemSetting, VideoSource, db
from app.core.node_identity import get_node_id


LICENSE_TOKEN_KEY = 'license.token'
LICENSE_CLOCK_KEY = 'license.highest_utc'
LICENSE_LOCK_KEY = 'license.quota_lock'
LICENSE_SCHEMA_VERSION = 1
LICENSE_KEY_ID = 'vendor-v1'
FREE_LIMITS = {'video_sources': 1, 'algorithms': 3}
CLOCK_ROLLBACK_TOLERANCE_SECONDS = 300
MAX_LICENSE_BYTES = 64 * 1024
_RESOURCE_MODELS = {
    'video_sources': VideoSource,
    'algorithms': Algorithm,
}


class LicenseError(ValueError):
    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {'code': self.code, 'error': self.message, **self.details}


@dataclass(frozen=True)
class LicenseEvaluation:
    tier: str
    license_status: str
    license_message: str
    limits: Dict[str, int]
    claims: Optional[Dict[str, Any]] = None

    @property
    def paid(self) -> bool:
        return self.tier == 'licensed'


def _setting_value(key: str) -> str:
    setting = SystemSetting.get_or_none(SystemSetting.key == key)
    return setting.value if setting else ''


def _write_transaction():
    if isinstance(db, pw.SqliteDatabase) and db.transaction_depth() == 0:
        return db.atomic('IMMEDIATE')
    return db.atomic()


def _locked_setting(key: str) -> SystemSetting:
    query = SystemSetting.select().where(SystemSetting.key == key)
    if not isinstance(db, pw.SqliteDatabase):
        query = query.for_update()
    return query.get()


def _read_public_key() -> str:
    try:
        with open(LICENSE_PUBLIC_KEY_PATH, 'r', encoding='utf-8') as key_file:
            public_key = key_file.read().strip()
    except OSError as exc:
        raise LicenseError(
            'license_public_key_unavailable',
            f'无法读取许可证验签公钥: {exc}',
        ) from exc
    if 'BEGIN PUBLIC KEY' not in public_key:
        raise LicenseError(
            'license_public_key_unavailable',
            '许可证验签公钥尚未配置',
        )
    return public_key


def _numeric_date(claims: dict, name: str) -> float:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LicenseError('license_claims_invalid', f'许可证字段 {name} 必须为 UTC NumericDate')
    return float(value)


def decode_and_validate_token(
    token: str,
    *,
    now: Optional[float] = None,
    node_id: Optional[str] = None,
    public_key: Optional[str] = None,
    check_clock: bool = True,
) -> dict:
    token = (token or '').strip()
    if not token or len(token.encode('utf-8')) > MAX_LICENSE_BYTES:
        raise LicenseError('license_file_invalid', '许可证文件为空或超过 64 KiB')

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise LicenseError('license_signature_invalid', '许可证格式或签名无效') from exc
    if header.get('alg') != 'EdDSA' or header.get('kid') != LICENSE_KEY_ID:
        raise LicenseError('license_signature_invalid', '许可证签名算法或密钥编号不受支持')

    verification_key = public_key or _read_public_key()
    try:
        claims = jwt.decode(
            token,
            verification_key,
            algorithms=['EdDSA'],
            options={
                'require': ['license_id', 'customer', 'node_id', 'iat', 'nbf', 'exp', 'limits'],
                'verify_exp': False,
                'verify_nbf': False,
                'verify_iat': False,
            },
        )
    except jwt.exceptions.InvalidKeyError as exc:
        raise LicenseError(
            'license_public_key_invalid',
            '许可证验签公钥无效或不是 Ed25519 公钥',
        ) from exc
    except (ValueError, TypeError) as exc:
        raise LicenseError(
            'license_public_key_invalid',
            '许可证验签公钥无法解析',
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise LicenseError('license_signature_invalid', '许可证格式或签名无效') from exc

    if claims.get('schema_version') != LICENSE_SCHEMA_VERSION:
        raise LicenseError('license_claims_invalid', '不支持的许可证 schema_version')
    for name in ('license_id', 'customer', 'node_id'):
        if not isinstance(claims.get(name), str) or not claims[name].strip():
            raise LicenseError('license_claims_invalid', f'许可证字段 {name} 不能为空')

    expected_node_id = node_id or get_node_id()
    licensed_node_id = claims['node_id']
    if licensed_node_id != '*' and licensed_node_id != expected_node_id:
        raise LicenseError(
            'license_node_mismatch',
            '许可证与当前节点不匹配',
            {'expected_node_id': expected_node_id},
        )

    issued_at = _numeric_date(claims, 'iat')
    not_before = _numeric_date(claims, 'nbf')
    expires_at = _numeric_date(claims, 'exp')
    if not (issued_at <= expires_at and not_before < expires_at):
        raise LicenseError('license_claims_invalid', '许可证时间范围无效')

    current_time = time.time() if now is None else float(now)
    if current_time < not_before:
        raise LicenseError('license_not_yet_valid', '许可证尚未生效')
    if current_time >= expires_at:
        raise LicenseError('license_expired', '许可证已过期')
    if check_clock and _clock_rolled_back(current_time):
        raise LicenseError('license_clock_rollback', '检测到系统时间明显回拨，付费授权已暂停')

    raw_limits = claims.get('limits')
    if not isinstance(raw_limits, dict):
        raise LicenseError('license_claims_invalid', '许可证 limits 必须为对象')
    limits: Dict[str, int] = {}
    for resource, free_limit in FREE_LIMITS.items():
        value = raw_limits.get(resource)
        if isinstance(value, bool) or not isinstance(value, int) or value < free_limit:
            raise LicenseError(
                'license_claims_invalid',
                f'许可证额度 {resource} 必须是不小于 {free_limit} 的整数',
            )
        limits[resource] = value
    claims = dict(claims)
    claims['limits'] = limits
    return claims


def _clock_rolled_back(now: float) -> bool:
    raw_value = _setting_value(LICENSE_CLOCK_KEY)
    try:
        highest = float(raw_value)
    except (TypeError, ValueError):
        return False
    return now + CLOCK_ROLLBACK_TOLERANCE_SECONDS < highest


def record_trusted_time(now: Optional[float] = None) -> None:
    current_time = time.time() if now is None else float(now)
    with _write_transaction():
        SystemSetting.get_or_create(
            key=LICENSE_CLOCK_KEY,
            defaults={'value': '0', 'description': 'License highest observed UTC timestamp'},
        )
        setting = _locked_setting(LICENSE_CLOCK_KEY)
        try:
            previous = float(setting.value or 0)
        except (TypeError, ValueError):
            previous = 0
        if current_time > previous + 60:
            setting.value = str(current_time)
            setting.updated_at = datetime.now()
            setting.updated_by = 'system'
            setting.save()


def evaluate_license(*, now: Optional[float] = None, update_clock: bool = False) -> LicenseEvaluation:
    current_time = time.time() if now is None else float(now)
    token = _setting_value(LICENSE_TOKEN_KEY).strip()
    if not token:
        if update_clock:
            record_trusted_time(current_time)
        return LicenseEvaluation('free', 'missing', '当前使用永久免费试用版', dict(FREE_LIMITS))

    try:
        claims = decode_and_validate_token(token, now=current_time)
    except LicenseError as exc:
        return LicenseEvaluation('free', exc.code, exc.message, dict(FREE_LIMITS))

    if update_clock:
        record_trusted_time(current_time)
    return LicenseEvaluation(
        'licensed',
        'valid',
        '付费许可证有效',
        dict(claims['limits']),
        claims,
    )


def install_license(token: str, *, installed_by: str = 'admin') -> LicenseEvaluation:
    claims = decode_and_validate_token(token)
    with _write_transaction():
        setting, _ = SystemSetting.get_or_create(
            key=LICENSE_TOKEN_KEY,
            defaults={'value': '', 'description': 'Offline signed license token'},
        )
        setting.value = token.strip()
        setting.updated_at = datetime.now()
        setting.updated_by = installed_by
        setting.save()
    record_trusted_time()
    return LicenseEvaluation('licensed', 'valid', '付费许可证有效', dict(claims['limits']), claims)


def resource_usage() -> Dict[str, int]:
    return {name: model.select().count() for name, model in _RESOURCE_MODELS.items()}


@contextmanager
def quota_capacity(resource: str, requested: int = 1) -> Iterator[LicenseEvaluation]:
    if resource not in _RESOURCE_MODELS:
        raise ValueError(f'Unknown licensed resource: {resource}')
    if requested < 1:
        raise ValueError('requested must be at least 1')
    with _write_transaction():
        SystemSetting.get_or_create(
            key=LICENSE_LOCK_KEY,
            defaults={'value': '1', 'description': 'Serializes licensed resource creation'},
        )
        _locked_setting(LICENSE_LOCK_KEY)
        evaluation = evaluate_license()
        used = _RESOURCE_MODELS[resource].select().count()
        limit = evaluation.limits[resource]
        if used + requested > limit:
            label = '视频源' if resource == 'video_sources' else '算法'
            raise LicenseError(
                'license_quota_exceeded',
                f'{label}数量已达到当前授权上限 {limit}',
                {'resource': resource, 'used': used, 'requested': requested, 'limit': limit},
            )
        yield evaluation


def _algorithm_entitlement_ids(limit: int) -> set[int]:
    return {
        algorithm.id
        for algorithm in Algorithm.select(Algorithm.id)
        .order_by(Algorithm.id.asc())
        .limit(limit)
    }


def _configured_source_entitlement_ids(limit: int) -> set[int]:
    """Sources eligible for configuration-time operations such as workflow tests."""
    return {
        source.id
        for source in VideoSource.select(VideoSource.id)
        .where(VideoSource.enabled == True)
        .order_by(VideoSource.id.asc())
        .limit(limit)
    }


def _runtime_source_entitlement_ids(limit: int) -> set[int]:
    """Enabled sources with active workflows, ordered deterministically by source ID."""
    from app.core.database_models import Workflow
    from app.core.workflow_runtime import extract_source_id_from_workflow_data

    active_source_ids = set()
    for workflow in Workflow.select().where(
        (Workflow.is_active == True) & (Workflow.is_template == False)
    ):
        source_id = workflow.video_source_id or extract_source_id_from_workflow_data(workflow.data_dict)
        if source_id is not None:
            active_source_ids.add(int(source_id))
    return {
        source.id
        for source in VideoSource.select(VideoSource.id)
        .where((VideoSource.enabled == True) & (VideoSource.id.in_(active_source_ids)))
        .order_by(VideoSource.id.asc())
        .limit(limit)
    } if active_source_ids else set()


def runtime_entitlements() -> dict:
    """Return deterministic resource IDs allowed to execute for the active tier."""
    evaluation = evaluate_license(update_clock=True)
    return {
        'evaluation': evaluation,
        'source_ids': _runtime_source_entitlement_ids(
            evaluation.limits['video_sources']
        ),
        'algorithm_ids': _algorithm_entitlement_ids(
            evaluation.limits['algorithms']
        ),
    }


def ensure_resource_entitled(resource: str, resource_id: int) -> None:
    evaluation = evaluate_license(update_clock=True)
    if resource == 'video_sources':
        allowed_ids = _runtime_source_entitlement_ids(
            evaluation.limits['video_sources']
        )
    elif resource == 'algorithms':
        allowed_ids = _algorithm_entitlement_ids(evaluation.limits['algorithms'])
    else:
        raise ValueError(f'Unknown licensed resource: {resource}')
    if int(resource_id) not in allowed_ids:
        label = '视频源' if resource == 'video_sources' else '算法'
        raise LicenseError(
            'resource_not_entitled',
            f'当前授权不允许运行该{label}',
            {'resource': resource, 'resource_id': int(resource_id)},
        )


def ensure_workflow_entitled(workflow) -> None:
    from app.core.workflow_runtime import extract_algorithm_ids, extract_source_id_from_workflow_data

    evaluation = evaluate_license(update_clock=True)
    source_id = workflow.video_source_id or extract_source_id_from_workflow_data(workflow.data_dict)
    allowed_sources = _configured_source_entitlement_ids(
        evaluation.limits['video_sources']
    )
    if source_id is not None and int(source_id) not in allowed_sources:
        raise LicenseError('resource_not_entitled', '当前授权不允许运行该工作流的视频源')
    allowed_algorithms = _algorithm_entitlement_ids(evaluation.limits['algorithms'])
    algorithm_ids = set(extract_algorithm_ids(workflow.data_dict))
    if not algorithm_ids.issubset(allowed_algorithms):
        raise LicenseError(
            'resource_not_entitled',
            '工作流引用了当前授权范围外的算法',
            {'algorithm_ids': sorted(algorithm_ids - allowed_algorithms)},
        )


def serialize_status(*, include_details: bool = True) -> dict:
    entitlements = runtime_entitlements()
    evaluation: LicenseEvaluation = entitlements['evaluation']
    usage = resource_usage()
    claims = evaluation.claims or {}
    result = {
        'tier': evaluation.tier,
        'license_status': evaluation.license_status,
        'message': evaluation.license_message,
        'limits': evaluation.limits,
        'usage': usage,
        'over_limit': {
            name: usage[name] > evaluation.limits[name]
            for name in FREE_LIMITS
        },
        'expires_at': (
            datetime.fromtimestamp(float(claims['exp']), tz=timezone.utc).isoformat()
            if claims.get('exp') is not None
            else None
        ),
    }
    if include_details:
        result.update({
            'license_id': claims.get('license_id'),
            'customer': claims.get('customer'),
            'node_id': get_node_id(),
            'licensed_node_id': claims.get('node_id'),
            'entitled_source_ids': (
                sorted(entitlements['source_ids'])
            ),
            'entitled_algorithm_ids': (
                sorted(entitlements['algorithm_ids'])
            ),
        })
    return result
