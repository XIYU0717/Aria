"""Install aggregator tools from a GitHub repository.

A tool is a small local HTTP service that speaks Aria's provider protocol:
``/recommend /charts /categories /playlist /chart /search /stream`` plus
``/health`` and an optional ``X-Aria-Token`` header.  This module resolves a
GitHub URL, lists installable entries (``aria-tool.json`` or package.json start
script), downloads the repository, starts it on a free loopback port and writes
the resulting provider entry into ``%APPDATA%\\Aria\\providers.json``.

Only, user-selected repositories are downloaded and executed; nothing is
fetched automatically from a bare GitHub link.
"""

from __future__ import annotations

import atexit
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Aria"
PROVIDERS_FILE = DATA_DIR / "providers.json"
TOOLS_DIR = DATA_DIR / "tools"
LOG_DIR = TOOLS_DIR / "logs"
UA = "Aria/1.0"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_PROCESSES: dict[str, subprocess.Popen] = {}
_LOCK = threading.RLock()
# Sidecars are lazy: no Node process is kept alive just because the app is open.
# Stop a tool after 30 minutes without a request, or after two hours of runtime.
_IDLE_STOP_SECONDS = 30 * 60
_MAX_UPTIME_SECONDS = 2 * 60 * 60
_WATCHDOG_INTERVAL = 30.0
_STARTED_AT: dict[str, float] = {}
_LAST_USED: dict[str, float] = {}
_ACTIVE: dict[str, int] = {}
_START_LOCKS: dict[str, threading.Lock] = {}
_WATCHDOG_STARTED = False
_WATCHDOG_LOCK = threading.Lock()


class ToolError(RuntimeError):
    pass


# --------------------------------------------------------------------------- GitHub
def parse_repo(url_or_slug: str) -> tuple[str, str]:
    text = str(url_or_slug or "").strip()
    text = re.sub(r"^git\+", "", text, flags=re.I)
    text = re.sub(r"\.git$", "", text, flags=re.I)
    text = text.rstrip("/")
    match = re.search(r"github\.com[:/]+([^/]+)/([^/#?]+)", text, re.I)
    if match:
        return match.group(1), match.group(2)
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text)
    if match:
        return match.group(1), match.group(2)
    raise ToolError("请输入 GitHub 仓库链接，例如 https://github.com/owner/repo")


def _api(url: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=15) as response:
            body = response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"GitHub 请求失败：{exc}") from exc
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ToolError("GitHub 返回非 JSON") from exc
    if not isinstance(data, dict):
        raise ToolError("GitHub 返回格式不正确")
    return data


def _raw(owner: str, repo: str, path: str, branch: str) -> str:
    url = (f"https://raw.githubusercontent.com/{owner}/{repo}/"
           f"{urllib.parse.quote(branch, safe='/')}/"
           f"{urllib.parse.quote(path, safe='/')}")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15) as response:
            return response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"读取 {path} 失败：{exc}") from exc


def _command(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v)]
    if isinstance(value, str) and value.strip():
        return shlex.split(value, posix=False)
    return []


def discover(url: str) -> dict:
    """Return repository info and the installable start options it exposes."""
    owner, repo = parse_repo(url)
    info = _api(f"https://api.github.com/repos/{owner}/{repo}")
    branch = str(info.get("default_branch") or "main")
    tree: list[dict] = []
    try:
        tree_data = _api(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/"
            f"{urllib.parse.quote(branch)}?recursive=1")
        tree = list(tree_data.get("tree") or [])
    except ToolError:
        tree = []

    options: list[dict] = []
    manifest_paths = [n["path"] for n in tree
                      if str(n.get("path") or "").lower().endswith("aria-tool.json")][:8]
    if not manifest_paths:
        # The GitHub tree may fail on huge repos; also check the root directly.
        try:
            _raw(owner, repo, "aria-tool.json", branch)
            manifest_paths = ["aria-tool.json"]
        except ToolError:
            manifest_paths = []

    for path in manifest_paths:
        try:
            manifest = json.loads(_raw(owner, repo, path, branch))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(manifest, dict):
            continue
        name = str(manifest.get("name") or repo)
        options.append({
            "kind": "manifest",
            "name": name,
            "description": str(manifest.get("description") or info.get("description") or ""),
            "path": path,
            "accent": str(manifest.get("accent") or "#6E8BFF"),
            "manifest": manifest,
        })

    if not options:
        package_paths = [n["path"] for n in tree
                         if str(n.get("path") or "").lower().endswith("package.json")][:6]
        if not package_paths:
            package_paths = ["package.json"]
        for path in package_paths:
            try:
                package = json.loads(_raw(owner, repo, path, branch))
            except Exception:  # noqa: BLE001
                continue
            scripts = package.get("scripts") or {}
            start = scripts.get("start") or scripts.get("serve") or ""
            if not start:
                continue
            options.append({
                "kind": "node",
                "name": str(package.get("name") or repo),
                "description": str(package.get("description") or info.get("description") or ""),
                "path": path,
                "accent": "#6E8BFF",
                "start": start,
                "main": str(package.get("main") or ""),
            })

    return {
        "owner": owner,
        "repo": repo,
        "full_name": info.get("full_name") or f"{owner}/{repo}",
        "description": info.get("description") or "",
        "branch": branch,
        "options": options,
        "paths": [str(n.get("path") or "") for n in tree if n.get("path")],
        "homepage": str(info.get("homepage") or ""),
        "license": str((info.get("license") or {}).get("spdx_id") or ""),
    }


