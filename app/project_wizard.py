"""Single-window wizard for adding an open-source project to Aria.

Pages:
  project URL -> capability result -> sidecar install / remote config
  -> remote audio test input -> result/confirm

Everything stays inside one QDialog; progress, errors and results are inline.
"""

from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from urllib.parse import quote, urlparse

import requests
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import adapters
import aggregator_tools
import project_scan
import theme
from db import new_id
from icons import make_icon

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except Exception:  # noqa: BLE001
    QAudioOutput = None  # type: ignore[assignment]
    QMediaPlayer = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- workers
class _AnalyzeSignals(QObject):
    done = Signal(dict)
    failed = Signal(str)


class AnalyzeWorker(QRunnable):
    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.signals = _AnalyzeSignals()

    def run(self) -> None:
        try:
            self.signals.done.emit(project_scan.analyze(self.url))
        except Exception as exc:  # noqa: BLE001
            try:
                self.signals.failed.emit(str(exc)[:220])
            except RuntimeError:
                pass


class _InstallSignals(QObject):
    done = Signal(dict)
    failed = Signal(str)


class InstallWorker(QRunnable):
    def __init__(self, url: str, option: dict) -> None:
        super().__init__()
        self.url = url
        self.option = dict(option)
        self.signals = _InstallSignals()

    def run(self) -> None:
        try:
            self.signals.done.emit(aggregator_tools.install(self.url, self.option))
        except Exception as exc:  # noqa: BLE001
            try:
                self.signals.failed.emit(str(exc)[:220])
            except RuntimeError:
                pass


class _TestSignals(QObject):
    done = Signal(dict)


class TestWorker(QRunnable):
    """Search, resolve and perform a cheap HTTP read of the remote audio."""

    def __init__(self, site: dict, title: str, artist: str, album: str,
                 proxy: str = "") -> None:
        super().__init__()
        self.site = dict(site)
        self.title = title
        self.artist = artist
        self.album = album
        self.proxy = proxy
        self.signals = _TestSignals()

    def run(self) -> None:
        result = {"status": "red", "audio": "", "headers": {}, "reason": ""}
        query = " ".join(x for x in (self.title, self.artist, self.album) if x).strip()
        try:
            tracks = adapters.search_site(self.site, query, 20, self.proxy)
        except Exception as exc:  # noqa: BLE001
            result["reason"] = str(exc)[:180]
            self.signals.done.emit(result)
            return
        if not tracks:
            result["reason"] = "没有搜索到结果"
            self.signals.done.emit(result)
            return
        best = _pick_track(tracks, self.title, self.artist, self.album)
        if not best:
            result["status"] = "yellow"
            result["reason"] = "没有匹配到目标歌曲"
            self.signals.done.emit(result)
            return
        try:
            audio = adapters.resolve_audio(best, proxy=self.proxy)
        except Exception as exc:  # noqa: BLE001
            result["status"] = "yellow"
            result["reason"] = str(exc)[:180]
            self.signals.done.emit(result)
            return
        if not audio:
            result["status"] = "yellow"
            result["reason"] = "没有返回播放地址"
            self.signals.done.emit(result)
            return
        result["audio"] = audio
        result["headers"] = dict(best.get("headers") or {})
        result["track"] = best
        try:
            response = requests.get(
                audio, headers={**dict(self.site.get("headers") or {}),
                               **result["headers"]},
                stream=True, timeout=(5, 10), allow_redirects=True)
            try:
                if response.status_code not in (200, 206):
                    result["status"] = "yellow"
                    result["reason"] = f"HTTP {response.status_code}"
                    self.signals.done.emit(result)
                    return
                ctype = (response.headers.get("Content-Type") or "").lower()
                if ctype and not any(x in ctype for x in ("audio", "mpeg", "octet-stream", "flac", "ogg")):
                    result["status"] = "yellow"
                    result["reason"] = "返回内容不是音频"
                    self.signals.done.emit(result)
                    return
            finally:
                response.close()
        except Exception as exc:  # noqa: BLE001
            result["status"] = "yellow"
            result["reason"] = str(exc)[:180]
            self.signals.done.emit(result)
            return
        result["status"] = "green"
        self.signals.done.emit(result)


