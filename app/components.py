"""Reusable widgets: cover cache, toggle switch, track rows, toast, blur helper."""

from typing import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QUrl,
    Property,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from icons import make_icon, placeholder_art, rounded


def human_time(ms: int) -> str:
    if not ms or ms < 0:
        return "--:--"
    seconds = int(ms // 1000)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


class CoverCache(QWidget):
    """Async network cover loader shared by all views (lives on the GUI thread)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setVisible(False)
        self.nam = QNetworkAccessManager(self)
        self.cache: dict[str, QPixmap] = {}
        self.pending: dict[str, list[Callable]] = {}

    def get(self, url: str, callback: Callable[[QPixmap | None], None]) -> None:
        if not url:
            callback(None)
            return
        if url in self.cache:
            callback(self.cache[url])
            return
        if url in self.pending:
            self.pending[url].append(callback)
            return
        self.pending[url] = [callback]
        request = QNetworkRequest(QUrl(url))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                              QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self.nam.get(request)
        reply.finished.connect(lambda r=reply, u=url: self._on_finished(u, r))

    def _on_finished(self, url: str, reply) -> None:
        pix = QPixmap()
        data = reply.readAll()
        if data and data.size():
            pix.loadFromData(bytes(data))
        callbacks = self.pending.pop(url, [])
        if not pix.isNull():
            self.cache[url] = pix
            if len(self.cache) > 300:
                self.cache.pop(next(iter(self.cache)))
        for cb in callbacks:
            try:
                cb(None if pix.isNull() else pix)
            except RuntimeError:
                pass
        reply.deleteLater()


class ToggleSwitch(QAbstractButton):
    def __init__(self, checked: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(42, 22)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        track = QColor(110, 139, 255) if self.isChecked() else QColor(255, 255, 255, 38)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        knob_x = 3 + (w - h + 2) * self._pos
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(knob_x, 3, h - 6, h - 6))


class PillToggle(QAbstractButton):
    """Whole-pill switch: text stays centered, frosted knob slides.

    The capsule is intentionally translucent, so it picks up the lyric cover
    colour behind it.  OFF is a dim grey glass; ON tints toward the current
    accent and adds a brighter rim, in the spirit of Apple's frosted capsules.

    ``label`` / ``width`` / ``pill_width`` keep the geometry reusable for the
    Folia capsule.
    """

    def __init__(self, checked: bool = False, parent=None, label: str = "Folia",
                 width: int = 108, pill_width: float = 84.0,
                 font_size: int = 15) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self._label = label
        self._pill_w = float(pill_width)
        self._font_size = int(font_size)
        self.setFixedSize(int(width), 38)
        self.setFocusPolicy(Qt.StrongFocus)
        self._pos = 1.0 if checked else 0.0
        self._accent = QColor("#6E8BFF")
        self._press_x = None
        self._dragged = False
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(190)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, value: float) -> None:
        self._pos = max(0.0, min(1.0, float(value)))
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def set_accent(self, color: QColor) -> None:
        if color and color.isValid():
            self._accent = QColor(color)
            self.update()

    def sync_checked(self, checked: bool) -> None:
        """Programmatic state sync without emitting toggled() or animating."""
        self.blockSignals(True)
        self.setChecked(bool(checked))
        self.blockSignals(False)
        self._anim.stop()
        self._set_knob(1.0 if checked else 0.0)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _pill_width(self) -> float:
        return self._pill_w

    def _hit_pos(self, x: float) -> float:
        travel = max(1.0, self.width() - 8 - self._pill_width())
        return max(0.0, min(1.0, (x - 4 - self._pill_width() / 2) / travel))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_x = event.position().x()
            self._dragged = False
            self.setFocus()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_x is not None:
            if abs(event.position().x() - self._press_x) > 4:
                self._dragged = True
            self._set_knob(self._hit_pos(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._press_x is not None:
            self._press_x = None
            if self._dragged:
                self.setChecked(self._pos >= 0.5)
            else:
                self.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        checked = self.isChecked()
        accent = self._accent

        # Outer capsule: dim grey glass when off, accent-tinted glass when on.
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 16 if not checked else 38))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)

        border = QColor(255, 255, 255, 42 if not checked else 0)
        if checked:
            border = QColor(min(255, accent.red() + 34), min(255, accent.green() + 34),
                            min(255, accent.blue() + 34), 170)
        p.setPen(border)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(0.75, 0.75, w - 1.5, h - 1.5), (h - 1.5) / 2, (h - 1.5) / 2)

        pill_w = self._pill_width()
        travel = w - 8 - pill_w
        x = 4 + travel * self._pos
        pill = QRectF(x, 4, pill_w, h - 8)
        if checked:
            hot = accent.lighter(145)
            grad = QLinearGradient(pill.topLeft(), pill.bottomRight())
            grad.setColorAt(0.0, QColor(hot.red(), hot.green(), hot.blue(), 150))
            grad.setColorAt(0.65, QColor(accent.red(), accent.green(), accent.blue(), 105))
            grad.setColorAt(1.0, QColor(255, 255, 255, 35))
            p.setBrush(grad)
            p.setPen(QColor(255, 255, 255, 120))
        else:
            p.setBrush(QColor(255, 255, 255, 20))
            p.setPen(QColor(255, 255, 255, 30))
        p.drawRoundedRect(pill, (h - 8) / 2, (h - 8) / 2)

        # Soft inner top light, like a glass bead.
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 34 if checked else 14))
        p.drawRoundedRect(QRectF(pill.left() + 6, 6, pill.width() - 12, (h - 12) / 2),
                          (h - 12) / 4, (h - 12) / 4)

        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(self._font_size)
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255, 250 if checked else 118))
        p.drawText(QRectF(0, 0, w, h), Qt.AlignHCenter | Qt.AlignVCenter, self._label)

        if self.hasFocus():
            p.setPen(QColor(accent.red(), accent.green(), accent.blue(), 110))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(2, 2, w - 4, h - 4), (h - 4) / 2, (h - 4) / 2)


class PillButton(QAbstractButton):
    """Text-only glass capsule that matches the Folia toggle's visual language."""

    def __init__(self, text: str, parent=None, width: int = 108,
                 height: int = 38, font_size: int = 14) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(int(width), int(height))
        self._font_size = int(font_size)
        self._hovered = False
        self._pressed = False

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        alpha = 34 if self._pressed else (28 if self._hovered else 20)
        p.setBrush(QColor(255, 255, 255, alpha))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.setPen(QColor(255, 255, 255, 64 if self._hovered else 42))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(0.75, 0.75, w - 1.5, h - 1.5),
                          (h - 1.5) / 2, (h - 1.5) / 2)
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(self._font_size)
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255, 245 if self._hovered else 210))
        p.drawText(QRectF(0, 0, w, h), Qt.AlignHCenter | Qt.AlignVCenter,
                   self.text())
        if self.hasFocus():
            p.setPen(QColor(255, 255, 255, 80))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(2, 2, w - 4, h - 4), (h - 4) / 2, (h - 4) / 2)


