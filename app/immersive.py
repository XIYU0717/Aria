"""Immersive lyric view, inspired by Folia's full-screen lyric stage."""

import math
import random

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import theme
from components import PillButton, PillToggle, SeekSlider, blur_pixmap, human_time
from icons import make_icon

# Folia's own "classic" lyric template, matched to the official deep-blue
# geometric reference: serif display type, spaced/tilted words, low-contrast
# navy shapes, a cyan subtitle, and the black capsule transport.
FOLIA_ACCENT = QColor("#FFD54A")
AUTO_HIDE_MS = 3000
FOLIA_BG = QColor("#080C15")
FOLIA_SHAPE = QColor(18, 45, 66)
FOLIA_LINE = QColor(38, 73, 92)
FOLIA_MODES = (
    ("fume", "浮名"),
    ("classic", "流光"),
    ("cadenza", "心象"),
    ("cappella", "群唱"),
    ("partita", "云阶"),
    ("tilt", "倾诉"),
    ("claddagh", "回环"),
    ("diorama", "镜台"),
    ("hanabi", "花火"),
    ("lumiere", "绘光"),
    ("machi", "町景"),
    ("monet", "莫奈"),
    ("nebbia", "雾笺"),
    ("pendolo", "时计"),
    ("sonnet", "商籁"),
    ("still", "静止"),
    ("tempera", "凝彩"),
)
DEFAULT_FOLIA_MODE = "classic"


