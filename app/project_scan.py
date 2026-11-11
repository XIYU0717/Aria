"""Recognize what an open-source project can do for Aria.

The wizard deliberately keeps the result conservative:

* ``sidecar``  - repository exposes ``aria-tool.json`` or a real server start script
* ``remote``  - repository/website exposes enough evidence for an HTTP music API
* ``both``     - both of the above
* ``unknown``  - no usable evidence

Only read-only HTTP requests are made here.  No repository code is executed.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import urllib.parse
import urllib.request
from pathlib import PurePosixPath

import aggregator_tools

UA = "Aria/1.0"
_TIMEOUT = 15

_BAD_SIDECAR_DIRS = {
    "ui", "web", "www", "frontend", "client", "docs", "doc", "site", "admin",
    "example", "examples", "demo", "test", "tests", "e2e", "playground",
}
_BAD_START = (
    "vite", "next dev", "react-scripts", "ng serve", "nuxt dev", "astro dev",
    "webpack serve", "parcel", "storybook", "electron .",
)
_SOURCE_SUFFIXES = (
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".kt", ".java",
    ".cs", ".php", ".rb", ".yml", ".yaml", ".toml",
)
_SOURCE_HINTS = (
    "api", "server", "backend", "client", "config", "url", "music", "search",
    "stream", "subsonic", "navidrome", "neko", "provider", "service",
)


class ScanError(RuntimeError):
    pass


def _fetch_text(url: str) -> str:
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA}),
                timeout=_TIMEOUT) as response:
            return response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        raise ScanError(f"读取失败：{exc}") from exc


def _fetch_json(url: str) -> dict:
    body = _fetch_text(url)
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ScanError("接口返回非 JSON") from exc
    if not isinstance(data, dict):
        raise ScanError("接口返回格式不正确")
    return data


def normalize_project_url(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        raise ScanError("请输入项目网址")
    if value.startswith("git+"):
        value = value[4:]
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        value = "https://github.com/" + value
    if not re.match(r"^https?://", value, re.I):
        raise ScanError("请输入以 http:// 或 https:// 开头的网址")
    return value.rstrip("/")


def _github_parts(url: str) -> tuple[str, str] | None:
    match = re.search(r"github\.com[:/]+([^/]+)/([^/#?]+)", url, re.I)
    if not match:
        return None
    return match.group(1), re.sub(r"\.git$", "", match.group(2), flags=re.I)


def _read_repo_file(owner: str, repo: str, branch: str, path: str) -> str:
    try:
        return aggregator_tools._raw(owner, repo, path, branch)
    except Exception:  # noqa: BLE001
        return ""


def _pick_readme(paths: list[str]) -> str:
    for name in ("README.md", "readme.md", "README.rst", "README.txt", "Readme.md"):
        if name in paths:
            return name
    for path in paths:
        if PurePosixPath(path).name.lower().startswith("readme"):
            return path
    return ""


def _valid_sidecar(option: dict) -> bool:
    if not isinstance(option, dict):
        return False
    if option.get("kind") == "manifest":
        return True
    path = str(option.get("path") or "").replace("\\", "/").lstrip("./")
    parts = [p.lower() for p in PurePosixPath(path).parts[:-1]]
    if any(part in _BAD_SIDECAR_DIRS for part in parts):
        return False
    start = str(option.get("start") or "").strip().lower()
    if not start:
        return False
    return not any(start == bad or start.startswith(bad + " ") for bad in _BAD_START)


def _source_paths(paths: list[str]) -> list[str]:
    picked: list[str] = []
    for path in paths:
        low = path.lower()
        if "node_modules/" in low or "/vendor/" in low or "/test" in low or "/docs/" in low:
            continue
        if not low.endswith(_SOURCE_SUFFIXES):
            continue
        name = PurePosixPath(path).name.lower()
        if not any(hint in name or hint in low for hint in _SOURCE_HINTS):
            continue
        picked.append(path)
        if len(picked) >= 10:
            break
    return picked


def _neko_remote() -> dict:
    base = "https://music.nekocore.cn"
    return {
        "family": "nekomusic",
        "name": "NekoMusic",
        "base_url": base,
        "requires_base": False,
        "requires_credentials": False,
        "search_url": base + "/api/music/search",
        "method": "POST",
        "body": {"query": "{query}"},
        "results_path": "results",
        "title": "title",
        "artist": "artist",
        "album": "album",
        "duration": "duration",
        "native_id": "id",
        "audio_from": base + "/api/music/file/{id}?quality=hq",
        "audio_path": "data.url",
        "headers": {"Accept": "application/json", "User-Agent": UA},
        "evidence": [
            "发现 NekoMusic 公共 API：POST /api/music/search",
            "发现两段式取流：GET /api/music/file/{id} → data.url",
            "服务地址：https://music.nekocore.cn",
        ],
    }


def _subsonic_remote(base_url: str = "") -> dict:
    return {
        "family": "subsonic",
        "name": "Subsonic / Navidrome",
        "base_url": base_url,
        "requires_base": not bool(base_url),
        "requires_credentials": True,
        "search_url": (base_url.rstrip("/") + "/rest/search3") if base_url else "",
        "method": "GET",
        "results_path": "subsonic-response.searchResult3.match",
        "title": "title",
        "artist": "artist",
        "album": "album",
        "duration": "duration",
        "native_id": "id",
        "audio_from": "",
        "audio_path": "",
        "headers": {"Accept": "application/json", "User-Agent": UA},
        "evidence": [
            "发现 Subsonic / Navidrome 兼容接口",
            "搜索：/rest/search3；取流：/rest/stream?id=…",
            "需要填写已部署的服务地址与账号",
        ],
    }


def _generic_remote(base_url: str, evidence: list[str]) -> dict:
    return {
        "family": "generic",
        "name": urllib.parse.urlparse(base_url).netloc or "远程服务",
        "base_url": base_url.rstrip("/"),
        "requires_base": False,
        "requires_credentials": False,
        "search_url": "",
        "method": "GET",
        "results_path": "",
        "title": "",
        "artist": "",
        "album": "",
        "duration": "",
        "native_id": "",
        "audio_from": "",
        "audio_path": "url",
        "headers": {"Accept": "application/json", "User-Agent": UA},
        "evidence": evidence,
    }


def _openapi_remote(base_url: str, doc: dict) -> dict | None:
    servers = doc.get("servers") or []
    server_url = ""
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        server_url = str(servers[0].get("url") or "")
    root = urllib.parse.urljoin(base_url.rstrip("/") + "/", server_url) if server_url else base_url
    root = root.rstrip("/")
    paths = doc.get("paths") or {}
    search_path = ""
    method = "GET"
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        low = str(path).lower()
        if "search" not in low and "query" not in low:
            continue
        for verb in ("get", "post"):
            if isinstance(item.get(verb), dict):
                search_path, method = str(path), verb.upper()
                break
        if search_path:
            break
    if not search_path:
        return None
    return {
        "family": "openapi",
        "name": urllib.parse.urlparse(root).netloc or "OpenAPI",
        "base_url": root,
        "requires_base": False,
        "requires_credentials": False,
        "search_url": root + search_path,
        "method": method,
        "body": {"query": "{query}"} if method == "POST" else {},
        "results_path": "",
        "title": "",
        "artist": "",
        "album": "",
        "duration": "",
        "native_id": "",
        "audio_from": "",
        "audio_path": "url",
        "headers": {"Accept": "application/json", "User-Agent": UA},
        "evidence": [f"发现 OpenAPI 文档，搜索接口：{method} {search_path}"],
    }


def _detect_remote(full_name: str, text: str, input_url: str,
                   paths: list[str], base_url: str = "", branch: str = "") -> dict | None:
    lower = text.lower()
    repo_lower = full_name.lower()
    if "nekomusic" in repo_lower or "nekocore.cn" in lower or (
            "/api/music/search" in lower and "/api/music/file" in lower):
        return _neko_remote()

    if "subsonic" in lower or "navidrome" in repo_lower or "/rest/stream" in lower:
        # A GitHub repository describes a self-hosted server; its homepage is
        # not an API endpoint.  Never prefill the project website as base_url.
        return _subsonic_remote("" if "github.com" in input_url.lower() else base_url)

    openapi_path = ""
    for path in paths:
        name = PurePosixPath(path).name.lower()
        if name in ("openapi.json", "openapi.yaml", "swagger.json", "swagger.yaml"):
            openapi_path = path
            break
    if openapi_path and openapi_path.lower().endswith(".json"):
        try:
            owner_repo = _github_parts(input_url)
            if owner_repo:
                doc = json.loads(_read_repo_file(
                    owner_repo[0], owner_repo[1],
                    branch, openapi_path))
                if isinstance(doc, dict):
                    spec = _openapi_remote(base_url or input_url, doc)
                    if spec:
                        return spec
        except Exception:  # noqa: BLE001
            pass

    if all(token in lower for token in ("/health", "/search", "/stream")):
        root = base_url.rstrip("/")
        return {
            "family": "aria",
            "name": urllib.parse.urlparse(root).netloc or "Aria HTTP",
            "base_url": root,
            "requires_base": not bool(root),
            "requires_credentials": False,
            "search_url": (root + "/search") if root else "",
            "method": "GET",
            "results_path": "",
            "title": "",
            "artist": "",
            "album": "",
            "duration": "",
            "native_id": "",
            "audio_from": (root + "/stream?id={id}") if root else "",
            "audio_path": "url",
            "headers": {"Accept": "application/json", "User-Agent": UA},
            "evidence": ["发现 /health、/search、/stream，符合 Aria HTTP 音源协议"],
        }
    return None


def _analyze_github(url: str) -> dict:
    owner, repo = _github_parts(url) or ("", "")
    info = aggregator_tools.discover(url)
    branch = str(info.get("branch") or "main")
    paths = list(info.get("paths") or [])
    full_name = str(info.get("full_name") or f"{owner}/{repo}")
    readme_path = _pick_readme(paths)
    readme = _read_repo_file(owner, repo, branch, readme_path) if readme_path else ""
    source_text = readme
    for path in _source_paths(paths):
        source_text += "\n" + _read_repo_file(owner, repo, branch, path)
    options = [o for o in (info.get("options") or []) if _valid_sidecar(o)]

    homepage = str(info.get("homepage") or "")
    remote = _detect_remote(full_name, source_text, url, paths, homepage, branch)
    evidence: list[str] = []
    if readme_path:
        evidence.append(f"读取 {readme_path}")
    for option in options:
        path = str(option.get("path") or "")
        if option.get("kind") == "manifest":
            evidence.append(f"发现侧车清单：{path}")
        else:
            evidence.append(f"发现服务端启动脚本：{path} · {option.get('start') or ''}")
    if remote:
        evidence.extend(remote.get("evidence") or [])
    if not evidence:
        evidence.append("没有发现 aria-tool.json、服务端启动脚本或可用的远程 API 证据")

    caps = []
    if options:
        caps.append("sidecar")
    if remote:
        caps.append("remote")
    return {
        "url": url,
        "kind": "github",
        "full_name": full_name,
        "name": full_name.split("/")[-1],
        "description": str(info.get("description") or ""),
        "license": str(info.get("license") or ""),
        "branch": branch,
        "options": options,
        "remote": remote,
        "capabilities": caps,
        "evidence": evidence,
    }


def _website_remote(url: str) -> dict | None:
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc:
        return None
    # Neko's public endpoint has a stable host.
    if "nekocore.cn" in parsed.netloc.lower():
        return _neko_remote()
    for suffix in ("/openapi.json", "/swagger.json", "/v3/api-docs"):
        try:
            doc = _fetch_json(url.rstrip("/") + suffix)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(doc, dict) and (doc.get("openapi") or doc.get("swagger")):
            spec = _openapi_remote(url, doc)
            if spec:
                return spec
    return None


def _analyze_website(url: str) -> dict:
    page = ""
    try:
        page = _fetch_text(url)
    except Exception:  # noqa: BLE001
        pass
    github_link = ""
    match = re.search(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", page)
    if match:
        github_link = match.group(0)
    if github_link:
        result = _analyze_github(github_link)
        result["url"] = url
        result["project_url"] = github_link
        result["evidence"] = ["从项目官网找到 GitHub 仓库：" + github_link] + list(result.get("evidence") or [])
        return result

    remote = _website_remote(url)
    if remote:
        return {
            "url": url,
            "kind": "site",
            "full_name": urllib.parse.urlparse(url).netloc,
            "name": urllib.parse.urlparse(url).netloc,
            "description": "",
            "license": "",
            "branch": "",
            "options": [],
            "remote": remote,
            "capabilities": ["remote"],
            "evidence": list(remote.get("evidence") or []),
        }
    return {
        "url": url,
        "kind": "site",
        "full_name": urllib.parse.urlparse(url).netloc,
        "name": urllib.parse.urlparse(url).netloc,
        "description": "",
        "license": "",
        "branch": "",
        "options": [],
        "remote": None,
        "capabilities": [],
        "evidence": ["没有发现 GitHub 仓库、OpenAPI 文档或可用的远程音乐接口"],
    }


def analyze(url: str) -> dict:
    """Read-only capability scan.  Never executes repository code."""
    clean = normalize_project_url(url)
    if _github_parts(clean):
        return _analyze_github(clean)
    return _analyze_website(clean)
