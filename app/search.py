"""Aggregated search page."""

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import adapters
import providers
from components import ChipButton, CoverCache, ResultRow, SectionHeading
from icons import make_icon
from keys import match_key, song_key


class _SearchSignals(QObject):
    done = Signal(str, list, str, int)  # site_id, tracks, error, generation


class SearchWorker(QRunnable):
    def __init__(self, site: dict, query: str, limit: int, proxy: str, generation: int) -> None:
        super().__init__()
        self.site = site
        self.query = query
        self.limit = limit
        self.proxy = proxy
        self.generation = generation
        self.signals = _SearchSignals()

    def run(self) -> None:
        try:
            if self.site.get("kind") == "listen1":
                tracks = providers.REGISTRY.search_by("listen1", self.query,
                                                       limit=self.limit)
                # The same song can appear on more than one platform; keep the first.
                seen, merged = set(), []
                for track in tracks:
                    key = (str(track.get("title", "")).strip().lower(),
                           str(track.get("artist", "")).strip().lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(track)
                if not merged:
                    raise RuntimeError("Listen1 没有返回结果")
                self.signals.done.emit(self.site["id"], merged, "", self.generation)
                return
            if self.site.get("kind") == "provider":
                tracks = providers.REGISTRY.search_by(
                    str(self.site["id"]), self.query, limit=self.limit)
                if not tracks:
                    raise RuntimeError(f"{self.site.get('name', '')} 没有返回结果")
                self.signals.done.emit(self.site["id"], tracks, "", self.generation)
                return
            tracks = adapters.search_site(self.site, self.query, self.limit, self.proxy)
            self.signals.done.emit(self.site["id"], tracks, "", self.generation)
        except Exception as exc:  # noqa: BLE001
            self.signals.done.emit(self.site["id"], [], str(exc)[:120], self.generation)


class SearchPage(QWidget):
    playTrack = Signal(dict)
    playAll = Signal(list)
    queueTrack = Signal(dict)
    favoriteTrack = Signal(dict)
    playlistTrack = Signal(dict)
    matchBindingRequested = Signal(dict)

    def __init__(self, covers: CoverCache, db, parent=None) -> None:
        super().__init__(parent)
        self._covers = covers
        self.db = db
        self.sites: list[dict] = []
        self.results: dict[str, list[dict]] = {}
        self.errors: dict[str, str] = {}
        self.filter_id = ""
        self.generation = 0
        self.pending = 0
        self.pool = QThreadPool.globalInstance()
        self._fav_keys: set[str] = set()
        self._match_keys: set[str] = set()
        self._rows: list[ResultRow] = []
        # The title bar owns the single visible search field. Keep this hidden
        # editor only as the last-query state so existing callers still work.
        self.input = QLineEdit(self)
        self.input.setVisible(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(34, 24, 34, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(SectionHeading("聚合搜索", "一次检索所有已启用来源，点击结果即可试听"))
        head.addStretch(1)
        self.play_all_btn = QPushButton("全部播放")
        self.play_all_btn.setObjectName("Primary")
        self.play_all_btn.setIcon(make_icon("play", "#FFFFFF", 14))
        self.play_all_btn.setVisible(False)
        self.play_all_btn.clicked.connect(self._emit_play_all)
        head.addWidget(self.play_all_btn)
        self.meta = QLabel("")
        self.meta.setObjectName("Muted")
        head.addWidget(self.meta)
        root.addLayout(head)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        self.progress.setStyleSheet("QProgressBar{background:transparent;}QProgressBar::chunk{"
                                    "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #5F86FF,stop:1 #A071FF);"
                                    "border-radius:2px;}")
        root.addWidget(self.progress)

        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        self.chips_container = QWidget()
        self.chips_layout = QHBoxLayout(self.chips_container)
        self.chips_layout.setContentsMargins(0, 0, 0, 0)
        self.chips_layout.setSpacing(8)
        chips_row.addWidget(self.chips_container)
        chips_row.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        chips_row.addWidget(self.status)
        root.addLayout(chips_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 4, 8, 10)
        self.body_layout.setSpacing(14)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        self._show_empty("输入歌名开始聚合搜索",
                         "会同时检索所有已启用的来源，结果按来源分组，双击即可播放。")

    # ------------------------------------------------------------------ state
    def set_sites(self, sites: list[dict]) -> None:
        known = {str(site.get("id") or "") for site in sites}
        self.sites = list(sites)
        for provider in providers.REGISTRY.discoverable():
            if provider.id in known or provider.id == "listen1":
                continue
            self.sites.append({
                "id": provider.id,
                "name": provider.name,
                "kind": "provider",
                "tag": "开源项目",
                "desc": "从开源项目向导接入的音乐源",
                "enabled": 1,
            })
        self.meta.setText(f"{len(self.sites)} 个已启用来源")
        self._rebuild_chips()

    def set_favorite_keys(self, keys: set[str]) -> None:
        """Filled hearts follow the shared favourite set, even in stale result rows."""
        self._fav_keys = set(keys or ())
        for row in self._rows:
            if row.isVisible():
                row.set_favorite(song_key(row.track) in self._fav_keys)

    def set_match_bindings(self, keys: set[str]) -> None:
        self._match_keys = set(keys or ())
        for row in self._rows:
            row.set_match_bound(match_key(row.track) in self._match_keys)

    def _rebuild_chips(self) -> None:
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.all_chip = ChipButton("全部")
        self.all_chip.setChecked(not self.filter_id)
        self.all_chip.clicked.connect(lambda: self._set_filter(""))
        self.chips_layout.addWidget(self.all_chip)
        for site in self.sites:
            chip = ChipButton(site["name"])
            chip.setChecked(self.filter_id == site["id"])
            chip.clicked.connect(lambda _=False, sid=site["id"]: self._set_filter(sid))
            self.chips_layout.addWidget(chip)

    def _set_filter(self, site_id: str) -> None:
        self.filter_id = site_id
        for i in range(self.chips_layout.count()):
            w = self.chips_layout.itemAt(i).widget()
            if isinstance(w, ChipButton):
                if i == 0:
                    w.setChecked(not site_id)
                else:
                    w.setChecked(self.sites[i - 1]["id"] == site_id if i - 1 < len(self.sites) else False)
        self._render()

    # ------------------------------------------------------------------ search
    def search(self, query: str, autoplay_first: bool = False, site_id: str = "") -> None:
        self.input.setText(query)
        if site_id:
            sites = [s for s in self.sites if s["id"] == site_id]
        else:
            sites = list(self.sites)
        if not sites:
            self.status.setText("没有已启用的来源")
            return
        self.generation += 1
        gen = self.generation
        self._autoplay_pending = autoplay_first
        self.results = {}
        self.errors = {}
        self.pending = len(sites)
        self._clear_body()
        self.progress.setVisible(True)
        self.status.setText(f"正在检索 {len(sites)} 个来源…")
        limit = self.db.get_int("page_size", 25)
        proxy = self.db.get_setting("proxy", "") or ""
        for site in sites:
            worker = SearchWorker(site, query, limit, proxy, gen)
            worker.signals.done.connect(self._on_result)
            self.pool.start(worker)

    def _on_result(self, site_id: str, tracks: list, error: str, generation: int) -> None:
        if generation != self.generation:
            return
        self.results[site_id] = tracks or []
        if error:
            self.errors[site_id] = error
        self.pending -= 1
        if self.pending <= 0:
            self.progress.setVisible(False)
            total = sum(len(v) for v in self.results.values())
            errs = len(self.errors)
            self.status.setText(f"共 {total} 条结果" + (f" · {errs} 个来源不可用" if errs else ""))
            if total == 0:
                self._show_empty("没有找到结果",
                                 "换个关键词试试，或在站点库中检测来源是否可用。")
                return
            if generation == self.generation and not self.filter_id and self._autoplay_pending:
                self._autoplay_pending = False
                self._play_first()
        self._render()

    _autoplay_pending = False

    def _play_first(self) -> None:
        for site in list(self.sites):
            rows = self.results.get(site["id"]) or []
            if rows:
                self.playTrack.emit(rows[0])
                return

    def play_first_for(self, site_id: str) -> None:
        rows = self.results.get(site_id) or []
        if rows:
            self.playTrack.emit(rows[0])

    def _visible_tracks(self) -> list[dict]:
        sites = [s for s in self.sites
                 if (s["id"] == self.filter_id if self.filter_id else True)]
        tracks: list[dict] = []
        for site in sites:
            tracks.extend(list((self.results.get(site["id"]) or [])[:40]))
        return tracks

    def _emit_play_all(self) -> None:
        tracks = self._visible_tracks()
        if tracks:
            self.playAll.emit([dict(t) for t in tracks])

    # ------------------------------------------------------------------ render
    def _clear_body(self) -> None:
        self._rows = []
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_empty(self, title: str, sub: str) -> None:
        self._clear_body()
        self.play_all_btn.setVisible(False)
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 90, 0, 0)
        col.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(make_icon("music", "#3B4050", 46).pixmap(46, 46))
        icon.setAlignment(Qt.AlignCenter)
        col.addWidget(icon)
        t = QLabel(title)
        t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet("color:#E9EAF2;font-size:16px;font-weight:600;")
        col.addWidget(t)
        s = QLabel(sub)
        s.setAlignment(Qt.AlignCenter)
        s.setObjectName("Muted")
        s.setWordWrap(True)
        col.addWidget(s)
        col.addStretch(1)
        self.body_layout.addWidget(wrap)

    def _render(self) -> None:
        if not self.results and not self.errors:
            return
        self._clear_body()
        sites = [s for s in self.sites if (s["id"] == self.filter_id if self.filter_id else True)]
        total_rows = 0
        for site in sites:
            rows = self.results.get(site["id"], [])
            error = self.errors.get(site["id"], "")
            if not rows and not error:
                continue
            section = QWidget()
            col = QVBoxLayout(section)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(2)
            header = QHBoxLayout()
            name = QLabel(site["name"])
            name.setStyleSheet("color:#FFFFFF;font-size:14px;font-weight:600;")
            count = QLabel(f"{len(rows)} 首" if rows else "不可用")
            count.setObjectName("Muted")
            header.addWidget(name)
            header.addWidget(count)
            header.addStretch(1)
            if rows:
                play_all = QPushButton("播放首条")
                play_all.setObjectName("LinkBtn")
                play_all.setIcon(make_icon("play", "#8A8FA3", 13))
                play_all.clicked.connect(lambda _=False, sid=site["id"]: self.play_first_for(sid))
                header.addWidget(play_all)
            col.addLayout(header)
            if error and not rows:
                err = QLabel(f"  {error}")
                err.setObjectName("Muted")
                err.setWordWrap(True)
                col.addWidget(err)
            for i, track in enumerate(rows[:40], start=1):
                row = ResultRow(track, i, self._covers)
                row.playRequested = self.playTrack.emit
                row.queueRequested = self.queueTrack.emit
                row.favoriteRequested = self.favoriteTrack.emit
                row.playlistRequested = self.playlistTrack.emit
                row.matchBindingRequested = self.matchBindingRequested.emit
                row.set_favorite(song_key(track) in self._fav_keys)
                row.set_match_bound(match_key(track) in self._match_keys)
                self._rows.append(row)
                col.addWidget(row)
                total_rows += 1
            self.body_layout.addWidget(section)
        if not total_rows and not self.errors:
            self._show_empty("还没有结果", "换个关键词，或检查来源是否可用。")
        self.play_all_btn.setVisible(bool(total_rows))
        self.body_layout.addStretch(1)
