"""Face gallery, enrollment, event, and runtime APIs."""

from __future__ import annotations

import base64
import csv
import fcntl
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

import numpy as np
from cryptography.exceptions import InvalidTag
from flask import Blueprint, jsonify, request, send_file
from PIL import Image, UnidentifiedImageError
from peewee import IntegrityError
from werkzeug.utils import secure_filename

from app.config import FACE_DATA_PATH, FACE_EVENT_PATH, FACE_MODEL_PATH
from app.core.algorithm_test_service import submit_algorithm_test
from app.core.database_models import (
    FaceEvent,
    FaceGallery,
    FaceGalleryMembership,
    FaceImportJob,
    FaceModelArtifact,
    FaceModelBundle,
    FacePerson,
    FaceTemplate,
    db,
)
from app.core.face_crypto import (
    FaceEncryptionConfigurationError,
    decrypt_biometric,
    decrypt_biometric_stream,
    encrypt_biometric,
    encrypt_biometric_stream,
    encryption_ready,
    generate_face_encryption_key,
)
from app.core.face_gallery import gallery_index_cache
from app.core.face_inference import (
    face_runtime_extensions,
    runtime_capabilities,
    supported_face_runtimes,
    verify_bundle_artifacts,
)
from app.core.alert_media_cleaner import resolve_media_path
from app.web.api.auth import (
    current_username,
    is_admin_user,
    require_admin,
    require_auth,
)


faces_bp = Blueprint('faces', __name__, url_prefix='/api/face')
logger = logging.getLogger(__name__)
_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
_MAX_ENROLLMENT_BYTES = 20 * 1024 * 1024
_MAX_TEMPLATES_PER_PERSON = 5
_MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
_MAX_IMPORT_ENTRIES = 10_000
_MAX_IMPORT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_IMPORT_UPLOAD_BYTES = 512 * 1024 * 1024
_MAX_IMPORT_MANIFEST_BYTES = 5 * 1024 * 1024
_FACE_IMPORT_LEASE_SECONDS = 600
_FACE_IMPORT_POLL_SECONDS = 1.0
_FACE_IMPORT_MAX_ATTEMPTS = 3
# Generic 500 responses can represent permanently invalid model artifacts.
# Retry only statuses that specifically describe overload or service transport.
_FACE_IMPORT_TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})
_FACE_ENROLLMENT_BATCH_IMAGES = 32
_FACE_ENROLLMENT_BATCH_BYTES = 12 * 1024 * 1024
_face_import_worker = None
_face_import_worker_lock = threading.Lock()


