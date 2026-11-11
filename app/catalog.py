"""Built-in source catalog.

Only the two sources that ship by default are declared here:
``buguyy`` (search + two-step stream) and ``listen1`` (the bundled Node
sidecar that aggregates the five public web endpoints).  ``OFFICIAL_APIS``
holds optional streaming APIs the user can enable from the sites page;
everything else is a user-added JSON source or an aggregator tool installed
from GitHub.
"""

CATALOG_VERSION = "2026.11.10"

BUILTIN = [
    {
        "id": "buguyy",
        "name": "布谷音乐",
        "kind": "buguyy",
        "tag": "华语曲库 · 整首播放",
        "desc": "公开聚合站：先按关键词检索，播放时再换取一次有效直链，支持整首播放与 LRC 歌词",
        "accent": "#F2A65A",
        "base_url": "https://www.buguyy.top",
        "search_url": "https://www.buguyy.top/api/search",
        "mapping": {
            "query_param": "keyword",
            "results_path": "data",
            "title": "title",
            "artist": "singer",
            "cover": "picurl",
            "audio_from": "https://www.buguyy.top/api/geturl?id={id}",
            "audio_path": "url",
        },
        "enabled": 1,
        "limit": 25,
    },
    {
        "id": "listen1",
        "name": "Listen1",
        "kind": "listen1",
        "tag": "五平台聚合 · 内置侧车",
        "desc": "随应用内置的 Node 侧车，聚合网易云、QQ、酷狗、酷我与汽水的公开接口；发现页与播放由它提供元数据，点播时再统一匹配音源",
        "accent": "#7C9EFF",
        "base_url": "",
        "search_url": "",
        "mapping": {},
        "enabled": 1,
        "limit": 25,
    },
]

KIND_LABELS = {
    "buguyy": "布谷音乐 API",
    "listen1": "内置 Listen1 侧车",
    "generic": "自定义 JSON 接口",
    "html_rule": "网页规则源",
    "official": "自定义官方 API",
    "itunes": "iTunes 官方 API",
    "audius": "Audius 官方 API",
    "deezer": "Deezer 官方 API",
    "jamendo": "Jamendo 官方 API",
    "openverse": "Openverse 官方 API",
    "archive": "Internet Archive",
    "radio": "Radio Browser",
}

# Optional official APIs.  They are not seeded into the database because a
# fresh install intentionally keeps only Listen1.  The "添加官方 API" dialog
# writes one of these rows on demand.
OFFICIAL_APIS = [
    {
        "id": "itunes",
        "name": "iTunes",
        "kind": "itunes",
        "badge": "官方 API",
        "tag": "Apple 官方目录 · 30 秒试听",
        "desc": "Apple iTunes Search API：公开曲库搜索，返回官方 30 秒试听流；支持 Range，播放时保持流式。",
        "accent": "#FA243C",
        "base_url": "https://itunes.apple.com",
        "search_url": "https://itunes.apple.com/search",
        "mapping": {"country": "us"},
        "enabled": 1,
        "is_builtin": 0,
    },
    {
        "id": "audius",
        "name": "Audius",
        "kind": "audius",
        "badge": "官方 API",
        "tag": "Audius 官方 API · 整首流式",
        "desc": "Audius 公共 API：直接返回整首流的官方地址，支持 Range，播放时保持流式，不落本地缓存。",
        "accent": "#7E5BFF",
        "base_url": "https://api.audius.co",
        "search_url": "https://api.audius.co/v1/tracks/search",
        "mapping": {},
        "enabled": 1,
        "is_builtin": 0,
    },
]
