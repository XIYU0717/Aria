"""Discover page: platform playlists, charts and categories powered by built-in sources."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import providers
import theme
from components import ChipButton, CoverCache, ResultRow, SectionHeading
from icons import make_icon, placeholder_art, rounded


def _platform_meta() -> dict[str, dict]:
    """Platform chips for the discover page.

    Listen1 is still one source, but its five public endpoints keep the old
    QQ / NetEase / Kugou / Kuwo / Qishui tabs so each platform has its own
    hot playlists, charts and categories.
    """
    meta: dict[str, dict] = {}
    if providers.REGISTRY.get("listen1") is not None:
        for pid, name, short, accent in providers.Listen1Provider.PLATFORMS:
            meta[pid] = {
                "name": name,
                "short": short,
                "accent": accent,
                "provider": "listen1",
                "platform": pid,
            }
    for provider in providers.REGISTRY.discoverable():
        # The discover page starts with Listen1's five platforms only.
        # Buguyy remains available as a source in existing profiles/search,
        # but it must not add a sixth button here.
        if provider.id in ("listen1", "buguyy"):
            continue
        meta[provider.id] = {
            "name": provider.name,
            "short": provider.short,
            "accent": provider.accent,
            "provider": provider.id,
            "platform": "",
        }
    return meta


class _WorkerSignals(QObject):
    done = Signal(str, object, str, int)


class RecommendWorker(QRunnable):
    def __init__(self, kind: str, fn, generation: int) -> None:
        super().__init__()
        self.kind = kind
        self.fn = fn
        self.generation = generation
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            data = self.fn()
            error = ""
        except Exception as exc:  # noqa: BLE001
            data = None
            error = str(exc)[:160]
        try:
            self.signals.done.emit(self.kind, data, error, self.generation)
        except RuntimeError:
            pass


class PlaylistTile(QFrame):
    clicked = Signal(dict)

    def __init__(self, item: dict, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.item = dict(item)
        self.setObjectName("PlaylistTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(204)
        self.setMinimumWidth(150)
        self.setMaximumWidth(260)
        col = QVBoxLayout(self)
        col.setContentsMargins(10, 10, 10, 10)
        col.setSpacing(8)
        self.cover = QLabel()
        self.cover.setFixedHeight(126)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet("border-radius:13px;background:rgba(255,255,255,0.06);")
        covers.get(item.get("cover", ""), self._set_cover)
        col.addWidget(self.cover)
        self.title = QLabel()
        self.title.setObjectName("TrackTitle")
        self.title.setWordWrap(False)
        self.title.setFixedHeight(20)
        col.addWidget(self.title)
        self.meta = QLabel()
        self.meta.setObjectName("TrackMeta")
        self.meta.setFixedHeight(18)
        col.addWidget(self.meta)
        self._set_text()

    def _set_text(self) -> None:
        title = self.item.get("title", "")
        metrics = QFontMetrics(self.title.font())
        self.title.setText(metrics.elidedText(title, Qt.ElideRight, max(120, self.width() - 22)))
        self.title.setToolTip(title)
        bits = [self.item.get("source_name", "")]
        count = self.item.get("count")
        if count:
            unit = self.item.get("count_unit") or "首"
            if unit == "播放" and count >= 10000:
                bits.append(f"{count / 10000:.1f} 万播放")
            else:
                bits.append(f"{int(count)} {unit}")
        if self.item.get("subtitle"):
            bits.append(str(self.item["subtitle"]))
        self.meta.setText(" · ".join(b for b in bits if b))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._set_text()

    def _set_cover(self, pix) -> None:
        if pix and not pix.isNull():
            self.cover.setPixmap(rounded(pix, 126, 13))
        else:
            art = placeholder_art(126, 126, self.item.get("title", "歌单"), _platform_meta().get(
                self.item.get("source", ""), {}).get("accent", theme.ACCENT))
            self.cover.setPixmap(rounded(art, 126, 13))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item)
        super().mouseReleaseEvent(event)


class ChartTile(QFrame):
    clicked = Signal(dict)

    def __init__(self, item: dict, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.item = dict(item)
        self.setObjectName("PlaylistTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(84)
        self.setMinimumWidth(150)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(10)
        self.cover = QLabel()
        self.cover.setFixedSize(62, 62)
        self.cover.setStyleSheet("border-radius:11px;background:rgba(255,255,255,0.06);")
        covers.get(item.get("cover", ""), self._set_cover)
        row.addWidget(self.cover)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(item.get("title", ""))
        title.setObjectName("TrackTitle")
        title.setWordWrap(False)
        meta = QLabel(item.get("subtitle", "") or item.get("source_name", ""))
        meta.setObjectName("TrackMeta")
        col.addWidget(title)
        col.addWidget(meta)
        row.addLayout(col, 1)

    def _set_cover(self, pix) -> None:
        if pix and not pix.isNull():
            self.cover.setPixmap(rounded(pix, 62, 11))
        else:
            art = placeholder_art(62, 62, self.item.get("title", "榜"), "#6E8BFF")
            self.cover.setPixmap(rounded(art, 62, 11))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item)
        super().mouseReleaseEvent(event)


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.setParent(None)
            widget.deleteLater()


class RecommendPage(QWidget):
    playTrack = Signal(dict)
    queueTrack = Signal(dict)
    favoriteTrack = Signal(dict)
    playlistTrack = Signal(dict)
    playAll = Signal(list)
    queueRequested = Signal()
    searchRequested = Signal()
    matchBindingRequested = Signal(dict)

    def __init__(self, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self._covers = covers
        self.platforms = _platform_meta()
        self.platform = next(iter(self.platforms), "")
        self.generation = 0
        self.category_generation = 0
        self.detail_generation = 0
        self._workers: list[RecommendWorker] = []
        self._category_items: list[dict] = []
        self._chart_items: list[dict] = []
        self._category = ""
        self._category_id = ""
        self._category_sort = "5"
        self._tracks: list[dict] = []
        self._rows: list[ResultRow] = []
        self._fav_keys: set[str] = set()
        self._match_keys: set[str] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        self.overview = QWidget()
        ov = QVBoxLayout(self.overview)
        ov.setContentsMargins(34, 24, 34, 12)
        ov.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.heading = SectionHeading("发现", "热门歌单、榜单与分类，来源可随时增删")
        head.addWidget(self.heading)
        head.addStretch(1)
        self.platform_row = QHBoxLayout()
        self.platform_row.setSpacing(8)
        self.platform_buttons: dict[str, ChipButton] = {}
        self._build_platform_buttons()
        head.addLayout(self.platform_row)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("Ghost")
        self.refresh_btn.setIcon(make_icon("refresh", "#E9EAF2", 15))
        self.refresh_btn.clicked.connect(lambda: self.load(force=True))
        head.addWidget(self.refresh_btn)
        self.search_btn = QPushButton("搜索")
        self.search_btn.setObjectName("Ghost")
        self.search_btn.setIcon(make_icon("search", "#E9EAF2", 15))
        self.search_btn.clicked.connect(self.searchRequested.emit)
        head.addWidget(self.search_btn)
        ov.addLayout(head)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        ov.addWidget(self.status)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        body_col = QVBoxLayout(self.body)
        body_col.setContentsMargins(0, 2, 8, 18)
        body_col.setSpacing(18)

        body_col.addWidget(self._section_title("热门歌单", "当前平台最受欢迎的歌单"))
        self.hot_grid = QGridLayout()
        self.hot_grid.setHorizontalSpacing(12)
        self.hot_grid.setVerticalSpacing(12)
        body_col.addLayout(self.hot_grid)

        self.chart_section = self._section_title("榜单", "平台官方排行榜")
        body_col.addWidget(self.chart_section)
        self.chart_grid = QGridLayout()
        self.chart_grid.setHorizontalSpacing(12)
        self.chart_grid.setVerticalSpacing(10)
        body_col.addLayout(self.chart_grid)

        self.category_section = self._section_title("分类", "按语种、风格与场景浏览")
        body_col.addWidget(self.category_section)
        self.category_grid = QGridLayout()
        self.category_grid.setHorizontalSpacing(8)
        self.category_grid.setVerticalSpacing(8)
        body_col.addLayout(self.category_grid)
        self.category_hint = QLabel("")
        self.category_hint.setObjectName("Muted")
        body_col.addWidget(self.category_hint)

        self.cat_grid = QGridLayout()
        self.cat_grid.setHorizontalSpacing(12)
        self.cat_grid.setVerticalSpacing(12)
        body_col.addLayout(self.cat_grid)
        body_col.addStretch(1)
        self.scroll.setWidget(self.body)
        ov.addWidget(self.scroll, 1)

        self.stack.addWidget(self.overview)

        self.detail = QWidget()
        dv = QVBoxLayout(self.detail)
        dv.setContentsMargins(34, 20, 34, 12)
        dv.setSpacing(10)
        dhead = QHBoxLayout()
        self.back_btn = QPushButton("返回发现")
        self.back_btn.setObjectName("Ghost")
        self.back_btn.setIcon(make_icon("chevron_left", "#E9EAF2", 15))
        self.back_btn.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        dhead.addWidget(self.back_btn)
        dhead.addStretch(1)
        dv.addLayout(dhead)

        self.detail_card = QFrame()
        self.detail_card.setObjectName("Card")
        dc = QHBoxLayout(self.detail_card)
        dc.setContentsMargins(14, 12, 14, 12)
        dc.setSpacing(14)
        self.detail_cover = QLabel()
        self.detail_cover.setFixedSize(78, 78)
        self.detail_cover.setStyleSheet("border-radius:14px;background:rgba(255,255,255,0.06);")
        dc.addWidget(self.detail_cover)
        info = QVBoxLayout()
        info.setSpacing(3)
        self.detail_title = QLabel("正在打开歌单…")
        self.detail_title.setStyleSheet("color:#FFFFFF;font-size:19px;font-weight:700;")
        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("Muted")
        info.addWidget(self.detail_title)
        info.addWidget(self.detail_meta)
        dc.addLayout(info, 1)
        self.play_all_btn = QPushButton("全部播放")
        self.play_all_btn.setObjectName("Primary")
        self.play_all_btn.setIcon(make_icon("play", "#FFFFFF", 14))
        self.play_all_btn.clicked.connect(self._emit_play_all)
        self.play_all_btn.setVisible(False)
        dc.addWidget(self.play_all_btn)
        dv.addWidget(self.detail_card)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.detail_body = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_body)
        self.detail_layout.setContentsMargins(0, 4, 8, 8)
        self.detail_layout.setSpacing(2)
        self.detail_scroll.setWidget(self.detail_body)
        dv.addWidget(self.detail_scroll, 1)
        self.detail_empty = QLabel("正在加载…")
        self.detail_empty.setObjectName("Muted")
        self.detail_empty.setAlignment(Qt.AlignCenter)
        dv.addWidget(self.detail_empty, 1)
        self.stack.addWidget(self.detail)

        self.load()

    def _section_title(self, title: str, sub: str) -> QWidget:
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        t = QLabel(title)
        t.setObjectName("SectionTitle")
        s = QLabel(sub)
        s.setObjectName("PageSub")
        col.addWidget(t)
        col.addWidget(s)
        return wrap

    # ------------------------------------------------------------------ data
    # ------------------------------------------------------------------ compatibility
    def set_recent(self, _items) -> None:
        """Kept for MainWindow's history refresh hook; the recommendation page has no recent row."""
        return

    def set_sites(self, _sites) -> None:
        return

    def set_favorite_keys(self, keys: set[str]) -> None:
        self._fav_keys = set(keys or ())
        for row in self._rows:
            row.set_favorite(self._row_is_fav(row.track))

    def _row_is_fav(self, track: dict) -> bool:
        from keys import song_key
        return song_key(track) in self._fav_keys

    def set_match_bindings(self, keys: set[str]) -> None:
        self._match_keys = set(keys or ())
        for row in self._rows:
            row.set_match_bound(self._match_bound(row.track))

    def _match_bound(self, track: dict) -> bool:
        from keys import match_key
        return match_key(track) in self._match_keys

    def _build_platform_buttons(self) -> None:
        self.platform_buttons = {}
        self.platforms = _platform_meta()
        for key, meta in self.platforms.items():
            chip = ChipButton(meta["name"])
            chip.setChecked(key == self.platform)
            chip.clicked.connect(lambda _=False, k=key: self.set_platform(k))
            self.platform_buttons[key] = chip
            self.platform_row.addWidget(chip)

    def reload_sources(self) -> None:
        """Rebuild the source chips after an aggregator is installed/removed."""
        _clear_layout(self.platform_row)
        self.platforms = _platform_meta()
        if self.platform not in self.platforms:
            self.platform = next(iter(self.platforms), "")
        self._build_platform_buttons()
        if self.platform:
            self.load(force=True)
        else:
            self.status.setText("还没有可用的发现来源")

    def set_platform(self, key: str) -> None:
        if key not in self.platforms:
            return
        self.platform = key
        for k, chip in self.platform_buttons.items():
            chip.setChecked(k == key)
        self.load(force=True)

    def load(self, force: bool = False) -> None:
        if not force and self.status.text() == "加载中…":
            return
        self.generation += 1
        gen = self.generation
        self.status.setText("加载中…")
        self._category = ""
        self._category_id = ""
        self._category_sort = "5"
        _clear_layout(self.hot_grid)
        _clear_layout(self.chart_grid)
        _clear_layout(self.category_grid)
        _clear_layout(self.cat_grid)
        self.category_hint.setText("")
        self.category_hint.setVisible(False)
        self.chart_section.setVisible(False)
        self.category_section.setVisible(False)
        self._workers = []
        meta = self.platforms.get(self.platform) or {}
        provider = providers.REGISTRY.get(meta.get("provider") or self.platform)
        if provider is None:
            self.status.setText("该来源不可用")
            return
        platform = str(meta.get("platform") or "")
        self._start("hot", lambda: provider.recommend(limit=16, platform=platform), gen)
        self._start("charts", lambda: provider.charts(platform=platform), gen)
        self._start("categories", lambda: provider.categories(platform=platform), gen)

    def _start(self, kind: str, fn, gen: int) -> None:
        worker = RecommendWorker(kind, fn, gen)
        worker.signals.done.connect(self._on_done)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_done(self, kind: str, data, error: str, generation: int) -> None:
        if kind == "detail":
            if generation != self.detail_generation:
                return
            if error:
                self.detail_empty.setText(f"加载失败：{error}")
                self.detail_empty.setVisible(True)
                self.detail_scroll.setVisible(False)
                return
            if data:
                self._fill_detail(data)
            return
        if kind == "category":
            if generation != self.category_generation:
                return
            if error:
                self.category_hint.setText(f"分类歌单加载失败：{error}")
                return
            self._fill_category(data or [])
            return
        if generation != self.generation:
            return
        if error:
            self.status.setText(f"内置音源不可用：{error}")
            return
        if kind == "hot":
            self._fill_hot(data or [])
            self.status.setText(f"{self.platforms[self.platform]['name']} · {len(data or [])} 个热门歌单")
        elif kind == "charts":
            self._chart_items = list(data or [])
            self._fill_charts(self._chart_items)
        elif kind == "categories":
            self._fill_categories(data or [])
            if data:
                first = (data[0].get("items") or [{}])[0]
                if first:
                    self._select_category(first)

    def _fill_hot(self, items: list[dict]) -> None:
        _clear_layout(self.hot_grid)
        if not items:
            hint = QLabel("暂时没有取到热门歌单，点右上角刷新重试。")
            hint.setObjectName("Muted")
            self.hot_grid.addWidget(hint, 0, 0)
            return
        for i, item in enumerate(items):
            tile = PlaylistTile(item, self._covers)
            tile.clicked.connect(self.open_playlist)
            self.hot_grid.addWidget(tile, i // 4, i % 4)

    def _fill_charts(self, items: list[dict]) -> None:
        _clear_layout(self.chart_grid)
        self.chart_section.setVisible(bool(items))
        for i, item in enumerate(items[:8]):
            tile = ChartTile(item, self._covers)
            tile.clicked.connect(self.open_chart)
            self.chart_grid.addWidget(tile, i // 2, i % 2)

    def _fill_categories(self, groups: list[dict]) -> None:
        _clear_layout(self.category_grid)
        self.category_section.setVisible(bool(groups))
        self.category_hint.setVisible(bool(groups))
        self._category_items = []
        row = 0
        col = 0
        for group in groups:
            for item in group.get("items") or []:
                chip = ChipButton(item.get("name", ""))
                chip.clicked.connect(lambda _=False, it=item: self._select_category(it))
                self.category_grid.addWidget(chip, row, col)
                self._category_items.append((chip, item))
                col += 1
                if col >= 10:
                    col = 0
                    row += 1

    def _select_category(self, item: dict) -> None:
        name = str(item.get("name", ""))
        cid = str(item.get("id", ""))
        sort = str(item.get("sort_id", "5"))
        if not name:
            return
        self._category = name
        self._category_id = cid
        self._category_sort = sort
        for chip, it in self._category_items:
            chip.setChecked(str(it.get("id", "")) == cid)
        self.category_hint.setText(f"正在加载「{name}」分类歌单…")
        self.category_generation += 1
        gen = self.category_generation
        meta = self.platforms.get(self.platform) or {}
        provider = providers.REGISTRY.get(meta.get("provider") or self.platform)
        if provider is None:
            self.category_hint.setText("该来源不可用")
            return
        platform = str(meta.get("platform") or "")
        self._start("category", lambda: provider.recommend(
            limit=16, category=cid, category_id=cid,
            sort_id=sort, platform=platform), gen)

    def _fill_category(self, items: list[dict]) -> None:
        _clear_layout(self.cat_grid)
        self.category_hint.setText(f"「{self._category}」· {len(items)} 个歌单")
        for i, item in enumerate(items):
            tile = PlaylistTile(item, self._covers)
            tile.clicked.connect(self.open_playlist)
            self.cat_grid.addWidget(tile, i // 4, i % 4)

    # ------------------------------------------------------------------ detail
    def open_playlist(self, item: dict) -> None:
        self.stack.setCurrentIndex(1)
        self.detail_title.setText(item.get("title", "歌单"))
        self.detail_meta.setText(item.get("source_name", ""))
        self.detail_empty.setText("正在加载歌单…")
        self.detail_empty.setVisible(True)
        self.detail_scroll.setVisible(False)
        self.play_all_btn.setVisible(False)
        _clear_layout(self.detail_layout)
        self._rows = []
        self.detail_generation += 1
        gen = self.detail_generation
        provider = providers.REGISTRY.get(item.get("provider_id") or self.platform)
        self._start("detail", lambda: provider.playlist(item.get("id", "")) if provider else {}, gen)
        self._pending_info = item

    def open_chart(self, item: dict) -> None:
        self.stack.setCurrentIndex(1)
        self.detail_title.setText(item.get("title", "榜单"))
        self.detail_meta.setText(item.get("source_name", ""))
        self.detail_empty.setText("正在加载榜单…")
        self.detail_empty.setVisible(True)
        self.detail_scroll.setVisible(False)
        self.play_all_btn.setVisible(False)
        _clear_layout(self.detail_layout)
        self._rows = []
        self.detail_generation += 1
        gen = self.detail_generation
        provider = providers.REGISTRY.get(item.get("provider_id") or self.platform)
        self._start("detail", lambda: provider.chart(item.get("id", "")) if provider else {}, gen)
        self._pending_info = item

    @staticmethod
    def _metadata_only(track: dict) -> dict:
        """Discover tracks are display-only.

        The discover page never owns a stream binding: clicking a song asks the
        source resolver to search Listen1 / Buguyy / installed aggregators and
        play whichever returns a working URL.
        """
        drop = ("audio", "audio_from", "provider_id", "provider_track_id",
                "listen1_id", "stream_via", "audio_headers", "audio_path")
        return {k: v for k, v in dict(track).items() if k not in drop}

    def _fill_detail(self, data: dict) -> None:
        info = data.get("info") or {}
        tracks = [self._metadata_only(t) for t in (data.get("tracks") or [])]
        self._tracks = tracks
        self.detail_title.setText(info.get("title") or self.detail_title.text())
        self.detail_meta.setText(f"{info.get('source_name', '')} · {len(tracks)} 首")
        cover = info.get("cover") or (tracks[0].get("cover") if tracks else "")
        self._covers.get(cover, self._set_detail_cover)
        self.play_all_btn.setVisible(bool(tracks))
        # Building 100+ rows used to paint them all at y=0 for one frame.
        # Keep the scroll area hidden and updatable-off until the layout has
        # actually assigned each row its final geometry.
        self.detail_scroll.setUpdatesEnabled(False)
        self.detail_scroll.setVisible(False)
        _clear_layout(self.detail_layout)
        self._rows = []
        self.detail_empty.setVisible(not tracks)
        if not tracks:
            self.detail_empty.setText("这个歌单没有可播放的曲目。")
            self.detail_scroll.setUpdatesEnabled(True)
            return
        for i, track in enumerate(tracks, start=1):
            row = ResultRow(track, i, self._covers)
            row.playRequested = lambda t, items=tracks: self.playTrack.emit(
                {**t, "_collection": list(items)})
            row.queueRequested = self.queueTrack.emit
            row.favoriteRequested = self.favoriteTrack.emit
            row.playlistRequested = self.playlistTrack.emit
            row.matchBindingRequested = self.matchBindingRequested.emit
            row.set_match_bound(self._match_bound(track))
            row.set_favorite(self._row_is_fav(track))
            row.setVisible(False)
            self._rows.append(row)
            self.detail_layout.addWidget(row)
        self.detail_layout.addStretch(1)
        self.detail_layout.activate()
        self.detail_body.adjustSize()
        self.detail_scroll.setUpdatesEnabled(True)
        self.detail_scroll.setVisible(True)
        gen = self.detail_generation
        QTimer.singleShot(16, lambda g=gen, rows=list(self._rows): self._reveal_detail_rows(g, rows))

    def _reveal_detail_rows(self, generation: int, rows: list[ResultRow]) -> None:
        if generation != self.detail_generation:
            return
        # A 600+ track playlist cannot become visible in one go: the layout has
        # not assigned geometry yet and every row paints at y=0.  Reveal in
        # small chunks so each batch gets a real layout pass.
        chunk = 40
        for start in range(0, len(rows), chunk):
            batch = rows[start:start + chunk]
            QTimer.singleShot(16 + (start // chunk) * 8,
                              lambda g=generation, b=batch: self._reveal_chunk(g, b))

    def _reveal_chunk(self, generation: int, rows: list[ResultRow]) -> None:
        if generation != self.detail_generation:
            return
        for row in rows:
            row.setVisible(True)
        self.detail_layout.activate()
        self.detail_body.adjustSize()

    def _set_detail_cover(self, pix) -> None:
        if pix and not pix.isNull():
            self.detail_cover.setPixmap(rounded(pix, 78, 14))
        else:
            self.detail_cover.setPixmap(rounded(placeholder_art(78, 78, "歌单"), 78, 14))

    def _emit_play_all(self) -> None:
        if self._tracks:
            self.playAll.emit([dict(t) for t in self._tracks if not t.get("disabled")])

