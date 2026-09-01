"""Person ReID model bundle, artifact, and verified download APIs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import requests
import numpy as np
from flask import Blueprint, jsonify, request
from peewee import IntegrityError
from werkzeug.utils import secure_filename

from app.config import (
    HF_DOWNLOAD_TIMEOUT_SECONDS,
    HF_MIRROR_ENDPOINT,
    HF_USE_MIRROR,
    REID_MODEL_CATALOG_PATH,
    REID_MODEL_PATH,
)
from app.core.database_models import (
    ReIdModelArtifact,
    ReIdModelBundle,
    ReIdModelImportJob,
    db,
)
from app.core.reid_inference import (
    SUPPORTED_REID_RUNTIMES,
    runtime_capabilities,
    select_reid_artifact,
    verified_reid_artifact,
    ReIdWorkerBackend,
)
from app.web.api.auth import current_username, require_admin, require_auth


reid_bp = Blueprint('reid', __name__, url_prefix='/api/reid')
_MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
_IMPORT_THREADS: dict[int, threading.Thread] = {}
_IMPORT_LOCK = threading.Lock()
_IMPORT_LEASE_SECONDS = max(300, int(HF_DOWNLOAD_TIMEOUT_SECONDS) + 60)


@reid_bp.before_request
def enforce_authentication():
    return require_auth(lambda: None)()


def _admin_guard():
    return require_admin(lambda: None)()


def _json_object(value, name='metadata'):
    if value in (None, ''):
        return {}
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{name} 必须是 JSON 对象') from exc
    if not isinstance(result, dict):
        raise ValueError(f'{name} 必须是 JSON 对象')
    return result


def _serialize_bundle(bundle):
    return {
        'id': bundle.id,
        'portable_id': bundle.portable_id,
        'name': bundle.name,
        'version': bundle.version,
        'contract_id': bundle.contract_id,
        'target_type': bundle.target_type,
        'embedding_dimension': bundle.embedding_dimension,
        'input_size': bundle.input_size,
        'preprocess': bundle.preprocess,
        'distance_metric': bundle.distance_metric,
        'default_similarity_threshold': bundle.default_similarity_threshold,
        'license_name': bundle.license_name,
        'license_url': bundle.license_url,
        'commercial_use_allowed': bundle.commercial_use_allowed,
        'enabled': bundle.enabled,
        'artifacts': [
            {
                'id': artifact.id,
                'runtime': artifact.runtime,
                'architecture': artifact.architecture,
                'device': artifact.device,
                'filename': artifact.filename,
                'file_size': artifact.file_size,
                'artifact_sha256': artifact.artifact_sha256,
                'metadata': artifact.metadata,
                'enabled': artifact.enabled,
            }
            for artifact in bundle.artifacts.order_by(ReIdModelArtifact.runtime)
        ],
    }


def _serialize_job(job):
    return {
        'id': job.id,
        'bundle_id': job.bundle_id,
        'artifact_id': job.artifact_id,
        'status': job.status,
        'progress': job.progress,
        'error': job.error_message,
        'created_at': job.created_at.isoformat(),
        'updated_at': job.updated_at.isoformat(),
    }


def _validate_platform(runtime, architecture, device):
    runtime = str(runtime or '').strip().lower()
    if runtime not in SUPPORTED_REID_RUNTIMES:
        raise ValueError('ReID 推理运行时无效')
    # CUDA is an execution provider for the same portable ONNX artifact. The
    # device tag distinguishes it from the CPU copy, matching face bundles.
    if runtime == 'onnxruntime-cuda':
        runtime = 'onnxruntime'
        if str(device or 'any').strip().lower() in {'any', 'all', '*'}:
            device = 'cuda'
    architecture = str(architecture or 'any').strip().lower()
    device = str(device or 'any').strip().lower()
    pattern = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
    if not pattern.fullmatch(architecture) or not pattern.fullmatch(device):
        raise ValueError('架构或设备标签格式无效')
    return runtime, architecture, device


def _extension_for_runtime(runtime):
    return {
        'onnxruntime': {'.onnx'}, 'onnxruntime-cuda': {'.onnx'},
        'tensorrt': {'.onnx'}, 'rknn': {'.rknn'},
        'torchscript': {'.pt', '.pth'},
    }[runtime]


def _publish_file(bundle, source_path, *, filename, runtime, architecture, device,
                  metadata, expected_sha256=None):
    extension = Path(filename).suffix.lower()
    if extension not in _extension_for_runtime(runtime):
        raise ValueError(f'{runtime} 不支持 {extension or "无扩展名"} 制品')
    digest = hashlib.sha256()
    size = 0
    with open(source_path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            size += len(chunk)
            if size > _MAX_ARTIFACT_BYTES:
                raise ValueError('ReID 模型制品不能超过 1GB')
            digest.update(chunk)
    actual_sha256 = digest.hexdigest()
    if expected_sha256 and actual_sha256 != str(expected_sha256).lower():
        raise ValueError('下载文件 SHA-256 与目录声明不一致')
    target_dir = Path(REID_MODEL_PATH) / str(bundle.id) / runtime
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f'{uuid.uuid4().hex}{extension}'
    shutil.move(str(source_path), target_path)
    old_path = None
    try:
        with db.atomic():
            artifact = ReIdModelArtifact.get_or_none(
                (ReIdModelArtifact.bundle == bundle.id)
                & (ReIdModelArtifact.runtime == runtime)
                & (ReIdModelArtifact.architecture == architecture)
                & (ReIdModelArtifact.device == device)
            )
            if artifact is None:
                artifact = ReIdModelArtifact.create(
                    bundle=bundle, runtime=runtime, architecture=architecture,
                    device=device, filename=filename, file_path=str(target_path),
                    file_size=size, artifact_sha256=actual_sha256,
                    metadata_json=json.dumps(metadata, ensure_ascii=False),
                    enabled=True, created_at=datetime.now(),
                )
            else:
                old_path = artifact.file_path
                artifact.filename = filename
                artifact.file_path = str(target_path)
                artifact.file_size = size
                artifact.artifact_sha256 = actual_sha256
                artifact.metadata_json = json.dumps(metadata, ensure_ascii=False)
                artifact.enabled = True
                artifact.save()
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    if old_path and old_path != str(target_path):
        try:
            Path(old_path).unlink(missing_ok=True)
        except OSError:
            pass
    return artifact


def _load_catalog():
    if not REID_MODEL_CATALOG_PATH:
        return []
    try:
        value = json.loads(Path(REID_MODEL_CATALOG_PATH).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _normalize_import_source(source):
    source = dict(source or {})
    source_type = str(source.get('type') or 'huggingface').lower()
    if source_type == 'catalog':
        catalog_id = str(source.get('catalog_id') or '')
        entry = next((item for item in _load_catalog() if item.get('id') == catalog_id), None)
        if entry is None:
            raise ValueError('精选目录中不存在该模型制品')
        # Persist the expanded platform contract. A worker may run later or in
        # another process where the catalog entry is no longer available.
        source = {**entry, 'catalog_id': catalog_id, 'type': 'huggingface'}
    if source_type not in {'huggingface', 'catalog'}:
        raise ValueError('ReID 下载仅支持 Hugging Face 或精选目录')
    return source


def _resolve_import_source(source):
    source = _normalize_import_source(source)
    repo_id = str(source.get('repo_id') or '').strip()
    filename = str(source.get('filename') or '').strip().lstrip('/')
    revision = str(source.get('revision') or '').strip()
    expected = str(source.get('sha256') or '').strip().lower()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo_id):
        raise ValueError('Hugging Face repo_id 格式无效')
    if not filename or '..' in Path(filename).parts:
        raise ValueError('Hugging Face 文件路径无效')
    if not revision or revision.lower() in {'main', 'master'}:
        raise ValueError('生产下载必须固定 revision，不能使用 main/master')
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('生产下载必须提供 64 位 SHA-256')
    endpoint = HF_MIRROR_ENDPOINT if source.get('use_mirror', HF_USE_MIRROR) else 'https://huggingface.co'
    from urllib.parse import quote
    url = f'{endpoint}/{quote(repo_id, safe="/")}/resolve/{quote(revision, safe="")}/{quote(filename, safe="/")}'
    return source, url, filename, expected


def _run_import(job_id):
    temp_path = None
    try:
        updated = ReIdModelImportJob.update(
            status='running', progress=1, updated_at=datetime.now()
        ).where(
            (ReIdModelImportJob.id == job_id)
            & (ReIdModelImportJob.status == 'pending')
        ).execute()
        if updated != 1:
            return
        job = ReIdModelImportJob.get_by_id(job_id)
        source = job.source
        source, url, filename, expected = _resolve_import_source(source)
        fd, temp_path = tempfile.mkstemp(prefix='reid-import-', suffix=Path(filename).suffix)
        os.close(fd)
        headers = {'User-Agent': 'video-ba-pipe-reid-import/1.0'}
        token = str(source.get('hf_token') or os.getenv('HF_TOKEN') or '').strip()
        if token:
            headers['Authorization'] = f'Bearer {token}'
        downloaded = 0
        with requests.get(
            url, headers=headers, stream=True,
            timeout=(30, HF_DOWNLOAD_TIMEOUT_SECONDS), allow_redirects=True,
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get('Content-Length') or 0)
            with open(temp_path, 'wb') as output:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > _MAX_ARTIFACT_BYTES:
                        raise ValueError('ReID 模型制品不能超过 1GB')
                    output.write(chunk)
                    progress = min(90, int(downloaded * 90 / total)) if total else 10
                    ReIdModelImportJob.update(
                        progress=progress, updated_at=datetime.now()
                    ).where(ReIdModelImportJob.id == job_id).execute()
        runtime, architecture, device = _validate_platform(
            source.get('runtime'), source.get('architecture'), source.get('device')
        )
        artifact = _publish_file(
            job.bundle, temp_path, filename=secure_filename(Path(filename).name),
            runtime=runtime, architecture=architecture, device=device,
            metadata=_json_object(source.get('metadata')), expected_sha256=expected,
        )
        temp_path = None
        ReIdModelImportJob.update(
            status='completed', progress=100, artifact=artifact,
            error_message=None, updated_at=datetime.now(),
        ).where(ReIdModelImportJob.id == job_id).execute()
    except Exception as exc:
        ReIdModelImportJob.update(
            status='failed', error_message=f'{type(exc).__name__}: {exc}',
            updated_at=datetime.now(),
        ).where(ReIdModelImportJob.id == job_id).execute()
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        with _IMPORT_LOCK:
            _IMPORT_THREADS.pop(job_id, None)


def _ensure_import_worker(job_id):
    with _IMPORT_LOCK:
        current = _IMPORT_THREADS.get(job_id)
        if current and current.is_alive():
            return
        worker = threading.Thread(target=_run_import, args=(job_id,), daemon=True)
        _IMPORT_THREADS[job_id] = worker
        worker.start()


def _recover_stale_import_job(job):
    if job.status != 'running':
        return job
    cutoff = datetime.now() - timedelta(seconds=_IMPORT_LEASE_SECONDS)
    if job.updated_at and job.updated_at > cutoff:
        return job
    recovered = ReIdModelImportJob.update(
        status='pending',
        error_message='导入进程中断，任务已自动恢复',
        updated_at=datetime.now(),
    ).where(
        (ReIdModelImportJob.id == job.id)
        & (ReIdModelImportJob.status == 'running')
        & (ReIdModelImportJob.updated_at <= cutoff)
    ).execute()
    return ReIdModelImportJob.get_by_id(job.id) if recovered else job


@reid_bp.get('/model-catalog')
def model_catalog():
    return jsonify({'success': True, 'entries': _load_catalog()})


@reid_bp.route('/model-bundles', methods=['GET', 'POST'])
def model_bundles():
    if request.method == 'GET':
        bundles = ReIdModelBundle.select().order_by(ReIdModelBundle.updated_at.desc())
        return jsonify({'success': True, 'bundles': [_serialize_bundle(item) for item in bundles]})
    guard = _admin_guard()
    if guard is not None:
        return guard
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    contract_id = str(data.get('contract_id') or '').strip()
    if not name or not contract_id:
        return jsonify({'success': False, 'error': '名称和模型契约不能为空'}), 400
    try:
        dimension = int(data.get('embedding_dimension') or 512)
        threshold = float(data.get('default_similarity_threshold') or 0.75)
        preprocess = _json_object(data.get('preprocess'), 'preprocess')
        if not 32 <= dimension <= 4096 or not 0 <= threshold <= 1:
            raise ValueError('特征维度或阈值超出范围')
        bundle = ReIdModelBundle.create(
            name=name, version=str(data.get('version') or 'v1.0'),
            contract_id=contract_id, target_type='person',
            embedding_dimension=dimension,
            input_size=str(data.get('input_size') or '256x128'),
            preprocess_json=json.dumps(preprocess, ensure_ascii=False),
            distance_metric='cosine', default_similarity_threshold=threshold,
            license_name=str(data.get('license_name') or '').strip() or None,
            license_url=str(data.get('license_url') or '').strip() or None,
            commercial_use_allowed=bool(data.get('commercial_use_allowed', False)),
            enabled=bool(data.get('enabled', True)), created_at=datetime.now(),
            updated_at=datetime.now(), created_by=current_username('admin'),
        )
    except (ValueError, IntegrityError) as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    return jsonify({'success': True, 'bundle': _serialize_bundle(bundle)}), 201


@reid_bp.route('/model-bundles/<int:bundle_id>', methods=['PATCH', 'DELETE'])
def mutate_bundle(bundle_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    bundle = ReIdModelBundle.get_or_none(ReIdModelBundle.id == bundle_id)
    if bundle is None:
        return jsonify({'success': False, 'error': 'ReID 模型包不存在'}), 404
    if request.method == 'DELETE':
        paths = [item.file_path for item in bundle.artifacts]
        bundle.delete_instance(recursive=True)
        for path in paths:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        return jsonify({'success': True})
    data = request.get_json(silent=True) or {}
    for key in ('name', 'version', 'license_name', 'license_url'):
        if key in data:
            setattr(bundle, key, str(data[key]).strip() or None)
    if 'enabled' in data:
        bundle.enabled = bool(data['enabled'])
    if 'commercial_use_allowed' in data:
        bundle.commercial_use_allowed = bool(data['commercial_use_allowed'])
    if 'default_similarity_threshold' in data:
        value = float(data['default_similarity_threshold'])
        if not 0 <= value <= 1:
            return jsonify({'success': False, 'error': '阈值超出范围'}), 400
        bundle.default_similarity_threshold = value
    bundle.updated_at = datetime.now()
    bundle.save()
    return jsonify({'success': True, 'bundle': _serialize_bundle(bundle)})


@reid_bp.post('/model-bundles/<int:bundle_id>/artifacts')
def upload_artifact(bundle_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    bundle = ReIdModelBundle.get_or_none(ReIdModelBundle.id == bundle_id)
    uploaded = request.files.get('file')
    if bundle is None:
        return jsonify({'success': False, 'error': 'ReID 模型包不存在'}), 404
    if uploaded is None:
        return jsonify({'success': False, 'error': '请选择模型制品'}), 400
    temp_path = None
    try:
        runtime, architecture, device = _validate_platform(
            request.form.get('runtime'), request.form.get('architecture'), request.form.get('device')
        )
        fd, temp_path = tempfile.mkstemp(prefix='reid-upload-', suffix=Path(uploaded.filename).suffix)
        os.close(fd)
        uploaded.save(temp_path)
        artifact = _publish_file(
            bundle, temp_path, filename=secure_filename(uploaded.filename),
            runtime=runtime, architecture=architecture, device=device,
            metadata=_json_object(request.form.get('metadata')),
            expected_sha256=request.form.get('sha256') or None,
        )
        temp_path = None
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)
    return jsonify({'success': True, 'artifact_id': artifact.id, 'bundle': _serialize_bundle(bundle)}), 201


@reid_bp.post('/model-bundles/<int:bundle_id>/imports')
def create_import(bundle_id):
    guard = _admin_guard()
    if guard is not None:
        return guard
    bundle = ReIdModelBundle.get_or_none(ReIdModelBundle.id == bundle_id)
    if bundle is None:
        return jsonify({'success': False, 'error': 'ReID 模型包不存在'}), 404
    source = dict(request.get_json(silent=True) or {})
    # Access tokens are process secrets, not durable job metadata.
    source.pop('hf_token', None)
    try:
        source, _url, _filename, _expected = _resolve_import_source(source)
        _validate_platform(source.get('runtime'), source.get('architecture'), source.get('device'))
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    job = ReIdModelImportJob.create(
        bundle=bundle, status='pending', source_json=json.dumps(source, ensure_ascii=False),
        progress=0, created_at=datetime.now(), updated_at=datetime.now(),
        created_by=current_username('admin'),
    )
    _ensure_import_worker(job.id)
    return jsonify({'success': True, 'job': _serialize_job(job)}), 202


@reid_bp.post('/model-bundles/<int:bundle_id>/validate')
def validate_bundle(bundle_id):
    """Load, warm up, and verify the embedding contract on this host."""
    guard = _admin_guard()
    if guard is not None:
        return guard
    bundle = ReIdModelBundle.get_or_none(ReIdModelBundle.id == bundle_id)
    if bundle is None:
        return jsonify({'success': False, 'error': 'ReID 模型包不存在'}), 404
    data = request.get_json(silent=True) or {}
    backend = None
    try:
        backend = ReIdWorkerBackend(
            bundle, str(data.get('runtime') or 'auto'),
            {'reid_boxes': [[8, 4, 120, 252]], 'reid_min_box_height': 16},
        )
        _detections, details, metadata = backend.infer(
            np.zeros((256, 128, 3), dtype=np.uint8)
        )
        if len(details) != 1:
            raise ValueError('ReID 试运行未返回单个 embedding')
        dimension = len(details[0].get('embedding') or [])
        if dimension != int(bundle.embedding_dimension):
            raise ValueError(
                f'ReID 输出维度不匹配: expected={bundle.embedding_dimension}, actual={dimension}'
            )
        return jsonify({
            'success': True, 'ready': True, 'runtime': metadata.get('backend'),
            'model_contract': metadata.get('model_contract'),
            'embedding_dimension': dimension,
            'artifact_hash': metadata.get('artifact_hash'),
            'startup_time_ms': backend.startup_time_ms,
            'inference_time_ms': metadata.get('inference_time_ms'),
        })
    except Exception as exc:
        return jsonify({
            'success': False, 'ready': False,
            'error': f'{type(exc).__name__}: {exc}',
        }), 400
    finally:
        if backend is not None:
            backend.cleanup()


@reid_bp.get('/imports/<int:job_id>')
def get_import(job_id):
    job = ReIdModelImportJob.get_or_none(ReIdModelImportJob.id == job_id)
    if job is None:
        return jsonify({'success': False, 'error': '导入任务不存在'}), 404
    job = _recover_stale_import_job(job)
    if job.status == 'pending':
        _ensure_import_worker(job.id)
    return jsonify({'success': True, 'job': _serialize_job(job)})


@reid_bp.get('/runtime')
def runtime_info():
    capabilities = runtime_capabilities()
    available = []
    for bundle in ReIdModelBundle.select().where(ReIdModelBundle.enabled):
        try:
            runtime, artifact, _ = select_reid_artifact(bundle, 'auto', capabilities)
            verified_reid_artifact(bundle, artifact, runtime)
            available.append({'bundle_id': bundle.id, 'runtime': runtime})
        except Exception as exc:
            available.append({'bundle_id': bundle.id, 'error': str(exc)})
    return jsonify({'success': True, 'capabilities': capabilities, 'bundles': available})
