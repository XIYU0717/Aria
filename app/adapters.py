"""Source adapters, generic JSON adapter, endpoint sniffing and health checks."""

import html as html_lib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urljoin, urlparse, parse_qs, urlencode, urlunparse

import html_rule as html_rule_engine
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

COMMON_TITLE = ["title", "name", "trackName", "track_name", "songname", "song_name", "song",
                "musicName", "music_name", "songName", "歌曲", "歌曲名", "track"]
COMMON_ARTIST = ["artist", "artistName", "artist_name", "singer", "author", "creator",
                 "artists", "歌手", "演唱", "band"]
COMMON_ALBUM = ["album", "collectionName", "album_name", "albumName", "albumTitle", "专辑"]
COMMON_COVER = ["artworkUrl100", "artworkUrl", "artwork", "cover", "cover_url", "pic", "picture",
                "image", "img", "thumbnail", "thumb", "album_image", "albumImage", "icon", "封面"]
COMMON_AUDIO = ["previewUrl", "preview_url", "audio", "audio_url", "audioUrl", "play_url",
                "playUrl", "url", "src", "stream", "streamUrl", "stream_url", "music_url",
                "musicUrl", "file", "mp3", "song_url", "songUrl", "listen_url", "contentUrl",
                "content_url", "试听"]
COMMON_DURATION = ["duration", "trackTimeMillis", "length", "time", "duration_ms", "durationMs",
                   "seconds", "时长"]
COMMON_PAGE = ["page", "pageUrl", "page_url", "link", "detail_url", "detailUrl", "share_url",
               "shareUrl", "foreign_landing_url", "origin_url", "source_url"]
COMMON_QUERY_PARAMS = ("q", "keyword", "keywords", "search", "query", "name", "wd", "s")
AUDIO_SUFFIXES = (".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav", ".opus")
WEB_HEADERS = {"Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
               "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"}
_WEB_API_CACHE: dict[str, dict] = {}


# --------------------------------------------------------------------------- utils
def make_session(proxy: str = "") -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _get_json(session: requests.Session, url: str, params=None, headers=None, timeout=10):
    resp = session.get(url, params=params, headers=headers or {}, timeout=timeout)
    ctype = resp.headers.get("Content-Type", "")
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError("接口返回的不是 JSON" if "html" not in ctype.lower() else "接口返回了 HTML 页面")


def get_path(obj, path: str):
    if not path:
        return obj
    cur = obj
    for part in str(path).split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def find_dict_list(obj, max_depth: int = 6) -> tuple[str, list]:
    """Breadth-first search for the first list of dicts; returns (dot path, list)."""
    queue = [("", obj, 0)]
    while queue:
        path, node, depth = queue.pop(0)
        if depth > max_depth:
            continue
        if isinstance(node, list):
            dicts = [x for x in node if isinstance(x, dict)]
            if len(dicts) >= 1 and dicts and len(dicts) >= max(1, len(node) // 2):
                return path, node
            for i, child in enumerate(node[:3]):
                queue.append((f"{path}.{i}" if path else str(i), child, depth + 1))
        elif isinstance(node, dict):
            for key, child in node.items():
                if isinstance(child, (list, dict)):
                    queue.append((f"{path}.{key}" if path else key, child, depth + 1))
    return "", []


def _first_value(item: dict, candidates: list[str]) -> tuple[str, object]:
    for key in candidates:
        if key in item and item[key] not in (None, "", [], {}):
            return key, item[key]
    lowered = {str(k).lower(): k for k in item.keys()}
    for key in candidates:
        real = lowered.get(key.lower())
        if real is not None and item[real] not in (None, "", [], {}):
            return real, item[real]
    return "", None


def _as_url(value) -> str:
    if isinstance(value, dict):
        for key in ("url", "src", "href", "link", "download"):
            if value.get(key):
                return str(value[key])
        return ""
    if isinstance(value, list) and value:
        return _as_url(value[0])
    if isinstance(value, str) and value.startswith(("http://", "https://", "//")):
        return "https:" + value if value.startswith("//") else value
    return ""


def _duration_seconds(value) -> int:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0
    if num > 10000:  # milliseconds
        num = num / 1000
    return int(num)


def _track(site: dict, title: str, artist: str, album: str, cover: str, audio: str,
           duration=0, page: str = "", native_id=None, live: bool = False,
           preview: bool = False) -> dict:
    return {
        "id": f"{site['id']}:{native_id or abs(hash((title, artist, audio))) & 0xffffff}",
        "title": (title or "未命名").strip(),
        "artist": (artist or "未知艺术家").strip(),
        "album": (album or "").strip(),
        "cover": cover or "",
        "audio": audio or "",
        "duration": int(duration or 0),
        "page": page or "",
        "source_id": site["id"],
        "source_name": site["name"],
        "source_accent": site.get("accent", "#6E8BFF"),
        "is_live": bool(live),
        "is_preview": bool(preview),
        "headers": {},
        "kind": site.get("kind", "generic"),
        "stream_only": bool(site.get("stream_only")
                            or (site.get("mapping") or {}).get("_stream_only")),
    }


# --------------------------------------------------------------------------- builtin adapters
def search_itunes(site, query, limit, session, timeout=10):
    country = (site.get("mapping") or {}).get("country", "us")
    data = _get_json(session, site["search_url"],
                     {"term": query, "media": "music", "entity": "song",
                      "country": country, "limit": limit},
                     timeout=timeout)
    out = []
    for r in data.get("results", []):
        preview = r.get("previewUrl") or ""
        if not preview or "video-ssl" in preview:
            continue
        art = (r.get("artworkUrl100") or "").replace("100x100bb", "400x400bb")
        out.append(_track(site, r.get("trackName", ""), r.get("artistName", ""),
                          r.get("collectionName", ""), art, preview,
                          _duration_seconds(r.get("trackTimeMillis")), r.get("trackViewUrl", ""),
                          r.get("trackId"), live=False, preview=True))
    return out


def search_deezer(site, query, limit, session, timeout=10):
    data = _get_json(session, site["search_url"], {"q": query, "limit": limit}, timeout=timeout)
    out = []
    for r in data.get("data", []):
        album = r.get("album") or {}
        artist = r.get("artist") or {}
        out.append(_track(site, r.get("title", ""), artist.get("name", ""),
                          album.get("title", ""), album.get("cover_medium") or album.get("cover_big", ""),
                          r.get("preview", ""), r.get("duration", 0), r.get("link", ""), r.get("id")))
    return out


def search_audius(site, query, limit, session, timeout=10):
    url = site["search_url"] if "/tracks/search" in site["search_url"] else "https://api.audius.co/v1/tracks/search"
    data = _get_json(session, url, {"query": query, "app_name": "AriaDesktop"}, timeout=timeout)
    rows = data.get("data", [])
    if isinstance(data, list):
        rows = data
    out = []
    for r in rows[:limit]:
        art = r.get("artwork") or {}
        cover = art.get("480x480") or art.get("150x150") if isinstance(art, dict) else ""
        out.append(_track(site, r.get("title", ""), (r.get("user") or {}).get("name", ""),
                          r.get("genre", "") or "Audius", cover,
                          f"https://api.audius.co/v1/tracks/{r.get('id')}/stream?app_name=AriaDesktop",
                          r.get("duration", 0), "", r.get("id")))
    return out


def search_openverse(site, query, limit, session, timeout=12):
    data = _get_json(session, site["search_url"], {"q": query, "page_size": limit}, timeout=timeout)
    out = []
    for r in data.get("results", []):
        dur = _duration_seconds(r.get("duration") or 0)
        out.append(_track(site, r.get("title", ""), r.get("creator", ""),
                          ", ".join((r.get("source") or "").split("-")).title(),
                          r.get("thumbnail", ""), r.get("url", ""), dur,
                          r.get("foreign_landing_url", ""), r.get("id")))
    return out


def search_archive(site, query, limit, session, timeout=12):
    params = [
        ("q", f'(title:("{query}" OR "{query}") OR creator:("{query}")) AND mediatype:(audio)'),
        ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "creator"),
        ("fl[]", "downloads"), ("rows", str(min(limit, 20))), ("page", "1"), ("output", "json"),
    ]
    data = _get_json(session, site["search_url"], params, timeout=timeout)
    docs = ((data.get("response") or {}).get("docs") or [])

    def enrich(doc):
        ident = doc.get("identifier", "")
        try:
            meta = _get_json(session, f"https://archive.org/metadata/{quote(ident)}", timeout=10)
        except Exception:
            return None
        files = meta.get("files") or []
        best = None
        for f in files:
            fmt = (f.get("format") or "").lower()
            name = f.get("name") or ""
            if fmt in ("vbr mp3", "mp3", "128kbps mp3", "64kbps mp3") or name.lower().endswith(".mp3"):
                best = f
                break
        if not best:
            for f in files:
                if (f.get("name") or "").lower().endswith((".ogg", ".m4a", ".flac")):
                    best = f
                    break
        if not best:
            return None
        m = meta.get("metadata") or {}
        creator = doc.get("creator") or m.get("creator") or ""
        if isinstance(creator, list):
            creator = creator[0]
        audio = f"https://archive.org/download/{quote(ident)}/{quote(best.get('name', ''))}"
        return _track(site, doc.get("title") or m.get("title") or ident, creator,
                      "Internet Archive", "", audio,
                      _duration_seconds(best.get("length") or 0),
                      f"https://archive.org/details/{ident}", ident)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(enrich, docs))
    return [r for r in results if r]


