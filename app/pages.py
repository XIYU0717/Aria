"""Queue and settings pages."""

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import theme
from components import CoverCache, SectionHeading, human_time
from db import DATA_DIR
from icons import make_icon, placeholder_art, rounded
from keys import match_key
from media_cache import AudioCache, format_bytes

VERSION = "1.7.2"


class QueueRow(QFrame):
    playRequested = Signal(int)
    removeRequested = Signal(int)
    playlistRequested = Signal(dict)
    matchBindingRequested = Signal(dict)

    def __init__(self, track: dict, index: int, covers: CoverCache,
                 current: bool, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultRow")
        self.setFixedHeight(64)
        self.setCursor(Qt.PointingHandCursor)
        self.index = index
        self.track = track
        self._is_current = bool(current)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(12)

        self.num = QLabel("▶" if current else str(index + 1))
        self.num.setFixedWidth(24)
        self.num.setAlignment(Qt.AlignCenter)
        self.num.setStyleSheet(f"color:{theme.ACCENT if current else theme.TEXT_DIM};font-size:12px;")
        row.addWidget(self.num)

        cover = QLabel()
        cover.setFixedSize(44, 44)
        covers.get(track.get("cover", ""), lambda pix: self._set_cover(pix, cover, track))
        row.addWidget(cover)

        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(track.get("title", ""))
        title.setObjectName("TrackTitle")
        meta = QLabel(" · ".join([x for x in [track.get("artist", ""), track.get("album", "")] if x]))
        meta.setObjectName("TrackMeta")
        col.addWidget(title)
        col.addWidget(meta)
        row.addLayout(col, 1)

        badge = QLabel(track.get("source_name", ""))
        badge.setObjectName("SourceBadge")
        badge.setAlignment(Qt.AlignCenter)
        row.addWidget(badge)

        if track.get("is_live"):
            dur_text = "直播"
        elif track.get("is_preview"):
            dur_text = "试听 30s"
        elif not track.get("duration"):
            dur_text = "整首"
        else:
            dur_text = human_time((track.get("duration") or 0) * 1000)
        dur = QLabel(dur_text)
        dur.setObjectName("TrackMeta")
        dur.setFixedWidth(58)
        dur.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(dur)

        remove = QPushButton()
        remove.setObjectName("IconBtn")
        remove.setFixedSize(30, 30)
        remove.setIcon(make_icon("trash", "#8A8FA3", 15))
        remove.setToolTip("移出队列")
        remove.clicked.connect(lambda: self.removeRequested.emit(self.index))
        row.addWidget(remove)

        playlist = QPushButton()
        playlist.setObjectName("IconBtn")
        playlist.setFixedSize(30, 30)
        playlist.setIcon(make_icon("note", "#8A8FA3", 16))
        playlist.setToolTip("加入歌单")
        playlist.setCursor(Qt.PointingHandCursor)
        playlist.clicked.connect(lambda: self.playlistRequested.emit(self.track))
        row.addWidget(playlist)

        self.bind_btn = QPushButton()
        self.bind_btn.setObjectName("IconBtn")
        self.bind_btn.setFixedSize(30, 30)
        self.bind_btn.setIcon(make_icon("gear", "#A6ACC2", 16))
        self.bind_btn.setToolTip("这首歌已绑定默认音源，点击可解绑或更换")
        self.bind_btn.setCursor(Qt.PointingHandCursor)
        self.bind_btn.setVisible(False)
        self.bind_btn.clicked.connect(lambda: self.matchBindingRequested.emit(self.track))
        row.addWidget(self.bind_btn)

    def set_match_bound(self, bound: bool) -> None:
        self.bind_btn.setVisible(bool(bound))

    def set_current(self, current: bool) -> None:
        """Move the ▶ marker without rebuilding the whole queue list."""
        if bool(current) == self._is_current:
            return
        self._is_current = bool(current)
        self.num.setText("▶" if current else str(self.index + 1))
        self.num.setStyleSheet(
            f"color:{theme.ACCENT if current else theme.TEXT_DIM};font-size:12px;")

    def _set_cover(self, pix, label: QLabel, track: dict) -> None:
        if pix and not pix.isNull():
            label.setPixmap(rounded(pix, 44, 10))
        else:
            label.setPixmap(rounded(placeholder_art(44, 44, track.get("title", "A")), 44, 10))

    def mouseReleaseEvent(self, event) -> None:
        child = self.childAt(event.position().toPoint())
        if isinstance(child, QPushButton):
            super().mouseReleaseEvent(event)
            return
        self.playRequested.emit(self.index)
        super().mouseReleaseEvent(event)


class QueuePage(QWidget):
    playRequested = Signal(int)
    removeRequested = Signal(int)
    clearRequested = Signal()
    playlistTrack = Signal(dict)
    matchBindingRequested = Signal(dict)

    def __init__(self, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self._covers = covers
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 24, 34, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(SectionHeading("播放队列", "双击任意一首开始播放，队列会自动保存"))
        head.addStretch(1)
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        head.addWidget(self.count)
        clear = QPushButton("清空队列")
        clear.setObjectName("Ghost")
        clear.setIcon(make_icon("trash", "#E9EAF2", 15))
        clear.clicked.connect(self.clearRequested.emit)
        head.addWidget(clear)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.list_layout = QVBoxLayout(self.body)
        self.list_layout.setContentsMargins(0, 4, 8, 4)
        self.list_layout.setSpacing(2)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        self.empty = QLabel("队列还是空的，从搜索结果里添加几首吧")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("Muted")
        self.empty_box = QWidget()
        empty_layout = QVBoxLayout(self.empty_box)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.addStretch(1)
        empty_layout.addWidget(self.empty)
        empty_layout.addStretch(1)
        self.empty_box.setVisible(False)
        root.addWidget(self.empty_box, 1)

        self._rows: list[QueueRow] = []
        self._match_keys: set[str] = set()
        self._pending: list[dict] = []
        self._pending_index = 0
        self._current_id = ""
        self._build_token = 0
        self._build_timer = QTimer(self)
        self._build_timer.setSingleShot(True)
        self._build_timer.setInterval(0)
        self._build_timer.timeout.connect(self._build_batch)

    def set_queue(self, tracks: list[dict], current_id: str = "") -> None:
        """Replace the queue without blocking the GUI on huge playlists.

        Rebuilding one row per track used to freeze the window for a noticeable
        moment when a playlist carried hundreds of songs.  The first screenful
        is created immediately and the rest arrives in small idle-time batches.
        """
        self._build_token += 1
        token = self._build_token
        self._build_timer.stop()
        self._rows = []
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.count.setText(f"{len(tracks)} 首")
        self.empty.setVisible(not tracks)
        self.empty_box.setVisible(not tracks)
        self.scroll.setVisible(bool(tracks))
        self._pending = list(tracks)
        self._pending_index = 0
        self._current_id = str(current_id or "")
        if tracks:
            self._build_batch(token)

    def _build_batch(self, token: int | None = None) -> None:
        if token is not None and token != self._build_token:
            return
        batch = self._pending[self._pending_index:self._pending_index + 32]
        start = self._pending_index
        self._pending_index += len(batch)
        for offset, track in enumerate(batch):
            i = start + offset
            row = QueueRow(track, i, self._covers, self._row_is_current(track))
            row.playRequested.connect(self.playRequested.emit)
            row.removeRequested.connect(self.removeRequested.emit)
            row.playlistRequested.connect(self.playlistTrack.emit)
            row.matchBindingRequested.connect(self.matchBindingRequested.emit)
            row.set_match_bound(match_key(track) in self._match_keys)
            self.list_layout.addWidget(row)
            self._rows.append(row)
        if self._pending_index < len(self._pending):
            self._build_timer.start()
            return
        self.list_layout.addStretch(1)

    def set_match_bindings(self, keys: set[str]) -> None:
        self._match_keys = set(keys or ())
        for row in self._rows:
            row.set_match_bound(match_key(row.track) in self._match_keys)

    def _row_is_current(self, track: dict) -> bool:
        return bool(self._current_id) and str(track.get("id") or "") == self._current_id

    def set_current(self, current_id: str) -> None:
        """Highlight the playing row without touching any other row."""
        self._current_id = str(current_id or "")
        for row in self._rows:
            row.set_current(self._row_is_current(row.track))


class SettingsPage(QWidget):
    settingsChanged = Signal()
    dataChanged = Signal()

    def __init__(self, db, parent=None, cache: AudioCache | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.cache = cache or AudioCache(db)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(34, 24, 34, 16)
        root.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        root.addWidget(SectionHeading("设置", "检索、播放与缓存的偏好设置，保存后立即生效"))

        search_card = self._card("搜索")
        form_row = QHBoxLayout()
        form_row.setSpacing(12)
        label = QLabel("每个来源返回条数")
        label.setObjectName("Muted")
        form_row.addWidget(label)
        self.page_size = QComboBox()
        for value in (10, 15, 20, 25, 40, 50):
            self.page_size.addItem(str(value), value)
        current = self.db.get_int("page_size", 25)
        index = self.page_size.findData(current)
        self.page_size.setCurrentIndex(index if index >= 0 else 3)
        form_row.addWidget(self.page_size)
        form_row.addStretch(1)
        search_card.layout().addLayout(form_row)

        proxy_row = QHBoxLayout()
        proxy_row.setSpacing(12)
        proxy_label = QLabel("HTTP 代理（可选）")
        proxy_label.setObjectName("Muted")
        proxy_row.addWidget(proxy_label)
        self.proxy = QLineEdit(self.db.get_setting("proxy", "") or "")
        self.proxy.setPlaceholderText("http://127.0.0.1:7890")
        self.proxy.setMinimumWidth(280)
        proxy_row.addWidget(self.proxy)
        proxy_row.addStretch(1)
        search_card.layout().addLayout(proxy_row)
        root.addWidget(search_card)

        cache_card = self._card("临时缓存")
        cache_path_row = QHBoxLayout()
        cache_path_row.setSpacing(10)
        cache_path_label = QLabel("临时文件存放目录")
        cache_path_label.setObjectName("Muted")
        cache_path_row.addWidget(cache_path_label)
        self.cache_dir = QLineEdit(str(self.cache.directory))
        self.cache_dir.setPlaceholderText("留空则使用应用安装盘下的 cache 目录")
        self.cache_dir.setMinimumWidth(320)
        cache_path_row.addWidget(self.cache_dir, 1)
        cache_browse = QPushButton("浏览")
        cache_browse.setObjectName("Ghost")
        cache_browse.clicked.connect(self._browse_cache_dir)
        cache_path_row.addWidget(cache_browse)
        cache_default = QPushButton("默认")
        cache_default.setObjectName("Ghost")
        cache_default.clicked.connect(lambda: self.cache_dir.setText(str(self.cache.default_dir)))
        cache_path_row.addWidget(cache_default)
        cache_card.layout().addLayout(cache_path_row)

        cache_limit_row = QHBoxLayout()
        cache_limit_row.setSpacing(10)
        cache_limit_label = QLabel("音乐缓存上限")
        cache_limit_label.setObjectName("Muted")
        cache_limit_row.addWidget(cache_limit_label)
        self.cache_limit = QSpinBox()
        self.cache_limit.setRange(1, 10000)
        self.cache_limit.setSuffix(" 首")
        self.cache_limit.setValue(self.cache.limit)
        self.cache_limit.setFixedWidth(120)
        cache_limit_row.addWidget(self.cache_limit)
        cache_limit_hint = QLabel("超出上限时自动删除最久未播放的缓存")
        cache_limit_hint.setObjectName("Muted")
        cache_limit_row.addWidget(cache_limit_hint)
        cache_limit_row.addStretch(1)
        cache_card.layout().addLayout(cache_limit_row)

        cache_stats_row = QHBoxLayout()
        self.cache_stats = QLabel("")
        self.cache_stats.setObjectName("Muted")
        cache_stats_row.addWidget(self.cache_stats, 1)
        cache_open = QPushButton("打开目录")
        cache_open.setObjectName("Ghost")
        cache_open.clicked.connect(self._open_cache_dir)
        cache_stats_row.addWidget(cache_open)
        cache_clear = QPushButton("清空缓存")
        cache_clear.setObjectName("Ghost")
        cache_clear.clicked.connect(self._clear_cache)
        cache_stats_row.addWidget(cache_clear)
        cache_card.layout().addLayout(cache_stats_row)
        root.addWidget(cache_card)
        self.refresh_cache_info()

        data_card = self._card("数据与备份")
        path_row = QHBoxLayout()
        path_label = QLabel(f"数据目录：{DATA_DIR}")
        path_label.setObjectName("Muted")
        path_label.setWordWrap(True)
        path_row.addWidget(path_label, 1)
        open_btn = QPushButton("打开目录")
        open_btn.setObjectName("Ghost")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR))))
        path_row.addWidget(open_btn)
        data_card.layout().addLayout(path_row)

        clear_row = QHBoxLayout()
        clear_btn = QPushButton("清空播放历史")
        clear_btn.setObjectName("Ghost")
        clear_btn.clicked.connect(self._clear_history)
        clear_row.addWidget(clear_btn)
        clear_row.addStretch(1)
        data_card.layout().addLayout(clear_row)

        backup_row = QHBoxLayout()
        backup_hint = QLabel("备份「我喜欢」「我的歌单」「音乐站点」「默认音源匹配」与「默认歌词」，"
                             "换设备导入后即可找回这些数据")
        backup_hint.setObjectName("Muted")
        backup_row.addWidget(backup_hint, 1)
        export_btn = QPushButton("导出备份")
        export_btn.setObjectName("Primary")
        export_btn.setIcon(make_icon("note", "#FFFFFF", 15))
        export_btn.clicked.connect(self._export_backup)
        backup_row.addWidget(export_btn)
        import_btn = QPushButton("导入备份")
        import_btn.setObjectName("Ghost")
        import_btn.setIcon(make_icon("refresh", "#E9EAF2", 15))
        import_btn.clicked.connect(self.import_backup)
        backup_row.addWidget(import_btn)
        data_card.layout().addLayout(backup_row)
        root.addWidget(data_card)

        about_card = self._card("关于 Aria")
        about = QLabel(
            f"Aria {VERSION} · 本地桌面音乐聚合器\n"
            "参考 Folia 的深色卡片墙与歌词舞台设计。搜索接口全部在本地请求，不做账号登录、不上传数据。\n"
            "全新安装默认只有 Listen1；保留旧档案时继续沿用原有来源（含布谷音乐）。也可添加开源项目接入侧车或远程服务，或自行添加公开 JSON 接口。\n"
            "聚合源播放前会把整首音频缓存到本机，官方 API（iTunes / Audius 等）保持流式播放。"
            "音频版权与使用条款归原平台所有。"
        )
        about.setObjectName("Muted")
        about.setWordWrap(True)
        about_card.layout().addWidget(about)
        root.addWidget(about_card)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = QPushButton("保存设置")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self.save)
        save_row.addWidget(self.save_btn)
        root.addLayout(save_row)
        root.addStretch(1)

    def _card(self, title: str) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(18, 14, 18, 16)
        col.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        col.addWidget(label)
        return card

    # ------------------------------------------------------------------ cache
    def refresh_cache_info(self) -> None:
        try:
            count, total = self.cache.stats()
        except Exception:  # noqa: BLE001
            count, total = 0, 0
        self.cache_stats.setText(f"已缓存 {count} 首 · {format_bytes(total)}")

    def _browse_cache_dir(self) -> None:
        start = self.cache_dir.text().strip() or str(self.cache.default_dir)
        path = QFileDialog.getExistingDirectory(self, "选择临时文件存放目录", start)
        if path:
            self.cache_dir.setText(path)

    def _open_cache_dir(self) -> None:
        try:
            path = Path(self.cache_dir.text().strip() or str(self.cache.default_dir)).expanduser()
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "缓存目录", f"目录不可用：{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _clear_cache(self) -> None:
        answer = QMessageBox.question(
            self, "清空缓存",
            "确定删除所有已缓存的歌曲吗？删除后再次播放聚合源需要重新下载。")
        if answer == QMessageBox.Yes:
            self.cache.clear()
            self.refresh_cache_info()

    def _resolve_cache_path(self) -> str | None:
        text = self.cache_dir.text().strip()
        if not text:
            return ""
        path = Path(os.path.expanduser(text)).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
            if not path.is_dir():
                raise OSError("不是文件夹")
        except OSError as exc:
            QMessageBox.warning(self, "缓存目录", f"无法创建该目录：{exc}")
            return None
        return str(path)

    def save(self) -> None:
        cache_path = self._resolve_cache_path()
        if cache_path is None:
            return
        self.db.set_setting("page_size", str(self.page_size.currentData()))
        self.db.set_setting("proxy", self.proxy.text().strip())
        self.db.set_setting("cache_dir", cache_path)
        self.db.set_setting("cache_limit", str(self.cache_limit.value()))
        try:
            self.cache.enforce_limit()
        except Exception:
            pass
        self.cache_dir.setText(cache_path or str(self.cache.directory))
        self.refresh_cache_info()
        self.settingsChanged.emit()
        self.save_btn.setText("已保存")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1400, lambda: self.save_btn.setText("保存设置"))

    def _clear_history(self) -> None:
        answer = QMessageBox.question(self, "清空历史", "确定清空最近播放记录吗？")
        if answer == QMessageBox.Yes:
            self.db.clear_history()
            self.settingsChanged.emit()

    # ------------------------------------------------------------------ backup
    def _export_backup(self) -> None:
        default = str(DATA_DIR / f"Aria-备份-{time.strftime('%Y%m%d-%H%M')}.json")
        path, _ = QFileDialog.getSaveFileName(self, "导出备份", default, "Aria 备份 (*.json)")
        if not path:
            return
        try:
            payload = self.db.export_backup(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", f"没能写入备份文件：{exc}")
            return
        data = payload.get("data") or {}
        settings = data.get("settings") or {}
        try:
            bindings = json.loads(settings.get("match_overrides") or "{}")
            binding_count = len(bindings) if isinstance(bindings, dict) else 0
        except (TypeError, ValueError):
            binding_count = 0
        try:
            lyrics = json.loads(settings.get("lyric_overrides") or "{}")
            lyric_count = len(lyrics) if isinstance(lyrics, dict) else 0
        except (TypeError, ValueError):
            lyric_count = 0
        QMessageBox.information(
            self, "导出完成",
            f"已备份到：\n{path}\n\n"
            f"音乐站点 {len(data.get('sites') or [])} 个 · "
            f"我喜欢 {len(data.get('favorites') or [])} 首 · "
            f"歌单 {len(data.get('playlists') or [])} 个 · "
            f"默认音源匹配 {binding_count} 首 · "
            f"默认歌词 {lyric_count} 首")

    def import_backup(self, path: str = "") -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "导入备份", str(DATA_DIR), "Aria 备份 (*.json)")
        if not path:
            return
        mode = self._ask_import_mode()
        if not mode:
            return
        try:
            stats = self.db.import_backup(path, mode)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", f"备份文件无法导入：{exc}")
            return
        self.settingsChanged.emit()
        self.dataChanged.emit()
        label = "覆盖" if mode == "replace" else "合并"
        QMessageBox.information(
            self, "导入完成",
            f"已{label}导入：\n音乐站点 {stats['sites']} 个 · "
            f"我喜欢 {stats['favorites']} 首 · 歌单 {stats['playlists']} 个 · "
            f"默认音源匹配 {stats.get('bindings', 0)} 首 · "
            f"默认歌词 {stats.get('lyrics', 0)} 首 · 队列 {stats['queue']} 首")

    def _ask_import_mode(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("导入方式")
        box.setText("覆盖导入会先清空本机的音乐站点、我喜欢、我的歌单、默认音源匹配和默认歌词，"
                    "再写入备份内容；合并导入只补充缺失项，保留本机已有数据。")
        replace_btn = box.addButton("覆盖导入", QMessageBox.AcceptRole)
        merge_btn = box.addButton("合并导入", QMessageBox.ActionRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked == replace_btn:
            return "replace"
        if clicked == merge_btn:
            return "merge"
        return ""
