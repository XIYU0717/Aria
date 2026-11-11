"""Heuristic analyzer for ordinary server-rendered HTML music sites.

The analyzer is deliberately conservative: it only returns a source when it
can find (a) a search URL that returns a repeated result list, (b) readable
title/artist fields, (c) a detail page, and (d) an audio URL.  When something
is missing the caller can fall back to the manual rule editor.
"""

from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin, urlparse

import requests

import html_rule

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
}
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav", ".opus")
_SEARCH_HINTS = ("search", "/so/", "/s/", "/find", "/music", "keyword", "query")
_DETAIL_HINTS = ("song", "music", "detail", "track", "play", "album", "video")
_ID_ATTRS = ("data-rid", "data-id", "data-songid", "data-song-id", "data-track-id",
             "data-key", "data-uuid", "data-music-id", "id")
_LOCATION_RE = re.compile(r"(?:window\.)?location(?:\.href)?\s*=\s*([^;]+);?", re.S)
_STRING_RE = re.compile(r"['\"]([^'\"]*)['\"]")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _session(proxy: str = "") -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def _fetch(session: requests.Session, url: str, timeout: float) -> tuple[str, str]:
    response = session.get(url, timeout=timeout, allow_redirects=True)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}")
    ctype = (response.headers.get("Content-Type") or "").lower()
    if "html" not in ctype and "text" not in ctype:
        raise RuntimeError(f"不是 HTML 页面（{ctype or '未知类型'}）")
    response.encoding = response.encoding or response.apparent_encoding
    return response.text, response.url