def search_radio(site, query, limit, session, timeout=10):
    params = {"name": query, "limit": limit, "hidebroken": "true",
              "order": "clickcount", "reverse": "true"}
    data = _get_json(session, site["search_url"], params, timeout=timeout)
    rows = data if isinstance(data, list) else data.get("data", [])
    out = []
    for r in rows[:limit]:
        name = r.get("name", "")
        out.append(_track(site, name, ", ".join(filter(None, [r.get("country"), (r.get("tags") or "").split(",")[0].strip()])) or "全球电台",
                          "电台直播", r.get("favicon", ""), r.get("url_resolved") or r.get("url", ""),
                          0, r.get("homepage", ""), r.get("stationuuid"), live=True))
    return out


def search_jamendo(site, query, limit, session, timeout=12):
    key = (site.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("Jamendo 需要在站点设置里填写 Client ID")
    params = {"client_id": key, "format": "json", "limit": limit, "search": query,
              "audioformat": "mp32", "include": "musicinfo"}
    data = _get_json(session, site["search_url"], params, timeout=timeout)
    out = []
    for r in data.get("results", []):
        out.append(_track(site, r.get("name", ""), r.get("artist_name", ""),
                          r.get("album_name", ""), r.get("image", ""), r.get("audio", ""),
                          _duration_seconds(r.get("duration")), r.get("shareurl", ""), r.get("id")))
    return out


# --------------------------------------------------------------------------- generic
def build_search_url(site: dict, query: str) -> str:
    template = (site.get("search_url") or "").strip()
    mapping = site.get("mapping") or {}
    key = (site.get("api_key") or "").strip()
    template = template.replace("{api_key}", quote(key))
    if "{query}" in template:
        return template.replace("{query}", quote(query))
    if str(mapping.get("method") or "GET").upper() == "POST":
        return template
    param = mapping.get("query_param") or "q"
    sep = "&" if "?" in template else "?"
    return f"{template}{sep}{urlencode({param: query})}"


def _fill_body(value, query: str):
    """Fill search-body placeholders without URL-encoding Chinese text."""
    if isinstance(value, dict):
        return {str(k): _fill_body(v, query) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_body(v, query) for v in value]
    if isinstance(value, str):
        return value.replace("{query}", query)
    return value


def _resolve_template(template: str, item: dict) -> str:
    """Fill {field} placeholders in a stream-fetch URL with values from the row."""

    def repl(match: re.Match) -> str:
        name = match.group(1)
        value = get_path(item, name)
        if value is None:
            value = item.get(name, "")
        return quote(str(value), safe="")

    return re.sub(r"\{([A-Za-z0-9_.]+)\}", repl, template or "")


def _fill_blank_query(url: str, query: str) -> str:
    """If the URL ends with ?keyword= / ?q=, fill that parameter instead of appending a new one."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
    except ValueError:
        return url
    changed = False
    for key in COMMON_QUERY_PARAMS:
        if key in params and (not params[key] or not params[key][0]):
            params[key] = [query]
            changed = True
    if not changed:
        return url
    encoded = urlencode(params, doseq=True).replace("%7Bquery%7D", "{query}")
    return urlunparse(parsed._replace(query=encoded))


def _looks_audio(url: str) -> bool:
    low = (url or "").split("#", 1)[0].lower()
    path = urlparse(low).path
    return path.endswith(AUDIO_SUFFIXES) or any(part in low for part in ("/audio", "/stream", "audio/", "stream/"))


def _abs_url(base: str, url: str) -> str:
    if not url:
        return ""
    if isinstance(url, dict):
        url = _as_url(url)
    url = html_lib.unescape(str(url)).replace("\\/", "/").strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith(("data:", "javascript:", "#")):
        return ""
    return urljoin(base or "", url)


def _first_row_audio(row: dict, mapping: dict) -> str:
    if mapping.get("audio"):
        raw = get_path(row, mapping["audio"])
        audio = _as_url(raw)
        if not audio and isinstance(raw, str) and raw.startswith(("/", "./", "../")):
            audio = raw
        return audio
    trusted = {"audio", "audio_url", "audioUrl", "play_url", "playUrl", "stream",
               "streamUrl", "stream_url", "music_url", "musicUrl", "mp3", "song_url",
               "songUrl", "listen_url", "contentUrl", "content_url"}
    fallback = ""
    for key in COMMON_AUDIO:
        raw = row.get(key)
        if raw in (None, "", [], {}):
            continue
        audio = _as_url(raw)
        if not audio and isinstance(raw, str) and raw.startswith(("/", "./", "../")):
            audio = raw
        if audio and (key in trusted or _looks_audio(audio)):
            return audio
        if audio and not fallback:
            fallback = audio
    return fallback


def _rows_to_tracks(site: dict, rows: list, limit: int, headers: dict, base_url: str) -> list[dict]:
    mapping = site.get("mapping") or {}
    base = base_url or site.get("base_url") or site.get("search_url") or ""
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = (get_path(row, mapping["title"]) if mapping.get("title")
                 else _first_value(row, COMMON_TITLE)[1])
        artist = (get_path(row, mapping["artist"]) if mapping.get("artist")
                  else _first_value(row, COMMON_ARTIST)[1])
        album = (get_path(row, mapping["album"]) if mapping.get("album")
                 else _first_value(row, COMMON_ALBUM)[1])
        cover = (get_path(row, mapping["cover"]) if mapping.get("cover")
                 else _first_value(row, COMMON_COVER)[1])
        audio = _first_row_audio(row, mapping)
        duration = (get_path(row, mapping["duration"]) if mapping.get("duration")
                    else _first_value(row, COMMON_DURATION)[1])
        page = (get_path(row, mapping["page"]) if mapping.get("page")
                else _first_value(row, COMMON_PAGE)[1])
        if not audio:
            continue
        audio = _abs_url(base, audio)
        if not audio or audio in seen:
            continue
        if not mapping.get("audio") and not _looks_audio(audio):
            continue
        cover = _abs_url(base, cover) if cover else ""
        page_url = _abs_url(base, page) if page else ""
        if isinstance(title, dict):
            title = title.get("rendered") or title.get("name") or title.get("title") or ""
        if isinstance(artist, dict):
            artist = artist.get("name") or artist.get("title") or ""
        if isinstance(artist, list):
            artist = ", ".join(str(a.get("name") if isinstance(a, dict) else a) for a in artist)
        seen.add(audio)
        track = _track(site, str(title or ""), str(artist or ""), str(album or ""),
                       cover, audio, _duration_seconds(duration), page_url,
                       row.get("id") or row.get("song_id") or row.get("track_id"))
        track["headers"] = {k: v for k, v in headers.items() if k.lower() in ("referer", "origin")}
        out.append(track)
        if len(out) >= max(limit, 1):
            break
    return out


_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_META_ATTR_RE = re.compile(r"([A-Za-z0-9:_-]+)\s*=\s*(\"[^\"]*\"|'[^']*')")
_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)
_SCRIPT_SRC_RE = re.compile(r"<script\b[^>]*\bsrc\s*=\s*(\"[^\"]*\"|'[^']*')[^>]*>", re.I)
_AUDIO_TAG_RE = re.compile(
    r"<(?:audio|source)\b[^>]*?(?:src|data-src|data-url)\s*=\s*(\"[^\"]*\"|'[^']*')[^>]*>", re.I)
_RAW_AUDIO_RE = re.compile(
    r"https?://[^\s\"'<>\\]+?\.(?:mp3|m4a|aac|ogg|flac|wav|opus)(?:\?[^\s\"'<>\\]*)?", re.I)
_ANCHOR_RE = re.compile(r"<a\b[^>]*\bhref\s*=\s*(\"[^\"]*\"|'[^']*')[^>]*>(.*?)</a>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _meta_value(html: str, names: tuple[str, ...]) -> str:
    wanted = {n.lower() for n in names}
    for tag in _META_TAG_RE.findall(html):
        attrs = {}
        for key, value in _META_ATTR_RE.findall(tag):
            attrs[key.lower()] = html_lib.unescape(value.strip("\"'"))
        key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        if key in wanted and attrs.get("content"):
            return attrs["content"]
    return ""


def _html_title(html: str) -> str:
    title = _meta_value(html, ("og:title", "twitter:title"))
    if title:
        return title
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return _strip_text(match.group(1)) if match else ""


def _strip_text(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", value or ""))).strip()


def _audio_filename_title(url: str) -> str:
    try:
        name = urlparse(url).path.rsplit("/", 1)[-1]
        name = html_lib.unescape(name)
        name = re.sub(r"\.(?:mp3|m4a|aac|ogg|flac|wav|opus)$", "", name, flags=re.I)
        return name.replace("_", " ").strip()[:60]
    except Exception:  # noqa: BLE001
        return ""


def _jsonld_rows(node) -> list[dict]:
    found: list[dict] = []

    def walk(obj):
        if isinstance(obj, dict):
            types = obj.get("@type") or ""
            if isinstance(types, str):
                types = [types]
            if any("MusicRecording" in str(t) or "AudioObject" in str(t) for t in types):
                audio = obj.get("contentUrl") or obj.get("audio")
                if audio:
                    artist = obj.get("byArtist") or obj.get("artist") or ""
                    if isinstance(artist, dict):
                        artist = artist.get("name") or artist.get("title") or ""
                    if isinstance(artist, list):
                        artist = ", ".join(str(a.get("name") if isinstance(a, dict) else a) for a in artist)
                    found.append({"title": obj.get("name") or obj.get("title") or "",
                                  "artist": artist,
                                  "audio": audio,
                                  "duration": obj.get("duration") or "",
                                  "cover": obj.get("image") or obj.get("thumbnailUrl") or "",
                                  "page": obj.get("url") or ""})
            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(node)
    return found


def _dedupe_tracks(tracks: list[dict]) -> list[dict]:
    out, seen = [], set()
    for track in tracks:
        audio = track.get("audio") or ""
        if not audio or audio in seen:
            continue
        seen.add(audio)
        out.append(track)
    return out


def _html_direct_tracks(site, html, page_url, limit, headers) -> list[dict]:
    title = _html_title(html)
    cover = _abs_url(page_url, _meta_value(html, ("og:image", "twitter:image")))
    rows = []
    for match in _AUDIO_TAG_RE.finditer(html):
        audio = _abs_url(page_url, match.group(1).strip("\"'"))
        if audio and _looks_audio(audio):
            rows.append({"title": title or _audio_filename_title(audio), "audio": audio, "cover": cover})
    for audio in _RAW_AUDIO_RE.findall(html):
        audio = _abs_url(page_url, html_lib.unescape(audio))
        if audio:
            rows.append({"title": title or _audio_filename_title(audio), "audio": audio, "cover": cover})
    if not rows:
        return []
    return _rows_to_tracks(site, rows, limit, headers, page_url)


def _html_script_tracks(site, html, page_url, limit, headers) -> list[dict]:
    tracks = []
    for body in _SCRIPT_RE.findall(html):
        body = body.strip()
        if not body or not body.startswith(("{", "[")) or len(body) > 3_000_000:
            continue
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            continue
        jsonld = _jsonld_rows(data)
        if jsonld:
            tracks.extend(_rows_to_tracks(site, jsonld, limit, headers, page_url))
            if len(tracks) >= limit:
                break
        _, rows = find_dict_list(data)
        if rows:
            found = _rows_to_tracks(site, rows, limit - len(tracks), headers, page_url)
            tracks.extend(found)
            if len(tracks) >= limit:
                break
    return _dedupe_tracks(tracks)


def _detail_links(html: str, page_url: str) -> list[str]:
    host = urlparse(page_url).netloc
    links, seen = [], set()
    for match in _ANCHOR_RE.finditer(html):
        href = _abs_url(page_url, match.group(1).strip("\"'"))
        text = _strip_text(match.group(2))
        if not href or urlparse(href).netloc != host or href in seen:
            continue
        low = href.lower()
        if not any(part in low for part in ("song", "track", "music", "play", "detail", "audio", "/s/")):
            continue
        if not text or len(text) > 90:
            continue
        seen.add(href)
        links.append(href)
        if len(links) >= 5:
            break
    return links


def _html_tracks(site, html, page_url, limit, session, headers, timeout, depth=0) -> list[dict]:
    tracks = _html_script_tracks(site, html, page_url, limit, headers)
    if len(tracks) < limit:
        tracks.extend(_html_direct_tracks(site, html, page_url, limit - len(tracks), headers))
    tracks = _dedupe_tracks(tracks)[:limit]
    if tracks or depth >= 1:
        return tracks
    for link in _detail_links(html, page_url):
        try:
            resp = session.get(link, headers=headers, timeout=timeout)
            if resp.status_code >= 400:
                continue
            detail = _html_tracks(site, resp.text, resp.url or link, limit, session,
                                  headers, timeout, depth=1)
        except Exception:  # noqa: BLE001
            continue
        if detail:
            tracks.extend(detail)
            tracks = _dedupe_tracks(tracks)
            if len(tracks) >= limit:
                break
    return tracks[:limit]


def _web_search_urls(site: dict, query: str) -> list[str]:
    template = (site.get("search_url") or site.get("base_url") or "").strip()
    if not template:
        return []
    if "{query}" in template:
        return [template.replace("{query}", quote(query))]
    candidates = []
    filled = _fill_blank_query(template, query)
    if filled != template:
        candidates.append(filled)
    mapping = site.get("mapping") or {}
    param = mapping.get("query_param") or "q"
    if not parse_qs(urlparse(template).query, keep_blank_values=True):
        sep = "&" if "?" in template else "?"
        candidates.append(f"{template}{sep}{urlencode({param: query})}")
        for alt in ("keyword", "search", "wd"):
            if alt != param:
                candidates.append(f"{template}{sep}{urlencode({alt: query})}")
    parsed = urlparse(template)
    if parsed.path in ("", "/"):
        base = f"{parsed.scheme}://{parsed.netloc}"
        candidates.append(f"{base}/search?q={quote(query)}")
        candidates.append(f"{base}/search?keyword={quote(query)}")
    out, seen = [], set()
    for item in candidates:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out[:4]


def search_web(site, query, limit, session, timeout=12):
    """Best-effort HTML/SPA source: discover its API, then parse audio from the page itself."""
    headers = dict(WEB_HEADERS)
    headers.update({str(k): str(v) for k, v in (site.get("headers") or {}).items()})
    base = site.get("search_url") or site.get("base_url") or ""
    host_key = urlparse(base).netloc.lower()
    proxy = ""
    try:
        proxy = session.proxies.get("https") or session.proxies.get("http") or ""
    except Exception:  # noqa: BLE001
        proxy = ""

    cached = _WEB_API_CACHE.get(host_key)
    if cached:
        try:
            temp = site_from_sniff(cached["url"], cached, site.get("name", ""))
            temp["id"] = site["id"]
            return search_site(temp, query, limit, proxy, timeout)
        except Exception:  # noqa: BLE001
            _WEB_API_CACHE.pop(host_key, None)

    if "{query}" not in base:
        discovered = sniff_any(base, query, proxy, timeout=min(10, timeout))
        if discovered.get("ok"):
            _WEB_API_CACHE[host_key] = discovered
            temp = site_from_sniff(discovered["url"], discovered, site.get("name", ""))
            temp["id"] = site["id"]
            return search_site(temp, query, limit, proxy, timeout)

    last_error = "网页源没有找到可播放音频"
    for url in _web_search_urls(site, query):
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code}"
                continue
            ctype = resp.headers.get("Content-Type", "").lower()
            if "json" in ctype or resp.text.lstrip().startswith(("{", "[")):
                try:
                    data = resp.json()
                except ValueError:
                    data = None
                if data is not None:
                    _, rows = find_dict_list(data)
                    tracks = _rows_to_tracks(site, rows, limit, headers, resp.url or url)
                    if tracks:
                        return tracks
            tracks = _html_tracks(site, resp.text, resp.url or url, limit, session,
                                  headers, timeout)
            if tracks:
                return tracks
            last_error = "网页里没有找到可播放的音频地址（可能需要登录或接口加密）"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)[:120]
    raise RuntimeError(last_error)


def search_html_rule(site, query, limit, session, timeout=12):
    """Rule-configured HTML source: search list -> optional detail page -> audio."""
    rows = html_rule_engine.search_html_rule(site, query, limit, session, timeout=timeout)
    out = []
    for row in rows:
        track = _track(site, row.get("title", ""), row.get("artist", ""),
                       row.get("album", ""), row.get("cover", ""),
                       row.get("audio", ""), 0, row.get("page", ""),
                       row.get("id") or None)
        out.append(track)
    return out


def search_subsonic(site, query, limit, session, timeout=12):
    """Subsonic / Navidrome search; stream URLs are built from the child id."""
    mapping = site.get("mapping") or {}
    url = build_search_url(site, query)
    headers = dict(site.get("headers") or {})
    data = _get_json(session, url, None, headers, timeout=timeout)
    rows = get_path(data, mapping.get("results_path") or "subsonic-response.searchResult3.match")
    if not isinstance(rows, list):
        rows = []
    template = str(mapping.get("audio_from") or "")
    out = []
    for row in rows[:max(1, limit)]:
        if not isinstance(row, dict):
            continue
        track = _track(site, str(row.get("title") or ""), str(row.get("artist") or ""),
                       str(row.get("album") or ""),
                       urljoin(site.get("base_url") or "", str(row.get("coverArt") or "")),
                       _resolve_template(template, row) if template else "",
                       _duration_seconds(row.get("duration")), "", row.get("id"))
        track["base_url"] = str(site.get("base_url") or "")
        out.append(track)
    return out


def search_generic(site, query, limit, session, timeout=12):
    mapping = site.get("mapping") or {}
    url = build_search_url(site, query)
    headers = dict(site.get("headers") or {})
    if site.get("api_key") and mapping.get("key_header"):
        headers[mapping["key_header"]] = site["api_key"]
    method = str(mapping.get("method") or "GET").upper()
    if method == "POST":
        body = _fill_body(mapping.get("body") or {"query": query}, query)
        try:
            resp = session.post(url, json=body, headers=headers, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"请求失败：{exc}") from exc
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError("接口返回的不是 JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("接口返回格式不正确")
    else:
        data = _get_json(session, url, None, headers, timeout=timeout)
    path = mapping.get("results_path", "")
    rows = get_path(data, path) if path else None
    if not isinstance(rows, list):
        _, rows = find_dict_list(data)
    out = []
    for r in rows[: max(limit, 1)]:
        if not isinstance(r, dict):
            continue
        title = get_path(r, mapping.get("title", "title")) if mapping.get("title") else _first_value(r, COMMON_TITLE)[1]
        artist = get_path(r, mapping.get("artist", "artist")) if mapping.get("artist") else _first_value(r, COMMON_ARTIST)[1]
        album = get_path(r, mapping.get("album", "")) if mapping.get("album") else _first_value(r, COMMON_ALBUM)[1]
        cover = get_path(r, mapping.get("cover", "")) if mapping.get("cover") else _first_value(r, COMMON_COVER)[1]
        audio = get_path(r, mapping.get("audio", "")) if mapping.get("audio") else _first_value(r, COMMON_AUDIO)[1]
        duration = get_path(r, mapping.get("duration", "")) if mapping.get("duration") else _first_value(r, COMMON_DURATION)[1]
        page = get_path(r, mapping.get("page", "")) if mapping.get("page") else _first_value(r, COMMON_PAGE)[1]
        audio = _as_url(audio)
        audio_from = ""
        if not audio and mapping.get("audio_from"):
            audio_from = _resolve_template(mapping["audio_from"], r)
        if not audio and not audio_from:
            continue
        if audio.startswith("/"):
            audio = urljoin(site.get("base_url") or site.get("search_url"), audio)
        cover = _as_url(cover)
        if cover.startswith("/"):
            cover = urljoin(site.get("base_url") or site.get("search_url"), cover)
        if isinstance(title, dict):
            title = title.get("rendered") or title.get("name") or ""
        if isinstance(artist, dict):
            artist = artist.get("name") or artist.get("title") or ""
        if isinstance(artist, list):
            artist = ", ".join(str((a.get("name") if isinstance(a, dict) else a)) for a in artist)
        track = _track(site, str(title or ""), str(artist or ""), str(album or ""),
                       str(cover or ""), audio, _duration_seconds(duration), str(page or ""),
                       r.get("id") or r.get("song_id") or r.get("track_id"))
        track["base_url"] = str(site.get("base_url") or "")
        track["headers"] = {k: v for k, v in headers.items() if k.lower() in ("referer", "origin")}
        lyric_field = mapping.get("lyrics", "")
        if lyric_field:
            text = get_path(r, lyric_field)
            if isinstance(text, str) and text.strip():
                track["lyrics_text"] = text
        # Buguyy's search payload has an `about` field, but it is not a
        # reliable per-track lyric (it can belong to another same-title song).
        # Online lyric matching is preferred; do not attach it here.
        if audio_from:
            track["audio_from"] = audio_from
            track["audio_path"] = mapping.get("audio_path", "url")
            track["audio_headers"] = dict(mapping.get("audio_from_headers") or {})
        out.append(track)
    return out


def fetch_stream(track: dict, proxy: str = "", timeout: int = 12) -> dict:
    """Resolve a playable URL (and any lyrics returned with it) for a two-step source."""
    template = (track or {}).get("audio_from") or ""
    if template == "listen1":
        from listen1 import SIDECAR
        track_id = (track or {}).get("listen1_id") or (track or {}).get("id") or ""
        url = SIDECAR.stream(track_id)
        if not url:
            raise RuntimeError("内置音源没有返回播放地址")
        return {"audio": url}
    if not template:
        return {"audio": (track or {}).get("audio", "")}
    session = make_session(proxy)
    try:
        data = _get_json(session, template, None, track.get("audio_headers") or {}, timeout=timeout)
        path = track.get("audio_path") or ""
        raw_url = get_path(data, path) if path else None
        url = _as_url(raw_url)
        if not url and isinstance(raw_url, str) and raw_url.startswith("/"):
            base = str((track or {}).get("base_url") or template or "")
            url = urljoin(base, raw_url)
        if not url:
            url = _as_url(_first_value(data, COMMON_AUDIO)[1])
        if not url and isinstance(data, dict):
            for value in data.values():
                found = _as_url(value)
                if found:
                    url = found
                    break
                if isinstance(value, str) and value.startswith("/"):
                    base = str((track or {}).get("base_url") or template or "")
                    url = urljoin(base, value)
                    break
        if not url:
            message = ""
            if isinstance(data, dict):
                message = str(data.get("message") or data.get("error") or "")
            raise RuntimeError("取流接口没有返回音频地址" + (f"（{message}）" if message else ""))
        info = {"audio": url}
        if isinstance(data, dict):
            for key in ("lrc", "lyric", "lyrics"):
                value = data.get(key)
                if isinstance(value, str) and value.strip() and "失败" not in value[:12]:
                    info["lrc"] = value
                    break
        return info
    finally:
        session.close()


def resolve_audio(track: dict, proxy: str = "", timeout: int = 12) -> str:
    """Fetch a fresh stream URL for a track whose source separates search from stream."""
    return fetch_stream(track, proxy=proxy, timeout=timeout).get("audio", "")



# --------------------------------------------------------------------------- dispatch
ADAPTERS = {
    "itunes": search_itunes,
    "deezer": search_deezer,
    "audius": search_audius,
    "openverse": search_openverse,
    "archive": search_archive,
    "radio": search_radio,
    "jamendo": search_jamendo,
    "generic": search_generic,
    "subsonic": search_subsonic,
    "web": search_web,
    "html_rule": search_html_rule,
    "buguyy": search_generic,
}


def search_site(site: dict, query: str, limit: int = 25, proxy: str = "", timeout: int = 12) -> list[dict]:
    if not (site.get("search_url") or "").strip():
        raise RuntimeError("该站点没有配置搜索接口")
    session = make_session(proxy)
    fn = ADAPTERS.get(site.get("kind", "generic"), search_generic)
    try:
        return fn(site, query, limit, session, timeout=timeout)
    finally:
        session.close()


def health_check(site: dict, proxy: str = "", timeout: int = 8) -> dict:
    started = time.perf_counter()
    try:
        tracks = search_site(site, "love", limit=3, proxy=proxy, timeout=timeout)
        latency = int((time.perf_counter() - started) * 1000)
        if not tracks:
            return {"ok": False, "latency": latency, "message": "接口可访问，但没有解析到可播放结果",
                    "samples": []}
        playable = [t for t in tracks if t.get("audio") or t.get("audio_from")]
        if not playable:
            return {"ok": False, "latency": latency, "message": "结果里没有音频地址", "samples": []}
        needs_fetch = next((t for t in playable if not t.get("audio") and t.get("audio_from")), None)
        if needs_fetch:
            resolve_audio(needs_fetch, proxy=proxy, timeout=timeout)
            latency = int((time.perf_counter() - started) * 1000)
        return {"ok": True, "latency": latency, "message": "可用",
                "samples": [f"{t['title']} · {t['artist']}" for t in tracks[:3]]}
    except Exception as exc:  # noqa: BLE001
        latency = int((time.perf_counter() - started) * 1000)
        return {"ok": False, "latency": latency, "message": str(exc)[:120], "samples": []}


# --------------------------------------------------------------------------- sniffing
def sniff_endpoint(url: str, query: str = "love", proxy: str = "", timeout: int = 10) -> dict:
    """Try to turn an arbitrary JSON search URL into a working adapter mapping."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "message": "地址需要以 http(s):// 开头"}
    raw_url = url
    url = _fill_blank_query(url, query)
    session = make_session(proxy)
    attempts = []
    if "{query}" in url:
        attempts.append(url.replace("{query}", quote(query)))
    else:
        attempts.append(url)
        for param in ("q", "keyword", "keywords", "search", "query", "name", "wd"):
            base, sep = (url, "&") if "?" in url else (url, "?")
            attempts.append(f"{base}{sep}{urlencode({param: query})}")
    last_error = "没有解析到列表数据"
    for attempt in attempts[:4]:
        try:
            resp = session.get(attempt, timeout=timeout)
            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code}"
                continue
            data = resp.json()
        except ValueError:
            last_error = "返回内容不是 JSON，无法自动嗅探"
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)[:100]
            continue
        path, rows = find_dict_list(data)
        if not rows:
            last_error = "JSON 里没有找到歌曲列表"
            continue
        dicts = [r for r in rows[:8] if isinstance(r, dict)]
        if not dicts:
            continue
        mapping = {"results_path": path}
        scores = {}
        for field, candidates in (("title", COMMON_TITLE), ("artist", COMMON_ARTIST),
                                  ("album", COMMON_ALBUM), ("cover", COMMON_COVER),
                                  ("audio", COMMON_AUDIO), ("duration", COMMON_DURATION),
                                  ("page", COMMON_PAGE)):
            best_key, hits = "", 0
            for key in candidates:
                found = sum(1 for d in dicts if key in d and d.get(key) not in (None, "", [], {}))
                if found > hits:
                    best_key, hits = key, found
            if best_key and hits >= max(1, len(dicts) // 2):
                scores[field] = hits
                mapping[field] = best_key
        sample_tracks = []
        query_param = next((k for k in COMMON_QUERY_PARAMS
                            if k in parse_qs(urlparse(raw_url).query, keep_blank_values=True)), "q")
        mapping.setdefault("query_param", query_param)
        audio_key = mapping.get("audio")
        if not audio_key:
            return {"ok": False, "message": "没有识别到音频字段，请在批量导入后手动编辑映射",
                    "mapping": mapping, "url": attempt, "sample_rows": dicts[:3],
                    "used_url": attempt}
        for d in dicts:
            audio = _as_url(d.get(audio_key))
            if not audio:
                continue
            title_val = d.get(mapping.get("title", "")) if mapping.get("title") else ""
            if isinstance(title_val, dict):
                title_val = title_val.get("name") or title_val.get("title") or ""
            sample_tracks.append({
                "title": str(title_val or "未命名")[:60],
                "audio": audio,
                "artist": str(d.get(mapping.get("artist", ""), "") or "")[:40],
                "cover": _as_url(d.get(mapping.get("cover", ""))) if mapping.get("cover") else "",
            })
        if not sample_tracks:
            last_error = "音频字段存在，但值不是可用的 http 地址"
            continue
        mapping.setdefault("query_param", "q")
        base = "{0.scheme}://{0.netloc}".format(urlparse(attempt))
        # rebuild a template from the address the user supplied, not from the sniffed sample query
        if "{query}" not in raw_url:
            url_template = _template_from_url(raw_url)
        else:
            url_template = raw_url
        # strip the sniffed query value but keep a {query} placeholder, preserving other params
        return {
            "ok": True,
            "url": url_template,
            "base_url": base,
            "mapping": mapping,
            "samples": sample_tracks[:3],
            "score": {k: v for k, v in scores.items()},
            "used_url": attempt,
            "message": f"识别到 {len(sample_tracks)} 条可播放结果",
        }
    return {"ok": False, "message": last_error, "url": url, "sample_rows": []}


_QUOTED_URL_RE = re.compile(r"[\"'`]((?:https?://|/)[^\"'`<>\\\s]{2,220})[\"'`]")
_API_WORDS = ("api", "search", "query", "song", "music", "track", "list")
_STATIC_SUFFIXES = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
                    ".woff", ".woff2", ".ttf", ".map")


