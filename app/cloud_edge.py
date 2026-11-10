"""Edge connection to the optional public preview platform.

Run as one separate control-image process per installation. Camera URLs stay local.
"""

import json
import hashlib
import logging
import os
import re
import subprocess
import time

import websocket

from app.core.database_models import VideoSource, db
from app.core.mediamtx_client import mediamtx_client
from app.core.node_identity import get_node_id


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
PLATFORM = os.environ.get("CLOUD_PLATFORM_URL", "").rstrip("/")
TOKEN = os.environ.get("CLOUD_DEVICE_TOKEN", "")
DEVICE_ID = os.environ.get("CLOUD_DEVICE_ID", "") or get_node_id()
processes = {}


def public_code(code):
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", code):
        return code
    return "source_" + hashlib.sha256(code.encode()).hexdigest()[:32]


def sources():
    with db.connection_context():
        return [{"source_code": public_code(source.source_code), "name": source.name,
                 "codec": source.source_codec or "unknown"}
                for source in VideoSource.select().where(VideoSource.enabled == True)]


def stop(code):
    entry = processes.pop(code, None)
    if entry:
        proc = entry["process"]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def start(code, destination):
    stop(code)
    with db.connection_context():
        source = next((item for item in VideoSource.select().where(VideoSource.enabled == True)
                       if public_code(item.source_code) == code), None)
        if source is None:
            raise ValueError("视频源不存在或已禁用")
        url = source.source_url
        codec = (source.source_codec or "unknown").lower()
    local_url = None
    if mediamtx_client.enabled:
        mediamtx_client.register_path(source.source_code, url)
        local_url = mediamtx_client.rtsp_read_url(source.source_code)
    if not local_url:
        raise RuntimeError("本地 MediaMTX 不可用；请启用 MEDIAMTX_ENABLED 和 mediamtx 服务")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
           "-i", local_url, "-map", "0:v:0", "-an"]
    if codec in ("h265", "hevc") or os.environ.get("CLOUD_FORCE_H264", "false").lower() == "true":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                "-profile:v", "baseline", "-pix_fmt", "yuv420p", "-g", "30", "-bf", "0"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-f", "rtsp", "-rtsp_transport", "tcp", destination]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    processes[code] = {"process": proc, "destination": destination, "retry_at": time.monotonic() + 10}
    log.info("Started cloud preview for %s", code)


def run():
    if not PLATFORM.startswith("wss://") or not TOKEN:
        raise RuntimeError("Set CLOUD_PLATFORM_URL=wss://... and CLOUD_DEVICE_TOKEN")
    backoff = 2
    while True:
        socket = None
        try:
            socket = websocket.create_connection(f"{PLATFORM}/device/connect", timeout=20)
            socket.settimeout(5)
            socket.send(json.dumps({"device_id": DEVICE_ID, "token": TOKEN}))
            registered = json.loads(socket.recv())
            if registered.get("type") != "registered":
                raise RuntimeError("Cloud device registration failed")
            backoff = 2
            last_sync = 0
            last_heartbeat = 0
            while True:
                now = time.monotonic()
                if now - last_sync >= 30:
                    socket.send(json.dumps({"type": "sources", "sources": sources()}))
                    last_sync = now
                if now - last_heartbeat >= 15:
                    socket.send(json.dumps({"type": "heartbeat"}))
                    last_heartbeat = now
                for code, entry in list(processes.items()):
                    if entry["process"].poll() is None and not entry.get("reported") and now >= entry["retry_at"] - 5:
                        socket.send(json.dumps({"type": "stream_status", "source_code": code, "state": "publishing"}))
                        entry["reported"] = True
                    if entry["process"].poll() is not None and now >= entry["retry_at"]:
                        try:
                            start(code, entry["destination"])
                            socket.send(json.dumps({"type": "stream_status", "source_code": code, "state": "starting"}))
                        except Exception as exc:
                            log.warning("Preview restart failed for %s: %s", code, exc)
                            entry["retry_at"] = now + 15
                try:
                    raw = socket.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                message = json.loads(raw)
                kind = message.get("type")
                code = message.get("source_code", "")
                if kind == "start":
                    try:
                        start(code, message["destination"])
                        socket.send(json.dumps({"type": "stream_status", "source_code": code, "state": "starting"}))
                    except Exception as exc:
                        log.warning("Preview start failed for %s: %s", code, exc)
                        socket.send(json.dumps({"type": "stream_status", "source_code": code, "state": "error"}))
                elif kind == "stop":
                    stop(code)
        except Exception as exc:
            log.warning("Cloud connection lost: %s", exc)
        finally:
            if socket:
                socket.close()
            for code in list(processes):
                stop(code)
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    run()
