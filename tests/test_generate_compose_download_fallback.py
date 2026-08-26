import fcntl
import os
import pty
import select
import shutil
import subprocess
import termios
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _prepare_remote_generator(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    generator = tmp_path / "generate_compose.sh"
    shutil.copyfile(ROOT / "scripts/generate_compose.sh", generator)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -Eeuo pipefail

output=''
url=''
while (($#)); do
  case "$1" in
    --output)
      output="$2"
      shift 2
      ;;
    https://*)
      url="$1"
      shift
      ;;
    *) shift ;;
  esac
done

printf '%s\n' "${url}" >>"${FAKE_CURL_LOG}"
case "${url}" in
  https://raw.githubusercontent.com/*)
    exit 28
    ;;
  https://gh-proxy.com/https://raw.githubusercontent.com/zuoa/video-ba-pipe/main/*)
    relative_path="${url#https://gh-proxy.com/https://raw.githubusercontent.com/zuoa/video-ba-pipe/main/}"
    cp -- "${FAKE_SOURCE_ROOT}/${relative_path}" "${output}"
    ;;
  *) exit 22 ;;
esac
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    log = tmp_path / "curl.log"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["FAKE_CURL_LOG"] = str(log)
    env["FAKE_SOURCE_ROOT"] = str(ROOT)
    env.pop("VIDEO_BA_PIPE_CONFIG_BASE_URL", None)
    env.pop("VIDEO_BA_PIPE_GH_PROXY_BASE_URL", None)
    return generator, log, env


def test_remote_generator_falls_back_to_ghproxy_for_github_raw(tmp_path):
    generator, log, env = _prepare_remote_generator(tmp_path)

    result = subprocess.run(
        [
            "bash",
            str(generator),
            "--non-interactive",
            "--platform",
            "cpu",
            "--no-env-file",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "docker-compose.yml").is_file()
    requested_urls = log.read_text(encoding="utf-8").splitlines()
    assert any(url.startswith("https://raw.githubusercontent.com/") for url in requested_urls)
    assert any(url.startswith("https://gh-proxy.com/https://raw.githubusercontent.com/") for url in requested_urls)
    assert "GitHub Raw 下载失败，尝试 GHProxy" in result.stderr


def test_remote_generator_does_not_proxy_a_custom_source(tmp_path):
    generator, log, env = _prepare_remote_generator(tmp_path)

    result = subprocess.run(
        [
            "bash",
            str(generator),
            "--non-interactive",
            "--platform",
            "cpu",
            "--no-env-file",
            "--config-base-url",
            "https://config.example.test/video-ba-pipe",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    requested_urls = log.read_text(encoding="utf-8").splitlines()
    assert requested_urls
    assert all("gh-proxy.com" not in url for url in requested_urls)


def test_remote_generator_can_prompt_while_script_arrives_on_stdin(tmp_path):
    generator, _, env = _prepare_remote_generator(tmp_path)
    master_fd, slave_fd = pty.openpty()

    def attach_controlling_terminal():
        os.setsid()
        fcntl.ioctl(2, termios.TIOCSCTTY, 0)

    process = subprocess.Popen(
        ["bash"],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=slave_fd,
        preexec_fn=attach_controlling_terminal,
    )
    os.close(slave_fd)
    output = bytearray()
    try:
        assert process.stdin is not None
        process.stdin.write(generator.read_bytes())
        process.stdin.close()

        deadline = time.monotonic() + 15
        answers_sent = False
        while process.poll() is None and time.monotonic() < deadline:
            readable, _, _ = select.select([master_fd], [], [], 0.1)
            if not readable:
                continue
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            output.extend(chunk)
            if not answers_sent and b": " in output:
                os.write(master_fd, b"cpu\nn\nn\nn\nn\nn\n")
                answers_sent = True

        process.wait(timeout=max(0.1, deadline - time.monotonic()))
    finally:
        os.close(master_fd)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 0, output.decode(errors="replace")
    assert (tmp_path / "docker-compose.yml").is_file()