def _discover_api_urls(text: str, base_url: str) -> list[str]:
    text = html_lib.unescape((text or "").replace("\\/", "/"))
    text = text.replace("\\u002F", "/").replace("\\u002f", "/")
    found, seen = [], set()
    for match in _QUOTED_URL_RE.finditer(text):
        value = match.group(1).strip()
        low = value.lower().split("?", 1)[0]
        if low.endswith(_STATIC_SUFFIXES) or "javascript:" in low:
            continue
        if not any(word in low for word in _API_WORDS):
            continue
        absolute = _abs_url(base_url, value)
        if not absolute or absolute == base_url or absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
        if len(found) >= 16:
            break
    return found


def _script_api_urls(session, html: str, base_url: str, headers: dict, timeout: int) -> list[str]:
    out = []
    sources = []
    for raw in _SCRIPT_SRC_RE.findall(html):
        src = _abs_url(base_url, raw.strip("\"'"))
        if src and src.endswith(".js"):
            sources.append(src)
    for src in sources[:5]:
        try:
            resp = session.get(src, headers=headers, timeout=min(timeout, 8))
            if resp.status_code < 400 and len(resp.text) < 4_000_000:
                out.extend(_discover_api_urls(resp.text, base_url))
        except Exception:  # noqa: BLE001
            continue
        if len(out) >= 10:
            break
    return out


