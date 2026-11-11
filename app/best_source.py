"""Scoring helpers for the unified cross-source resolver.

Imported playlists and favourites only carry metadata (title / artist /
duration), so ``resolver`` searches every enabled source and uses ``score`` to
rank candidates before checking that the winning stream actually resolves.
"""

from difflib import SequenceMatcher

import requests

from keys import norm_artist, norm_title

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

SOURCE_BONUS = {"buguyy": 0.8, "audius": 0.55, "jamendo": 0.4, "archive": 0.35,
                "generic": 0.25, "itunes": -1.2, "radio": -0.5}


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 2 and short in long:
        return 0.88
    return SequenceMatcher(None, a, b).ratio()


def _artist_sim(target: str, cand: str) -> float:
    a, b = norm_artist(target), norm_artist(cand)
    if not a or not b:
        return 0.45
    if a == b:
        return 1.0
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        return 0.72
    return 0.0


def _cover_url(value) -> str:
    text = str(value or "").split("?", 1)[0].split("#", 1)[0].strip().lower()
    return text


def score(target: dict, cand: dict) -> float:
    title = _sim(norm_title(target.get("title", "")), norm_title(cand.get("title", "")))
    artist = _artist_sim(target.get("artist", ""), cand.get("artist", ""))
    album = _sim(norm_title(target.get("album", "")), norm_title(cand.get("album", "")))
    cover_match = 0.0
    target_cover = _cover_url(target.get("cover"))
    cand_cover = _cover_url(cand.get("cover"))
    if target_cover and cand_cover and target_cover == cand_cover:
        cover_match = 0.35
    has_cover = 0.12 if cand_cover else 0.0
    # Titles differ between catalogues far more often than artists/albums do
    # ("晴天" vs "晴天 (Live)", translated names, punctuation).  A weaker title
    # is allowed only when the artist and/or album still identify the recording.
    artist_support = artist >= 0.8
    album_support = album >= 0.65 and bool(norm_title(cand.get("album", "")))
    if title < 0.55:
        # A translated / retitled release can still be the same recording when
        # both the artist and album identify it; otherwise it is noise.
        if not (artist >= 0.9 and album_support):
            return -1.0
    if title < 0.72 and not (artist_support or (artist >= 0.6 and album_support)):
        return -1.0
    if title < 0.94 and artist < 0.5 and not album_support:
        return -1.0
    total = title * 2.2 + artist * 1.7 + album * 0.9
    if title >= 0.94:
        total += 0.35
    total += cover_match + has_cover
    td, cd = int(target.get("duration") or 0), int(cand.get("duration") or 0)
    if td and cd:
        diff = abs(td - cd)
        total += 0.55 if diff <= 5 else 0.35 if diff <= 12 else 0.15 if diff <= 25 else -0.35
    total += SOURCE_BONUS.get(cand.get("kind", ""), 0.0)
    if cand.get("is_preview"):
        total -= 0.85
    if cand.get("is_live"):
        total -= 0.6
    if target.get("source_id") and cand.get("source_id") == target.get("source_id"):
        total += 0.35
    return total


def _stream_ok(cand: dict, proxy: str, timeout: int) -> bool:
    """Some APIs (Audius notably) return a stream URL that 404s; verify before use."""
    url = cand.get("audio") or ""
    if not url:
        return False
    headers = dict(cand.get("headers") or {})
    headers.setdefault("User-Agent", UA)
    headers.setdefault("Range", "bytes=0-1023")
    try:
        response = requests.get(url, headers=headers, stream=True,
                                timeout=min(timeout, 6), allow_redirects=True,
                                proxies={"http": proxy, "https": proxy} if proxy else None)
        ok = response.status_code in (200, 206)
        response.close()
        return ok
    except requests.RequestException:
        return False


def stream_ok(cand: dict, proxy: str = "", timeout: int = 8) -> bool:
    """Public probe used by the unified resolver."""
    return _stream_ok(cand, proxy=proxy, timeout=timeout)