# --------------------------------------------------------------------------- install
def _safe_extract(archive: bytes, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for member in zf.infolist():
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                continue
            dest = (root / name).resolve()
            if root not in dest.parents and dest != root:
                continue
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    children = [p for p in root.iterdir() if p.is_dir()]
    files = [p for p in root.iterdir() if p.is_file()]
    if len(children) == 1 and not files:
        return children[0]
    return root


def _download_zip(owner: str, repo: str, branch: str) -> bytes:
    url = (f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/"
           f"{urllib.parse.quote(branch)}")
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as response:
            return response.read()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"下载仓库失败：{exc}") from exc


def _slug(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower()).strip("-._")
    return text or fallback


def _free_port(start: int = 19000, end: int = 19999) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise ToolError("没有可用的本地端口")


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def _health(base_url: str, token: str, timeout: float = 2.5) -> tuple[bool, int, str]:
    started = time.perf_counter()
    headers = {"Accept": "application/json", "User-Agent": UA}
    if token:
        headers["X-Aria-Token"] = token
    try:
        with urllib.request.urlopen(
                urllib.request.Request(f"{base_url}/health", headers=headers),
                timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            latency = int((time.perf_counter() - started) * 1000)
            if response.status != 200:
                return False, latency, f"HTTP {response.status}"
            try:
                data = json.loads(body)
            except ValueError:
                return False, latency, "健康检查返回非 JSON"
            if not isinstance(data, dict) or not (data.get("ok") or data.get("status")):
                return False, latency, str(data.get("error") or "健康检查未通过")
            return True, latency, ""
    except Exception as exc:  # noqa: BLE001
        return False, int((time.perf_counter() - started) * 1000), str(exc)[:120]


def _log_tail(path: Path, limit: int = 320) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    return text[-limit:].strip()


def _node_exe() -> str:
    here = Path(sys.executable).resolve().parent
    for candidate in (here / "runtime" / "node.exe", here.parent / "runtime" / "node.exe"):
        if candidate.exists():
            return str(candidate)
    found = shutil.which("node")
    if found:
        return found
    raise ToolError("没有找到 Node 运行时，无法启动该工具")


def _program(token: str) -> str:
    low = token.strip().lower()
    if low in ("node", "node.exe"):
        return _node_exe()
    if low in ("python", "python.exe", "python3"):
        if getattr(sys, "frozen", False):
            raise ToolError("该工具需要 Python 运行时，Aria 安装版未内置 Python")
        return sys.executable
    if low in ("npm", "npm.cmd"):
        found = shutil.which("npm.cmd") or shutil.which("npm")
        if found:
            return found
        raise ToolError("该工具依赖 npm，但当前环境没有 npm")
    return token


def _start_command(option: dict, entry_dir: Path, node_exe: str) -> list[str]:
    manifest = dict(option.get("manifest") or {})
    command = _command(manifest.get("start") or manifest.get("command"))
    if not command:
        runtime = str(manifest.get("runtime") or "node").lower()
        entry = str(manifest.get("entry") or "")
        if not entry:
            for name in ("server.js", "index.js", "app.js", "main.js"):
                if (entry_dir / name).exists():
                    entry = name
                    break
        if not entry:
            raise ToolError("没有找到启动入口，请在该仓库提供 aria-tool.json")
        command = ([_program(runtime)] if runtime in ("node", "python") else [runtime]) + [entry]
        command.extend(_command(manifest.get("args")))
    if option.get("kind") == "node" and option.get("start"):
        raw = _command(str(option["start"]))
        if raw and raw[0].lower() in ("node", "node.exe"):
            command = [node_exe] + raw[1:]
        elif raw and raw[0].lower().startswith("node "):
            command = [node_exe] + raw[0].split(" ", 1)[1:] + raw[1:]
        elif raw and raw[0].lower() in ("npm", "npm.cmd"):
            command = [_program(raw[0])] + raw[1:]
        else:
            command = raw
        if len(command) == 1 and option.get("main"):
            command.append(str(option["main"]))
    if not command:
        raise ToolError("工具没有提供启动命令")
    command[0] = _program(command[0])
    return command


def _read_entries() -> list[dict]:
    if not PROVIDERS_FILE.exists():
        return []
    try:
        data = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if isinstance(data, dict):
        data = data.get("providers") or []
    return [dict(item) for item in data if isinstance(item, dict)]


def _write_entries(entries: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = PROVIDERS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({"providers": entries}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    temp.replace(PROVIDERS_FILE)
    try:
        import providers
        providers.REGISTRY.load_custom()
    except Exception:  # noqa: BLE001
        pass


def _find_entry(tool_id: str) -> tuple[int, dict] | tuple[None, None]:
    entries = _read_entries()
    for index, entry in enumerate(entries):
        if str(entry.get("id") or "") == tool_id:
            return index, entry
    return None, None


def _stop(tool_id: str) -> None:
    with _LOCK:
        process = _PROCESSES.pop(tool_id, None)
        _STARTED_AT.pop(tool_id, None)
        _LAST_USED.pop(tool_id, None)
        _ACTIVE.pop(tool_id, None)
    if not process:
        return
    try:
        process.terminate()
        process.wait(timeout=4)
    except Exception:  # noqa: BLE001
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass


def _stop_under(path: Path) -> None:
    target = str(path.resolve())
    for entry in _read_entries():
        cwd = str(entry.get("cwd") or "")
        try:
            inside = str(Path(cwd).resolve()).startswith(target)
        except Exception:  # noqa: BLE001
            inside = False
        if inside:
            _stop(str(entry.get("id") or ""))


def _spawn(entry: dict) -> subprocess.Popen:
    tool_id = str(entry.get("id") or "")
    cwd = Path(str(entry.get("cwd") or TOOLS_DIR))
    port = int(entry.get("port") or _free_port())
    token = str(entry.get("token") or "")
    command = [str(part).format(port=port, token=token, cwd=str(cwd))
               for part in entry.get("command") or []]
    if not command:
        raise ToolError("工具缺少启动命令")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{_slug(tool_id, 'tool')}.log"
    env = dict(os.environ)
    for key, value in (entry.get("env") or {}).items():
        env[str(key)] = str(value).format(port=port, token=token)
    env["ARIA_TOKEN"] = token
    env["ARIA_PORT"] = str(port)
    env["PORT"] = str(port)
    env["ARIA_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        log = open(log_path, "ab", buffering=0)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"无法写入工具日志：{exc}") from exc
    try:
        process = subprocess.Popen(
            command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
    finally:
        log.close()
    with _LOCK:
        _PROCESSES[tool_id] = process
        _STARTED_AT[tool_id] = time.time()
        _LAST_USED[tool_id] = time.time()
        _ACTIVE[tool_id] = 0
    _ensure_watchdog()
    return process


def _wait_ready(entries: list[dict], seconds: float = 18.0) -> None:
    deadline = time.time() + seconds
    pending = [e for e in entries if not e.get("disabled")]
    while pending and time.time() < deadline:
        pending = [e for e in pending
                   if not _health(str(e.get("base_url") or ""),
                                  str(e.get("token") or ""), timeout=1.2)[0]]
        if pending:
            time.sleep(0.35)


def _start_lock(tool_id: str) -> threading.Lock:
    with _LOCK:
        return _START_LOCKS.setdefault(tool_id, threading.Lock())


def _ensure_watchdog() -> None:
    global _WATCHDOG_STARTED
    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    def loop() -> None:
        while True:
            time.sleep(_WATCHDOG_INTERVAL)
            try:
                with _LOCK:
                    ids = list(_PROCESSES)
                    started = dict(_STARTED_AT)
                    used = dict(_LAST_USED)
                now = time.time()
                for tool_id in ids:
                    with _LOCK:
                        if _ACTIVE.get(tool_id):
                            continue
                        last = max(used.get(tool_id, 0.0), started.get(tool_id, 0.0))
                        idle = now - last if last else 0.0
                        uptime = now - started.get(tool_id, now)
                        if idle >= _IDLE_STOP_SECONDS or uptime >= _MAX_UPTIME_SECONDS:
                            _stop(tool_id)
            except Exception:  # noqa: BLE001 - a watchdog must never die
                continue

    threading.Thread(target=loop, name="aria-tool-watchdog", daemon=True).start()


def ensure_started(tool_id: str, seconds: float = 10.0) -> bool:
    """Start a tool on demand; returns False when it is disabled/missing."""
    _index, entry = _find_entry(tool_id)
    if not entry or entry.get("disabled") or not entry.get("launch"):
        return False
    with _LOCK:
        process = _PROCESSES.get(tool_id)
    if process is not None and process.poll() is None:
        with _LOCK:
            _LAST_USED[tool_id] = time.time()
            _ACTIVE.setdefault(tool_id, 0)
        _ensure_watchdog()
        return True

    lock = _start_lock(tool_id)
    with lock:
        # Another request may have started it while this one waited.
        with _LOCK:
            process = _PROCESSES.get(tool_id)
        if process is not None and process.poll() is None:
            with _LOCK:
                _LAST_USED[tool_id] = time.time()
            return True
        base_url = str(entry.get("base_url") or "")
        token = str(entry.get("token") or "")
        if base_url and _health(base_url, token, timeout=1.5)[0]:
            # Adopt a healthy but untracked process (for example after a UI
            # crash) so the watchdog can still own its lifetime.
            with _LOCK:
                _STARTED_AT[tool_id] = time.time()
                _LAST_USED[tool_id] = time.time()
                _ACTIVE[tool_id] = 0
            _ensure_watchdog()
            return True
        entries = _read_entries()
        index, current = _find_entry(tool_id)
        if index is None:
            return False
        entry = current
        port = int(entry.get("port") or 0)
        if not port or _port_open(port):
            port = _free_port()
            entry["port"] = port
            entry["base_url"] = f"http://127.0.0.1:{port}"
            entries[index] = entry
            _write_entries(entries)
        _stop(tool_id)
        try:
            _spawn(entry)
        except Exception:  # noqa: BLE001
            return False
        deadline = time.time() + max(3.0, seconds)
        while time.time() < deadline:
            if _health(str(entry.get("base_url") or ""),
                       str(entry.get("token") or ""), timeout=1.0)[0]:
                return True
            time.sleep(0.3)
        _stop(tool_id)
        return False


@contextmanager
def request_context(tool_id: str):
    """Keep a sidecar alive for the duration of one provider HTTP request.

    Providers that are plain user-entered HTTP endpoints have no tool entry;
    they pass straight through.  Only installed GitHub tools are lazily
    started, watched, and eventually stopped.
    """
    _index, entry = _find_entry(tool_id)
    if not entry or not entry.get("launch"):
        yield
        return
    if entry.get("disabled"):
        raise ToolError(f"开源项目 {tool_id} 已停用")
    if not ensure_started(tool_id):
        raise ToolError(f"开源项目 {tool_id} 暂时不可用")
    with _LOCK:
        _LAST_USED[tool_id] = time.time()
        _ACTIVE[tool_id] = _ACTIVE.get(tool_id, 0) + 1
    try:
        yield
    finally:
        with _LOCK:
            _ACTIVE[tool_id] = max(0, _ACTIVE.get(tool_id, 0) - 1)
            _LAST_USED[tool_id] = time.time()


def start_all(budget: float = 3.0) -> list[str]:
    """Adopt running tools without spawning idle Node processes.

    Startup used to launch every installed sidecar, which added a couple of
    seconds before the first frame.  Tools are now started on first use by
    ``ensure_started``.  ``budget`` caps the health probes so a dead remote
    service cannot keep the startup path busy indefinitely.
    """
    entries = _read_entries()
    ready: list[str] = []
    deadline = time.time() + max(0.0, budget)
    for entry in entries:
        if time.time() >= deadline:
            break
        if entry.get("disabled") or not entry.get("launch"):
            continue
        base_url = str(entry.get("base_url") or "")
        token = str(entry.get("token") or "")
        remaining = max(0.2, deadline - time.time())
        if base_url and _health(base_url, token, timeout=min(1.0, remaining))[0]:
            tool_id = str(entry.get("id") or "")
            if tool_id:
                with _LOCK:
                    _STARTED_AT.setdefault(tool_id, time.time())
                    _LAST_USED[tool_id] = time.time()
                ready.append(tool_id)
    if ready:
        _ensure_watchdog()
    return ready


def list_tools() -> list[dict]:
    return [e for e in _read_entries() if e.get("launch")]


def install(url: str, option: dict) -> dict:
    """Download, start and register one selected tool option."""
    owner, repo = parse_repo(url)
    info = discover(url)
    branch = str(info["branch"])
    archive = _download_zip(owner, repo, branch)
    repo_dir = TOOLS_DIR / _slug(f"{owner}-{repo}", repo)
    if repo_dir.exists():
        _stop_under(repo_dir)
        shutil.rmtree(repo_dir, ignore_errors=True)
    root = _safe_extract(archive, repo_dir)

    option = dict(option or {})
    if option.get("kind") == "manifest":
        manifest_path = root / str(option.get("path") or "aria-tool.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry_dir = manifest_path.parent
    else:
        package_dir = (root / str(option.get("path") or "package.json")).parent
        entry_dir = package_dir if package_dir.exists() else root
        manifest = {}

    tool_id = _slug(str(manifest.get("id") or option.get("name") or repo), repo)
    if tool_id in {"listen1", "buguyy"}:
        tool_id = _slug(f"{tool_id}-{repo}", repo)
    name = str(manifest.get("name") or option.get("name") or repo)
    port = int(manifest.get("port") or 0)
    if port and _port_open(port):
        port = _free_port()
    if not port:
        port = _free_port()
    token = os.urandom(16).hex()
    node_exe = _node_exe()
    command = _start_command(option, entry_dir, node_exe)
    command = [part.replace(str(entry_dir), "{cwd}") for part in command]

    entry = {
        "id": tool_id,
        "name": name,
        "short": str(manifest.get("short") or name)[:10],
        "accent": str(manifest.get("accent") or option.get("accent") or "#6E8BFF"),
        "base_url": f"http://127.0.0.1:{port}",
        "port": port,
        "token": token,
        "discover": bool(manifest.get("discover", True)),
        "timeout": int(manifest.get("timeout") or 12),
        "cwd": str(entry_dir),
        "install_dir": str(repo_dir),
        "command": command,
        "env": {str(k): str(v) for k, v in (manifest.get("env") or {}).items()},
        "launch": True,
        "source": "github",
        "repo": f"{owner}/{repo}",
        "branch": branch,
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    entries = _read_entries()
    entries = [e for e in entries if str(e.get("id") or "") != tool_id]
    entries.append(entry)
    _write_entries(entries)
    _stop(tool_id)
    process = _spawn(entry)
    if not _wait_one(entry, seconds=20):
        _stop(tool_id)
        _write_entries([e for e in _read_entries()
                        if str(e.get("id") or "") != tool_id])
        raise ToolError("工具已下载，但没有在端口上响应健康检查："
                        + (_log_tail(LOG_DIR / f"{_slug(tool_id, 'tool')}.log") or "无日志"))
    return entry


def _wait_one(entry: dict, seconds: float = 20.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _health(str(entry.get("base_url") or ""), str(entry.get("token") or ""))[0]:
            return True
        time.sleep(0.4)
    return False


def test_tool(tool_id: str) -> tuple[bool, int, str]:
    _index, entry = _find_entry(tool_id)
    if not entry:
        return False, 0, "工具不存在"
    if entry.get("disabled"):
        return False, 0, "已停用"
    if not ensure_started(tool_id):
        return False, 0, "工具没有响应健康检查"
    return _health(str(entry.get("base_url") or ""), str(entry.get("token") or ""))


def set_enabled(tool_id: str, enabled: bool) -> None:
    entries = _read_entries()
    for entry in entries:
        if str(entry.get("id") or "") != tool_id:
            continue
        entry["disabled"] = not bool(enabled)
        if not enabled:
            _stop(tool_id)
        else:
            if not _health(str(entry.get("base_url") or ""),
                           str(entry.get("token") or ""), timeout=1.5)[0]:
                _stop(tool_id)
                _spawn(entry)
        _write_entries(entries)
        return
    raise ToolError("工具不存在")


def remove_tool(tool_id: str) -> None:
    _stop(tool_id)
    entries = _read_entries()
    kept = [e for e in entries if str(e.get("id") or "") != tool_id]
    _write_entries(kept)
    for entry in entries:
        if str(entry.get("id") or "") != tool_id:
            continue
        cwd = Path(str(entry.get("install_dir") or entry.get("cwd") or ""))
        try:
            root = TOOLS_DIR.resolve()
            target = cwd.resolve()
            if root in target.parents:
                shutil.rmtree(target, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


def open_folder(tool_id: str) -> str:
    _index, entry = _find_entry(tool_id)
    if not entry:
        raise ToolError("工具不存在")
    return str(entry.get("cwd") or TOOLS_DIR)


def stop_all() -> None:
    with _LOCK:
        ids = list(_PROCESSES)
    for tool_id in ids:
        _stop(tool_id)


atexit.register(stop_all)
