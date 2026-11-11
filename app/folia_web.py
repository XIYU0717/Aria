"""Embed Folia's own React stage in Aria through Qt WebEngine.

The stage is a standalone Vite page built from Folia's classic visualizer and its
common geometric background.  Aria only pushes lyrics + transport state into it;
none of Folia's login, library, routing or app shell is part of the page.
"""

from __future__ import annotations

import base64
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QVBoxLayout, QWidget

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings
    WEBENGINE_AVAILABLE = True
except Exception:  # pragma: no cover - a machine without the WebEngine wheel
    QWebEngineView = None  # type: ignore
    QWebEngineSettings = None  # type: ignore
    WEBENGINE_AVAILABLE = False


def _stage_dir() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    base = Path(bundle) if bundle else Path(__file__).resolve().parent.parent
    return base / "folia_stage"


def _pixmap_data_url(pix: QPixmap | None) -> str:
    """A small PNG data URL for Folia's cover-colour + blurred-cover layers.

    Data URLs are same-origin for the local stage server, so the page can call
    canvas.getImageData() without a CORS failure. 640 px is Folia's own
    COVER_SOURCE_MAX; its FluidBackground then bakes the blur into a 320 px
    canvas, so this is already more than it can use.
    """
    if pix is None or pix.isNull():
        return ""
    scaled = pix.scaled(640, 640, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    try:
        if not scaled.save(buffer, "PNG"):
            return ""
        raw = bytes(buffer.data())
    finally:
        buffer.close()
    if not raw:
        return ""
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003 - stdlib signature
        pass


class _LocalServer:
    """Loopback-only HTTP server: file:// modules are blocked by Chromium CORS."""

    def __init__(self, directory: Path) -> None:
        handler = partial(_QuietHandler, directory=str(directory))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return int(self.httpd.server_port)

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


class FoliaWebStage(QWidget):
    """A transparent-to-input web stage.  The native Folia pill stays on top."""

    failed = Signal(str)
    ready = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._ready = False
        self._bridge_ready = False
        self._failed = False
        self._server: _LocalServer | None = None
        self._lines: list[tuple[int, str]] = []
        self._translations: dict[int, str] = {}
        self._duration = 0
        self._position = 0
        self._playing = False
        self._cover_data_url = ""
        self._cover_key = 0
        self._mode = "classic"
        self._song_title = ""
        self._song_artist = ""
        self._tuning = {"fontScale": 1.0, "animationIntensity": "normal", "wordSpacing": 0.7}

        self._view = QWebEngineView(self) if WEBENGINE_AVAILABLE else None
        if self._view is None:
            QTimerSingleShot(self, 0, lambda: self.failed.emit("Qt WebEngine 不可用"))
            return
        self._view.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._view.setContextMenuPolicy(Qt.NoContextMenu)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._view)
        page = self._view.page()
        page.setBackgroundColor(QColor("#09090B"))
        settings = page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
        self._view.loadFinished.connect(self._on_load_finished)

        stage = _stage_dir()
        if not (stage / "folia-stage.html").exists():
            QTimerSingleShot(self, 0, lambda: self._fail(f"找不到 Folia 舞台资源：{stage}"))
            return
        try:
            self._server = _LocalServer(stage)
        except Exception as exc:  # noqa: BLE001
            QTimerSingleShot(self, 0, lambda: self._fail(f"Folia 本地服务启动失败：{exc}"))
            return
        self._view.load(QUrl(f"http://127.0.0.1:{self._server.port}/folia-stage.html"))

    def _fail(self, message: str) -> None:
        if self._failed:
            return
        self._failed = True
        self.failed.emit(message)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self._fail("Folia 舞台页面加载失败")
            return
        self._ready = True
        self._probe_bridge(0)

    def _probe_bridge(self, attempt: int) -> None:
        """loadFinished can fire just before React installs window.foliaStage."""
        if self._failed or self._bridge_ready or self._view is None:
            return
        if attempt > 50:
            self._fail("Folia 舞台桥接超时")
            return

        def done(result) -> None:
            if result:
                self._bridge_ready = True
                self._push_lyrics()
                self._push_cover()
                self._push_time()
                self._push_song()
                self._push_mode()
                self._push_tuning()
                self.ready.emit()
            else:
                QTimer.singleShot(60, lambda: self._probe_bridge(attempt + 1))

        try:
            self._view.page().runJavaScript("window.foliaStageReady === true", done)
        except Exception as exc:  # noqa: BLE001
            self._fail(f"Folia 舞台探测失败：{exc}")

    def _run(self, script: str) -> None:
        if not self._ready or not self._bridge_ready or self._view is None:
            return
        try:
            self._view.page().runJavaScript(script)
        except Exception as exc:  # noqa: BLE001
            self._fail(f"Folia 舞台通信失败：{exc}")

    @staticmethod
    def _line_payload(lines: list[tuple[int, str]],
                      translations: dict[int, str],
                      duration_ms: int) -> list[dict]:
        payload: list[dict] = []
        for index, (start_ms, text) in enumerate(lines):
            if index + 1 < len(lines):
                end_ms = max(start_ms + 800, int(lines[index + 1][0]))
            else:
                # A last LRC line has no next timestamp.  Keep it on screen until
                # the song ends, but do not stretch its word timings across the
                # whole track (that would reveal one word every few seconds).
                end_ms = start_ms + 6000
                if 0 < duration_ms < end_ms:
                    end_ms = max(start_ms + 800, duration_ms)
            payload.append({
                "startTime": round(start_ms / 1000, 3),
                "endTime": round(end_ms / 1000, 3),
                "text": text,
                "translation": (translations or {}).get(start_ms, ""),
            })
        return payload

    def _push_lyrics(self) -> None:
        data = self._line_payload(self._lines, self._translations, self._duration)
        blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self._run(f"window.foliaStage && window.foliaStage.setLyrics({blob});")

    def _push_cover(self) -> None:
        blob = json.dumps(self._cover_data_url or "", ensure_ascii=False)
        self._run(f"window.foliaStage && window.foliaStage.setCover({blob});")

    def _push_time(self) -> None:
        seconds = round(max(0, int(self._position)) / 1000, 3)
        duration = round(max(0, int(self._duration)) / 1000, 3)
        flag = "true" if self._playing else "false"
        self._run(
            f"window.foliaStage && window.foliaStage.setTime({seconds},{duration},{flag});"
        )

    def _push_song(self) -> None:
        title = json.dumps(self._song_title or "", ensure_ascii=False)
        artist = json.dumps(self._song_artist or "", ensure_ascii=False)
        self._run(f"window.foliaStage && window.foliaStage.setSong({title},{artist});")

    def _push_mode(self) -> None:
        mode = json.dumps(self._mode or "classic", ensure_ascii=False)
        self._run(f"window.foliaStage && window.foliaStage.setMode({mode});")

    def _push_tuning(self) -> None:
        blob = json.dumps(self._tuning, ensure_ascii=False, separators=(",", ":"))
        self._run(f"window.foliaStage && window.foliaStage.setTuning({blob});")

    def set_tuning(self, **changes: object) -> None:
        self._tuning.update({key: value for key, value in changes.items() if value is not None})
        if self._ready:
            self._push_tuning()

    def set_song(self, title: str, artist: str) -> None:
        self._song_title = str(title or "")
        self._song_artist = str(artist or "")
        if self._ready:
            self._push_song()

    def set_mode(self, mode: str) -> None:
        self._mode = str(mode or "classic")
        if self._ready:
            self._push_mode()

    def set_lyrics(self, lines: list[tuple[int, str]],
                   translations: dict[int, str] | None) -> None:
        self._lines = list(lines or [])
        self._translations = dict(translations or {})
        if self._ready:
            self._push_lyrics()

    def set_cover(self, pix: QPixmap | None) -> None:
        """Push the track cover once; the page derives its theme from the same image."""
        key = pix.cacheKey() if pix is not None and not pix.isNull() else 0
        if key == self._cover_key:
            return
        self._cover_key = key
        self._cover_data_url = _pixmap_data_url(pix) if key else ""
        if self._ready:
            self._push_cover()

    def set_duration(self, duration_ms: int) -> None:
        self._duration = max(0, int(duration_ms or 0))
        if self._ready:
            self._push_lyrics()

    def set_transport(self, position_ms: int, playing: bool) -> None:
        self._position = max(0, int(position_ms or 0))
        self._playing = bool(playing)
        if self._ready:
            self._push_time()

    def closeEvent(self, event) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None
        if self._view is not None:
            self._view.stop()
        super().closeEvent(event)


def QTimerSingleShot(parent, delay: int, callback) -> None:
    """Tiny helper kept local so importing folia_web has no Qt timer side effects."""
    from PySide6.QtCore import QTimer

    QTimer.singleShot(delay, callback)
