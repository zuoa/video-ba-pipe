import subprocess
from pathlib import Path

from app.version import DEFAULT_VERSION, get_app_version, get_git_version
from scripts.generate_docker_build_info import read_git_version


GIT_OUTPUT = "2026-08-13T12:26:04+08:00\n7b59fd944f9a04539408a106e362dabd58cc3f4f\n"


def test_get_git_version_uses_commit_date_and_seven_character_hash(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: GIT_OUTPUT)

    assert get_git_version(Path("/repo")) == "20260813-7b59fd9"
    assert read_git_version(Path("/repo")) == "20260813-7b59fd9"


def test_app_version_prefers_environment(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "20260812-abcdef0")
    monkeypatch.setattr("app.version.get_git_version", lambda repo_root: "20260813-7b59fd9")

    assert get_app_version() == "20260812-abcdef0"


def test_app_version_uses_git_when_environment_is_missing(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.setattr("app.version.get_git_version", lambda repo_root: "20260813-7b59fd9")

    assert get_app_version() == "20260813-7b59fd9"


def test_git_version_returns_none_when_git_is_unavailable(monkeypatch):
    def raise_git_error(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(subprocess, "check_output", raise_git_error)

    assert get_git_version(Path("/repo")) is None


def test_app_version_returns_default_without_any_version_source(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.setattr("app.version.get_git_version", lambda repo_root: None)
    monkeypatch.setattr("app.version.Path", lambda value: tmp_path / "app" / "version.py")

    assert get_app_version() == DEFAULT_VERSION
