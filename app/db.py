"""SQLite persistence for sites, queue, history and settings."""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from catalog import BUILTIN

APP_NAME = "Aria"
DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
DB_PATH = DATA_DIR / "aria.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    tag TEXT DEFAULT '',
    desc TEXT DEFAULT '',
    accent TEXT DEFAULT '#6E8BFF',
    base_url TEXT DEFAULT '',
    search_url TEXT DEFAULT '',
    mapping TEXT DEFAULT '{}',
    headers TEXT DEFAULT '{}',
    api_key TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    is_builtin INTEGER DEFAULT 0,
    health TEXT DEFAULT 'unknown',
    latency INTEGER DEFAULT 0,
    last_check TEXT DEFAULT '',
    sort INTEGER DEFAULT 0,
    added_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track TEXT NOT NULL,
    added_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track TEXT NOT NULL,
    played_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    k TEXT PRIMARY KEY,
    v TEXT
);
CREATE TABLE IF NOT EXISTS favorites (
    song_key TEXT PRIMARY KEY,
    track TEXT NOT NULL,
    created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS playlists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cover TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id TEXT NOT NULL,
    song_key TEXT DEFAULT '',
    track TEXT NOT NULL,
    position INTEGER DEFAULT 0,
    added_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_playlist_tracks ON playlist_tracks(playlist_id, position);
"""

DEFAULT_SETTINGS = {
    "page_size": "25",
    "volume": "65",
    "proxy": "",
    "show_all_sources": "1",
    "enabled_on_boot": "listen1",
    "removed_builtins": "[]",
    "play_mode": "list_loop",
    "cache_dir": "",
    "cache_limit": "200",
}


class DB:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Distinguish a genuinely new profile before opening the database.
        # Existing profiles must never be re-seeded or have their sources changed.
        self._fresh_profile = not DB_PATH.exists()
        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        self._seed()

    def _migrate(self) -> None:
        """Add columns introduced after the first release."""
        columns = {row["name"] for row in self.query("PRAGMA table_info(playlists)")}
        if "platform" not in columns:
            self.conn.execute("ALTER TABLE playlists ADD COLUMN platform TEXT DEFAULT ''")

    # ---------------------------------------------------------------- seed
    def _seed(self) -> None:
        # Remember that this database was created as a fresh install, so later
        # launches keep the Listen1-only seed instead of treating it as an old
        # profile and re-adding Buguyy.
        if self._fresh_profile and self.get_setting("fresh_install", None) is None:
            self.set_setting("fresh_install", "1")
        listen1_only = self._fresh_profile or self.get_setting("fresh_install", "0") == "1"
        for key, value in DEFAULT_SETTINGS.items():
            if self.get_setting(key, None) is None:
                self.set_setting(key, value)
        removed = set(self.removed_builtins())
        existing = {row["id"] for row in self.query("SELECT id FROM sites")}
        for idx, item in enumerate(BUILTIN):
            # A brand-new profile starts with Listen1 only.  Buguyy stays in
            # BUILTIN so existing profiles keep it and “恢复内置来源” still works,
            # but a fresh install must not insert it.
            if listen1_only and item["id"] != "listen1":
                continue
            if item["id"] in existing or item["id"] in removed:
                continue
            self.conn.execute(
                """INSERT INTO sites (id, name, kind, tag, desc, accent, base_url, search_url,
                       mapping, headers, api_key, enabled, is_builtin, health, latency,
                       last_check, sort, added_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["id"], item["name"], item["kind"], item.get("tag", ""),
                    item.get("desc", ""), item.get("accent", "#6E8BFF"),
                    item.get("base_url", ""), item.get("search_url", ""),
                    json.dumps(item.get("mapping") or {}, ensure_ascii=False),
                    json.dumps(item.get("headers") or {}, ensure_ascii=False), "", item.get("enabled", 1), 1,
                    "unknown", 0, "", idx, time.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
        self.conn.commit()
        self._cleanup_superseded_builtins()

    def _cleanup_superseded_builtins(self) -> None:
        """Drop built-in rows from older releases.

        The built-in catalog still contains Buguyy and Listen1, while a
        fresh profile seeds only Listen1.  Rows created by earlier versions
        (iTunes / Audius / Deezer / ...) are removed on upgrade.  Existing
        Buguyy / Listen1 rows are kept so old profiles keep their sources.
        User-created custom sources are never touched.
        """
        builtin_ids = {item["id"] for item in BUILTIN}
        placeholders = ",".join("?" for _ in builtin_ids) or "''"
        stale = [row["id"] for row in self.query(
            f"SELECT id FROM sites WHERE is_builtin=1 AND id NOT IN ({placeholders})",
            tuple(builtin_ids))] if builtin_ids else []
        for site_id in stale:
            self.conn.execute("DELETE FROM sites WHERE id=?", (site_id,))
        removed = [sid for sid in self.removed_builtins() if sid in builtin_ids]
        self.set_setting("removed_builtins", json.dumps(sorted(set(removed))))

    # ---------------------------------------------------------------- basic
    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, tuple(params)))

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return cur

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str, default: str | None = "") -> str | None:
        rows = self.query("SELECT v FROM settings WHERE k=?", (key,))
        return rows[0]["v"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute("INSERT INTO settings (k, v) VALUES (?,?) "
                     "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))

    def get_int(self, key: str, default: int) -> int:
        try:
            return int(self.get_setting(key, str(default)) or default)
        except (TypeError, ValueError):
            return default

    # ---------------------------------------------------------------- sites
    def sites(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM sites"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY sort, name"
        out = []
        for row in self.query(sql):
            item = dict(row)
            try:
                item["mapping"] = json.loads(item.get("mapping") or "{}")
            except json.JSONDecodeError:
                item["mapping"] = {}
            item["stream_only"] = bool(item["mapping"].get("_stream_only"))
            try:
                item["headers"] = json.loads(item.get("headers") or "{}")
            except json.JSONDecodeError:
                item["headers"] = {}
            out.append(item)
        return out

    def get_site(self, site_id: str) -> dict | None:
        for item in self.sites():
            if item["id"] == site_id:
                return item
        return None

    def upsert_site(self, data: dict) -> None:
        sid = data["id"]
        mapping = dict(data.get("mapping") or {})
        if "stream_only" in data:
            mapping["_stream_only"] = 1 if data.get("stream_only") else 0
        if self.get_site(sid):
            self.execute(
                """UPDATE sites SET name=?, kind=?, tag=?, desc=?, accent=?, base_url=?,
                   search_url=?, mapping=?, headers=?, api_key=?, enabled=? WHERE id=?""",
                (data.get("name", sid), data.get("kind", "generic"), data.get("tag", ""),
                 data.get("desc", ""), data.get("accent", "#6E8BFF"), data.get("base_url", ""),
                 data.get("search_url", ""), json.dumps(mapping, ensure_ascii=False),
                 json.dumps(data.get("headers", {}), ensure_ascii=False), data.get("api_key", ""),
                 int(data.get("enabled", 1)), sid),
            )
        else:
            self.execute(
                """INSERT INTO sites (id, name, kind, tag, desc, accent, base_url, search_url,
                   mapping, headers, api_key, enabled, is_builtin, health, latency, last_check, sort, added_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, data.get("name", sid), data.get("kind", "generic"), data.get("tag", ""),
                 data.get("desc", ""), data.get("accent", "#6E8BFF"), data.get("base_url", ""),
                 data.get("search_url", ""), json.dumps(mapping, ensure_ascii=False),
                 json.dumps(data.get("headers", {}), ensure_ascii=False), data.get("api_key", ""),
                 int(data.get("enabled", 1)), int(data.get("is_builtin", 0)),
                 "unknown", 0, "", 999, time.strftime("%Y-%m-%d %H:%M:%S")),
            )

    def delete_site(self, site_id: str) -> None:
        builtin_ids = {item["id"] for item in BUILTIN}
        if site_id in builtin_ids:
            removed = set(self.removed_builtins())
            removed.add(site_id)
            self.set_setting("removed_builtins", json.dumps(sorted(removed)))
        self.execute("DELETE FROM sites WHERE id=?", (site_id,))

    def removed_builtins(self) -> list[str]:
        raw = self.get_setting("removed_builtins", "[]") or "[]"
        try:
            items = json.loads(raw)
            return [str(x) for x in items] if isinstance(items, list) else []
        except json.JSONDecodeError:
            return []

    def restore_builtins(self) -> list[str]:
        """Bring back built-in sources the user removed; returns the ids re-added."""
        self.set_setting("removed_builtins", "[]")
        existing = {row["id"] for row in self.query("SELECT id FROM sites")}
        added = []
        for idx, item in enumerate(BUILTIN):
            if item["id"] in existing:
                continue
            self.conn.execute(
                """INSERT INTO sites (id, name, kind, tag, desc, accent, base_url, search_url,
                       mapping, headers, api_key, enabled, is_builtin, health, latency,
                       last_check, sort, added_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["id"], item["name"], item["kind"], item.get("tag", ""),
                    item.get("desc", ""), item.get("accent", "#6E8BFF"),
                    item.get("base_url", ""), item.get("search_url", ""),
                    json.dumps(item.get("mapping") or {}, ensure_ascii=False),
                    json.dumps(item.get("headers") or {}, ensure_ascii=False), "",
                    item.get("enabled", 1), 1, "unknown", 0, "", idx,
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            added.append(item["id"])
        self.conn.commit()
        return added

    def set_enabled(self, site_id: str, enabled: bool) -> None:
        self.execute("UPDATE sites SET enabled=? WHERE id=?", (1 if enabled else 0, site_id))

    def set_health(self, site_id: str, health: str, latency: int) -> None:
        self.execute(
            "UPDATE sites SET health=?, latency=?, last_check=? WHERE id=?",
            (health, int(latency), time.strftime("%Y-%m-%d %H:%M:%S"), site_id),
        )

    # ---------------------------------------------------------------- queue
    def queue(self) -> list[dict]:
        out = []
        for row in self.query("SELECT id, track FROM queue ORDER BY id"):
            try:
                out.append({"queue_id": row["id"], **json.loads(row["track"])})
            except json.JSONDecodeError:
                continue
        return out

    def queue_add(self, track: dict) -> int:
        return self.execute("INSERT INTO queue (track, added_at) VALUES (?,?)",
                            (json.dumps(track, ensure_ascii=False), time.strftime("%Y-%m-%d %H:%M:%S"))).lastrowid

    def queue_remove(self, queue_id: int) -> None:
        self.execute("DELETE FROM queue WHERE id=?", (queue_id,))

    def queue_clear(self) -> None:
        self.execute("DELETE FROM queue")

    def queue_set(self, tracks: list[dict]) -> list[dict]:
        """Persist a queue in exactly the given order and return it with fresh ids."""
        self.queue_clear()
        out = []
        for track in tracks:
            clean = {k: v for k, v in dict(track).items()
                     if k not in ("queue_id", "_collection")}
            qid = self.queue_add(clean)
            out.append({**clean, "queue_id": qid})
        return out

    # ---------------------------------------------------------------- history
    def add_history(self, track: dict) -> None:
        self.execute("INSERT INTO history (track, played_at) VALUES (?,?)",
                     (json.dumps(track, ensure_ascii=False), time.strftime("%Y-%m-%d %H:%M:%S")))
        self.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 200)")

    def history(self, limit: int = 12) -> list[dict]:
        out = []
        for row in self.query("SELECT track FROM history ORDER BY id DESC LIMIT ?", (limit,)):
            try:
                out.append(json.loads(row["track"]))
            except json.JSONDecodeError:
                continue
        return out

    def clear_history(self) -> None:
        self.execute("DELETE FROM history")

    # ---------------------------------------------------------------- favorites
    def favorite_keys(self) -> set[str]:
        return {row["song_key"] for row in self.query("SELECT song_key FROM favorites")}

    def favorites(self) -> list[dict]:
        out = []
        for row in self.query("SELECT track, created_at FROM favorites ORDER BY created_at DESC, song_key"):
            try:
                track = json.loads(row["track"])
            except json.JSONDecodeError:
                continue
            track.setdefault("_favorite_at", row["created_at"])
            out.append(track)
        return out

    def is_favorite(self, key: str) -> bool:
        if not key:
            return False
        return bool(self.query("SELECT 1 FROM favorites WHERE song_key=?", (key,)))

    def add_favorite(self, track: dict, key: str = "") -> None:
        from keys import song_key  # local import keeps db usable without app path
        key = key or song_key(track)
        if not key:
            raise ValueError("无法识别这首歌的标题")
        self.execute(
            "INSERT INTO favorites (song_key, track, created_at) VALUES (?,?,?) "
            "ON CONFLICT(song_key) DO UPDATE SET track=excluded.track, created_at=excluded.created_at",
            (key, json.dumps(track, ensure_ascii=False), time.strftime("%Y-%m-%d %H:%M:%S")),
        )

    def remove_favorite(self, key: str) -> None:
        self.execute("DELETE FROM favorites WHERE song_key=?", (key,))

    def toggle_favorite(self, track: dict, key: str = "") -> bool:
        """Return True when the song is now favourited."""
        from keys import song_key
        key = key or song_key(track)
        if self.is_favorite(key):
            self.remove_favorite(key)
            return False
        self.add_favorite(track, key)
        return True

    # ---------------------------------------------------------------- playlists
    def playlists(self) -> list[dict]:
        out = []
        rows = self.query(
            "SELECT p.*, (SELECT COUNT(*) FROM playlist_tracks t WHERE t.playlist_id=p.id) AS count "
            "FROM playlists p ORDER BY p.created_at DESC, p.id DESC")
        for row in rows:
            out.append(dict(row))
        return out

    def get_playlist(self, playlist_id: str) -> dict | None:
        for item in self.playlists():
            if item["id"] == playlist_id:
                return item
        return None

    def create_playlist(self, name: str, cover: str, source_url: str, tracks: list[dict],
                        platform: str = "") -> str:
        pid = new_id("pl")
        self.execute(
            "INSERT INTO playlists (id, name, cover, source_url, platform, created_at) VALUES (?,?,?,?,?,?)",
            (pid, (name or "未命名歌单").strip(), cover or "", source_url or "",
             platform or "", time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        for index, track in enumerate(tracks):
            from keys import song_key
            try:
                key = song_key(track)
            except Exception:  # noqa: BLE001
                key = ""
            self.conn.execute(
                "INSERT INTO playlist_tracks (playlist_id, song_key, track, position, added_at) "
                "VALUES (?,?,?,?,?)",
                (pid, key, json.dumps(track, ensure_ascii=False), index,
                 time.strftime("%Y-%m-%d %H:%M:%S")),
            )
        self.conn.commit()
        return pid

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        self.execute("UPDATE playlists SET name=? WHERE id=?", ((name or "").strip() or "未命名歌单", playlist_id))

    def delete_playlist(self, playlist_id: str) -> None:
        self.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
        self.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (playlist_id,))

    def playlist_tracks(self, playlist_id: str) -> list[dict]:
        out = []
        rows = self.query(
            "SELECT track FROM playlist_tracks WHERE playlist_id=? ORDER BY position, id", (playlist_id,))
        for row in rows:
            try:
                out.append(json.loads(row["track"]))
            except json.JSONDecodeError:
                continue
        return out

    def add_playlist_track(self, playlist_id: str, track: dict) -> bool:
        """Append one song to a playlist; returns False when it is already there."""
        from keys import song_key
        if not self.get_playlist(playlist_id):
            return False
        key = song_key(track)
        if key:
            exists = self.query(
                "SELECT 1 FROM playlist_tracks WHERE playlist_id=? AND song_key=? LIMIT 1",
                (playlist_id, key))
            if exists:
                return False
        rows = self.query(
            "SELECT COALESCE(MAX(position), -1) AS p FROM playlist_tracks WHERE playlist_id=?",
            (playlist_id,))
        position = int(rows[0]["p"]) + 1 if rows else 0
        self.execute(
            "INSERT INTO playlist_tracks (playlist_id, song_key, track, position, added_at) "
            "VALUES (?,?,?,?,?)",
            (playlist_id, key, json.dumps(track, ensure_ascii=False), position,
             time.strftime("%Y-%m-%d %H:%M:%S")))
        return True

    # ---------------------------------------------------------------- backup
    BACKUP_SETTINGS = (
        "page_size", "volume", "proxy", "show_all_sources", "removed_builtins",
        # Per-song “默认匹配” bindings, including sources added later.  This is
        # stored as one JSON string in settings, so it must travel with the
        # backup for a device migration to restore playback choices.
        "match_overrides",
        # Per-song “默认歌词” choices.  Lyrics are self-contained so a new device
        # can render them immediately without searching the network again.
        "lyric_overrides",
    )

    def export_payload(self) -> dict:
        settings = {}
        for key in self.BACKUP_SETTINGS:
            value = self.get_setting(key, None)
            if value is not None:
                settings[key] = value
        playlists = []
        for item in self.playlists():
            playlists.append({**item, "tracks": self.playlist_tracks(item["id"])})
        return {
            "app": APP_NAME,
            "format": 1,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "data": {
                "sites": self.sites(),
                "favorites": self.favorites(),
                "playlists": playlists,
                "queue": self.queue(),
                "history": self.history(limit=200),
                "settings": settings,
            },
        }

    def export_backup(self, path: str) -> dict:
        payload = self.export_payload()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return payload

    def import_backup(self, path: str, mode: str = "merge") -> dict:
        """Import a backup file. mode='replace' wipes local data first, 'merge' appends."""
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError("这不是 Aria 的备份文件")
        replace = mode == "replace"
        stats = {"sites": 0, "favorites": 0, "playlists": 0, "queue": 0,
                 "bindings": 0, "lyrics": 0}

        if replace:
            self.conn.execute("DELETE FROM sites")
            self.conn.execute("DELETE FROM favorites")
            self.conn.execute("DELETE FROM playlists")
            self.conn.execute("DELETE FROM playlist_tracks")
            self.conn.execute("DELETE FROM queue")
            self.conn.execute("DELETE FROM history")
            # A replace import mirrors the other device exactly; do not leave
            # this device's old per-song bindings behind.
            self.set_setting("match_overrides", "{}")
            self.set_setting("lyric_overrides", "{}")

        for site in data.get("sites") or []:
            if not isinstance(site, dict) or not site.get("id"):
                continue
            try:
                self.upsert_site(site)
                stats["sites"] += 1
            except Exception:  # noqa: BLE001
                continue

        from keys import song_key
        for track in data.get("favorites") or []:
            if not isinstance(track, dict):
                continue
            try:
                self.add_favorite(track, song_key(track))
                stats["favorites"] += 1
            except Exception:  # noqa: BLE001
                continue

        existing_playlists = {p["id"]: p for p in self.playlists()}
        for item in data.get("playlists") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            pid = str(item.get("id") or new_id("pl"))
            tracks = [t for t in (item.get("tracks") or []) if isinstance(t, dict)]
            if pid in existing_playlists and not replace:
                # Merge: keep the local playlist, append songs it does not have.
                current = self.playlist_tracks(pid)
                keys = {song_key(t) for t in current}
                added = [t for t in tracks if song_key(t) not in keys]
                if added:
                    self.conn.executemany(
                        "INSERT INTO playlist_tracks (playlist_id, song_key, track, position, added_at) "
                        "VALUES (?,?,?,?,?)",
                        [(pid, song_key(t), json.dumps(t, ensure_ascii=False),
                          len(current) + i, time.strftime("%Y-%m-%d %H:%M:%S"))
                         for i, t in enumerate(added)],
                    )
                    self.conn.commit()
                stats["playlists"] += 1
                continue
            if pid in existing_playlists and replace:
                self.delete_playlist(pid)
            self.conn.execute(
                "INSERT INTO playlists (id, name, cover, source_url, platform, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (pid, str(item.get("name")), item.get("cover", ""), item.get("source_url", ""),
                 item.get("platform", ""), item.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            for index, track in enumerate(tracks):
                self.conn.execute(
                    "INSERT INTO playlist_tracks (playlist_id, song_key, track, position, added_at) "
                    "VALUES (?,?,?,?,?)",
                    (pid, song_key(track), json.dumps(track, ensure_ascii=False), index,
                     time.strftime("%Y-%m-%d %H:%M:%S")),
                )
            stats["playlists"] += 1
        self.conn.commit()

        for item in data.get("queue") or []:
            if not isinstance(item, dict):
                continue
            clean = {k: v for k, v in item.items() if k != "queue_id"}
            self.queue_add(clean)
            stats["queue"] += 1
        for item in data.get("history") or []:
            if isinstance(item, dict):
                self.conn.execute(
                    "INSERT INTO history (track, played_at) VALUES (?,?)",
                    (json.dumps(item, ensure_ascii=False), time.strftime("%Y-%m-%d %H:%M:%S")))
        self.conn.commit()

        for key, value in (data.get("settings") or {}).items():
            if key in self.BACKUP_SETTINGS and value is not None:
                text = str(value)
                if (not replace and key in ("match_overrides", "lyric_overrides")):
                    # Merge imports must not drop local per-song choices.
                    try:
                        incoming = json.loads(text or "{}")
                        local = json.loads(self.get_setting(key, "{}") or "{}")
                    except (TypeError, ValueError):
                        incoming, local = {}, {}
                    if isinstance(incoming, dict) and isinstance(local, dict):
                        text = json.dumps({**local, **incoming}, ensure_ascii=False,
                                          separators=(",", ":"))
                self.set_setting(key, text)
        self.conn.commit()
        for key, stat in (("match_overrides", "bindings"),
                          ("lyric_overrides", "lyrics")):
            try:
                rows = json.loads(self.get_setting(key, "") or "{}")
                stats[stat] = len(rows) if isinstance(rows, dict) else 0
            except (TypeError, ValueError):
                stats[stat] = 0
        return stats



_NEW_ID_COUNTER = 0


def new_id(prefix: str = "site") -> str:
    global _NEW_ID_COUNTER
    _NEW_ID_COUNTER += 1
    return f"{prefix}_{int(time.time() * 1000)}_{_NEW_ID_COUNTER}"