class FaceEnrollmentServiceError(RuntimeError):
    """An operational response from the isolated inference worker."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = int(status_code)


@faces_bp.before_request
def enforce_face_authentication():
    return require_auth(lambda: None)()


def _admin_guard():
    return require_admin(lambda: None)()


def _iso(value):
    return value.isoformat() if value else None


def _json_object(value, field='metadata'):
    if value in (None, ''):
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{field} 必须是 JSON 对象') from exc
    if not isinstance(parsed, dict):
        raise ValueError(f'{field} 必须是 JSON 对象')
    return parsed


def _remove_files(paths):
    for path in paths:
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                logger.warning('无法删除人脸临时文件: %s', path)


def _validated_image_mime(payload):
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image_format = str(image.format or '').upper()
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > 40_000_000:
                raise ValueError('人脸图片像素尺寸无效或超过4000万像素')
            if image_format not in {'JPEG', 'PNG', 'WEBP'}:
                raise ValueError('仅支持 JPEG、PNG 或 WebP 人脸图片')
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError('无法读取人脸图片') from exc
    return {
        'JPEG': 'image/jpeg',
        'PNG': 'image/png',
        'WEBP': 'image/webp',
    }[image_format]


def _serialize_bundle(bundle):
    artifacts = [
        {
            'id': artifact.id,
            'role': artifact.role,
            'runtime': artifact.runtime,
            'architecture': artifact.architecture,
            'device': artifact.device,
            'filename': artifact.filename,
            'file_size': artifact.file_size,
            'artifact_sha256': artifact.artifact_sha256,
            'metadata': artifact.metadata,
            'enabled': artifact.enabled,
        }
        for artifact in bundle.artifacts.order_by(FaceModelArtifact.role, FaceModelArtifact.runtime)
    ]
    return {
        'id': bundle.id,
        'portable_id': bundle.portable_id,
        'name': bundle.name,
        'version': bundle.version,
        'contract_id': bundle.contract_id,
        'embedding_dimension': bundle.embedding_dimension,
        'input_size': bundle.input_size,
        'preprocess': bundle.preprocess,
        'license_name': bundle.license_name,
        'license_url': bundle.license_url,
        'commercial_use_allowed': bundle.commercial_use_allowed,
        'enabled': bundle.enabled,
        'artifacts': artifacts,
        'created_at': _iso(bundle.created_at),
        'updated_at': _iso(bundle.updated_at),
        'created_by': bundle.created_by,
    }


def _serialize_gallery(gallery, include_counts=True):
    payload = {
        'id': gallery.id,
        'portable_id': gallery.portable_id,
        'name': gallery.name,
        'description': gallery.description,
        'model_bundle_id': gallery.model_bundle_id,
        'model_bundle_name': gallery.model_bundle.name if gallery.model_bundle_id else None,
        'model_contract': gallery.model_bundle.contract_id if gallery.model_bundle_id else None,
        'gallery_version': gallery.gallery_version,
        'high_threshold': gallery.high_threshold,
        'low_threshold': gallery.low_threshold,
        'enabled': gallery.enabled,
        'created_at': _iso(gallery.created_at),
        'updated_at': _iso(gallery.updated_at),
        'created_by': gallery.created_by,
    }
    if include_counts:
        payload['person_count'] = (
            FaceGalleryMembership.select()
            .where(FaceGalleryMembership.gallery == gallery.id)
            .count()
        )
        payload['template_count'] = (
            FaceTemplate.select()
            .join(FacePerson)
            .join(FaceGalleryMembership)
            .where(
                (FaceGalleryMembership.gallery == gallery.id)
                & (FaceTemplate.model_contract == gallery.model_bundle.contract_id)
            )
            .count()
        ) if gallery.model_bundle_id else 0
    return payload


def _serialize_person(
    person,
    gallery_ids=None,
    template_contract=None,
    templates=None,
):
    if gallery_ids is None:
        gallery_ids = [membership.gallery_id for membership in person.gallery_memberships]
    if templates is None:
        template_query = person.face_templates.order_by(FaceTemplate.created_at.desc())
        if template_contract:
            template_query = template_query.where(
                FaceTemplate.model_contract == template_contract
            )
        templates = list(template_query)
    return {
        'id': person.id,
        'portable_id': person.portable_id,
        'person_code': person.person_code,
        'name': person.name,
        'metadata': person.metadata,
        'enabled': person.enabled,
        'gallery_ids': gallery_ids,
        'template_count': len(templates),
        'ready_template_count': sum(1 for item in templates if item.encrypted_embedding is not None),
        'templates': [
            {
                'id': item.id,
                'image_mime': item.image_mime,
                'image_sha256': item.image_sha256,
                'quality_score': item.quality_score,
                'model_contract': item.model_contract,
                'inference_backend': item.inference_backend,
                'created_at': _iso(item.created_at),
            }
            for item in templates
        ],
        'created_at': _iso(person.created_at),
        'updated_at': _iso(person.updated_at),
        'created_by': person.created_by,
    }


def _bump_galleries(person_id, *, model_contract=None):
    query = (
        FaceGallery.select(FaceGallery.id)
        .join(FaceGalleryMembership)
        .where(FaceGalleryMembership.person == int(person_id))
    )
    if model_contract is not None:
        query = (
            query.switch(FaceGallery)
            .join(FaceModelBundle)
            .where(FaceModelBundle.contract_id == str(model_contract))
        )
    gallery_ids = [gallery.id for gallery in query]
    if gallery_ids:
        FaceGallery.update(
            gallery_version=FaceGallery.gallery_version + 1,
            updated_at=datetime.now(),
        ).where(FaceGallery.id.in_(gallery_ids)).execute()
        for gallery_id in gallery_ids:
            gallery_index_cache.invalidate(gallery_id)


def _gallery_has_active_import(gallery_id):
    return FaceImportJob.select().where(
        (FaceImportJob.gallery == int(gallery_id))
        & FaceImportJob.status.in_({'uploading', 'pending', 'processing'})
    ).exists()


def _person_has_active_import(person):
    gallery_ids = [item.gallery_id for item in person.gallery_memberships]
    return bool(gallery_ids) and FaceImportJob.select().where(
        FaceImportJob.gallery.in_(gallery_ids)
        & FaceImportJob.status.in_({'uploading', 'pending', 'processing'})
    ).exists()


@faces_bp.route('/model-bundles', methods=['GET'])
def list_model_bundles():
    bundles = FaceModelBundle.select().order_by(FaceModelBundle.updated_at.desc())
    return jsonify({'success': True, 'bundles': [_serialize_bundle(item) for item in bundles]})


@faces_bp.route('/model-bundles', methods=['POST'])
def create_model_bundle():
    guard = _admin_guard()
    if guard is not None:
        return guard
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    version = str(data.get('version') or 'v1.0').strip()
    contract_id = str(data.get('contract_id') or '').strip()
    if not name or not contract_id:
        return jsonify({'success': False, 'error': '名称和模型契约不能为空'}), 400
    try:
        embedding_dimension = int(data.get('embedding_dimension') or 512)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '特征维度必须是整数'}), 400
    if not 32 <= embedding_dimension <= 4096 or len(contract_id) > 128:
        return jsonify({'success': False, 'error': '特征维度或模型契约超出范围'}), 400
    now = datetime.now()
    try:
        bundle = FaceModelBundle.create(
            name=name,
            version=version,
            contract_id=contract_id,
            embedding_dimension=embedding_dimension,
            input_size=str(data.get('input_size') or '112x112'),
            preprocess_json=json.dumps(_json_object(data.get('preprocess')), ensure_ascii=False),
            license_name=str(data.get('license_name') or '').strip() or None,
            license_url=str(data.get('license_url') or '').strip() or None,
            commercial_use_allowed=bool(data.get('commercial_use_allowed', False)),
            enabled=bool(data.get('enabled', True)),
            created_at=now,
            updated_at=now,
            created_by=current_username('admin'),
        )
    except (IntegrityError, ValueError) as exc:
        return jsonify({'success': False, 'error': str(exc)}), 409
    return jsonify({'success': True, 'bundle': _serialize_bundle(bundle)}), 201


@faces_bp.route('/model-bundles/<int:bundle_id>/artifacts', methods=['POST'])
def upload_model_artifact(bundle_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    try:
        bundle = FaceModelBundle.get_by_id(bundle_id)
    except FaceModelBundle.DoesNotExist:
        return jsonify({'success': False, 'error': '模型包不存在'}), 404
    uploaded = request.files.get('file')
    role = str(request.form.get('role') or '').strip().lower()
    runtime = str(request.form.get('runtime') or '').strip().lower()
    if uploaded is None or role not in {'detection', 'embedding'}:
        return jsonify({'success': False, 'error': '缺少模型文件或模型角色无效'}), 400
    supported_runtimes, _plugin_errors = supported_face_runtimes()
    if runtime not in supported_runtimes:
        return jsonify({'success': False, 'error': '推理运行时无效'}), 400
    filename = secure_filename(uploaded.filename or '')
    if not filename:
        return jsonify({'success': False, 'error': '模型文件类型不支持'}), 400
    expected_extensions = face_runtime_extensions(runtime)
    extension = os.path.splitext(filename)[1].lower()
    if not expected_extensions or extension not in expected_extensions:
        allowed = ' / '.join(sorted(expected_extensions)) or '插件声明的扩展名'
        return jsonify({
            'success': False,
            'error': f'{runtime} 制品必须使用 {allowed} 文件',
        }), 400
    architecture = str(request.form.get('architecture') or 'any').strip().lower()
    device = str(request.form.get('device') or 'any').strip().lower()
    platform_tag = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
    if not platform_tag.fullmatch(architecture) or not platform_tag.fullmatch(device):
        return jsonify({'success': False, 'error': '架构和设备标签格式无效'}), 400
    try:
        metadata = _json_object(request.form.get('metadata'), 'metadata')
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    target_dir = os.path.join(FACE_MODEL_PATH, str(bundle.id), runtime, role)
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, f'{uuid.uuid4().hex}{extension}')
    digest = hashlib.sha256()
    size = 0
    try:
        with open(target_path, 'wb') as handle:
            while True:
                chunk = uploaded.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_ARTIFACT_BYTES:
                    raise ValueError('模型制品不能超过1GB')
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise ValueError('模型制品不能为空')
    except ValueError as exc:
        _remove_files((target_path,))
        return jsonify({'success': False, 'error': str(exc)}), 413
    except Exception:
        _remove_files((target_path,))
        raise
    old_path = None
    try:
        with db.atomic():
            artifact = FaceModelArtifact.get_or_none(
                (FaceModelArtifact.bundle == bundle.id)
                & (FaceModelArtifact.role == role)
                & (FaceModelArtifact.runtime == runtime)
                & (FaceModelArtifact.architecture == architecture)
                & (FaceModelArtifact.device == device)
            )
            if artifact is None:
                artifact = FaceModelArtifact.create(
                    bundle=bundle,
                    role=role,
                    runtime=runtime,
                    architecture=architecture,
                    device=device,
                    filename=filename,
                    file_path=target_path,
                    file_size=size,
                    artifact_sha256=digest.hexdigest(),
                    metadata_json=json.dumps(metadata, ensure_ascii=False),
                    enabled=True,
                    created_at=datetime.now(),
                )
            else:
                old_path = artifact.file_path
                artifact.filename = filename
                artifact.file_path = target_path
                artifact.file_size = size
                artifact.artifact_sha256 = digest.hexdigest()
                artifact.metadata_json = json.dumps(metadata, ensure_ascii=False)
                artifact.enabled = True
                artifact.created_at = datetime.now()
                artifact.save()
            bundle.updated_at = datetime.now()
            bundle.save()
    except Exception:
        _remove_files((target_path,))
        raise
    if old_path and old_path != target_path and os.path.isfile(old_path):
        try:
            os.remove(old_path)
        except OSError as exc:
            # The database already references the durable new artifact.  Old
            # file reclamation is best effort and must not corrupt that state.
            logger.warning('无法删除已替换的人脸模型制品 %s: %s', old_path, exc)
    return jsonify({'success': True, 'bundle': _serialize_bundle(bundle)}), 201


@faces_bp.route('/runtime', methods=['GET'])
def get_face_runtime():
    capabilities = runtime_capabilities()
    bundles = []
    for bundle in FaceModelBundle.select().where(FaceModelBundle.enabled == True):
        try:
            backend, artifacts, _ = verify_bundle_artifacts(bundle, 'auto')
            bundles.append({
                'bundle_id': bundle.id,
                'bundle_name': bundle.name,
                'ready': True,
                'backend': backend,
                'artifacts': {role: item.filename for role, item in artifacts.items()},
            })
        except Exception as exc:
            bundles.append({
                'bundle_id': bundle.id,
                'bundle_name': bundle.name,
                'ready': False,
                'error': str(exc),
            })
    return jsonify({
        'success': True,
        'encryption_ready': encryption_ready(),
        'capabilities': capabilities,
        'bundles': bundles,
    })


def _protected_face_data_exists() -> bool:
    if FaceTemplate.select(FaceTemplate.id).limit(1).exists():
        return True
    import_paths = FaceImportJob.select(FaceImportJob.encrypted_archive_path)
    if any(
        os.path.isfile(str(job.encrypted_archive_path or ''))
        for job in import_paths
    ):
        return True
    for root in (Path(FACE_EVENT_PATH), Path(FACE_DATA_PATH) / 'imports'):
        if root.is_dir() and next(root.rglob('*.face'), None) is not None:
            return True
    return False


@faces_bp.route('/encryption-key/generate', methods=['POST'])
def generate_encryption_key():
    guard = _admin_guard()
    if guard is not None:
        return guard
    if encryption_ready():
        return jsonify({
            'success': True,
            'encryption_ready': True,
            'created': False,
        })
    if _protected_face_data_exists():
        return jsonify({
            'success': False,
            'error': '检测到已加密的人脸数据，不能生成新密钥；请恢复原密钥',
        }), 409
    try:
        created, source = generate_face_encryption_key()
    except FaceEncryptionConfigurationError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 409
    except OSError as exc:
        logger.exception('生成人脸数据加密密钥失败')
        return jsonify({
            'success': False,
            'error': f'无法写入人脸数据加密密钥: {exc}',
        }), 503
    return jsonify({
        'success': True,
        'encryption_ready': True,
        'created': created,
        'source': source,
    }), 201 if created else 200


@faces_bp.route('/galleries', methods=['GET'])
def list_galleries():
    query = FaceGallery.select().order_by(FaceGallery.updated_at.desc())
    if not is_admin_user():
        query = query.where(FaceGallery.created_by == current_username())
    return jsonify({'success': True, 'galleries': [_serialize_gallery(item) for item in query]})


@faces_bp.route('/galleries', methods=['POST'])
def create_gallery():
    guard = _admin_guard()
    if guard is not None:
        return guard
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': '人脸库名称不能为空'}), 400
    try:
        low = float(data.get('low_threshold', 0.50))
        high = float(data.get('high_threshold', 0.60))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '阈值必须是数字'}), 400
    if not 0 <= low < high <= 1:
        return jsonify({'success': False, 'error': '阈值必须满足 0 ≤ 低阈值 < 高阈值 ≤ 1'}), 400
    bundle_id = data.get('model_bundle_id')
    try:
        bundle = FaceModelBundle.get_by_id(int(bundle_id)) if bundle_id else None
        now = datetime.now()
        gallery = FaceGallery.create(
            name=name,
            description=str(data.get('description') or '').strip() or None,
            model_bundle=bundle,
            high_threshold=high,
            low_threshold=low,
            enabled=bool(data.get('enabled', True)),
            created_at=now,
            updated_at=now,
            created_by=current_username('admin'),
        )
    except FaceModelBundle.DoesNotExist:
        return jsonify({'success': False, 'error': '模型包不存在'}), 400
    except IntegrityError:
        return jsonify({'success': False, 'error': '同名人脸库已存在'}), 409
    return jsonify({'success': True, 'gallery': _serialize_gallery(gallery)}), 201


@faces_bp.route('/galleries/<int:gallery_id>', methods=['PATCH'])
def update_gallery(gallery_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    try:
        gallery = FaceGallery.get_by_id(gallery_id)
    except FaceGallery.DoesNotExist:
        return jsonify({'success': False, 'error': '人脸库不存在'}), 404
    data = request.get_json(silent=True) or {}
    if 'name' in data:
        gallery.name = str(data['name'] or '').strip()
    if 'description' in data:
        gallery.description = str(data['description'] or '').strip() or None
    recognition_policy_changed = False
    if 'enabled' in data:
        enabled = bool(data['enabled'])
        recognition_policy_changed = recognition_policy_changed or gallery.enabled != enabled
        gallery.enabled = enabled
    try:
        low = float(data.get('low_threshold', gallery.low_threshold))
        high = float(data.get('high_threshold', gallery.high_threshold))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '阈值必须是数字'}), 400
    if not 0 <= low < high <= 1:
        return jsonify({'success': False, 'error': '阈值必须满足 0 ≤ 低阈值 < 高阈值 ≤ 1'}), 400
    recognition_policy_changed = recognition_policy_changed or (
        float(gallery.low_threshold) != low or float(gallery.high_threshold) != high
    )
    gallery.low_threshold = low
    gallery.high_threshold = high
    if 'model_bundle_id' in data:
        if _gallery_has_active_import(gallery.id):
            return jsonify({'success': False, 'error': '批量录入期间不能更换模型包'}), 409
        try:
            bundle = (
                FaceModelBundle.get_by_id(int(data['model_bundle_id']))
                if data['model_bundle_id'] else None
            )
        except (TypeError, ValueError, FaceModelBundle.DoesNotExist):
            return jsonify({'success': False, 'error': '模型包不存在'}), 400
        bundle_id = bundle.id if bundle is not None else None
        recognition_policy_changed = (
            recognition_policy_changed or gallery.model_bundle_id != bundle_id
        )
        gallery.model_bundle = bundle
    if recognition_policy_changed:
        gallery.gallery_version += 1
        gallery_index_cache.invalidate(gallery.id)
    gallery.updated_at = datetime.now()
    gallery.save()
    return jsonify({'success': True, 'gallery': _serialize_gallery(gallery)})


@faces_bp.route('/galleries/<int:gallery_id>', methods=['DELETE'])
def delete_gallery(gallery_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    if _gallery_has_active_import(gallery_id):
        return jsonify({'success': False, 'error': '批量录入期间不能删除人脸库'}), 409
    deleted = FaceGallery.delete().where(FaceGallery.id == gallery_id).execute()
    gallery_index_cache.invalidate(gallery_id)
    return jsonify({'success': True, 'deleted': bool(deleted)})


@faces_bp.route('/persons', methods=['GET'])
def list_persons():
    query = FacePerson.select().order_by(FacePerson.updated_at.desc())
    gallery_id = request.args.get('gallery_id', type=int)
    template_contract = None
    if gallery_id:
        query = query.join(FaceGalleryMembership).where(FaceGalleryMembership.gallery == gallery_id)
        gallery = FaceGallery.get_or_none(FaceGallery.id == gallery_id)
        if gallery and gallery.model_bundle_id:
            template_contract = gallery.model_bundle.contract_id
    if not is_admin_user():
        query = query.where(FacePerson.created_by == current_username())
    search = str(request.args.get('q') or '').strip()
    if search:
        query = query.where(
            (FacePerson.name.contains(search)) | (FacePerson.person_code.contains(search))
        )
    page = max(1, request.args.get('page', default=1, type=int) or 1)
    page_size = min(
        100,
        max(1, request.args.get('page_size', default=50, type=int) or 50),
    )
    total = query.count()
    last_page = max(1, (total + page_size - 1) // page_size)
    page = min(page, last_page)
    people = list(query.paginate(page, page_size))
    person_ids = [person.id for person in people]
    gallery_ids_by_person = {person_id: [] for person_id in person_ids}
    templates_by_person = {person_id: [] for person_id in person_ids}
    if person_ids:
        for membership in FaceGalleryMembership.select().where(
            FaceGalleryMembership.person.in_(person_ids)
        ):
            gallery_ids_by_person[membership.person_id].append(membership.gallery_id)
        template_query = (
            FaceTemplate.select()
            .where(FaceTemplate.person.in_(person_ids))
            .order_by(FaceTemplate.created_at.desc())
        )
        if template_contract:
            template_query = template_query.where(
                FaceTemplate.model_contract == template_contract
            )
        for template in template_query:
            templates_by_person[template.person_id].append(template)
    return jsonify({
        'success': True,
        'persons': [
            _serialize_person(
                item,
                gallery_ids=gallery_ids_by_person[item.id],
                templates=templates_by_person[item.id],
            )
            for item in people
        ],
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': (total + page_size - 1) // page_size,
        },
    })


@faces_bp.route('/persons', methods=['POST'])
def create_person():
    guard = _admin_guard()
    if guard is not None:
        return guard
    data = request.get_json(silent=True) or {}
    code = str(data.get('person_code') or '').strip()
    name = str(data.get('name') or '').strip()
    if not code or not name:
        return jsonify({'success': False, 'error': '人员编号和姓名不能为空'}), 400
    try:
        metadata = _json_object(data.get('metadata'))
        with db.atomic():
            now = datetime.now()
            person = FacePerson.create(
                person_code=code,
                name=name,
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                enabled=bool(data.get('enabled', True)),
                created_at=now,
                updated_at=now,
                created_by=current_username('admin'),
            )
            gallery_ids = sorted({int(value) for value in (data.get('gallery_ids') or [])})
            for gallery in FaceGallery.select().where(FaceGallery.id.in_(gallery_ids)):
                FaceGalleryMembership.create(gallery=gallery, person=person, created_at=now)
                gallery.gallery_version += 1
                gallery.updated_at = now
                gallery.save()
    except (IntegrityError, ValueError) as exc:
        return jsonify({'success': False, 'error': str(exc)}), 409
    return jsonify({'success': True, 'person': _serialize_person(person)}), 201


@faces_bp.route('/persons/<int:person_id>', methods=['PATCH'])
def update_person(person_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    try:
        person = FacePerson.get_by_id(person_id)
    except FacePerson.DoesNotExist:
        return jsonify({'success': False, 'error': '人员不存在'}), 404
    data = request.get_json(silent=True) or {}
    identity_updates = {}
    for field, label in (('person_code', '人员编号'), ('name', '姓名')):
        if field not in data:
            continue
        value = str(data[field] or '').strip()
        if not value:
            return jsonify({'success': False, 'error': f'{label}不能为空'}), 400
        identity_updates[field] = value
    affected_gallery_ids = set()
    try:
        with db.atomic():
            previous = {item.gallery_id for item in person.gallery_memberships}
            identity_changed = False
            if 'person_code' in identity_updates:
                person_code = identity_updates['person_code']
                identity_changed = identity_changed or person.person_code != person_code
                person.person_code = person_code
            if 'name' in identity_updates:
                name = identity_updates['name']
                identity_changed = identity_changed or person.name != name
                person.name = name
            if 'metadata' in data:
                person.metadata_json = json.dumps(_json_object(data['metadata']), ensure_ascii=False)
            if 'enabled' in data:
                enabled = bool(data['enabled'])
                identity_changed = identity_changed or person.enabled != enabled
                person.enabled = enabled
            person.updated_at = datetime.now()
            person.save()
            if 'gallery_ids' in data:
                desired = {int(value) for value in (data.get('gallery_ids') or [])}
                FaceGalleryMembership.delete().where(
                    (FaceGalleryMembership.person == person.id)
                    & FaceGalleryMembership.gallery.in_(previous - desired)
                ).execute()
                for gallery_id in desired - previous:
                    FaceGalleryMembership.create(
                        gallery=gallery_id, person=person, created_at=datetime.now()
                    )
                if desired != previous:
                    affected_gallery_ids.update(previous | desired)
            if identity_changed:
                affected_gallery_ids.update(previous)
                if 'gallery_ids' in data:
                    affected_gallery_ids.update(desired)
            if affected_gallery_ids:
                FaceGallery.update(
                    gallery_version=FaceGallery.gallery_version + 1,
                    updated_at=datetime.now(),
                ).where(FaceGallery.id.in_(affected_gallery_ids)).execute()
    except (IntegrityError, ValueError) as exc:
        return jsonify({'success': False, 'error': str(exc)}), 409
    for gallery_id in affected_gallery_ids:
        gallery_index_cache.invalidate(gallery_id)
    return jsonify({'success': True, 'person': _serialize_person(person)})


@faces_bp.route('/persons/<int:person_id>', methods=['DELETE'])
def delete_person(person_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    try:
        person = FacePerson.get_by_id(person_id)
    except FacePerson.DoesNotExist:
        return jsonify({'success': True, 'deleted': False})
    if _person_has_active_import(person):
        return jsonify({'success': False, 'error': '批量录入期间不能删除相关人员'}), 409
    gallery_ids = [item.gallery_id for item in person.gallery_memberships]
    with db.atomic():
        person.delete_instance(recursive=True)
        if gallery_ids:
            FaceGallery.update(
                gallery_version=FaceGallery.gallery_version + 1,
                updated_at=datetime.now(),
            ).where(FaceGallery.id.in_(gallery_ids)).execute()
    for gallery_id in gallery_ids:
        gallery_index_cache.invalidate(gallery_id)
    return jsonify({'success': True, 'deleted': True})


def _extract_enrollment(bundle_id, image_bytes, min_face_size=80):
    body, status = submit_algorithm_test({
        'kind': 'face_enrollment',
        'bundle_id': int(bundle_id),
        'backend': 'auto',
        'min_face_size': int(min_face_size),
        'image_base64': base64.b64encode(image_bytes).decode('ascii'),
    })
    if status >= 400:
        raise FaceEnrollmentServiceError(
            body.get('error') or '人脸特征提取服务不可用', status
        )
    if not body.get('success'):
        raise ValueError(body.get('error') or '人脸特征提取失败')
    return body


def _extract_enrollment_batch(bundle_id, images, min_face_size=80):
    body, status = submit_algorithm_test({
        'kind': 'face_enrollment_batch',
        'bundle_id': int(bundle_id),
        'backend': 'auto',
        'min_face_size': int(min_face_size),
        'images_base64': [
            base64.b64encode(image_bytes).decode('ascii')
            for image_bytes in images
        ],
    })
    if status >= 400:
        raise FaceEnrollmentServiceError(
            body.get('error') or '人脸特征提取服务不可用', status
        )
    results = body.get('results')
    if not body.get('success') or not isinstance(results, list):
        raise FaceEnrollmentServiceError(
            body.get('error') or '人脸特征提取服务返回无效响应', 502
        )
    if len(results) != len(images):
        raise FaceEnrollmentServiceError('人脸特征提取服务返回数量不一致', 502)
    return results


@faces_bp.route('/persons/<int:person_id>/templates', methods=['POST'])
def upload_face_template(person_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    try:
        person = FacePerson.get_by_id(person_id)
    except FacePerson.DoesNotExist:
        return jsonify({'success': False, 'error': '人员不存在'}), 404
    uploaded = request.files.get('file')
    if uploaded is None:
        return jsonify({'success': False, 'error': '请选择人脸图片'}), 400
    image_bytes = uploaded.stream.read(_MAX_ENROLLMENT_BYTES + 1)
    if not image_bytes or len(image_bytes) > _MAX_ENROLLMENT_BYTES:
        return jsonify({'success': False, 'error': '人脸图片为空或超过20MB'}), 413
    galleries = [item.gallery for item in person.gallery_memberships]
    bundles = {item.model_bundle_id: item.model_bundle for item in galleries if item.model_bundle_id}
    if not bundles:
        return jsonify({'success': False, 'error': '人员所在人脸库尚未配置模型包'}), 409
    raw_gallery_id = request.form.get('gallery_id')
    raw_bundle_id = request.form.get('model_bundle_id')
    if raw_gallery_id and raw_bundle_id:
        return jsonify({'success': False, 'error': 'gallery_id 和 model_bundle_id 只能选择一个'}), 400
    if raw_gallery_id:
        try:
            selected_gallery_id = int(raw_gallery_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'gallery_id 必须是整数'}), 400
        selected_gallery = next(
            (gallery for gallery in galleries if gallery.id == selected_gallery_id),
            None,
        )
        if selected_gallery is None:
            return jsonify({'success': False, 'error': '人员不属于选择的人脸库'}), 409
        if not selected_gallery.model_bundle_id:
            return jsonify({'success': False, 'error': '选择的人脸库尚未配置模型包'}), 409
        bundle = selected_gallery.model_bundle
    elif raw_bundle_id:
        try:
            selected_bundle_id = int(raw_bundle_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'model_bundle_id 必须是整数'}), 400
        bundle = bundles.get(selected_bundle_id)
        if bundle is None:
            return jsonify({'success': False, 'error': '人员所属人脸库未使用选择的模型包'}), 409
    elif len(bundles) == 1:
        bundle = next(iter(bundles.values()))
    else:
        return jsonify({
            'success': False,
            'error': '人员所属人脸库使用了不同模型契约，请指定 gallery_id 分别录入',
        }), 409
    existing_count = FaceTemplate.select().where(
        (FaceTemplate.person == person.id)
        & (FaceTemplate.model_contract == bundle.contract_id)
    ).count()
    if existing_count >= _MAX_TEMPLATES_PER_PERSON:
        return jsonify({'success': False, 'error': '每人每个模型契约最多录入5张照片'}), 409
    try:
        image_mime = _validated_image_mime(image_bytes)
        inference = _extract_enrollment(bundle.id, image_bytes)
        embedding_bytes = base64.b64decode(inference['embedding_base64'], validate=True)
        image_digest = hashlib.sha256(image_bytes).hexdigest()
        with db.atomic():
            template = FaceTemplate.create(
                person=person,
                encrypted_image=encrypt_biometric(image_bytes, purpose=f'face-image:{person.id}'),
                encrypted_embedding=encrypt_biometric(
                    embedding_bytes, purpose=f'face-embedding:{person.id}'
                ),
                image_mime=image_mime,
                image_sha256=image_digest,
                quality_score=float((inference.get('quality') or {}).get('score') or 0),
                model_contract=inference.get('model_contract'),
                inference_backend=inference.get('backend'),
                created_at=datetime.now(),
                created_by=current_username('admin'),
            )
            _bump_galleries(person.id, model_contract=bundle.contract_id)
    except FaceEnrollmentServiceError as exc:
        return jsonify({'success': False, 'error': str(exc)}), exc.status_code
    except FaceEncryptionConfigurationError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 503
    except (ValueError, IntegrityError) as exc:
        return jsonify({'success': False, 'error': str(exc)}), 422
    return jsonify({'success': True, 'person': _serialize_person(person), 'template_id': template.id}), 201


@faces_bp.route('/templates/<int:template_id>', methods=['GET'])
def get_face_template_image(template_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    try:
        template = FaceTemplate.get_by_id(template_id)
        payload = decrypt_biometric(
            bytes(template.encrypted_image), purpose=f'face-image:{template.person_id}'
        )
    except FaceTemplate.DoesNotExist:
        return jsonify({'success': False, 'error': '人脸模板不存在'}), 404
    except FaceEncryptionConfigurationError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 503
    except (InvalidTag, ValueError):
        return jsonify({'success': False, 'error': '人脸模板密文校验失败'}), 422
    response = send_file(
        io.BytesIO(payload), mimetype=template.image_mime,
        download_name='face-template', max_age=0,
    )
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['Pragma'] = 'no-cache'
    return response


@faces_bp.route('/templates/<int:template_id>', methods=['DELETE'])
def delete_face_template(template_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    try:
        template = FaceTemplate.get_by_id(template_id)
    except FaceTemplate.DoesNotExist:
        return jsonify({'success': True, 'deleted': False})
    person_id = template.person_id
    model_contract = template.model_contract
    template.delete_instance()
    _bump_galleries(person_id, model_contract=model_contract)
    return jsonify({'success': True, 'deleted': True})


def _safe_zip_members(archive):
    total = 0
    members = archive.infolist()
    seen = set()
    if len(members) > _MAX_IMPORT_ENTRIES:
        raise ValueError('导入包文件数量超过限制')
    for member in members:
        path = PurePosixPath(member.filename.replace('\\', '/'))
        if path.is_absolute() or '..' in path.parts:
            raise ValueError(f'导入包包含非法路径: {member.filename}')
        normalized = path.as_posix()
        if normalized in seen:
            raise ValueError(f'导入包包含重复文件: {member.filename}')
        seen.add(normalized)
        total += int(member.file_size or 0)
        if total > _MAX_IMPORT_BYTES:
            raise ValueError('导入包解压大小超过限制')
    return members


def _read_import_manifest(archive):
    matches = [item for item in archive.infolist() if item.filename == 'manifest.csv']
    if len(matches) != 1:
        raise ValueError('导入包必须且只能包含一个 manifest.csv')
    if matches[0].file_size > _MAX_IMPORT_MANIFEST_BYTES:
        raise ValueError('manifest.csv 不能超过5MB')
    rows = list(csv.DictReader(
        io.StringIO(archive.read(matches[0]).decode('utf-8-sig'))
    ))
    if not rows:
        raise ValueError('manifest.csv 不能为空')
    return rows


def _valid_import_person_code(value):
    return (
        bool(value)
        and len(value) <= 128
        and '/' not in value
        and '\\' not in value
        and value not in {'.', '..'}
    )


def _inspect_face_import(archive):
    _safe_zip_members(archive)
    rows = _read_import_manifest(archive)
    errors = []
    codes = set()
    image_count = 0
    entries = {item.filename: item for item in archive.infolist()}
    for number, row in enumerate(rows, 2):
        code = str(row.get('person_code') or '').strip()
        name = str(row.get('name') or '').strip()
        if not _valid_import_person_code(code) or not name:
            errors.append({'row': number, 'error': 'person_code 或 name 无效'})
            continue
        if code in codes:
            errors.append({'row': number, 'error': f'人员编号重复: {code}'})
        codes.add(code)
        prefix = f'photos/{code}/'
        photos = [
            item for item in entries
            if item.startswith(prefix)
            and os.path.splitext(item)[1].lower() in _IMAGE_EXTENSIONS
        ]
        if not photos:
            errors.append({'row': number, 'error': '未找到人员照片'})
        oversized = [
            item for item in photos
            if entries[item].file_size > _MAX_ENROLLMENT_BYTES
        ]
        if oversized:
            errors.append({
                'row': number,
                'error': f'{len(oversized)} 张照片超过20MB',
            })
        image_count += len(photos)
    return rows, image_count, errors


def _serialize_import_job(job):
    return {
        'id': job.id,
        'gallery_id': job.gallery_id,
        'status': job.status,
        'total_people': job.total_people,
        'total_images': job.total_images,
        'processed_people': job.processed_people,
        'succeeded_people': job.succeeded_people,
        'failed_people': job.failed_people,
        'errors': job.errors,
        'locked_at': _iso(job.locked_at),
        'created_at': _iso(job.created_at),
        'updated_at': _iso(job.updated_at),
        'completed_at': _iso(job.completed_at),
        'created_by': job.created_by,
    }


def _face_import_retry_attempt(job):
    for error in job.errors:
        if not isinstance(error, dict) or 'retry_attempt' not in error:
            continue
        try:
            return max(0, int(error['retry_attempt']))
        except (TypeError, ValueError):
            continue
    return 0


def _person_gallery_ids(person, *, model_contract=None):
    gallery_ids = set()
    for membership in person.gallery_memberships:
        member_gallery = membership.gallery
        if model_contract is not None:
            if not member_gallery.model_bundle_id:
                continue
            if member_gallery.model_bundle.contract_id != model_contract:
                continue
        gallery_ids.add(member_gallery.id)
    return gallery_ids


def _face_import_plaintext_directories(create=False, required_bytes=0):
    directories = []
    shared_memory = '/dev/shm'
    if os.path.isdir(shared_memory) and os.access(shared_memory, os.W_OK):
        try:
            stats = os.statvfs(shared_memory)
            free_bytes = stats.f_bavail * stats.f_frsize
        except OSError:
            free_bytes = 0
        if free_bytes >= int(required_bytes) + 8 * 1024 * 1024:
            directories.append(os.path.join(shared_memory, 'video-ba-face-imports'))
    directories.append(os.path.join(tempfile.gettempdir(), 'video-ba-face-imports'))
    if create:
        for directory in directories:
            try:
                os.makedirs(directory, mode=0o700, exist_ok=True)
                os.chmod(directory, 0o700)
                return directory
            except OSError:
                continue
        raise OSError('无法创建人脸导入临时目录')
    # FACE_DATA_PATH is included only to reclaim plaintext left by versions
    # that used the persistent biometric volume for decryption.
    directories.append(FACE_DATA_PATH)
    return list(dict.fromkeys(directories))


def _new_plaintext_face_import_archive(required_bytes=0):
    directory = _face_import_plaintext_directories(
        create=True, required_bytes=required_bytes
    )
    archive = tempfile.NamedTemporaryFile(
        prefix='.face-import-', suffix='.zip', dir=directory, delete=False
    )
    archive.close()
    os.chmod(archive.name, 0o600)
    return archive.name


def _cleanup_stale_plaintext_face_imports(now=None):
    cutoff = float(now if now is not None else datetime.now().timestamp())
    cutoff -= _FACE_IMPORT_LEASE_SECONDS
    removed = 0
    for directory in _face_import_plaintext_directories():
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not name.startswith('.face-import-') or not name.endswith('.zip'):
                continue
            path = os.path.join(directory, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                logger.warning('无法清理崩溃遗留的人脸导入明文文件: %s', path)
    return removed


def _bump_gallery_versions(gallery_ids):
    gallery_ids = {int(value) for value in gallery_ids if value}
    if not gallery_ids:
        return
    FaceGallery.update(
        gallery_version=FaceGallery.gallery_version + 1,
        updated_at=datetime.now(),
    ).where(FaceGallery.id.in_(gallery_ids)).execute()
    for gallery_id in gallery_ids:
        gallery_index_cache.invalidate(gallery_id)


def _infer_import_candidates(bundle_id, candidates):
    offset = 0
    while offset < len(candidates):
        chunk = []
        chunk_bytes = 0
        while offset < len(candidates) and len(chunk) < _FACE_ENROLLMENT_BATCH_IMAGES:
            candidate = candidates[offset]
            image_size = len(candidate['image_bytes'])
            if chunk and chunk_bytes + image_size > _FACE_ENROLLMENT_BATCH_BYTES:
                break
            chunk.append(candidate)
            chunk_bytes += image_size
            offset += 1
        results = _extract_enrollment_batch(
            bundle_id, [candidate['image_bytes'] for candidate in chunk]
        )
        for candidate, result in zip(chunk, results):
            if result.get('success'):
                candidate['inference'] = result
            else:
                candidate['error'] = result.get('error') or '人脸特征提取失败'


def _prepare_import_row(archive, archive_names, row_number, row, gallery, job):
    code = str(row.get('person_code') or '').strip()
    name = str(row.get('name') or '').strip()
    prefix = f'photos/{code}/'
    photos = sorted(
        item for item in archive_names
        if item.startswith(prefix)
        and os.path.splitext(item)[1].lower() in _IMAGE_EXTENSIONS
    )
    context = {
        'row': row_number,
        'person_code': code,
        'name': name,
        'candidates': [],
        'photo_errors': [],
        'preparation_error': None,
    }
    if not _valid_import_person_code(code) or not name or not photos:
        context['preparation_error'] = '人员编号、姓名或照片缺失'
        return context

    person = FacePerson.get_or_none(
        (FacePerson.created_by == job.created_by)
        & (FacePerson.person_code == code)
    )
    existing_count = 0
    existing_digests = set()
    if person is not None:
        existing_templates = FaceTemplate.select(
            FaceTemplate.image_sha256
        ).where(
            (FaceTemplate.person == person.id)
            & (FaceTemplate.model_contract == gallery.model_bundle.contract_id)
        )
        existing_digests = {item.image_sha256 for item in existing_templates}
        existing_count = len(existing_digests)
    context['existing_count'] = existing_count
    seen_digests = set(existing_digests)
    available = max(0, _MAX_TEMPLATES_PER_PERSON - existing_count)
    for photo_path in photos:
        if len(context['candidates']) >= available:
            break
        if archive.getinfo(photo_path).file_size > _MAX_ENROLLMENT_BYTES:
            context['photo_errors'].append(f'{photo_path}: 图片超过20MB')
            continue
        image_bytes = archive.read(photo_path)
        digest = hashlib.sha256(image_bytes).hexdigest()
        if digest in seen_digests:
            continue
        seen_digests.add(digest)
        try:
            image_mime = _validated_image_mime(image_bytes)
        except ValueError as exc:
            context['photo_errors'].append(f'{photo_path}: {exc}')
            continue
        context['candidates'].append({
            'path': photo_path,
            'image_bytes': image_bytes,
            'image_mime': image_mime,
            'digest': digest,
        })
    return context


def _commit_import_row(context, gallery, job):
    if context.get('preparation_error'):
        raise ValueError(context['preparation_error'])
    affected_gallery_ids = set()
    photo_errors = list(context.get('photo_errors') or [])
    with db.atomic():
        person, created = FacePerson.get_or_create(
            created_by=job.created_by,
            person_code=context['person_code'],
            defaults={
                'name': context['name'],
                'metadata_json': '{}',
                'enabled': True,
                'created_at': datetime.now(),
                'updated_at': datetime.now(),
            },
        )
        identity_changed = not created and person.name != context['name']
        if identity_changed:
            person.name = context['name']
            person.updated_at = datetime.now()
            person.save()
        _membership, membership_created = FaceGalleryMembership.get_or_create(
            gallery=gallery,
            person=person,
            defaults={'created_at': datetime.now()},
        )
        existing_count = FaceTemplate.select().where(
            (FaceTemplate.person == person.id)
            & (FaceTemplate.model_contract == gallery.model_bundle.contract_id)
        ).count()
        successful_templates = existing_count
        templates_changed = False
        for candidate in context['candidates']:
            inference = candidate.get('inference')
            if inference is None:
                photo_errors.append(
                    f"{candidate['path']}: {candidate.get('error') or '人脸特征提取失败'}"
                )
                continue
            if successful_templates >= _MAX_TEMPLATES_PER_PERSON:
                break
            if FaceTemplate.select().where(
                (FaceTemplate.person == person.id)
                & (FaceTemplate.image_sha256 == candidate['digest'])
                & (FaceTemplate.model_contract == gallery.model_bundle.contract_id)
            ).exists():
                continue
            embedding = base64.b64decode(
                inference['embedding_base64'], validate=True
            )
            FaceTemplate.create(
                person=person,
                encrypted_image=encrypt_biometric(
                    candidate['image_bytes'], purpose=f'face-image:{person.id}'
                ),
                encrypted_embedding=encrypt_biometric(
                    embedding, purpose=f'face-embedding:{person.id}'
                ),
                image_mime=candidate['image_mime'],
                image_sha256=candidate['digest'],
                quality_score=float(
                    (inference.get('quality') or {}).get('score') or 0
                ),
                model_contract=inference.get('model_contract'),
                inference_backend=inference.get('backend'),
                created_at=datetime.now(),
                created_by=job.created_by,
            )
            successful_templates += 1
            templates_changed = True
        if successful_templates <= 0:
            raise ValueError('; '.join(photo_errors) or '没有可用的人脸照片')
        if identity_changed:
            affected_gallery_ids.update(_person_gallery_ids(person))
        if membership_created:
            affected_gallery_ids.add(gallery.id)
        if templates_changed:
            affected_gallery_ids.update(_person_gallery_ids(
                person, model_contract=gallery.model_bundle.contract_id,
            ))
        # Also cover idempotent replay after a crash between the row commit and
        # the version bump. Existing templates are eligible in every gallery
        # with this contract, so all of those indexes must be refreshed.
        affected_gallery_ids.update(_person_gallery_ids(
            person, model_contract=gallery.model_bundle.contract_id,
        ))
        affected_gallery_ids.add(gallery.id)
    return affected_gallery_ids, photo_errors


def _run_face_import(job_id):
    temporary_archive = None
    encrypted_path = None
    remove_encrypted_archive = False
    try:
        with db.connection_context():
            job = FaceImportJob.get_by_id(job_id)
            gallery = FaceGallery.get_by_id(job.gallery_id)
            encrypted_path = job.encrypted_archive_path
            job.status = 'processing'
            job.locked_at = datetime.now()
            job.updated_at = datetime.now()
            job.save()
            temporary_archive = _new_plaintext_face_import_archive(
                os.path.getsize(encrypted_path)
            )
            with open(encrypted_path, 'rb') as source, open(temporary_archive, 'wb') as target:
                decrypt_biometric_stream(source, target, purpose=f'face-import:{job.id}')

            errors = []
            with zipfile.ZipFile(temporary_archive) as archive:
                _safe_zip_members(archive)
                rows = _read_import_manifest(archive)
                archive_names = set(archive.namelist())
                group = []
                group_images = 0
                group_bytes = 0

                def flush_group():
                    nonlocal group, group_images, group_bytes
                    if not group:
                        return
                    candidates = [
                        candidate
                        for context in group
                        for candidate in context['candidates']
                    ]
                    if candidates:
                        _infer_import_candidates(gallery.model_bundle_id, candidates)
                    affected_gallery_ids = set()
                    for context in group:
                        try:
                            affected, photo_errors = _commit_import_row(
                                context, gallery, job
                            )
                            affected_gallery_ids.update(affected)
                            if photo_errors:
                                errors.append({
                                    'row': context['row'],
                                    'person_code': context['person_code'],
                                    'warning': '; '.join(photo_errors),
                                })
                            job.succeeded_people += 1
                        except Exception as exc:
                            job.failed_people += 1
                            errors.append({
                                'row': context['row'],
                                'person_code': context['person_code'],
                                'error': str(exc),
                            })
                        job.processed_people += 1
                        job.errors_json = json.dumps(errors[-200:], ensure_ascii=False)
                        job.locked_at = datetime.now()
                        job.updated_at = datetime.now()
                        job.save()
                    _bump_gallery_versions(affected_gallery_ids)
                    group = []
                    group_images = 0
                    group_bytes = 0

                for row_number, row in enumerate(rows, 2):
                    context = _prepare_import_row(
                        archive, archive_names, row_number, row, gallery, job
                    )
                    context_images = len(context['candidates'])
                    context_bytes = sum(
                        len(candidate['image_bytes'])
                        for candidate in context['candidates']
                    )
                    if group and (
                        group_images + context_images > _FACE_ENROLLMENT_BATCH_IMAGES
                        or group_bytes + context_bytes > _FACE_ENROLLMENT_BATCH_BYTES
                    ):
                        flush_group()
                    group.append(context)
                    group_images += context_images
                    group_bytes += context_bytes
                    if (
                        group_images >= _FACE_ENROLLMENT_BATCH_IMAGES
                        or group_bytes >= _FACE_ENROLLMENT_BATCH_BYTES
                    ):
                        flush_group()
                flush_group()

            job.status = 'completed' if job.failed_people == 0 else 'completed_with_errors'
            job.errors_json = json.dumps(errors[-200:], ensure_ascii=False)
            job.locked_at = None
            job.completed_at = datetime.now()
            job.updated_at = job.completed_at
            job.save()
            remove_encrypted_archive = True
    except FaceEnrollmentServiceError as exc:
        try:
            with db.connection_context():
                job = FaceImportJob.get_by_id(job_id)
                attempt = _face_import_retry_attempt(job) + 1
                retrying = (
                    exc.status_code in _FACE_IMPORT_TRANSIENT_STATUSES
                    and attempt < _FACE_IMPORT_MAX_ATTEMPTS
                )
                now = datetime.now()
                job.status = 'processing' if retrying else 'failed'
                job.errors_json = json.dumps([{
                    'error': str(exc),
                    'status_code': exc.status_code,
                    'retrying': retrying,
                    'retry_attempt': attempt,
                    'max_attempts': _FACE_IMPORT_MAX_ATTEMPTS,
                }], ensure_ascii=False)
                job.locked_at = now if retrying else None
                job.completed_at = None if retrying else now
                job.updated_at = now
                job.save()
                if not retrying:
                    remove_encrypted_archive = True
        except Exception:
            pass
    except Exception as exc:
        try:
            with db.connection_context():
                job = FaceImportJob.get_by_id(job_id)
                job.status = 'failed'
                job.errors_json = json.dumps([{'error': str(exc)}], ensure_ascii=False)
                job.locked_at = None
                job.completed_at = datetime.now()
                job.updated_at = job.completed_at
                job.save()
                remove_encrypted_archive = True
        except Exception:
            pass
    finally:
        paths = [temporary_archive]
        if remove_encrypted_archive:
            paths.append(encrypted_path)
        for path in paths:
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


@faces_bp.route('/imports/preflight', methods=['POST'])
def preflight_face_import():
    guard = _admin_guard()
    if guard is not None:
        return guard
    uploaded = request.files.get('file')
    if uploaded is None:
        return jsonify({'success': False, 'error': '请选择ZIP导入包'}), 400
    try:
        uploaded.stream.seek(0, os.SEEK_END)
        upload_size = uploaded.stream.tell()
        uploaded.stream.seek(0)
        if upload_size > _MAX_IMPORT_UPLOAD_BYTES:
            return jsonify({'success': False, 'error': '导入包不能超过512MB'}), 413
        with zipfile.ZipFile(uploaded.stream) as archive:
            rows, image_count, errors = _inspect_face_import(archive)
            return jsonify({
                'success': not errors,
                'person_count': len(rows),
                'image_count': image_count,
                'errors': errors,
            }), 200 if not errors else 422
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, ValueError) as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400


@faces_bp.route('/imports', methods=['POST'])
def create_face_import():
    guard = _admin_guard()
    if guard is not None:
        return guard
    gallery_id = request.form.get('gallery_id', type=int)
    uploaded = request.files.get('file')
    if not gallery_id or uploaded is None:
        return jsonify({'success': False, 'error': '人脸库和ZIP导入包不能为空'}), 400
    try:
        gallery = FaceGallery.get_by_id(gallery_id)
    except FaceGallery.DoesNotExist:
        return jsonify({'success': False, 'error': '人脸库不存在'}), 404
    if not gallery.model_bundle_id:
        return jsonify({'success': False, 'error': '人脸库尚未配置模型包'}), 409

    # Reuse Werkzeug's request stream so the application never creates a
    # second plaintext archive on disk. Persistent storage below is encrypted.
    temporary = uploaded.stream
    try:
        temporary.seek(0, os.SEEK_END)
        total = temporary.tell()
        temporary.seek(0)
    except (OSError, AttributeError):
        return jsonify({'success': False, 'error': '无法读取导入包'}), 400
    if total > _MAX_IMPORT_UPLOAD_BYTES:
        return jsonify({'success': False, 'error': '导入包不能超过512MB'}), 413
    job = None
    encrypted_path = None
    temporary_encrypted_path = None
    try:
        with zipfile.ZipFile(temporary) as archive:
            rows, image_count, errors = _inspect_face_import(archive)
            if errors:
                return jsonify({
                    'success': False,
                    'error': '导入包预检未通过',
                    'errors': errors,
                }), 422
        now = datetime.now()
        import_dir = os.path.join(FACE_DATA_PATH, 'imports')
        os.makedirs(import_dir, exist_ok=True)
        encrypted_path = os.path.join(
            import_dir, f'{uuid.uuid4().hex}.zip.face'
        )
        job = FaceImportJob.create(
            gallery=gallery,
            status='uploading',
            encrypted_archive_path=encrypted_path,
            total_people=len(rows),
            total_images=image_count,
            created_at=now,
            updated_at=now,
            created_by=current_username('admin'),
        )
        temporary_encrypted_path = f'{encrypted_path}.tmp-{uuid.uuid4().hex}'
        temporary.seek(0)
        with open(temporary_encrypted_path, 'wb') as target:
            encrypt_biometric_stream(
                temporary, target, purpose=f'face-import:{job.id}'
            )
        os.replace(temporary_encrypted_path, encrypted_path)
        temporary_encrypted_path = None
        job.status = 'pending'
        job.save()
    except FaceEncryptionConfigurationError as exc:
        _remove_files((temporary_encrypted_path, encrypted_path))
        if job is not None:
            job.delete_instance()
        return jsonify({'success': False, 'error': str(exc)}), 503
    except (zipfile.BadZipFile, UnicodeDecodeError, ValueError) as exc:
        _remove_files((temporary_encrypted_path, encrypted_path))
        if job is not None:
            job.delete_instance()
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        _remove_files((temporary_encrypted_path, encrypted_path))
        if job is not None:
            job.delete_instance()
        logger.exception('创建人脸批量录入任务失败')
        return jsonify({'success': False, 'error': f'创建导入任务失败: {exc}'}), 500
    return jsonify({'success': True, 'job': _serialize_import_job(job)}), 202


@faces_bp.route('/imports/<int:job_id>', methods=['GET'])
def get_face_import(job_id):
    query = FaceImportJob.select().where(FaceImportJob.id == job_id)
    if not is_admin_user():
        query = query.where(FaceImportJob.created_by == current_username())
    job = query.first()
    if job is None:
        return jsonify({'success': False, 'error': '导入任务不存在'}), 404
    return jsonify({'success': True, 'job': _serialize_import_job(job)})


def _recover_stale_face_imports():
    _cleanup_stale_plaintext_face_imports()
    cutoff = datetime.now() - timedelta(seconds=_FACE_IMPORT_LEASE_SECONDS)
    now = datetime.now()
    stale_uploads = list(
        FaceImportJob.select().where(
            (FaceImportJob.status == 'uploading')
            & (FaceImportJob.updated_at < cutoff)
        )
    )
    for job in stale_uploads:
        path = str(job.encrypted_archive_path or '')
        if path:
            directory = os.path.dirname(path)
            prefix = os.path.basename(path)
            try:
                for name in os.listdir(directory):
                    if name == prefix or name.startswith(f'{prefix}.tmp-'):
                        candidate = os.path.join(directory, name)
                        if os.path.isfile(candidate):
                            os.remove(candidate)
            except OSError:
                logger.warning('Failed to remove stale face import upload %s', path)
        job.status = 'failed'
        job.errors_json = json.dumps(
            [{'error': '上传过程中服务中断，请重新提交'}], ensure_ascii=False
        )
        job.completed_at = now
        job.updated_at = now
        job.save()
    stale_processing = list(
        FaceImportJob.select().where(
            (FaceImportJob.status == 'processing')
            & (
                FaceImportJob.locked_at.is_null(True)
                | (FaceImportJob.locked_at < cutoff)
            )
        )
    )
    for job in stale_processing:
        retry_errors = [
            error for error in job.errors
            if isinstance(error, dict) and error.get('retrying') is True
        ]
        job.status = 'pending'
        job.processed_people = 0
        job.succeeded_people = 0
        job.failed_people = 0
        job.errors_json = json.dumps(retry_errors[:1], ensure_ascii=False)
        job.locked_at = None
        job.completed_at = None
        job.updated_at = datetime.now()
        job.save()
    return len(stale_processing) + len(stale_uploads)


def _claim_next_face_import():
    with db.connection_context():
        _recover_stale_face_imports()
        with db.atomic():
            job = (
                FaceImportJob.select()
                .where(FaceImportJob.status == 'pending')
                .order_by(FaceImportJob.id.asc())
                .first()
            )
            if job is None:
                return None
            now = datetime.now()
            updated = (
                FaceImportJob.update(
                    status='processing', locked_at=now, updated_at=now
                )
                .where(
                    (FaceImportJob.id == job.id)
                    & (FaceImportJob.status == 'pending')
                )
                .execute()
            )
            return job.id if updated else None


def _cleanup_face_import_history():
    cutoff = datetime.now() - timedelta(days=30)
    with db.connection_context():
        return (
            FaceImportJob.delete()
            .where(
                FaceImportJob.status.in_({
                    'completed', 'completed_with_errors', 'failed'
                })
                & (FaceImportJob.completed_at < cutoff)
            )
            .execute()
        )


class FaceImportWorker:
    """Durable single-consumer worker for encrypted enrollment archives."""

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name='face-import', daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    @staticmethod
    def _try_lock():
        import_dir = os.path.join(FACE_DATA_PATH, 'imports')
        os.makedirs(import_dir, exist_ok=True)
        handle = open(os.path.join(import_dir, '.worker.lock'), 'a+')
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            handle.close()
            return None

    def _run(self):
        while not self._stop_event.is_set():
            lock_handle = None
            try:
                lock_handle = self._try_lock()
                if lock_handle is None:
                    self._stop_event.wait(_FACE_IMPORT_POLL_SECONDS)
                    continue
                job_id = _claim_next_face_import()
                if job_id is None:
                    _cleanup_face_import_history()
                    self._stop_event.wait(_FACE_IMPORT_POLL_SECONDS)
                    continue
                _run_face_import(job_id)
            except Exception:
                logger.exception('Face import worker loop failed')
                self._stop_event.wait(_FACE_IMPORT_POLL_SECONDS)
            finally:
                if lock_handle is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                    lock_handle.close()


def start_face_import_worker():
    global _face_import_worker
    with _face_import_worker_lock:
        if _face_import_worker is None:
            _face_import_worker = FaceImportWorker()
        _face_import_worker.start()
        return _face_import_worker


@faces_bp.route('/events', methods=['GET'])
def list_face_events():
    query = FaceEvent.select().order_by(FaceEvent.occurred_at.desc())
    if not is_admin_user():
        query = query.where(FaceEvent.created_by == current_username())
    status = str(request.args.get('identity_status') or '').strip()
    if status:
        query = query.where(FaceEvent.identity_status == status)
    gallery_id = request.args.get('gallery_id', type=int)
    if gallery_id:
        query = query.where(FaceEvent.gallery == gallery_id)
    limit = max(1, min(200, request.args.get('limit', default=50, type=int)))
    events = []
    for event in query.limit(limit):
        events.append({
            'id': event.id,
            'video_source_id': event.video_source_id,
            'workflow_id': event.workflow_id,
            'gallery_id': event.gallery_id,
            'person_id': event.person_id,
            'person_code': event.person_code_snapshot,
            'person_name': event.person_name_snapshot,
            'track_id': event.track_id,
            'identity_status': event.identity_status,
            'similarity': event.similarity,
            'threshold': event.threshold,
            'quality': event.quality,
            'snapshot_path': event.snapshot_path,
            'liveness_status': event.liveness_status,
            'model_contract': event.model_contract,
            'inference_backend': event.inference_backend,
            'occurred_at': _iso(event.occurred_at),
            'expires_at': _iso(event.expires_at),
        })
    return jsonify({'success': True, 'events': events})


@faces_bp.route('/calibrations', methods=['POST'])
def calibrate_face_thresholds():
    guard = _admin_guard()
    if guard is not None:
        return guard
    data = request.get_json(silent=True) or {}
    gallery_id = data.get('gallery_id')
    try:
        gallery = FaceGallery.get_by_id(int(gallery_id))
    except (TypeError, ValueError, FaceGallery.DoesNotExist):
        return jsonify({'success': False, 'error': '人脸库不存在'}), 404
    index = gallery_index_cache.get(gallery.id)
    if index.template_count < 2:
        return jsonify({'success': False, 'error': '至少需要两个可用模板才能评估阈值'}), 422

    maximum = 2000
    matrix = index.matrix[:maximum]
    people = index.people[:maximum]
    similarities = matrix @ matrix.T
    left, right = np.triu_indices(len(matrix), k=1)
    pair_scores = similarities[left, right]
    person_ids = np.asarray([item[0] for item in people])
    same_person = person_ids[left] == person_ids[right]
    genuine_scores = pair_scores[same_person]
    impostor_scores = pair_scores[~same_person]
    impostor_limit = 200_000
    impostor_sampled = len(impostor_scores) > impostor_limit
    if len(impostor_scores) > impostor_limit:
        sampled_indexes = np.linspace(
            0, len(impostor_scores) - 1, impostor_limit, dtype=np.int64
        )
        impostor_scores = impostor_scores[sampled_indexes]
    if len(impostor_scores) == 0:
        return jsonify({'success': False, 'error': '至少需要两名不同人员才能评估误识率'}), 422
    try:
        target_fpir = max(
            0.00001, min(0.1, float(data.get('target_fpir') or 0.001))
        )
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'target_fpir 必须是数字'}), 400
    high = float(np.quantile(np.asarray(impostor_scores), 1.0 - target_fpir)) + 1e-4
    # Gallery validation requires 0 <= low < high. Keep enough room for a
    # valid low threshold even when every impostor cosine score is negative.
    high = max(0.01, min(0.99, high))
    if len(genuine_scores):
        genuine_floor = float(np.quantile(np.asarray(genuine_scores), 0.05))
        low = min(high - 0.01, genuine_floor)
    else:
        low = high - 0.10
    low = max(0.0, min(high - 0.01, low))
    measured_fpir = float(np.mean(impostor_scores >= high))
    measured_fnir = (
        float(np.mean(genuine_scores < low))
        if len(genuine_scores) else None
    )
    return jsonify({
        'success': True,
        'gallery_id': gallery.id,
        'suggested_low_threshold': round(low, 4),
        'suggested_high_threshold': round(high, 4),
        'target_fpir': target_fpir,
        'measured_fpir': measured_fpir,
        'measured_fnir': measured_fnir,
        'genuine_pair_count': len(genuine_scores),
        'impostor_pair_count': len(impostor_scores),
        'template_count': len(matrix),
        'sampled': index.template_count > maximum or impostor_sampled,
        'applied': False,
    })


@faces_bp.route('/events/<int:event_id>/snapshot', methods=['GET'])
def get_face_event_snapshot(event_id):
    query = FaceEvent.select().where(FaceEvent.id == event_id)
    if not is_admin_user():
        query = query.where(FaceEvent.created_by == current_username())
    event = query.first()
    if event is None or not event.snapshot_path:
        return jsonify({'success': False, 'error': '抓拍不存在'}), 404
    path = resolve_media_path(FACE_EVENT_PATH, event.snapshot_path)
    if path is None or not path.is_file():
        return jsonify({'success': False, 'error': '抓拍文件不存在'}), 404
    try:
        with open(path, 'rb') as handle:
            payload = decrypt_biometric(handle.read(), purpose='face-event-snapshot')
    except FaceEncryptionConfigurationError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 503
    except (InvalidTag, ValueError):
        return jsonify({'success': False, 'error': '事件抓拍密文校验失败'}), 422
    response = send_file(
        io.BytesIO(payload), mimetype='image/jpeg',
        download_name='face-event.jpg', max_age=0,
    )
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['Pragma'] = 'no-cache'
    return response
