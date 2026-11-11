"""Aria entry point."""

import os
import sys
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

import theme
from db import DATA_DIR, DB
from window import MainWindow

SINGLE_INSTANCE_NAME = "Aria.6F2A0A5E6E7B4B5A9E3D1A2B3C4D5E6F"


def _acquire_single_instance() -> QLocalServer | None:
    """Return a listening server, or ``None`` when another Aria owns the name."""
    socket = QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_NAME)
    if socket.waitForConnected(700):
        socket.write(b"activate")
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return None
    socket.abort()
    QLocalServer.removeServer(SINGLE_INSTANCE_NAME)
    server = QLocalServer()
    if not server.listen(SINGLE_INSTANCE_NAME):
        return None
    return server


def build_app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QLinearGradient, QPainterPath
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0, QColor("#5F86FF"))
        grad.setColorAt(1, QColor("#A071FF"))
        path = QPainterPath()
        path.addRoundedRect(QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88),
                            size * 0.26, size * 0.26)
        p.fillPath(path, grad)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        bars = [0.28, 0.5, 0.72, 0.5, 0.3]
        bar_w = size * 0.075
        for i, ratio in enumerate(bars):
            h = size * 0.42 * ratio * 1.5
            x = size * 0.24 + i * size * 0.12
            p.drawRoundedRect(QRectF(x, size / 2 - h / 2, bar_w, h), bar_w / 2, bar_w / 2)
        p.end()
        icon.addPixmap(pm)
    return icon


def _selftest() -> int:
    """Headless check of search + playback (used to validate the packaged exe)."""
    import adapters
    from player import Player, StreamProxy

    lines: list[str] = []

    def log(text: str) -> None:
        lines.append(text)
        print(text)

    app = QApplication(sys.argv)
    db = DB()
    sites = db.sites(enabled_only=True)
    log(f"sources: {[s['name'] for s in sites]}")
    candidates: list[dict] = []
    for site in sites:
        try:
            tracks = adapters.search_site(site, "晴天", limit=3)
            log(f"  {site['name']}: {len(tracks)} tracks")
            candidates.extend(tracks[:2])
        except Exception as exc:  # noqa: BLE001
            log(f"  {site['name']}: FAILED {str(exc)[:90]}")
    if not candidates:
        log("no playable track")
        _write_selftest_log(lines)
        return 2
    candidates = candidates[:5]
    proxy = StreamProxy()
    player = Player(proxy)
    from PySide6.QtCore import QTimer

    state = {"index": 0, "ok": False, "busy": True}

    def finish() -> None:
        log(f"playback: pos={player.position()} dur={player.duration()} "
            f"status={'ok' if state['ok'] else 'fail'} attempts={state['index']}")
        track = state.get("track")
        if track:
            try:
                import lyrics
                data = lyrics.fetch_lyrics(track, timeout=6)
                log(f"lyrics: lines={len(data.get('lines') or [])} "
                    f"source={data.get('source') or '-'} "
                    f"first={(data.get('lines') or [('', '')])[0][1][:18]}")
            except Exception as exc:  # noqa: BLE001
                log(f"lyrics: FAILED {str(exc)[:80]}")
        app.quit()

    def attempt() -> None:
        if state["index"] >= len(candidates):
            state["busy"] = False
            finish()
            return
        track = dict(candidates[state["index"]])
        state["index"] += 1
        try:
            if not track.get("audio") and track.get("audio_from"):
                track["audio"] = adapters.resolve_audio(track)
        except Exception as exc:  # noqa: BLE001
            log(f"  resolve failed: {track.get('title')} {str(exc)[:60]}")
            QTimer.singleShot(0, attempt)
            return
        log(f"  playing: {track['title']} · {track.get('artist', '')}")
        state["track"] = track
        player.play_track(track)

        def check() -> None:
            if player.position() > 0 and player.duration() > 0:
                state["ok"] = True
                state["busy"] = False
                finish()
            else:
                player.stop()
                QTimer.singleShot(0, attempt)

        QTimer.singleShot(6500, check)

    QTimer.singleShot(0, attempt)
    QTimer.singleShot(60000, lambda: app.quit() if state["busy"] else None)
    app.exec()
    player.stop()
    proxy.stop()
    _write_selftest_log(lines)
    return 0 if state["ok"] else 3


