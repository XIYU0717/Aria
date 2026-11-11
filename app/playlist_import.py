"""Import a playlist from another music app's share link.

Supported share links (public pages only, no login):
  * 网易云音乐  music.163.com/playlist?id=... / y.music.163.com/m/playlist?id=...
  * QQ音乐      y.qq.com/n/ryqq/playlist/<id> / i.y.qq.com/...taoge.html?id=<id>
  * 酷狗音乐    kugou.com/yy/special/single/<id>.html / m.kugou.com/plist/list/<id>

Only metadata (name / cover / songs) is imported; audio is resolved later from
the enabled Aria sources when the user presses play.
"""

import html
import re

import requests

import playlist_share

from adapters import UA

TIMEOUT = 12
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
}
MOBILE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"),
}


class PlaylistImportError(RuntimeError):
    pass


def _get(session: requests.Session, url: str, headers=None, params=None) -> requests.Response:
    return session.get(url, headers=headers or HEADERS, params=params, timeout=TIMEOUT)


def _json(resp: requests.Response):
    try:
        return resp.json()
    except ValueError as exc:
        raise PlaylistImportError("分享页返回的不是可解析的数据，可能已失效或需要登录") from exc


def _first(pattern: str, text: str) -> str:
    match = re.search(pattern, text or "", re.I)
    if not match:
        return ""
    return next((g for g in match.groups() if g), "")


def detect_platform(url: str) -> str:
    low = (url or "").lower()
    if "163.com" in low or "y.music.163.com" in low:
        return "netease"
    if "y.qq.com" in low or "qq.com" in low:
        return "qq"
    if "kugou.com" in low:
        return "kugou"
    if "kuwo.cn" in low:
        return "kuwo"
    return ""


def _track(title: str, artist: str, album: str = "", cover: str = "",
           duration: int = 0, platform: str = "", platform_id: str = "",
           platform_key: str = "") -> dict:
    return {
        "id": f"import:{platform}:{abs(hash((title, artist, album))) & 0xffffff}",
        "title": (title or "未命名").strip(),
        "artist": (artist or "未知艺术家").strip(),
        "album": (album or "").strip(),
        "cover": cover or "",
        "audio": "",
        "duration": int(duration or 0),
        "page": "",
        "source_id": "imported",
        "source_name": platform or "导入歌单",
        "source_accent": "#8B7BFF",
        "is_live": False,
        "is_preview": False,
        "headers": {},
        "kind": "imported",
        # Keep the original platform id so the built-in Listen1 sidecar can
        # stream this exact song without a fuzzy re-search.
        "audio_from": "listen1" if platform_id else "",
        "listen1_id": platform_id,
        "listen1_source": platform_key,
    }


# --------------------------------------------------------------------------- NetEase
def _netease(session, url: str, playlist_id: str) -> dict:
    if not playlist_id:
        playlist_id = _first(r"playlist[/?].*?id=(\d+)", url) or _first(r"playlist/(\d+)", url)
    if not playlist_id:
        resp = _get(session, url)
        playlist_id = (_first(r"id=(\d+)", resp.url) or _first(r"playlist\?id=(\d+)", resp.text))
    if not playlist_id:
        raise PlaylistImportError("没能从链接里识别出网易云歌单 ID")
    detail = _json(_get(session, "https://music.163.com/api/v6/playlist/detail",
                        params={"id": playlist_id, "n": 1000, "s": 0}))
    playlist = (detail or {}).get("playlist") or {}
    if not playlist:
        raise PlaylistImportError("网易云没有返回歌单数据（歌单可能已删除或需要登录）")
    tracks = list(playlist.get("tracks") or [])
    ids = [int(x.get("id")) for x in (playlist.get("trackIds") or []) if x.get("id")]
    if len(ids) > len(tracks):
        known = {int(t.get("id")) for t in tracks}
        missing = [i for i in ids if i not in known]
        for start in range(0, len(missing), 200):
            chunk = missing[start:start + 200]
            data = _json(_get(session, "https://music.163.com/api/song/detail",
                              params={"ids": "[" + ",".join(str(i) for i in chunk) + "]"}))
            tracks.extend((data or {}).get("songs") or [])

    out = []
    for item in tracks:
        artists = item.get("ar") or item.get("artists") or []
        artist = "、".join(a.get("name", "") for a in artists if a.get("name"))
        album = item.get("al") or item.get("album") or {}
        cover = album.get("picUrl") or ""
        duration = int((item.get("dt") or item.get("duration") or 0) / 1000)
        track_id = item.get("id")
        if item.get("name"):
            out.append(_track(item["name"], artist, album.get("name", ""), cover, duration,
                              "网易云音乐", f"netrack_{track_id}" if track_id else "", "netease"))
    return {
        "name": playlist.get("name") or "网易云歌单",
        "cover": playlist.get("coverImgUrl") or "",
        "platform": "网易云音乐",
        "tracks": out,
    }