_STREAM_WORDS = ("geturl", "get_url", "playurl", "play_url", "stream", "audio", "play", "url")
_ID_KEYS = ("id", "song_id", "songid", "songId", "track_id", "trackId", "mid", "musicid",
            "musicId", "rid", "nid", "aid")
_TRUSTED_AUDIO_KEYS = {"audio", "audio_url", "audioUrl", "play_url", "playUrl", "stream",
                       "streamUrl", "stream_url", "music_url", "musicUrl", "mp3", "song_url",
                       "songUrl", "listen_url", "contentUrl", "content_url", "url", "src"}


def _stream_candidates(text: str, base_url: str) -> list[str]:
    text = html_lib.unescape((text or "").replace("\\/", "/"))
    out, seen = [], set()
    for match in _QUOTED_URL_RE.finditer(text):
        value = match.group(1).strip()
        low = value.lower()
        if not any(word in low for word in _STREAM_WORDS):
            continue
        if low.endswith(_STATIC_SUFFIXES) or low.endswith((".mp4", ".webm")):
            continue
        absolute = _abs_url(base_url, value)
        if absolute and absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out[:16]


def _stream_template(candidate: str, id_key: str = "id") -> str:
    parsed = urlparse(candidate)
    params = parse_qs(parsed.query, keep_blank_values=True)
    for key in _ID_KEYS:
        if key in params:
            params[key] = [f"{{{id_key}}}"]
            encoded = urlencode(params, doseq=True)
            encoded = encoded.replace("%7B" + id_key + "%7D", "{" + id_key + "}")
            return urlunparse(parsed._replace(query=encoded))
    sep = "&" if parsed.query else "?"
    return f"{candidate}{sep}{id_key}={{{id_key}}}"


