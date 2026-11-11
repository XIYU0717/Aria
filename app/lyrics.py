"""Lyrics lookup + LRC parsing.

Matching order: time-coded lyrics embedded in the track -> the best of
LRCLIB and NetEase Cloud Music, using artist/album/title scoring.  Every
provider is best-effort; a total miss is not an error.
"""

import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

import requests

from adapters import UA

TIME_RE = re.compile(r"\[(\d+):(\d+)(?:[.:](\d+))?(?:-\d+)?\]")
SEC_RE = re.compile(r"^\[(\d+(?:\.\d+)?)\]\s*(.*)$")
TAG_RE = re.compile(r"\[[a-zA-Z]+:.*?\]")
NETEASE_API = "https://music.163.com/api"
T2S = str.maketrans({
    "傑": "杰", "倫": "伦", "張": "张", "學": "学", "陳": "陈", "華": "华",
    "國": "国", "語": "语", "週": "周", "劉": "刘", "鄧": "邓", "蕭": "萧",
    "羅": "罗", "黃": "黄", "楊": "杨", "趙": "赵", "鄭": "郑", "葉": "叶",
    "吳": "吴", "許": "许", "盧": "卢", "廣": "广", "東": "东", "灣": "湾",
    "臺": "台", "灣": "湾", "為": "为", "與": "与", "說": "说", "聽": "听",
})


def _plain(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = text.replace("\ufeff", "")
    text = re.sub(r"<[^>]+>", "", text)
    # LRCLIB occasionally stores Rust-style wrappers such as Optional("不散")
    # in album/artist fields; show the real value instead of the wrapper.
    text = re.sub(r'(?:Optional|Some)\("([^"]*)"\)', r"\1", text)
    text = re.sub(r"(?:Optional|Some)\(([^()]*)\)", r"\1", text)
    return text


def parse_lrc(text: str) -> list[tuple[int, str]]:
    """Parse LRC text into [(start_ms, line)]; tolerates <br/> markup and BOM."""
    lines: list[tuple[int, str]] = []
    for raw in _plain(text).splitlines():
        stamps = list(TIME_RE.finditer(raw))
        if not stamps:
            short = SEC_RE.match(raw.strip())
            if short:
                content = TAG_RE.sub("", short.group(2)).strip()
                if content:
                    lines.append((int(float(short.group(1)) * 1000), content))
            continue
        content = TAG_RE.sub("", raw[stamps[-1].end():]).strip()
        minutes, seconds, frac = stamps[-1].groups()
        ms = int(minutes) * 60_000 + int(seconds) * 1000
        if frac:
            frac = (frac + "00")[:3]
            ms += int(frac)
        if content:
            lines.append((ms, content))
    lines.sort(key=lambda item: item[0])
    deduped: list[tuple[int, str]] = []
    for ms, content in lines:
        if deduped and deduped[-1][0] == ms and deduped[-1][1] == content:
            continue
        deduped.append((ms, content))
    return deduped


def clean_title(title: str) -> str:
    title = re.sub(r"[（(【\[].*?[）)】\]]", "", title or "").strip()
    return re.sub(r"\s+", " ", title)


def _artists(track: dict) -> list[str]:
    raw = track.get("artist", "") or ""
    parts = [p.strip() for p in re.split(r"[/、,，&]| feat\.? | ft\.? ", raw) if p.strip()]
    return parts or ([raw.strip()] if raw.strip() else [])


def _norm(text: str) -> str:
    return (text or "").translate(T2S).lower().replace(" ", "")


_NAME_PUNCT = re.compile(
    r"[\s\-_·•.,，。!！?？:：;；'\"“”‘’/\\|@#&+=()（）\[\]【】<>《》]+")
_SCRIPT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_STOP_WORDS = {
    "the", "a", "an", "of", "and", "to", "in", "on", "for", "with", "by",
    "from", "feat", "ft", "live", "remix", "version", "edit", "acoustic",
    "official", "audio", "video", "mv", "ost", "deluxe", "remastered",
    "remaster", "extended", "original",
}


def _compact(text: str) -> str:
    """Punctuation/space-insensitive name for artist and album comparison."""
    return _NAME_PUNCT.sub("", _norm(text))


def _artist_hit(artists: list[str], candidate: str) -> bool:
    """True when at least one target artist is the same act as a candidate name."""
    targets = [_compact(a) for a in artists if _compact(a)]
    candidates = [_compact(a) for a in _artists({"artist": candidate}) if _compact(a)]
    if not targets or not candidates:
        return False
    for target in targets:
        for name in candidates:
            if target == name:
                return True
            # "Jay Chou" vs "Jay Chou / 周杰伦"; require at least 2 chars so
            # a one-letter artist cannot match everything by substring.
            if len(target) >= 2 and len(name) >= 2 and (target in name or name in target):
                return True
    return False


def _keywords(text: str) -> set[str]:
    """Latin words plus CJK bigrams, used for title keyword overlap."""
    parts = re.findall(r"[^\W_]+", (text or "").lower().translate(T2S), flags=re.UNICODE)
    keys: set[str] = set()
    for part in parts:
        if _SCRIPT_RE.search(part):
            if len(part) <= 2:
                keys.add(part)
            else:
                keys.update(part[i:i + 2] for i in range(len(part) - 1))
        elif len(part) >= 2 and part not in _STOP_WORDS:
            keys.add(part)
    return keys


def _text_overlap(target: str, candidate: str) -> float:
    left = _keywords(target)
    right = _keywords(candidate)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left)