def _normalize_root(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("请输入网站地址")
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError("网站地址不完整")
    return f"{parsed.scheme}://{parsed.netloc}/"


def _format(query: str, page: int) -> dict[str, str]:
    return {"query": quote(query, safe=""), "page": str(max(1, page))}


def _fill(template: str, query: str, page: int) -> str:
    result = template
    for key, value in _format(query, page).items():
        result = result.replace("{" + key + "}", value)
    return result


def _add_candidate(candidates: list[dict], template: str, source: str) -> None:
    template = (template or "").strip()
    if not template or "{query}" not in template:
        return
    if any(item["template"] == template for item in candidates):
        return
    candidates.append({"template": template, "source": source})


def _form_candidates(root: str, tree: html_rule.Node) -> list[dict]:
    candidates: list[dict] = []
    for form in tree.iter_elements():
        if form.tag != "form":
            continue
        action = html_rule._attr(form, "action") or root
        method = (html_rule._attr(form, "method") or "get").lower()
        if method != "get":
            continue
        action = urljoin(root, action)
        names = []
        for field in form.iter_elements():
            if field.tag not in {"input", "textarea"}:
                continue
            name = html_rule._attr(field, "name")
            kind = (html_rule._attr(field, "type") or "text").lower()
            if not name or kind in {"submit", "button", "reset", "file", "checkbox", "radio"}:
                continue
            if any(word in name.lower() for word in ("search", "keyword", "query", "q", "wd", "word")):
                names.insert(0, name)
            else:
                names.append(name)
        if not names:
            continue
        param = names[0]
        sep = "&" if "?" in action else "?"
        _add_candidate(candidates, f"{action}{sep}{param}={{query}}", "页面搜索表单")
    return candidates


def _js_candidates(root: str, text: str) -> tuple[list[dict], list[str]]:
    candidates: list[dict] = []
    detail_paths: list[str] = []
    for match in _LOCATION_RE.finditer(text):
        expression = match.group(1)
        lowered = expression.lower()
        strings = [item for item in _STRING_RE.findall(expression) if item]
        if not strings:
            continue
        has_query_var = any(word in lowered for word in
                             ("keyword", "query", "searchword", "search_word", "q", "wd"))
        for path in strings:
            clean = path.strip()
            if not clean or clean.startswith(("javascript:", "#", "data:")):
                continue
            if any(hint in clean.lower() for hint in _SEARCH_HINTS):
                if has_query_var and "{query}" not in clean:
                    if clean.endswith(("/", "=", "?")):
                        clean += "{query}"
                    else:
                        clean += "/{query}"
                    page_prefix = ""
                    for other in strings:
                        other_clean = other.strip()
                        if other_clean == path:
                            continue
                        if re.fullmatch(r"/?\d+/?", other_clean):
                            page_prefix = other_clean.strip("/") or "1"
                    if page_prefix:
                        if clean.endswith("/"):
                            clean += "{page}/"
                        else:
                            clean += "/{page}"
                    url = urljoin(root, clean)
                    _add_candidate(candidates, url, "首页 JavaScript")
                elif "{query}" in clean:
                    _add_candidate(candidates, urljoin(root, clean), "首页 JavaScript")
            if any(hint in clean.lower() for hint in _DETAIL_HINTS):
                if any(word in lowered for word in ("rid", "songid", "song_id", "trackid", "id")):
                    if re.search(r"\d", clean) or clean.endswith("/"):
                        detail_paths.append(clean)
    return candidates, detail_paths


def _common_candidates(root: str) -> list[dict]:
    templates = [
        "/so/{query}/{page}/",
        "/so/{query}/1/",
        "/search?q={query}&page={page}",
        "/search?keyword={query}&page={page}",
        "/search?q={query}",
        "/?s={query}",
        "/search/{query}/",
        "/search/{query}/{page}/",
        "/s/{query}/",
        "/music/search?q={query}",
    ]
    result = []
    for path in templates:
        url = urljoin(root, path)
        _add_candidate(result, url, "常见搜索路径")
    return result


def _search_candidates(root: str, home: str) -> list[dict]:
    tree = html_rule.parse_html(home)
    candidates: list[dict] = []
    candidates.extend(_form_candidates(root, tree))
    js_search, _ = _js_candidates(root, home)
    candidates.extend(js_search)
    candidates.extend(_common_candidates(root))
    return candidates


def _dynamic_class(value: str) -> bool:
    value = value or ""
    return bool(re.search(r"^(?:\w{10,}|\d{4,}|css-|sc-|jsx-|\d)", value))


def _selector_for(node: html_rule.Node, scope: html_rule.Node | None = None) -> str:
    candidates = []
    for cls in node.classes():
        if cls and not _dynamic_class(cls):
            candidates.append(f".{cls}")
    if node.attrs.get("id") and not _dynamic_class(node.attrs["id"]):
        candidates.append(f"#{node.attrs['id']}")
    for attr in ("data-id", "data-rid", "data-songid", "data-track-id", "data-key"):
        if node.attrs.get(attr):
            candidates.append(f"{node.tag}[{attr}]")
    candidates.append(node.tag)
    for selector in candidates:
        if scope is None:
            return selector
        found = html_rule.first(scope, selector)
        if found is node:
            return selector
    return candidates[0]


def _group_signature(node: html_rule.Node) -> tuple:
    classes = tuple(cls for cls in node.classes() if not _dynamic_class(cls))
    attrs = tuple(name for name in ("data-id", "data-rid", "data-songid", "data-track-id", "data-key")
                  if node.attrs.get(name))
    return node.tag, classes[:2], attrs[:1]


def _best_group(tree: html_rule.Node) -> dict | None:
    best = None
    for parent in tree.iter_elements():
        children = parent.element_children()
        if len(children) < 3:
            continue
        groups: dict[tuple, list[html_rule.Node]] = {}
        for child in children:
            groups.setdefault(_group_signature(child), []).append(child)
        for signature, nodes in groups.items():
            if len(nodes) < 3:
                continue
            texts = [node.text() for node in nodes]
            avg_len = sum(len(t) for t in texts) / len(texts)
            if avg_len < 8:
                continue
            links = sum(1 for node in nodes if any(a.tag == "a" for a in node.iter_elements()))
            images = sum(1 for node in nodes if any(i.tag == "img" for i in node.iter_elements()))
            title_hits = sum(1 for node in nodes if _field_hint(node, "title") >= 2)
            artist_hits = sum(1 for node in nodes if _field_hint(node, "artist") >= 2)
            score = len(nodes) * 4 + min(avg_len, 100) / 5 + links * 2 + images
            score += title_hits * 4 + artist_hits * 3
            if best is None or score > best["score"]:
                selector = _selector_for(nodes[0], parent)
                if len(html_rule.select(tree, selector)) < 3:
                    selector = nodes[0].tag
                best = {
                    "parent": parent,
                    "nodes": nodes,
                    "selector": selector,
                    "score": score,
                    "signature": signature,
                }
    return best


def _field_hint(node: html_rule.Node, field: str) -> int:
    marker = " ".join([node.tag, " ".join(node.classes()),
                       *(str(v) for v in node.attrs.values())]).lower()
    if field == "title":
        words = ("title", "songname", "song_name", "song-name", "trackname", "track_name",
                 "musicname", "music-name", "name", "歌曲", "歌名")
    elif field == "artist":
        words = ("artist", "singer", "author", "performer", "band", "歌手", "演唱")
    elif field == "album":
        words = ("album", "collection", "record", "专辑")
    else:
        words = ("cover", "pic", "image", "img", "thumb", "封面")
    hits = sum(1 for word in words if word in marker)
    if node.tag in {"h1", "h2", "h3"} and field == "title":
        hits += 1
    return hits


def _guess_field(item: html_rule.Node, field: str) -> tuple[str, str]:
    best_node = None
    best_score = -1
    for node in item.iter_elements(include_self=False):
        text = node.text().strip()
        if not text or len(text) > 120:
            continue
        score = _field_hint(node, field)
        if field == "cover":
            if node.tag != "img":
                continue
            score = 5 if _field_hint(node, "cover") else 2
        if score < 1:
            continue
        if field == "artist" and _field_hint(node, "title") > 0 and _field_hint(node, "artist") == 0:
            continue
        if best_node is None or score > best_score:
            best_node, best_score = node, score
    if best_node is None:
        return "", ""
    text = html_rule._attr(best_node, "src") if field == "cover" else best_node.text().strip()
    if field == "cover" and not text:
        text = html_rule._attr(best_node, "data-src")
    return _selector_for(best_node, item), text


def _guess_detail(item: html_rule.Node, root: str) -> tuple[str, str, str]:
    anchors = [node for node in item.iter_elements() if node.tag == "a" and html_rule._attr(node, "href")]
    anchors.sort(key=lambda node: (
        0 if any(hint in html_rule._attr(node, "href").lower() for hint in _DETAIL_HINTS) else 1,
        len(html_rule._attr(node, "href")),
    ))
    for anchor in anchors:
        href = html_rule._attr(anchor, "href")
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        absolute = html_rule._abs(root, href)
        selector = _selector_for(anchor, item)
        if selector:
            return selector, absolute, href
    return "", "", ""


def _detail_template(absolute: str, href: str) -> str:
    if not absolute:
        return ""
    parsed = urlparse(absolute)
    path = parsed.path
    # replace the last numeric or UUID-looking segment with {id}
    match = re.search(r"/([0-9]+|[0-9a-fA-F-]{8,})(?=/|$)", path)
    if match:
        return absolute[:match.start(1)] + "{id}" + absolute[match.end(1):]
    return ""


def _valid_audio_url(value: str) -> bool:
    value = (value or "").strip()
    if not value.startswith(("http://", "https://")):
        return False
    path = urlparse(value).path.lower()
    return path.endswith(AUDIO_EXTENSIONS) or any(part in path for part in ("audio", "stream", "media"))


def _audio_candidate(text: str, base: str) -> dict | None:
    checks = [
        ("auto", {"audio_mode": "auto", "audio_selector": "audio", "audio_attr": "src"}),
        ("auto", {"audio_mode": "auto", "audio_selector": "audio#audio-element", "audio_attr": "src"}),
        ("auto", {"audio_mode": "auto", "audio_selector": "source", "audio_attr": "src"}),
        ("base64_var", None),
        ("json_ld", {"audio_mode": "json_ld"}),
    ]
    for mode, rule in checks[:3]:
        try:
            found = html_rule._audio_from_text(text, rule)
        except Exception:  # noqa: BLE001
            found = ""
        found = html_rule._abs(base, found)
        if _valid_audio_url(found):
            return {"audio_mode": "auto", "audio_selector": rule["audio_selector"],
                    "audio_attr": "src", "audio_var": "", "audio_url": found}
    # Base64 variables such as const encodedUrl = "..."
    for match in re.finditer(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(['\"])(.*?)\2", text, re.S):
        variable, payload = match.group(1), match.group(3)
        if len(payload) < 16 or len(payload) > 20000:
            continue
        decoded = html_rule._decode_base64(payload)
        decoded = html_rule._abs(base, decoded)
        if _valid_audio_url(decoded):
            return {"audio_mode": "base64_var", "audio_selector": "", "audio_attr": "src",
                    "audio_var": variable, "audio_url": decoded}
    try:
        found = html_rule._audio_from_text(text, {"audio_mode": "json_ld"})
    except Exception:  # noqa: BLE001
        found = ""
    found = html_rule._abs(base, found)
    if _valid_audio_url(found):
        return {"audio_mode": "json_ld", "audio_selector": "", "audio_attr": "src",
                "audio_var": "", "audio_url": found}
    try:
        found = html_rule._audio_from_text(text, {"audio_mode": "raw"})
    except Exception:  # noqa: BLE001
        found = ""
    found = html_rule._abs(base, found)
    if _valid_audio_url(found):
        return {"audio_mode": "raw", "audio_selector": "", "audio_attr": "src",
                "audio_var": "", "audio_url": found}
    return None


def _site_name(home: str, root: str) -> str:
    match = _TITLE_RE.search(home)
    if match:
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip()
        title = re.split(r"\s*[-–—|_]\s*", title)[0].strip()
        if 1 < len(title) <= 30:
            return title
    host = urlparse(root).netloc.replace("www.", "")
    return host.split(".")[0].title() or "网页音源"


def _detail_candidates(root: str, detail_paths: list[str]) -> list[str]:
    result: list[str] = []
    for path in detail_paths:
        if not path:
            continue
        if any(hint in path.lower() for hint in _DETAIL_HINTS):
            if path.endswith("/"):
                value = path + "{id}/"
            else:
                value = path + "/{id}"
            value = urljoin(root, value)
            if value not in result:
                result.append(value)
    for path in ("/song/{id}/", "/music/{id}/", "/detail/{id}/",
                 "/track/{id}/", "/play/{id}/"):
        value = urljoin(root, path)
        if value not in result:
            result.append(value)
    return result


def _build_rule(group: dict, search_url: str, root: str,
                detail_templates: list[str] | None = None) -> tuple[dict, str]:
    first_item = group["nodes"][0]
    title_sel, title_text = _guess_field(first_item, "title")
    artist_sel, artist_text = _guess_field(first_item, "artist")
    album_sel, _ = _guess_field(first_item, "album")
    cover_sel, _ = _guess_field(first_item, "cover")
    page_sel, detail_abs, detail_href = _guess_detail(first_item, root)
    id_attr = ""
    for attr in _ID_ATTRS:
        if html_rule._attr(first_item, attr):
            id_attr = attr
            break
    detail_template = "" if page_sel else _detail_template(detail_abs, detail_href)
    if not detail_template and not page_sel and id_attr:
        for candidate in (detail_templates or []):
            if "{id}" in candidate:
                detail_template = candidate
                break
    if not detail_abs and detail_template and id_attr and "{id}" in detail_template:
        detail_abs = detail_template.replace("{id}", html_rule._attr(first_item, id_attr))
    rule = {
        "list_selector": group["selector"],
        "id_attr": id_attr,
        "title_selector": title_sel,
        "artist_selector": artist_sel,
        "album_selector": album_sel,
        "cover_selector": cover_sel,
        "detail_url": detail_template,
        "page_selector": page_sel,
        "page_attr": "href",
        "max_pages": "2" if "{page}" in search_url else "1",
        "audio_mode": "",
        "audio_selector": "",
        "audio_attr": "src",
        "audio_var": "",
    }
    return rule, detail_abs


def analyze_site(url: str, proxy: str = "", timeout: float = 8) -> dict:
    """Analyze a site and return a ready-to-save html_rule source.

    The result always contains ``ok`` and ``reason``.  ``site`` is present for
    partial successes too, so the manual editor can be prefilled.
    """
    session = _session(proxy)
    started = time.monotonic()
    try:
        root = _normalize_root(url)
        home, final_root = _fetch(session, root, timeout)
        root = _normalize_root(final_root)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"打开网站失败：{exc}", "site": None}

    candidates = _search_candidates(root, home)
    _, detail_paths = _js_candidates(root, home)
    detail_templates = _detail_candidates(root, detail_paths)
    if not candidates:
        return {"ok": False, "reason": "没有识别到搜索入口，请手动配置", "site": None}

    last_reason = "没有找到可用的搜索结果列表"
    for candidate in candidates[:10]:
        if time.monotonic() - started > 40:
            last_reason = "分析超时，请改用手动配置"
            break
        template = candidate["template"]
        for query in ("晴天", "love"):
            if time.monotonic() - started > 40:
                last_reason = "分析超时，请改用手动配置"
                break
            search_url = _fill(template, query, 1)
            try:
                search_text, search_final = _fetch(session, search_url, timeout)
            except Exception as exc:  # noqa: BLE001
                last_reason = f"搜索地址打不开：{exc}"
                continue
            tree = html_rule.parse_html(search_text)
            group = _best_group(tree)
            if not group:
                last_reason = "搜索页没有识别到重复结果列表"
                continue
            rule, detail_abs = _build_rule(group, template, root, detail_templates)
            if not rule["title_selector"]:
                last_reason = "识别到列表，但没有找到标题字段"
                continue
            if not detail_abs:
                # Try the first anchor or a detail template.
                last_reason = "识别到列表，但没有找到详情链接"
                continue
            audio = _audio_candidate(search_text, search_final)
            if not audio and detail_abs:
                try:
                    detail_text, detail_final = _fetch(session, detail_abs, timeout)
                    audio = _audio_candidate(detail_text, detail_final)
                except Exception as exc:  # noqa: BLE001
                    last_reason = f"详情页打不开：{exc}"
            if not audio:
                last_reason = "列表和详情页里没有识别到可播放音频"
                continue

            rule["audio_mode"] = audio["audio_mode"]
            rule["audio_selector"] = audio["audio_selector"]
            rule["audio_attr"] = audio["audio_attr"]
            rule["audio_var"] = audio["audio_var"]
            name = _site_name(home, root)
            host = urlparse(root).netloc.replace("www.", "")
            site = {
                "name": name,
                "kind": "html_rule",
                "tag": "网页规则源 · 自动解析",
                "desc": f"网页规则源 · {host}（自动识别，可在规则里调整）",
                "base_url": root,
                "search_url": template,
                "mapping": {
                    "query_param": "q",
                    "_stream_only": 1,
                    "rule": rule,
                },
                "headers": {},
                "stream_only": 1,
                "enabled": 1,
                "is_builtin": 0,
            }
            return {
                "ok": True,
                "site": site,
                "reason": "",
                "summary": (
                    f"已识别搜索入口、列表 {rule['list_selector']}、"
                    f"音频模式 {audio['audio_mode']}"
                ),
                "audio_url": audio["audio_url"],
                "search_url": search_url,
            }
    return {"ok": False, "reason": last_reason, "site": None}