def _find_json_audio(data) -> tuple[str, str]:
    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else key
                if isinstance(value, str):
                    url = _as_url(value)
                    if url and (str(key) in _TRUSTED_AUDIO_KEYS or _looks_audio(url)):
                        return child, url
                elif isinstance(value, (dict, list)):
                    found = walk(value, child)
                    if found:
                        return found
        elif isinstance(node, list):
            for index, value in enumerate(node[:4]):
                child = f"{path}.{index}" if path else str(index)
                found = walk(value, child)
                if found:
                    return found
        return "", ""

    return walk(data)


def _template_from_url(url: str, preferred: str = "") -> str:
    parsed = urlparse(url or "")
    params = parse_qs(parsed.query, keep_blank_values=True)
    if preferred and preferred in params:
        for key in COMMON_QUERY_PARAMS:
            if key != preferred:
                params.pop(key, None)
        params[preferred] = ["{query}"]
        encoded = urlencode(params, doseq=True).replace("%7Bquery%7D", "{query}")
        return urlunparse(parsed._replace(query=encoded))
    touched = False
    for key in COMMON_QUERY_PARAMS:
        if key in params:
            if touched:
                params.pop(key, None)
            else:
                params[key] = ["{query}"]
                touched = True
    if not touched:
        params["q"] = ["{query}"]
    encoded = urlencode(params, doseq=True).replace("%7Bquery%7D", "{query}")
    return urlunparse(parsed._replace(query=encoded))


