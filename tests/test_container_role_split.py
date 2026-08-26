from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_dynamic_build_metadata_only_affects_application_stages():
    final_stages = {
        "Dockerfile.control": "FROM runtime-base AS control",
        "Dockerfile.cpu": "FROM runtime-base AS worker",
        "Dockerfile.cuda": "FROM runtime-base AS worker",
        "Dockerfile.jetson": "FROM runtime-base AS worker",
        "Dockerfile.rk": "FROM runtime-base AS worker",
    }
    for filename, final_stage in final_stages.items():
        content = (ROOT / filename).read_text(encoding="utf-8")
        split_at = content.index(final_stage)
        runtime_content = content[:split_at]
        application_content = content[split_at:]
        assert "AS runtime-base" in runtime_content
        assert "ARG APP_VERSION" not in runtime_content
        assert "ARG BUILD_TIME" not in runtime_content
        assert "ARG APP_VERSION" in application_content
        assert "ARG BUILD_TIME" in application_content


def test_control_dependencies_exclude_inference_frameworks():
    requirements = {
        line.strip().split(";", 1)[0].lower()
        for line in (ROOT / "requirements.control.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert requirements.isdisjoint({
        "torch",
        "torchvision",
        "torchaudio",
        "ultralytics",
        "onnxruntime",
        "onnxruntime-gpu",
        "paddlepaddle",
        "paddlepaddle-gpu",
        "paddleocr",
        "rknn-toolkit-lite2",
    })


def test_web_and_orchestrator_do_not_start_jobs_threads():
    webapp = (ROOT / "app/web/webapp.py").read_text(encoding="utf-8")
    orchestrator = (ROOT / "app/core/orchestrator.py").read_text(encoding="utf-8")
    alert_exports = (ROOT / "app/web/api/alert_exports.py").read_text(
        encoding="utf-8"
    )
    combined = webapp + orchestrator + alert_exports
    assert "start_alert_export_worker" not in combined
    assert "start_face_import_worker" not in combined
    assert "alert_delivery_worker" not in orchestrator
    assert "AlertMediaCleaner" not in orchestrator


def test_compose_roles_use_one_platform_release_and_worker_capabilities():
    variants = {
        "deploy/compose/templates/cpu.yml": (
            "api", "cpu", "VIDEO_BA_PIPE_WORKER_IMAGE",
        ),
        "deploy/compose/templates/cuda.yml": (
            "app", "cuda", "VIDEO_BA_PIPE_CUDA_IMAGE",
        ),
        "deploy/compose/templates/jetson.yml": (
            "api", "jetson", "VIDEO_BA_PIPE_JETSON_IMAGE",
        ),
        "deploy/compose/templates/rknn.yml": (
            "api", "rk", "VIDEO_BA_PIPE_RK_IMAGE",
        ),
    }
    release = "${VIDEO_BA_PIPE_RELEASE:-stable}"

    for filename, (api_name, platform, worker_override) in variants.items():
        compose = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
        services = compose["services"]
        expected_control = (
            "${VIDEO_BA_PIPE_CONTROL_IMAGE:-ghcr.io/zuoa/video-ba-pipe:"
            f"control-{platform}-{release}}}"
        )
        expected_worker = (
            f"${{{worker_override}:-ghcr.io/zuoa/video-ba-pipe:"
            f"{platform}-{release}}}"
        )

        assert services["db-init"]["image"] == expected_control
        assert services[api_name]["image"] == expected_control
        assert services["jobs"]["image"] == expected_control
        assert services["worker"]["image"] == expected_worker
        assert services[api_name]["environment"][
            "OCR_RUNTIME_CAPABILITY_SOURCE"
        ] == "worker"


def test_release_workflows_promote_only_matching_commit_images():
    workflows = {
        "cpu": ("build_backend_images.yml", "cpu"),
        "cuda": ("build_x86_cuda_image.yml", "cuda"),
        "jetson": ("build_jetson_image.yml", "jetson"),
        "rk": ("build_rknn_image.yml", "rk"),
    }
    workflow_root = ROOT / ".github" / "workflows"

    for control_platform, (filename, worker_platform) in workflows.items():
        content = (workflow_root / filename).read_text(encoding="utf-8")
        sha_expression = "${{ github.sha }}"
        immutable_control = f":control-{control_platform}-{sha_expression}"
        immutable_worker = f":{worker_platform}-{sha_expression}"
        stable_worker = f':{worker_platform}-stable"'
        stable_control = f':control-{control_platform}-stable"'

        assert immutable_control in content
        assert immutable_worker in content
        assert stable_worker in content
        assert stable_control in content
        assert f"group: release-{control_platform}-stable" in content
        # Worker is promoted first. A rolling pull can therefore never see a
        # new control plane paired with the previous, thread-owning worker.
        assert content.index(stable_worker) < content.index(stable_control)


def test_release_workflows_check_control_artifact_before_push_and_promotion():
    release_jobs = (
        ("build_backend_images.yml", "build-cpu", "cpu"),
        ("build_backend_images.yml", "build-jetson", "jetson"),
        ("build_x86_cuda_image.yml", "build-backend", "cuda"),
        ("build_jetson_image.yml", "build-backend", "jetson"),
        ("build_rknn_image.yml", "build-backend", "rk"),
    )
    workflow_root = ROOT / ".github" / "workflows"

    for filename, job_name, platform in release_jobs:
        workflow = yaml.safe_load(
            (workflow_root / filename).read_text(encoding="utf-8")
        )
        steps = workflow["jobs"][job_name]["steps"]
        build_index = next(
            index
            for index, step in enumerate(steps)
            if step["name"].startswith("Build immutable")
            and "control image" in step["name"]
        )
        check_index = next(
            index
            for index, step in enumerate(steps)
            if step["name"].startswith("Enforce")
            and "control image size budget" in step["name"]
        )
        push_index = next(
            index
            for index, step in enumerate(steps)
            if step["name"].startswith("Push checked")
            and "control image" in step["name"]
        )
        promote_index = next(
            index
            for index, step in enumerate(steps)
            if step["name"].startswith("Promote matching")
        )

        build_inputs = steps[build_index]["with"]
        immutable_tag = build_inputs["tags"]
        assert immutable_tag.endswith(
            f":control-{platform}-${{{{ github.sha }}}}"
        )
        assert build_inputs["load"] is True
        assert not build_inputs.get("push", False)
        assert steps[check_index]["run"] == (
            "python3 scripts/check_docker_image_size.py "
            f"{immutable_tag} --max-mib 1024"
        )
        assert steps[push_index]["run"] == f"docker push {immutable_tag}"
        assert build_index < check_index < push_index < promote_index


def test_standalone_control_workflow_cannot_advance_deployment_tag():
    content = (
        ROOT / ".github" / "workflows" / "build_control_image.yml"
    ).read_text(encoding="utf-8")

    assert ":control-multiarch-${{ github.sha }}" in content
    assert ":control\n" not in content
