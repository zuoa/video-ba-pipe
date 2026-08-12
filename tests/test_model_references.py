import json
from types import SimpleNamespace

from app.web.api.models import _algorithm_references_model


def _algorithm(models, ext_config=None):
    return SimpleNamespace(
        config_dict={"models": models},
        ext_config=ext_config or {},
    )


def test_dictionary_model_name_reference_is_detected():
    target = SimpleNamespace(id=7, name="PersonModel")
    algorithm = _algorithm({"person": "PersonModel"})

    assert _algorithm_references_model(algorithm, target.id, target_model=target) is True


def test_dictionary_model_object_reference_is_detected():
    target = SimpleNamespace(id=7, name="PersonModel")
    algorithm = _algorithm({"person": {"name": "PersonModel", "confidence": 0.5}})

    assert _algorithm_references_model(algorithm, target.id, target_model=target) is True


def test_ext_config_model_ids_reference_is_detected():
    target = SimpleNamespace(id=7, name="PersonModel")
    algorithm = _algorithm([], {"model_ids": json.dumps([3, 7])})

    assert _algorithm_references_model(algorithm, target.id, target_model=target) is True


def test_cascade_stage_model_reference_is_detected():
    target = SimpleNamespace(id=7, name="SmokeModel")
    algorithm = _algorithm([], {
        "algorithm_type": "cascade",
        "cascade_config": {
            "stages": [
                {"id": "person", "model_id": 3},
                {"id": "smoke", "model_id": 7},
            ]
        },
    })

    assert _algorithm_references_model(algorithm, target.id, target_model=target) is True


def test_combination_detector_model_reference_is_detected():
    target = SimpleNamespace(id=7, name="HelmetModel")
    algorithm = _algorithm([], {
        "algorithm_type": "cascade",
        "cascade_config": {
            "version": 2,
            "nodes": [
                {"id": "frame", "type": "frame"},
                {"id": "head", "type": "detector", "model_id": 3},
                {"id": "helmet", "type": "detector", "model_id": 7},
            ],
        },
    })

    assert _algorithm_references_model(algorithm, target.id, target_model=target) is True