def _try_two_step(result: dict, page_text: str, page_url: str, session, headers,
                  proxy: str, timeout: int) -> dict:
    rows = [r for r in (result.get("sample_rows") or []) if isinstance(r, dict)]
    if not rows:
        return {}
    id_key = next((key for key in _ID_KEYS if rows[0].get(key) not in (None, "", 0)), "")
    if not id_key:
        return {}
    mapping = dict(result.get("mapping") or {})
    base = result.get("base_url") or ""
    if not base:
        parsed = urlparse(page_url)
        base = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else page_url
    search_url = result.get("url") or page_url
    search_template = _template_from_url(search_url, mapping.get("query_param") or "")
    candidates = _stream_candidates(page_text, page_url)
    scripts = []
    for raw in _SCRIPT_SRC_RE.findall(page_text):
        src = _abs_url(page_url, raw.strip("\"'"))
        if src and src.endswith(".js"):
            scripts.append(src)
    for src in scripts[:4]:
        try:
            resp = session.get(src, headers=headers, timeout=min(timeout, 8))
            if resp.status_code < 400 and len(resp.text) < 4_000_000:
                candidates.extend(_stream_candidates(resp.text, page_url))
        except Exception:  # noqa: BLE001
            continue
    root = "{0.scheme}://{0.netloc}".format(urlparse(page_url))
    candidates.extend([
        f"{root}/api/geturl", f"{root}/api/play", f"{root}/api/url",
        f"{root}/api/song/url", f"{root}/api/music/url", f"{root}/api/stream",
        f"{root}/api/audio",
    ])
    for candidate in list(dict.fromkeys(candidates))[:8]:
        template = _stream_template(candidate, id_key)
        probe = _resolve_template(template, rows[0])
        try:
            resp = session.get(probe, headers=headers, timeout=min(timeout, 8))
            if resp.status_code >= 400:
                continue
            ctype = resp.headers.get("Content-Type", "").lower()
            if any(part in ctype for part in ("audio/", "video/", "octet-stream")):
                continue
            data = resp.json()
        except Exception:  # noqa: BLE001
            continue
        path, _ = _find_json_audio(data)
        if not path:
            continue
        mapping["audio_from"] = template
        mapping["audio_path"] = path
        samples = []
        for row in rows[:3]:
            title = get_path(row, mapping.get("title", "title")) if mapping.get("title") else row.get("title", "")
            artist = get_path(row, mapping.get("artist", "artist")) if mapping.get("artist") else row.get("artist", "")
            samples.append({"title": str(title or row.get("title") or "未命名"),
                            "artist": str(artist or row.get("artist") or ""),
                            "audio": ""})
        return {"ok": True, "url": search_template, "base_url": base, "mapping": mapping,
                "samples": samples, "used_url": result.get("used_url"),
                "message": f"识别到 {len(samples)} 条可播放结果（搜索 + 取流两步）"}
    return {}


