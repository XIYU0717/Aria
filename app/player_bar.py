"""Bottom now-playing bar."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

import theme
from components import ClickableLabel, CoverCache, SeekSlider, human_time
from icons import make_icon, placeholder_art, rounded


class PlayerBar(QFrame):
    toggleRequested = Signal()
    prevRequested = Signal()
    nextRequested = Signal()
    seekRequested = Signal(int)
    volumeChanged = Signal(int)
    immersiveRequested = Signal()
    queueRequested = Signal()
    modeRequested = Signal()

    def __init__(self, covers: CoverCache, volume: int = 65, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.setFixedHeight(88)
        self._covers = covers
        self._track: dict = {}
        self._dragging = False

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 10, 20, 10)
        row.setSpacing(14)

        self.cover = ClickableLabel()
        self.cover.setFixedSize(54, 54)
        self.cover.setCursor(Qt.PointingHandCursor)
        self.cover.setToolTip("点击查看歌词")
        self.cover.clicked.connect(self.immersiveRequested.emit)
        self._set_placeholder()
        row.addWidget(self.cover)

        info = QVBoxLayout()
        info.setSpacing(3)
        self.title = QLabel("还没有播放")
        self.title.setObjectName("NowTitle")
        self.title.setFixedWidth(190)
        self.meta = QLabel("从搜索结果选一首开始")
        self.meta.setObjectName("NowMeta")
        self.meta.setFixedWidth(190)
        info.addWidget(self.title)
        info.addWidget(self.meta)
        row.addLayout(info)

        self.prev_btn = self._icon_button("prev", 18, 34, "上一首")
        self.prev_btn.clicked.connect(self.prevRequested.emit)
        row.addWidget(self.prev_btn)

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("PlayCircle")
        self.play_btn.setFixedSize(50, 50)
        self.play_btn.setIcon(make_icon("play", "#FFFFFF", 22))
        self.play_btn.setIconSize(QSize(22, 22))
        self.play_btn.setToolTip("播放 / 暂停")
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.clicked.connect(self.toggleRequested.emit)
        row.addWidget(self.play_btn)

        self.next_btn = self._icon_button("next", 18, 34, "下一首")
        self.next_btn.clicked.connect(self.nextRequested.emit)
        row.addWidget(self.next_btn)

        self.time_now = QLabel("0:00")
        self.time_now.setObjectName("TimeLabel")
        self.time_now.setFixedWidth(40)
        self.time_now.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.time_now)

        self.slider = SeekSlider()
        self.slider.setObjectName("Seek")
        self.slider.setRange(0, 1)
        self.slider.sliderPressed.connect(self._on_slider_press)
        self.slider.sliderReleased.connect(self._on_slider_release)
        self.slider.sliderMoved.connect(lambda v: self.time_now.setText(human_time(v)))
        self.slider.setMinimumWidth(180)
        row.addWidget(self.slider, 1)

        self.time_total = QLabel("0:00")
        self.time_total.setObjectName("TimeLabel")
        self.time_total.setFixedWidth(40)
        row.addWidget(self.time_total)

        self.status = QLabel("")
        self.status.setObjectName("PlayStatus")
        self.status.setMaximumWidth(190)
        self.status.setVisible(False)
        row.addWidget(self.status)

        self.source = QLabel("")
        self.source.setObjectName("SourceBadge")
        self.source.setAlignment(Qt.AlignCenter)
        self.source.setVisible(False)
        row.addWidget(self.source)

        self.vol_btn = self._icon_button("volume", 16, 30, "静音")
        self.vol_btn.clicked.connect(self._mute)
        row.addWidget(self.vol_btn)
        self.volume = SeekSlider()
        self.volume.setObjectName("Volume")
        self.volume.setRange(0, 100)
        self.volume.setValue(volume)
        self.volume.setFixedWidth(86)
        self.volume.setCursor(Qt.PointingHandCursor)
        self.volume.valueChanged.connect(self._on_volume)
        row.addWidget(self.volume)

        self.immerse_btn = self._icon_button("expand", 17, 32, "沉浸歌词模式")
        self.immerse_btn.clicked.connect(self.immersiveRequested.emit)
        row.addWidget(self.immerse_btn)

        self.mode_btn = self._icon_button("repeat", 17, 32, "播放模式：列表循环")
        self.mode_btn.clicked.connect(self.modeRequested.emit)
        row.addWidget(self.mode_btn)

        self.queue_btn = self._icon_button("list", 17, 32, "播放队列")
        self.queue_btn.clicked.connect(self.queueRequested.emit)
        row.addWidget(self.queue_btn)

        self._last_volume = volume

    # ------------------------------------------------------------------ helpers
    def _icon_button(self, kind: str, icon_size: int, size: int, tip: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("IconBtn")
        btn.setFixedSize(size, size)
        btn.setIcon(make_icon(kind, "#C9CCD8", icon_size))
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def _set_placeholder(self) -> None:
        art = placeholder_art(54, 54, "♪", theme.ACCENT)
        self.cover.setPixmap(rounded(art, 54, 13))

    def _elide(self, text: str, label: QLabel) -> str:
        metrics = QFontMetrics(label.font())
        return metrics.elidedText(text, Qt.ElideRight, label.width() - 2)

    # ------------------------------------------------------------------ data
    def set_track(self, track: dict) -> None:
        self._track = track
        self.clear_status()
        self.title.setText(self._elide(track.get("title", ""), self.title))
        artist = track.get("artist", "")
        album = track.get("album", "")
        bits = [x for x in [artist, album] if x]
        if track.get("is_preview"):
            bits.append("30 秒试听")
        self.meta.setText(self._elide(" · ".join(bits), self.meta))
        self.title.setToolTip(track.get("title", ""))
        self.source.setText(track.get("source_name", ""))
        self.source.setVisible(bool(track.get("source_name")))
        self._set_placeholder()
        self._covers.get(track.get("cover", ""), self._on_cover)
        self.slider.setRange(0, 1)
        self.time_now.setText("0:00")
        self.time_total.setText("直播" if track.get("is_live") else "0:00")
        self.slider.setEnabled(not track.get("is_live"))

    def _on_cover(self, pix) -> None:
        if pix and not pix.isNull():
            self.cover.setPixmap(rounded(pix, 54, 13))

    def set_state(self, playing: bool) -> None:
        self.play_btn.setIcon(make_icon("pause" if playing else "play", "#FFFFFF", 22))

    def set_status(self, text: str, error: bool = False) -> None:
        text = (text or "").strip()
        if not text:
            self.clear_status()
            return
        color = theme.DANGER if error else theme.ACCENT
        self.status.setStyleSheet(
            f"color:{color};font-size:12px;font-weight:600;background:transparent;")
        self.status.setText(text)
        self.status.setToolTip(text)
        self.status.setVisible(True)

    def clear_status(self) -> None:
        self.status.clear()
        self.status.setToolTip("")
        self.status.setVisible(False)

    def set_mode(self, mode: str) -> None:
        meta = {
            "list_loop": ("repeat", "列表循环"),
            "single_loop": ("repeat_one", "单曲循环"),
            "list_shuffle": ("shuffle", "列表随机"),
            "pure_random": ("dice", "纯随机"),
        }
        icon, label = meta.get(mode, meta["list_loop"])
        self.mode_btn.setIcon(make_icon(icon, "#C9CCD8", 17))
        self.mode_btn.setToolTip(f"播放模式：{label}")

    def set_position(self, ms: int) -> None:
        if self._dragging:
            return
        if self.slider.maximum() > 1:
            self.slider.blockSignals(True)
            self.slider.setValue(min(ms, self.slider.maximum()))
            self.slider.blockSignals(False)
        self.time_now.setText(human_time(ms))

    def set_duration(self, ms: int) -> None:
        if ms <= 0:
            return
        self.slider.setRange(0, ms)
        self.time_total.setText(human_time(ms))
        self.slider.setEnabled(True)

    def set_volume(self, value: int) -> None:
        self.volume.blockSignals(True)
        self.volume.setValue(value)
        self.volume.blockSignals(False)
        self.vol_btn.setIcon(make_icon("volume" if value > 0 else "mute", "#C9CCD8", 16))
        if value > 0:
            self._last_volume = value

    # ------------------------------------------------------------------ events
    def _on_slider_press(self) -> None:
        self._dragging = True

    def _on_slider_release(self) -> None:
        self._dragging = False
        self.seekRequested.emit(self.slider.value())

    def _on_volume(self, value: int) -> None:
        self.vol_btn.setIcon(make_icon("volume" if value > 0 else "mute", "#C9CCD8", 16))
        if value > 0:
            self._last_volume = value
        self.volumeChanged.emit(value)

    def _mute(self) -> None:
        if self.volume.value() > 0:
            self.volume.setValue(0)
        else:
            self.volume.setValue(self._last_volume or 60)
