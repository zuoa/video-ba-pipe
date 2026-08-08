"""MediaMTX REST 客户端：用于「按需拉流」实时预览的路径注册/注销。

设计要点
--------
- MediaMTX 仅作为 WebRTC 实时预览的「按需中继」，**不参与检测流水线**。
- 通过 REST API 注册路径时配置 ``sourceOnDemand: true``：只有当有 WebRTC
  观众连入时，MediaMTX 才向摄像机拉 RTSP；观众全部离开后自动断开，避免常驻占资源。
- 本客户端**绝不向调用方抛异常**：MediaMTX 未启用或不可达时，所有方法安全降级
  （返回 False / no-op），确保视频源增删改与编排主循环不受影响。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

import requests

from app import logger
from app import config as cfg

# REST 调用超时（秒）。注册/注销走 best-effort，避免阻塞 CRUD 与编排循环。
_TIMEOUT = (2.5, 3.0)  # (connect, read)


class MediaMTXClient:
    """MediaMTX REST API 的轻量封装，线程安全（只读配置 + 幂等 HTTP 调用）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 探活缓存：避免在编排主循环里高频探测 MediaMTX。
        self._available: Optional[bool] = None
        self._available_checked_at: float = 0.0

    # ------------------------------------------------------------------ #
    # 基础
    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        return bool(getattr(cfg, "MEDIAMTX_ENABLED", False))

    def _api_base(self) -> str:
        host = getattr(cfg, "MEDIAMTX_API_HOST", "mediamtx")
        port = getattr(cfg, "MEDIAMTX_API_PORT", 9997)
        return f"http://{host}:{port}"

    def _path_name(self, source_code: str) -> str:
        # source_code 作为 MediaMTX path 名；去除首尾斜杠避免拼出空段。
        return (source_code or "").strip().strip("/")

    # ------------------------------------------------------------------ #
    # 探活
    # ------------------------------------------------------------------ #
    def is_available(self, force: bool = False) -> bool:
        """探测 MediaMTX REST 是否可达。未启用时直接返回 False。"""
        if not self.enabled:
            return False
        import time

        now = time.monotonic()
        with self._lock:
            if not force and self._available is not None and now - self._available_checked_at < 15.0:
                return self._available

        ok = False
        try:
            resp = requests.get(f"{self._api_base()}/v3/paths", timeout=_TIMEOUT)
            ok = resp.status_code == 200
        except Exception as exc:  # noqa: BLE001 - 探活吞掉所有异常
            logger.debug(f"[MediaMTX] 探活失败（忽略）: {exc}")
        with self._lock:
            self._available = ok
            self._available_checked_at = now
        return ok

    # ------------------------------------------------------------------ #
    # 路径注册 / 注销
    # ------------------------------------------------------------------ #
    def register_path(self, source_code: str, rtsp_url: str) -> bool:
        """注册/更新一个按需拉流路径。

        body 中 ``sourceOnDemand: true`` 表示只有观众连入时才向 source 拉流。
        """
        if not self.enabled:
            return False
        name = self._path_name(source_code)
        if not name or not rtsp_url:
            return False
        if not self.is_available():
            return False

        payload: Dict[str, Any] = {
            "source": rtsp_url,
            "sourceProtocol": "tcp",
            "sourceOnDemand": True,
            # RTSP 时钟校正，避免部分相机时间戳抖动导致 WebRTC 卡顿
            "rtspRangeType": "clock",
        }
        url = f"{self._api_base()}/v3/config/paths/patch/{name}"
        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT)
            if resp.status_code < 300:
                logger.info(f"[MediaMTX] 已注册按需拉流路径 '{name}' -> {rtsp_url}")
                return True
            logger.warning(
                f"[MediaMTX] 注册路径 '{name}' 失败: HTTP {resp.status_code} {resp.text[:200]}"
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning(f"[MediaMTX] 注册路径 '{name}' 异常（忽略）: {exc}")
        return False

    def unregister_path(self, source_code: str) -> bool:
        """删除一个路径（视频源删除时调用）。"""
        if not self.enabled:
            return False
        name = self._path_name(source_code)
        if not name:
            return False
        if not self.is_available():
            return False

        url = f"{self._api_base()}/v3/config/paths/{name}"
        try:
            resp = requests.delete(url, timeout=_TIMEOUT)
            # 404 视为成功（本来就不存在）
            if resp.status_code < 300 or resp.status_code == 404:
                logger.info(f"[MediaMTX] 已注销路径 '{name}'")
                return True
            logger.warning(
                f"[MediaMTX] 注销路径 '{name}' 失败: HTTP {resp.status_code} {resp.text[:200]}"
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning(f"[MediaMTX] 注销路径 '{name}' 异常（忽略）: {exc}")
        return False

    def list_path_names(self) -> list:
        """返回 MediaMTX 当前已注册的路径名列表（用于编排器做差量同步）。"""
        if not self.enabled or not self.is_available():
            return []
        try:
            resp = requests.get(f"{self._api_base()}/v3/config/paths", timeout=_TIMEOUT)
            if resp.status_code != 200:
                return []
            data = resp.json()
            # MediaMTX v3: {"items": {"<name>": {...}, ...}} 或 dict
            items = data.get("items", data) if isinstance(data, dict) else {}
            return [str(k) for k in items.keys()] if isinstance(items, dict) else []
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[MediaMTX] 读取路径列表失败（忽略）: {exc}")
            return []


# 模块级单例
mediamtx_client = MediaMTXClient()
