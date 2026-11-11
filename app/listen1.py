"""Local Listen1 sidecar: start Node, speak HTTP, shut it down with Aria."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

APP_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).resolve().parent
else:
    ROOT_DIR = APP_DIR.parent

# A Node sidecar is cheap to start but leaks a little memory over long
# sessions.  Stop it after half an hour without a request, or after two hours
# of continuous uptime, and let the next request start it again.
IDLE_STOP_SECONDS = 30 * 60
MAX_UPTIME_SECONDS = 2 * 60 * 60
WATCHDOG_INTERVAL = 30.0


class SidecarError(RuntimeError):
    pass


class Listen1Sidecar:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.port = 0
        self.token = ""
        self.error = ""
        self._lock = threading.RLock()
        self._started_at = 0.0
        self._last_used = 0.0
        self._active = 0
        self._watchdog = threading.Thread(
            target=self._watchdog_loop, name="aria-listen1-watchdog", daemon=True)
        self._watchdog.start()

    # ------------------------------------------------------------------ lifecycle
    def _node_exe(self) -> str:
        bundled = ROOT_DIR / "runtime" / "node.exe"
        if bundled.exists():
            return str(bundled)
        found = shutil.which("node")
        if found:
            return found
        return ""

    def _server_path(self) -> Path:
        for candidate in (ROOT_DIR / "sidecar" / "server.js",
                          ROOT_DIR / "runtime" / "listen1-sidecar" / "server.js",
                          APP_DIR / "sidecar" / "server.js"):
            if candidate.exists():
                return candidate
        return ROOT_DIR / "sidecar" / "server.js"

    def start(self, timeout: float = 8.0) -> bool:
        with self._lock:
            if self.process and self.process.poll() is None and self.port:
                return True
            if self.process and self.process.poll() is None:
                try:
                    self.process.kill()
                except Exception:  # noqa: BLE001
                    pass
            self.process = None
            self.port = 0
            # A previous timeout/error must not permanently brick lazy starts.
            self.error = ""
            node = self._node_exe()
            server = self._server_path()
            if not node:
                self.error = "没有找到 node.exe，内置 Listen1 音源不可用"
                return False
            if not server.exists():
                self.error = "缺少 sidecar/server.js，内置 Listen1 音源不可用"
                return False
            self.token = secrets.token_hex(16)
            env = os.environ.copy()
            env["ARIA_TOKEN"] = self.token
            env["ARIA_PARENT_PID"] = str(os.getpid())
            env.pop("NODE_OPTIONS", None)
            kwargs: dict = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self.process = subprocess.Popen(
                    [node, str(server)],
                    cwd=str(server.parent),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    **kwargs,
                )
            except Exception as exc:  # noqa: BLE001
                self.error = f"启动 Listen1 失败：{exc}"
                return False

            assert self.process.stdout is not None
            timer = threading.Timer(timeout, lambda: self.process and self.process.kill())
            timer.daemon = True
            timer.start()
            try:
                while True:
                    line = self.process.stdout.readline()
                    if not line:
                        if self.process.poll() is not None:
                            self.error = "Listen1 侧车进程提前退出"
                            return False
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("event") == "ready" and payload.get("port"):
                        self.port = int(payload["port"])
                        self._started_at = time.time()
                        self._last_used = self._started_at
                        return True
                self.error = "Listen1 侧车启动超时"
                self.stop()
                return False
            finally:
                timer.cancel()

    def stop(self) -> None:
        with self._lock:
            if self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    try:
                        self.process.kill()
                    except Exception:  # noqa: BLE001
                        pass
            self.process = None
            self.port = 0
            self._started_at = 0.0
            self._last_used = 0.0

    def _watchdog_loop(self) -> None:
        while True:
            time.sleep(WATCHDOG_INTERVAL)
            try:
                with self._lock:
                    if not self.ready or self._active:
                        continue
                    now = time.time()
                    idle = now - (self._last_used or self._started_at or now)
                    uptime = now - (self._started_at or now)
                    if idle >= IDLE_STOP_SECONDS or uptime >= MAX_UPTIME_SECONDS:
                        self.stop()
            except Exception:  # noqa: BLE001 - a watchdog must never die
                continue

    @property
    def ready(self) -> bool:
        return bool(self.port and self.process and self.process.poll() is None)

    # ------------------------------------------------------------------ HTTP
    def get(self, path: str, **params) -> dict:
        with self._lock:
            if not self.ready and not self.start():
                raise SidecarError(self.error or "Listen1 侧车未启动")
            self._last_used = time.time()
            self._active += 1
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            response = requests.get(
                url,
                params={k: v for k, v in params.items() if v not in (None, "")},
                headers={"X-Aria-Token": self.token},
                timeout=25,
            )
        except requests.RequestException as exc:
            raise SidecarError(f"Listen1 请求失败：{exc}") from exc
        finally:
            with self._lock:
                self._last_used = time.time()
                self._active = max(0, self._active - 1)
        if response.status_code != 200:
            try:
                message = response.json().get("error", "")
            except Exception:  # noqa: BLE001
                message = response.text[:120]
            raise SidecarError(message or f"Listen1 返回 {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise SidecarError("Listen1 返回了无法解析的数据") from exc
        if not isinstance(data, dict):
            raise SidecarError("Listen1 返回格式异常")
        return data

    # ------------------------------------------------------------------ API
    def recommend(self, source: str = "netease", limit: int = 24,
                  category: str = "", category_id: str = "", sort_id: str = "") -> list[dict]:
        data = self.get("/recommend", source=source, limit=limit, category=category,
                        category_id=category_id, sort_id=sort_id)
        return data.get("items") or []

    def charts(self, source: str = "netease") -> list[dict]:
        return self.get("/charts", source=source).get("items") or []

    def categories(self, source: str = "netease") -> list[dict]:
        return self.get("/categories", source=source).get("groups") or []

    def playlist(self, playlist_id: str) -> dict:
        return self.get("/playlist", id=playlist_id)

    def chart(self, source: str, chart_id: str) -> dict:
        return self.get("/chart", source=source, id=chart_id)

    def stream(self, track_id: str) -> str:
        data = self.get("/stream", id=track_id)
        return data.get("url") or ""

    def search(self, source: str, keyword: str, limit: int = 30) -> list[dict]:
        return self.get("/search", source=source, keyword=keyword, limit=limit).get("tracks") or []


SIDECAR = Listen1Sidecar()