class LyricView(QWidget):
    seekRequested = Signal(int)
    toggleRequested = Signal()
    nextRequested = Signal()
    prevRequested = Signal()
    playlistRequested = Signal()
    modeRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lines: list[tuple[int, str]] = []
        self.translations: dict[int, str] = {}
        self.index = 0
        self.scroll = 0.0
        self.offset = 0.0
        self.position = 0
        self.message = "等待播放"
        self.source = ""
        self.folia = False
        self._folia_chrome = True
        self._folia_title = ""
        self._folia_artist = ""
        self._folia_duration = 0
        self._folia_playing = False
        self.accent = QColor("#A8BEFF")
        self._font_scale = 1.0
        self._line_density = 0.7
        self._anim_step = 0.16
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._follow = QTimer(self)
        self._follow.setSingleShot(True)
        self._follow.setInterval(3200)
        self._follow.timeout.connect(self._resume_follow)
        self.setMinimumHeight(240)
        self.setCursor(Qt.PointingHandCursor)

    def _line_height(self) -> int:
        # 0.5 = compact / more lines, 1.0 = loose / fewer lines.
        factor = 0.60 + 0.57 * float(self._line_density)
        return max(42, int(self.height() / 8 * factor))

    def set_tuning(self, font_scale: float = 1.0,
                   density: float = 0.7, animation: str = "normal") -> None:
        """Apply the same Aa parameters to the classic native lyric page."""
        self._font_scale = max(0.75, min(1.45, float(font_scale)))
        self._line_density = max(0.45, min(1.15, float(density)))
        self._anim_step = {"calm": 0.10, "chaotic": 0.26}.get(str(animation), 0.16)
        self.update()

    def _resume_follow(self) -> None:
        if self.offset:
            self.offset = 0.0
            self._timer.start()
        self.update()

    def set_lines(self, lines: list[tuple[int, str]], message: str = "",
                  source: str = "", translations: dict[int, str] | None = None) -> None:
        self.lines = lines or []
        self.translations = dict(translations or {})
        self.index = 0
        self.scroll = 0.0
        self.offset = 0.0
        self.source = source or ""
        self.message = message or ("暂无歌词，享受音乐吧" if not lines else "")
        self._timer.start()
        self.update()

    def set_folia(self, folia: bool) -> None:
        self.folia = bool(folia)
        self.update()

    def set_folia_track(self, track: dict) -> None:
        self._folia_title = str((track or {}).get("title", "") or "")
        self._folia_artist = str((track or {}).get("artist", "") or "")
        self.update()

    def set_folia_playing(self, playing: bool) -> None:
        self._folia_playing = bool(playing)
        self.update()

    def set_folia_duration(self, ms: int) -> None:
        self._folia_duration = max(0, int(ms or 0))
        self.update()

    def set_folia_chrome_visible(self, visible: bool) -> None:
        self._folia_chrome = bool(visible)
        self.update()

    def set_accent(self, color: QColor) -> None:
        self.accent = QColor(color)
        self.update()

    def set_position(self, ms: int) -> None:
        self.position = max(0, int(ms))
        if not self.lines:
            return
        idx = 0
        for i, (time_ms, _text) in enumerate(self.lines):
            if time_ms <= ms:
                idx = i
            else:
                break
        if idx != self.index:
            self.index = idx
            self._timer.start()
        self.update()

    def _progress(self) -> float:
        if not self.lines or not (0 <= self.index < len(self.lines)):
            return 0.0
        start = self.lines[self.index][0]
        end = (self.lines[self.index + 1][0] if self.index + 1 < len(self.lines)
               else start + 6000)
        return min(1.0, max(0.0, (self.position - start) / max(1, end - start)))

    def _folia_layout(self, w: int, h: int) -> dict[str, QRectF]:
        """Geometry of Folia's own bottom chrome, in lyric-canvas coordinates."""
        pill_w = float(min(760, max(500, int(w * 0.44))))
        pill_h = 76.0
        pill = QRectF((w - pill_w) / 2, h - 100, pill_w, pill_h)
        play = QRectF(pill.left() + 12, pill.top() + 15, 46, 46)
        progress = QRectF(pill.left() + 72, pill.top() + 50,
                          max(80.0, pill.width() - 72 - 132), 10)
        shuffle = QRectF(pill.right() - 86, pill.top() + 26, 28, 28)
        queue = QRectF(pill.right() - 52, pill.top() + 26, 28, 28)
        close = QRectF(w - 76, pill.top() + 14, 46, 46)
        lyric_hit = QRectF(0, h * 0.27, w, h * 0.36)
        return {
            "pill": pill, "play": play, "progress": progress,
            "shuffle": shuffle, "queue": queue, "close": close,
            "lyric": lyric_hit,
        }

    def folia_queue_anchor(self) -> QPoint:
        """Global anchor of Folia's own queue button, used to offset the popup."""
        layout = self._folia_layout(self.width(), self.height())
        return self.mapToGlobal(layout["queue"].center().toPoint())

    def _tick(self) -> None:
        target = float(self.index) + self.offset
        target = min(max(target, 0.0), float(max(0, len(self.lines) - 1)))
        if abs(self.scroll - target) > 0.01:
            self.scroll += (target - self.scroll) * self._anim_step
            self.update()
        else:
            self.scroll = target
            self._timer.stop()

    # ------------------------------------------------------------------ input
    def wheelEvent(self, event) -> None:
        if not self.lines or self.folia:
            return
        steps = event.angleDelta().y() / 120
        if not steps:
            return
        self.offset -= steps * 3
        low = -float(self.index) - 4
        high = float(len(self.lines) - 1 - self.index) + 4
        self.offset = min(max(self.offset, low), high)
        self._follow.start()
        self._timer.start()
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.lines:
            pos = event.position()
            if self.folia:
                layout = self._folia_layout(self.width(), self.height())
                if self._folia_chrome and layout["close"].contains(pos):
                    self.closeRequested.emit()
                    event.accept()
                    return
                if self._folia_chrome and layout["pill"].contains(pos):
                    if layout["play"].contains(pos):
                        self.toggleRequested.emit()
                    elif layout["progress"].contains(pos):
                        width = max(1.0, layout["progress"].width())
                        fraction = (pos.x() - layout["progress"].left()) / width
                        duration = self._folia_duration or (
                            self.lines[-1][0] + 6000 if self.lines else 0)
                        self.seekRequested.emit(int(max(0.0, min(1.0, fraction)) * duration))
                    elif layout["shuffle"].contains(pos):
                        self.modeRequested.emit()
                    elif layout["queue"].contains(pos):
                        self.playlistRequested.emit()
                    event.accept()
                    return
                if self._folia_chrome and layout["lyric"].contains(pos):
                    self.seekRequested.emit(self.lines[self.index][0])
                    event.accept()
                    return
                event.accept()
                return
            y = event.position().y()
            idx = int(round(self.scroll + (y - self.height() / 2) / self._line_height()))
            if 0 <= idx < len(self.lines):
                self.offset = 0.0
                self.index = idx
                self.scroll = float(idx)
                self._follow.stop()
                self.seekRequested.emit(self.lines[idx][0])
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def _translation(self, index: int) -> str:
        if not self.translations or not (0 <= index < len(self.lines)):
            return ""
        ms, original = self.lines[index]
        text = self.translations.get(ms, "")
        if not text:
            nearest = min(self.translations.keys(), key=lambda key: abs(key - ms), default=None)
            if nearest is not None and abs(nearest - ms) <= 900:
                text = self.translations[nearest]
        text = " ".join((text or "").split())
        if not text or text == " ".join(original.split()):
            return ""
        return text

    def _draw_folia_pill(self, p: QPainter, w: int, h: int) -> None:
        layout = self._folia_layout(w, h)
        pill = layout["pill"]

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 176))
        p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        p.setBrush(Qt.NoBrush)
        p.setPen(QColor(255, 255, 255, 18))
        p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5),
                          (pill.height() - 1) / 2, (pill.height() - 1) / 2)

        play = layout["play"]
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 242))
        p.drawEllipse(play)
        icon = make_icon("pause" if self._folia_playing else "play", "#11151F", 17)
        icon.paint(p, play.adjusted(11, 11, -11, -11).toRect())

        title = self._folia_title or "Aria"
        if self._folia_artist and self._folia_artist.lower() not in title.lower():
            title = f"{title}  ·  {self._folia_artist}"
        title_font = QFont("Microsoft YaHei UI")
        title_font.setPixelSize(12)
        title_font.setWeight(QFont.DemiBold)
        p.setFont(title_font)
        p.setPen(QColor(246, 247, 250, 238))
        title_rect = QRectF(pill.left() + 72, pill.top() + 9,
                            max(80.0, pill.width() - 72 - 120), 22)
        title = p.fontMetrics().elidedText(title, Qt.ElideRight, int(title_rect.width()))
        p.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, title)

        now_s = max(0, int(self.position // 1000))
        total_s = max(0, int(self._folia_duration // 1000))
        now = f"{now_s // 60:02d}:{now_s % 60:02d}"
        total = f"{total_s // 60:02d}:{total_s % 60:02d}"
        time_font = QFont("Microsoft YaHei UI")
        time_font.setPixelSize(10)
        p.setFont(time_font)
        p.setPen(QColor(255, 255, 255, 144))
        p.drawText(QRectF(pill.left() + 72, pill.top() + 36,
                          max(120.0, pill.width() - 72 - 132), 14),
                   Qt.AlignHCenter | Qt.AlignVCenter, f"{now} —— {total}")

        progress_rect = layout["progress"]
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(QRectF(progress_rect.left(), progress_rect.top() + 4,
                                 progress_rect.width(), 2),
                          1, 1)
        duration = max(1, self._folia_duration)
        fraction = max(0.0, min(1.0, self.position / duration))
        if fraction > 0.001:
            p.setBrush(QColor(255, 255, 255, 226))
            p.drawRoundedRect(QRectF(progress_rect.left(), progress_rect.top() + 4,
                                     progress_rect.width() * fraction, 2), 1, 1)

        shuffle = layout["shuffle"]
        queue = layout["queue"]
        icon_shuffle = make_icon("shuffle", "#C7D1DC", 15)
        icon_queue = make_icon("list", "#C7D1DC", 15)
        icon_shuffle.paint(p, shuffle.adjusted(6, 6, -6, -6).toRect())
        icon_queue.paint(p, queue.adjusted(6, 6, -6, -6).toRect())

        close = layout["close"]
        p.setPen(QColor(255, 255, 255, 20))
        p.setBrush(QColor(0, 0, 0, 124))
        p.drawEllipse(close)
        close_icon = make_icon("close", "#DCE5EE", 15)
        close_icon.paint(p, close.adjusted(14, 14, -14, -14).toRect())

    def _paint_folia(self, p: QPainter, w: int, h: int) -> None:
        # Folia's Web stage owns the lyrics.  Drawing the native lyric text here
        # duplicated the song lines and covered the React stage.
        if self._folia_chrome:
            self._draw_folia_pill(p, w, h)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        if self.folia:
            # Folia owns the lyric stage.  Even its "matching lyrics…" placeholder
            # must not be painted here, or it lands on top of the Web stage.
            self._paint_folia(p, w, h)
            return
        if not self.lines:
            p.setPen(QColor(255, 255, 255, 110))
            p.setFont(QFont("Microsoft YaHei UI", 14))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        line_h = self._line_height()
        center_y = h / 2
        progress = self._progress()
        span = max(4, min(10, int(round(h / max(1, line_h) / 2)) + 2))
        for offset in range(-span, span + 1):
            i = int(round(self.scroll)) + offset
            if i < 0 or i >= len(self.lines):
                continue
            y = center_y + (i - self.scroll) * line_h
            distance = abs(i - self.scroll)
            active = distance < 0.5
            alpha = int(235 if active else max(0, 145 - distance * 32))
            size = max(11, int((28 if active else 15) * self._font_scale))
            font = QFont("Microsoft YaHei UI", size)
            font.setBold(active)
            p.setFont(font)
            text = self.lines[i][1]
            if active:
                # Long titles / long lyric lines must not run off both edges.
                while size > 11 and p.fontMetrics().horizontalAdvance(text) > w - 120:
                    size -= 2
                    font = QFont("Microsoft YaHei UI", size)
                    font.setBold(True)
                    p.setFont(font)
                if p.fontMetrics().horizontalAdvance(text) > w - 120:
                    text = p.fontMetrics().elidedText(text, Qt.ElideRight, int(w - 120))
            rect = QRectF(40, y - line_h / 2, w - 80, line_h)

            if active:
                # soft halo behind the active line
                p.setPen(Qt.NoPen)
                glow = QRadialGradient(QPointF(w / 2, y), w * 0.36)
                glow.setColorAt(0.0, QColor(110, 139, 255, 40))
                glow.setColorAt(1.0, QColor(110, 139, 255, 0))
                p.setBrush(glow)
                p.drawEllipse(QPointF(w / 2, y), w * 0.36, line_h * 0.95)
                p.setPen(QColor(255, 255, 255, alpha))
                p.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, text)

                # "sung" portion sweeps left -> right in accent colour
                if progress > 0.01:
                    metrics = p.fontMetrics()
                    text_w = min(metrics.horizontalAdvance(text), int(rect.width()))
                    x0 = w / 2 - text_w / 2
                    p.save()
                    p.setClipRect(QRectF(x0, y - line_h / 2, text_w * progress, line_h))
                    p.setPen(QColor(168, 190, 255))
                    p.setFont(font)
                    p.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, text)
                    p.restore()

                translation = self._translation(i)
                if translation:
                    p.setFont(QFont("Microsoft YaHei UI",
                                    max(10, int(13 * self._font_scale))))
                    p.setPen(QColor(255, 255, 255, 150))
                    tr = p.fontMetrics().elidedText(
                        translation, Qt.ElideRight, int(w - 120))
                    p.drawText(QRectF(40, y + 18, w - 80, 22),
                               Qt.AlignHCenter | Qt.AlignVCenter, tr)
            else:
                p.setPen(QColor(255, 255, 255, alpha))
                p.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, text)



class ImmersiveView(QWidget):
    closeRequested = Signal()
    toggleRequested = Signal()
    nextRequested = Signal()
    prevRequested = Signal()
    playlistRequested = Signal()
    modeRequested = Signal()
    seekRequested = Signal(int)
    foliaChanged = Signal(bool)
    fullscreenRequested = Signal(bool)
    lyricSearchRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._bg: QPixmap | None = None
        self._scaled_bg: QPixmap | None = None
        self._track: dict = {}
        self._cover_for_blur: QPixmap | None = None
        self._signals_conn = False
        self._folia = False
        self._folia_mode = DEFAULT_FOLIA_MODE
        self._folia_tuning = {"fontScale": 1.0, "animationIntensity": "normal", "wordSpacing": 0.7}
        self._accent = QColor("#DFA15B")
        self._cover_accent = QColor("#A8BEFF")
        self._folia_cache: QPixmap | None = None
        self._folia_key: tuple[int, int] | None = None
        self._folia_phase = 0.0
        self._web_stage = None
        self._web_stage_failed = False
        self._web_ticks = 0
        self._lyric_lines: list[tuple[int, str]] = []
        self._lyric_translations: dict[int, str] = {}
        self._duration_ms = 0
        self._playing = False
        self._fullscreen = False
        self._ui_hidden = False
        self._last_cursor = QCursor.pos()

        root = QVBoxLayout(self)
        self._root = root
        root.setContentsMargins(34, 20, 34, 26)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.source_label = QLabel("")
        self.source_label.setStyleSheet("color:rgba(255,255,255,0.5);font-size:12px;")
        top.addWidget(self.source_label)
        top.addStretch(1)
        self.folia_btn = PillToggle(False, label="Folia", width=108,
                                    pill_width=100, font_size=14)
        self.folia_btn.setToolTip("Folia 模式：深蓝几何舞台 · 衬线错位大字 · 胶囊播放条")
        self.folia_btn.toggled.connect(self._on_folia_toggled)
        top.addWidget(self.folia_btn)
        self.lyric_search_btn = PillButton("歌词搜索", width=108, height=38,
                                           font_size=14)
        self.lyric_search_btn.setToolTip("搜索同名歌曲的其它歌词版本并替换")
        self.lyric_search_btn.clicked.connect(self.lyricSearchRequested.emit)
        top.addWidget(self.lyric_search_btn)
        self.folia_mode = QComboBox()
        self.folia_mode.setFixedSize(104, 40)
        self.folia_mode.setCursor(Qt.PointingHandCursor)
        self.folia_mode.setToolTip("Folia 歌词动画模式")
        self.folia_mode.setStyleSheet(
            "QComboBox{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 rgba(255,255,255,0.20),stop:1 rgba(255,255,255,0.08));"
            "border:1px solid rgba(255,255,255,0.20);border-radius:20px;"
            "padding:3px 8px 3px 16px;color:#F4F4F6;font-size:12px;}"
            "QComboBox:hover{background:rgba(255,255,255,0.24);border-color:rgba(255,255,255,0.30);}"
            "QComboBox::drop-down{border:none;background:transparent;width:20px;}"
            "QComboBox QAbstractItemView{background-color:#12141C;border:1px solid rgba(255,255,255,0.12);"
            "border-radius:8px;padding:4px;color:#F4F4F6;selection-background-color:#2B2F45;}"
        )
        for key, label in FOLIA_MODES:
            self.folia_mode.addItem(label, key)
        self.folia_mode.setCurrentIndex(
            next((i for i, (key, _label) in enumerate(FOLIA_MODES) if key == DEFAULT_FOLIA_MODE), 0)
        )
        self.folia_mode.currentIndexChanged.connect(self._on_folia_mode_changed)
        top.addWidget(self.folia_mode)
        self.folia_tune = QToolButton()
        self.folia_tune.setText("Aa")
        self.folia_tune.setFixedSize(42, 42)
        self.folia_tune.setCursor(Qt.PointingHandCursor)
        self.folia_tune.setToolTip("歌词参数：字号 / 动画节奏 / 歌词密度")
        self.folia_tune.setPopupMode(QToolButton.InstantPopup)
        self.folia_tune.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.folia_tune.setStyleSheet(
            "QToolButton{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 rgba(255,255,255,0.20),stop:1 rgba(255,255,255,0.08));"
            "border:1px solid rgba(255,255,255,0.20);border-radius:21px;"
            "color:#F4F4F6;font-size:13px;padding:0;}"
            "QToolButton:hover{background:rgba(255,255,255,0.24);border-color:rgba(255,255,255,0.30);}"
            "QToolButton::menu-indicator{image:none;}"
        )
        tune_menu = QMenu(self.folia_tune)
        tune_menu.setStyleSheet(
            "QMenu{background-color:#12141C;border:1px solid rgba(255,255,255,0.12);"
            "border-radius:8px;padding:5px;color:#F4F4F6;font-size:12px;}"
            "QMenu::item{padding:5px 16px;border-radius:5px;}"
            "QMenu::item:selected{background-color:#2B2F45;}"
        )
        for title, key, items in (
            ("字体大小", "fontScale", (("小", 0.9), ("标准", 1.0), ("大", 1.15))),
            ("动画节奏", "animationIntensity", (("舒缓", "calm"), ("标准", "normal"), ("强烈", "chaotic"))),
            ("歌词密度", "wordSpacing", (("紧凑", 0.5), ("标准", 0.7), ("宽松", 1.0))),
        ):
            sub = tune_menu.addMenu(title)
            group = QActionGroup(sub)
            group.setExclusive(True)
            current = self._folia_tuning.get(key)
            for label, value in items:
                action = QAction(label, sub)
                action.setCheckable(True)
                action.setChecked(current == value)
                action.triggered.connect(
                    lambda _checked=False, k=key, v=value: self._on_folia_tuning(k, v)
                )
                group.addAction(action)
                sub.addAction(action)
        # The immersive chrome auto-hides after three idle seconds.  A submenu
        # takes longer than that to navigate, which used to close the menu
        # before an option could be applied.
        tune_menu.aboutToShow.connect(self._pause_chrome_hide)
        tune_menu.aboutToHide.connect(self._resume_chrome_hide)
        self.folia_tune.setMenu(tune_menu)
        top.addWidget(self.folia_tune)
        top.addSpacing(8)
        self.lyric_source = QLabel("")
        self.lyric_source.setStyleSheet("color:rgba(255,255,255,0.38);font-size:11px;")
        top.addWidget(self.lyric_source)
        top.addSpacing(10)
        self.fullscreen_btn = QPushButton()
        self.fullscreen_btn.setObjectName("FullscreenFab")
        self.fullscreen_btn.setFixedSize(40, 40)
        self.fullscreen_btn.setIcon(make_icon("expand", "#FFFFFF", 19))
        self.fullscreen_btn.setToolTip("全屏歌词 (F11)")
        self.fullscreen_btn.setCursor(Qt.PointingHandCursor)
        self.fullscreen_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.12);border:none;border-radius:20px;}"
            "QPushButton:hover{background:rgba(124,140,255,0.50);}")
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        top.addWidget(self.fullscreen_btn)
        self.close_btn = QPushButton()
        self.close_btn.setObjectName("CloseFab")
        self.close_btn.setFixedSize(48, 48)
        self.close_btn.setIcon(make_icon("close", "#FFFFFF", 24))
        self.close_btn.setToolTip("关闭歌词页 (Esc)")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.14);border:none;border-radius:24px;}"
            "QPushButton:hover{background:rgba(124,140,255,0.55);}"
            "QPushButton:pressed{background:rgba(124,140,255,0.75);}")
        self.close_btn.clicked.connect(self.closeRequested.emit)
        top.addWidget(self.close_btn)
        root.addLayout(top)

        root.addStretch(1)

        self.lyrics = LyricView()
        self.lyrics.set_tuning(
            self._folia_tuning["fontScale"],
            self._folia_tuning["wordSpacing"],
            self._folia_tuning["animationIntensity"],
        )
        root.addWidget(self.lyrics, 3)

        root.addStretch(1)

        self.title = QLabel("")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("color:#FFFFFF;font-size:23px;font-weight:700;")
        root.addWidget(self.title)

        self.meta = QLabel("")
        self.meta.setAlignment(Qt.AlignCenter)
        self.meta.setStyleSheet("color:rgba(255,255,255,0.55);font-size:13px;")
        root.addWidget(self.meta)

        root.addSpacing(14)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(12)
        self.time_now = QLabel("0:00")
        self.time_now.setStyleSheet("color:rgba(255,255,255,0.55);font-size:11px;")
        self.time_total = QLabel("0:00")
        self.time_total.setStyleSheet("color:rgba(255,255,255,0.55);font-size:11px;")
        self.slider = SeekSlider()
        self.slider.setObjectName("Seek")
        self.slider.setRange(0, 1)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.sliderReleased.connect(self._on_seek)
        self.slider.sliderMoved.connect(
            lambda v: self.time_now.setText(human_time(v))
        )
        progress_row.addWidget(self.time_now)
        progress_row.addWidget(self.slider, 1)
        progress_row.addWidget(self.time_total)
        root.addLayout(progress_row)

        controls = QHBoxLayout()
        controls.setSpacing(22)
        controls.addStretch(1)
        self.prev_btn = self._icon_button("prev", 20, 40)
        self.prev_btn.clicked.connect(self.prevRequested.emit)
        controls.addWidget(self.prev_btn)
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("PlayCircle")
        self.play_btn.setFixedSize(50, 50)
        self.play_btn.setIcon(make_icon("play", "#FFFFFF", 22))
        self.play_btn.setIconSize(QSize(22, 22))
        self.play_btn.clicked.connect(self.toggleRequested.emit)
        controls.addWidget(self.play_btn)
        self.next_btn = self._icon_button("next", 20, 40)
        self.next_btn.clicked.connect(self.nextRequested.emit)
        controls.addWidget(self.next_btn)
        self.playlist_btn = self._icon_button("list", 20, 40)
        self.playlist_btn.setToolTip("播放列表")
        self.playlist_btn.clicked.connect(self.playlistRequested.emit)
        controls.addWidget(self.playlist_btn)
        controls.addStretch(1)
        root.addLayout(controls)

        self._slider_dragging = False
        self.slider.sliderPressed.connect(lambda: setattr(self, "_slider_dragging", True))
        self.lyrics.seekRequested.connect(self.seek_request_line)
        self.lyrics.toggleRequested.connect(self.toggleRequested.emit)
        self.lyrics.nextRequested.connect(self.nextRequested.emit)
        self.lyrics.prevRequested.connect(self.prevRequested.emit)
        self.lyrics.playlistRequested.connect(self.playlistRequested.emit)
        self.lyrics.modeRequested.connect(self.modeRequested.emit)
        self.lyrics.closeRequested.connect(self.closeRequested.emit)

        self._ui_widgets = [
            self.source_label, self.folia_btn, self.lyric_search_btn, self.folia_mode,
            self.folia_tune, self.lyric_source, self.fullscreen_btn, self.close_btn,
            self.title, self.meta, self.time_now, self.time_total, self.slider,
            self.prev_btn, self.play_btn, self.next_btn, self.playlist_btn,
        ]
        self._folia_timer = QTimer(self)
        self._folia_timer.setInterval(40)
        self._folia_timer.timeout.connect(self._folia_tick)
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(140)
        self._cursor_timer.timeout.connect(self._watch_cursor)
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._hide_fullscreen_ui)

    def _toggle_fullscreen(self) -> None:
        self.fullscreenRequested.emit(not self._fullscreen)

    def _auto_hide_enabled(self) -> bool:
        """Folia and fullscreen borrow the same idle-hide treatment."""
        return bool(self._folia or self._fullscreen)

    def _sync_auto_hide(self) -> None:
        if not self._auto_hide_enabled() or not self.isVisible():
            self._cursor_timer.stop()
            self._idle_timer.stop()
            return
        self._ui_hidden = False
        self._apply_visibility()
        self._last_cursor = QCursor.pos()
        self._cursor_timer.start()
        self._idle_timer.start(AUTO_HIDE_MS)

    def _pause_chrome_hide(self) -> None:
        """Keep chrome and the Aa menu alive while the user is choosing an option."""
        self._cursor_timer.stop()
        self._idle_timer.stop()

    def _resume_chrome_hide(self) -> None:
        if self._auto_hide_enabled() and self.isVisible():
            self._last_cursor = QCursor.pos()
            self._cursor_timer.start()
            self._idle_timer.start(AUTO_HIDE_MS)

    def toggle_ui_hidden(self) -> None:
        """H hides every Aria control and lets Folia fill the whole window."""
        if not self.isVisible():
            return
        self._ui_hidden = not self._ui_hidden
        self._apply_visibility()
        if self._auto_hide_enabled():
            self._last_cursor = QCursor.pos()
            if self._ui_hidden:
                self._idle_timer.stop()
            else:
                self._idle_timer.start(AUTO_HIDE_MS)

    def reset_ui_hidden(self) -> None:
        self._ui_hidden = False
        self._apply_visibility()
        if self._auto_hide_enabled() and self.isVisible():
            self._last_cursor = QCursor.pos()
            self._cursor_timer.start()
            self._idle_timer.start(AUTO_HIDE_MS)

    def set_fullscreen(self, fullscreen: bool) -> None:
        self._fullscreen = bool(fullscreen)
        self.fullscreen_btn.setIcon(make_icon("collapse" if self._fullscreen else "expand",
                                              "#FFFFFF", 19))
        self.fullscreen_btn.setToolTip("退出全屏 (F11)" if self._fullscreen else "全屏歌词 (F11)")
        if self._fullscreen:
            self._ui_hidden = False
            self._apply_visibility()
            self._last_cursor = QCursor.pos()
            self._cursor_timer.start()
            self._idle_timer.start(AUTO_HIDE_MS)
        else:
            self._idle_timer.stop()
            if self._folia:
                self._sync_auto_hide()
            else:
                self._cursor_timer.stop()
                self._ui_hidden = False
                self._apply_visibility()

    def _watch_cursor(self) -> None:
        if not self._auto_hide_enabled() or not self.isVisible():
            return
        pos = QCursor.pos()
        if (pos - self._last_cursor).manhattanLength() > 3:
            self._last_cursor = pos
            self._ui_hidden = False
            self._apply_visibility()
            self._idle_timer.start(AUTO_HIDE_MS)

    def _hide_fullscreen_ui(self) -> None:
        if not self._auto_hide_enabled():
            return
        self._ui_hidden = True
        self._apply_visibility()

    def _raise_chrome(self, widgets=()) -> None:
        """Keep native Folia capsules above the full-bleed WebEngine stage."""
        if self._web_stage is None:
            return
        try:
            self.lyrics.raise_()
        except RuntimeError:
            pass
        for widget in widgets:
            try:
                widget.raise_()
            except RuntimeError:
                pass

    def _apply_visibility(self) -> None:
        visible = not self._ui_hidden
        if self._folia:
            # Folia keeps its own full-bleed stage.  Only the Aria mode
            # capsules + the Folia mode picker + fullscreen/close remain; the
            # Aria transport row is replaced by Folia's black capsule.
            keep = (self.folia_btn, self.lyric_search_btn, self.folia_mode, self.folia_tune,
                    self.fullscreen_btn, self.close_btn)
            for widget in self._ui_widgets:
                widget.setVisible(visible and widget in keep)
            self.lyrics.set_folia_chrome_visible(visible)
            self._sync_web_geometry()
            self._raise_chrome(keep if visible else ())
            return
        for widget in self._ui_widgets:
            widget.setVisible(visible)
        self.folia_mode.setVisible(False)
        self.lyrics.set_folia_chrome_visible(False)

    def _folia_tick(self) -> None:
        if not self._folia:
            return
        self._folia_phase += 0.045
        self._web_ticks += 1
        # Folia's own React/Bits run in the web stage; 20 Hz is enough for its
        # word clock while keeping the JS bridge cheap.
        if self._web_stage is not None and self._web_stage.isVisible() and self._web_ticks % 2 == 0:
            try:
                self._web_stage.set_transport(getattr(self, "_last_position", 0), self._playing)
            except Exception:
                pass
        if self.isVisible():
            self.update()

    def _ensure_web_stage(self) -> bool:
        if self._web_stage is not None:
            return True
        if self._web_stage_failed:
            return False
        try:
            from folia_web import FoliaWebStage

            stage = FoliaWebStage(self)
            stage.failed.connect(self._on_web_stage_failed)
            stage.ready.connect(self._on_web_stage_ready)
            stage.hide()
            stage.lower()
            self._web_stage = stage
            return True
        except Exception as exc:  # noqa: BLE001
            self._web_stage_failed = True
            self._web_stage = None
            try:
                print(f"folia web stage unavailable: {exc}")
            except Exception:
                pass
            return False

    def _on_web_stage_failed(self, message: str) -> None:
        self._web_stage_failed = True
        try:
            print(f"folia web stage failed: {message}")
        except Exception:
            pass
        if self._web_stage is not None:
            self._web_stage.hide()

    def _on_web_stage_ready(self) -> None:
        if not self._folia or self._web_stage is None:
            return
        self._sync_web_geometry()
        self._push_web_mode()
        self._push_web_song()
        self._push_web_tuning()
        self._push_web_content()
        self._push_web_cover()
        self._web_stage.show()
        self._web_stage.lower()
        self._apply_visibility()

    def _on_folia_mode_changed(self, _index: int) -> None:
        mode = self.folia_mode.currentData() or DEFAULT_FOLIA_MODE
        self._folia_mode = str(mode)
        self._push_web_mode()

    def _push_web_mode(self) -> None:
        if self._web_stage is None:
            return
        try:
            self._web_stage.set_mode(self._folia_mode)
        except Exception:
            pass

    def _push_web_tuning(self) -> None:
        if self._web_stage is None:
            return
        try:
            self._web_stage.set_tuning(**self._folia_tuning)
        except Exception:
            pass

    def _on_folia_tuning(self, key: str, value: object) -> None:
        self._folia_tuning[key] = value
        self.lyrics.set_tuning(
            self._folia_tuning["fontScale"],
            self._folia_tuning["wordSpacing"],
            self._folia_tuning["animationIntensity"],
        )
        self._push_web_tuning()

    def folia_queue_anchor(self) -> QPoint:
        return self.lyrics.folia_queue_anchor()

    def _push_web_song(self) -> None:
        if self._web_stage is None:
            return
        try:
            self._web_stage.set_song(
                str(self._track.get("title", "") or ""),
                str(self._track.get("artist", "") or ""),
            )
        except Exception:
            pass

    def _sync_web_geometry(self) -> None:
        if self._web_stage is None:
            return
        # Folia's backdrop is painted by the web stage and must run edge to edge
        # behind the native top bar and the capsule.  The chrome is an overlay,
        # not a reserved strip: reserving space leaves two opaque Aria bars over
        # the song theme, which reads as an unfilled background.
        self._web_stage.setGeometry(0, 0, self.width(), self.height())

    def _push_web_content(self) -> None:
        if self._web_stage is None:
            return
        try:
            self._web_stage.set_lyrics(self._lyric_lines, self._lyric_translations)
            self._web_stage.set_duration(self._duration_ms)
            self._web_stage.set_transport(getattr(self, "_last_position", 0), self._playing)
        except Exception:
            pass

    def _push_web_cover(self) -> None:
        """Give Folia the same artwork the native stage blurs behind the lyrics."""
        if self._web_stage is None:
            return
        try:
            self._web_stage.set_cover(self._cover_for_blur)
        except Exception:
            pass

    def _set_web_stage_visible(self, visible: bool) -> None:
        if not visible:
            if self._web_stage is not None:
                self._web_stage.hide()
            return
        if not self._ensure_web_stage():
            return
        self._sync_web_geometry()
        self._push_web_mode()
        self._push_web_song()
        self._push_web_tuning()
        self._push_web_content()
        if self._web_stage is not None:
            self._web_stage.show()
            # The stage is only the full-bleed backdrop.  Raising it here put
            # it above every native capsule, so the Folia chrome looked hidden
            # even while it was receiving a mouse-move reveal.
            self._web_stage.lower()
            self._apply_visibility()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._folia and not self._folia_timer.isActive():
            self._folia_timer.start()
        if self._auto_hide_enabled():
            self._sync_auto_hide()
        if self._folia:
            # The saved Folia setting may have instantiated the web stage while
            # this view was still hidden. Qt does not bring an explicitly-shown
            # child back on screen in that case, so re-assert it after layout.
            QTimer.singleShot(0, lambda: self._set_web_stage_visible(True))

    def hideEvent(self, event) -> None:
        self._folia_timer.stop()
        self._cursor_timer.stop()
        self._idle_timer.stop()
        super().hideEvent(event)

    def _icon_button(self, kind: str, icon_size: int, size: int) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("IconBtn")
        btn.setFixedSize(size, size)
        btn.setIcon(make_icon(kind, "#FFFFFF", icon_size))
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def _on_folia_toggled(self, checked: bool) -> None:
        self.set_folia(bool(checked))
        self.foliaChanged.emit(self._folia)

    def set_folia(self, folia: bool) -> None:
        self._apply_mode_state(bool(folia))

    def _apply_mode_state(self, folia: bool) -> None:
        self._folia = bool(folia)
        self.folia_btn.sync_checked(self._folia)
        self._apply_accent()
        self.lyrics.set_folia(self._folia)
        self._set_web_stage_visible(self._folia)
        self.lyrics.set_folia_chrome_visible(
            not (self._fullscreen and self._ui_hidden))
        # Folia uses the full stage; classic mode keeps the original balanced
        # stage with room for title and metadata.
        try:
            self._root.setStretch(1, 0 if self._folia else 1)
            self._root.setStretch(2, 8 if self._folia else 3)
            self._root.setStretch(3, 0 if self._folia else 1)
        except Exception:
            pass
        if self._folia and self.isVisible():
            if not self._folia_timer.isActive():
                self._folia_timer.start()
        else:
            self._folia_timer.stop()
        if self._folia:
            # Entering Folia must start from visible chrome: a previous H
            # press otherwise leaves the picker/tune/close buttons hidden.
            self._ui_hidden = False
            self._sync_auto_hide()
        elif not self._fullscreen:
            self._cursor_timer.stop()
            self._idle_timer.stop()
            self._ui_hidden = False
        self._apply_visibility()
        self.update()

    def _apply_accent(self) -> None:
        if self._folia:
            self._accent = QColor(FOLIA_ACCENT)
        else:
            self._accent = QColor(self._cover_accent)
        self.lyrics.set_accent(self._accent)
        self.folia_btn.set_accent(QColor(FOLIA_ACCENT))
        self._folia_cache = None
        self._folia_key = None

    def _set_accent_from_cover(self, pix: QPixmap | None) -> None:
        color = QColor("#A8BEFF")
        if pix and not pix.isNull():
            image = pix.toImage().scaled(14, 14, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            red = green = blue = count = 0
            for y in range(image.height()):
                for x in range(image.width()):
                    c = image.pixelColor(x, y)
                    if c.alpha() < 40:
                        continue
                    hi, lo = max(c.red(), c.green(), c.blue()), min(c.red(), c.green(), c.blue())
                    if hi - lo < 12:
                        continue
                    red += c.red()
                    green += c.green()
                    blue += c.blue()
                    count += 1
            if count:
                color = QColor(red // count, green // count, blue // count)
                if color.lightness() < 70:
                    color = color.lighter(170)
                elif color.lightness() > 215:
                    color = color.lighter(105)
        self._cover_accent = color
        self._apply_accent()

    # ------------------------------------------------------------------ data
    def set_track(self, track: dict, cover_pix: QPixmap | None) -> None:
        self._track = dict(track)
        self.title.setText(track.get("title", ""))
        artist = track.get("artist", "")
        album = track.get("album", "")
        bits = [x for x in [artist, album] if x]
        if track.get("is_preview"):
            bits.append("30 秒试听")
        self.meta.setText(" · ".join(bits))
        self.source_label.setText(f"来源 · {track.get('source_name', '')}")
        self._cover_for_blur = cover_pix
        self._set_accent_from_cover(cover_pix)
        self._push_web_cover()
        self._push_web_song()
        self._bg = None
        self._scaled_bg = None
        self.resize_bg()
        self.lyrics.set_folia_track(track)
        self.lyrics.set_lines([], "正在匹配歌词…")
        self._lyric_lines = []
        self._lyric_translations = {}
        self._push_web_content()
        self.update()

    def set_lyrics(self, data: dict) -> None:
        """Window supplies matched lyrics; the stage only renders them."""
        lines = (data or {}).get("lines") or []
        source = (data or {}).get("source") or ""
        translations = (data or {}).get("translations") or {}
        if isinstance(translations, (list, tuple)):
            translations = {
                lines[i][0]: text for i, text in enumerate(translations)
                if i < len(lines) and text
            }
        message = "暂无歌词，享受音乐吧" if not lines else ""
        self.lyric_source.setText(f"歌词 · {source}" if source else "")
        self._lyric_lines = list(lines or [])
        self._lyric_translations = dict(translations or {})
        self.lyrics.set_lines(lines, message, source, translations)
        if lines:
            self.lyrics.set_position(getattr(self, "_last_position", 0))
        self._push_web_content()
        self.update()

    def set_cover(self, pix: QPixmap | None) -> None:
        if pix and not pix.isNull():
            self._cover_for_blur = pix
            self._scaled_bg = None
            self._set_accent_from_cover(pix)
            self._push_web_cover()
            self.resize_bg()
            self.update()

    def resize_bg(self) -> None:
        src = None
        if self._cover_for_blur and not self._cover_for_blur.isNull():
            src = self._cover_for_blur
        elif self._track.get("cover"):
            src = None
        if src is None:
            self._scaled_bg = None
            return
        blurred = blur_pixmap(src)
        self._scaled_bg = blurred.scaled(self.size(), Qt.KeepAspectRatioByExpanding,
                                        Qt.SmoothTransformation)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._folia_cache = None
        self._folia_key = None
        self.resize_bg()
        self._sync_web_geometry()

    def set_state(self, playing: bool) -> None:
        self._playing = bool(playing)
        self.play_btn.setIcon(make_icon("pause" if playing else "play", "#FFFFFF", 22))
        self.lyrics.set_folia_playing(playing)
        if self._web_stage is not None:
            self._web_stage.set_transport(getattr(self, "_last_position", 0), self._playing)

    def set_position(self, ms: int) -> None:
        self._last_position = ms
        if not self._slider_dragging:
            if self.slider.maximum() <= 1:
                return
            self.slider.blockSignals(True)
            self.slider.setValue(min(ms, self.slider.maximum()))
            self.slider.blockSignals(False)
            self.time_now.setText(human_time(ms))
        self.lyrics.set_position(ms)

    def set_duration(self, ms: int) -> None:
        duration = max(1, ms)
        self._duration_ms = max(0, int(ms or 0))
        self.slider.setRange(0, duration)
        self.time_total.setText(human_time(ms))
        self.lyrics.set_folia_duration(ms)
        if self._web_stage is not None:
            self._web_stage.set_duration(self._duration_ms)

    def _on_seek(self) -> None:
        self._slider_dragging = False
        self.seekRequested.emit(self.slider.value())

    def seek_request_line(self, ms: int) -> None:
        if self.slider.maximum() > 1:
            self.slider.blockSignals(True)
            self.slider.setValue(min(ms, self.slider.maximum()))
            self.slider.blockSignals(False)
        self.time_now.setText(human_time(ms))
        self.seekRequested.emit(ms)

    # ------------------------------------------------------------------ paint
    def _build_folia_backdrop(self, w: int, h: int) -> QPixmap:
        pix = QPixmap(w, h)
        pix.fill(FOLIA_BG)
        q = QPainter(pix)
        q.setRenderHint(QPainter.Antialiasing, True)
        q.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # Deep navy field with a slightly lifted centre, sampled from Folia's
        # geometric theme (#080c15 / #101529 / #0c1222).
        base = QRadialGradient(QPointF(w * 0.5, h * 0.53), max(w, h) * 0.92)
        base.setColorAt(0.0, QColor("#141A31"))
        base.setColorAt(0.48, QColor("#101529"))
        base.setColorAt(0.82, QColor("#0B1120"))
        base.setColorAt(1.0, QColor("#070A12"))
        q.fillRect(0, 0, w, h, base)

        # Large, low-contrast geometry: filled disc / rings / triangle /
        # crescent / sun mark, all in Folia's secondary blue-grey family.
        q.setPen(Qt.NoPen)
        q.setBrush(QColor(FOLIA_SHAPE.red(), FOLIA_SHAPE.green(), FOLIA_SHAPE.blue(), 168))
        q.drawEllipse(QPointF(w * 0.237, h * 0.071), w * 0.047, w * 0.047)

        q.setBrush(QColor(14, 30, 47, 112))
        q.drawEllipse(QPointF(w * 0.073, h * 0.735), w * 0.055, w * 0.055)

        ring_pen = QColor(FOLIA_LINE.red(), FOLIA_LINE.green(), FOLIA_LINE.blue(), 58)
        q.setPen(ring_pen)
        q.setBrush(Qt.NoBrush)
        q.drawEllipse(QPointF(w * 0.345, h * 0.148), w * 0.031, w * 0.031)
        q.drawEllipse(QPointF(w * 0.466, h * 0.302), w * 0.064, w * 0.064)
        q.setPen(QColor(FOLIA_LINE.red(), FOLIA_LINE.green(), FOLIA_LINE.blue(), 34))
        q.drawEllipse(QPointF(w * 0.752, h * 0.255), w * 0.032, w * 0.032)

        triangle = QPainterPath()
        triangle.moveTo(w * 0.268, h * 0.332)
        triangle.lineTo(w * 0.322, h * 0.438)
        triangle.lineTo(w * 0.245, h * 0.411)
        triangle.closeSubpath()
        q.setBrush(QColor(12, 31, 49, 122))
        q.setPen(Qt.NoPen)
        q.drawPath(triangle)

        # Thin arc / crescent lines.
        q.setPen(QColor(FOLIA_LINE.red(), FOLIA_LINE.green(), FOLIA_LINE.blue(), 68))
        q.setBrush(Qt.NoBrush)
        crescent = QPainterPath()
        crescent.moveTo(w * 0.071, h * 0.455)
        crescent.cubicTo(w * 0.034, h * 0.565, w * 0.058, h * 0.690, w * 0.132, h * 0.734)
        q.drawPath(crescent)
        crescent2 = QPainterPath()
        crescent2.moveTo(w * 0.105, h * 0.455)
        crescent2.cubicTo(w * 0.074, h * 0.554, w * 0.090, h * 0.660, w * 0.147, h * 0.700)
        q.setPen(QColor(FOLIA_LINE.red(), FOLIA_LINE.green(), FOLIA_LINE.blue(), 38))
        q.drawPath(crescent2)

        # Small sun / asterisk mark on the left and tiny specks around the field.
        q.setPen(QColor(147, 183, 204, 76))
        q.drawEllipse(QPointF(w * 0.153, h * 0.199), 3.0, 3.0)
        for angle in range(0, 360, 45):
            dx = math.cos(math.radians(angle)) * 8
            dy = math.sin(math.radians(angle)) * 8
            q.drawLine(QPointF(w * 0.153 + dx * 0.45, h * 0.199 + dy * 0.45),
                       QPointF(w * 0.153 + dx, h * 0.199 + dy))
        rnd = random.Random(20261109)
        for _ in range(26):
            x = rnd.uniform(w * 0.04, w * 0.96)
            y = rnd.uniform(h * 0.08, h * 0.90)
            radius = rnd.uniform(0.7, 2.0)
            alpha = rnd.randint(26, 88)
            q.setPen(Qt.NoPen)
            q.setBrush(QColor(196, 220, 236, alpha))
            q.drawEllipse(QPointF(x, y), radius, radius)

        # Bottom shade keeps the black capsule readable on the navy field.
        bottom = QLinearGradient(0, h * 0.70, 0, h)
        bottom.setColorAt(0.0, QColor(2, 5, 10, 0))
        bottom.setColorAt(0.72, QColor(2, 5, 10, 42))
        bottom.setColorAt(1.0, QColor(2, 5, 10, 118))
        q.fillRect(0, int(h * 0.70), w, int(h * 0.30), bottom)

        vignette = QRadialGradient(QPointF(w * 0.5, h * 0.47), max(w, h) * 0.80)
        vignette.setColorAt(0.0, QColor(0, 3, 8, 0))
        vignette.setColorAt(0.60, QColor(0, 3, 8, 10))
        vignette.setColorAt(1.0, QColor(0, 2, 6, 142))
        q.setPen(Qt.NoPen)
        q.setBrush(vignette)
        q.drawRect(0, 0, w, h)

        q.end()
        return pix

    def _paint_folia_backdrop(self, p: QPainter, w: int, h: int) -> None:
        key = (w, h)
        if self._folia_cache is None or key != self._folia_key:
            self._folia_cache = self._build_folia_backdrop(w, h)
            self._folia_key = key
        p.drawPixmap(0, 0, self._folia_cache)

        # Folia's drifting specks.  They are deliberately sparse; the stage
        # should read as near-black navy, not a starfield.
        p.setPen(Qt.NoPen)
        rnd = random.Random(20261110)
        for i in range(18):
            x0 = rnd.uniform(0.05, 0.95) * w
            y0 = rnd.uniform(0.08, 0.88) * h
            radius = rnd.uniform(0.8, 2.2)
            alpha = rnd.randint(36, 108)
            x = x0 + math.sin(self._folia_phase * 0.55 + i) * 5.0
            y = y0 + math.cos(self._folia_phase * 0.42 + i * 1.7) * 7.0
            p.setBrush(QColor(206, 225, 239, alpha))
            p.drawEllipse(QPointF(x, y), radius, radius)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        w, h = self.width(), self.height()
        if self._folia:
            self._paint_folia_backdrop(p, w, h)
            return
        if self._scaled_bg and not self._scaled_bg.isNull():
            x = (w - self._scaled_bg.width()) // 2
            y = (h - self._scaled_bg.height()) // 2
            p.drawPixmap(x, y, self._scaled_bg)
        else:
            grad = QLinearGradient(0, 0, w, h)
            grad.setColorAt(0, QColor(18, 20, 32))
            grad.setColorAt(1, QColor(8, 9, 14))
            p.fillRect(0, 0, w, h, grad)
        overlay = QLinearGradient(0, 0, 0, h)
        overlay.setColorAt(0, QColor(6, 7, 12, 190))
        overlay.setColorAt(0.45, QColor(6, 7, 12, 215))
        overlay.setColorAt(1, QColor(6, 7, 12, 240))
        p.fillRect(0, 0, w, h, overlay)
        p.setPen(Qt.NoPen)
        glow = QRadialGradient(QPointF(w * 0.24, h * 0.32), max(w, h) * 0.62)
        glow.setColorAt(0.0, QColor(110, 139, 255, 62))
        glow.setColorAt(0.55, QColor(110, 139, 255, 18))
        glow.setColorAt(1.0, QColor(110, 139, 255, 0))
        p.setBrush(glow)
        p.drawEllipse(QPointF(w * 0.24, h * 0.32), max(w, h) * 0.62, max(w, h) * 0.62)
        glow2 = QRadialGradient(QPointF(w * 0.86, h * 0.78), max(w, h) * 0.5)
        glow2.setColorAt(0.0, QColor(160, 113, 255, 46))
        glow2.setColorAt(1.0, QColor(160, 113, 255, 0))
        p.setBrush(glow2)
        p.drawEllipse(QPointF(w * 0.86, h * 0.78), max(w, h) * 0.5, max(w, h) * 0.5)
