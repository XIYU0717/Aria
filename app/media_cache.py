"""Local audio cache for aggregator sources.

Official API sources (iTunes / Audius / Deezer / ...) keep streaming through
the Range-aware proxy.  Aggregator sources are downloaded once into this
cache and then played from disk, so the next play is instant and does not
depend on a freshly signed URL.

Cache entries are keyed by the same normalised song identity used by the
favourites system, not by the temporary audio URL: Bugu rotates its CDN URL
on every request, so a URL-keyed cache would never hit.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

from adapters import UA
from db import DATA_DIR
from keys import song_key

# A single download may never hold a QThreadPool worker longer than this.
DOWNLOAD_HARD_SECONDS = 180
DOWNLOAD_STALL_SECONDS = 45
DOWNLOAD_READ_SECONDS = 30

# These adapters return a direct official-API stream (or a preview stream).
# They are intentionally *not* pre-downloaded.
OFFICIAL_KINDS = {
    "itunes",
    "audius",
    "deezer",
    "jamendo",
    "openverse",
    "archive",
    "radio",
}

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac", ".webm", ".mp4",
}
CONTENT_TYPE_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "video/mp4": ".mp4",
}


class CacheCancelled(Exception):
    """Raised when the user selects another track while a download is running."""


def default_cache_dir() -> Path:
    """Cache lives on the application's install drive by default."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "cache"
    return Path(__file__).resolve().parent.parent / "cache"


def should_prefetch(track: dict) -> bool:
    """Aggregators are cached; official APIs keep their Range streaming path."""
    if not track or not (track.get("audio") or "").strip():
        return False
    if track.get("is_live"):
        return False
    if track.get("stream_only"):
        return False
    kind = str(track.get("kind") or "").strip().lower()
    return kind not in OFFICIAL_KINDS