def _title_similarity(target: str, candidate: str) -> tuple[int, bool]:
    """Return (0..100 keyword-overlap score, exact-after-clean flag).

    Titles are compared as written: bracketed suffixes such as ``(Live)`` stay
    in the fuzzy score instead of being stripped before comparison.
    """
    if not target or not candidate:
        return 0, False
    if _norm(target) == _norm(candidate):
        return 100, True
    left = _compact(target)
    right = _compact(candidate)
    if not left or not right:
        return 0, False
    overlap = _text_overlap(target, candidate)
    fuzzy = SequenceMatcher(None, left, right).ratio()
    score = min(1.0, 0.75 * overlap + 0.25 * fuzzy)
    return int(round(score * 100)), False


def _album_similarity(target: str, candidate: str) -> int:
    """0..100; an absent album on either side is ignored rather than penalised."""
    left = _compact(target)
    right = _compact(candidate)
    if not left or not right:
        return 0
    if left == right:
        return 100
    if len(left) >= 3 and (left in right or right in left):
        return 75
    return int(round(_text_overlap(target, candidate) * 100))


def lyric_match_details(track: dict, candidate: dict) -> dict:
    """Unified ranking used by automatic lookup and the lyric picker.

    Artist dominates: a mismatched-artist candidate can never overtake a
    matched-artist one (1000 vs at most 250 from album + title).  Within the
    same artist tier, album is checked before title keyword overlap.
    """
    target_title = str(track.get("title") or "")
    target_artists = _artists(track)
    artist_ok = _artist_hit(target_artists, str(candidate.get("artist") or ""))
    album_score = _album_similarity(str(track.get("album") or ""),
                                    str(candidate.get("album") or ""))
    title_score, title_exact = _title_similarity(
        target_title, str(candidate.get("title") or ""))
    payload = candidate.get("_lyric") or {}
    lines = payload.get("lines") or []
    score = (1000 if artist_ok else 0) + int(round(album_score * 1.5)) + title_score
    if payload.get("synced"):
        score += 4
    if lines:
        score += min(6, len(lines) // 20)
    return {
        "score": score,
        "artist": artist_ok,
        "album": album_score,
        "title": title_score,
        "title_exact": title_exact,
        "synced": bool(payload.get("synced")),
        "lines": len(lines),
    }


def _translations(text: str) -> dict[int, str]:
    """Parse a translated LRC into {start_ms: translated line}."""
    if not isinstance(text, str) or not text.strip():
        return {}
    return {
        ms: content for ms, content in parse_lrc(text)
        if content
    }


def _split_bilingual(lines: list[tuple[int, str]],
                     translations: dict[int, str]) -> tuple[list[tuple[int, str]], dict[int, str]]:
    """Pull paired Chinese translation lines out of a bilingual track LRC.

    Some providers return "English line / Chinese line" as consecutive lyric
    rows instead of a separate tlyric track.  Treat the CJK row as the
    translation of the preceding Latin row so the stage can keep the real
    next line in the footer instead of showing the translation twice.
    """
    if translations or not lines:
        return lines, translations
    kept: list[tuple[int, str]] = []
    trans: dict[int, str] = {}
    i = 0
    while i < len(lines):
        ms, text = lines[i]
        if i + 1 < len(lines):
            nms, ntext = lines[i + 1]
            latin = re.search(r"[A-Za-z]", text or "")
            cjk = re.search(r"[\u4e00-\u9fff]", ntext or "")
            n_latin = re.search(r"[A-Za-z]", ntext or "")
            if latin and not re.search(r"[\u4e00-\u9fff]", text or "") \
                    and cjk and not n_latin and 0 <= nms - ms <= 6_000:
                kept.append((ms, text))
                trans[ms] = ntext
                i += 2
                continue
        kept.append((ms, text))
        i += 1
    return kept, trans


def _title_keys(title: str) -> list[str]:
    cleaned = clean_title(title)
    keys = [cleaned]
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", cleaned)
    if cjk and cjk != cleaned:
        keys.append(cjk)
    return [_norm(k) for k in keys if k]


def _title_variants(title: str) -> list[str]:
    cleaned = clean_title(title)
    variants = [cleaned]
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", cleaned)
    if cjk and cjk != cleaned:
        variants.append(cjk)
    return [v for v in variants if v]


def _title_hit(title: str, candidate: str) -> bool:
    cand = _norm(candidate)
    return any(key and key in cand for key in _title_keys(title))


def _good(lines: list[tuple[int, str]]) -> bool:
    """Reject stubs like a bare '作词/作曲' block that would hijack the real match."""
    return len(lines) >= 4 and (lines[-1][0] - lines[0][0]) >= 15_000


def _from_track(track: dict) -> dict:
    """Trust only time-coded lyrics that came with the track itself.

    Buguyy search results used to expose an `about` field that is *not*
    reliably the current song's lyric sheet: the catalogue sometimes returns
    another track's lyrics for the same title.  A plain-text blob therefore
    cannot prove that it belongs to this song, and feeding it to the stage
    makes the first play look mismatched while the next play (which goes
    through online lookup) looks correct.  Keep the inline path only for LRC
    with real timestamps; plain inline text falls through to the online
    matcher.
    """
    translated = _translations(
        track.get("tlyric") or track.get("translated_lrc") or track.get("translation") or "")
    for key in ("lrc", "lyrics_text", "lyrics"):
        text = track.get(key) or ""
        if not isinstance(text, str) or not text.strip():
            continue
        # `parse_lrc` also accepts "[12.3] text" second-based lines, so require
        # a real LRC tag before trusting the payload as this track's lyrics.
        if not TIME_RE.search(text):
            continue
        lines = parse_lrc(text)
        lines, translated = _split_bilingual(lines, translated)
        if _good(lines):
            return {"lines": lines, "source": track.get("source_name") or "曲目自带",
                    "synced": True, "plain": _plain(text), "translations": translated}
    return {}


def _score(row: dict, track: dict) -> tuple[int, int, int]:
    """LRCLIB/NetEase row score; artist match dominates album then title."""
    details = lyric_match_details(track, {
        "title": row.get("trackName") or row.get("name") or "",
        "artist": row.get("artistName") or "",
        "album": row.get("albumName") or "",
    })
    return (details["score"],
            1 if row.get("syncedLyrics") else 0,
            len(row.get("syncedLyrics") or ""))


def _proxies(proxy: str) -> dict | None:
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def fetch_lrclib(track: dict, timeout: int = 8, proxy: str = "") -> dict:
    artists = _artists(track)
    headers = {"User-Agent": UA, "Accept": "application/json"}
    payloads = []
    for name in _title_variants(track.get("title", "")):
        if artists:
            payloads.append({"track_name": name, "artist_name": artists[0]})
        payloads.append({"track_name": name})
    if not payloads:
        return {}

    rows: list[dict] = []
    seen: set[tuple] = set()
    for params in payloads:
        try:
            resp = requests.get("https://lrclib.net/api/search", params=params,
                                headers=headers, timeout=timeout, proxies=_proxies(proxy))
            if resp.status_code >= 400:
                continue
            batch = resp.json()
        except Exception:
            continue
        if not isinstance(batch, list):
            continue
        for row in batch:
            if not isinstance(row, dict):
                continue
            key = (row.get("id"), row.get("trackName"), row.get("artistName"),
                   row.get("albumName"), row.get("duration"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

    # Compare the artist and title-only result sets together.  Previously the
    # first non-empty query won, so a same-title foreign song could beat the
    # correct artist version without ever being compared.
    for best in sorted(rows, key=lambda item: _score(item, track), reverse=True)[:12]:
        lines = parse_lrc(best.get("syncedLyrics") or "")
        if _good(lines):
            return {"lines": lines, "source": "LRCLIB", "synced": True,
                    "plain": best.get("plainLyrics") or "",
                    "_match": {"title": best.get("trackName") or "",
                               "artist": best.get("artistName") or "",
                               "album": best.get("albumName") or ""}}
        plain = (best.get("plainLyrics") or "").strip()
        if plain:
            plain_lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
            if len(plain_lines) >= 3:
                return {"lines": [(i * 4000, ln) for i, ln in enumerate(plain_lines)],
                        "source": "LRCLIB", "synced": False, "plain": plain,
                        "_match": {"title": best.get("trackName") or "",
                                   "artist": best.get("artistName") or "",
                                   "album": best.get("albumName") or ""}}
    return {}


def fetch_netease(track: dict, timeout: int = 8, proxy: str = "") -> dict:
    title = clean_title(track.get("title", ""))
    artists = _artists(track)
    variants = _title_variants(track.get("title", ""))
    if not variants:
        return {}
    query_title = variants[-1] if len(variants) > 1 else variants[0]
    lead = artists[0] if artists else ""
    if " " in lead and re.search(r"[\u4e00-\u9fff]", lead):
        lead = next((part for part in lead.split() if re.search(r"[\u4e00-\u9fff]", part)), lead)
    query = " ".join([query_title, lead]).strip()
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://music.163.com/"})
    if proxy:
        session.proxies.update(_proxies(proxy) or {})
    try:
        resp = session.post(f"{NETEASE_API}/search/get",
                            data={"s": query, "type": 1, "limit": 5, "offset": 0},
                            timeout=timeout)
        songs = ((resp.json() or {}).get("result") or {}).get("songs") or []
    except Exception:
        return {}
    scored: list[tuple[int, dict]] = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        name = _plain(song.get("name") or "")
        singer = " / ".join(_plain(a.get("name") or "")
                            for a in (song.get("artists") or []) if isinstance(a, dict))
        album = _plain((song.get("album") or {}).get("name") or "")
        details = lyric_match_details(track, {
            "title": name, "artist": singer, "album": album,
        })
        if not _title_hit(title, name) and details["title"] < 60:
            continue
        scored.append((details["score"], song))
    best = max(scored, key=lambda item: item[0])[1] if scored else None
    if not best:
        return {}
    best_title = _plain(best.get("name") or "")
    best_artist = " / ".join(_plain(a.get("name") or "")
                             for a in (best.get("artists") or []) if isinstance(a, dict))
    best_album = _plain((best.get("album") or {}).get("name") or "")
    try:
        lyr = session.get(f"{NETEASE_API}/song/lyric",
                          params={"id": best.get("id"), "lv": 1, "kv": 1, "tv": -1},
                          timeout=timeout).json()
    except Exception:
        return {}
    lines = parse_lrc(((lyr.get("lrc") or {}).get("lyric")) or "")
    if _good(lines):
        translated = _translations(((lyr.get("tlyric") or {}).get("lyric")) or "")
        return {"lines": lines, "source": "网易云音乐", "synced": True,
                "plain": ((lyr.get("lrc") or {}).get("lyric")) or "",
                "translations": translated,
                "_match": {"title": best_title, "artist": best_artist,
                           "album": best_album}}
    return {}


def _plain_payload(text: str, source: str) -> dict:
    body = _plain(text).strip()
    rows = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if len(rows) < 3:
        return {}
    return {"lines": [(i * 4000, ln) for i, ln in enumerate(rows)],
            "source": source, "synced": False, "plain": body, "translations": {}}


def _lrclib_lyric(row: dict) -> dict:
    synced_text = row.get("syncedLyrics") or ""
    lines = parse_lrc(synced_text)
    if _good(lines):
        return {"lines": lines, "source": "LRCLIB", "synced": True,
                "plain": synced_text or row.get("plainLyrics") or "",
                "translations": {}}
    return _plain_payload(row.get("plainLyrics") or "", "LRCLIB")


def _netease_lyric(song: dict, proxy: str, timeout: int) -> dict:
    headers = {"User-Agent": UA, "Referer": "https://music.163.com/",
               "Accept": "application/json"}
    try:
        resp = requests.get(f"{NETEASE_API}/song/lyric",
                            params={"id": song.get("id"), "lv": 1, "kv": 1, "tv": -1},
                            headers=headers, timeout=timeout,
                            proxies=_proxies(proxy))
        lyr = resp.json() or {}
    except Exception:
        return {}
    text = ((lyr.get("lrc") or {}).get("lyric")) or ""
    lines = parse_lrc(text)
    translated = _translations(((lyr.get("tlyric") or {}).get("lyric")) or "")
    lines, translated = _split_bilingual(lines, translated)
    if _good(lines):
        return {"lines": lines, "source": "网易云音乐", "synced": True,
                "plain": text, "translations": translated}
    return _plain_payload(text, "网易云音乐")


def _lyric_rank(track: dict, candidate: dict) -> tuple:
    details = lyric_match_details(track, candidate)
    return (details["score"], 1 if details["artist"] else 0,
            details["album"], details["title"],
            1 if details["synced"] else 0, details["lines"])


def search_lyric_candidates(track: dict, proxy: str = "", timeout: int = 8,
                            limit: int = 20) -> list[dict]:
    """Search full lyric versions for manual replacement.

    Candidates are ranked by artist, album, then title keyword overlap.  Only
    rows with usable lyric lines are returned, so every item in the picker can
    actually replace the current sheet in both lyric surfaces.
    """
    title = clean_title(track.get("title", ""))
    if not title:
        return []
    artists = _artists(track)
    lead_artist = artists[0] if artists else ""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    candidates: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add(candidate: dict) -> None:
        lyric = candidate.get("_lyric") or {}
        if not lyric.get("lines"):
            return
        identity = (_norm(clean_title(candidate.get("title", ""))),
                    _norm(candidate.get("artist", "")),
                    str(candidate.get("source") or ""))
        if not identity[0] or identity in seen:
            return
        seen.add(identity)
        candidates.append(candidate)

    # LRCLIB is best for synced lyrics.  Ask with artist first, then title-only
    # so live/acoustic editions still appear as fuzzy title candidates.
    lrclib_rows: list[dict] = []
    queries = [{"track_name": title}]
    if lead_artist:
        queries.insert(0, {"track_name": title, "artist_name": lead_artist})

    def fetch_lrclib_query(params: dict) -> list[dict]:
        try:
            resp = requests.get("https://lrclib.net/api/search", params=params,
                                headers=headers, timeout=timeout,
                                proxies=_proxies(proxy))
            rows = resp.json() if resp.status_code < 400 else []
        except Exception:
            rows = []
        return rows if isinstance(rows, list) else []

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fetch_lrclib_query, params) for params in queries]
        for future in futures:
            try:
                for row in future.result():
                    if isinstance(row, dict) and row not in lrclib_rows:
                        lrclib_rows.append(row)
            except Exception:
                continue
    for row in lrclib_rows[:24]:
        payload = _lrclib_lyric(row)
        if not payload:
            continue
        add({"title": _plain(row.get("trackName") or ""),
             "artist": _plain(row.get("artistName") or ""),
             "album": _plain(row.get("albumName") or ""),
             "duration": row.get("duration") or 0,
             "source": "LRCLIB", "_lyric": payload})

    # NetEase search gives the Chinese catalogue; fetch each lyric in parallel
    # so a 20-row result still fills the picker quickly.
    songs: list[dict] = []
    query = " ".join([title, lead_artist]).strip()
    try:
        session_headers = {**headers, "Referer": "https://music.163.com/"}
        resp = requests.post(f"{NETEASE_API}/search/get",
                             data={"s": query, "type": 1, "limit": 24, "offset": 0},
                             headers=session_headers, timeout=timeout,
                             proxies=_proxies(proxy))
        songs = (((resp.json() or {}).get("result") or {}).get("songs") or [])
    except Exception:
        songs = []
    rows: list[tuple[float, dict]] = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        song_title = _plain(song.get("name") or "")
        song_artists = " / ".join(_plain(a.get("name") or "")
                                  for a in (song.get("artists") or []) if isinstance(a, dict))
        sim = SequenceMatcher(None, _norm(title), _norm(clean_title(song_title))).ratio()
        if sim < 0.34:
            continue
        rows.append((sim, {"title": song_title, "artist": song_artists,
                           "album": _plain((song.get("album") or {}).get("name") or ""),
                           "duration": (song.get("duration") or 0) / 1000,
                           "source": "网易云音乐", "id": song.get("id"),
                           "sim": sim}))
    rows.sort(key=lambda item: item[0], reverse=True)
    picked = [row for _sim, row in rows[:18] if row.get("id")]

    def fetch_row(row: dict) -> tuple[dict, dict]:
        return row, _netease_lyric(row, proxy, timeout)

    if picked:
        pool = ThreadPoolExecutor(max_workers=6)
        futures = [pool.submit(fetch_row, row) for row in picked]
        try:
            for future in as_completed(futures, timeout=8):
                try:
                    row, payload = future.result()
                except Exception:
                    continue
                if payload:
                    clean = {k: v for k, v in row.items() if k not in ("id", "sim")}
                    add({**clean, "_lyric": payload})
        except TimeoutError:
            pass
        # Never block the picker on a slow NetEase lyric endpoint; already
        # returned LRCLIB rows are enough to choose from.
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)

    # The raw title/artist are shown in the picker; only the match score is
    # normalized.  Artist is the first gate, then album, then title overlap.
    for candidate in candidates:
        details = lyric_match_details(track, candidate)
        candidate["_rank"] = _lyric_rank(track, candidate)
        candidate["_score"] = details["score"]
        candidate["_exact"] = bool(details["title_exact"])
        candidate["_artistMatch"] = bool(details["artist"])
        candidate["_albumMatch"] = details["album"] >= 60
        candidate["_titleScore"] = int(details["title"])
    candidates.sort(key=lambda item: item.get("_rank") or (0, 0, 0, 0, 0, 0),
                    reverse=True)
    return candidates[:max(1, int(limit))]


