"""Self-contained, static playlist share links.

The playlist data is compressed into the link fragment, so sharing never
depends on a third-party paste service and the link keeps working as long as
the text is kept.  Aria recognises the link in its normal "导入歌单" dialog.
"""

import base64
import json
import secrets
import zlib
from urllib.parse import parse_qs, urlparse

SHARE_SCHEME = "aria"
SHARE_HOST = "playlist"
SHARE_PREFIX = f"{SHARE_SCHEME}://{SHARE_HOST}/"
_VERSION = 1

# Keep metadata that another Aria can use to match the song again.  Stream
# URLs, tokens and source-specific ids intentionally do not travel: they expire
# or point at the sharing device.
_TRACK_FIELDS = (
    "title", "artist", "album", "duration", "cover", "source_name",
    "source_kind", "page_url", "platform", "song_id", "album_artist",
)


def _clean_track(track: dict) -> dict:
    out = {}
    for key in _TRACK_FIELDS:
        value = track.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            out[key] = value
    return out


def make_share_link(data: dict) -> str:
    """Build a random-looking, static ``aria://playlist/...`` link."""
    tracks = [_clean_track(t) for t in (data.get("tracks") or []) if isinstance(t, dict)]
    payload = {
        "v": _VERSION,
        "name": str(data.get("name") or "分享歌单"),
        "cover": str(data.get("cover") or ""),
        "platform": str(data.get("platform") or "Aria 歌单"),
        "note": str(data.get("note") or ""),
        "tracks": tracks,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")
    token = secrets.token_urlsafe(9)
    return f"{SHARE_PREFIX}{token}#{body}"


def is_share_link(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    return parsed.scheme.lower() == SHARE_SCHEME and parsed.netloc.lower() == SHARE_HOST


def parse_share_link(text: str) -> dict:
    """Decode a link produced by :func:`make_share_link`.

    Raises ``ValueError`` for malformed or unsupported links so the import layer
    can show a useful message instead of trying to fetch it as a web page.
    """
    text = (text or "").strip()
    if not is_share_link(text):
        raise ValueError("这不是 Aria 歌单分享链接")
    parsed = urlparse(text)
    body = (parsed.fragment or "").strip()
    if not body:
        body = (parse_qs(parsed.query).get("d") or [""])[0]
    if not body:
        raise ValueError("分享链接里没有歌单数据")
    padded = body + "=" * (-len(body) % 4)
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(padded.encode("ascii")))
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("分享链接数据损坏或版本不兼容") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("tracks"), list):
        raise ValueError("分享链接里没有有效的歌单")
    tracks = [_clean_track(t) for t in payload["tracks"] if isinstance(t, dict)]
    if not tracks:
        raise ValueError("分享歌单里没有歌曲")
    return {
        "name": str(payload.get("name") or "分享歌单"),
        "cover": str(payload.get("cover") or ""),
        "platform": str(payload.get("platform") or "Aria 歌单"),
        "note": str(payload.get("note") or ""),
        "tracks": tracks,
    }
