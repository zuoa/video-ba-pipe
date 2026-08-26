from pathlib import Path

import yaml

import app.core.orchestrator as orchestrator_module
from app.core.inference_budget import InferenceAdmissionController, OomCircuitBreaker
from app.core.inference_resource_config import InferenceResourceConfig
from app.core.orchestrator import Orchestrator


class FakeWorkflow:
    def __init__(self, nodes):
        self.data_dict = {"nodes": nodes}


class FakeAlgorithm:
    def __init__(self, script_path, config, ext_config=None):
        self.script_path = script_path
        self.config_dict = config
        self.ext_config = ext_config or {}


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


def test_adaptive_rknn_model_is_budgeted_as_globally_shared(monkeypatch):
    algorithms = {
        3: FakeAlgorithm(
            "templates/adaptive_yolo_detector.py",
            {"model_id": 8, "backend": "auto"},
        ),
    }
    models = {8: FakeModel(8, path="/models/model.rknn", framework="rknn")}
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
    orchestrator = _orchestrator()
    orchestrator.inference_capabilities = {
        "shared_ultralytics": True,
        "rknn_shared": True,
    }
    workflow = FakeWorkflow([
        {"id": "algorithm-1", "type": "algorithm", "dataId": 3}
    ])

    shared, local = orchestrator._workflow_model_requirements([workflow])

    assert shared == {8}
    assert local == ()


