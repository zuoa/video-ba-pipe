"""Authenticated encryption helpers for biometric images and embeddings."""

from __future__ import annotations

import base64
import os
import tempfile
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


_ENVELOPE_VERSION = b'FCE1'
_NONCE_BYTES = 12
_STREAM_ENVELOPE_VERSION = b'FCS1'
_TAG_BYTES = 16
_MANAGED_KEY_RELATIVE_PATH = ('secrets', 'face-data.key')


class FaceEncryptionConfigurationError(RuntimeError):
    pass


def _decode_key(value: str) -> bytes:
    raw = value.strip()
    if not raw:
        raise FaceEncryptionConfigurationError('人脸数据加密密钥未配置')
    try:
        key = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
    except Exception as exc:
        raise FaceEncryptionConfigurationError('人脸数据加密密钥不是有效的 Base64') from exc
    if len(key) != 32:
        raise FaceEncryptionConfigurationError('人脸数据加密密钥必须是 32 字节')
    return key


def _read_key_file(path: str) -> bytes:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return _decode_key(handle.read())
    except OSError as exc:
        raise FaceEncryptionConfigurationError(
            f'无法读取人脸数据加密密钥文件: {path}'
        ) from exc


def managed_face_encryption_key_file() -> str:
    """Return the shared persistent fallback used by one-click setup."""
    from app.config import FACE_DATA_PATH

    return os.path.join(FACE_DATA_PATH, *_MANAGED_KEY_RELATIVE_PATH)


@lru_cache(maxsize=1)
def face_encryption_key() -> bytes:
    # Resolve at first use rather than module import so the API can still start
    # and report a clear enrollment error when the deployment secret is absent.
    key_file = os.getenv('FACE_DATA_ENCRYPTION_KEY_FILE', '').strip()
    if key_file:
        return _read_key_file(key_file)
    key_value = os.getenv('FACE_DATA_ENCRYPTION_KEY', '').strip()
    if key_value:
        return _decode_key(key_value)
    managed_key_file = managed_face_encryption_key_file()
    if os.path.exists(managed_key_file):
        return _read_key_file(managed_key_file)
    return _decode_key('')


def generate_face_encryption_key() -> tuple[bool, str]:
    """Create a persistent 256-bit key without exposing it through the API.

    Returns ``(created, source)``. The exclusive hard-link publish keeps the
    destination atomic across multiple Gunicorn processes.
    """
    face_encryption_key.cache_clear()
    try:
        face_encryption_key()
        source = (
            'configured_file'
            if os.getenv('FACE_DATA_ENCRYPTION_KEY_FILE', '').strip()
            else 'environment'
            if os.getenv('FACE_DATA_ENCRYPTION_KEY', '').strip()
            else 'managed_file'
        )
        return False, source
    except FaceEncryptionConfigurationError:
        pass

    configured_key_file = os.getenv('FACE_DATA_ENCRYPTION_KEY_FILE', '').strip()
    if (
        not configured_key_file
        and os.getenv('FACE_DATA_ENCRYPTION_KEY', '').strip()
    ):
        raise FaceEncryptionConfigurationError(
            '环境变量 FACE_DATA_ENCRYPTION_KEY 已配置但无效，请先修正或清除'
        )

    target_path = configured_key_file or managed_face_encryption_key_file()
    parent = os.path.dirname(os.path.abspath(target_path))
    os.makedirs(parent, mode=0o700, exist_ok=True)

    encoded_key = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=') + b'\n'
    descriptor, temporary_path = tempfile.mkstemp(
        prefix='.face-data-key-', dir=parent
    )
    published = False
    try:
        os.fchmod(descriptor, 0o400)
        with os.fdopen(descriptor, 'wb') as handle:
            descriptor = -1
            handle.write(encoded_key)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, target_path)
            published = True
        except FileExistsError:
            # Another API worker may have completed the same request first.
            published = False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass

    face_encryption_key.cache_clear()
    try:
        face_encryption_key()
    except FaceEncryptionConfigurationError as exc:
        if not published:
            raise FaceEncryptionConfigurationError(
                '人脸数据加密密钥文件已存在，但内容无效或不可读'
            ) from exc
        raise
    return published, 'configured_file' if configured_key_file else 'managed_file'


def encryption_ready() -> bool:
    try:
        face_encryption_key()
        return True
    except FaceEncryptionConfigurationError:
        return False


def encrypt_biometric(payload: bytes, *, purpose: str) -> bytes:
    if not isinstance(payload, bytes) or not payload:
        raise ValueError('待加密的人脸数据不能为空')
    nonce = os.urandom(_NONCE_BYTES)
    aad = purpose.encode('utf-8')
    ciphertext = AESGCM(face_encryption_key()).encrypt(nonce, payload, aad)
    return _ENVELOPE_VERSION + nonce + ciphertext


def decrypt_biometric(envelope: bytes, *, purpose: str) -> bytes:
    if not isinstance(envelope, bytes) or not envelope.startswith(_ENVELOPE_VERSION):
        raise ValueError('不支持的人脸数据密文格式')
    nonce_start = len(_ENVELOPE_VERSION)
    nonce_end = nonce_start + _NONCE_BYTES
    if len(envelope) <= nonce_end:
        raise ValueError('人脸数据密文不完整')
    return AESGCM(face_encryption_key()).decrypt(
        envelope[nonce_start:nonce_end],
        envelope[nonce_end:],
        purpose.encode('utf-8'),
    )


def encrypt_biometric_stream(source, target, *, purpose: str, chunk_size: int = 1024 * 1024):
    """Encrypt a file-like stream without retaining the whole archive in memory."""
    nonce = os.urandom(_NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(face_encryption_key()), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(purpose.encode('utf-8'))
    target.write(_STREAM_ENVELOPE_VERSION)
    target.write(nonce)
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            break
        target.write(encryptor.update(chunk))
    target.write(encryptor.finalize())
    target.write(encryptor.tag)


def decrypt_biometric_stream(source, target, *, purpose: str, chunk_size: int = 1024 * 1024):
    header = source.read(len(_STREAM_ENVELOPE_VERSION))
    if header != _STREAM_ENVELOPE_VERSION:
        raise ValueError('不支持的人脸导入包密文格式')
    nonce = source.read(_NONCE_BYTES)
    source.seek(0, os.SEEK_END)
    total_size = source.tell()
    ciphertext_start = len(_STREAM_ENVELOPE_VERSION) + _NONCE_BYTES
    ciphertext_end = total_size - _TAG_BYTES
    if ciphertext_end <= ciphertext_start:
        raise ValueError('人脸导入包密文不完整')
    source.seek(ciphertext_end)
    tag = source.read(_TAG_BYTES)
    decryptor = Cipher(
        algorithms.AES(face_encryption_key()), modes.GCM(nonce, tag)
    ).decryptor()
    decryptor.authenticate_additional_data(purpose.encode('utf-8'))
    source.seek(ciphertext_start)
    remaining = ciphertext_end - ciphertext_start
    while remaining > 0:
        chunk = source.read(min(chunk_size, remaining))
        if not chunk:
            raise ValueError('人脸导入包密文不完整')
        remaining -= len(chunk)
        target.write(decryptor.update(chunk))
    target.write(decryptor.finalize())