def _norm(text: str) -> str:
    return re.sub(r"[\s\u3000]+", "", str(text or "").lower())


def _ratio(a: str, b: str) -> float:
    left, right = _norm(a), _norm(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.92
    return SequenceMatcher(None, left, right).ratio()


def _pick_track(tracks: list[dict], title: str, artist: str, album: str) -> dict | None:
    candidates: list[tuple[float, dict]] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        title_score = _ratio(track.get("title", ""), title) if title else 0.0
        artist_score = _ratio(track.get("artist", ""), artist) if artist else 0.0
        album_score = _ratio(track.get("album", ""), album) if album else 0.0
        if title and artist:
            ok = title_score >= 0.45 and artist_score >= 0.55
        elif title:
            ok = title_score >= 0.5
        elif artist:
            ok = artist_score >= 0.6
        elif album:
            ok = album_score >= 0.5
        else:
            ok = True
        if not ok:
            continue
        score = artist_score * 0.50 + album_score * 0.30 + title_score * 0.20
        candidates.append((score, track))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


# --------------------------------------------------------------------------- playback probe
class PlaybackProbe(QObject):
    done = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._finished = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self.player = None
        self.audio = None
        if QMediaPlayer is not None and QAudioOutput is not None:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.audio.setMuted(True)
            self.audio.setVolume(0.0)
            self.player.setAudioOutput(self.audio)
            self.player.errorOccurred.connect(self._on_error)
            self.player.mediaStatusChanged.connect(self._on_status)

    def start(self, url: str) -> None:
        self._finished = False
        if not self.player:
            self._finish(False)
            return
        self.player.stop()
        self.player.setSource(QUrl(url))
        self.player.play()
        self._timer.start(3500)

    def _finish(self, ok: bool) -> None:
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        try:
            if self.player:
                self.player.stop()
        except Exception:  # noqa: BLE001
            pass
        self.done.emit(bool(ok))

    def _on_status(self, status) -> None:
        if not self.player:
            return
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._finish(False)
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._finish(True)
        elif status == QMediaPlayer.MediaStatus.BufferedMedia:
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._finish(True)

    def _on_error(self, *_args) -> None:
        self._finish(False)

    def _on_timeout(self) -> None:
        if not self.player:
            self._finish(False)
            return
        ok = (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
              or self.player.position() > 0)
        self._finish(ok)


# --------------------------------------------------------------------------- dialog
class ProjectWizardDialog(QDialog):
    """One modal window; every step is a page inside it."""

    def __init__(self, parent=None, proxy: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("添加开源项目")
        self.setWindowModality(Qt.ApplicationModal)
        self.setModal(True)
        self.setMinimumSize(760, 640)
        self.resize(800, 680)
        self.proxy = proxy
        self.result_site: dict | None = None
        self.result_tool: dict | None = None

        self._analysis: dict = {}
        self._remote_spec: dict = {}
        self._site: dict = {}
        self._page = "url"
        self._test_status = ""
        self._pending_test: dict = {}
        self._workers: list = []
        self._closing = False
        self._probe = PlaybackProbe(self)
        self._probe.done.connect(self._on_probe_done)
        self.pool = QThreadPool.globalInstance()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)

        head = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("添加开源项目")
        title.setStyleSheet("color:#FFFFFF;font-size:20px;font-weight:700;")
        heading.addWidget(title)
        self.step_label = QLabel("")
        self.step_label.setObjectName("Muted")
        self.step_label.setStyleSheet("font-size:12px;")
        heading.addWidget(self.step_label)
        head.addLayout(heading)
        head.addStretch(1)
        self.close_btn = QPushButton()
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setIcon(make_icon("close", "#FFFFFF", 15))
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.07);border:none;border-radius:15px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.16);}")
        self.close_btn.clicked.connect(self.reject)
        head.addWidget(self.close_btn)
        root.addLayout(head)

        self.stack = QStackedWidget()
        self.pages = {
            "url": self._build_url_page(),
            "result": self._build_result_page(),
            "install": self._build_install_page(),
            "config": self._build_config_page(),
            "test_entry": self._build_test_entry_page(),
            "test_input": self._build_test_input_page(),
            "test_result": self._build_test_result_page(),
            "done": self._build_done_page(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.back_btn = QPushButton("上一步")
        self.back_btn.setObjectName("Ghost")
        self.back_btn.clicked.connect(self._go_back)
        foot.addWidget(self.back_btn)
        self.exit_btn = QPushButton("退出")
        self.exit_btn.setObjectName("Ghost")
        self.exit_btn.clicked.connect(self.reject)
        foot.addWidget(self.exit_btn)
        foot.addStretch(1)
        self.primary_btn = QPushButton("识别并测试")
        self.primary_btn.setObjectName("Primary")
        self.primary_btn.setMinimumWidth(132)
        self.primary_btn.clicked.connect(self._primary_action)
        foot.addWidget(self.primary_btn)
        root.addLayout(foot)

        self._show_page("url")

    # ------------------------------------------------------------------ pages
    def _page_base(self, spacing: int = 12) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(spacing)
        return page, layout

    def _build_url_page(self) -> QWidget:
        page, layout = self._page_base()
        intro = QLabel("输入项目网址，识别它能否作为侧车或远程服务接入 Aria。")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#C8CBD8;font-size:13px;")
        layout.addWidget(intro)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://github.com/owner/repo 或项目网址")
        self.url_input.returnPressed.connect(self._start_analyze)
        layout.addWidget(self.url_input)
        hint = QLabel("支持 GitHub 仓库、项目官网和公开 API 地址；识别过程只读取公开信息，不执行项目代码。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size:12px;")
        layout.addWidget(hint)
        self.url_status = QLabel("")
        self.url_status.setWordWrap(True)
        self.url_status.setStyleSheet("color:#F0A35E;font-size:12px;")
        layout.addWidget(self.url_status)
        layout.addStretch(1)
        return page

    def _build_result_page(self) -> QWidget:
        page, layout = self._page_base()
        self.res_project = QLabel("")
        self.res_project.setStyleSheet("color:#FFFFFF;font-size:16px;font-weight:700;")
        self.res_project.setWordWrap(True)
        layout.addWidget(self.res_project)
        self.res_desc = QLabel("")
        self.res_desc.setObjectName("Muted")
        self.res_desc.setWordWrap(True)
        self.res_desc.setStyleSheet("font-size:12px;")
        layout.addWidget(self.res_desc)

        self.cap_card = QFrame()
        self.cap_card.setObjectName("Card")
        cap_layout = QVBoxLayout(self.cap_card)
        cap_layout.setContentsMargins(18, 16, 18, 16)
        cap_layout.setSpacing(10)
        self.res_cap = QLabel("")
        self.res_cap.setStyleSheet("color:#FFFFFF;font-size:15px;font-weight:600;")
        cap_layout.addWidget(self.res_cap)
        self.choice_box = QWidget()
        choice_layout = QHBoxLayout(self.choice_box)
        choice_layout.setContentsMargins(0, 0, 0, 0)
        self.sidecar_radio = QRadioButton("侧车")
        self.remote_radio = QRadioButton("远程服务")
        self.sidecar_radio.setChecked(True)
        self._choice_group = QButtonGroup(self)
        self._choice_group.addButton(self.sidecar_radio)
        self._choice_group.addButton(self.remote_radio)
        choice_layout.addWidget(self.sidecar_radio)
        choice_layout.addWidget(self.remote_radio)
        choice_layout.addStretch(1)
        cap_layout.addWidget(self.choice_box)
        self.res_evidence = QLabel("")
        self.res_evidence.setWordWrap(True)
        self.res_evidence.setObjectName("Muted")
        self.res_evidence.setStyleSheet("font-size:12px;line-height:1.6;")
        cap_layout.addWidget(self.res_evidence)
        layout.addWidget(self.cap_card)
        layout.addStretch(1)
        return page

    def _build_install_page(self) -> QWidget:
        page, layout = self._page_base()
        self.install_project = QLabel("")
        self.install_project.setStyleSheet("color:#FFFFFF;font-size:16px;font-weight:700;")
        layout.addWidget(self.install_project)
        self.install_info = QLabel("")
        self.install_info.setObjectName("Muted")
        self.install_info.setWordWrap(True)
        self.install_info.setStyleSheet("font-size:12px;line-height:1.6;")
        layout.addWidget(self.install_info)
        warning = QLabel("安装会下载并运行该仓库代码，请只选择你信任的来源。")
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#F0A35E;font-size:12px;")
        layout.addWidget(warning)
        self.install_status = QLabel("")
        self.install_status.setWordWrap(True)
        self.install_status.setStyleSheet("color:#C8CBD8;font-size:12px;")
        layout.addWidget(self.install_status)
        layout.addStretch(1)
        return page

    def _build_config_page(self) -> QWidget:
        page, layout = self._page_base()
        self.config_title = QLabel("")
        self.config_title.setStyleSheet("color:#FFFFFF;font-size:16px;font-weight:700;")
        layout.addWidget(self.config_title)
        self.config_summary = QLabel("")
        self.config_summary.setObjectName("Muted")
        self.config_summary.setWordWrap(True)
        self.config_summary.setStyleSheet("font-size:12px;line-height:1.6;")
        layout.addWidget(self.config_summary)

        self.base_edit = QLineEdit()
        self.base_edit.setPlaceholderText("服务地址，例如 https://music.example.com")
        self.base_edit.textChanged.connect(self._update_config_primary)
        layout.addWidget(self.base_edit)

        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Token（可选）")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.token_edit)

        self.auth_box = QWidget()
        auth_layout = QVBoxLayout(self.auth_box)
        auth_layout.setContentsMargins(0, 0, 0, 0)
        auth_layout.setSpacing(8)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("账号")
        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("密码")
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        auth_layout.addWidget(self.user_edit)
        auth_layout.addWidget(self.pass_edit)
        self.auth_box.setVisible(False)
        layout.addWidget(self.auth_box)

        self.config_status = QLabel("")
        self.config_status.setWordWrap(True)
        self.config_status.setStyleSheet("color:#F0A35E;font-size:12px;")
        layout.addWidget(self.config_status)
        layout.addStretch(1)
        return page

    def _build_test_entry_page(self) -> QWidget:
        page, layout = self._page_base()
        self.test_service = QLabel("")
        self.test_service.setStyleSheet("color:#FFFFFF;font-size:16px;font-weight:700;")
        self.test_service.setWordWrap(True)
        layout.addWidget(self.test_service)
        note = QLabel("用一首歌验证这套远程服务能否搜索、取到音源并播放。")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        note.setStyleSheet("font-size:12px;")
        layout.addWidget(note)
        self.test_entry_status = QLabel("")
        self.test_entry_status.setWordWrap(True)
        self.test_entry_status.setStyleSheet("color:#C8CBD8;font-size:12px;")
        layout.addWidget(self.test_entry_status)
        layout.addStretch(1)
        return page

    def _build_test_input_page(self) -> QWidget:
        page, layout = self._page_base()
        intro = QLabel("自定义测试歌曲")
        intro.setStyleSheet("color:#FFFFFF;font-size:16px;font-weight:700;")
        layout.addWidget(intro)
        hint = QLabel("歌名和歌手只需填一个；填写其中一个时另一个会自动清空。专辑可选。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size:12px;")
        layout.addWidget(hint)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("默认晴天")
        self.title_edit.setText("晴天")
        self.artist_edit = QLineEdit()
        self.artist_edit.setPlaceholderText("默认周杰伦")
        self.artist_edit.setText("周杰伦")
        self.album_edit = QLineEdit()
        self.album_edit.setPlaceholderText("专辑（可选）")
        self.title_edit.textEdited.connect(self._on_title_edited)
        self.artist_edit.textEdited.connect(self._on_artist_edited)
        layout.addWidget(self.title_edit)
        layout.addWidget(self.artist_edit)
        layout.addWidget(self.album_edit)
        self.test_input_status = QLabel("")
        self.test_input_status.setWordWrap(True)
        self.test_input_status.setStyleSheet("color:#F0A35E;font-size:12px;")
        layout.addWidget(self.test_input_status)
        layout.addStretch(1)
        return page

    def _build_test_result_page(self) -> QWidget:
        page, layout = self._page_base()
        layout.addStretch(1)
        self.result_status = QLabel("")
        self.result_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_status.setStyleSheet("font-size:32px;font-weight:700;")
        layout.addWidget(self.result_status)
        self.result_hint = QLabel("建议更换歌名或者歌手重新测试")
        self.result_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_hint.setStyleSheet("color:#8A8FA3;font-size:12px;")
        layout.addWidget(self.result_hint)
        layout.addStretch(1)
        return page

    def _build_done_page(self) -> QWidget:
        page, layout = self._page_base()
        layout.addStretch(1)
        self.done_label = QLabel("")
        self.done_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.done_label.setWordWrap(True)
        self.done_label.setStyleSheet("color:#FFFFFF;font-size:16px;font-weight:600;")
        layout.addWidget(self.done_label)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------ navigation
    def _show_page(self, name: str) -> None:
        self._page = name
        self.stack.setCurrentWidget(self.pages[name])
        self.back_btn.setVisible(name not in ("url", "done"))
        self.exit_btn.setVisible(name != "done")
        self.primary_btn.setVisible(True)
        self.primary_btn.setEnabled(True)
        if name == "url":
            self.step_label.setText("第 1 步 / 共 6 步")
            self.primary_btn.setText("识别并测试")
        elif name == "result":
            self.step_label.setText("第 2 步 / 共 6 步")
            self.primary_btn.setText("下一步")
        elif name == "install":
            self.step_label.setText("第 3 步 / 共 3 步")
            self.primary_btn.setText("安装")
        elif name == "config":
            self.step_label.setText("第 3 步 / 共 6 步")
            self.primary_btn.setText("下一步")
            self._update_config_primary()
        elif name == "test_entry":
            self.step_label.setText("第 4 步 / 共 6 步")
            self.primary_btn.setText("音源测试")
        elif name == "test_input":
            self.step_label.setText("第 5 步 / 共 6 步")
            self.primary_btn.setText("开始测试")
        elif name == "test_result":
            self.step_label.setText("第 6 步 / 共 6 步")
            self.primary_btn.setVisible(self._test_status == "green")
            self.primary_btn.setText("确认")
        elif name == "done":
            self.step_label.setText("完成")
            self.primary_btn.setText("完成")
        if name != "test_result":
            self.primary_btn.setVisible(True)

    def _go_back(self) -> None:
        if self._closing:
            return
        if self._page == "result":
            self._show_page("url")
        elif self._page == "install":
            self._show_page("result")
        elif self._page == "config":
            self._show_page("result")
        elif self._page == "test_entry":
            self._show_page("config")
        elif self._page == "test_input":
            self._show_page("test_entry")
        elif self._page == "test_result":
            self._show_page("test_input")

    def _primary_action(self) -> None:
        if self._closing:
            return
        if self._page == "url":
            self._start_analyze()
        elif self._page == "result":
            self._result_next()
        elif self._page == "install":
            self._start_install()
        elif self._page == "config":
            self._config_next()
        elif self._page == "test_entry":
            self._show_page("test_input")
        elif self._page == "test_input":
            self._start_test()
        elif self._page == "test_result" and self._test_status == "green":
            self._confirm_remote()
        elif self._page == "done":
            self.accept()

    # ------------------------------------------------------------------ url -> analyze
    def _start_analyze(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self.url_status.setText("请输入项目网址")
            return
        self.url_status.setText("正在识别项目…")
        self.primary_btn.setEnabled(False)
        worker = AnalyzeWorker(url)
        worker.signals.done.connect(self._on_analyzed)
        worker.signals.failed.connect(self._on_analyze_failed)
        self._workers.append(worker)
        self.pool.start(worker)

    def _on_analyzed(self, data: dict) -> None:
        self.primary_btn.setEnabled(True)
        self._analysis = dict(data or {})
        self.url_status.setText("")
        self._show_analysis()

    def _on_analyze_failed(self, message: str) -> None:
        self.primary_btn.setEnabled(True)
        self.url_status.setText(message)

    def _show_analysis(self) -> None:
        data = self._analysis
        self.res_project.setText(str(data.get("full_name") or data.get("name") or "项目"))
        desc = str(data.get("description") or "")
        license_text = str(data.get("license") or "")
        extra = " · ".join(x for x in (license_text, str(data.get("branch") or "")) if x)
        self.res_desc.setText((desc + ("\n" if desc and extra else "") + extra).strip())
        caps = list(data.get("capabilities") or [])
        if caps == ["sidecar", "remote"]:
            text, color = "侧车/远程服务可用", "#FFFFFF"
        elif caps == ["sidecar"]:
            text, color = "侧车可用", "#FFFFFF"
        elif caps == ["remote"]:
            text, color = "远程服务可用", "#FFFFFF"
        else:
            text, color = "无法识别", theme.DANGER
        self.res_cap.setText(text)
        self.res_cap.setStyleSheet(f"color:{color};font-size:15px;font-weight:600;")
        both = caps == ["sidecar", "remote"] or caps == ["remote", "sidecar"]
        self.choice_box.setVisible(both)
        if "sidecar" in caps and "remote" in caps:
            self.sidecar_radio.setChecked(True)
        elif "remote" in caps:
            self.remote_radio.setChecked(True)
        else:
            self.sidecar_radio.setChecked(True)
        evidence = data.get("evidence") or []
        if evidence:
            self.res_evidence.setText("· " + "\n· ".join(str(x) for x in evidence))
        else:
            self.res_evidence.setText("")
        self.res_evidence.setVisible(bool(evidence))
        self._show_page("result")
        self.primary_btn.setEnabled(bool(caps))

    def _result_next(self) -> None:
        caps = list(self._analysis.get("capabilities") or [])
        if not caps:
            return
        choice = "remote" if self.remote_radio.isChecked() else "sidecar"
        if choice == "sidecar" and "sidecar" in caps:
            self._show_install()
        elif choice == "remote" and "remote" in caps:
            self._show_config()
        elif "sidecar" in caps:
            self._show_install()
        elif "remote" in caps:
            self._show_config()

    # ------------------------------------------------------------------ sidecar
    def _show_install(self) -> None:
        options = self._analysis.get("options") or []
        option = options[0] if options else {}
        self.install_project.setText(str(self._analysis.get("full_name") or "侧车"))
        start = option.get("start") or (option.get("manifest") or {}).get("start") or ""
        if isinstance(start, (list, tuple)):
            start = " ".join(str(x) for x in start)
        runtime = (option.get("manifest") or {}).get("runtime") or (
            "Node" if option.get("kind") == "node" else "见项目清单")
        self.install_info.setText(
            f"入口：{option.get('path') or '自动识别'}\n"
            f"启动：{start or '按入口自动识别'}\n"
            f"运行环境：{runtime}")
        self.install_status.setText("")
        self._show_page("install")

    def _start_install(self) -> None:
        options = self._analysis.get("options") or []
        if not options:
            self.install_status.setText("没有可安装的侧车入口")
            return
        self.install_status.setText("正在下载、启动并检查接口…")
        self.primary_btn.setEnabled(False)
        worker = InstallWorker(str(self._analysis.get("url") or ""), options[0])
        worker.signals.done.connect(self._on_installed)
        worker.signals.failed.connect(self._on_install_failed)
        self._workers.append(worker)
        self.pool.start(worker)

    def _on_installed(self, entry: dict) -> None:
        self.result_tool = dict(entry or {})
        self.done_label.setText(f"安装成功\n{entry.get('name') or ''}\n{entry.get('base_url') or ''}")
        self._show_page("done")

    def _on_install_failed(self, message: str) -> None:
        self.primary_btn.setEnabled(True)
        self.install_status.setText(message)

    # ------------------------------------------------------------------ remote config
    def _show_config(self) -> None:
        spec = dict(self._analysis.get("remote") or {})
        self._remote_spec = spec
        family = str(spec.get("family") or "generic")
        self.config_title.setText(f"远程服务 · {spec.get('name') or family}")
        self.config_summary.setText(
            "系统已自动识别接口，不需要手动填写映射字段。\n"
            f"适配器：{family}\n"
            f"搜索：{spec.get('method') or 'GET'} {spec.get('search_url') or '自动推断'}")
        self.base_edit.setText(str(spec.get("base_url") or ""))
        self.token_edit.clear()
        self.user_edit.clear()
        self.pass_edit.clear()
        self.token_edit.setVisible(family != "subsonic")
        self.auth_box.setVisible(family == "subsonic")
        self.config_status.setText("")
        self._show_page("config")

    def _update_config_primary(self) -> None:
        if not hasattr(self, "primary_btn"):
            return
        if self._page == "config":
            self.primary_btn.setEnabled(bool(self.base_edit.text().strip()))

    def _config_next(self) -> None:
        base = self.base_edit.text().strip().rstrip("/")
        if not base:
            self.config_status.setText("请填写服务地址")
            return
        family = str(self._remote_spec.get("family") or "generic")
        token = self.token_edit.text().strip()
        if family == "subsonic" and (not self.user_edit.text().strip()
                                     or not self.pass_edit.text().strip()):
            self.config_status.setText("Subsonic / Navidrome 需要账号和密码")
            return
        try:
            self._site = self._build_site(base, token)
        except Exception as exc:  # noqa: BLE001
            self.config_status.setText(str(exc)[:200])
            return
        self.config_status.setText("")
        self.test_service.setText(f"{self._site.get('name')} · {self._site.get('base_url')}")
        self.test_entry_status.setText("")
        self._show_page("test_entry")

    def _build_site(self, base: str, token: str) -> dict:
        spec = dict(self._remote_spec or {})
        family = str(spec.get("family") or "generic")
        name = str(spec.get("name") or self._analysis.get("name") or "远程服务")
        headers = {str(k): str(v) for k, v in (spec.get("headers") or {}).items()}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        mapping = {
            "method": str(spec.get("method") or "GET").upper(),
            "body": spec.get("body") or {"query": "{query}"},
            "query_param": "query",
            "results_path": str(spec.get("results_path") or ""),
            "title": str(spec.get("title") or ""),
            "artist": str(spec.get("artist") or ""),
            "album": str(spec.get("album") or ""),
            "duration": str(spec.get("duration") or ""),
        }
        kind = "generic"
        if family in ("nekomusic", "openapi", "aria", "generic"):
            search_url = (base + "/api/music/search") if family == "nekomusic" else (
                base + "/search" if family in ("aria", "generic") and not spec.get("search_url")
                else str(spec.get("search_url") or base + "/search"))
            audio_from = (base + "/api/music/file/{id}?quality=hq") if family == "nekomusic" else (
                str(spec.get("audio_from") or ""))
            mapping["audio_path"] = str(spec.get("audio_path") or "url")
            if audio_from:
                mapping["audio_from"] = audio_from
        elif family == "subsonic":
            kind = "subsonic"
            user = quote(self.user_edit.text().strip(), safe="")
            password = self.pass_edit.text().strip()
            salt = new_id("s")[:8]
            import hashlib
            token_value = hashlib.md5((password + salt).encode("utf-8")).hexdigest()
            auth = (f"u={user}&t={token_value}&s={salt}&v=1.16.1&c=Aria&f=json")
            search_url = f"{base}/rest/search3?{auth}&songCount=20"
            audio_from = f"{base}/rest/stream?id={{id}}&{auth}"
            mapping.update({
                "results_path": "subsonic-response.searchResult3.match",
                "title": "title",
                "artist": "artist",
                "album": "album",
                "duration": "duration",
                "audio_from": audio_from,
                "audio_path": "",
            })
        else:
            search_url = str(spec.get("search_url") or (base + "/search"))
            mapping["audio_from"] = str(spec.get("audio_from") or "")
            mapping["audio_path"] = str(spec.get("audio_path") or "url")

        return {
            "id": new_id("src"),
            "name": name,
            "kind": kind,
            "tag": "开源远程服务",
            "desc": f"由开源项目向导自动适配：{family}",
            "accent": "#6E8BFF",
            "base_url": base,
            "search_url": search_url,
            "mapping": mapping,
            "headers": headers,
            "api_key": "",
            "enabled": 1,
            "is_builtin": 0,
        }

    # ------------------------------------------------------------------ test
    def _on_title_edited(self, text: str) -> None:
        if text.strip():
            self.artist_edit.clear()

    def _on_artist_edited(self, text: str) -> None:
        if text.strip():
            self.title_edit.clear()

    def _test_values(self) -> tuple[str, str, str]:
        title = self.title_edit.text().strip()
        artist = self.artist_edit.text().strip()
        album = self.album_edit.text().strip()
        if not title and not artist and not album:
            title, artist = "晴天", "周杰伦"
        return title, artist, album

    def _start_test(self) -> None:
        if not self._site:
            self.test_input_status.setText("远程服务配置尚未完成")
            return
        title, artist, album = self._test_values()
        self.test_input_status.setText("正在搜索、取流并验证播放…")
        self.primary_btn.setEnabled(False)
        worker = TestWorker(self._site, title, artist, album, self.proxy)
        worker.signals.done.connect(self._on_test_done)
        self._workers.append(worker)
        self.pool.start(worker)

    def _on_test_done(self, result: dict) -> None:
        if self._closing:
            return
        self._pending_test = dict(result or {})
        status = str(self._pending_test.get("status") or "red")
        audio = str(self._pending_test.get("audio") or "")
        if status == "green" and audio:
            self._probe.start(audio)
            return
        self._test_status = status
        self._show_test_result()

    def _on_probe_done(self, ok: bool) -> None:
        if self._closing:
            return
        if ok and self._pending_test.get("status") == "green":
            self._test_status = "green"
        else:
            self._test_status = "yellow"
        self._show_test_result()

    def _show_test_result(self) -> None:
        if self._test_status == "green":
            text, color = "有效", theme.OK
        elif self._test_status == "yellow":
            text, color = "歌曲获取失败", "#F0A35E"
        else:
            text, color = "无效", theme.DANGER
        self.result_status.setText(text)
        self.result_status.setStyleSheet(
            f"color:{color};font-size:32px;font-weight:700;")
        self._show_page("test_result")

    def _confirm_remote(self) -> None:
        if not self._site:
            return
        self.result_site = dict(self._site)
        self.accept()

    # ------------------------------------------------------------------ close
    def reject(self) -> None:
        self._closing = True
        try:
            if self._probe.player:
                self._probe.player.stop()
        except Exception:  # noqa: BLE001
            pass
        super().reject()