def test_ocr_models_are_budgeted_as_globally_shared(monkeypatch):
    algorithms = {
        3: FakeAlgorithm(
            "",
            {},
            ext_config={
                "algorithm_type": "ocr",
                "ocr_config": {
                    "detection_model_id": 11,
                    "recognition_model_id": 12,
                    "device": "auto",
                },
            },
        ),
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

    assert shared == {11}
    assert local == ()


def test_confirmed_shared_model_ids_include_ocr_recognition():
    confirmed = Orchestrator._confirmed_shared_model_ids({
        "models": [
            {"model_id": 11, "recognition_model_id": 12, "ready": True},
            {"model_id": 7, "ready": False},
            {"model_id": "bad", "ready": True},
        ]
    })
    assert confirmed == {11, 12}


def test_ocr_models_are_local_when_shared_service_unavailable(monkeypatch):
    algorithms = {
        3: FakeAlgorithm(
            "",
            {},
            ext_config={
                "algorithm_type": "ocr",
                "ocr_config": {
                    "detection_model_id": 11,
                    "recognition_model_id": 12,
                },
            },
        ),
    }
    monkeypatch.setattr(
        orchestrator_module.Algorithm,
        "get_by_id",
        lambda algorithm_id: algorithms[algorithm_id],
    )
    orchestrator = _orchestrator()
    orchestrator.shared_inference_service = None
    workflow = FakeWorkflow([
        {"id": "algorithm-1", "type": "algorithm", "dataId": 3}
    ])

    shared, local = orchestrator._workflow_model_requirements([workflow])

    assert shared == set()
    assert local == (11, 12)


def test_cascade_repeated_model_is_budgeted_per_stage_backend(monkeypatch):
    cascade_config = {
        "stages": [
            {"id": "first", "model_id": 9, "inference": {"backend": "ultralytics"}},
            {"id": "second", "model_id": 9, "inference": {"backend": "auto"}},
        ]
    }
    algorithms = {
        3: FakeAlgorithm(
            "",
            {},
            ext_config={"algorithm_type": "cascade", "cascade_config": cascade_config},
        ),
    }
    models = {9: FakeModel(9, path="/models/model.onnx", framework="onnx")}
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

    assert shared == {9}
    assert local == (9,)


def test_cuda_compose_enables_shared_inference_only_in_worker():
    compose_path = (
        Path(__file__).resolve().parents[1]
        / "deploy/compose/templates/cuda.yml"
    )
    with compose_path.open(encoding="utf-8") as handle:
        compose = yaml.safe_load(handle)
    assert compose["services"]["app"]["environment"]["SHARED_INFERENCE_ENABLED"] == "false"
    assert compose["services"]["worker"]["environment"]["SHARED_INFERENCE_ENABLED"].endswith(
        ":-true}"
    )


def test_jetson_api_keeps_worker_local_shared_socket_disabled():
    compose_path = (
        Path(__file__).resolve().parents[1]
        / "deploy/compose/templates/jetson.yml"
    )
    with compose_path.open(encoding="utf-8") as handle:
        compose = yaml.safe_load(handle)

    assert compose["services"]["api"]["environment"]["SHARED_INFERENCE_ENABLED"] == "false"
    assert compose["services"]["worker"]["environment"]["SHARED_INFERENCE_ENABLED"].endswith(
        ":-true}"
    )


def test_base_compose_excludes_optional_services_and_enables_worker_inference():
    compose_path = (
        Path(__file__).resolve().parents[1]
        / "deploy/compose/templates/cpu.yml"
    )
    with compose_path.open(encoding="utf-8") as handle:
        compose = yaml.safe_load(handle)

    assert {"mqtt", "rabbitmq", "mediamtx"}.isdisjoint(compose["services"])
    assert compose["services"]["api"]["environment"]["SHARED_INFERENCE_ENABLED"] == "false"
    assert compose["services"]["worker"]["environment"]["SHARED_INFERENCE_ENABLED"].endswith(
        ":-true}"
    )
    assert compose["services"]["worker"]["environment"]["INFERENCE_ADMISSION_ENABLED"].endswith(
        ":-true}"
    )


def test_rknn_algorithm_preview_runtime_is_worker_only():
    compose_path = (
        Path(__file__).resolve().parents[1]
        / "deploy/compose/templates/rknn.yml"
    )
    with compose_path.open(encoding="utf-8") as handle:
        compose = yaml.safe_load(handle)

    api = compose["services"]["api"]
    worker = compose["services"]["worker"]
    assert api["environment"]["SHARED_INFERENCE_ENABLED"] == "false"
    assert worker["environment"]["SHARED_INFERENCE_ENABLED"].endswith(
        ":-true}"
    )
    assert api.get("privileged") is not True
    assert "/dev/dri:/dev/dri" not in api.get("devices", [])
    assert "/opt/rknn:/opt/rknn:ro" not in api.get("volumes", [])
    assert "/usr/lib/librknnrt.so:/usr/lib/librknnrt.so:ro" not in api.get("volumes", [])
    assert worker["privileged"] is True
    assert "/dev/dri:/dev/dri" in worker["devices"]
    assert "/opt/rknn:/opt/rknn:ro" not in worker["volumes"]
    assert "/usr/lib/librknnrt.so:/usr/lib/librknnrt.so:ro" not in worker["volumes"]
    assert api["environment"]["ALGORITHM_TEST_WORKER_URL"].endswith("worker:5010}")


def test_rknn_image_pins_matching_toolkit_and_runtime_versions():
    dockerfile_path = Path(__file__).resolve().parents[1] / "Dockerfile.rk"
    dockerfile = dockerfile_path.read_text(encoding="utf-8")

    assert "rknn_toolkit_lite2-2.3.2-cp311-cp311" in dockerfile
    assert "/v2.3.2/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so" in dockerfile
    assert "/usr/lib/librknnrt.so" in dockerfile
    assert "RKNN_TOOLKIT_LITE2_WHL" not in dockerfile
    assert "/opt/rknn/lib" not in dockerfile

    compose_path = (
        Path(__file__).resolve().parents[1]
        / "deploy/compose/templates/rknn.yml"
    )
    with compose_path.open(encoding="utf-8") as handle:
        compose = yaml.safe_load(handle)
    worker = compose["services"]["worker"]
    assert "/opt/rknn/lib" not in worker["environment"]["LD_LIBRARY_PATH"]
    assert "/opt/rknn:/opt/rknn:ro" not in worker["volumes"]
    assert "/usr/lib/librknnrt.so:/usr/lib/librknnrt.so:ro" not in worker["volumes"]


def test_runtime_policy_values_are_hot_updated():
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.oom_circuit = OomCircuitBreaker(
        enabled=False,
        failure_threshold=3,
        open_seconds=600,
        stable_reset_seconds=600,
        backoff_cap_seconds=300,
    )
    orchestrator.inference_admission = InferenceAdmissionController(
        enabled=False,
        reserve_mb=1024,
        reserve_percent=10,
        default_new_model_mb=512,
        margin_percent=10,
    )
    config = InferenceResourceConfig(
        inference_admission_enabled=True,
        system_reserve_mb=3072,
        system_reserve_percent=20,
        new_model_default_mb=1536,
        model_memory_margin_percent=40,
        oom_circuit_breaker_enabled=True,
        oom_failure_threshold=5,
        oom_circuit_open_seconds=900,
        oom_stable_reset_seconds=1200,
        oom_restart_backoff_max_seconds=240,
    )

    orchestrator._apply_inference_policy_values(config)

    assert orchestrator.inference_admission.enabled is True
    assert orchestrator.inference_admission.reserve_mb == 3072
    assert orchestrator.inference_admission.reserve_percent == 20
    assert orchestrator.inference_admission.default_new_model_mb == 1536
    assert orchestrator.inference_admission.margin_percent == 40
    assert orchestrator.oom_circuit.enabled is True
    assert orchestrator.oom_circuit.failure_threshold == 5
    assert orchestrator.oom_circuit.open_seconds == 900


def test_source_host_environment_uses_effective_database_config():
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.effective_inference_config = InferenceResourceConfig(
        shared_inference_enabled=True,
        request_timeout_seconds=47,
    )

    environment = orchestrator._source_host_inference_environment()

    assert environment["SHARED_INFERENCE_ENABLED"] == "true"
    assert environment["SHARED_INFERENCE_REQUEST_TIMEOUT_SECONDS"] == "47"


def test_service_rebuild_stops_hosts_before_router_restart():
    events = []

    class FakeController:
        process = None

        def start(self):
            events.append("new_start")
            return True

        def stop(self):
            events.append("old_stop")

    class FakeTestController:
        def replace_environment(self, environment):
            assert environment["SHARED_INFERENCE_ENABLED"] == "true"
            events.append("test_restart")
            return True

    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.workflow_hosts = {1: {}, 2: {}}
    orchestrator.shared_inference_service = FakeController()
    orchestrator.algorithm_test_service = FakeTestController()
    orchestrator.inference_reconcile_error = "previous"

    def stop_host(source_id):
        events.append(f"host_stop:{source_id}")
        orchestrator.workflow_hosts.pop(source_id)

    orchestrator._stop_source_host = stop_host
    orchestrator._build_shared_inference_controller = lambda _config: FakeController()

    orchestrator._rebuild_shared_inference_runtime(InferenceResourceConfig(
        shared_inference_enabled=True,
    ))

    assert events == [
        "host_stop:1", "host_stop:2", "old_stop", "new_start", "test_restart"
    ]
    assert orchestrator.inference_reconcile_error is None
