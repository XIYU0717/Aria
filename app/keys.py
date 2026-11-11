"""Stable cross-source song identity.

Different sources spell the same song differently: iTunes may use traditional
Chinese, NetEase appends "(Live)" or " - Remastered", and so on.  A favourite is
stored under a normalised ``title|artist`` key so the same song shows a filled
heart no matter which source surfaced it.
"""

import re

T2S = str.maketrans({
    "傑": "杰", "倫": "伦", "張": "张", "學": "学", "陳": "陈", "華": "华",
    "國": "国", "語": "语", "週": "周", "劉": "刘", "鄧": "邓", "蕭": "萧",
    "羅": "罗", "黃": "黄", "楊": "杨", "趙": "赵", "鄭": "郑", "葉": "叶",
    "吳": "吴", "許": "许", "盧": "卢", "廣": "广", "東": "东", "灣": "湾",
    "臺": "台", "為": "为", "與": "与", "說": "说", "聽": "听", "愛": "爱",
    "夢": "梦", "遠": "远", "這": "这", "們": "们", "個": "个", "來": "来",
    "時": "时", "間": "间", "後": "后", "還": "还", "會": "会", "讓": "让",
    "點": "点", "麼": "么", "樣": "样", "對": "对", "開": "开", "關": "关",
})

_BRACKET = re.compile(r"[（(【\[].*?[）)】\]]")
_VERSION_SUFFIX = re.compile(
    r"\s*[-–—]\s*[^-–—]{0,24}?(live|remaster|remastered|version|edit|mix|"
    r"伴奏|纯音乐|純音樂|翻唱|现场|現場|版)\s*$", re.I)
_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[\s\-_·•.,，。!！?？:：;；'\"“”‘’/\\|@#&+=()（）\[\]【】<>《》]+")


def norm_title(title: str) -> str:
    text = (title or "").translate(T2S)
    text = _BRACKET.sub("", text)
    text = _VERSION_SUFFIX.sub("", text)
    # "晴天 Sunny Day" and "周杰伦-七里香" should collapse to the Chinese core
    # so the same song keeps one key across bilingual catalogues.
    parts = re.findall(r"[\u3400-\u9fff]+", text)
    if parts:
        if len(parts) > 1 and re.search(r"[-–—]", text):
            text = parts[-1]
        else:
            text = "".join(parts)
    text = text.lower()
    text = _PUNCT.sub("", text)
    return text.strip()


def norm_artist(artist: str) -> str:
    text = (artist or "").translate(T2S)
    first = re.split(r"[/、,，&]|\sfeat\.?\s|\sft\.?\s|\s×\s", text, maxsplit=1)[0]
    first = re.sub(r"[（(【\[].*?[）)】\]]", "", first)
    # "周杰倫 Jay Chou" / "Jay 周杰伦" should match the Chinese-only spelling
    # used by NetEase-style sources.
    cjk = re.findall(r"[\u3400-\u9fff]+", first)
    if cjk:
        first = "".join(cjk)
    first = _PUNCT.sub("", first.lower())
    return first.strip()


def song_key(track: dict) -> str:
    title = norm_title((track or {}).get("title", ""))
    artist = norm_artist((track or {}).get("artist", ""))
    if not title:
        return ""
    return f"{title}|{artist}"


def match_key(track: dict) -> str:
    """Key for a manual “默认匹配” binding.

    The user's rule is explicit: the binding is identified by the *original*
    title and artist.  Do not reuse ``song_key`` here — it strips bracketed
    suffixes and version words, which would make two different recordings
    share one binding.
    """
    track = track or {}
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    if not title:
        return ""
    return f"{title}|{artist}"
