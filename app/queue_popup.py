"""A floating now-playing list, opened from the player bar or lyric page."""

from PySide6.QtCore import QPoint, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from components import CoverCache
from icons import make_icon
from pages import QueueRow


class QueuePopup(QFrame):
    playRequested = Signal(int)
    removeRequested = Signal(int)
    clearRequested = Signal()
    playlistRequested = Signal(dict)

    def __init__(self, covers: CoverCache, parent=None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("QueuePopup")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._covers = covers
        self._rows: list[QueueRow] = []
        self.setFixedWidth(480)
        self.setMinimumHeight(320)
        self.setMaximumHeight(540)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("播放列表")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        self.count = QLabel("0 首")
        self.count.setObjectName("Muted")
        head.addWidget(self.count)
        head.addStretch(1)
        clear = QPushButton()
        clear.setObjectName("IconBtn")
        clear.setFixedSize(28, 28)
        clear.setIcon(make_icon("trash", "#8A8FA3", 14))
        clear.setToolTip("清空播放列表")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self.clearRequested.emit)
        head.addWidget(clear)
        close = QPushButton()
        close.setObjectName("IconBtn")
        close.setFixedSize(28, 28)
        close.setIcon(make_icon("close", "#8A8FA3", 14))
        close.setToolTip("关闭")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.hide)
        head.addWidget(close)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.list_layout = QVBoxLayout(self.body)
        self.list_layout.setContentsMargins(0, 2, 4, 2)
        self.list_layout.setSpacing(1)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        self.empty = QLabel("播放列表还是空的")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty)

        self._pending: list[dict] = []
        self._pending_index = 0
        self._current_id = ""
        self._build_token = 0
        self._build_timer = QTimer(self)
        self._build_timer.setSingleShot(True)
        self._build_timer.setInterval(0)
        self._build_timer.timeout.connect(self._build_batch)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)
        painter.fillPath(path, QColor("#171923"))
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.drawPath(path)
        painter.end()

    def set_queue(self, tracks: list[dict], current_id: str = "") -> None:
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
        self.scroll.setVisible(bool(tracks))
        self._pending = list(tracks)
        self._pending_index = 0
        self._current_id = str(current_id or "")
        self.setFixedHeight(min(540, max(320, 84 + len(tracks) * 64)))
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
            row.playlistRequested.connect(self.playlistRequested.emit)
            self.list_layout.addWidget(row)
            self._rows.append(row)
        if self._pending_index < len(self._pending):
            self._build_timer.start()
            return
        self.list_layout.addStretch(1)

    def _row_is_current(self, track: dict) -> bool:
        return bool(self._current_id) and str(track.get("id") or "") == self._current_id

    def set_current(self, current_id: str) -> None:
        self._current_id = str(current_id or "")
        for row in self._rows:
            row.set_current(self._row_is_current(row.track))

    def show_at(self, anchor: QPoint, gap: int = 10) -> None:
        parent = self.parentWidget()
        self.adjustSize()
        width = self.width()
        height = self.height()
        x = anchor.x() - width + 34
        y = anchor.y() - height - gap
        if parent:
            origin = parent.mapToGlobal(QPoint(0, 0))
            screen_w = parent.width()
            if x < origin.x() + 8:
                x = origin.x() + 8
            if x + width > origin.x() + screen_w - 8:
                x = origin.x() + screen_w - width - 8
            if y < origin.y() + 8:
                y = origin.y() + 8
        self.move(x, y)
        self.show()
        self.raise_()
