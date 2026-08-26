from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_alert_exports_proxy_does_not_redirect_collection_endpoint():
    config = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

    assert "location /api/alert-exports {" in config
    assert "location /api/alert-exports/ {" not in config


def test_api_proxies_preserve_the_external_host_port():
    config = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

    assert config.count("proxy_set_header Host $http_host;") == 4
    assert "proxy_set_header Host $host;" not in config
