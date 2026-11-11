"""Configurable HTML music source engine.

The engine is deliberately dependency-free: it builds a small DOM with the
standard ``html.parser`` and supports the subset of CSS selectors needed by
ordinary server-rendered music sites (tag / class / id / attribute / descendant
/ child).  A source carries its rules in ``site["mapping"]["rule"]`` so the
existing SQLite mapping column can store it without a schema migration.
"""

from __future__ import annotations

import base64
import binascii
import html as html_lib
import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, urljoin

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wav", ".opus")

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_SELECTOR_TOKEN = re.compile(r"[^\s>]+|>")
_SIMPLE_TOKEN = re.compile(
    r"^(?P<tag>\*|[A-Za-z][A-Za-z0-9_-]*)?"
    r"(?P<rest>(?:\.[A-Za-z0-9_-]+|\#[A-Za-z0-9_-]+|\[[^\]]+\])*)"
)
_ATTR_TOKEN = re.compile(r"\[([^\]]+)\]")
_TEXT_WS = re.compile(r"\s+")


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict | None = None, parent=None) -> None:
        self.tag = (tag or "").lower()
        self.attrs = {str(k).lower(): ("" if v is None else str(v)) for k, v in (attrs or {}).items()}
        self.children: list[Node | str] = []
        self.parent = parent

    def element_children(self) -> list["Node"]:
        return [child for child in self.children if isinstance(child, Node)]

    def iter_elements(self, include_self: bool = True):
        if include_self:
            yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter_elements(True)

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                parts.append(child.text())
        return _TEXT_WS.sub(" ", html_lib.unescape(" ".join(parts))).strip()

    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        node = Node(tag, dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = (tag or "").lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    parser = _TreeBuilder()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - malformed pages should still yield a partial tree
        pass
    return parser.root


def _parse_simple(token: str):
    match = _SIMPLE_TOKEN.match(token or "")
    if not match:
        return None
    tag = match.group("tag")
    rest = match.group("rest") or ""
    classes = []
    node_id = ""
    attrs: list[tuple[str, str, str]] = []
    index = 0
    while index < len(rest):
        char = rest[index]
        if char == ".":
            end = index + 1
            while end < len(rest) and rest[end] not in ".#[":
                end += 1
            classes.append(rest[index + 1:end])
            index = end
        elif char == "#":
            end = index + 1
            while end < len(rest) and rest[end] not in ".#[":
                end += 1
            node_id = rest[index + 1:end]
            index = end
        elif char == "[":
            end = rest.find("]", index + 1)
            if end < 0:
                break
            body = rest[index + 1:end]
            if "=" in body:
                key, value = body.split("=", 1)
                value = value.strip().strip("\"'")
                attrs.append((key.strip().lower(), "=", value))
            elif body.strip():
                attrs.append((body.strip().lower(), "", ""))
            index = end + 1
        else:
            index += 1
    return tag.lower() if tag and tag != "*" else "", classes, node_id, attrs


def _matches(node: Node, token: str) -> bool:
    parsed = _parse_simple(token)
    if not parsed:
        return False
    tag, classes, node_id, attr_rules = parsed
    if tag and node.tag != tag:
        return False
    if classes and not all(item in node.classes() for item in classes):
        return False
    if node_id and node.attrs.get("id", "") != node_id:
        return False
    for key, op, value in attr_rules:
        if key not in node.attrs:
            return False
        if op == "=" and html_lib.unescape(node.attrs.get(key, "")) != html_lib.unescape(value):
            return False
    return True


def select(root: Node, selector: str) -> list[Node]:
    tokens = _SELECTOR_TOKEN.findall((selector or "").strip())
    if not tokens:
        return []
    current = [root]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == ">":
            index += 1
            if index >= len(tokens):
                break
            token = tokens[index]
            current = [
                child
                for parent in current
                for child in parent.element_children()
                if _matches(child, token)
            ]
        else:
            current = [
                node
                for parent in current
                for node in parent.iter_elements(True)
                if node is not parent and _matches(node, token)
            ]
        index += 1
    return current


def first(root: Node, selector: str) -> Node | None:
    rows = select(root, selector)
    return rows[0] if rows else None


def _text(node: Node | None) -> str:
    return node.text() if node else ""


def _attr(node: Node | None, name: str) -> str:
    if not node:
        return ""
    return html_lib.unescape(node.attrs.get((name or "").lower(), "")).strip()


def _clean_url(value: str) -> str:
    value = html_lib.unescape(str(value or "")).strip().replace("\\/", "/")
    if value.startswith("//"):
        return "https:" + value
    return value


def _abs(base: str, value: str) -> str:
    value = _clean_url(value)
    if not value or value.startswith(("data:", "javascript:", "#")):
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return urljoin(base or "", value)


def _format(template: str, values: dict, quote_values: bool = True) -> str:
    def repl(match: re.Match) -> str:
        key = match.group(1)
        value = values.get(key, "")
        if value is None:
            value = ""
        return quote(str(value), safe="") if quote_values else str(value)

    return re.sub(r"\{([A-Za-z0-9_.]+)\}", repl, template or "")


def _decode_base64(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("data:"):
        return raw
    padded = raw + "=" * (-len(raw) % 4)
    try:
        return base64.b64decode(padded, validate=False).decode("utf-8", "replace").strip()
    except (binascii.Error, ValueError, UnicodeError):
        try:
            urlsafe = padded.replace("-", "+").replace("_", "/")
            return base64.b64decode(urlsafe, validate=False).decode("utf-8", "replace").strip()
        except (binascii.Error, ValueError, UnicodeError):
            return ""


def _audio_from_text(text: str, rule: dict) -> str:
    mode = str(rule.get("audio_mode") or "auto").strip().lower()
    if mode in ("base64", "base64_var", "base64var"):
        var = str(rule.get("audio_var") or "encodedUrl").strip() or "encodedUrl"
        pattern = re.compile(
            r"(?:var|let|const)\s+" + re.escape(var) + r"\s*=\s*(['\"])(.*?)\1",
            re.S,
        )
        match = pattern.search(text)
        if not match:
            pattern = re.compile(
                re.escape(var) + r"\s*[:=]\s*(['\"])(.*?)\1",
                re.S,
            )
            match = pattern.search(text)
        if match:
            return _decode_base64(match.group(2))
        return ""

    if mode in ("json_ld", "jsonld", "json-ld"):
        for body in re.findall(r"<script[^>]+type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>", text, re.S | re.I):
            try:
                data = json.loads(body)
            except (ValueError, TypeError):
                continue
            found = _json_audio(data)
            if found:
                return found
        return ""

    if mode in ("regex", "pattern"):
        pattern = str(rule.get("audio_regex") or r"https?://[^\s\"'<>]+")
        match = re.search(pattern, text, re.S)
        if match:
            return _clean_url(match.group(1) if match.groups() else match.group(0))
        return ""

    if mode in ("raw", "mp3"):
        return _raw_audio(text)

    # auto / audio_tag / selector
    selector = str(rule.get("audio_selector") or "").strip()
    attr_name = str(rule.get("audio_attr") or "src").strip() or "src"
    if selector:
        node = first(parse_html(text), selector)
        if node:
            value = _attr(node, attr_name)
            if value:
                return value
            source = first(node, "source")
            if source:
                return _attr(source, "src")
    # <audio src="..."> and <source src="...">
    for match in re.finditer(r"<audio[^>]*>", text, re.I | re.S):
        tag = match.group(0)
        found = re.search(r"\bsrc\s*=\s*(['\"])(.*?)\1", tag, re.I | re.S)
        if found:
            return _clean_url(found.group(2))
    for match in re.finditer(r"<source[^>]*>", text, re.I | re.S):
        tag = match.group(0)
        found = re.search(r"\bsrc\s*=\s*(['\"])(.*?)\1", tag, re.I | re.S)
        if found:
            return _clean_url(found.group(2))
    return _raw_audio(text)


def _json_audio(data) -> str:
    if isinstance(data, dict):
        for key in ("contentUrl", "contentURL", "audio", "audioUrl", "url"):
            value = data.get(key)
            if isinstance(value, str) and any(ext in value.lower() for ext in AUDIO_EXTENSIONS):
                return _clean_url(value)
        for value in data.values():
            found = _json_audio(value)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _json_audio(value)
            if found:
                return found
    return ""


def _raw_audio(text: str) -> str:
    pattern = r"https?://[^\s\"'<>]+?(?:%s)(?:\?[^\s\"'<>]*)?" % "|".join(
        re.escape(ext) for ext in AUDIO_EXTENSIONS
    )
    match = re.search(pattern, text or "", re.I)
    if match:
        return _clean_url(match.group(0))
    match = re.search(r"//[^\s\"'<>]+?(?:%s)(?:\?[^\s\"'<>]*)?" % "|".join(
        re.escape(ext) for ext in AUDIO_EXTENSIONS
    ), text or "", re.I)
    return "https:" + match.group(0) if match else ""


def _rule_value(rule: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        value = rule.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _headers(site: dict) -> dict:
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    }
    headers.update({str(k): str(v) for k, v in (site.get("headers") or {}).items()})
    return headers


def _search_urls(site: dict, rule: dict, query: str, page: int) -> list[str]:
    template = (site.get("search_url") or "").strip()
    if not template:
        return []
    values = {"query": query, "q": query, "keyword": query, "page": page, "p": page}
    url = _format(template, values)
    if "{query}" not in template and "{q}" not in template and "{keyword}" not in template:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}q={quote(query)}"
    return [url]


def _search_page(session, site: dict, rule: dict, query: str, page: int, timeout: int):
    urls = _search_urls(site, rule, query, page)
    if not urls:
        raise RuntimeError("网页规则源没有搜索地址")
    last_error = "搜索页没有返回内容"
    for url in urls:
        try:
            resp = session.get(url, headers=_headers(site), timeout=timeout)
            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code}"
                continue
            return resp.text, resp.url or url
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)[:120]
    raise RuntimeError(last_error)


