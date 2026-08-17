from urllib.parse import parse_qs, urlparse

import pytest

from app.core.public_media_config import (
    PublicMediaConfig,
    add_public_media_urls_to_detection_images,
    build_public_media_url,
    normalize_public_media_config,
    verify_public_media_signature,
)


def test_environment_base_url_is_used_when_database_override_is_empty(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://video.example.com/root/")
    config = normalize_public_media_config(
        {
            "public_base_url_override": "",
            "sign_media_urls": False,
            "media_url_ttl_hours": 24,
        }
    )

    assert config.public_base_url == "https://video.example.com/root"
    assert config.public_base_url_override == ""
    assert config.config_source == "environment"


def test_database_override_has_priority_and_is_validated(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://env.example")
    config = normalize_public_media_config({"public_base_url_override": "https://db.example/media/"})
    assert config.public_base_url == "https://db.example/media"
    assert config.config_source == "database"

    with pytest.raises(ValueError, match="公共访问地址"):
        normalize_public_media_config({"public_base_url_override": "ftp://bad.example"})


def test_signed_media_url_can_be_verified_and_expires(monkeypatch):
    monkeypatch.setenv("MEDIA_URL_SIGNING_SECRET", "test-signing-secret")
    config = PublicMediaConfig(
        public_base_url="https://video.example.com/base",
        sign_media_urls=True,
        media_url_ttl_hours=24,
    )
    url = build_public_media_url("image", "gate/frame 1.jpg", config=config, now=1000)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.path == "/base/api/image/frames/gate/frame%201.jpg"
    assert query["expires"] == [str(1000 + 24 * 3600)]
    assert verify_public_media_signature(
        "/api/image/frames/gate/frame 1.jpg",
        query["expires"][0],
        query["signature"][0],
        config=config,
        now=1001,
    )
    assert not verify_public_media_signature(
        "/api/image/frames/gate/other.jpg",
        query["expires"][0],
        query["signature"][0],
        config=config,
        now=1001,
    )
    assert not verify_public_media_signature(
        "/api/image/frames/gate/frame 1.jpg",
        query["expires"][0],
        query["signature"][0],
        config=config,
        now=1000 + 24 * 3600 + 1,
    )


def test_signing_can_be_disabled_for_compatibility():
    config = PublicMediaConfig(public_base_url="", sign_media_urls=False)
    url = build_public_media_url("video", "gate/alert.mp4", config=config)
    assert url == "/api/video/gate/alert.mp4"
    assert verify_public_media_signature("/api/video/gate/alert.mp4", None, None, config=config)


def test_detection_images_are_enriched_and_container_type_is_preserved():
    config = PublicMediaConfig(
        public_base_url="https://video.example.com",
        sign_media_urls=False,
    )
    raw_value = '[{"image_path":"gate/result.jpg","image_ori_path":"gate/original.jpg"}]'

    enriched_json = add_public_media_urls_to_detection_images(raw_value, config=config)
    enriched_list = add_public_media_urls_to_detection_images(["gate/other.jpg"], config=config)

    assert isinstance(enriched_json, str)
    assert '"image_url": "https://video.example.com/api/image/frames/gate/result.jpg"' in enriched_json
    assert '"image_ori_url": "https://video.example.com/api/image/frames/gate/original.jpg"' in enriched_json
    assert enriched_list == [{
        "image_path": "gate/other.jpg",
        "image_url": "https://video.example.com/api/image/frames/gate/other.jpg",
    }]


def test_delivery_modes_default_to_url_and_preserve_object_storage_secret():
    default_config = normalize_public_media_config({})
    assert default_config.delivery_mode == "url"
    assert default_config.inline_max_bytes == 512 * 1024

    object_config = normalize_public_media_config(
        {
            "delivery_mode": "object_storage",
            "object_storage": {
                "endpoint_url": "https://s3.example.com",
                "bucket": "alerts",
                "access_key_id": "key-id",
                "secret_access_key": "",
            },
        },
        existing_secret="saved-secret",
    )
    assert object_config.object_storage_secret_access_key == "saved-secret"
    public_dict = object_config.to_dict(include_secret=False)
    assert public_dict["object_storage"]["secret_access_key"] == ""
    assert public_dict["object_storage"]["secret_configured"] is True
