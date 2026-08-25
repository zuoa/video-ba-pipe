import json
from types import SimpleNamespace

import pytest

import app.core.face_settings as face_settings
from app.core.face_settings import (
    FaceRecognitionConfig,
    load_face_recognition_config,
    normalize_face_recognition_config,
    save_face_recognition_config,
)


def test_face_recognition_config_uses_product_defaults(monkeypatch):
    monkeypatch.setattr(
        face_settings.SystemSetting,
        "get_or_none",
        lambda *_args, **_kwargs: None,
    )

    config, source, database_available = load_face_recognition_config()

    assert config == FaceRecognitionConfig()
    assert source == "default"
    assert database_available is True


def test_face_recognition_config_loads_database_value(monkeypatch):
    record = SimpleNamespace(value=json.dumps({
        "known_retention_days": 120,
        "unknown_retention_days": 7,
        "inference_backend": "rknn",
        "require_commercial_models": True,
    }))
    monkeypatch.setattr(
        face_settings.SystemSetting,
        "get_or_none",
        lambda *_args, **_kwargs: record,
    )

    config, source, database_available = load_face_recognition_config()

    assert config.known_retention_days == 120
    assert config.unknown_retention_days == 7
    assert config.inference_backend == "rknn"
    assert config.require_commercial_models is True
    assert source == "database"
    assert database_available is True


def test_face_recognition_config_keeps_last_known_value_on_database_failure(monkeypatch):
    record = SimpleNamespace(value=json.dumps({
        "known_retention_days": 45,
        "unknown_retention_days": 5,
        "inference_backend": "auto",
        "require_commercial_models": True,
    }))
    monkeypatch.setattr(
        face_settings.SystemSetting,
        "get_or_none",
        lambda *_args, **_kwargs: record,
    )
    loaded, _source, _available = load_face_recognition_config()
    monkeypatch.setattr(
        face_settings.SystemSetting,
        "get_or_none",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    cached, source, database_available = load_face_recognition_config(
        log_failure=False
    )

    assert cached == loaded
    assert source == "cache"
    assert database_available is False


def test_normalize_face_recognition_config_bounds_corrupt_values():
    config = normalize_face_recognition_config({
        "known_retention_days": 99999,
        "unknown_retention_days": -3,
        "inference_backend": "not valid!",
        "require_commercial_models": "true",
    })

    assert config.known_retention_days == 3650
    assert config.unknown_retention_days == 0
    assert config.inference_backend == "auto"
    assert config.require_commercial_models is True


@pytest.mark.parametrize(
    "data,error",
    [
        ({}, "缺少配置项"),
        ({
            "known_retention_days": -1,
            "unknown_retention_days": 30,
            "inference_backend": "auto",
            "require_commercial_models": False,
        }, "已识别事件保留天数"),
        ({
            "known_retention_days": 90,
            "unknown_retention_days": 30,
            "inference_backend": "missing-runtime",
            "require_commercial_models": False,
        }, "当前主机不可用"),
        ({
            "known_retention_days": 90,
            "unknown_retention_days": 30,
            "inference_backend": "auto",
            "require_commercial_models": "true",
        }, "必须是布尔值"),
    ],
)
def test_save_face_recognition_config_validates(data, error, monkeypatch):
    monkeypatch.setattr(
        face_settings,
        "available_face_inference_backends",
        lambda: ["auto", "onnxruntime", "rknn"],
    )
    with pytest.raises(ValueError, match=error):
        save_face_recognition_config(data)


def test_save_face_recognition_config_persists_complete_record(monkeypatch):
    class FakeRecord:
        value = ""
        description = ""
        updated_at = None
        updated_by = ""

        def save(self):
            return 1

    record = FakeRecord()
    monkeypatch.setattr(
        face_settings.SystemSetting,
        "get_or_create",
        lambda **_kwargs: (record, True),
    )
    monkeypatch.setattr(
        face_settings,
        "available_face_inference_backends",
        lambda: ["auto", "onnxruntime", "rknn"],
    )

    config = save_face_recognition_config({
        "known_retention_days": 180,
        "unknown_retention_days": 14,
        "inference_backend": "onnxruntime",
        "require_commercial_models": True,
    }, updated_by="face-admin")

    assert json.loads(record.value) == config.to_dict()
    assert record.updated_by == "face-admin"


def test_available_face_backends_use_host_runnable_choices(monkeypatch):
    monkeypatch.setattr(
        'app.core.face_inference.available_face_runtimes',
        lambda: (['onnxruntime'], []),
    )

    assert face_settings.available_face_inference_backends() == [
        'auto', 'onnxruntime',
    ]


def test_available_face_backends_can_use_worker_capabilities():
    assert face_settings.available_face_inference_backends({
        'available_runtimes': ['onnxruntime-cuda', 'torchscript'],
        'plugin_errors': [],
    }) == ['auto', 'onnxruntime-cuda', 'torchscript']


def test_save_rejects_uploadable_but_host_unavailable_backend(monkeypatch):
    monkeypatch.setattr(
        face_settings,
        'available_face_inference_backends',
        lambda: ['auto', 'onnxruntime'],
    )

    with pytest.raises(ValueError, match='当前主机不可用'):
        save_face_recognition_config({
            'known_retention_days': 90,
            'unknown_retention_days': 30,
            'inference_backend': 'rknn',
            'require_commercial_models': False,
        })
