from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_alert_exports_proxy_does_not_redirect_collection_endpoint():
    config = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

    assert "location /api/alert-exports {" in config
    assert "location /api/alert-exports/ {" not in config


def test_api_proxies_preserve_the_external_host_port():
    config = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

    assert config.count("proxy_set_header Host $http_host;") == 4
    assert "proxy_set_header Host $host;" not in config


def test_frontend_nginx_config_is_supplied_by_the_image():
    for dockerfile in ("frontend/Dockerfile", "frontend/Dockerfile.rk"):
        content = (ROOT / dockerfile).read_text(encoding="utf-8")
        assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in content

    for platform in ("cpu", "cuda", "jetson", "rknn"):
        compose = yaml.safe_load(
            (ROOT / f"deploy/compose/templates/{platform}.yml").read_text(
                encoding="utf-8"
            )
        )
        frontend_volumes = compose["services"]["frontend"].get("volumes", [])
        assert not any("nginx.conf" in volume for volume in frontend_volumes)

    required_files = (ROOT / "deploy/compose/required-files.txt").read_text(
        encoding="utf-8"
    )
    assert "frontend/nginx.conf" not in required_files