class ChipButton(QPushButton):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Chip")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)


class Toast(QLabel):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setStyleSheet(
            "QLabel#Toast{background:rgba(20,22,32,235);border:1px solid rgba(255,255,255,0.12);"
            "border-radius:14px;padding:10px 18px;color:#E9EAF2;font-size:12px;}"
        )
        self.setAlignment(Qt.AlignCenter)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, msec: int = 2600) -> None:
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent:
            self.move((parent.width() - self.width()) // 2, parent.height() - 170)
        self.show()
        self.raise_()
        self._timer.start(msec)


class ResultRow(QFrame):
    playRequested = None
    queueRequested = None

    def __init__(self, track: dict, index: int, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultRow")
        self.track = track
        self.setFixedHeight(66)
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 12, 8)
        row.setSpacing(12)

        self.index_label = QLabel(str(index))
        self.index_label.setObjectName("IndexBadge")
        self.index_label.setFixedWidth(22)
        self.index_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.index_label)

        self.cover = QLabel()
        self.cover.setFixedSize(46, 46)
        self.cover.setStyleSheet("border-radius:10px;background:rgba(255,255,255,0.06);")
        self.cover.setScaledContents(False)
        row.addWidget(self.cover)
        covers.get(track.get("cover", ""), self._set_cover)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        text_col.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(self._elide(track.get("title", ""), 34))
        self.title.setObjectName("TrackTitle")
        meta_bits = [b for b in [track.get("artist", ""), track.get("album", "")] if b]
        self.meta = QLabel(self._elide(" · ".join(meta_bits), 48))
        self.meta.setObjectName("TrackMeta")
        text_col.addWidget(self.title)
        text_col.addWidget(self.meta)
        row.addLayout(text_col, 1)

        src = QLabel(track.get("source_name", ""))
        src.setObjectName("SourceBadge")
        src.setFixedHeight(24)
        src.setAlignment(Qt.AlignCenter)
        row.addWidget(src)

        dur = QLabel()
        if track.get("is_live"):
            dur.setText("直播")
        elif track.get("is_preview"):
            dur.setText("试听 30s")
            dur.setToolTip(f"完整曲目 {human_time((track.get('duration') or 0) * 1000)}，实际播放 Apple 30 秒试听")
        elif not track.get("duration"):
            dur.setText("整首")
            dur.setToolTip("来源未提供时长，播放时按音频流实际长度显示")
        else:
            dur.setText(human_time((track.get("duration") or 0) * 1000))
        dur.setObjectName("TrackMeta")
        dur.setFixedWidth(58)
        dur.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(dur)

        self.queue_btn = QPushButton()
        self.queue_btn.setObjectName("IconBtn")
        self.queue_btn.setFixedSize(30, 30)
        self.queue_btn.setIcon(make_icon("plus", "#8A8FA3", 16))
        self.queue_btn.setToolTip("加入播放队列")
        self.queue_btn.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.queue_btn)

        self.favoriteRequested = None
        self._favorite = False
        self.fav_btn = QPushButton()
        self.fav_btn.setObjectName("FavBtn")
        self.fav_btn.setFixedSize(26, 26)
        self.fav_btn.setCursor(Qt.PointingHandCursor)
        self.fav_btn.clicked.connect(self._on_favorite)
        row.addWidget(self.fav_btn)
        self.set_favorite(False)

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("IconBtn")
        self.play_btn.setFixedSize(34, 34)
        self.play_btn.setIcon(make_icon("play", "#E9EAF2", 18))
        self.play_btn.setToolTip("播放")
        self.play_btn.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.play_btn)

        self.playlistRequested = None
        self.matchBindingRequested = None
        self.bind_btn = QPushButton()
        self.bind_btn.setObjectName("IconBtn")
        self.bind_btn.setFixedSize(30, 30)
        self.bind_btn.setIcon(make_icon("gear", "#A6ACC2", 16))
        self.bind_btn.setToolTip("这首歌已绑定默认音源，点击可解绑或更换")
        self.bind_btn.setCursor(Qt.PointingHandCursor)
        self.bind_btn.setVisible(False)
        self.bind_btn.clicked.connect(self._on_binding)
        row.addWidget(self.bind_btn)

        self.playlist_btn = QPushButton()
        self.playlist_btn.setObjectName("IconBtn")
        self.playlist_btn.setFixedSize(30, 30)
        self.playlist_btn.setIcon(make_icon("note", "#8A8FA3", 16))
        self.playlist_btn.setToolTip("加入歌单")
        self.playlist_btn.setCursor(Qt.PointingHandCursor)
        self.playlist_btn.clicked.connect(self._on_playlist)
        row.addWidget(self.playlist_btn)

        self.queue_btn.clicked.connect(self._on_queue)
        self.play_btn.clicked.connect(self._on_play)

    def _set_cover(self, pix: QPixmap | None) -> None:
        if pix and not pix.isNull():
            self.cover.setPixmap(rounded(pix, 46, 10))
        else:
            art = placeholder_art(46, 46, self.track.get("title", "A"),
                                  self.track.get("source_accent", "#6E8BFF"))
            pm = rounded(art, 46, 10)
            self.cover.setPixmap(pm)

    @staticmethod
    def _elide(text: str, width_chars: int) -> str:
        text = text or ""
        return text if len(text) <= width_chars else text[: width_chars - 1] + "…"

    def _on_play(self) -> None:
        if callable(getattr(self, "playRequested", None)):
            self.playRequested(self.track)

    def _on_queue(self) -> None:
        if callable(getattr(self, "queueRequested", None)):
            self.queueRequested(self.track)

    def _on_favorite(self) -> None:
        if callable(getattr(self, "favoriteRequested", None)):
            self.favoriteRequested(self.track)

    def _on_playlist(self) -> None:
        if callable(getattr(self, "playlistRequested", None)):
            self.playlistRequested(self.track)

    def _on_binding(self, *_args) -> None:
        if callable(getattr(self, "matchBindingRequested", None)):
            self.matchBindingRequested(self.track)

    def set_match_bound(self, bound: bool) -> None:
        self.bind_btn.setVisible(bool(bound))

    def set_favorite(self, on: bool) -> None:
        self._favorite = bool(on)
        color = "#FF2E55" if on else "#8A8FA3"
        self.fav_btn.setIcon(make_icon("heart_filled" if on else "heart", color, 14))
        self.fav_btn.setToolTip("取消收藏" if on else "收藏到「我喜欢」")
        self.fav_btn.setProperty("fav", "1" if on else "0")
        self.fav_btn.style().unpolish(self.fav_btn)
        self.fav_btn.style().polish(self.fav_btn)

    def is_favorite(self) -> bool:
        return self._favorite

    def mouseDoubleClickEvent(self, event) -> None:
        self._on_play()
        super().mouseDoubleClickEvent(event)