def track_key(track: dict) -> str:
    """Stable cache filename stem.  Falls back to a URL hash for odd tracks."""
    track = track or {}
    key = song_key(track)
    if key:
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    raw = "|".join(str(track.get(k) or "") for k in ("audio", "id", "title", "artist"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _extension(url: str, content_type: str) -> str:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in CONTENT_TYPE_EXTENSIONS:
        return CONTENT_TYPE_EXTENSIONS[ctype]
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return suffix
    return ".mp3"


def download_audio(dest_dir: Path | str, key: str, url: str, headers: dict | None = None,
                   proxy: str = "", should_cancel=None, progress=None) -> Path:
    """Download one audio stream atomically to ``dest_dir``.

    The network loop deliberately stays outside ``AudioCache`` and its SQLite
    connection: this function runs on a QThreadPool worker.  A hard deadline
    plus a stall deadline guarantee that a hung source cannot occupy a worker
    thread forever.
    """
    directory = Path(dest_dir)
    directory.mkdir(parents=True, exist_ok=True)
    request_headers = {"User-Agent": UA, "Accept-Encoding": "identity"}
    request_headers.update(headers or {})
    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}
    temp = directory / f"{key}.part-{uuid.uuid4().hex[:10]}"
    hard_deadline = time.time() + DOWNLOAD_HARD_SECONDS
    try:
        with requests.get(url, headers=request_headers, stream=True,
                          timeout=(10, DOWNLOAD_READ_SECONDS),
                          allow_redirects=True, proxies=proxies) as response:
            if response.status_code not in (200, 206):
                raise RuntimeError(f"下载失败：HTTP {response.status_code}")
            ctype = response.headers.get("Content-Type", "")
            if "text/html" in ctype.lower() or "json" in ctype.lower():
                raise RuntimeError("源站返回的不是音频数据")
            total = 0
            try:
                total = int(response.headers.get("Content-Length") or 0)
            except ValueError:
                total = 0
            # Closing the response is the only reliable way to interrupt a
            # socket read that is already blocked inside requests.
            watchdog = threading.Timer(DOWNLOAD_HARD_SECONDS, response.close)
            watchdog.daemon = True
            watchdog.start()
            written = 0
            last_report = 0.0
            last_data = time.time()
            try:
                with open(temp, "wb") as fh:
                    for chunk in response.iter_content(64 * 1024):
                        if should_cancel and should_cancel():
                            raise CacheCancelled()
                        if not chunk:
                            continue
                        now = time.time()
                        if now > hard_deadline:
                            raise TimeoutError("下载超过硬性时限，已中止")
                        if now - last_data > DOWNLOAD_STALL_SECONDS:
                            raise TimeoutError("下载长时间没有数据，已中止")
                        last_data = now
                        fh.write(chunk)
                        written += len(chunk)
                        if progress and (now - last_report > 0.35 or written == total):
                            last_report = now
                            progress(written, total)
            finally:
                watchdog.cancel()
            if written <= 0:
                raise RuntimeError("下载到的音频为空")
            final = directory / f"{key}{_extension(url, ctype)}"
            os.replace(temp, final)
            try:
                os.utime(final, None)
            except OSError:
                pass
            if progress:
                progress(written, total)
            return final
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def _parse_range(value: str, total: int) -> tuple[int, int] | None:
    """Return (start, end) for a single-range request, or None."""
    match = re.match(r"bytes=(\d*)-(\d*)$", (value or "").strip())
    if not match:
        return None
    left, right = match.groups()
    if not left and not right:
        return None
    if left:
        start = int(left)
        end = int(right) if right else (total - 1 if total > 0 else start + 1024 * 1024)
    else:
        length = int(right)
        if total <= 0:
            return None
        start = max(0, total - length)
        end = total - 1
    if total > 0:
        end = min(end, total - 1)
    if start > end:
        return None
    return start, end


class CacheStream:
    """A growing, atomically-finalised ``.part`` file with a Range HTTP view.

    The GUI only ever shows the local proxy URL.  The downloader writes into
    ``<key>.part-*`` and readers wait for bytes as they arrive; when the
    response is complete the file is renamed to the normal cache entry.
    """

    def __init__(self, dest_dir: Path | str, key: str, url: str,
                 headers: dict | None = None, proxy: str = "",
                 cancel_event: threading.Event | None = None) -> None:
        self.directory = Path(dest_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.key = key
        self.url = url
        self.headers = dict(headers or {})
        self.proxy = (proxy or "").strip()
        self.cancel_event = cancel_event
        self.temp = self.directory / f"{key}.part-{uuid.uuid4().hex[:10]}"
        self.total = 0
        self.available = 0
        self.content_type = ""
        self.error = ""
        self.finished = False
        self.final: Path | None = None
        self.ready = threading.Event()
        self._cond = threading.Condition()
        self._response = None
        self._thread = threading.Thread(target=self._run, name="aria-cache-stream", daemon=True)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "CacheStream":
        self._thread.start()
        return self

    def _notify(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def _run(self) -> None:
        response = None
        started = time.monotonic()
        try:
            request_headers = {"User-Agent": UA, "Accept-Encoding": "identity"}
            request_headers.update(self.headers)
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
            response = requests.get(self.url, headers=request_headers, stream=True,
                                    timeout=(10, DOWNLOAD_READ_SECONDS),
                                    allow_redirects=True, proxies=proxies)
            self._response = response
            if response.status_code not in (200, 206):
                raise RuntimeError(f"服务器请求失败（HTTP {response.status_code}）")
            ctype = response.headers.get("Content-Type", "") or ""
            if "text/html" in ctype.lower() or "json" in ctype.lower():
                raise RuntimeError("音源返回的不是音频数据")
            total = 0
            try:
                total = int(response.headers.get("Content-Length") or 0)
            except ValueError:
                total = 0
            # A 206 response may carry the total only in Content-Range.
            content_range = response.headers.get("Content-Range", "") or ""
            if total <= 0 and "/" in content_range:
                try:
                    total = int(content_range.rsplit("/", 1)[-1])
                except ValueError:
                    total = 0
            with self._cond:
                self.total = max(0, total)
                self.content_type = ctype
                self.ready.set()
                self._cond.notify_all()

            watchdog = threading.Timer(DOWNLOAD_HARD_SECONDS, response.close)
            watchdog.daemon = True
            watchdog.start()
            written = 0
            last_data = time.monotonic()
            try:
                with open(self.temp, "wb") as fh:
                    for chunk in response.iter_content(64 * 1024):
                        if self.cancel_event and self.cancel_event.is_set():
                            raise CacheCancelled()
                        if not chunk:
                            continue
                        now = time.monotonic()
                        if now - started > DOWNLOAD_HARD_SECONDS:
                            raise TimeoutError("服务器响应超时")
                        if now - last_data > DOWNLOAD_STALL_SECONDS:
                            raise TimeoutError("服务器请求超时")
                        last_data = now
                        fh.write(chunk)
                        fh.flush()
                        written += len(chunk)
                        with self._cond:
                            self.available = written
                            self._cond.notify_all()
            finally:
                watchdog.cancel()
            if written <= 0:
                raise RuntimeError("音源返回空数据")
            if self.total and written != self.total:
                raise RuntimeError("音频数据不完整")
            final = self.directory / f"{self.key}{_extension(self.url, self.content_type)}"
            # Windows blocks rename while the proxy's reader has the .part open.
            # Readers open per chunk, so a short retry loop reliably gets a gap
            # and keeps progressive playback from turning a finished download
            # into a false "cache failed".
            for attempt in range(120):
                try:
                    os.replace(self.temp, final)
                    break
                except OSError:
                    if attempt >= 119:
                        raise
                    time.sleep(0.05)
            try:
                os.utime(final, None)
            except OSError:
                pass
            with self._cond:
                self.final = final
                self.finished = True
                self._cond.notify_all()
        except CacheCancelled:
            with self._cond:
                self.error = ""
                self._cond.notify_all()
        except Exception as exc:  # noqa: BLE001
            with self._cond:
                self.error = str(exc)[:180] or "服务器请求失败"
                self._cond.notify_all()
        finally:
            try:
                if response is not None:
                    response.close()
            except Exception:
                pass
            if not self.finished and self.final is None:
                try:
                    if self.temp.exists():
                        self.temp.unlink()
                except OSError:
                    pass
            self.ready.set()
            self._notify()

    # ------------------------------------------------------------------ readers
    def wait_for(self, offset: int, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            while self.available < offset and not self.finished and not self.error:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(min(left, 0.5))
            return self.available >= offset

    def read(self, offset: int, length: int) -> bytes:
        with self._cond:
            if offset >= self.available:
                return b""
            count = min(length, self.available - offset)
        path = self.final if (self.final is not None and self.final.is_file()) else self.temp
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                return fh.read(count)
        except OSError:
            return b""

    def serve(self, handler, body: bool = True) -> None:
        """Serve this spool to a local QMediaPlayer request."""
        if not self.ready.wait(10.0):
            self._send_error(handler, 504, "等待音源响应超时")
            return
        if self.error and self.available <= 0:
            self._send_error(handler, 502, self.error)
            return

        total = self.total
        rng = handler.headers.get("Range")
        r = _parse_range(rng, total) if (rng and total > 0) else None
        if r:
            start, end = r
        else:
            start, end = 0, (total - 1 if total > 0 else 0)
        if total > 0 and start >= total:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{total}")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return

        handler.send_response(206 if r else 200)
        ctype = self.content_type or "audio/mpeg"
        handler.send_header("Content-Type", ctype)
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Cache-Control", "no-store")
        if total > 0:
            length = end - start + 1
            handler.send_header("Content-Length", str(length))
            if r:
                handler.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        else:
            handler.send_header("Connection", "close")
            handler.close_connection = True
        handler.end_headers()
        if not body:
            return

        offset = start
        remaining = (end - start + 1) if total > 0 else None
        deadline = time.monotonic() + DOWNLOAD_HARD_SECONDS + 10
        try:
            while remaining is None or remaining > 0:
                data = self.read(offset, min(64 * 1024, remaining or 64 * 1024))
                if data:
                    handler.wfile.write(data)
                    offset += len(data)
                    if remaining is not None:
                        remaining -= len(data)
                    continue
                if self.finished or self.error:
                    break
                if time.monotonic() > deadline:
                    break
                if not self.wait_for(offset + 1, 30.0):
                    if self.error or self.finished:
                        break
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def _send_error(handler, status: int, message: str) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(message.encode("utf-8"))))
        handler.end_headers()
        try:
            handler.wfile.write(message.encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            pass

    def close(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
        response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def format_bytes(size: int | float) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


class AudioCache:
    """Cache index + eviction policy.  Read/use only from the GUI thread."""

    def __init__(self, db) -> None:
        self.db = db
        self.default_dir = default_cache_dir()
        self._last_dir: Path | None = None

    # ------------------------------------------------------------------ paths
    @property
    def directory(self) -> Path:
        raw = (self.db.get_setting("cache_dir", "") or "").strip()
        candidate = Path(os.path.expanduser(raw)) if raw else self.default_dir
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if candidate.is_dir():
                self._last_dir = candidate
                return candidate
        except OSError:
            pass
        fallback = DATA_DIR / "cache"
        fallback.mkdir(parents=True, exist_ok=True)
        self._last_dir = fallback
        return fallback

    @property
    def limit(self) -> int:
        try:
            value = int(self.db.get_setting("cache_limit", "200") or 200)
        except (TypeError, ValueError):
            value = 200
        return max(1, min(10000, value))

    # ------------------------------------------------------------------ index
    def lookup(self, track: dict) -> Path | None:
        key = track_key(track)
        directory = self.directory
        candidates = []
        for path in directory.glob(f"{key}.*"):
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                try:
                    if path.stat().st_size > 0:
                        candidates.append(path)
                except OSError:
                    continue
        if not candidates:
            return None
        try:
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            os.utime(newest, None)  # LRU order for eviction
            return newest
        except OSError:
            return candidates[0]

    def invalidate(self, path: Path | str) -> None:
        try:
            Path(path).unlink()
        except OSError:
            pass

    def stats(self) -> tuple[int, int]:
        count = 0
        total = 0
        for path in self._audio_files():
            try:
                count += 1
                total += path.stat().st_size
            except OSError:
                continue
        return count, total

    def _audio_files(self) -> list[Path]:
        directory = self.directory
        files: list[Path] = []
        try:
            entries = list(directory.iterdir())
        except OSError:
            return files
        for path in entries:
            try:
                if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                    files.append(path)
            except OSError:
                continue
        return files

    def enforce_limit(self, protected_keys: set[str] | None = None) -> int:
        """Delete the oldest entries until the limit is met.

        ``protected_keys`` are the songs currently playing / prefetched /
        being written; they must never be evicted.  Mtime is the persisted
        LRU rank: a cache hit touches the file, so the next eviction starts
        from the least recently played entry.
        """
        files = self._audio_files()
        limit = self.limit
        protected = {str(k) for k in (protected_keys or set()) if k}
        candidates: list[Path] = []
        for path in files:
            if path.stem in protected:
                continue
            candidates.append(path)
        if len(files) <= limit:
            return 0
        def mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0
        candidates.sort(key=mtime)
        excess = len(files) - limit
        removed = 0
        for path in candidates:
            if removed >= excess:
                break
            try:
                path.unlink()
                removed += 1
            except OSError:
                # A player may still hold the file open on Windows.  Leave it
                # in place and try again on the next cache write.
                continue
        return removed

    def clear(self) -> tuple[int, int]:
        count, total = self.stats()
        for path in self._audio_files():
            try:
                path.unlink()
            except OSError:
                pass
        for path in self.directory.glob("*.part-*"):
            try:
                path.unlink()
            except OSError:
                pass
        return count, total
