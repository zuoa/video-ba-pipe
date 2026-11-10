"""Small single-instance cloud control plane for on-demand edge previews."""

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import time
from contextlib import asynccontextmanager, contextmanager

import jwt
import psycopg2
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel


DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_PASSWORD = os.environ.get("CLOUD_ADMIN_PASSWORD", "")
JWT_SECRET = os.environ.get("CLOUD_JWT_SECRET", "")
PUBLIC_URL = os.environ.get("CLOUD_PUBLIC_URL", "").rstrip("/")
INGEST_URL = os.environ.get("CLOUD_INGEST_URL", "").rstrip("/")
NAME = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
LEASE_SECONDS = 40
IDLE_SECONDS = 20

connections: dict[str, WebSocket] = {}
active: dict[str, dict] = {}
leases: dict[str, dict] = {}
guard = asyncio.Lock()


@contextmanager
def database():
    connection = psycopg2.connect(DATABASE_URL)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def query(sql, args=(), *, one=False):
    with database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, args)
            if cursor.description:
                rows = cursor.fetchall()
                return (rows[0] if rows else None) if one else rows


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def valid_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise HTTPException(400, "标识只能包含字母、数字、下划线和连字符，最长 80 字符")
    return value


def authorize(authorization):
    try:
        scheme, token = (authorization or "").split(" ", 1)
        if scheme.lower() != "bearer":
            raise ValueError()
        claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], audience="cloud-preview")
        if claims.get("sub") != "admin":
            raise ValueError()
    except (ValueError, jwt.PyJWTError):
        raise HTTPException(401, "未授权")


async def send(device_id, message):
    socket = connections.get(device_id)
    if not socket:
        raise HTTPException(503, "设备离线")
    try:
        await socket.send_json(message)
    except Exception:
        connections.pop(device_id, None)
        raise HTTPException(503, "设备连接中断")


async def start_stream(key):
    device_id, source_code = key.split("/", 1)
    publish_token = secrets.token_urlsafe(32)
    query("DELETE FROM media_tokens WHERE path=%s AND action='publish'", (key,))
    query("INSERT INTO media_tokens (token_hash, path, action, expires_at) VALUES (%s,%s,'publish',now() + interval '12 hours')",
          (digest(publish_token), key))
    # RTSP URLs use credentials only for authentication. The URL is never sent to browsers.
    from urllib.parse import quote
    destination = f"{INGEST_URL}/media/{device_id}/{source_code}"
    destination = destination.replace("rtsps://", f"rtsps://edge:{quote(publish_token, safe='')}@", 1)
    await send(device_id, {"type": "start", "source_code": source_code, "destination": destination})
    active[key] = {"last_viewer": time.monotonic(), "started": time.monotonic(), "state": "starting"}


async def cleanup_loop():
    while True:
        await asyncio.sleep(5)
        async with guard:
            now = time.monotonic()
            for lease_id, lease in list(leases.items()):
                if lease["expires"] < now:
                    del leases[lease_id]
                    query("DELETE FROM media_tokens WHERE token_hash=%s", (lease["token_hash"],))
            for key, stream in list(active.items()):
                viewers = any(lease["path"] == key for lease in leases.values())
                if viewers:
                    stream["last_viewer"] = now
                    if now - stream["started"] > 10 * 3600 and key.split("/", 1)[0] in connections:
                        await start_stream(key)
                elif now - stream["last_viewer"] > IDLE_SECONDS:
                    device_id, source_code = key.split("/", 1)
                    try:
                        await send(device_id, {"type": "stop", "source_code": source_code})
                    except HTTPException:
                        pass
                    del active[key]
                    query("DELETE FROM media_tokens WHERE path=%s", (key,))
            query("DELETE FROM media_tokens WHERE expires_at < now()")


@asynccontextmanager
async def lifespan(_app):
    if not all((DATABASE_URL, ADMIN_PASSWORD, JWT_SECRET, PUBLIC_URL, INGEST_URL)):
        raise RuntimeError("Cloud configuration is incomplete")
    if len(JWT_SECRET) < 32:
        raise RuntimeError("CLOUD_JWT_SECRET must contain at least 32 characters")
    query("""CREATE TABLE IF NOT EXISTS devices (
        device_id text PRIMARY KEY, token_hash text NOT NULL, last_seen timestamptz,
        connected boolean NOT NULL DEFAULT false)""")
    query("""CREATE TABLE IF NOT EXISTS sources (
        device_id text NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
        source_code text NOT NULL, name text NOT NULL, codec text NOT NULL DEFAULT 'unknown',
        PRIMARY KEY(device_id, source_code))""")
    query("""CREATE TABLE IF NOT EXISTS media_tokens (
        token_hash text PRIMARY KEY, path text NOT NULL, action text NOT NULL,
        expires_at timestamptz NOT NULL)""")
    query("UPDATE devices SET connected=false")
    task = asyncio.create_task(cleanup_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)


class Login(BaseModel):
    password: str


class DeviceCreate(BaseModel):
    device_id: str


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))


@app.post("/api/login")
def login(body: Login):
    if not hmac.compare_digest(body.password, ADMIN_PASSWORD):
        raise HTTPException(401, "密码错误")
    return {"token": jwt.encode({"sub": "admin", "aud": "cloud-preview", "exp": int(time.time()) + 12 * 3600}, JWT_SECRET, algorithm="HS256")}


@app.post("/api/devices")
def create_device(body: DeviceCreate, authorization: str = Header(default="")):
    authorize(authorization)
    device_id = valid_name(body.device_id)
    token = secrets.token_urlsafe(40)
    try:
        query("INSERT INTO devices (device_id, token_hash) VALUES (%s,%s)", (device_id, digest(token)))
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "设备已存在；请使用新标识")
    return {"device_id": device_id, "device_token": token}