def sniff_any(url: str, query: str = "love", proxy: str = "", timeout: int = 10) -> dict:
    """Sniff a normal website too: find the JSON search endpoint used by its front end."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "message": "地址需要以 http(s):// 开头"}
    session = make_session(proxy)
    headers = dict(WEB_HEADERS)
    try:
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code >= 400:
            return {"ok": False, "url": url, "message": f"HTTP {resp.status_code}"}
        ctype = resp.headers.get("Content-Type", "").lower()
        if "json" in ctype or resp.text.lstrip().startswith(("{", "[")):
            result = sniff_endpoint(url, query, proxy, timeout)
            result.setdefault("site_url", url)
            if not result.get("ok") and result.get("mapping") and result.get("sample_rows"):
                two = _try_two_step(result, "", url, session, headers, proxy, timeout)
                if two:
                    two["site_url"] = url
                    two["discovered"] = url
                    return two
            return result
        html = resp.text
        page_url = resp.url or url
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "message": str(exc)[:120]}

    candidates = _discover_api_urls(html, page_url)
    if len(candidates) < 6:
        candidates.extend(_script_api_urls(session, html, page_url, headers, timeout))
    parsed = urlparse(page_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates.extend([
        f"{root}/api/search", f"{root}/api/v1/search", f"{root}/api/music/search",
        f"{root}/api/song/search", f"{root}/search",
    ])
    candidates = [c for c in dict.fromkeys(candidates) if c and c != url][:14]
    last_error = "没有找到 JSON 搜索接口"
    two_step_tries = 0
    for candidate in candidates:
        probe = _fill_blank_query(candidate, "{query}")
        if "{query}" not in probe:
            sep = "&" if "?" in probe else "?"
            probe = f"{probe}{sep}q={{{query}}}"
        result = sniff_endpoint(probe, query, proxy, timeout=min(timeout, 8))
        if result.get("ok"):
            result["site_url"] = url
            result["discovered"] = candidate
            return result
        if result.get("mapping") and result.get("sample_rows") and two_step_tries < 2:
            two = _try_two_step(result, html, page_url, session, headers, proxy, timeout)
            if two:
                two["site_url"] = url
                two["discovered"] = candidate
                return two
            two_step_tries += 1
        last_error = result.get("message") or last_error
    return {"ok": False, "url": url, "message": last_error,
            "hint": "可直接点击「添加为网页源」入库"}


def site_from_sniff(url_template: str, sniffed: dict, name: str = "") -> dict:
    host = urlparse(sniffed.get("base_url") or url_template).netloc or "自定义源"
    display = name or host.replace("www.", "").split(".")[0].title()
    return {
        "id": "",
        "name": display,
        "kind": "generic",
        "tag": "自定义接口",
        "desc": f"自动嗅探 · {host}",
        "accent": "#6E8BFF",
        "base_url": sniffed.get("base_url", ""),
        "search_url": url_template,
        "mapping": sniffed.get("mapping", {}),
        "headers": {},
        "api_key": "",
        "enabled": 1,
        "is_builtin": 0,
    }


def site_from_web(url: str, name: str = "") -> dict:
    host = urlparse(url).netloc or "网页源"
    display = name or host.replace("www.", "").split(".")[0].title()
    return {
        "id": "",
        "name": display,
        "kind": "web",
        "tag": "网页源",
        "desc": f"网页源 · {host}",
        "accent": "#7C8BFF",
        "base_url": f"{urlparse(url).scheme}://{urlparse(url).netloc}",
        "search_url": url,
        "mapping": {"mode": "html", "query_param": "q"},
        "headers": {},
        "api_key": "",
        "enabled": 1,
        "is_builtin": 0,
    }
