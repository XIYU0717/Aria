"""Favourites and imported playlists."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from components import CoverCache, SectionHeading, human_time
from icons import make_icon, placeholder_art, rounded
from keys import match_key, song_key


class SongRow(QFrame):
    playRequested = Signal(dict)
    queueRequested = Signal(dict)
    favoriteRequested = Signal(dict)
    playlistRequested = Signal(dict)
    matchBindingRequested = Signal(dict)

    def __init__(self, track: dict, index: int, covers: CoverCache,
                 favorite: bool, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultRow")
        self.track = track
        self.setFixedHeight(62)
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 12, 8)
        row.setSpacing(12)

        num = QLabel(str(index + 1))
        num.setFixedWidth(22)
        num.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        num.setObjectName("IndexBadge")
        row.addWidget(num)

        cover = QLabel()
        cover.setFixedSize(44, 44)
        covers.get(track.get("cover", ""), lambda pix: self._set_cover(pix, cover, track))
        row.addWidget(cover)

        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(track.get("title", ""))
        title.setObjectName("TrackTitle")
        meta = QLabel(" · ".join(x for x in [track.get("artist", ""), track.get("album", "")] if x))
        meta.setObjectName("TrackMeta")
        col.addWidget(title)
        col.addWidget(meta)
        row.addLayout(col, 1)

        badge = QLabel(track.get("source_name", ""))
        badge.setObjectName("SourceBadge")
        badge.setAlignment(Qt.AlignCenter)
        row.addWidget(badge)

        dur = QLabel(human_time((track.get("duration") or 0) * 1000) if track.get("duration") else "整首")
        dur.setObjectName("TrackMeta")
        dur.setFixedWidth(54)
        dur.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(dur)

        self.fav_btn = QPushButton()
        self.fav_btn.setObjectName("FavBtn")
        self.fav_btn.setFixedSize(26, 26)
        self.fav_btn.setCursor(Qt.PointingHandCursor)
        self.fav_btn.clicked.connect(lambda: self.favoriteRequested.emit(self.track))
        row.addWidget(self.fav_btn)
        self.set_favorite(favorite)

        play = QPushButton()
        play.setObjectName("IconBtn")
        play.setFixedSize(32, 32)
        play.setIcon(make_icon("play", "#E9EAF2", 17))
        play.setToolTip("播放")
        play.setCursor(Qt.PointingHandCursor)
        play.clicked.connect(lambda: self.playRequested.emit(self.track))
        row.addWidget(play)

        self.playlist_btn = QPushButton()
        self.playlist_btn.setObjectName("IconBtn")
        self.playlist_btn.setFixedSize(30, 30)
        self.playlist_btn.setIcon(make_icon("note", "#8A8FA3", 16))
        self.playlist_btn.setToolTip("加入歌单")
        self.playlist_btn.setCursor(Qt.PointingHandCursor)
        self.playlist_btn.clicked.connect(lambda: self.playlistRequested.emit(self.track))
        row.addWidget(self.playlist_btn)

        self.bind_btn = QPushButton()
        self.bind_btn.setObjectName("IconBtn")
        self.bind_btn.setFixedSize(30, 30)
        self.bind_btn.setIcon(make_icon("gear", "#A6ACC2", 16))
        self.bind_btn.setToolTip("这首歌已绑定默认音源，点击可解绑或更换")
        self.bind_btn.setCursor(Qt.PointingHandCursor)
        self.bind_btn.setVisible(False)
        self.bind_btn.clicked.connect(lambda: self.matchBindingRequested.emit(self.track))
        row.addWidget(self.bind_btn)

        queue = QPushButton()
        queue.setObjectName("IconBtn")
        queue.setFixedSize(30, 30)
        queue.setIcon(make_icon("plus", "#8A8FA3", 16))
        queue.setToolTip("加入播放队列")
        queue.setCursor(Qt.PointingHandCursor)
        queue.clicked.connect(lambda: self.queueRequested.emit(self.track))
        row.addWidget(queue)

    def _set_cover(self, pix, label: QLabel, track: dict) -> None:
        if pix and not pix.isNull():
            label.setPixmap(rounded(pix, 44, 10))
        else:
            label.setPixmap(rounded(placeholder_art(44, 44, track.get("title", "A")), 44, 10))

    def set_favorite(self, on: bool) -> None:
        self._fav = bool(on)
        color = "#FF2E55" if on else "#8A8FA3"
        self.fav_btn.setIcon(make_icon("heart_filled" if on else "heart", color, 14))
        self.fav_btn.setToolTip("取消收藏" if on else "收藏到「我喜欢」")
        self.fav_btn.setProperty("fav", "1" if on else "0")
        self.fav_btn.style().unpolish(self.fav_btn)
        self.fav_btn.style().polish(self.fav_btn)

    def is_favorite(self) -> bool:
        return bool(getattr(self, "_fav", False))

    def set_match_bound(self, bound: bool) -> None:
        self.bind_btn.setVisible(bool(bound))

    def mouseDoubleClickEvent(self, event) -> None:
        self.playRequested.emit(self.track)
        super().mouseDoubleClickEvent(event)


class _ListBase(QWidget):
    playTrack = Signal(dict)
    playAll = Signal(list)
    queueTrack = Signal(dict)
    favoriteTrack = Signal(dict)
    playlistTrack = Signal(dict)
    matchBindingRequested = Signal(dict)

    def __init__(self, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self._covers = covers
        self._rows: list[SongRow] = []
        self._tracks: list[dict] = []
        self._match_keys: set[str] = set()
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 24, 34, 16)
        root.setSpacing(12)
        self.root = root

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.list_layout = QVBoxLayout(self.body)
        self.list_layout.setContentsMargins(0, 4, 8, 8)
        self.list_layout.setSpacing(2)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        self.empty = QLabel("")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("Muted")
        root.addWidget(self.empty, 1)

    def _clear(self) -> None:
        self._rows = []
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _fill(self, tracks: list[dict], fav_keys: set[str]) -> None:
        self._clear()
        self._tracks = list(tracks or [])
        self.empty.setVisible(not tracks)
        self.scroll.setVisible(bool(tracks))
        for index, track in enumerate(tracks):
            row = SongRow(track, index, self._covers, song_key(track) in fav_keys)
            row.playRequested.connect(self.playTrack.emit)
            row.queueRequested.connect(self.queueTrack.emit)
            row.favoriteRequested.connect(self.favoriteTrack.emit)
            row.playlistRequested.connect(self.playlistTrack.emit)
            row.matchBindingRequested.connect(self.matchBindingRequested.emit)
            row.set_match_bound(match_key(track) in self._match_keys)
            self.list_layout.addWidget(row)
            self._rows.append(row)
        self.list_layout.addStretch(1)

    def set_match_bindings(self, keys: set[str]) -> None:
        self._match_keys = set(keys or ())
        for row in self._rows:
            row.set_match_bound(match_key(row.track) in self._match_keys)

    def _emit_play_all(self) -> None:
        if self._tracks:
            self.playAll.emit([dict(t) for t in self._tracks if not t.get("disabled")])


class FavoritesPage(_ListBase):
    def __init__(self, covers: CoverCache, parent=None) -> None:
        super().__init__(covers, parent)
        head = QHBoxLayout()
        head.addWidget(SectionHeading("我喜欢", "收藏过的歌会跨来源同步显示红心，播放时自动挑选最优音源"))
        head.addStretch(1)
        self.play_all = QPushButton("全部播放")
        self.play_all.setObjectName("Primary")
        self.play_all.setIcon(make_icon("play", "#FFFFFF", 14))
        self.play_all.setVisible(False)
        self.play_all.clicked.connect(self._emit_play_all)
        head.addWidget(self.play_all)
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        head.addWidget(self.count)
        self.root.insertLayout(0, head)
        self.empty.setText("还没有收藏。在搜索结果或歌单里点一下爱心即可加入。")

    def set_tracks(self, tracks: list[dict], fav_keys: set[str]) -> None:
        self.count.setText(f"{len(tracks)} 首")
        self.play_all.setVisible(bool(tracks))
        self._fill(tracks, fav_keys)


class PlaylistCard(QFrame):
    clicked = Signal(str)

    def __init__(self, playlist: dict, covers: CoverCache, selected: bool, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PlaylistCard")
        self.setProperty("selected", "1" if selected else "0")
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.playlist_id = playlist["id"]
        self.setFixedHeight(62)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 7, 10, 7)
        row.setSpacing(10)
        self.cover = QLabel()
        self.cover.setFixedSize(46, 46)
        covers.get(playlist.get("cover", ""), self._set_cover)
        row.addWidget(self.cover)
        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(playlist.get("name", ""))
        name.setObjectName("TrackTitle")
        count = QLabel(f"{playlist.get('count', 0)} 首 · {playlist.get('platform', '歌单')}")
        count.setObjectName("TrackMeta")
        col.addWidget(name)
        col.addWidget(count)
        row.addLayout(col, 1)

    def _set_cover(self, pix) -> None:
        if pix and not pix.isNull():
            self.cover.setPixmap(rounded(pix, 46, 11))
        else:
            self.cover.setPixmap(rounded(placeholder_art(46, 46, self.playlist_id, "#8B7BFF"), 46, 11))

    def mouseReleaseEvent(self, event) -> None:
        self.clicked.emit(self.playlist_id)
        super().mouseReleaseEvent(event)


class PlaylistsPage(QWidget):
    LOAD_STEP = 30
    RENDER_AHEAD = 5
    RANDOM_RENDER_EVERY = 5

    playTrack = Signal(dict)
    queueTrack = Signal(dict)
    favoriteTrack = Signal(dict)
    playlistTrack = Signal(dict)
    playAll = Signal(list)
    renameRequested = Signal(str, str)
    shareRequested = Signal(str)
    deleteRequested = Signal(str)
    importRequested = Signal()
    createRequested = Signal()
    playlistSelected = Signal(str)
    matchBindingRequested = Signal(dict)

    def __init__(self, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self._covers = covers
        self._playlists: list[dict] = []
        self._selected = ""
        self._rows: list[SongRow] = []
        self._tracks: list[dict] = []
        self._fav_keys: set[str] = set()
        self._rendered = 0
        self._played_since_render = 0
        self._match_keys: set[str] = set()
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 24, 34, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.addWidget(SectionHeading("我的歌单", "新建本地歌单，或从其他音乐 app 的歌单链接导入"))
        head.addStretch(1)
        self.create_btn = QPushButton("新建歌单")
        self.create_btn.setObjectName("Ghost")
        self.create_btn.setIcon(make_icon("plus", "#E9EAF2", 15))
        self.create_btn.clicked.connect(self.createRequested.emit)
        head.addWidget(self.create_btn)
        self.import_btn = QPushButton("导入歌单")
        self.import_btn.setObjectName("Primary")
        self.import_btn.setIcon(make_icon("plus", "#FFFFFF", 15))
        self.import_btn.clicked.connect(self.importRequested.emit)
        head.addWidget(self.import_btn)
        root.addLayout(head)

        split = QHBoxLayout()
        split.setSpacing(14)
        self.left = QFrame()
        self.left.setObjectName("Card")
        self.left.setFixedWidth(280)
        left_col = QVBoxLayout(self.left)
        left_col.setContentsMargins(10, 10, 10, 10)
        left_col.setSpacing(4)
        self.playlist_scroll = QScrollArea()
        self.playlist_scroll.setWidgetResizable(True)
        self.playlist_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.playlist_body = QWidget()
        self.playlist_layout = QVBoxLayout(self.playlist_body)
        self.playlist_layout.setContentsMargins(0, 0, 4, 0)
        self.playlist_layout.setSpacing(3)
        self.playlist_scroll.setWidget(self.playlist_body)
        left_col.addWidget(self.playlist_scroll)
        self.left_empty = QLabel("还没有歌单")
        self.left_empty.setObjectName("Muted")
        self.left_empty.setAlignment(Qt.AlignCenter)
        left_col.addWidget(self.left_empty)
        split.addWidget(self.left)

        self.right = QFrame()
        self.right.setObjectName("Card")
        right_col = QVBoxLayout(self.right)
        right_col.setContentsMargins(16, 12, 16, 12)
        right_col.setSpacing(8)
        header = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setFixedSize(64, 64)
        header.addWidget(self.cover)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.name = QLabel("选择一个歌单")
        self.name.setStyleSheet("color:#FFFFFF;font-size:17px;font-weight:700;")
        self.meta = QLabel("")
        self.meta.setObjectName("Muted")
        info.addWidget(self.name)
        info.addWidget(self.meta)
        header.addLayout(info, 1)
        self.play_all = QPushButton("全部播放")
        self.play_all.setObjectName("Ghost")
        self.play_all.setIcon(make_icon("play", "#E9EAF2", 14))
        self.play_all.setVisible(False)
        header.addWidget(self.play_all)
        self.rename = QPushButton("改名")
        self.rename.setObjectName("Ghost")
        self.rename.setIcon(make_icon("edit", "#E9EAF2", 14))
        self.rename.setVisible(False)
        header.addWidget(self.rename)
        self.share = QPushButton()
        self.share.setObjectName("IconBtn")
        self.share.setFixedSize(32, 32)
        self.share.setIcon(make_icon("share", "#8A8FA3", 16))
        self.share.setToolTip("分享歌单")
        self.share.setVisible(False)
        header.addWidget(self.share)
        self.delete = QPushButton()
        self.delete.setObjectName("IconBtn")
        self.delete.setFixedSize(32, 32)
        self.delete.setIcon(make_icon("trash", "#8A8FA3", 16))
        self.delete.setToolTip("删除歌单")
        self.delete.setVisible(False)
        header.addWidget(self.delete)
        right_col.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.list_layout = QVBoxLayout(self.body)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(2)
        self.scroll.setWidget(self.body)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_tracks_scroll)
        right_col.addWidget(self.scroll, 1)
        self.empty = QLabel("还没有导入歌单，点右上角「导入歌单」粘贴分享链接")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        right_col.addWidget(self.empty, 1)
        split.addWidget(self.right, 1)
        root.addLayout(split, 1)

        self.play_all.clicked.connect(self._emit_play_all)
        self.rename.clicked.connect(self._rename)
        self.share.clicked.connect(self._share)
        self.delete.clicked.connect(self._delete)

    # ------------------------------------------------------------------ data
    def set_playlists(self, playlists: list[dict]) -> None:
        keep = self._selected
        self._playlists = list(playlists)
        while self.playlist_layout.count():
            item = self.playlist_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        ids = {p["id"] for p in playlists}
        if keep not in ids:
            self._selected = playlists[0]["id"] if playlists else ""
        self.left_empty.setVisible(not playlists)
        self.playlist_scroll.setVisible(bool(playlists))
        for playlist in playlists:
            card = PlaylistCard(playlist, self._covers, playlist["id"] == self._selected)
            card.clicked.connect(self._select)
            self.playlist_layout.addWidget(card)
        self.playlist_layout.addStretch(1)
        if self._selected:
            self._select(self._selected)
        else:
            self._clear_tracks()

    def selected(self) -> str:
        return self._selected

    def select_playlist(self, playlist_id: str) -> None:
        if playlist_id and any(p["id"] == playlist_id for p in self._playlists):
            self._select(playlist_id)

    def _clear_tracks(self) -> None:
        self._tracks = []
        self._fav_keys = set()
        self._rows = []
        self._rendered = 0
        self._played_since_render = 0
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.name.setText("选择一个歌单")
        self.meta.setText("")
        self.play_all.setVisible(False)
        self.rename.setVisible(False)
        self.share.setVisible(False)
        self.delete.setVisible(False)
        self.scroll.setVisible(False)
        self.empty.setVisible(True)
        self.empty.setText("还没有导入歌单，去左下角或右上角「导入歌单」粘贴分享链接")

    def _select(self, playlist_id: str) -> None:
        self._selected = playlist_id
        for i in range(self.playlist_layout.count()):
            widget = self.playlist_layout.itemAt(i).widget()
            if isinstance(widget, PlaylistCard):
                widget.setProperty("selected", "1" if widget.playlist_id == playlist_id else "0")
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        playlist = next((p for p in self._playlists if p["id"] == playlist_id), None)
        if not playlist:
            return
        self.name.setText(playlist.get("name", "未命名歌单"))
        self.meta.setText(f"{playlist.get('count', 0)} 首 · 来源 {playlist.get('platform', '其他音乐 app')}")
        self._covers.get(playlist.get("cover", ""), self._set_cover)
        self.play_all.setVisible(True)
        self.rename.setVisible(True)
        self.share.setVisible(True)
        self.delete.setVisible(True)
        self.playlistSelected.emit(playlist_id)

    def _set_cover(self, pix) -> None:
        if pix and not pix.isNull():
            self.cover.setPixmap(rounded(pix, 64, 14))
        else:
            self.cover.setPixmap(rounded(placeholder_art(64, 64, self._selected, "#8B7BFF"), 64, 14))

    def set_tracks(self, tracks: list[dict], fav_keys: set[str]) -> None:
        self._tracks = list(tracks)
        self._fav_keys = set(fav_keys or ())
        self._rows = []
        self._rendered = 0
        self._played_since_render = 0
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.empty.setVisible(not self._tracks)
        self.scroll.setVisible(bool(self._tracks))
        bar = self.scroll.verticalScrollBar()
        bar.blockSignals(True)
        bar.setValue(0)
        bar.blockSignals(False)
        if self._tracks:
            self._render_more()

    def _render_more(self) -> None:
        """Append the next page of songs instead of building the whole list."""
        if self._rendered >= len(self._tracks):
            return
        # Drop the trailing stretch while rows are appended, then restore it.
        if self.list_layout.count():
            item = self.list_layout.itemAt(self.list_layout.count() - 1)
            if item.spacerItem():
                self.list_layout.takeAt(self.list_layout.count() - 1)
        end = min(self._rendered + self.LOAD_STEP, len(self._tracks))
        tracks = self._tracks
        for index in range(self._rendered, end):
            track = tracks[index]
            row = SongRow(track, index, self._covers,
                          song_key(track) in self._fav_keys)
            row.playRequested.connect(
                lambda t, items=tracks: self.playTrack.emit(
                    {**t, "_collection": list(items), "_match": True,
                     "needs_match": True}))
            row.queueRequested.connect(self.queueTrack.emit)
            row.favoriteRequested.connect(self.favoriteTrack.emit)
            row.playlistRequested.connect(self.playlistTrack.emit)
            row.matchBindingRequested.connect(self.matchBindingRequested.emit)
            row.set_match_bound(match_key(track) in self._match_keys)
            self.list_layout.addWidget(row)
            self._rows.append(row)
        self._rendered = end
        self.list_layout.addStretch(1)

    def _on_tracks_scroll(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        if (self._rendered < len(self._tracks)
                and value >= bar.maximum() - 120):
            self._render_more()

    def note_playback(self, track: dict, mode: str) -> None:
        """Advance the rendered page as playback moves through the playlist."""
        if not self._tracks or not isinstance(track, dict):
            return
        track_id = str(track.get("id") or "")
        key = song_key(track)
        index = next((i for i, item in enumerate(self._tracks)
                      if (track_id and str(item.get("id") or "") == track_id)
                      or song_key(item) == key), None)
        if index is None:
            return
        if mode in ("list_shuffle", "pure_random"):
            # Shuffle has no list position to watch, so append in batches.
            if self._rendered >= len(self._tracks):
                return
            self._played_since_render += 1
            if self._played_since_render >= self.RANDOM_RENDER_EVERY:
                self._played_since_render = 0
                self._render_more()
            return
        # Sequential playback: fetch the next page five songs before the end.
        if index >= self._rendered - self.RENDER_AHEAD:
            self._render_more()

    def set_match_bindings(self, keys: set[str]) -> None:
        self._match_keys = set(keys or ())
        for row in self._rows:
            row.set_match_bound(match_key(row.track) in self._match_keys)

    def _emit_play_all(self) -> None:
        if getattr(self, "_tracks", None):
            self.playAll.emit([{**t, "_match": True, "needs_match": True} for t in self._tracks])

    def _rename(self) -> None:
        playlist = next((p for p in self._playlists if p["id"] == self._selected), None)
        if not playlist:
            return
        name, ok = QInputDialog.getText(self, "重命名歌单", "歌单名称", text=playlist.get("name", ""))
        if ok and name.strip():
            self.renameRequested.emit(self._selected, name.strip())

    def _share(self) -> None:
        if self._selected:
            self.shareRequested.emit(self._selected)

    def _delete(self) -> None:
        playlist = next((p for p in self._playlists if p["id"] == self._selected), None)
        if not playlist:
            return
        answer = QMessageBox.question(self, "删除歌单", f"确定删除「{playlist.get('name', '')}」吗？")
        if answer == QMessageBox.Yes:
            self.deleteRequested.emit(self._selected)
