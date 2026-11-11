"""“你可能想听这首歌” manual source picker.

Search results are only treated as a safe match when title, album and artist all
line up.  Everything else is offered here, ordered by artist > album > title.
"""

from PySide6.QtCore import Qt, Signal
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

from components import CoverCache
from icons import make_icon, placeholder_art


class MatchCandidateRow(QFrame):
    playRequested = Signal(int)
    selected = Signal(int)

    def __init__(self, candidate: dict, index: int, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("MatchCandidate")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(76)
        self._index = index
        self._selected = False
        self.setStyleSheet(
            "QFrame#MatchCandidate{background:rgba(255,255,255,0.045);"
            "border:1px solid rgba(255,255,255,0.08);border-radius:10px;}"
            "QFrame#MatchCandidate:hover{background:rgba(255,255,255,0.09);"
            "border-color:rgba(124,140,255,0.45);}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)

        cover = QLabel()
        cover.setFixedSize(54, 54)
        covers.get(candidate.get("cover", ""), lambda pix: self._set_cover(pix, cover))
        row.addWidget(cover)

        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(candidate.get("title", "") or "未命名歌曲")
        title.setStyleSheet("color:#F4F4F6;font-size:14px;font-weight:600;")
        meta = QLabel(" · ".join(x for x in [
            candidate.get("artist", ""), candidate.get("album", "")
        ] if x) or "未知歌手 / 未知专辑")
        meta.setStyleSheet("color:rgba(255,255,255,0.48);font-size:11px;")
        col.addWidget(title)
        col.addWidget(meta)
        row.addLayout(col, 1)

        badge = QLabel(candidate.get("source_name", "") or candidate.get("kind", ""))
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            "color:rgba(255,255,255,0.65);font-size:10px;"
            "background:rgba(255,255,255,0.08);border-radius:8px;padding:2px 7px;")
        row.addWidget(badge)

        score = int(round(float(candidate.get("_matchScore") or 0)))
        exact = bool(candidate.get("_exact"))
        pct = QLabel("歌名/作者一致" if exact else f"匹配度 {score}%")
        pct.setFixedWidth(92)
        pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        pct.setStyleSheet(
            f"color:{'#9EE6B8' if exact else 'rgba(255,255,255,0.58)'};"
            "font-size:11px;font-weight:600;")
        row.addWidget(pct)

        playable = bool(candidate.get("_playable"))
        state = QLabel("正常" if playable else "不可播放")
        state.setFixedWidth(64)
        state.setAlignment(Qt.AlignCenter)
        state.setToolTip("已经尝试获取该音源的播放地址"
                         if playable else "暂时没有拿到可播放地址")
        state.setStyleSheet(
            "color:%s;font-size:11px;font-weight:600;background:%s;"
            "border-radius:8px;padding:2px 6px;" % (
                "#9EE6B8" if playable else "#FF8A9A",
                "rgba(74,190,120,0.16)" if playable else "rgba(255,80,100,0.14)",
            ))
        row.addWidget(state)

        play = QPushButton("播放")
        play.setCursor(Qt.PointingHandCursor)
        play.setFixedSize(58, 30)
        play.setEnabled(playable)
        play.setStyleSheet(
            "QPushButton{background:#31364F;color:#FFFFFF;border:none;"
            "border-radius:15px;font-size:12px;}"
            "QPushButton:hover{background:#465080;}"
            "QPushButton:disabled{background:rgba(255,255,255,0.06);color:#666A7B;}")
        play.clicked.connect(lambda: self.playRequested.emit(self._index))
        row.addWidget(play)
        self.play_btn = play

    def _set_cover(self, pix, label: QLabel) -> None:
        if pix and not pix.isNull():
            label.setPixmap(pix.scaled(54, 54, Qt.KeepAspectRatioByExpanding,
                                       Qt.SmoothTransformation))
        else:
            label.setPixmap(placeholder_art(54, 54, "match"))

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.setStyleSheet(
            "QFrame#MatchCandidate{background:%s;"
            "border:1px solid %s;border-radius:10px;}"
            "QFrame#MatchCandidate:hover{background:rgba(255,255,255,0.09);"
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


class MatchDialog(QDialog):
    """Modal picker used when a metadata match is not exact."""

    chosen = Signal(dict, bool)

    def __init__(self, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("你可能想听这首歌")
        self.setModal(True)
        self.setFixedSize(760, 616)
        self.setStyleSheet(
            "QDialog{background:#0B0D13;}"
            "QLabel{color:#F4F4F6;}"
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}"
            "QCheckBox{color:rgba(255,255,255,0.72);font-size:12px;spacing:7px;}")
        self._covers = covers
        self._candidates: list[dict] = []
        self._rows: list[MatchCandidateRow] = []
        self._selected_index = -1
        self._original: dict = {}
        self._binding_mode = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("你可能想听这首歌")
        title.setStyleSheet("color:#FFFFFF;font-size:18px;font-weight:700;")
        head.addWidget(title)
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
        self.remember = QCheckBox("默认匹配该歌曲")
        self.remember.setToolTip(
            "勾选后，下次播放这首歌会直接使用你选择的音源，不再重新匹配。")
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
        self.play_btn = QPushButton("播放选中")
        self.play_btn.setFixedSize(104, 34)
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.setStyleSheet(
            "QPushButton{background:#5264B8;color:#FFFFFF;border:none;"
            "border-radius:17px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:#6678D0;}"
            "QPushButton:disabled{background:rgba(255,255,255,0.08);color:#777B8C;}")
        self.play_btn.clicked.connect(self._play_selected)
        footer.addWidget(self.play_btn)
        root.addLayout(footer)

    def _clear_rows(self) -> None:
        self._rows = []
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _set_cover(self, pix, label: QLabel) -> None:
        if pix and not pix.isNull():
            label.setPixmap(pix.scaled(54, 54, Qt.KeepAspectRatioByExpanding,
                                       Qt.SmoothTransformation))
        else:
            label.setPixmap(placeholder_art(54, 54, "match"))

    def open_for(self, original: dict, candidates: list[dict],
                 binding_mode: bool = False) -> None:
        self._original = dict(original or {})
        self._binding_mode = bool(binding_mode)
        self._candidates = [dict(c) for c in (candidates or [])]
        self._selected_index = 0 if self._candidates else -1
        self.remember.setChecked(self._binding_mode)
        self.remember.setVisible(not self._binding_mode)
        self.play_btn.setText("绑定选中" if self._binding_mode else "播放选中")
        self._clear_rows()
        if self._binding_mode:
            self.note.setText("选择一个音源作为这首歌的默认匹配；绿色为可播放，红色为暂时无法获取播放地址。")
        else:
            self.note.setText(
                "未能确认“歌名、歌手、专辑”全部一致，下面按歌手、专辑、歌名的顺序列出相似音源。")
        if not self._candidates:
            empty = QLabel("没有找到足够相似的音源")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:rgba(255,255,255,0.5);font-size:13px;padding:40px;")
            self.list_layout.addWidget(empty)
            self.play_btn.setEnabled(False)
        else:
            self.play_btn.setEnabled(True)
            for i, cand in enumerate(self._candidates):
                row = MatchCandidateRow(cand, i, self._covers, self.body)
                row.playRequested.connect(self._play_index)
                row.selected.connect(self._select)
                self.list_layout.addWidget(row)
                self._rows.append(row)
            self.list_layout.addStretch(1)
            self._select(0)
        self.show()
        self.raise_()
        self.activateWindow()

    def _select(self, index: int) -> None:
        if not (0 <= index < len(self._rows)):
            return
        self._selected_index = index
        for i, row in enumerate(self._rows):
            row.set_selected(i == index)
        self.play_btn.setEnabled(bool(self._candidates[index].get("_playable")))

    def _play_selected(self) -> None:
        self._play_index(self._selected_index)

    def _play_index(self, index: int) -> None:
        if not (0 <= index < len(self._candidates)):
            return
        if self._binding_mode and not self._candidates[index].get("_playable"):
            return
        self.chosen.emit(dict(self._candidates[index]), self.remember.isChecked())
        self.accept()