def _write_selftest_log(lines: list[str]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "selftest.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _warm_services(timeout: float = 4.0) -> dict:
    """Start local service sidecars before building the main window.

    The UI used to appear first and pay for Node startup on its first request,
    which showed up as a short freeze.  Warm-up is now bounded: a broken or
    slow service must never keep the window from appearing.
    """
    import threading

    done = threading.Event()
    state: dict = {"listen1": False, "tools": [], "errors": []}

    def work() -> None:
        try:
            import listen1
            ok = listen1.SIDECAR.start(timeout=max(1.5, timeout))
            state["listen1"] = bool(ok)
            if not ok:
                state["errors"].append(listen1.SIDECAR.error or "Listen1 未启动")
        except Exception as exc:  # noqa: BLE001
            state["errors"].append(f"Listen1 启动异常：{exc}")
        try:
            import aggregator_tools
            state["tools"] = aggregator_tools.start_all(budget=max(1.0, timeout))
        except Exception as exc:  # noqa: BLE001
            state["errors"].append(f"聚合工具检查异常：{exc}")
        finally:
            done.set()

    thread = threading.Thread(target=work, name="aria-service-warmup", daemon=True)
    thread.start()
    # Do not let a hung sidecar or a broken service entry delay the window.
    done.wait(max(1.0, timeout))
    if not done.is_set():
        state["errors"].append("服务预热超时，主窗口先启动")
    return state


def _log_startup(state: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        text = (f"listen1={state.get('listen1')} tools={state.get('tools')} "
                f"errors={state.get('errors')}\n")
        (DATA_DIR / "startup.log").write_text(text, encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()
    # QtWebEngine needs to share the GL context with the app before QApplication
    # exists; set it once here so the embedded Folia stage can create its view.
    from PySide6.QtCore import QCoreApplication
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Aria")
    app.setApplicationDisplayName("Aria · 全网音乐聚合")
    app.setQuitOnLastWindowClosed(False)
    # A second click on the desktop icon must wake the running tray instance,
    # never create a competing player/window.
    instance = _acquire_single_instance()
    if instance is None:
        return 0
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setWindowIcon(build_app_icon())

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(theme.BG))
    palette.setColor(QPalette.Base, QColor(theme.BG_SOFT))
    palette.setColor(QPalette.Text, QColor(theme.TEXT))
    palette.setColor(QPalette.WindowText, QColor(theme.TEXT))
    palette.setColor(QPalette.Button, QColor(theme.BG_SOFT))
    palette.setColor(QPalette.ButtonText, QColor(theme.TEXT))
    palette.setColor(QPalette.Highlight, QColor(theme.ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(palette)
    app.setStyleSheet(theme.QSS)

    def handle_exception(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(DATA_DIR / "error.log", "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = handle_exception

    import aggregator_tools
    import listen1
    app.aboutToQuit.connect(listen1.SIDECAR.stop)
    app.aboutToQuit.connect(aggregator_tools.stop_all)

    # Service warm-up is intentionally before MainWindow: Node startup no longer
    # lands while the window is painting.  It is bounded and fail-open.
    startup = _warm_services()
    _log_startup(startup)

    db = DB()
    window = MainWindow(db)
    window.show()

    def activate_existing() -> None:
        while instance.hasPendingConnections():
            client = instance.nextPendingConnection()
            try:
                client.readAll()
            except Exception:  # noqa: BLE001
                pass
            client.deleteLater()
        window.activate_from_second_instance()

    instance.newConnection.connect(activate_existing)
    app.aboutToQuit.connect(instance.close)

    # Nothing network-heavy starts before this point: Listen1 and installed
    # aggregator tools are now lazily started by their first request.  This
    # removes the old ~2 s freeze caused by Node spawning before the first
    # frame, while the tray and single-instance listeners stay resident.
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