@app.get("/api/devices")
def list_devices(authorization: str = Header(default="")):
    authorize(authorization)
    rows = query("SELECT d.device_id,d.connected,d.last_seen,s.source_code,s.name,s.codec FROM devices d LEFT JOIN sources s USING (device_id) ORDER BY d.device_id,s.source_code")
    result = {}
    for device_id, connected, last_seen, code, name, codec in rows:
        item = result.setdefault(device_id, {"device_id": device_id, "online": connected and device_id in connections,
                                             "last_seen": last_seen.isoformat() if last_seen else None, "sources": []})
        if code:
            path = f"{device_id}/{code}"
            item["sources"].append({"source_code": code, "name": name, "codec": codec,
                                    "state": active.get(path, {}).get("state", "idle")})
    return list(result.values())


@app.websocket("/device/connect")
async def device_connect(socket: WebSocket):
    await socket.accept()
    device_id = None
    try:
        hello = await asyncio.wait_for(socket.receive_json(), timeout=10)
        candidate = valid_name(hello.get("device_id"))
        stored = query("SELECT token_hash FROM devices WHERE device_id=%s", (candidate,), one=True)
        if not stored or not hmac.compare_digest(stored[0], digest(str(hello.get("token", "")))):
            await socket.close(code=1008)
            return
        device_id = candidate
        old = connections.get(device_id)
        if old:
            await old.close(code=1001)
        connections[device_id] = socket
        query("UPDATE devices SET connected=true,last_seen=now() WHERE device_id=%s", (device_id,))
        await socket.send_json({"type": "registered"})
        async with guard:
            for key in list(active):
                if key.startswith(device_id + "/"):
                    active.pop(key, None)
                    await start_stream(key)
        while True:
            message = await asyncio.wait_for(socket.receive_json(), timeout=45)
            kind = message.get("type")
            if kind == "heartbeat":
                query("UPDATE devices SET last_seen=now() WHERE device_id=%s", (device_id,))
                await socket.send_json({"type": "heartbeat"})
            elif kind == "sources":
                items = message.get("sources")
                if not isinstance(items, list) or len(items) > 500:
                    continue
                clean = [(valid_name(item.get("source_code")), str(item.get("name", ""))[:255],
                          str(item.get("codec", "unknown"))[:16]) for item in items if isinstance(item, dict)]
                with database() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute("DELETE FROM sources WHERE device_id=%s", (device_id,))
                        for code, name, codec in clean:
                            cursor.execute("INSERT INTO sources VALUES (%s,%s,%s,%s)", (device_id, code, name, codec))
            elif kind == "stream_status":
                key = f"{device_id}/{message.get('source_code', '')}"
                if key in active:
                    active[key]["state"] = message.get("state", "error") if message.get("state") in ("starting", "publishing", "error") else "error"
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, HTTPException):
        pass
    finally:
        if device_id and connections.get(device_id) is socket:
            connections.pop(device_id, None)
            query("UPDATE devices SET connected=false,last_seen=now() WHERE device_id=%s", (device_id,))
            for key in list(active):
                if key.startswith(device_id + "/"):
                    active[key]["state"] = "offline"


@app.post("/api/view/{device_id}/{source_code}")
async def view(device_id: str, source_code: str, authorization: str = Header(default="")):
    authorize(authorization)
    valid_name(device_id)
    valid_name(source_code)
    if not query("SELECT 1 FROM sources WHERE device_id=%s AND source_code=%s", (device_id, source_code), one=True):
        raise HTTPException(404, "视频源不存在")
    if device_id not in connections:
        raise HTTPException(503, "设备离线")
    key = f"{device_id}/{source_code}"
    async with guard:
        if key not in active:
            await start_stream(key)
        lease_id = secrets.token_urlsafe(24)
        read_token = secrets.token_urlsafe(32)
        query("INSERT INTO media_tokens VALUES (%s,%s,'read',now() + interval '12 hours')", (digest(read_token), key))
        leases[lease_id] = {"path": key, "expires": time.monotonic() + LEASE_SECONDS, "token_hash": digest(read_token)}
    return {"lease_id": lease_id, "whep_url": f"{PUBLIC_URL}/media/{key}/whep", "read_token": read_token}


@app.post("/api/leases/{lease_id}/renew")
def renew(lease_id: str, authorization: str = Header(default="")):
    authorize(authorization)
    if lease_id not in leases:
        raise HTTPException(404, "预览已结束")
    leases[lease_id]["expires"] = time.monotonic() + LEASE_SECONDS
    return {"ok": True}


@app.delete("/api/leases/{lease_id}")
def stop_view(lease_id: str, authorization: str = Header(default="")):
    authorize(authorization)
    lease = leases.pop(lease_id, None)
    if lease:
        query("DELETE FROM media_tokens WHERE token_hash=%s", (lease["token_hash"],))
    return {"ok": True}


@app.post("/internal/media-auth")
def media_auth(payload: dict):
    action = payload.get("action")
    path = str(payload.get("path", "")).removeprefix("media/")
    if action not in ("publish", "read") or len(path.split("/")) != 2:
        raise HTTPException(401)
    user = payload.get("user")
    if user != ("edge" if action == "publish" else "viewer"):
        raise HTTPException(401)
    token = str(payload.get("password") or "")
    row = query("SELECT 1 FROM media_tokens WHERE token_hash=%s AND path=%s AND action=%s AND expires_at>now()",
                (digest(token), path, action), one=True)
    if not row:
        raise HTTPException(401)
    return {"ok": True}
