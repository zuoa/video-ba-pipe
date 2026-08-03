from pathlib import Path

import yaml

import app.core.orchestrator as orchestrator_module
from app.core.orchestrator import Orchestrator


class FakeWorkflow:
    def __init__(self, nodes):
        self.data_dict = {"nodes": nodes}


class FakeAlgorithm:
    def __init__(self, script_path, config):
        self.script_path = script_path
        self.config_dict = config


class FakeModel:
    def __init__(self, model_id, path="/models/model.pt", framework="ultralytics"):
        self.id = model_id
        self.file_path = path
        self.framework = framework


def _orchestrator():
    instance = Orchestrator.__new__(Orchestrator)
    instance.shared_inference_service = object()
    return instance


def test_workflow_requirements_honor_node_model_override(monkeypatch):
    algorithms = {
        3: FakeAlgorithm("templates/adaptive_yolo_detector.py", {"model_id": 1}),
    }
    models = {2: FakeModel(2)}
    monkeypatch.setattr(
        orchestrator_module.Algorithm,
        "get_by_id",
        lambda algorithm_id: algorithms[algorithm_id],
    )
    monkeypatch.setattr(
        orchestrator_module.MLModel,
        "get_by_id",
        lambda model_id: models[model_id],
    )
    workflow = FakeWorkflow([
        {
            "id": "algorithm-1",
            "type": "algorithm",
            "dataId": 3,
            "config": {"model_id": 2},
        }
    ])

    shared, local = _orchestrator()._workflow_model_requirements([workflow])

    assert shared == {2}
    assert local == ()


def test_direct_yolo_script_is_budgeted_as_local_per_host(monkeypatch):
    algorithms = {
        3: FakeAlgorithm("templates/simple_yolo_detector.py", {"model_id": 7}),
    }
    monkeypatch.setattr(
        orchestrator_module.Algorithm,
        "get_by_id",
        lambda algorithm_id: algorithms[algorithm_id],
    )
    workflow = FakeWorkflow([
        {"id": "algorithm-1", "type": "algorithm", "dataId": 3}
    ])

    shared, local = _orchestrator()._workflow_model_requirements([workflow])

    assert shared == set()
    assert local == (7,)


def test_adaptive_local_backend_is_not_assumed_shared(monkeypatch):
    algorithms = {
        3: FakeAlgorithm(
            "templates/adaptive_yolo_detector.py",
            {"model_id": 8, "backend": "auto"},
        ),
    }
    models = {8: FakeModel(8, path="/models/model.onnx", framework="onnx")}
    monkeypatch.setattr(
        orchestrator_module.Algorithm,
        "get_by_id",
        lambda algorithm_id: algorithms[algorithm_id],
    )
    monkeypatch.setattr(
        orchestrator_module.MLModel,
        "get_by_id",
        lambda model_id: models[model_id],
    )
    workflow = FakeWorkflow([
        {"id": "algorithm-1", "type": "algorithm", "dataId": 3}
    ])

    shared, local = _orchestrator()._workflow_model_requirements([workflow])

    assert shared == set()
    assert local == (8,)


def test_jetson_api_keeps_worker_local_shared_socket_disabled():
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.yml.jetson"
    with compose_path.open(encoding="utf-8") as handle:
        compose = yaml.safe_load(handle)

    assert compose["services"]["api"]["environment"]["SHARED_INFERENCE_ENABLED"] == "false"
    assert compose["services"]["worker"]["environment"]["SHARED_INFERENCE_ENABLED"].endswith(
        ":-true}"
    )
