"""Manual lyric search / replacement dialog.

Every candidate already carries parsed lines, so choosing one can update both
the native lyric page and the Folia web stage in a single call.
"""

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from icons import make_icon


class _LyricSearchSignals(QObject):
    done = Signal(list)


class _LyricSearchWorker(QRunnable):
    def __init__(self, track: dict, proxy: str) -> None:
        super().__init__()
        self.track = dict(track)
        self.proxy = proxy
        self.signals = _LyricSearchSignals()

    def run(self) -> None:
        try:
            from lyrics import search_lyric_candidates
            rows = search_lyric_candidates(self.track, proxy=self.proxy)
        except Exception:  # noqa: BLE001 - a failed search is an empty picker
            rows = []
        try:
            self.signals.done.emit(list(rows or []))
        except RuntimeError:
            pass


class LyricCandidateRow(QFrame):
    selected = Signal(int)

    def __init__(self, candidate: dict, index: int, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LyricCandidate")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(96)
        self._index = index
        self._selected = False
        self.setStyleSheet(
            "QFrame#LyricCandidate{background:rgba(255,255,255,0.045);"
            "border:1px solid rgba(255,255,255,0.08);border-radius:10px;}"
            "QFrame#LyricCandidate:hover{background:rgba(255,255,255,0.09);"
            "border-color:rgba(124,140,255,0.45);}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(7)
        title = QLabel(candidate.get("title", "") or "未命名歌曲")
        title.setStyleSheet("color:#F4F4F6;font-size:14px;font-weight:600;")
        head.addWidget(title)
        if candidate.get("_artistMatch") and candidate.get("_albumMatch"):
            badge_text, color = "作者/专辑一致", "#9EE6B8"
        elif candidate.get("_artistMatch"):
            badge_text, color = "作者一致", "#AFC4FF"
        elif candidate.get("_exact"):
            badge_text, color = "歌名一致", "#9EE6B8"
        elif candidate.get("_albumMatch"):
            badge_text, color = "专辑一致", "#AFC4FF"
        else:
            badge_text = f"歌名重合 {int(candidate.get('_titleScore') or 0)}%"
            color = "rgba(255,255,255,0.55)"
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            "color:%s;font-size:10px;font-weight:600;"
            "background:rgba(255,255,255,0.075);border-radius:7px;padding:2px 7px;" % color)
        head.addWidget(badge)
        head.addStretch(1)
        col.addLayout(head)
        meta = QLabel(" · ".join(x for x in [
            candidate.get("artist", ""), candidate.get("album", "")
        ] if x) or "未知作者 / 未知专辑")
        meta.setStyleSheet("color:rgba(255,255,255,0.48);font-size:11px;")
        col.addWidget(meta)
        preview = QLabel(self._preview(candidate))
        preview.setStyleSheet("color:rgba(255,255,255,0.62);font-size:11px;")
        preview.setWordWrap(False)
        col.addWidget(preview)
        row.addLayout(col, 1)

        sync = candidate.get("_lyric") or {}
        sync_badge = QLabel("逐句" if sync.get("synced") else "纯文本")
        sync_badge.setFixedSize(48, 22)
        sync_badge.setAlignment(Qt.AlignCenter)
        sync_badge.setStyleSheet(
            "color:%s;font-size:10px;background:rgba(255,255,255,0.06);"
            "border-radius:7px;padding:2px 5px;" % (
                "#9EE6B8" if sync.get("synced") else "rgba(255,255,255,0.48)"))
        row.addWidget(sync_badge)

        source = QLabel(str(candidate.get("source") or ""))
        source.setFixedWidth(84)
        source.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        source.setStyleSheet("color:rgba(255,255,255,0.56);font-size:10px;")
        row.addWidget(source)

    @staticmethod
    def _preview(candidate: dict) -> str:
        lines = ((candidate.get("_lyric") or {}).get("lines") or [])
        texts = [str(text).strip() for _ms, text in lines if str(text).strip()]
        if not texts:
            return "没有可显示的歌词内容"
        return "  /  ".join(texts[:2])[:72]

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.setStyleSheet(
            "QFrame#LyricCandidate{background:%s;border:1px solid %s;"
            "border-radius:10px;}"
            "QFrame#LyricCandidate:hover{background:rgba(255,255,255,0.09);"
            "border-color:rgba(124,140,255,0.65);}" % (
                "rgba(124,140,255,0.16)" if selected else "rgba(255,255,255,0.045)",
                "rgba(124,140,255,0.85)" if selected else "rgba(255,255,255,0.08)",
            ))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.selected.emit(self._index)
            event.accept()
            return
        super().mousePressEvent(event)


class LyricSearchDialog(QDialog):
    """Auto-search same-song lyric versions and replace the current sheet."""

    chosen = Signal(dict, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("歌词搜索")
        self.setModal(True)
        self.setFixedSize(820, 640)
        self.setStyleSheet(
            "QDialog{background:#0B0D13;}"
            "QLabel{color:#F4F4F6;}"
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}"
            "QCheckBox{color:rgba(255,255,255,0.72);font-size:12px;spacing:7px;}")
        self._track: dict = {}
        self._proxy = ""
        self._candidates: list[dict] = []
        self._rows: list[LyricCandidateRow] = []
        self._selected_index = -1
        self._token = 0
        self._worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(8)

        head = QHBoxLayout()
        caption = QLabel("歌词搜索")
        caption.setStyleSheet("color:#FFFFFF;font-size:18px;font-weight:700;")
        head.addWidget(caption)
        head.addStretch(1)
        close = QPushButton()
        close.setFixedSize(30, 30)
        close.setIcon(make_icon("close", "#FFFFFF", 15))
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.08);border:none;border-radius:15px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.18);}")
        close.clicked.connect(self.reject)
        head.addWidget(close)
        root.addLayout(head)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:rgba(255,255,255,0.54);font-size:12px;")
        root.addWidget(self.note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.list_layout = QVBoxLayout(self.body)
        self.list_layout.setContentsMargins(0, 4, 4, 4)
        self.list_layout.setSpacing(7)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.remember = QCheckBox("默认歌词")
        self.remember.setToolTip(
            "勾选后，以后播放这首歌会直接使用这份歌词；不勾选只替换当前播放。")
        footer.addWidget(self.remember)
        footer.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setFixedSize(82, 34)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.08);color:#C8CBD8;"
            "border:none;border-radius:17px;font-size:12px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.16);}")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.confirm = QPushButton("确认替换")
        self.confirm.setFixedSize(104, 34)
        self.confirm.setCursor(Qt.PointingHandCursor)
        self.confirm.setStyleSheet(
            "QPushButton{background:#5264B8;color:#FFFFFF;border:none;"
            "border-radius:17px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#6678D0;}"
            "QPushButton:disabled{background:rgba(255,255,255,0.08);color:#777B8C;}")
        self.confirm.clicked.connect(self._confirm_selected)
        footer.addWidget(self.confirm)
        root.addLayout(footer)

    def open_for(self, track: dict, proxy: str = "", default_active: bool = False) -> None:
        self._track = dict(track or {})
        self._proxy = proxy or ""
        self._token += 1
        token = self._token
        self._candidates = []
        self._selected_index = -1
        self.remember.setChecked(bool(default_active))
        self.confirm.setEnabled(False)
        self._clear_rows()
        loading = QLabel("正在搜索同名歌词…")
        loading.setAlignment(Qt.AlignCenter)
        loading.setStyleSheet("color:rgba(255,255,255,0.5);font-size:13px;padding:40px;")
        self.list_layout.addWidget(loading)
        self.note.setText(
            f"正在为《{self._track.get('title') or '当前歌曲'}》搜索歌词；"
            "结果按作者、专辑、歌名关键词重合度排序。")
        self.show()
        self.raise_()
        self.activateWindow()
        worker = _LyricSearchWorker(self._track, self._proxy)
        worker.signals.done.connect(lambda rows, t=token: self._on_results(rows, t))
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _clear_rows(self) -> None:
        self._rows = []
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _on_results(self, candidates: list[dict], token: int) -> None:
        if token != self._token:
            return
        self._worker = None
        self._clear_rows()
        self._candidates = [dict(c) for c in (candidates or [])]
        self._selected_index = 0 if self._candidates else -1
        if not self._candidates:
            empty = QLabel("没有找到包含可用歌词的版本")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:rgba(255,255,255,0.5);font-size:13px;padding:40px;")
            self.list_layout.addWidget(empty)
            self.note.setText("没有搜索到可替换的歌词版本，可以稍后重试。")
            self.confirm.setEnabled(False)
            return
        for index, candidate in enumerate(self._candidates):
            row = LyricCandidateRow(candidate, index, self.body)
            row.selected.connect(self._select)
            self.list_layout.addWidget(row)
            self._rows.append(row)
        self.list_layout.addStretch(1)
        self.note.setText(
            f"找到 {len(self._candidates)} 个歌词版本，按作者、专辑、歌名关键词重合度排序；"
            "选中后点「确认替换」。")
        self.confirm.setEnabled(True)
        self._select(0)

    def _select(self, index: int) -> None:
        if not (0 <= index < len(self._rows)):
            return
        self._selected_index = index
        for i, row in enumerate(self._rows):
            row.set_selected(i == index)

    def _confirm_selected(self) -> None:
        if not (0 <= self._selected_index < len(self._candidates)):
            return
        self.chosen.emit(dict(self._candidates[self._selected_index]),
                         self.remember.isChecked())
        self.accept()