def _detail_url(site: dict, rule: dict, values: dict) -> str:
    template = _rule_value(rule, "detail_url", "detail_template")
    if not template:
        return ""
    base = site.get("base_url") or site.get("search_url") or ""
    return _abs(base, _format(template, values))


def _item_rows(tree: Node, rule: dict, base_url: str, site: dict, limit: int) -> list[dict]:
    list_selector = _rule_value(rule, "list_selector", "item_selector", "list", default="")
    if not list_selector:
        return []
    items = select(tree, list_selector)[: max(limit, 1)]
    id_attr = _rule_value(rule, "id_attr", "native_id_attr", default="data-rid")
    title_selector = _rule_value(rule, "title_selector", "title", default="")
    artist_selector = _rule_value(rule, "artist_selector", "artist", default="")
    album_selector = _rule_value(rule, "album_selector", "album", default="")
    cover_selector = _rule_value(rule, "cover_selector", "cover", default="")
    cover_attr = _rule_value(rule, "cover_attr", default="src")
    page_selector = _rule_value(rule, "page_selector", "link_selector", default="")
    page_attr = _rule_value(rule, "page_attr", default="href")
    rows: list[dict] = []
    for item in items:
        native_id = _attr(item, id_attr) if id_attr else ""
        if not native_id and id_attr and id_attr.startswith("."):
            native_id = _text(first(item, id_attr))
        if not native_id:
            id_node = first(item, _rule_value(rule, "id_selector", default="[data-rid]"))
            native_id = _attr(id_node, id_attr) if id_node else ""
        title = _text(first(item, title_selector)) if title_selector else ""
        artist = _text(first(item, artist_selector)) if artist_selector else ""
        album = _text(first(item, album_selector)) if album_selector else ""
        cover = ""
        if cover_selector:
            cover_node = first(item, cover_selector)
            cover = _attr(cover_node, cover_attr) if cover_node else ""
        page = ""
        if page_selector:
            page_node = first(item, page_selector)
            page = _attr(page_node, page_attr) if page_node else ""
        if not page:
            page = _detail_url(site, rule, {
                "id": native_id,
                "title": title,
                "artist": artist,
                "page": base_url,
            })
        rows.append({
            "id": native_id,
            "title": title,
            "artist": artist,
            "album": album,
            "cover": _abs(base_url, cover),
            "page": _abs(base_url, page) if page else "",
        })
    return rows


