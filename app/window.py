"""Frameless main window: title bar, sidebar, pages, player bar, immersive overlay."""

import json
import random
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizeGrip,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

import providers
import resolver
import theme
from components import CoverCache, Toast
from db import DB
from dialogs import PlaylistImportDialog, PlaylistShareDialog
from recommend import RecommendPage
from icons import make_icon, two_state_icon
from immersive import ImmersiveView
from keys import match_key, norm_title, song_key
from library import FavoritesPage, PlaylistsPage
from lyric_dialog import LyricSearchDialog
from match_dialog import MatchDialog
from media_cache import AudioCache, format_bytes
from pages import QueuePage, SettingsPage, VERSION
from player import Player, StreamProxy
from player_bar import PlayerBar
from queue_popup import QueuePopup
from search import SearchPage
from sites import SitesPage

PLAY_MODES = {
    "list_loop": "列表循环",
    "single_loop": "单曲循环",
    "list_shuffle": "列表随机",
    "pure_random": "纯随机",
}


class _ResolveSignals(QObject):
    done = Signal(dict)
    failed = Signal(str)


class _ResolveWorker(QRunnable):
    """Fetch a fresh stream URL for sources that separate search from playback."""

    def __init__(self, track: dict, proxy: str) -> None:
        super().__init__()
        self.track = track
        self.proxy = proxy
        self.signals = _ResolveSignals()

    def run(self) -> None:
        try:
            info = resolver.resolve_native(self.track, proxy=self.proxy)
            payload = {**self.track, "audio": info.get("audio", "")}
            if info.get("lrc"):
                payload["lrc"] = info["lrc"]
            try:
                self.signals.done.emit(payload)
            except RuntimeError:
                pass
        except Exception as exc:  # noqa: BLE001
            try:
                self.signals.failed.emit(str(exc)[:120])
            except RuntimeError:
                pass


class _LyricSignals(QObject):
    done = Signal(dict)


class _LyricWorker(QRunnable):
    """Match lyrics in the background so the UI never blocks on network."""

    def __init__(self, track: dict, proxy: str) -> None:
        super().__init__()
        self.track = dict(track)
        self.proxy = proxy
        self.signals = _LyricSignals()

    def run(self) -> None:
        from lyrics import fetch_lyrics
        try:
            data = fetch_lyrics(self.track, proxy=self.proxy)
        except Exception:  # noqa: BLE001
            data = {}
        try:
            self.signals.done.emit(data or {})
        except RuntimeError:
            pass


class _MatchSignals(QObject):
    done = Signal(dict)


class _MatchWorker(QRunnable):
    """Search every enabled source and return the best playable stream."""

    def __init__(self, track: dict, sites: list[dict], proxy: str,
                 binding: bool = False) -> None:
        super().__init__()
        self.track = dict(track)
        self.sites = sites
        self.proxy = proxy
        self.binding = bool(binding)
        self.signals = _MatchSignals()

    def run(self) -> None:
        match = {}
        try:
            if self.binding:
                match = {"_alternatives": resolver.binding_candidates(
                    self.track, self.sites, proxy=self.proxy)}
            else:
                match = resolver.match(self.track, self.sites, proxy=self.proxy)
        except Exception:  # noqa: BLE001
            match = {}
        try:
            self.signals.done.emit(match or {})
        except RuntimeError:
            pass


class TitleBar(QFrame):
    searchSubmitted = Signal(str)
    minimizeClicked = Signal()
    maximizeClicked = Signal()
    closeClicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(58)
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 0, 12, 0)
        row.setSpacing(0)

        brand = QLabel("Aria")
        brand.setObjectName("Brand")
        row.addWidget(brand)
        dot = QLabel(" ●")
        dot.setStyleSheet(f"color:{theme.ACCENT};font-size:9px;")
        row.addWidget(dot)
        row.addSpacing(10)
        sub = QLabel("MUSIC AGGREGATOR")
        sub.setObjectName("BrandSub")
        row.addWidget(sub)
        row.addSpacing(36)

        self.search = QLineEdit()
        self.search.setObjectName("TopSearch")
        self.search.setPlaceholderText("搜索歌曲、歌手、专辑…   Enter 开始聚合搜索")
        self.search.setFixedWidth(430)
        self.search.setClearButtonEnabled(True)
        self.search.addAction(make_icon("search", "#8A8FA3", 16), QLineEdit.LeadingPosition)
        self.search.returnPressed.connect(lambda: self.searchSubmitted.emit(self.search.text().strip()))
        row.addWidget(self.search)
        row.addStretch(1)

        self.min_btn = self._win_button("minimize", "最小化")
        self.min_btn.clicked.connect(self.minimizeClicked.emit)
        row.addWidget(self.min_btn)
        self.max_btn = self._win_button("maximize", "最大化")
        self.max_btn.clicked.connect(self.maximizeClicked.emit)
        row.addWidget(self.max_btn)
        self.close_btn = self._win_button("close", "关闭", close=True)
        self.close_btn.clicked.connect(self.closeClicked.emit)
        row.addWidget(self.close_btn)

    def _win_button(self, kind: str, tip: str, close: bool = False) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("WinBtnClose" if close else "WinBtn")
        btn.setFixedSize(38, 30)
        btn.setIcon(make_icon(kind, "#C9CCD8", 16))
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            window = self.window()
            handle = window.windowHandle()
            if handle:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.maximizeClicked.emit()
        super().mouseDoubleClickEvent(event)