def fetch_lyrics(track: dict, proxy: str = "", timeout: int = 8) -> dict:
    """Best-effort lyrics for a track: {'lines': [(ms, text)], 'source', 'synced'}."""
    inline = _from_track(track)
    if inline:
        return inline
    chinese = bool(re.search(r"[\u4e00-\u9fff]",
                             f"{track.get('title', '')}{track.get('artist', '')}"))
    providers = ((fetch_netease, fetch_lrclib) if chinese
                 else (fetch_lrclib, fetch_netease))
    # Do not return the first provider that happens to have lines.  For a
    # Chinese title NetEase is queried first, and a same-title cover/duet can
    # come back before LRCLIB's correct artist version.  That used to show a
    # wrong sheet on the first play; compare both results with the same
    # artist/album/title scoring used by the manual picker instead.
    best: dict = {}
    best_score = -1
    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        futures = [pool.submit(provider, track, timeout=timeout, proxy=proxy)
                   for provider in providers]
        for future in futures:
            try:
                data = future.result()
            except Exception:
                data = {}
            if not data or not data.get("lines"):
                continue
            meta = data.get("_match") or {}
            if meta:
                details = lyric_match_details(track, {**meta, "_lyric": data})
                score = details["score"]
            else:
                score = 0
            if score > best_score:
                best_score = score
                best = data
    return best


def current_index(lines: list[tuple[int, str]], ms: int) -> int:
    idx = 0
    for i, (time_ms, _text) in enumerate(lines):
        if time_ms <= ms:
            idx = i
        else:
            break
    return idx