# --------------------------------------------------------------------------- QQ
def _qq(session, url: str, playlist_id: str) -> dict:
    low = url or ""
    if not playlist_id:
        playlist_id = (_first(r"/playlist/(\d+)", low)
                       or _first(r"taoge\.html.*?id=(\d+)", low)
                       or _first(r"disstid=(\d+)", low))
    if not playlist_id:
        resp = _get(session, url)
        playlist_id = (_first(r"/playlist/(\d+)", resp.url)
                       or _first(r"disstid=(\d+)", resp.text))
    if not playlist_id:
        raise PlaylistImportError("没能从链接里识别出 QQ 音乐歌单 ID")
    api = "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
    data = _json(_get(session, api, params={
        "type": 1, "json": 1, "utf8": 1, "onlysong": 0,
        "disstid": playlist_id, "format": "json", "g_tk": 5381,
    }, headers={**HEADERS, "Referer": "https://y.qq.com/"}))
    if not data or data.get("code") not in (0, "0"):
        raise PlaylistImportError("QQ 音乐没有返回歌单数据（歌单可能已删除或需要登录）")
    cd = ((data.get("cdlist") or [{}])[0]) or {}
    out = []
    for item in cd.get("songlist") or []:
        singers = item.get("singer") or []
        artist = "、".join(s.get("name", "") for s in singers if s.get("name"))
        album = item.get("albumname") or ""
        mid = item.get("albummid") or ""
        cover = f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{mid}.jpg" if mid else ""
        if item.get("songname") or item.get("name"):
            songmid = item.get("songmid") or item.get("mid") or ""
            out.append(_track(item.get("songname") or item.get("name"), artist, album, cover,
                              int(item.get("interval") or 0), "QQ音乐",
                              f"qqtrack_{songmid}" if songmid else "", "qq"))
    return {
        "name": cd.get("dissname") or "QQ音乐歌单",
        "cover": cd.get("logo") or "",
        "platform": "QQ音乐",
        "tracks": out,
    }


# --------------------------------------------------------------------------- KuGou
def _kugou(session, url: str, playlist_id: str) -> dict:
    if not playlist_id:
        playlist_id = (_first(r"special/single/(\d+)", url)
                       or _first(r"specialid=(\d+)", url)
                       or _first(r"plist/list/(\d+)", url))
    if not playlist_id:
        resp = _get(session, url)
        playlist_id = _first(r"special/single/(\d+)", resp.url) or _first(r"specialid=(\d+)", resp.text)
    if not playlist_id:
        raise PlaylistImportError("没能从链接里识别出酷狗歌单 ID")

    name, cover = "", ""
    page_text = ""
    try:
        page = _get(session, f"https://www.kugou.com/yy/special/single/{playlist_id}.html")
        if page.ok:
            page_text = page.text
            name = html.unescape(_first(r"<title>(.*?)</title>", page_text))
            name = re.sub(r"[-_].*$", "", name).strip()
            cover = _first(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page_text) \
                or _first(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page_text)
            if not cover:
                cover = _first(r'(https?://c\d\.kgimg\.com/[^"\']+\.(?:jpg|png))', page_text)
    except requests.RequestException:
        pass
    data = _json(_get(session, f"https://m.kugou.com/plist/list/{playlist_id}",
                      params={"json": "true"}, headers=MOBILE_HEADERS))
    info = ((((data or {}).get("list") or {}).get("list") or {}).get("info")) or []
    out = []
    for item in info:
        filename = item.get("filename") or ""
        if " - " in filename:
            artist, title = filename.split(" - ", 1)
        else:
            artist, title = "", filename
        title = re.sub(r"\.(mp3|flac|m4a|wav)$", "", title, flags=re.I).strip()
        if title:
            out.append(_track(title, artist, "", cover, int(item.get("duration") or 0), "酷狗音乐"))
    return {
        "name": name or f"酷狗歌单 {playlist_id}",
        "cover": cover,
        "platform": "酷狗音乐",
        "tracks": out,
    }


# --------------------------------------------------------------------------- generic
def _og_image(text: str) -> str:
    return (_first(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', text)
            or _first(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', text)
            or _first(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)', text))


def _generic(session, url: str) -> dict:
    resp = _get(session, url)
    if not resp.ok:
        raise PlaylistImportError(f"分享页无法访问（HTTP {resp.status_code}）")
    name = html.unescape(_first(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', resp.text))
    if not name:
        name = html.unescape(_first(r"<title>(.*?)</title>", resp.text))
    name = re.sub(r"\s*[-_|].*?(音乐|音乐网|官网).*$", "", name or "").strip() or "导入歌单"
    return {
        "name": name,
        "cover": _og_image(resp.text),
        "platform": "网页歌单",
        "tracks": [],
        "note": "这个站点没有公开的歌单接口，只能读取到歌单名称和封面",
    }


def fetch_playlist(url: str, proxy: str = "") -> dict:
    url = (url or "").strip()
    if playlist_share.is_share_link(url):
        try:
            return playlist_share.parse_share_link(url)
        except ValueError as exc:
            raise PlaylistImportError(str(exc)) from exc
    if not url.startswith(("http://", "https://")):
        raise PlaylistImportError(
            "请粘贴 Aria 歌单分享链接（以 aria:// 开头），或网易云 / QQ音乐 / 酷狗的 http(s) 链接")
    session = requests.Session()
    session.proxies.update({"http": proxy, "https": proxy} if proxy else {})
    platform = detect_platform(url)
    try:
        if platform == "netease":
            data = _netease(session, url, "")
        elif platform == "qq":
            data = _qq(session, url, "")
        elif platform == "kugou":
            data = _kugou(session, url, "")
        elif platform == "kuwo":
            raise PlaylistImportError("酷我歌单暂未支持，可改用网易云 / QQ音乐 / 酷狗的分享链接")
        else:
            data = _generic(session, url)
    finally:
        session.close()
    if not data.get("tracks"):
        note = data.get("note") or "没有解析到歌曲，请检查歌单是否公开"
        raise PlaylistImportError(note)
    return data
