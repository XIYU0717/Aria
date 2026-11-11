"""Audio playback: a local Range-aware proxy plus a QMediaPlayer wrapper."""

import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from adapters import UA
from media_cache import (
    AudioCache,
    CacheCancelled,
    CacheStream,
    download_audio,
    should_prefetch,
    track_key,
)


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):  # silence
        pass

    def do_HEAD(self):
        self._serve(body=False)

    def do_GET(self):
        self._serve(body=True)

    def _serve(self, body=True):
        registry = self.server.registry  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        token = parsed.path.rsplit("/", 1)[-1]
        entry = registry.get(token)
        if not entry:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        target, headers = entry["url"], entry["headers"]
        spool = entry.get("spool")
        if spool is not None:
            try:
                spool.serve(self, body=body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        headers = {"User-Agent": UA, **headers}
        rng = self.headers.get("Range")
        if rng:
            headers["Range"] = rng
        try:
            upstream = requests.get(target, headers=headers, stream=True, timeout=(6, 25),
                                    allow_redirects=True)
        except Exception:
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_response(upstream.status_code if upstream.status_code in (200, 206) else 200)
        ctype = upstream.headers.get("Content-Type", "audio/mpeg")
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        for key in ("Content-Length", "Content-Range"):
            if upstream.headers.get(key):
                self.send_header(key, upstream.headers[key])
        self.send_header("Cache-Control", "no-store")
        if not upstream.headers.get("Content-Length"):
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if not body:
            upstream.close()
            return
        try:
            for chunk in upstream.iter_content(64 * 1024):
                if chunk:
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            upstream.close()


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class StreamProxy:
    def __init__(self) -> None:
        self.registry: dict[str, dict] = {}
        self.server = _QuietServer(("127.0.0.1", 0), _ProxyHandler)
        self.server.registry = self.registry  # type: ignore[attr-defined]
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def register(self, url: str, headers: dict | None = None,
                 spool: CacheStream | None = None) -> str:
        if len(self.registry) > 60:
            for key in list(self.registry.keys())[:30]:
                self.registry.pop(key, None)
        token = uuid.uuid4().hex
        self.registry[token] = {
            "url": url, "headers": headers or {}, "at": time.time(), "spool": spool,
        }
        return f"http://127.0.0.1:{self.port}/s/{token}"

    def stop(self) -> None:
        try:
            self.server.shutdown()
        except Exception:
            pass


class _CacheSignals(QObject):
    progress = Signal(int, int, int)   # token, percent (-1 if unknown), bytes
    done = Signal(int, str, dict)      # token, local path, track
    failed = Signal(int, str)          # token, message (empty means cancelled)


class _CacheWorker(QRunnable):
    """Download one aggregator stream without blocking the GUI thread."""

    def __init__(self, token: int, cache_dir: Path, key: str, track: dict, url: str,
                 headers: dict, proxy: str, cancel_event: threading.Event) -> None:
        super().__init__()
        self.token = token
        self.cache_dir = cache_dir
        self.key = key
        self.track = dict(track)
        self.url = url
        self.headers = dict(headers or {})
        self.proxy = proxy
        self.cancel_event = cancel_event
        self.signals = _CacheSignals()

    def run(self) -> None:
        def cancelled() -> bool:
            return self.cancel_event.is_set()

        def on_progress(written: int, total: int) -> None:
            percent = int(written * 100 / total) if total > 0 else -1
            try:
                self.signals.progress.emit(self.token, percent, written)
            except RuntimeError:
                pass

        try:
            path = download_audio(self.cache_dir, self.key, self.url, self.headers,
                                  self.proxy, should_cancel=cancelled, progress=on_progress)
            if cancelled():
                raise CacheCancelled()
            self.signals.done.emit(self.token, str(path), self.track)
        except CacheCancelled:
            try:
                self.signals.failed.emit(self.token, "")
            except RuntimeError:
                pass
        except Exception as exc:  # noqa: BLE001
            try:
                self.signals.failed.emit(self.token, str(exc)[:220] or "缓存下载失败")
            except RuntimeError:
                pass


class Player(QObject):
    trackChanged = Signal(dict)
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    stateChanged = Signal(str)
    errorOccurred = Signal(str)
    finished = Signal()
    volumeChanged = Signal(int)
    mediaReady = Signal()
    cacheStarted = Signal(dict)
    cacheProgress = Signal(int, int)  # percent (-1 unknown), bytes
    cacheReady = Signal(dict)
    cacheHit = Signal(dict)

    def __init__(self, proxy: StreamProxy, parent=None, cache: AudioCache | None = None) -> None:
        super().__init__(parent)
        self.proxy = proxy
        self.cache = cache
        self.current: dict = {}
        self.audio = QAudioOutput()
        self.media = QMediaPlayer()
        self.media.setAudioOutput(self.audio)
        self.media.positionChanged.connect(self.positionChanged.emit)
        self.media.durationChanged.connect(self.durationChanged.emit)
        self.media.playbackStateChanged.connect(self._on_state)
        self.media.mediaStatusChanged.connect(self._on_status)
        self.media.errorOccurred.connect(self._on_error)
        self._volume = 65
        self._proxy = ""
        self._load_token = 0
        self._cancel_event: threading.Event | None = None
        self._cache_workers: list[tuple[int, _CacheWorker]] = []
        self._cache_path = ""
        self._streams: dict[str, CacheStream] = {}
        self._prefetch_streams: dict[str, CacheStream] = {}
        self._active_stream: CacheStream | None = None
        self._active_stream_key = ""
        self._stream_error_emitted = False
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(500)
        self._stream_timer.timeout.connect(self._poll_streams)
        self._stream_timer.start()

    # ------------------------------------------------------------------ controls
    def play_track(self, track: dict) -> None:
        if not track:
            return
        self._load_token += 1
        token = self._load_token
        if self._cancel_event:
            self._cancel_event.set()
        self._cancel_event = threading.Event()
        self._cache_path = ""
        self.current = dict(track)
        try:
            new_key = track_key(self.current)
        except Exception:  # noqa: BLE001
            new_key = ""
        if self._active_stream_key and self._active_stream_key != new_key:
            old = self._streams.pop(self._active_stream_key, None)
            if old is not None:
                old.close()
        for old_key, old in list(self._prefetch_streams.items()):
            if old_key != new_key:
                self._prefetch_streams.pop(old_key, None)
                old.close()
        url = (self.current.get("audio") or "").strip()
        if not url:
            self.media.stop()
            self.errorOccurred.emit("无音源")
            return
        self.media.stop()
        # Show the new track immediately; a cache download can take a few
        # seconds and the player bar should not look frozen.
        self.trackChanged.emit(self.current)

        local = self._local_path(url)
        if local is not None:
            self._play_local(local, from_cache=False)
            return

        if self.cache and should_prefetch(self.current):
            hit = self.cache.lookup(self.current)
            if hit is not None:
                self.cacheHit.emit(dict(self.current))
                self._play_local(hit, from_cache=True)
                return
            self._start_stream_cache(token, url)
            return
        self._play_stream(url)

    def set_proxy(self, proxy: str) -> None:
        self._proxy = (proxy or "").strip()

    def _local_path(self, url: str) -> Path | None:
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        if parsed.scheme == "file":
            path = Path(QUrl(url).toLocalFile())
            return path if path.is_file() else None
        if parsed.scheme == "" and Path(url).is_file():
            return Path(url)
        return None

    def _track_headers(self) -> dict:
        headers: dict = {}
        headers.update(self.current.get("audio_headers") or {})
        headers.update(self.current.get("headers") or {})
        return headers

    def _start_stream_cache(self, token: int, url: str) -> None:
        """Start (or reuse) a shared spool and play only its downloaded bytes."""
        try:
            directory = self.cache.directory  # type: ignore[union-attr]
            key = track_key(self.current)
        except Exception:  # noqa: BLE001
            self._play_stream(url)
            return
        # Evict before writing a new entry.  The current song and the next
        # prefetched song are pinned, so an unfinished/active entry is never
        # selected by the LRU pass.
        protected = {key}
        if self.current:
            protected.add(track_key(self.current))
        protected.update(self._prefetch_streams.keys())
        try:
            self.cache.enforce_limit(protected_keys=protected)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        stream = self._prefetch_streams.get(key)
        reused = stream is not None
        if stream is None or (stream.error and stream.available <= 0):
            if stream is not None:
                self._prefetch_streams.pop(key, None)
            stream = CacheStream(directory, key, url, self._track_headers(),
                                 self._proxy, self._cancel_event)
            self._streams[key] = stream
            stream.start()
        else:
            self._prefetch_streams.pop(key, None)
            self._streams[key] = stream
        self._active_stream = stream
        self._active_stream_key = key
        self._stream_error_emitted = False
        # A prefetch that already has a useful lead does not need to flash a
        # buffering cue when the song actually becomes current.
        if not reused or stream.available < 64 * 1024:
            self.cacheStarted.emit(dict(self.current))
        local = self.proxy.register(url, self._track_headers(), spool=stream)
        self.media.setSource(QUrl(local))
        self.media.play()

    def prefetch_track(self, track: dict, url: str | None = None) -> None:
        """Warm the next song silently; no UI signal is emitted."""
        track = dict(track or {})
        if not track or not self.cache or not should_prefetch(track):
            return
        raw_url = (url or track.get("audio") or "").strip()
        if not raw_url:
            return
        if self._local_path(raw_url) is not None:
            return
        try:
            directory = self.cache.directory
            key = track_key(track)
        except Exception:  # noqa: BLE001
            return
        if self.cache.lookup(track) is not None:
            return
        if key in self._streams or key in self._prefetch_streams:
            return
        protected = {key}
        if self.current:
            protected.add(track_key(self.current))
        protected.update(self._prefetch_streams.keys())
        try:
            self.cache.enforce_limit(protected_keys=protected)
        except Exception:  # noqa: BLE001
            pass
        stream = CacheStream(directory, key, raw_url,
                             {**(track.get("audio_headers") or {}),
                              **(track.get("headers") or {})},
                             self._proxy, threading.Event())
        self._prefetch_streams[key] = stream
        stream.start()

    def prefetch_for(self, track: dict) -> None:
        """Convenience: prefetch using the track's own resolved audio URL."""
        self.prefetch_track(track, (track or {}).get("audio"))

    def _poll_streams(self) -> None:
        stream = self._active_stream
        if stream is not None and stream.error and not self._stream_error_emitted:
            self._stream_error_emitted = True
            reason = stream.error or "服务器请求失败"
            self.errorOccurred.emit(reason)
        # Drop completed streams from the live maps; the final file is now a
        # normal cache entry and no longer needs creating a second download.
        for key, item in list(self._streams.items()):
            if item.finished or (item.error and item.available <= 0):
                self._streams.pop(key, None)
        for key, item in list(self._prefetch_streams.items()):
            if item.finished or (item.error and item.available <= 0):
                self._prefetch_streams.pop(key, None)

    def _start_cache(self, token: int, url: str) -> None:
        # Kept for old callers; the progressive path above is the live one.
        self._start_stream_cache(token, url)

    def _drop_cache_worker(self, token: int) -> None:
        self._cache_workers = [(t, w) for (t, w) in self._cache_workers if t != token]

    def _on_cache_progress(self, token: int, percent: int, written: int) -> None:
        if token == self._load_token:
            self.cacheProgress.emit(percent, written)

    def _on_cache_done(self, token: int, path: str, track: dict) -> None:
        self._drop_cache_worker(token)
        if token != self._load_token:
            return
        local = Path(path)
        if not local.is_file():
            self.errorOccurred.emit("缓存文件已丢失，请重试")
            return
        if self.cache:
            try:
                self.cache.enforce_limit()
            except Exception:
                pass
        self.cacheReady.emit(dict(track))
        self._play_local(local, from_cache=True)

    def _on_cache_failed(self, token: int, message: str) -> None:
        self._drop_cache_worker(token)
        if token != self._load_token:
            return
        if message:
            self.errorOccurred.emit(f"缓存下载失败：{message}")

    def _play_local(self, path: Path, from_cache: bool = False) -> None:
        self._cache_path = str(path) if from_cache else ""
        self._active_stream = None
        self._active_stream_key = ""
        self._stream_error_emitted = False
        self.media.setSource(QUrl.fromLocalFile(str(path)))
        self.media.play()

    def _play_stream(self, url: str) -> None:
        self._cache_path = ""
        self._active_stream = None
        self._active_stream_key = ""
        self._stream_error_emitted = False
        self.cacheStarted.emit(dict(self.current))
        local = self.proxy.register(url, self._track_headers())
        self.media.setSource(QUrl(local))
        self.media.play()

    def toggle(self) -> None:
        if not self.current:
            return
        if self.media.source().isEmpty():
            return
        if self.media.playbackState() == QMediaPlayer.PlayingState:
            self.media.pause()
        else:
            self.media.play()

    def pause(self) -> None:
        self.media.pause()

    def stop(self) -> None:
        if self._cancel_event:
            self._cancel_event.set()
        self._load_token += 1
        self._cache_path = ""
        self.media.stop()

    def seek(self, ms: int) -> None:
        if self.media.isSeekable():
            self.media.setPosition(int(ms))

    def set_volume(self, value: int) -> None:
        self._volume = max(0, min(100, int(value)))
        self.audio.setVolume(self._volume / 100)
        self.volumeChanged.emit(self._volume)

    @property
    def volume(self) -> int:
        return self._volume

    def is_playing(self) -> bool:
        return self.media.playbackState() == QMediaPlayer.PlayingState

    def position(self) -> int:
        return self.media.position()

    def duration(self) -> int:
        return self.media.duration()

    def _invalidate_cached(self) -> None:
        if not self._cache_path:
            return
        path = self._cache_path
        self._cache_path = ""
        if self.cache:
            try:
                self.cache.invalidate(path)
            except Exception:
                pass

    # ------------------------------------------------------------------ slots
    def _on_state(self, state) -> None:
        mapping = {
            QMediaPlayer.PlayingState: "playing",
            QMediaPlayer.PausedState: "paused",
            QMediaPlayer.StoppedState: "stopped",
        }
        self.stateChanged.emit(mapping.get(state, "stopped"))
        if state == QMediaPlayer.PlayingState:
            self.mediaReady.emit()

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia:
            self.finished.emit()
        elif status == QMediaPlayer.InvalidMedia:
            self._invalidate_cached()
            self.errorOccurred.emit("无法解码该音频流")

    def _on_error(self, _error, message: str) -> None:
        self._invalidate_cached()
        if message:
            self.errorOccurred.emit(message)
        else:
            self.errorOccurred.emit("无法解码该音频流")
