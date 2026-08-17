"""S3-compatible object storage adapter for outbound alert media."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:  # pragma: no cover - optional in lightweight tests
    boto3 = None
    BotoConfig = None

from app.core.public_media_config import PublicMediaConfig


@dataclass(frozen=True)
class UploadedObject:
    object_key: str
    url: str
    expires_at: datetime


def _client(config: PublicMediaConfig):
    if boto3 is None:
        raise RuntimeError("boto3 未安装，无法使用对象存储")
    addressing_style = "path" if config.object_storage_force_path_style else "auto"
    return boto3.client(
        "s3",
        endpoint_url=config.object_storage_endpoint_url,
        region_name=config.object_storage_region or None,
        aws_access_key_id=config.object_storage_access_key_id,
        aws_secret_access_key=config.object_storage_secret_access_key,
        verify=config.object_storage_verify_ssl,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": addressing_style}),
    )


def build_alert_object_key(
    config: PublicMediaConfig,
    *,
    node_id: str,
    external_alert_id: str,
    occurred_at: datetime,
) -> str:
    prefix = config.object_storage_key_prefix.strip("/")
    clean_node_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in node_id)
    clean_alert_id = "".join(c if c.isalnum() or c in "-_" else "-" for c in external_alert_id)
    dated = occurred_at.strftime("%Y/%m/%d")
    tail = f"{clean_node_id}/{dated}/{clean_alert_id}.jpg"
    return f"{prefix}/{tail}" if prefix else tail


def upload_alert_image(
    config: PublicMediaConfig,
    *,
    local_path: str,
    object_key: str,
) -> UploadedObject:
    client = _client(config)
    with open(local_path, "rb") as image_file:
        client.upload_fileobj(
            image_file,
            config.object_storage_bucket,
            object_key,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
    ttl_seconds = config.object_storage_presigned_url_ttl_hours * 3600
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": config.object_storage_bucket, "Key": object_key},
        ExpiresIn=ttl_seconds,
    )
    return UploadedObject(
        object_key=object_key,
        url=url,
        expires_at=datetime.now() + timedelta(seconds=ttl_seconds),
    )


def test_object_storage(config: PublicMediaConfig) -> UploadedObject:
    client = _client(config)
    prefix = config.object_storage_key_prefix.strip("/")
    object_key = "/".join(
        part for part in (prefix, ".connection-test", f"{uuid.uuid4().hex}.txt") if part
    )
    payload = b"video-ba-pipe object storage connection test"
    try:
        client.upload_fileobj(
            io.BytesIO(payload),
            config.object_storage_bucket,
            object_key,
            ExtraArgs={"ContentType": "text/plain"},
        )
        client.head_object(Bucket=config.object_storage_bucket, Key=object_key)
        ttl_seconds = min(config.object_storage_presigned_url_ttl_hours * 3600, 3600)
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": config.object_storage_bucket, "Key": object_key},
            ExpiresIn=ttl_seconds,
        )
        return UploadedObject(
            object_key=object_key,
            url=url,
            expires_at=datetime.now() + timedelta(seconds=ttl_seconds),
        )
    finally:
        try:
            client.delete_object(Bucket=config.object_storage_bucket, Key=object_key)
        except Exception:
            pass