class SeekSlider(QSlider):
    """Horizontal progress slider that also seeks on a plain click on the groove."""

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(180)

    def _value_at(self, x: float) -> int:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        pos = x - groove.x() - handle.width() / 2
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(round(pos)), span, opt.upsideDown)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.maximum() > self.minimum():
            self.setSliderDown(True)
            self.setValue(self._value_at(event.position().x()))
            self.sliderMoved.emit(self.value())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (self.isSliderDown() and event.buttons() & Qt.LeftButton
                and self.maximum() > self.minimum()):
            self.setValue(self._value_at(event.position().x()))
            self.sliderMoved.emit(self.value())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.isSliderDown() and event.button() == Qt.LeftButton:
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ClickableLabel(QLabel):
    """QLabel that emits clicked() on a left-button release."""

    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def blur_pixmap(pix: QPixmap, radius: int = 34) -> QPixmap:
    """Cheap stack-blur; good enough for the immersive backdrop."""
    if pix.isNull():
        return pix
    img = pix.toImage().scaled(480, 480, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    for _ in range(3):
        img = img.scaled(max(24, img.width() // 4), max(24, img.height() // 4),
                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        img = img.scaled(480, 480, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return QPixmap.fromImage(img)


class SectionHeading(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        col.addWidget(self.title)
        if subtitle:
            self.sub = QLabel(subtitle)
            self.sub.setObjectName("PageSub")
            col.addWidget(self.sub)


def gradient_badge_art(text: str, accent: str, size: int = 44) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    base = QColor(accent)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, base.lighter(115))
    grad.setColorAt(1, QColor(base.red() // 2, base.green() // 2, base.blue() // 2 + 20))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), 10, 10)
    p.fillPath(path, grad)
    p.setPen(QColor(255, 255, 255, 235))
    p.setFont(QFont("Segoe UI", int(size * 0.4), QFont.Bold))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, (text or "A")[:1].upper())
    p.end()
    return pm
