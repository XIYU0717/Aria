"""Provider registry: discovery providers and stream providers.

The discover page only talks to this layer.  Every provider exposes the same
small protocol (recommend / charts / categories / playlist / chart / search /
stream), so a built-in source or a user-supplied aggregator can be added
without teaching the page or the player about it.

Built-in providers:
* ``listen1``  - bundled Node sidecar (Netease / QQ / Kugou / Kuwo / Qishui)
* ``buguyy``   - public Buguyy API (search + two-step stream)

Additional aggregators are declared in ``%APPDATA%\\Aria\\providers.json`` and
only need to serve the same HTTP endpoints on their own loopback port.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import adapters
import listen1
from catalog import BUILTIN

DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Aria"
PROVIDERS_FILE = DATA_DIR / "providers.json"

BUGU_BASE = "https://www.buguyy.top"
BUGU_UA = adapters.UA


class ProviderError(RuntimeError):
    pass


def _http_json(url: str, timeout: int = 12, headers: dict | None = None) -> dict:
    request_headers = {
        "Accept": "application/json",
        "User-Agent": BUGU_UA,
        "Referer": BUGU_BASE + "/",
    }
    request_headers.update(headers or {})
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=request_headers),
                timeout=max(4, int(timeout or 12))) as response:
            body = response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"请求失败：{exc}") from exc
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ProviderError("接口返回非 JSON") from exc
    if not isinstance(data, dict):
        raise ProviderError("接口返回格式不正确")
    return data


def _uniq_tracks(tracks: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in tracks:
        key = (str(item.get("title") or "").strip().lower(),
               str(item.get("artist") or "").strip().lower(),
               str(item.get("provider_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


class DiscoverProvider:
    """One source of playlist metadata plus an optional stream resolver."""

    stream_via = "sidecar"

    def __init__(self, provider_id: str, name: str, short: str = "",
                 accent: str = "#6E8BFF", discover: bool = True) -> None:
        self.id = provider_id
        self.name = name
        self.short = short or name
        self.accent = accent
        self.discover = bool(discover)

    # -- metadata -------------------------------------------------------
    def meta(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "short": self.short,
            "accent": self.accent,
            "discover": self.discover,
            "stream_via": self.stream_via,
        }

    # -- discovery ------------------------------------------------------
    def recommend(self, limit: int = 16, category: str = "",
                  category_id: str = "", sort_id: str = "",
                  platform: str = "") -> list[dict]:
        raise NotImplementedError

    def charts(self, platform: str = "") -> list[dict]:
        raise NotImplementedError

    def categories(self, platform: str = "") -> list[dict]:
        raise NotImplementedError

    def playlist(self, playlist_id: str) -> dict:
        raise NotImplementedError

    def chart(self, chart_id: str) -> dict:
        raise NotImplementedError

    # -- playback -------------------------------------------------------
    def search(self, keyword: str, limit: int = 10) -> list[dict]:
        raise NotImplementedError

    def stream(self, track_id: str) -> str:
        raise NotImplementedError


class Listen1Provider(DiscoverProvider):
    """The bundled sidecar, exposed as one source.

    Internally it still queries the five public web endpoints, but the rest of
    the app sees a single provider whose tracks carry that endpoint's own id.
    """

    PLATFORMS = (
        ("netease", "网易云音乐", "网易云", "#FF4D6D"),
        ("qq", "QQ音乐", "QQ音乐", "#31C27C"),
        ("kugou", "酷狗音乐", "酷狗", "#3BA9FF"),
        ("kuwo", "酷我音乐", "酷我", "#FF8A3D"),
        ("qishui", "汽水音乐", "汽水", "#FF2D55"),
    )

    def __init__(self) -> None:
        super().__init__("listen1", "Listen1", "Listen1", "#7C9EFF")

    # -- helpers --------------------------------------------------------
    @classmethod
    def _source_meta(cls, source: str) -> tuple[str, str]:
        for pid, name, short, _accent in cls.PLATFORMS:
            if pid == source:
                return name, short
        return source, source

    @classmethod
    def _stamp(cls, item: dict, source: str) -> dict:
        name, short = cls._source_meta(source)
        out = dict(item or {})
        out["provider_id"] = "listen1"
        out["source"] = source
        out["source_name"] = name
        out["source_short"] = short
        out["stream_via"] = "sidecar"
        return out

    @classmethod
    def _stamp_track(cls, item: dict, source: str) -> dict:
        out = cls._stamp(item, source)
        out["provider_track_id"] = str(
            item.get("provider_track_id") or item.get("listen1_id")
            or item.get("id") or "")
        return out

    @classmethod
    def _stamp_playlist(cls, item: dict) -> dict:
        # Playlist ids already carry the platform prefix (neplaylist_,
        # kgplaylist_, ...); the sidecar dispatches on it.
        out = dict(item or {})
        source = str(out.get("source") or "")
        name, short = cls._source_meta(source)
        out["provider_id"] = "listen1"
        out["source_name"] = name
        out["source_short"] = short
        return out

    @classmethod
    def _stamp_result(cls, data: dict) -> dict:
        out = dict(data or {})
        out["info"] = cls._stamp_playlist(out.get("info") or {})
        source = str((out.get("info") or {}).get("source") or "")
        out["tracks"] = [cls._stamp_track(t, source) for t in (out.get("tracks") or [])]
        return out

    # -- discovery ------------------------------------------------------
    def recommend(self, limit: int = 16, category: str = "",
                  category_id: str = "", sort_id: str = "",
                  platform: str = "") -> list[dict]:
        if category_id or category:
            encoded = category_id or category
            if platform and "|" not in str(encoded):
                encoded = f"{platform}|{encoded}"
            source, _, cat = str(encoded).partition("|")
            if not source:
                source, cat = "netease", str(encoded)
            items = listen1.SIDECAR.recommend(
                source, limit=limit, category=cat, category_id=cat,
                sort_id=sort_id,
            )
            return [self._stamp_playlist(i) for i in items]
        if platform:
            items = listen1.SIDECAR.recommend(platform, limit=limit)
            return [self._stamp_playlist(i) for i in items]
        per = max(3, min(8, (limit + len(self.PLATFORMS) - 1) // len(self.PLATFORMS)))

        def one(platform: tuple) -> list[dict]:
            pid = platform[0]
            try:
                return [self._stamp_playlist(i)
                        for i in listen1.SIDECAR.recommend(pid, limit=per)]
            except Exception:  # noqa: BLE001
                return []

        items: list[dict] = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            for part in pool.map(one, self.PLATFORMS):
                items.extend(part or [])
        return items[:max(limit, per)]

    def charts(self, platform: str = "") -> list[dict]:
        if platform:
            items = listen1.SIDECAR.charts(platform)
            return [self._stamp_playlist(i) for i in items][:8]

        def one(platform: tuple) -> list[dict]:
            pid = platform[0]
            try:
                return [self._stamp_playlist(i)
                        for i in listen1.SIDECAR.charts(pid)][:8]
            except Exception:  # noqa: BLE001
                return []

        items: list[dict] = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            for part in pool.map(one, self.PLATFORMS):
                items.extend(part or [])
        return items

    def categories(self, platform: str = "") -> list[dict]:
        groups: list[dict] = []
        targets = ([p for p in self.PLATFORMS if p[0] == platform]
                   if platform else list(self.PLATFORMS))
        for pid, name, short, _accent in targets:
            try:
                data = listen1.SIDECAR.categories(pid)
            except Exception:  # noqa: BLE001
                continue
            for group in data:
                items = []
                for item in (group.get("items") or [])[:14]:
                    items.append({
                        "name": item.get("name") or "",
                        "id": f"{pid}|{item.get('id') or item.get('name') or ''}",
                        "sort_id": item.get("sort_id") or "",
                    })
                if items:
                    groups.append({"name": f"{short} · {group.get('name') or '分类'}",
                                   "items": items})
        return groups

    def playlist(self, playlist_id: str) -> dict:
        return self._stamp_result(listen1.SIDECAR.playlist(playlist_id))

    def chart(self, chart_id: str) -> dict:
        source = "netease"
        for prefix, pid in (("neplaylist_", "netease"), ("kgtop_", "kugou"),
                            ("kwtop_", "kuwo"), ("sodatop_", "qishui"),
                            ("qqtop_", "qq")):
            if str(chart_id).startswith(prefix):
                source = pid
                break
        return self._stamp_result(listen1.SIDECAR.chart(source, chart_id))

    # -- playback -------------------------------------------------------
    def search(self, keyword: str, limit: int = 10) -> list[dict]:
        per = max(4, min(20, (max(1, limit) + 4) // 5))

        def one(platform: tuple) -> list[dict]:
            pid = platform[0]
            try:
                return [self._stamp_track(t, pid)
                        for t in listen1.SIDECAR.search(pid, keyword, limit=per)]
            except Exception:  # noqa: BLE001
                return []

        tracks: list[dict] = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            for part in pool.map(one, self.PLATFORMS):
                tracks.extend(part or [])
        return _uniq_tracks(tracks)[:max(1, limit)]

    def stream(self, track_id: str) -> str:
        return listen1.SIDECAR.stream(track_id)


class BuguyyProvider(DiscoverProvider):
    """Buguyy: public search + a fresh signed URL at play time."""

    stream_via = "http"

    def __init__(self) -> None:
        super().__init__("buguyy", "布谷音乐", "布谷", "#F2A65A")
        site = next(i for i in BUILTIN if i["id"] == "buguyy")
        self.site = dict(site)

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _track_id(item: dict) -> str:
        raw = str(item.get("id") or "")
        return raw.split(":", 1)[1] if ":" in raw else raw

    def _track(self, item: dict) -> dict:
        sid = str(item.get("id") or "")
        track = {
            "id": f"buguyy:{sid}",
            "provider_id": self.id,
            "provider_track_id": self._track_id(item),
            "title": item.get("title") or "",
            "artist": item.get("singer") or "",
            "album": item.get("album") or "",
            "cover": item.get("picurl") or item.get("cover") or "",
            "duration": int(item.get("duration") or 0),
            "source": self.id,
            "source_id": self.id,
            "source_name": self.name,
            "source_accent": self.accent,
            "source_short": self.short,
            "kind": "buguyy",
            "stream_via": "http",
            "audio_from": f"{BUGU_BASE}/api/geturl?id={urllib.parse.quote(sid)}",
            "audio_path": "url",
        }
        # Do not copy Buguyy's `about` into `lyrics_text`.  It is not a
        # trustworthy per-track lyric: the catalogue sometimes returns a
        # different song's sheet for a matching title, which is exactly what
        # made the first play look mismatched.  Online lyric matching is used
        # instead.
        return track

    def _list(self) -> list[dict]:
        data = _http_json(f"{BUGU_BASE}/api/newlist")
        return [self._track(i) for i in (data.get("data") or [])]

    def _heji(self, cid: int, pages: int = 2) -> tuple[list[dict], int]:
        tracks: list[dict] = []
        total = 0
        for page in range(1, max(1, pages) + 1):
            data = _http_json(
                f"{BUGU_BASE}/api/heji?cid={int(cid)}&page={page}"
                f"&timestamp={__import__('time').time_ns() // 1_000_000}")
            total = int(data.get("totalCount") or total or 0)
            rows = data.get("data") or []
            if not rows:
                break
            tracks.extend(self._track(i) for i in rows)
            if not data.get("hasNext"):
                break
        return tracks, total

    # -- discovery ------------------------------------------------------
    def recommend(self, limit: int = 16, category: str = "",
                  category_id: str = "", sort_id: str = "",
                  platform: str = "") -> list[dict]:
        new = self._list()
        _heji_tracks, heji_total = self._heji(11, pages=1)
        _mix_tracks, mix_total = self._heji(12, pages=1)
        cover = next((t.get("cover") for t in new if t.get("cover")), "")
        items = [
            {
                "id": "bugu:newlist",
                "title": "布谷音乐 · 每日新歌",
                "cover": cover,
                "source": self.id,
                "provider_id": self.id,
                "source_name": self.name,
                "source_short": self.short,
                "count": len(new),
                "count_unit": "首",
                "subtitle": "今天刚更新",
            },
            {
                "id": "bugu:heji11",
                "title": "布谷音乐 · 热播串烧",
                "cover": cover,
                "source": self.id,
                "provider_id": self.id,
                "source_name": self.name,
                "source_short": self.short,
                "count": heji_total or 414,
                "count_unit": "首",
                "subtitle": "连续播放更顺耳",
            },
            {
                "id": "bugu:heji12",
                "title": "布谷音乐 · 主题合集",
                "cover": cover,
                "source": self.id,
                "provider_id": self.id,
                "source_name": self.name,
                "source_short": self.short,
                "count": mix_total or 475,
                "count_unit": "首",
                "subtitle": "按主题整理",
            },
        ]
        return items[:max(1, limit)]

    def charts(self, platform: str = "") -> list[dict]:
        # Buguyy has no separate chart API; its three collection cards are
        # already shown under “热门歌单”, so avoid duplicate chart tiles.
        return []

    def categories(self, platform: str = "") -> list[dict]:
        return []

    def _playlist_result(self, playlist_id: str) -> dict:
        pid = str(playlist_id or "")
        if pid.endswith("heji12") or pid == "bugu:heji12":
            tracks, total = self._heji(12, pages=3)
            title, sub = "布谷音乐 · 主题合集", "主题合集"
        elif pid.endswith("heji11") or pid == "bugu:heji11":
            tracks, total = self._heji(11, pages=3)
            title, sub = "布谷音乐 · 热播串烧", "串烧"
        else:
            tracks = self._list()
            total = len(tracks)
            title, sub = "布谷音乐 · 每日新歌", "每日更新"
        return {
            "info": {
                "id": pid,
                "title": title,
                "cover": tracks[0].get("cover") if tracks else "",
                "source": self.id,
                "provider_id": self.id,
                "source_name": self.name,
                "count": total or len(tracks),
                "count_unit": "首",
                "subtitle": sub,
            },
            "tracks": tracks,
        }

    def playlist(self, playlist_id: str) -> dict:
        return self._playlist_result(playlist_id)

    def chart(self, chart_id: str) -> dict:
        return self._playlist_result(chart_id)

    # -- playback -------------------------------------------------------
    def search(self, keyword: str, limit: int = 10) -> list[dict]:
        keyword = str(keyword or "").strip()
        if not keyword:
            return []
        try:
            tracks = adapters.search_site(self.site, keyword, limit=max(1, limit))
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"布谷搜索失败：{exc}") from exc
        out: list[dict] = []
        for track in tracks or []:
            item = dict(track)
            item["provider_id"] = self.id
            item["provider_track_id"] = self._track_id(item)
            item["source"] = self.id
            item["source_name"] = self.name
            item["source_short"] = self.short
            item["stream_via"] = "http"
            out.append(item)
        return out

    def stream(self, track_id: str) -> str:
        sid = str(track_id or "").strip()
        if not sid:
            raise ProviderError("布谷曲目缺少 id")
        info = adapters.fetch_stream({
            "audio_from": f"{BUGU_BASE}/api/geturl?id={urllib.parse.quote(sid)}",
            "audio_path": "url",
        }, timeout=15)
        url = str(info.get("audio") or "")
        if not url:
            raise ProviderError("布谷没有返回可播放地址")
        return url


class HttpProvider(DiscoverProvider):
    """A local HTTP aggregator implementing the same endpoints as the sidecar."""

    stream_via = "http"

    def __init__(self, provider_id: str, name: str, base_url: str,
                 short: str = "", accent: str = "#6E8BFF",
                 token: str = "", discover: bool = True, timeout: int = 12) -> None:
        super().__init__(provider_id, name, short=short, accent=accent, discover=discover)
        if not base_url:
            raise ProviderError(f"provider {provider_id} 缺少 base_url")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = max(3, int(timeout or 12))

    def _get(self, path: str, **params: Any) -> dict:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v not in (None, "")})
        url = f"{self.base_url}{path}"
        if query:
            url += f"?{query}"
        headers = {"Accept": "application/json", "User-Agent": "Aria/1.0"}
        if self.token:
            headers["X-Aria-Token"] = self.token
        try:
            import aggregator_tools
            with aggregator_tools.request_context(self.id):
                with urllib.request.urlopen(
                        urllib.request.Request(url, headers=headers),
                        timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"{self.name} 不可用：{exc}") from exc
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise ProviderError(f"{self.name} 返回非 JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError(f"{self.name} 返回格式不正确")
        if data.get("error"):
            raise ProviderError(str(data["error"])[:160])
        return data

    def _track(self, track: dict) -> dict:
        item = dict(track)
        track_id = str(item.get("provider_track_id") or item.get("id") or "")
        item["provider_id"] = self.id
        item["provider_track_id"] = track_id
        item["id"] = item.get("id") or f"{self.id}:{track_id}"
        item["source"] = self.id
        item["source_name"] = self.name
        item["source_short"] = self.short or self.name
        item["stream_via"] = "http"
        return item

    def _tracks(self, value) -> list[dict]:
        return [self._track(t) for t in (value or []) if isinstance(t, dict)]

    def recommend(self, limit: int = 16, category: str = "",
                  category_id: str = "", sort_id: str = "",
                  platform: str = "") -> list[dict]:
        data = self._get("/recommend", source=self.id, limit=limit,
                         category=category, category_id=category_id,
                         sort_id=sort_id)
        return self._tracks(data.get("items"))

    def charts(self, platform: str = "") -> list[dict]:
        return self._tracks(self._get("/charts", source=self.id).get("items"))

    def categories(self, platform: str = "") -> list[dict]:
        return self._get("/categories", source=self.id).get("groups") or []

    def playlist(self, playlist_id: str) -> dict:
        data = self._get("/playlist", id=playlist_id)
        data["tracks"] = self._tracks(data.get("tracks"))
        return data

    def chart(self, chart_id: str) -> dict:
        data = self._get("/chart", source=self.id, id=chart_id)
        data["tracks"] = self._tracks(data.get("tracks"))
        return data

    def search(self, keyword: str, limit: int = 10) -> list[dict]:
        data = self._get("/search", source=self.id, keyword=keyword, limit=limit)
        return self._tracks(data.get("tracks"))

    def stream(self, track_id: str) -> str:
        data = self._get("/stream", id=track_id)
        return str(data.get("url") or "")


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, DiscoverProvider] = {}
        self._custom_ids: set[str] = set()

    def register(self, provider: DiscoverProvider) -> None:
        if not provider.id:
            raise ProviderError("provider id 不能为空")
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> DiscoverProvider | None:
        return self._providers.get(str(provider_id or ""))

    def all(self) -> list[DiscoverProvider]:
        return list(self._providers.values())

    def discoverable(self) -> list[DiscoverProvider]:
        return [p for p in self._providers.values() if p.discover]

    def platform_meta(self) -> dict[str, dict]:
        return {
            p.id: {"name": p.name, "short": p.short, "accent": p.accent}
            for p in self.discoverable()
        }

    def search_by(self, provider_id: str, keyword: str,
                  limit: int = 20) -> list[dict]:
        provider = self.get(provider_id)
        if provider is None:
            return []
        try:
            return list(provider.search(keyword, limit=limit))
        except Exception:  # noqa: BLE001
            return []

    # -- external providers ---------------------------------------------
    def load_custom(self, path: Path | None = None) -> list[str]:
        """(Re)register user-supplied HTTP aggregators from providers.json."""
        for provider_id in list(self._custom_ids):
            self._providers.pop(provider_id, None)
        self._custom_ids.clear()

        target = Path(path or PROVIDERS_FILE)
        if not target.exists():
            return []
        try:
            entries = json.loads(target.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []
        if isinstance(entries, dict):
            entries = entries.get("providers") or []
        added: list[str] = []
        if not isinstance(entries, list):
            return added
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("disabled"):
                continue
            try:
                provider = HttpProvider(
                    str(entry.get("id") or ""),
                    str(entry.get("name") or entry.get("id") or ""),
                    str(entry.get("base_url") or ""),
                    short=str(entry.get("short") or ""),
                    accent=str(entry.get("accent") or "#6E8BFF"),
                    token=str(entry.get("token") or ""),
                    discover=bool(entry.get("discover", True)),
                    timeout=int(entry.get("timeout") or 12),
                )
            except Exception:  # noqa: BLE001
                continue
            if provider.id in self._providers:
                continue
            self.register(provider)
            self._custom_ids.add(provider.id)
            added.append(provider.id)
        return added


REGISTRY = ProviderRegistry()
for _provider in (Listen1Provider(), BuguyyProvider()):
    REGISTRY.register(_provider)

REGISTRY.load_custom()

# Backwards-friendly name used by the discover page.
PLATFORMS = REGISTRY.platform_meta()