def _fill_audio(session, site: dict, rule: dict, row: dict, timeout: int) -> str:
    # A search page may already contain the audio URL.
    search_audio = _audio_from_text(row.get("search_html", ""), rule)
    if search_audio:
        return _abs(row.get("base_url", ""), search_audio)
    detail_url = row.get("page") or ""
    if not detail_url:
        return ""
    try:
        resp = session.get(detail_url, headers=_headers(site), timeout=timeout)
        if resp.status_code >= 400:
            return ""
        audio = _audio_from_text(resp.text, rule)
        return _abs(resp.url or detail_url, audio)
    except Exception:  # noqa: BLE001
        return ""


def search_html_rule(site: dict, query: str, limit: int = 25, session=None, timeout: int = 12) -> list[dict]:
    """Search a rule-configured HTML source and return normalized row dicts."""
    if session is None:
        raise RuntimeError("网页规则源需要 requests session")
    mapping = site.get("mapping") or {}
    rule = mapping.get("rule") or site.get("rule") or {}
    if not isinstance(rule, dict) or not rule:
        raise RuntimeError("网页规则源没有配置 rule")
    try:
        max_pages = max(1, int(rule.get("max_pages") or 1))
    except (TypeError, ValueError):
        max_pages = 1
    try:
        page_start = max(1, int(rule.get("page_start") or 1))
    except (TypeError, ValueError):
        page_start = 1
    rows: list[dict] = []
    last_error = "网页规则没有找到歌曲"
    for page in range(page_start, page_start + max(max_pages, 1)):
        try:
            text, page_url = _search_page(session, site, rule, query, page, timeout)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)[:120]
            break
        tree = parse_html(text)
        page_rows = _item_rows(tree, rule, page_url, site, limit - len(rows))
        if not page_rows:
            last_error = "搜索页里没有匹配到列表项"
            break
        for row in page_rows:
            if len(rows) >= limit:
                break
            row["search_html"] = text
            row["base_url"] = page_url
            audio = _fill_audio(session, site, rule, row, timeout)
            if not audio:
                continue
            row.pop("search_html", None)
            row.pop("base_url", None)
            row["audio"] = audio
            rows.append(row)
        if len(rows) >= limit:
            break
    if not rows:
        raise RuntimeError(last_error)
    return rows[:limit]
