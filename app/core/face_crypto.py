"""Authenticated encryption helpers for biometric images and embeddings."""

from __future__ import annotations

import base64
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


_ENVELOPE_VERSION = b'FCE1'
_NONCE_BYTES = 12
_STREAM_ENVELOPE_VERSION = b'FCS1'
_TAG_BYTES = 16


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


@lru_cache(maxsize=1)
def face_encryption_key() -> bytes:
    # Resolve at first use rather than module import so the API can still start
    # and report a clear enrollment error when the deployment secret is absent.
    key_file = os.getenv('FACE_DATA_ENCRYPTION_KEY_FILE', '').strip()
    if key_file:
        try:
            with open(key_file, 'r', encoding='utf-8') as handle:
                return _decode_key(handle.read())
        except OSError as exc:
            raise FaceEncryptionConfigurationError(
                f'无法读取人脸数据加密密钥文件: {key_file}'
            ) from exc
    return _decode_key(os.getenv('FACE_DATA_ENCRYPTION_KEY', ''))


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