class Sidebar(QFrame):
    navigate = Signal(str)
    importPlaylistRequested = Signal()
    importBackupRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(212)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 18, 14, 16)
        col.setSpacing(6)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        items = [
            ("home", "发现", "home"),
            ("search", "聚合搜索", "search"),
            ("favorites", "我喜欢", "heart"),
            ("playlists", "我的歌单", "note"),
            ("sites", "音乐站点", "globe"),
            ("queue", "播放队列", "list"),
            ("settings", "设置", "gear"),
        ]
        for key, label, icon in items:
            btn = QPushButton("  " + label)
            btn.setObjectName("NavBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIcon(two_state_icon(icon, "#8A8FA3", "#FFFFFF", 18))
            btn.setIconSize(QSize(18, 18))
            btn.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            self.group.addButton(btn)
            self.buttons[key] = btn
            col.addWidget(btn)
        self.buttons["home"].setChecked(True)

        col.addStretch(1)

        meter = QFrame()
        meter.setObjectName("SourceMeter")
        m = QVBoxLayout(meter)
        m.setContentsMargins(12, 12, 12, 12)
        m.setSpacing(7)
        self.import_playlist_btn = QPushButton("  导入歌单")
        self.import_playlist_btn.setObjectName("SideAction")
        self.import_playlist_btn.setIcon(make_icon("note", "#E9EAF2", 15))
        self.import_playlist_btn.setCursor(Qt.PointingHandCursor)
        self.import_playlist_btn.setToolTip("粘贴网易云 / QQ音乐 / 酷狗歌单链接，生成自己的歌单")
        self.import_playlist_btn.clicked.connect(self.importPlaylistRequested.emit)
        m.addWidget(self.import_playlist_btn)
        self.import_backup_btn = QPushButton("  导入备份")
        self.import_backup_btn.setObjectName("SideAction")
        self.import_backup_btn.setIcon(make_icon("refresh", "#E9EAF2", 15))
        self.import_backup_btn.setCursor(Qt.PointingHandCursor)
        self.import_backup_btn.setToolTip("从备份文件恢复收藏、歌单与音乐源")
        self.import_backup_btn.clicked.connect(self.importBackupRequested.emit)
        m.addWidget(self.import_backup_btn)
        self.version = QLabel(f"v{VERSION}")
        self.version.setObjectName("NavHint")
        self.version.setAlignment(Qt.AlignLeft)
        m.addSpacing(6)
        m.addWidget(self.version)
        col.addWidget(meter)

    def select(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)


class MainWindow(QMainWindow):
    def __init__(self, db: DB) -> None:
        super().__init__()
        self.db = db
        self.setWindowTitle("Aria · 全网音乐聚合")
        self.setMinimumSize(1120, 760)
        self.resize(1280, 828)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)

        self.proxy = StreamProxy()
        self.cache = AudioCache(db)
        self.player = Player(self.proxy, self, cache=self.cache)
        self.player.set_proxy(db.get_setting("proxy", "") or "")
        self.covers = CoverCache(self)

        root_widget = QWidget()
        root_widget.setObjectName("Root")
        self.setCentralWidget(root_widget)
        shell = QVBoxLayout(root_widget)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.titlebar = TitleBar()
        shell.addWidget(self.titlebar)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.sidebar = Sidebar()
        content.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)
        shell.addLayout(content, 1)

        self.home = RecommendPage(self.covers)
        self.search_page = SearchPage(self.covers, db)
        self.favorites_page = FavoritesPage(self.covers)
        self.playlists_page = PlaylistsPage(self.covers)
        self.sites_page = SitesPage(db)
        self.queue_page = QueuePage(self.covers)
        self.settings_page = SettingsPage(db, cache=self.cache)
        for page in (self.home, self.search_page, self.favorites_page, self.playlists_page,
                     self.sites_page, self.queue_page, self.settings_page):
            self.stack.addWidget(page)

        self.player_bar = PlayerBar(self.covers, self.db.get_int("volume", 65))
        shell.addWidget(self.player_bar)

        self.immersive = ImmersiveView(root_widget)
        self.immersive.hide()

        self.toast = Toast(root_widget)

        # ---- state
        self.queue: list[dict] = self.db.queue()
        self.current_index = -1
        self.current_track: dict = {}
        self._resolvers: list[object] = []
        self.favorite_keys: set[str] = self.db.favorite_keys()
        self._match_token = 0
        self._matchers: list[object] = []
        # Successful resolutions are reused for the session so replaying an
        # imported song does not search every source again.
        self._match_cache: dict[str, tuple[float, dict]] = {}
        # Permanent “默认匹配该歌曲” choices, keyed by title|artist.
        self._match_overrides: dict[str, dict] = {}
        self._match_dialog: MatchDialog | None = None
        self._lyric_dialog: LyricSearchDialog | None = None
        self._lyric_overrides: dict[str, dict] = {}
        self._match_context: tuple[dict, int | None, bool] = ({}, None, False)
        self._binding_token = 0
        self._binding_target: dict = {}
        self._fallback_key = ""
        self._fallback_attempts = 0
        self._fallback_excluded: set[str] = set()
        self._fallback_excluded_sites: set[str] = set()
        self._fallback_in_progress = False
        self._fallback_exhausted = False
        self._immersive_fullscreen = False
        self._immersive_prev_max = False
        self._last_page_key = "home"
        self._really_quit = False
        self._tray_was_maximized = False
        self._tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray: QSystemTrayIcon | None = None
        self.tray_menu: QMenu | None = None
        self.play_mode = self.db.get_setting("play_mode", "list_loop") or "list_loop"
        if self.play_mode not in PLAY_MODES:
            self.play_mode = "list_loop"
        self._shuffle_history: list[int] = []
        self._shuffle_remaining: list[int] = []
        self._prefetch_armed = False
        self._prefetch_for = ""
        self._prefetched_index: int | None = None
        self._prefetched_id = ""
        self._prefetched_key = ""
        self.queue_popup: QueuePopup | None = None

        self._connect()
        self._load_match_overrides()
        self._load_lyric_overrides()
        self._load_state()

        QShortcut(QKeySequence("Ctrl+K"), self, lambda: self.titlebar.search.setFocus())
        QShortcut(QKeySequence("Ctrl+Space"), self, lambda: self.player.toggle())
        QShortcut(QKeySequence("Escape"), self, self._leave_immersive)
        QShortcut(QKeySequence("F11"), self, self._toggle_immersive_fullscreen)
        self._ui_hide_shortcut = QShortcut(QKeySequence("H"), self, self.immersive.toggle_ui_hidden)
        self._ui_hide_shortcut.setEnabled(False)
        self._setup_tray()

    # ------------------------------------------------------------------ tray
    def activate_from_second_instance(self) -> None:
        """A second launch asked us to surface the existing tray instance."""
        if getattr(self, "_really_quit", False):
            return
        if self.isMinimized():
            self.showNormal()
        if not self.isVisible():
            self._restore_from_tray()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _setup_tray(self) -> None:
        if not self._tray_available:
            return
        icon = self.windowIcon()
        if icon.isNull():
            icon = make_icon("music", "#FFFFFF", 32)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Aria")
        menu = QMenu(self)
        menu.setObjectName("TrayMenu")
        self.tray_menu = menu
        self.tray_open_action = menu.addAction("打开主页", self._restore_from_tray)
        self.tray_fullscreen_action = menu.addAction("全屏", self._open_fullscreen_lyrics)
        self.tray_prev_action = menu.addAction("上一首", self.play_prev)
        self.tray_pause_action = menu.addAction("暂停", self.player.toggle)
        self.tray_next_action = menu.addAction("下一首", self.play_next)
        menu.addSeparator()
        self.tray_quit_action = menu.addAction("退出", self._quit_app)
        menu.aboutToShow.connect(self._sync_tray_menu)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _sync_tray_menu(self) -> None:
        if not self.tray_menu:
            return
        self.tray_pause_action.setText("暂停" if self.player.is_playing() else "播放")
        self.tray_prev_action.setEnabled(bool(self.queue))
        self.tray_next_action.setEnabled(bool(self.queue))

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self) -> None:
        if self.immersive.isVisible() or not self.immersive.isHidden():
            self._leave_immersive()
        self._go(self._last_page_key)
        if self.tray:
            self.tray.show()
        if self._tray_was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _open_fullscreen_lyrics(self) -> None:
        if self.tray:
            self.tray.show()
        if not self.current_track:
            self._restore_from_tray()
            self.toast.show_message("先播放一首歌再进入歌词模式")
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._enter_immersive()
        if self.immersive.isVisible():
            self._set_immersive_fullscreen(True)

    def _quit_app(self) -> None:
        self._really_quit = True
        self.player.stop()
        if self.tray:
            self.tray.hide()
        self.close()
        app = QApplication.instance()
        if app:
            app.quit()

    # ------------------------------------------------------------------ wiring
    def _connect(self) -> None:
        self.titlebar.searchSubmitted.connect(self._on_global_search)
        self.titlebar.minimizeClicked.connect(self.showMinimized)
        self.titlebar.maximizeClicked.connect(self._toggle_max)
        self.titlebar.closeClicked.connect(self.close)

        self.sidebar.navigate.connect(self._go)
        self.sidebar.importPlaylistRequested.connect(self._open_import_playlist)
        self.sidebar.importBackupRequested.connect(self.settings_page.import_backup)

        self.home.playTrack.connect(self.play_track)
        self.home.queueTrack.connect(self._queue_menu)
        self.home.favoriteTrack.connect(self.toggle_favorite)
        self.home.playlistTrack.connect(self._add_to_playlist_menu)
        self.home.playAll.connect(self.play_collection)
        self.home.queueRequested.connect(self._show_queue_popup)
        self.home.searchRequested.connect(lambda: self._go("search"))

        self.search_page.playTrack.connect(self.play_track)
        self.search_page.playAll.connect(self.play_collection)
        self.search_page.queueTrack.connect(self._queue_menu)
        self.search_page.favoriteTrack.connect(self.toggle_favorite)
        self.search_page.playlistTrack.connect(self._add_to_playlist_menu)
        for page in (self.home, self.search_page, self.favorites_page,
                     self.playlists_page, self.queue_page):
            signal = getattr(page, "matchBindingRequested", None)
            if signal is not None:
                signal.connect(self._show_binding_menu)

        self.favorites_page.playTrack.connect(self.play_track)
        self.favorites_page.playAll.connect(self.play_collection)
        self.favorites_page.queueTrack.connect(self._queue_menu)
        self.favorites_page.favoriteTrack.connect(self.toggle_favorite)
        self.favorites_page.playlistTrack.connect(self._add_to_playlist_menu)
        self.playlists_page.playTrack.connect(self.play_track)
        self.playlists_page.queueTrack.connect(self._queue_menu)
        self.playlists_page.favoriteTrack.connect(self.toggle_favorite)
        self.playlists_page.playlistTrack.connect(self._add_to_playlist_menu)
        self.playlists_page.playAll.connect(self.play_collection)
        self.playlists_page.importRequested.connect(self._open_import_playlist)
        self.playlists_page.createRequested.connect(self._create_playlist)
        self.playlists_page.playlistSelected.connect(self._load_playlist)
        self.playlists_page.renameRequested.connect(self._rename_playlist)
        self.playlists_page.shareRequested.connect(self._share_playlist)
        self.playlists_page.deleteRequested.connect(self._delete_playlist)

        self.sites_page.changed.connect(self.refresh_sources)

        self.player_bar.toggleRequested.connect(self.player.toggle)
        self.player_bar.prevRequested.connect(self.play_prev)
        self.player_bar.nextRequested.connect(self.play_next)
        self.player_bar.seekRequested.connect(self.player.seek)
        self.player_bar.volumeChanged.connect(self._on_volume)
        self.player_bar.immersiveRequested.connect(self._enter_immersive)
        self.player_bar.queueRequested.connect(self._show_queue_popup)
        self.player_bar.modeRequested.connect(self._show_mode_menu)

        self.player.trackChanged.connect(self._on_track)
        self.player.stateChanged.connect(self._on_state)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.errorOccurred.connect(self._on_playback_error)
        self.player.finished.connect(self._on_finished)
        self.player.cacheStarted.connect(self._on_cache_started)
        self.player.cacheProgress.connect(self._on_cache_progress)
        self.player.cacheReady.connect(self._on_cache_ready)
        self.player.cacheHit.connect(self._on_cache_hit)

        self.immersive.closeRequested.connect(self._leave_immersive)
        self.immersive.toggleRequested.connect(self.player.toggle)
        self.immersive.nextRequested.connect(self.play_next)
        self.immersive.prevRequested.connect(self.play_prev)
        self.immersive.playlistRequested.connect(self._open_queue_from_immersive)
        self.immersive.modeRequested.connect(self._cycle_play_mode_from_immersive)
        self.immersive.seekRequested.connect(self.player.seek)
        self.immersive.foliaChanged.connect(self._on_lyric_folia_changed)
        self.immersive.lyricSearchRequested.connect(self._open_lyric_search)
        self.immersive.fullscreenRequested.connect(self._set_immersive_fullscreen)

        self.queue_page.playRequested.connect(self._play_queue_index)
        self.queue_page.removeRequested.connect(self._remove_queue)
        self.queue_page.clearRequested.connect(self._clear_queue)
        self.queue_page.playlistTrack.connect(self._add_to_playlist_menu)

        self.settings_page.settingsChanged.connect(self._on_settings_changed)
        self.settings_page.dataChanged.connect(self._on_data_changed)

        grip = QSizeGrip(self.centralWidget())
        grip.setFixedSize(16, 16)
        grip.setStyleSheet("background:transparent;")
        self.grip = grip

    # ------------------------------------------------------------------ state
    @staticmethod
    def _match_key(track: dict) -> str:
        """Binding key: original title + artist, as requested."""
        return match_key(track)

    @staticmethod
    def _legacy_match_key(track: dict) -> str:
        """Old normalised key, kept only to migrate existing bindings."""
        base = song_key(track)
        album = norm_title((track or {}).get("album", ""))
        return f"{base}#{album}" if base and album else base

    @staticmethod
    def _compact_match(track: dict) -> dict:
        keys = (
            "title", "artist", "album", "cover", "audio", "audio_from", "audio_headers",
            "audio_path", "provider_id", "provider_track_id", "listen1_id", "kind",
            "source_id", "source_name", "source_accent", "headers", "duration",
            "is_live", "is_preview", "stream_only", "needs_match",
        )
        data = {k: track[k] for k in keys if k in track and track[k] not in (None, "")}
        # Provider tracks often hand out short-lived signed URLs.  Remember
        # *which* source to use, then resolve a fresh URL when the song is replayed.
        if data.get("provider_id") or data.get("audio_from"):
            data.pop("audio", None)
        return data

    def _load_match_overrides(self) -> None:
        raw = self.db.get_setting("match_overrides", "") or ""
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        loaded: dict[str, dict] = {}
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            if not (value.get("audio") or value.get("audio_from") or value.get("provider_id")):
                continue
            # Older releases stored a normalised song_key.  Re-key those
            # entries from their saved title/artist so they survive the switch
            # to the literal-title binding key.
            new_key = match_key(value) or str(key)
            loaded[new_key] = dict(value)
        self._match_overrides = loaded
        if loaded != data:
            self._persist_match_overrides()
        self._refresh_match_bindings()

    def _persist_match_overrides(self) -> None:
        self.db.set_setting(
            "match_overrides",
            json.dumps(self._match_overrides, ensure_ascii=False, separators=(",", ":")),
        )

    def _refresh_match_bindings(self) -> None:
        keys = set(self._match_overrides)
        for page in (self.home, self.search_page, self.favorites_page,
                     self.playlists_page, self.queue_page):
            setter = getattr(page, "set_match_bindings", None)
            if callable(setter):
                setter(keys)

    def _save_match_override(self, key: str, track: dict) -> None:
        if not key:
            return
        self._match_overrides[key] = self._compact_match(track)
        self._persist_match_overrides()
        self._refresh_match_bindings()

    def _remove_match_override(self, track: dict) -> bool:
        key = self._match_key(track)
        if not key or key not in self._match_overrides:
            return False
        self._match_overrides.pop(key, None)
        self._persist_match_overrides()
        self._refresh_match_bindings()
        return True

    def _load_lyric_overrides(self) -> None:
        self._lyric_overrides = {}
        try:
            raw = json.loads(self.db.get_setting("lyric_overrides", "{}") or "{}")
        except (TypeError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            return
        for key, item in raw.items():
            if not isinstance(item, dict):
                continue
            lines = []
            for row in item.get("lines") or []:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    try:
                        lines.append((int(row[0]), str(row[1])))
                    except (TypeError, ValueError):
                        continue
            if not lines:
                continue
            translations = {}
            for ms, text in (item.get("translations") or {}).items():
                try:
                    translations[int(ms)] = str(text)
                except (TypeError, ValueError):
                    continue
            self._lyric_overrides[str(key)] = {
                "lines": lines,
                "translations": translations,
                "source": str(item.get("source") or ""),
                "plain": str(item.get("plain") or ""),
                "synced": bool(item.get("synced")),
            }

    def _persist_lyric_overrides(self) -> None:
        self.db.set_setting(
            "lyric_overrides",
            json.dumps(self._lyric_overrides, ensure_ascii=False, separators=(",", ":")))

    def _lyric_payload(self, stored: dict, label: str = "") -> dict:
        source = str(stored.get("source") or "")
        if label:
            source = f"{source} · {label}" if source else label
        return {"lines": list(stored.get("lines") or []),
                "translations": dict(stored.get("translations") or {}),
                "source": source,
                "plain": str(stored.get("plain") or ""),
                "synced": bool(stored.get("synced"))}

    def _open_lyric_search(self) -> None:
        track = getattr(self, "current_track", {}) or {}
        if not track or not track.get("title"):
            self.toast.show_message("先在播放一首歌，再搜索歌词", 2200)
            return
        if self._lyric_dialog is None:
            self._lyric_dialog = LyricSearchDialog(self)
            self._lyric_dialog.chosen.connect(self._on_lyric_chosen)
        key = match_key(track)
        self._lyric_dialog.open_for(
            track, self.db.get_setting("proxy", "") or "",
            default_active=bool(key and key in self._lyric_overrides))

    def _on_lyric_chosen(self, candidate: dict, remember: bool) -> None:
        track = getattr(self, "current_track", {}) or {}
        stored = dict(candidate.get("_lyric") or {})
        lines = list(stored.get("lines") or [])
        if not lines:
            self.toast.show_message("这个版本没有可用歌词，请换一个", 2200)
            return
        data = self._lyric_payload(
            stored, "默认歌词" if remember else "手动选择")
        self.immersive.set_lyrics(data)
        key = match_key(track)
        if remember and key:
            self._lyric_overrides[key] = {
                "lines": [[int(ms), str(text)] for ms, text in lines],
                "translations": {str(ms): str(text) for ms, text in
                                 (stored.get("translations") or {}).items()},
                "source": str(candidate.get("source") or stored.get("source") or ""),
                "plain": str(stored.get("plain") or ""),
                "synced": bool(stored.get("synced")),
            }
            self._persist_lyric_overrides()
            self.toast.show_message("已替换歌词，并设为这首歌的默认歌词", 2600)
        else:
            self.toast.show_message("已替换当前歌词", 2200)

    def _load_state(self) -> None:
        self.refresh_sources()
        self._refresh_library()
        self._refresh_match_bindings()
        self.home.set_recent(self.db.history())
        volume = self.db.get_int("volume", 65)
        self.player.set_volume(volume)
        self.player_bar.set_volume(volume)
        self.player_bar.set_mode(self.play_mode)
        self.queue_page.set_queue(self.queue)
        folia_on = self.db.get_setting("lyric_folia", "0") == "1"
        self.immersive.set_folia(folia_on)
        self._go("home")

    def refresh_sources(self) -> None:
        providers.REGISTRY.load_custom()
        sites = self.db.sites(enabled_only=True)
        self.home.set_sites(sites)
        self.search_page.set_sites(sites)
        reload_home = getattr(self.home, "reload_sources", None)
        if callable(reload_home):
            reload_home()

    def _refresh_library(self) -> None:
        self.favorite_keys = self.db.favorite_keys()
        self.search_page.set_favorite_keys(self.favorite_keys)
        self.home.set_favorite_keys(self.favorite_keys)
        favorites = self.db.favorites()
        self.favorites_page.set_tracks(favorites, self.favorite_keys)
        playlists = self.db.playlists()
        self.playlists_page.set_playlists(playlists)

    def _on_settings_changed(self) -> None:
        self.player.set_volume(self.player_bar.volume.value())
        self.player.set_proxy(self.db.get_setting("proxy", "") or "")
        self.refresh_sources()
        self.settings_page.refresh_cache_info()

    def _on_data_changed(self) -> None:
        """A backup was imported: reload every local collection."""
        self.queue = self.db.queue()
        self.current_index = -1
        self._reset_shuffle()
        self.queue_page.set_queue(self.queue)
        self._sync_queue_popup(rebuild=True)
        self.refresh_sources()
        self._refresh_library()
        self._load_match_overrides()
        self._load_lyric_overrides()
        self._refresh_match_bindings()
        volume = self.db.get_int("volume", 65)
        self.player.set_volume(volume)
        self.player_bar.set_volume(volume)

    # ------------------------------------------------------------------ navigation
    def _go(self, key: str) -> None:
        mapping = {
            "home": (self.home, 0),
            "search": (self.search_page, 1),
            "favorites": (self.favorites_page, 2),
            "playlists": (self.playlists_page, 3),
            "sites": (self.sites_page, 4),
            "queue": (self.queue_page, 5),
            "settings": (self.settings_page, 6),
        }
        page, index = mapping.get(key, (self.home, 0))
        self._last_page_key = key if key in mapping else "home"
        self.stack.setCurrentIndex(index)
        self.sidebar.select(key)
        if key == "search":
            self.titlebar.search.setFocus()
        if key == "queue":
            self.queue_page.set_current(self.current_track.get("id", ""))

    def _on_global_search(self, query: str) -> None:
        if not query:
            return
        self._go("search")
        self.search_page.search(query)

    # ------------------------------------------------------------------ playback
    def play_track(self, track: dict) -> None:
        if not track:
            return
        self._reset_playback_fallback()
        track = dict(track)
        # A single click on any list row plays that one song and puts it directly
        # below the current track.  Only the explicit "全部播放" button is allowed
        # to replace the queue, so the collection payload is intentionally ignored.
        track.pop("_collection", None)
        if track.get("needs_match") and not track.get("audio"):
            self._smart_play(track)
            return
        self._match_token += 1
        self._start_track(self._ensure_queue(track))

    def _persist_queue(self, tracks: list[dict]) -> list[dict]:
        clean = [{k: v for k, v in dict(t).items()
                 if k not in ("queue_id", "_collection")} for t in tracks]
        return self.db.queue_set(clean)

    def _play_from_collection(self, collection: list[dict], selected: dict) -> None:
        items = []
        for item in collection:
            if not isinstance(item, dict) or item.get("disabled"):
                continue
            track = dict(item)
            track.pop("_collection", None)
            items.append(track)
        if not items:
            self.toast.show_message("这个列表里没有可播放的歌曲")
            return
        index = 0
        for i, item in enumerate(items):
            if selected.get("id") and item.get("id") == selected.get("id"):
                index = i
                break
            if song_key(item) and song_key(item) == song_key(selected):
                index = i
                break
        items[index] = {**items[index], **selected, "_collection": None}
        items[index].pop("_collection", None)
        self.queue = self._persist_queue(items)
        self.current_index = index
        self._reset_shuffle()
        self.queue_page.set_queue(self.queue, self.queue[index].get("id", ""))
        self._sync_queue_popup(rebuild=True)
        self._play_queue_index(index)

    def _ensure_queue(self, track: dict) -> dict:
        existing = next((i for i, t in enumerate(self.queue) if track.get("id") and t.get("id") == track.get("id")), None)
        if (existing is not None
                and 0 <= self.current_index < len(self.queue)
                and existing != self.current_index):
            # Re-clicking a song that is already lower in the queue moves it
            # directly under the current track instead of jumping far ahead.
            item = {**self.queue[existing], **dict(track)}
            item.pop("_collection", None)
            self.queue.pop(existing)
            if existing < self.current_index:
                self.current_index -= 1
            position = (self.current_index + 1 if 0 <= self.current_index < len(self.queue)
                        else len(self.queue))
            self.queue.insert(position, item)
            self.queue = self._persist_queue(self.queue)
            existing = position
            self.current_index = existing
            self._reset_shuffle()
            self.queue_page.set_queue(self.queue, item.get("id", ""))
            self._sync_queue_popup(rebuild=True)
        elif existing is None:
            # A song outside the current list goes right after the last played
            # song; it must not wipe the whole playback list.
            position = self.current_index + 1 if 0 <= self.current_index < len(self.queue) else len(self.queue)
            track = {k: v for k, v in dict(track).items() if k != "_collection"}
            self.queue.insert(position, track)
            self.queue = self._persist_queue(self.queue)
            existing = position
            self.current_index = existing
            if self.play_mode == "list_shuffle":
                self._reset_shuffle()
            self.queue_page.set_queue(self.queue, track.get("id", ""))
            self._sync_queue_popup(rebuild=True)
        self.current_index = existing
        return self.queue[existing]

    def _cached_track(self, track: dict) -> dict | None:
        """Return a playable copy backed by the local audio cache, if present.

        Imported playlist entries carry metadata only.  Resolving them used to
        hit every source even when the exact song was already on disk, which is
        what made a cached song feel like it still needed three seconds.
        """
        if not self.cache or not track:
            return None
        try:
            path = self.cache.lookup(track)
        except Exception:  # noqa: BLE001
            return None
        if path is None:
            return None
        out = dict(track)
        out["audio"] = str(path)
        out["kind"] = "local_cache"
        out["stream_only"] = False
        out["needs_match"] = False
        out.setdefault("source_name", "本地缓存")
        return out

    def _start_track(self, track: dict, queue_index: int | None = None) -> None:
        audio = str(track.get("audio") or "")
        if audio and "://" not in audio:
            try:
                if not Path(audio).is_file():
                    track = {k: v for k, v in dict(track).items() if k != "audio"}
            except OSError:
                pass
        if not track.get("audio"):
            cached = self._cached_track(track)
            if cached is not None:
                if queue_index is not None and 0 <= queue_index < len(self.queue):
                    self.queue[queue_index] = {**self.queue[queue_index], **cached}
                    track = self.queue[queue_index]
                else:
                    track = cached
            elif track.get("audio_from") or track.get("provider_id"):
                self._fetch_then_play(track)
                return
            else:
                # Imported playlists only carry metadata: resolve them through
                # the provider registry / the enabled sources instead of handing
                # an empty URL to the player (which used to produce “无音源”).
                self._smart_play(track, queue_index=queue_index)
                return
        self._commit_play(track)

    # ------------------------------------------------------------------ best source
    def _smart_play(self, track: dict, queue_index: int | None = None) -> None:
        """Resolve a favourite / imported song to the best playable source first."""
        cached = self._cached_track(track)
        if cached is not None:
            if queue_index is not None and 0 <= queue_index < len(self.queue):
                self.queue[queue_index] = {**self.queue[queue_index], **cached}
                cached = self.queue[queue_index]
            self._commit_play(cached)
            return
        key = match_key(track)
        override = self._match_overrides.get(key) if key else None
        if override is None and key:
            # A binding saved by an older build may still use the normalised
            # key; honour it once, then the next save migrates it.
            legacy = self._legacy_match_key(track)
            override = self._match_overrides.get(legacy) if legacy else None
        if override:
            # “默认匹配该歌曲”: reuse the chosen source directly, never search
            # every enabled source again.  Signed URLs are re-resolved if needed.
            target = {**track, **override, "needs_match": False}
            if queue_index is not None and 0 <= queue_index < len(self.queue):
                self.queue[queue_index] = target
                self.queue = self._persist_queue(self.queue)
                target = self.queue[queue_index]
                self.current_index = queue_index
                self.queue_page.set_current(target.get("id", ""))
                self._sync_queue_popup()
            else:
                target = self._ensure_queue(target)
            self._start_track(target)
            return
        remembered = self._match_cache.get(song_key(track)) if song_key(track) else None
        if remembered:
            _, resolved = remembered
            if resolved.get("audio"):
                target = {**track, **resolved, "needs_match": False}
                if queue_index is not None and 0 <= queue_index < len(self.queue):
                    self.queue[queue_index] = target
                    target = self.queue[queue_index]
                self._commit_play(target)
                return
        self._match_token += 1
        token = self._match_token
        self.toast.show_message("正在匹配最优音源…")
        sites = self.db.sites(enabled_only=True)
        worker = _MatchWorker(track, sites, self.db.get_setting("proxy", "") or "")
        worker.signals.done.connect(lambda match, t=token: self._on_match(track, match, t, queue_index))
        self._matchers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _show_binding_menu(self, track: dict) -> None:
        """Gear menu for a song with a saved default source."""
        if not isinstance(track, dict):
            return
        key = self._match_key(track)
        bound = bool(key and key in self._match_overrides)
        menu = QMenu(self)
        unbind = menu.addAction("解绑默认音源")
        unbind.setEnabled(bound)
        change = menu.addAction("更换绑定源…")
        action = menu.exec(QCursor.pos())
        if action is unbind:
            if self._remove_match_override(track):
                self.toast.show_message("已解绑这首歌的默认音源")
        elif action is change:
            self._start_binding_change(track)

    def _start_binding_change(self, track: dict) -> None:
        self._binding_target = dict(track or {})
        self._binding_token = getattr(self, "_binding_token", 0) + 1
        token = self._binding_token
        self.toast.show_message("正在检查各音源是否可播放…")
        sites = self.db.sites(enabled_only=True)
        worker = _MatchWorker(track, sites,
                              self.db.get_setting("proxy", "") or "", binding=True)
        worker.signals.done.connect(
            lambda match, t=token, src=dict(track): self._on_binding_candidates(src, match, t))
        self._matchers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_binding_candidates(self, original: dict, match: dict, token: int) -> None:
        if token != getattr(self, "_binding_token", -1):
            return
        candidates = list((match or {}).get("_alternatives") or [])
        self._match_context = (dict(original), None, True)
        if self._match_dialog is None:
            self._match_dialog = MatchDialog(self.covers, self)
            self._match_dialog.chosen.connect(self._on_match_chosen)
        self._match_dialog.open_for(original, candidates, binding_mode=True)

    def _on_match(self, original: dict, match: dict, token: int, queue_index: int | None) -> None:
        if token != self._match_token:
            return
        self._fallback_in_progress = False
        self._matchers.clear()
        alternatives = list((match or {}).get("_alternatives") or [])
        if alternatives:
            self._match_context = (dict(original), queue_index, False)
            if self._match_dialog is None:
                self._match_dialog = MatchDialog(self.covers, self)
                self._match_dialog.chosen.connect(self._on_match_chosen)
            self._match_dialog.open_for(original, alternatives)
            return
        target = match if match else dict(original)
        if match and target.get("audio"):
            # A resolved URL is already playable.  Leaving needs_match set made
            # every replay of this queue row search all sources again.
            target = {**target, "needs_match": False}
            key = song_key(target)
            if key:
                self._match_cache[key] = (time.time(), dict(target))
        else:
            target = {**target, "needs_match": True}
        if not match and not (target.get("audio") or target.get("audio_from")):
            if self._fallback_attempts:
                self._fallback_exhausted = True
                self._show_buffer_error("无音源")
            else:
                self._show_buffer_error("无音源")
            return
        if queue_index is not None and 0 <= queue_index < len(self.queue):
            self.queue[queue_index] = target
            self.queue = self._persist_queue(self.queue)
            target = self.queue[queue_index]
            self.current_index = queue_index
            self.queue_page.set_current(target.get("id", ""))
            self._sync_queue_popup()
        else:
            target = self._ensure_queue(target)
        self._start_track(target)

    def _on_match_chosen(self, candidate: dict, remember: bool) -> None:
        """Play (or bind) the source the user picked in “你可能想听这首歌”."""
        original, queue_index, binding_mode = self._match_context
        if not isinstance(candidate, dict) or not candidate:
            return
        probe = candidate.get("_probe") if isinstance(candidate.get("_probe"), dict) else {}
        chosen = {**original, **candidate, "needs_match": False}
        if probe.get("audio") and not chosen.get("audio"):
            chosen["audio"] = probe["audio"]
        if probe.get("audio_headers"):
            chosen["audio_headers"] = probe["audio_headers"]
        for key in ("_alternatives", "_exact", "_matchScore", "_identityScore",
                    "_sourceScore", "_playable", "_probe"):
            chosen.pop(key, None)
        pref_key = self._match_key(original)
        if binding_mode:
            if pref_key:
                self._save_match_override(pref_key, chosen)
                self.toast.show_message("已更新这首歌的默认音源")
            return
        if remember:
            if pref_key:
                self._save_match_override(pref_key, chosen)
        if not chosen.get("audio") and not chosen.get("audio_from") and not chosen.get("provider_id"):
            self.toast.show_message("该音源暂时无法播放，请选择其他来源")
            return
        if queue_index is not None and 0 <= queue_index < len(self.queue):
            self.queue[queue_index] = chosen
            self.queue = self._persist_queue(self.queue)
            chosen = self.queue[queue_index]
            self.current_index = queue_index
            self.queue_page.set_current(chosen.get("id", ""))
            self._sync_queue_popup()
        else:
            chosen = self._ensure_queue(chosen)
        self._match_token += 1
        self._start_track(chosen)

    def play_collection(self, tracks: list[dict]) -> None:
        if not tracks:
            return
        self._play_from_collection(tracks, tracks[0])

    def toggle_favorite(self, track: dict) -> None:
        key = song_key(track)
        if not key:
            self.toast.show_message("这首歌缺少标题，无法收藏")
            return
        now_favorite = self.db.toggle_favorite(track, key)
        self.favorite_keys = self.db.favorite_keys()
        self.search_page.set_favorite_keys(self.favorite_keys)
        self.home.set_favorite_keys(self.favorite_keys)
        self._refresh_library()
        self.toast.show_message("已收藏到「我喜欢」" if now_favorite else "已取消收藏")

    # ------------------------------------------------------------------ playlists
    def _create_playlist(self) -> None:
        name, ok = QInputDialog.getText(self, "新建歌单", "歌单名称")
        if not ok or not name.strip():
            return
        pid = self.db.create_playlist(name.strip(), "", "", [], platform="本地歌单")
        self._refresh_library()
        self.playlists_page.select_playlist(pid)
        self._go("playlists")
        self.toast.show_message(f"已创建「{name.strip()}」")

    def _create_playlist_with_track(self, track: dict) -> None:
        name, ok = QInputDialog.getText(self, "新建歌单", "歌单名称")
        if not ok or not name.strip():
            return
        pid = self.db.create_playlist(name.strip(), "", "", [], platform="本地歌单")
        added = self.db.add_playlist_track(pid, track)
        self._refresh_library()
        self.playlists_page.select_playlist(pid)
        self._go("playlists")
        self.toast.show_message(
            f"已创建「{name.strip()}」并加入" if added else f"已创建「{name.strip()}」")

    def _add_to_playlist_menu(self, track: dict) -> None:
        playlists = self.db.playlists()
        menu = QMenu(self)
        if playlists:
            for playlist in playlists:
                action = menu.addAction(playlist.get("name", "未命名歌单"))
                action.triggered.connect(
                    lambda _=False, pid=playlist["id"]: self._add_track_to_playlist(pid, track))
        else:
            hint = menu.addAction("还没有歌单")
            hint.setEnabled(False)
        menu.addSeparator()
        create = menu.addAction("新建歌单并加入…")
        create.triggered.connect(lambda: self._create_playlist_with_track(track))
        menu.exec(QCursor.pos())

    def _add_track_to_playlist(self, playlist_id: str, track: dict) -> None:
        playlist = self.db.get_playlist(playlist_id)
        added = self.db.add_playlist_track(playlist_id, track)
        if added:
            self._refresh_library()
            self.toast.show_message(f"已加入「{(playlist or {}).get('name', '歌单')}」")
        else:
            self.toast.show_message(f"这首歌已经在「{(playlist or {}).get('name', '歌单')}」里了")

    def _open_import_playlist(self) -> None:
        dialog = PlaylistImportDialog(self.covers, self.db.get_setting("proxy", "") or "", self)
        if not dialog.exec() or not dialog.result_data:
            return
        data = dialog.result_data
        pid = self.db.create_playlist(data.get("name", "导入歌单"), data.get("cover", ""),
                                       dialog.link.text().strip(), data.get("tracks") or [],
                                       platform=data.get("platform", ""))
        self._refresh_library()
        self.playlists_page.select_playlist(pid)
        self._go("playlists")
        self.toast.show_message(f"已导入 {len(data.get('tracks') or [])} 首歌")

    def _load_playlist(self, playlist_id: str) -> None:
        if not playlist_id:
            return
        self.playlists_page.set_tracks(self.db.playlist_tracks(playlist_id), self.favorite_keys)

    def _rename_playlist(self, playlist_id: str, name: str) -> None:
        self.db.rename_playlist(playlist_id, name)
        self._refresh_library()
        self.toast.show_message("歌单已改名")

    def _share_playlist(self, playlist_id: str) -> None:
        import playlist_share
        playlist = self.db.get_playlist(playlist_id)
        if not playlist:
            return
        tracks = self.db.playlist_tracks(playlist_id)
        if not tracks:
            self.toast.show_message("这个歌单还没有歌曲，无法分享")
            return
        link = playlist_share.make_share_link({
            "name": playlist.get("name", "分享歌单"),
            "cover": playlist.get("cover", ""),
            "platform": playlist.get("platform", "Aria 歌单") or "Aria 歌单",
            "tracks": tracks,
        })
        PlaylistShareDialog(playlist.get("name", ""), link, self).exec()

    def _delete_playlist(self, playlist_id: str) -> None:
        self.db.delete_playlist(playlist_id)
        self._refresh_library()
        self.toast.show_message("歌单已删除")

    def _fetch_then_play(self, track: dict) -> None:
        self.toast.show_message("正在获取播放地址…")
        worker = _ResolveWorker(track, self.db.get_setting("proxy", "") or "")
        worker.signals.done.connect(self._commit_play)
        worker.signals.failed.connect(lambda msg, t=track: self._on_resolve_failed(msg, t))
        self._resolvers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _reset_playback_fallback(self) -> None:
        self._fallback_key = ""
        self._fallback_attempts = 0
        self._fallback_excluded.clear()
        self._fallback_excluded_sites.clear()
        self._fallback_in_progress = False
        self._fallback_exhausted = False

    def _show_buffer_error(self, message: str) -> None:
        reason = (message or "服务器请求失败").strip()
        for prefix in ("缓冲失败：", "播放失败：", "缓存下载失败："):
            if reason.startswith(prefix):
                reason = reason[len(prefix):].strip()
                break
        self.player_bar.set_status(f"缓冲失败：{reason or '服务器请求失败'}", error=True)

    def _on_playback_error(self, message: str) -> None:
        track = dict(self.player.current or {})
        key = song_key(track)
        if not key:
            self._show_buffer_error(message)
            return
        if key != self._fallback_key:
            self._reset_playback_fallback()
            self._fallback_key = key
        if self._fallback_in_progress:
            return
        if self._fallback_exhausted:
            return
        if self._fallback_attempts >= 3:
            self._fallback_exhausted = True
            self._show_buffer_error(message)
            return

        failed_provider = str(track.get("provider_id") or "")
        failed_site = str(track.get("source_id") or "")
        if failed_provider:
            self._fallback_excluded.add(failed_provider)
        if failed_site:
            self._fallback_excluded_sites.add(failed_site)
        failed_track_id = str(
            track.get("provider_track_id") or track.get("listen1_id")
            or track.get("id") or "")
        failed_audio = str(track.get("audio") or "")

        fallback = {
            k: v for k, v in track.items()
            if k not in ("audio", "audio_from", "provider_id", "provider_track_id",
                         "listen1_id", "listen1_source", "audio_headers",
                         "needs_match")
        }
        fallback["needs_match"] = True
        fallback["_failed_provider"] = failed_provider
        fallback["_failed_site"] = failed_site
        fallback["_failed_providers"] = sorted(self._fallback_excluded)
        fallback["_failed_sites"] = sorted(self._fallback_excluded_sites)
        fallback["_failed_track_id"] = failed_track_id
        fallback["_failed_audio"] = failed_audio

        self._fallback_attempts += 1
        self._fallback_in_progress = True
        self.player_bar.set_status("正在缓冲")
        index = self.current_index if 0 <= self.current_index < len(self.queue) else None
        self._smart_play(fallback, queue_index=index)

    def _on_resolve_failed(self, message: str, track: dict | None = None) -> None:
        self._resolvers.clear()
        if track and (track.get("provider_id") or track.get("audio_from")):
            key = song_key(track)
            if key and key != self._fallback_key:
                self._reset_playback_fallback()
                self._fallback_key = key
            failed_provider = track.get("provider_id") or track.get("listen1_source") or ""
            failed_site = track.get("source_id") or ""
            if failed_provider:
                self._fallback_excluded.add(str(failed_provider))
            if failed_site:
                self._fallback_excluded_sites.add(str(failed_site))
            fallback = {k: v for k, v in track.items()
                        if k not in ("audio_from", "listen1_id", "listen1_source",
                                     "provider_track_id", "audio", "needs_match")}
            fallback["needs_match"] = True
            fallback["_failed_provider"] = failed_provider
            fallback["_failed_site"] = failed_site
            fallback["_failed_providers"] = sorted(self._fallback_excluded)
            fallback["_failed_sites"] = sorted(self._fallback_excluded_sites)
            fallback["_failed_track_id"] = str(
                track.get("provider_track_id") or track.get("listen1_id")
                or track.get("id") or "")
            self._fallback_attempts += 1
            self._fallback_in_progress = True
            self.player_bar.set_status("正在缓冲")
            index = self.current_index if 0 <= self.current_index < len(self.queue) else None
            self._smart_play(fallback, queue_index=index)
            return
        self._show_buffer_error(message)

    def _commit_play(self, track: dict) -> None:
        self._resolvers.clear()
        self.player.play_track(track)
        self.db.add_history(track)
        self.home.set_recent(self.db.history())
        # Playback only changes the current marker; rebuilding every queue row
        # here made clicking a song in a long queue feel like a freeze.
        self.queue_page.set_current(track.get("id", ""))
        self._sync_queue_popup()
        self.current_track = dict(track)

    def _queue_menu(self, track: dict) -> None:
        """Secondary menu behind every row's “加入列表” button."""
        menu = QMenu(self)
        append = menu.addAction("加入播放列表")
        append.triggered.connect(lambda _=False, t=dict(track): self.add_to_queue(t))
        play_next = menu.addAction("下一曲播放")
        play_next.triggered.connect(
            lambda _=False, t=dict(track): self.add_to_queue_next(t))
        menu.exec(QCursor.pos())

    def add_to_queue(self, track: dict) -> None:
        if any(t.get("id") == track.get("id") and track.get("id") for t in self.queue):
            self.toast.show_message("这首歌已经在队列里了")
            return
        self.queue.append({k: v for k, v in dict(track).items() if k != "_collection"})
        self.queue = self._persist_queue(self.queue)
        self._reset_shuffle()
        self.queue_page.set_queue(self.queue, self.current_track.get("id", ""))
        self._sync_queue_popup(rebuild=True)
        self.toast.show_message("已加入播放列表")

    def add_to_queue_next(self, track: dict) -> None:
        """Insert the song immediately after the current one, without playing it."""
        clean = {k: v for k, v in dict(track).items() if k != "_collection"}
        current_id = str(self.current_track.get("id") or "")
        if clean.get("id") and clean.get("id") == current_id:
            self.toast.show_message("这首歌正在播放，无需插入下一曲")
            return
        existing = next((i for i, t in enumerate(self.queue)
                         if clean.get("id") and t.get("id") == clean.get("id")), None)
        if existing is not None:
            self.queue.pop(existing)
            if existing < self.current_index:
                self.current_index -= 1
        position = (self.current_index + 1
                    if 0 <= self.current_index < len(self.queue) else len(self.queue))
        self.queue.insert(position, clean)
        self.queue = self._persist_queue(self.queue)
        self._reset_shuffle()
        self.queue_page.set_queue(self.queue, current_id)
        self._sync_queue_popup(rebuild=True)
        self.toast.show_message("已插入到下一曲播放")

    def _reset_shuffle(self) -> None:
        self._shuffle_history = []
        if 0 <= self.current_index < len(self.queue):
            self._shuffle_remaining = [
                i for i in range(len(self.queue)) if i != self.current_index]
        else:
            self._shuffle_remaining = list(range(len(self.queue)))

    def _mark_shuffle(self, index: int) -> None:
        if self.play_mode != "list_shuffle":
            return
        if index in self._shuffle_remaining:
            self._shuffle_remaining.remove(index)
        if not self._shuffle_history or self._shuffle_history[-1] != index:
            self._shuffle_history.append(index)

    def _play_queue_index(self, index: int) -> None:
        if 0 <= index < len(self.queue):
            self._prefetched_index = None
            self._prefetched_id = ""
            self._prefetched_key = ""
            self._prefetch_armed = False
            self._prefetch_for = ""
            self._reset_playback_fallback()
            self.current_index = index
            self._mark_shuffle(index)
            track = self.queue[index]
            if track.get("needs_match") and not track.get("audio"):
                self._smart_play(track, queue_index=index)
                return
            self._match_token += 1
            self._start_track(track, queue_index=index)
            self.queue_page.set_current(track.get("id", ""))
            self._sync_queue_popup()

    def _on_finished(self) -> None:
        if self.play_mode == "single_loop":
            if 0 <= self.current_index < len(self.queue):
                self._play_queue_index(self.current_index)
            return
        self._advance_next(use_prefetch=True)

    def _advance_next(self, use_prefetch: bool = False) -> None:
        if not self.queue:
            return
        nxt: int | None = None
        if use_prefetch and self._prefetched_index is not None:
            idx = self._prefetched_index
            if 0 <= idx < len(self.queue) and idx != self.current_index:
                track_id = str(self.queue[idx].get("id") or "")
                if track_id:
                    valid = track_id == self._prefetched_id
                else:
                    valid = song_key(self.queue[idx]) == self._prefetched_key
                if valid:
                    nxt = idx
        if nxt is None:
            nxt = self._peek_next_index()
        if nxt is None and self.play_mode == "single_loop" and self.queue:
            nxt = (self.current_index + 1) % len(self.queue)
        if nxt is None:
            return
        self._play_queue_index(nxt)

    def play_next(self) -> None:
        # A manual “next” discards the prefetched branch so the user gets a
        # fresh choice for shuffle / pure-random.
        self._advance_next(use_prefetch=False)

    def play_prev(self) -> None:
        if not self.queue:
            return
        self._prefetched_index = None
        self._prefetched_id = ""
        self._prefetched_key = ""
        self._prefetch_for = ""
        self._prefetch_armed = False
        mode = self.play_mode
        if mode == "list_shuffle" and len(self._shuffle_history) > 1:
            self._shuffle_history.pop()
            prev = self._shuffle_history[-1]
        elif mode == "pure_random":
            prev = random.randrange(len(self.queue))
        else:
            prev = (self.current_index - 1) % len(self.queue)
        self._play_queue_index(prev)

    def _remove_queue(self, index: int) -> None:
        if not (0 <= index < len(self.queue)):
            return
        item = self.queue.pop(index)
        qid = item.get("queue_id")
        if qid:
            self.db.queue_remove(int(qid))
        if index == self.current_index:
            self.player.stop()
            self.current_index = -1
        elif index < self.current_index:
            self.current_index -= 1
        self.queue = self._persist_queue(self.queue)
        self._reset_shuffle()
        self.queue_page.set_queue(self.queue, self.current_track.get("id", ""))
        self._sync_queue_popup(rebuild=True)

    def _clear_queue(self) -> None:
        self.queue = []
        self.db.queue_clear()
        self.current_index = -1
        self.player.stop()
        self._reset_shuffle()
        self.queue_page.set_queue([])
        self._sync_queue_popup(rebuild=True)

    # ------------------------------------------------------------------ floating queue
    def _sync_queue_popup(self, rebuild: bool = False) -> None:
        if self.queue_popup and self.queue_popup.isVisible():
            current = self.current_track.get("id", "")
            if rebuild:
                self.queue_popup.set_queue(self.queue, current)
            else:
                self.queue_popup.set_current(current)

    def _show_queue_popup(self) -> None:
        if self.queue_popup is None:
            self.queue_popup = QueuePopup(self.covers, self)
            self.queue_popup.playRequested.connect(self._play_from_popup)
            self.queue_popup.removeRequested.connect(self._remove_queue)
            self.queue_popup.clearRequested.connect(self._clear_queue)
            self.queue_popup.playlistRequested.connect(self._add_to_playlist_menu)
        if self.queue_popup.isVisible():
            self.queue_popup.hide()
            return
        self.queue_popup.set_queue(self.queue, self.current_track.get("id", ""))
        if self.immersive.isVisible():
            if bool(getattr(self.immersive, "_folia", False)):
                # Folia hides the Aria transport; anchor to Folia's own list
                # icon so the 480 px popup opens above it instead of covering it.
                anchor = self.immersive.folia_queue_anchor()
            else:
                anchor = self.immersive.playlist_btn.mapToGlobal(QPoint(0, 0))
        else:
            anchor = self.player_bar.queue_btn.mapToGlobal(QPoint(0, 0))
        self.queue_popup.show_at(anchor)

    def _play_from_popup(self, index: int) -> None:
        if self.queue_popup:
            self.queue_popup.hide()
        self._play_queue_index(index)

    # ------------------------------------------------------------------ play mode
    def _show_mode_menu(self) -> None:
        menu = QMenu(self)
        for key, label in PLAY_MODES.items():
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == self.play_mode)
            action.triggered.connect(lambda _=False, k=key: self._set_play_mode(k))
        button = self.player_bar.mode_btn
        menu.exec(button.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 8)))

    def _set_play_mode(self, mode: str) -> None:
        if mode not in PLAY_MODES:
            return
        self.play_mode = mode
        self.db.set_setting("play_mode", mode)
        self.player_bar.set_mode(mode)
        self._reset_shuffle()
        self.toast.show_message(f"播放模式：{PLAY_MODES[mode]}")

    def _on_volume(self, value: int) -> None:
        self.player.set_volume(value)
        self.db.set_setting("volume", str(value))

    # ------------------------------------------------------------------ player events
    def _on_track(self, track: dict) -> None:
        self.current_track = track
        self._prefetch_armed = False
        self._prefetch_for = ""
        self._prefetched_index: int | None = None
        self._prefetched_id = ""
        self._prefetched_key = ""
        self.player_bar.set_track(track)
        self.playlists_page.note_playback(track, self.play_mode)
        self.immersive.set_track(track, None)
        self.covers.get(track.get("cover", ""), lambda pix: self.immersive.set_cover(pix))
        self.queue_page.set_current(track.get("id", ""))
        self._start_lyrics(track)

    def _on_cache_started(self, track: dict) -> None:
        # A silent prefetch never reaches this signal.  Only the song that is
        # actually switching on screen shows “正在缓冲”.
        self.player_bar.set_status("正在缓冲")

    def _on_cache_progress(self, percent: int, written: int) -> None:
        # The progressive proxy deliberately keeps this minimal: no percent
        # text and no popup while the bytes stream in.
        return

    def _on_cache_ready(self, track: dict) -> None:
        self.player_bar.clear_status()
        self.settings_page.refresh_cache_info()

    def _on_cache_hit(self, track: dict) -> None:
        # A complete cache is a silent instant play.
        self.player_bar.clear_status()

    def _start_lyrics(self, track: dict) -> None:
        self._lyric_token = getattr(self, "_lyric_token", 0) + 1
        token = self._lyric_token
        key = match_key(track)
        stored = self._lyric_overrides.get(key) if key else None
        if stored and stored.get("lines"):
            self._lyric_workers = []
            self.immersive.set_lyrics(self._lyric_payload(stored, "默认歌词"))
            return
        worker = _LyricWorker(track, self.db.get_setting("proxy", "") or "")
        worker.signals.done.connect(lambda data, t=token: self._on_lyrics(data, t))
        self._lyric_workers = getattr(self, "_lyric_workers", [])
        self._lyric_workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_lyrics(self, data: dict, token: int) -> None:
        if token != getattr(self, "_lyric_token", -1):
            return  # a newer track already started matching
        self._lyric_workers = []
        self.immersive.set_lyrics(data or {})

    def _on_state(self, state: str) -> None:
        playing = state == "playing"
        if playing:
            # The moment the first decodable frame starts, the buffering cue
            # must disappear even if the rest of the file is still arriving.
            self.player_bar.clear_status()
        self.player_bar.set_state(playing)
        self.immersive.set_state(playing)

    def _on_position(self, ms: int) -> None:
        self.player_bar.set_position(ms)
        self._duration = getattr(self, "_duration", 0)
        if self.immersive.isVisible():
            self.immersive.set_position(ms)
        self._arm_next_prefetch(ms)

    def _arm_next_prefetch(self, ms: int) -> None:
        duration = getattr(self, "_duration", 0)
        if duration <= 0 or not self.queue:
            return
        remaining = duration - ms
        if remaining > 6500:
            # A seek backwards gives the prefetch another chance.
            self._prefetch_armed = False
            return
        if remaining > 5000 or remaining < 0 or self._prefetch_armed:
            return
        self._prefetch_armed = True
        self._prefetch_for = song_key(self.current_track or {})
        self._prefetch_next()

    def _peek_next_index(self) -> int | None:
        """Preview the next index without consuming shuffle state."""
        if not self.queue:
            return None
        n = len(self.queue)
        mode = self.play_mode
        current = self.current_index if 0 <= self.current_index < n else 0
        if mode in ("single_loop",):
            return None
        if mode == "list_loop":
            return (current + 1) % n
        if mode == "list_shuffle":
            if n <= 1:
                return 0
            remaining = list(self._shuffle_remaining)
            if not remaining:
                remaining = [i for i in range(n) if i != current]
            if not remaining:
                return None
            return random.choice(remaining)
        if n <= 1:
            return None
        return random.randrange(n)

    def _prefetch_next(self) -> None:
        idx = self._peek_next_index()
        if idx is None or not (0 <= idx < len(self.queue)) or idx == self.current_index:
            return
        track = dict(self.queue[idx])
        self._prefetched_index = idx
        self._prefetched_id = str(track.get("id") or "")
        self._prefetched_key = song_key(track)
        audio = str(track.get("audio") or "").strip()
        if audio:
            self.player.prefetch_track(track, audio)
            return
        proxy = self.db.get_setting("proxy", "") or ""
        if track.get("audio_from") or track.get("provider_id"):
            worker = _ResolveWorker(track, proxy)
            worker.signals.done.connect(
                lambda result, t=track, i=idx: self._on_prefetch_resolved(t, result, i))
            QThreadPool.globalInstance().start(worker)
            return
        # Metadata-only chart / playlist rows: resolve the best exact source,
        # but never open the “你可能想听” dialog during a silent prefetch.
        sites = self.db.sites(enabled_only=True)
        worker = _MatchWorker(track, sites, proxy)
        worker.signals.done.connect(
            lambda result, t=track, i=idx: self._on_prefetch_resolved(t, result, i))
        QThreadPool.globalInstance().start(worker)

    def _on_prefetch_resolved(self, track: dict, result: dict, index: int) -> None:
        if not isinstance(result, dict) or not result.get("audio"):
            return
        if result.get("needs_match") or result.get("_alternatives"):
            return
        if not (0 <= index < len(self.queue)):
            return
        if str(self.queue[index].get("id") or "") != self._prefetched_id:
            return
        if self._prefetch_for != song_key(self.current_track or {}):
            return
        target = {**track, "audio": result["audio"], "needs_match": False}
        if result.get("audio_headers"):
            target["audio_headers"] = result["audio_headers"]
        self.player.prefetch_track(target, result["audio"])

    def _on_duration(self, ms: int) -> None:
        self._duration = ms
        self.player_bar.set_duration(ms)
        self.immersive.set_duration(ms)

    # ------------------------------------------------------------------ immersive
    def _enter_immersive(self) -> None:
        if not self.current_track:
            self.toast.show_message("先播放一首歌再进入歌词模式")
            return
        self.immersive.setGeometry(self.centralWidget().rect())
        self.immersive.show()
        self.immersive.raise_()
        self._ui_hide_shortcut.setEnabled(True)
        self.immersive.set_state(self.player.is_playing())
        self.immersive.set_position(self.player.position())

    def _leave_immersive(self) -> None:
        if self.immersive.isVisible() or not self.immersive.isHidden():
            if self._immersive_fullscreen:
                self._set_immersive_fullscreen(False)
            self.immersive.reset_ui_hidden()
            self.immersive.hide()
            self._ui_hide_shortcut.setEnabled(False)

    def _open_queue_from_immersive(self) -> None:
        self._show_queue_popup()

    def _on_lyric_folia_changed(self, enabled: bool) -> None:
        self.db.set_setting("lyric_folia", "1" if enabled else "0")

    def _cycle_play_mode_from_immersive(self) -> None:
        keys = list(PLAY_MODES.keys())
        if not keys:
            return
        try:
            index = keys.index(self.play_mode)
        except ValueError:
            index = -1
        self._set_play_mode(keys[(index + 1) % len(keys)])

    def _set_immersive_fullscreen(self, enabled: bool) -> None:
        if enabled == self._immersive_fullscreen:
            return
        if enabled and not self.immersive.isVisible():
            return
        self._immersive_fullscreen = bool(enabled)
        self.immersive.set_fullscreen(self._immersive_fullscreen)
        if enabled:
            self._immersive_prev_max = self.isMaximized()
            self.showFullScreen()
        else:
            if getattr(self, "_immersive_prev_max", False):
                self.showMaximized()
            else:
                self.showNormal()
            self.immersive.setGeometry(self.centralWidget().rect())

    def _toggle_immersive_fullscreen(self) -> None:
        if self.immersive.isVisible():
            self._set_immersive_fullscreen(not self._immersive_fullscreen)

    # ------------------------------------------------------------------ window
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.immersive.setGeometry(self.centralWidget().rect())
        if hasattr(self, "grip"):
            self.grip.move(self.centralWidget().width() - 16, self.centralWidget().height() - 16)
            self.grip.raise_()
        if hasattr(self, "toast") and self.toast.isVisible():
            self.toast.move((self.toast.parentWidget().width() - self.toast.width()) // 2,
                            self.toast.parentWidget().height() - 170)

    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape and self.immersive.isVisible():
            self._leave_immersive()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if self._really_quit or not self._tray_available:
            self.player.stop()
            self.proxy.stop()
            event.accept()
            return
        self._tray_was_maximized = self.isMaximized()
        if not self.immersive.isHidden():
            self._leave_immersive()
        event.ignore()
        self.hide()
        if self.tray:
            self.tray.show()
