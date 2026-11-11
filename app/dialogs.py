"""Site editing and web-rule dialogs."""

import json
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import adapters
import site_analyzer
import theme
from db import new_id
from icons import make_icon, rounded, placeholder_art


class _ImportSignals(QObject):
    done = Signal(dict)
    failed = Signal(str)


class PlaylistImportWorker(QRunnable):
    def __init__(self, url: str, proxy: str) -> None:
        super().__init__()
        self.url = url
        self.proxy = proxy
        self.signals = _ImportSignals()

    def run(self) -> None:
        import playlist_import
        try:
            data = playlist_import.fetch_playlist(self.url, proxy=self.proxy)
            self.signals.done.emit(data)
        except Exception as exc:  # noqa: BLE001
            try:
                self.signals.failed.emit(str(exc)[:180])
            except RuntimeError:
                pass


class PlaylistImportDialog(QDialog):
    """Paste a share link, fetch the playlist, let the user rename it, then import."""

    def __init__(self, covers, proxy: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入歌单")
        self.setMinimumWidth(620)
        self._covers = covers
        self._proxy = proxy
        self._data: dict | None = None
        self._workers: list[PlaylistImportWorker] = []
        self.result_data: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)
        title = QLabel("导入其他音乐 app 的歌单")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#FFFFFF;")
        layout.addWidget(title)
        hint = QLabel("支持 Aria 歌单分享链接，以及网易云音乐、QQ音乐、酷狗音乐的公开歌单分享链接。"
                      "导入后只保存歌单信息，播放时会自动从已启用来源匹配音源。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        link_row = QHBoxLayout()
        link_row.setSpacing(10)
        self.link = QLineEdit()
        self.link.setPlaceholderText("粘贴 Aria 分享链接（aria://playlist/…），或网易云 / QQ音乐 / 酷狗歌单链接")
        self.link.setMinimumHeight(40)
        self.link.returnPressed.connect(self.parse)
        link_row.addWidget(self.link, 1)
        self.parse_btn = QPushButton("解析歌单")
        self.parse_btn.setObjectName("Primary")
        self.parse_btn.setMinimumHeight(40)
        self.parse_btn.clicked.connect(self.parse)
        link_row.addWidget(self.parse_btn)
        layout.addLayout(link_row)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.preview = QFrame()
        self.preview.setObjectName("Card")
        preview_row = QHBoxLayout(self.preview)
        preview_row.setContentsMargins(14, 12, 14, 12)
        preview_row.setSpacing(14)
        self.cover = QLabel()
        self.cover.setFixedSize(84, 84)
        self.cover.setPixmap(rounded(placeholder_art(84, 84, "歌单", "#8B7BFF"), 84, 16))
        preview_row.addWidget(self.cover)
        col = QVBoxLayout()
        col.setSpacing(6)
        name_row = QHBoxLayout()
        name_label = QLabel("歌单名称")
        name_label.setObjectName("Muted")
        name_row.addWidget(name_label)
        self.name = QLineEdit()
        self.name.setPlaceholderText("导入后可以随时改名")
        self.name.setMinimumHeight(34)
        name_row.addWidget(self.name, 1)
        col.addLayout(name_row)
        self.meta = QLabel("")
        self.meta.setObjectName("Muted")
        col.addWidget(self.meta)
        self.sample = QLabel("")
        self.sample.setObjectName("TrackMeta")
        self.sample.setWordWrap(True)
        col.addWidget(self.sample, 1)
        preview_row.addLayout(col, 1)
        self.preview.setVisible(False)
        layout.addWidget(self.preview)

        buttons = QDialogButtonBox()
        self.import_btn = buttons.addButton("导入", QDialogButtonBox.AcceptRole)
        self.import_btn.setObjectName("Primary")
        self.import_btn.setEnabled(False)
        buttons.addButton("取消", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ parse
    def parse(self) -> None:
        url = self.link.text().strip()
        if not url:
            self.status.setText("请先粘贴歌单分享链接")
            return
        self.parse_btn.setEnabled(False)
        self.parse_btn.setText("解析中…")
        self.status.setText("正在读取歌单，请稍候…")
        self.preview.setVisible(False)
        self.import_btn.setEnabled(False)
        worker = PlaylistImportWorker(url, self._proxy)
        worker.signals.done.connect(self._on_done)
        worker.signals.failed.connect(self._on_failed)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_done(self, data: dict) -> None:
        self._workers = []
        self.parse_btn.setEnabled(True)
        self.parse_btn.setText("解析歌单")
        self._data = data
        tracks = data.get("tracks") or []
        self.name.setText(data.get("name", ""))
        self.name.selectAll()
        platform = data.get("platform", "")
        total_dur = sum(int(t.get("duration") or 0) for t in tracks)
        self.meta.setText(f"{platform} · {len(tracks)} 首 · 约 {total_dur // 60} 分钟")
        names = [f"{i + 1}. {t.get('title', '')} — {t.get('artist', '')}" for i, t in enumerate(tracks[:6])]
        self.sample.setText("\n".join(names) + (f"\n… 还有 {len(tracks) - 6} 首" if len(tracks) > 6 else ""))
        self._covers.get(data.get("cover", ""), self._set_cover)
        self.preview.setVisible(True)
        self.import_btn.setEnabled(bool(tracks))
        self.status.setText(f"识别成功，共 {len(tracks)} 首歌，可以修改名称后导入。")

    def _set_cover(self, pix) -> None:
        if pix and not pix.isNull():
            self.cover.setPixmap(rounded(pix, 84, 16))

    def _on_failed(self, message: str) -> None:
        self._workers = []
        self.parse_btn.setEnabled(True)
        self.parse_btn.setText("解析歌单")
        self.status.setText(f"解析失败：{message}")

    def _accept(self) -> None:
        if not self._data or not (self._data.get("tracks") or []):
            self.status.setText("还没有解析到歌曲")
            return
        name = self.name.text().strip() or self._data.get("name") or "导入歌单"
        self.result_data = {**self._data, "name": name}
        self.accept()


class PlaylistShareDialog(QDialog):
    """Show the self-contained share link and copy it to the clipboard."""

    def __init__(self, name: str, link: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("分享歌单")
        self.setMinimumWidth(680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        title = QLabel(f"分享「{name or '歌单'}」")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#FFFFFF;")
        layout.addWidget(title)
        hint = QLabel("这是 Aria 专用静态分享链接，歌单数据已压缩在链接中，不依赖服务器。"
                      "在另一台设备的「导入歌单」里粘贴此链接即可导入。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.link = QPlainTextEdit()
        self.link.setObjectName("BatchInput")
        self.link.setReadOnly(True)
        self.link.setPlainText(link)
        self.link.setFixedHeight(132)
        self.link.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.link)

        self.status = QLabel("链接较长，建议直接点「复制链接」后发送到另一台设备。")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.copy_btn = buttons.addButton("复制链接", QDialogButtonBox.ActionRole)
        self.copy_btn.setObjectName("Primary")
        buttons.addButton("关闭", QDialogButtonBox.RejectRole)
        self.copy_btn.clicked.connect(self._copy)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.link.toPlainText())
        self.status.setText("已复制到剪贴板，粘贴到另一台设备的「导入歌单」即可。")


class _HealthSignals(QObject):
    done = Signal(str, dict)


class HealthWorker(QRunnable):
    def __init__(self, site: dict, proxy: str) -> None:
        super().__init__()
        self.site = site
        self.proxy = proxy
        self.signals = _HealthSignals()

    def run(self) -> None:
        result = adapters.health_check(self.site, self.proxy)
        try:
            self.signals.done.emit(self.site["id"], result)
        except RuntimeError:
            pass  # dialog already closed


class _AnalyzeSignals(QObject):
    done = Signal(dict)


class HtmlAnalyzeWorker(QRunnable):
    """Fetch a site and infer a usable HTML rule without blocking the UI."""

    def __init__(self, url: str, proxy: str = "") -> None:
        super().__init__()
        self.url = url
        self.proxy = proxy
        self.signals = _AnalyzeSignals()

    def run(self) -> None:
        try:
            result = site_analyzer.analyze_site(self.url, self.proxy)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "reason": str(exc)[:180], "site": None}
        try:
            self.signals.done.emit(result)
        except RuntimeError:
            pass  # dialog already closed


class HtmlAutoDialog(QDialog):
    """One-field entry: paste a site URL and let the analyzer infer the rule."""

    def __init__(self, proxy: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("自动添加网页音源 · Aria")
        self.setMinimumWidth(560)
        self.proxy = proxy
        self.result_site: dict = {}
        self._partial_site: dict | None = None
        self._pool = QThreadPool.globalInstance()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel("自动添加网页音源")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#FFFFFF;")
        layout.addWidget(title)
        hint = QLabel(
            "输入音乐网站地址，Aria 会自动寻找搜索入口、结果列表、详情链接和音频地址，"
            "识别成功后直接加入本机来源。识别不出的站点可切换为手动配置。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://inmp3.com")
        self.url.returnPressed.connect(self._analyze)
        row.addWidget(self.url, 1)
        self.analyze_btn = QPushButton("自动分析并添加")
        self.analyze_btn.setObjectName("Primary")
        self.analyze_btn.clicked.connect(self._analyze)
        row.addWidget(self.analyze_btn)
        layout.addLayout(row)

        self.status = QLabel("只会尝试公开、无需登录的页面；不会保存登录信息。")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox()
        manual = QPushButton("手动配置")
        manual.setObjectName("Ghost")
        manual.setToolTip("自动识别失败时，打开完整规则表单逐项填写")
        manual.clicked.connect(self._manual)
        buttons.addButton(manual, QDialogButtonBox.ActionRole)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        buttons.addButton(cancel, QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)

    def _analyze(self) -> None:
        value = self.url.text().strip()
        if not value:
            self.status.setText("请先输入网站地址，例如 https://inmp3.com")
            return
        self.analyze_btn.setEnabled(False)
        self.status.setText("正在分析：搜索入口 → 结果列表 → 详情页 → 音频地址…")
        worker = HtmlAnalyzeWorker(value, self.proxy)
        worker.signals.done.connect(self._on_analyzed)
        self._pool.start(worker)

    def _on_analyzed(self, result: dict) -> None:
        self.analyze_btn.setEnabled(True)
        site = result.get("site") if isinstance(result, dict) else None
        if isinstance(site, dict):
            self._partial_site = dict(site)
        if result.get("ok") and isinstance(site, dict):
            site = dict(site)
            site["id"] = site.get("id") or new_id("src")
            self.result_site = site
            self.status.setText(result.get("summary") or "已识别，正在加入来源")
            self.accept()
            return
        reason = str(result.get("reason") or "没有识别到可用规则")[:180]
        self.status.setText(f"自动分析失败：{reason}。可点「手动配置」继续。")

    def _manual(self) -> None:
        dialog = HtmlRuleDialog(None, self)
        if self._partial_site:
            dialog.name.setText(self._partial_site.get("name", ""))
            dialog.search_url.setText(self._partial_site.get("search_url", ""))
            rule = (self._partial_site.get("mapping") or {}).get("rule") or {}
            for key, field in (
                ("list_selector", dialog.list_selector),
                ("id_attr", dialog.id_attr),
                ("title_selector", dialog.title_selector),
                ("artist_selector", dialog.artist_selector),
                ("album_selector", dialog.album_selector),
                ("cover_selector", dialog.cover_selector),
                ("detail_url", dialog.detail_url),
                ("page_selector", dialog.page_selector),
                ("audio_selector", dialog.audio_selector),
            ):
                field.setText(str(rule.get(key, "")))
        if dialog.exec():
            self.result_site = dialog.result_site or {}
            self.accept()


# --------------------------------------------------------------------------- edit
class HtmlRuleDialog(QDialog):
    """Add or edit a configurable HTML source without writing Python code."""

    AUDIO_MODES = [
        ("浏览器页面里的音频标签 / 自动识别", "auto"),
        ("详情页 Base64 变量（如 encodedUrl）", "base64_var"),
        ("明文 MP3 / M4A 链接", "raw"),
        ("JSON-LD 里的 contentUrl", "json_ld"),
        ("自定义正则（第 1 组或整个匹配）", "regex"),
    ]

    def __init__(self, site: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.site = dict(site or {})
        editing = bool(self.site.get("id"))
        self.setWindowTitle(("编辑网页规则" if editing else "添加网页规则") + " · Aria")
        self.setMinimumWidth(560)
        self._pool = QThreadPool.globalInstance()

        mapping = self.site.get("mapping") or {}
        rule = mapping.get("rule") or {}
        if not isinstance(rule, dict):
            rule = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel("网页规则音源")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#FFFFFF;")
        layout.addWidget(title)
        hint = QLabel(
            "给普通 HTML 音乐站写一份解析规则：搜索页找列表 → 详情页取音频。"
            "适合服务端渲染、搜索地址有规律、无需登录和验证码的站点。"
            "规则保存在本机数据库，不会随安装包预置。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft)

        self.name = QLineEdit(self.site.get("name", ""))
        self.name.setPlaceholderText("例如：IN音乐网")
        self.search_url = QLineEdit(self.site.get("search_url", ""))
        self.search_url.setPlaceholderText("https://example.com/so/{query}/{page}/")
        self.list_selector = QLineEdit(str(rule.get("list_selector", "")))
        self.list_selector.setPlaceholderText("div.result-item")
        self.id_attr = QLineEdit(str(rule.get("id_attr", "data-rid")))
        self.id_attr.setPlaceholderText("data-rid")
        self.title_selector = QLineEdit(str(rule.get("title_selector", "")))
        self.title_selector.setPlaceholderText(".result-title")
        self.artist_selector = QLineEdit(str(rule.get("artist_selector", "")))
        self.artist_selector.setPlaceholderText(".result-artist")
        self.album_selector = QLineEdit(str(rule.get("album_selector", "")))
        self.album_selector.setPlaceholderText(".result-album（可留空）")
        self.cover_selector = QLineEdit(str(rule.get("cover_selector", "")))
        self.cover_selector.setPlaceholderText(".cover img（可留空）")
        self.detail_url = QLineEdit(str(rule.get("detail_url", "")))
        self.detail_url.setPlaceholderText("https://example.com/song/{id}/")
        self.page_selector = QLineEdit(str(rule.get("page_selector", "")))
        self.page_selector.setPlaceholderText("a.result-title（详情链接不是模板时填）")
        self.page_attr = QLineEdit(str(rule.get("page_attr", "href")))
        self.page_attr.setPlaceholderText("href")
        self.max_pages = QLineEdit(str(rule.get("max_pages", "1")))
        self.max_pages.setPlaceholderText("1")

        self.audio_mode = QComboBox()
        for label, value in self.AUDIO_MODES:
            self.audio_mode.addItem(label, value)
        index = max(0, self.audio_mode.findData(str(rule.get("audio_mode", "auto"))))
        self.audio_mode.setCurrentIndex(index)
        self.audio_selector = QLineEdit(str(rule.get("audio_selector", "")))
        self.audio_selector.setPlaceholderText("audio#audio-element（可留空）")
        self.audio_attr = QLineEdit(str(rule.get("audio_attr", "src")))
        self.audio_attr.setPlaceholderText("src")
        self.audio_var = QLineEdit(str(rule.get("audio_var") or rule.get("audio_regex") or ""))
        self.audio_var.setPlaceholderText("encodedUrl / 自定义正则")
        self.headers = QPlainTextEdit(json.dumps(self.site.get("headers") or {}, ensure_ascii=False, indent=2))
        self.headers.setFixedHeight(64)
        self.headers.setPlaceholderText('{"Referer": "https://example.com/"}')

        form.addRow("名称", self.name)
        form.addRow("搜索地址模板", self.search_url)
        form.addRow("列表选择器", self.list_selector)
        form.addRow("歌曲 ID 属性", self.id_attr)
        form.addRow("标题选择器", self.title_selector)
        form.addRow("歌手选择器", self.artist_selector)
        form.addRow("专辑选择器", self.album_selector)
        form.addRow("封面选择器", self.cover_selector)
        form.addRow("详情地址模板", self.detail_url)
        form.addRow("详情链接选择器", self.page_selector)
        form.addRow("详情链接属性", self.page_attr)
        form.addRow("最多搜索页数", self.max_pages)
        form.addRow("音频模式", self.audio_mode)
        form.addRow("音频选择器", self.audio_selector)
        form.addRow("音频属性", self.audio_attr)
        form.addRow("变量名 / 正则", self.audio_var)
        form.addRow("请求头 JSON", self.headers)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(8)
        content_layout.addLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(500)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        self.enabled = QCheckBox("启用该来源")
        self.enabled.setChecked(bool(self.site.get("enabled", 1)))
        layout.addWidget(self.enabled)
        self.stream_only = QCheckBox("流式播放（支持 Range 时不写入本地缓存）")
        self.stream_only.setChecked(bool(
            self.site.get("stream_only") or mapping.get("_stream_only")
        ) or not editing)
        self.stream_only.setToolTip("勾选后直接流式播放；不勾选则先下载整首到临时缓存再播放")
        layout.addWidget(self.stream_only)

        self.test_label = QLabel("")
        self.test_label.setObjectName("Muted")
        self.test_label.setWordWrap(True)
        layout.addWidget(self.test_label)

        buttons = QDialogButtonBox()
        example = QPushButton("填入 inmp3 示例")
        example.setObjectName("LinkBtn")
        example.setToolTip("只填入规则示例，不会自动加入来源；点「保存」后才会写入本机")
        example.clicked.connect(self._fill_example)
        buttons.addButton(example, QDialogButtonBox.ActionRole)
        self.test_btn = QPushButton("测试规则")
        self.test_btn.setObjectName("Ghost")
        self.test_btn.clicked.connect(self._test)
        buttons.addButton(self.test_btn, QDialogButtonBox.ActionRole)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        buttons.addButton(cancel, QDialogButtonBox.RejectRole)
        save = QPushButton("保存")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addButton(save, QDialogButtonBox.AcceptRole)
        layout.addWidget(buttons)

        self.result_site: dict = {}

    def _fill_example(self) -> None:
        self.name.setText("IN音乐网")
        self.search_url.setText("https://inmp3.com/so/{query}/{page}/")
        self.list_selector.setText("div.result-item")
        self.id_attr.setText("data-rid")
        self.title_selector.setText(".result-title")
        self.artist_selector.setText(".result-artist")
        self.album_selector.setText(".result-album")
        self.cover_selector.setText("")
        self.detail_url.setText("https://inmp3.com/song/{id}/")
        self.page_selector.setText("")
        self.page_attr.setText("href")
        self.max_pages.setText("2")
        self.audio_mode.setCurrentIndex(max(0, self.audio_mode.findData("base64_var")))
        self.audio_selector.setText("")
        self.audio_attr.setText("src")
        self.audio_var.setText("encodedUrl")
        self.stream_only.setChecked(True)
        self.test_label.setText("示例已填入。个人使用请遵守目标站点条款，音频版权归原平台所有。")

    def _collect(self) -> dict:
        mode = self.audio_mode.currentData() or "auto"
        rule = {
            "list_selector": self.list_selector.text().strip(),
            "id_attr": self.id_attr.text().strip() or "data-rid",
            "title_selector": self.title_selector.text().strip(),
            "artist_selector": self.artist_selector.text().strip(),
            "album_selector": self.album_selector.text().strip(),
            "cover_selector": self.cover_selector.text().strip(),
            "detail_url": self.detail_url.text().strip(),
            "page_selector": self.page_selector.text().strip(),
            "page_attr": self.page_attr.text().strip() or "href",
            "max_pages": self.max_pages.text().strip() or "1",
            "audio_mode": mode,
            "audio_selector": self.audio_selector.text().strip(),
            "audio_attr": self.audio_attr.text().strip() or "src",
            "audio_var": self.audio_var.text().strip(),
            "audio_regex": self.audio_var.text().strip() if mode == "regex" else "",
        }
        old_mapping = dict(self.site.get("mapping") or {})
        old_mapping["rule"] = rule
        old_mapping.setdefault("query_param", "q")
        try:
            headers = json.loads(self.headers.toPlainText() or "{}")
            if not isinstance(headers, dict):
                raise ValueError("请求头需要是 JSON 对象")
        except (json.JSONDecodeError, ValueError):
            headers = {}
        host = urlparse(self.search_url.text().strip()).netloc or "网页规则"
        return {
            "id": self.site.get("id") or new_id("src"),
            "name": self.name.text().strip() or host.replace("www.", "").split(".")[0].title() or "网页规则源",
            "kind": "html_rule",
            "tag": "网页规则源 · 自定义解析",
            "desc": f"网页规则源 · {host}",
            "accent": self.site.get("accent", "#7C8BFF"),
            "base_url": (
                f"{urlparse(self.search_url.text().strip()).scheme}://{urlparse(self.search_url.text().strip()).netloc}"
                if self.search_url.text().strip().startswith(("http://", "https://")) else ""
            ),
            "search_url": self.search_url.text().strip(),
            "mapping": old_mapping,
            "headers": headers,
            "api_key": "",
            "enabled": 1 if self.enabled.isChecked() else 0,
            "stream_only": 1 if self.stream_only.isChecked() else 0,
            "is_builtin": 0,
        }

    def _test(self) -> None:
        data = self._collect()
        if not data["search_url"]:
            self.test_label.setText("先填写搜索地址模板。")
            return
        if not data["mapping"]["rule"].get("list_selector"):
            self.test_label.setText("先填写列表选择器，例如 div.result-item。")
            return
        self.test_label.setText("正在测试…")
        worker = HealthWorker({**data, "health": "unknown"}, "")
        worker.signals.done.connect(self._on_test)
        self._pool.start(worker)

    def _on_test(self, _sid: str, result: dict) -> None:
        if result.get("ok"):
            samples = " / ".join(result.get("samples", [])[:2])
            self.test_label.setText(f"可用 · {result['latency']}ms · {samples}")
        else:
            self.test_label.setText(f"不可用 · {result.get('message', '')}")

    def _save(self) -> None:
        data = self._collect()
        if not data["search_url"]:
            QMessageBox.warning(self, "缺少搜索地址", "请填写搜索地址模板，用 {query} 和 {page} 占位。")
            return
        if not data["mapping"]["rule"].get("list_selector"):
            QMessageBox.warning(self, "缺少列表选择器", "请填写搜索结果的列表选择器，例如 div.result-item。")
            return
        self.result_site = data
        self.accept()


class SiteEditDialog(QDialog):
    def __init__(self, site: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"编辑来源 · {site.get('name', '')}")
        self.setMinimumWidth(560)
        self.site = dict(site)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title = QLabel(site.get("name", "来源"))
        title.setStyleSheet("font-size:18px;font-weight:700;color:#FFFFFF;")
        layout.addWidget(title)
        if site.get("kind") == "web":
            hint_text = ("这是网页源：可只填网站地址。Aria 搜索时会先尝试找出网站自己的 JSON 接口，"
                         "找不到再解析网页里的音频；需要登录或接口加密的网站可能无法直接播放。")
        else:
            hint_text = ("支持 JSON 接口；若搜索与取流分离，在「取流接口模板」里填第二次请求的地址，"
                         "用 {字段} 占位，例如 https://api.example.com/geturl?id={id}。")
        hint = QLabel(hint_text)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft)

        self.name = QLineEdit(site.get("name", ""))
        self.search_url = QLineEdit(site.get("search_url", ""))
        self.search_url.setPlaceholderText(
            "https://www.example.com/search?q={query}"
            if site.get("kind") == "web" else "https://api.example.com/search?q={query}")
        mapping = site.get("mapping") or {}
        self.results_path = QLineEdit(mapping.get("results_path", ""))
        self.results_path.setPlaceholderText("data.list  （留空自动识别）")
        self.f_title = QLineEdit(mapping.get("title", ""))
        self.f_artist = QLineEdit(mapping.get("artist", ""))
        self.f_audio = QLineEdit(mapping.get("audio", ""))
        self.f_audio_from = QLineEdit(mapping.get("audio_from", ""))
        self.f_audio_from.setPlaceholderText("https://api.example.com/geturl?id={id}")
        self.f_audio_path = QLineEdit(mapping.get("audio_path", ""))
        self.f_audio_path.setPlaceholderText("url")
        self.f_cover = QLineEdit(mapping.get("cover", ""))
        self.f_duration = QLineEdit(mapping.get("duration", ""))
        self.f_page = QLineEdit(mapping.get("page", ""))
        self.f_lyrics = QLineEdit(mapping.get("lyrics", ""))
        self.f_lyrics.setPlaceholderText("歌词字段（可留空，自动匹配）")
        self.api_key = QLineEdit(site.get("api_key", ""))
        self.headers = QPlainTextEdit(json.dumps(site.get("headers") or {}, ensure_ascii=False, indent=2))
        self.headers.setFixedHeight(70)

        for widget in (self.results_path, self.f_title, self.f_artist, self.f_audio,
                       self.f_cover, self.f_duration, self.f_page):
            widget.setPlaceholderText(widget.placeholderText() or "字段名，支持 a.b.c")

        form.addRow("名称", self.name)
        form.addRow("搜索接口", self.search_url)
        form.addRow("结果路径", self.results_path)
        form.addRow("标题字段", self.f_title)
        form.addRow("歌手字段", self.f_artist)
        form.addRow("音频字段", self.f_audio)
        form.addRow("取流接口模板", self.f_audio_from)
        form.addRow("取流音频字段", self.f_audio_path)
        form.addRow("封面字段", self.f_cover)
        form.addRow("时长字段", self.f_duration)
        form.addRow("详情页字段", self.f_page)
        form.addRow("歌词字段", self.f_lyrics)
        form.addRow("API Key", self.api_key)
        form.addRow("请求头 JSON", self.headers)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(10)
        content_layout.addLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(520)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        self.enabled = QCheckBox("启用该来源")
        self.enabled.setChecked(bool(site.get("enabled", 1)))
        layout.addWidget(self.enabled)

        self.stream_only = QCheckBox("流式播放（支持 Range 时不写入本地缓存）")
        self.stream_only.setChecked(bool(
            site.get("stream_only") or (site.get("mapping") or {}).get("_stream_only")))
        self.stream_only.setToolTip("勾选后直接流式播放；不勾选则先下载整首到临时缓存再播放")
        layout.addWidget(self.stream_only)

        self.test_label = QLabel("")
        self.test_label.setObjectName("Muted")
        self.test_label.setWordWrap(True)
        layout.addWidget(self.test_label)

        buttons = QDialogButtonBox()
        self.test_btn = QPushButton("测试接口")
        self.test_btn.setObjectName("Ghost")
        self.test_btn.clicked.connect(self._test)
        buttons.addButton(self.test_btn, QDialogButtonBox.ActionRole)
        save = QPushButton("保存")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        buttons.addButton(cancel, QDialogButtonBox.RejectRole)
        buttons.addButton(save, QDialogButtonBox.AcceptRole)
        layout.addWidget(buttons)

        self.result_data: dict = {}
        self._pool = QThreadPool.globalInstance()

    def _collect(self) -> dict:
        old = dict(self.site.get("mapping") or {})
        mapping = {**old,
            "results_path": self.results_path.text().strip(),
            "title": self.f_title.text().strip() or "title",
            "artist": self.f_artist.text().strip() or "artist",
            "audio": self.f_audio.text().strip(),
            "query_param": old.get("query_param", "q"),
        }
        for key, widget in (("cover", self.f_cover), ("duration", self.f_duration),
                            ("page", self.f_page), ("lyrics", self.f_lyrics)):
            if widget.text().strip():
                mapping[key] = widget.text().strip()
            else:
                mapping.pop(key, None)
        audio_from = self.f_audio_from.text().strip()
        if audio_from:
            mapping["audio_from"] = audio_from
            mapping["audio_path"] = self.f_audio_path.text().strip() or "url"
        else:
            mapping.pop("audio_from", None)
            mapping.pop("audio_path", None)
        try:
            headers = json.loads(self.headers.toPlainText() or "{}")
            if not isinstance(headers, dict):
                raise ValueError("请求头需要是 JSON 对象")
        except (json.JSONDecodeError, ValueError):
            headers = {}
        kind = "generic" if self.site.get("kind") == "generic" else self.site.get("kind", "generic")
        stream_only = 1 if self.stream_only.isChecked() else 0
        tag = self.site.get("tag", "自定义接口")
        desc = self.site.get("desc", "")
        if kind == "official":
            tag = "自定义官方 API · 流式" if stream_only else "自定义官方 API · 本地缓存"
            desc = ("手动添加的官方 API；播放时保持流式，不写入本地缓存。"
                    if stream_only else
                    "手动添加的官方 API；播放前先下载整首音频到本地缓存。")
        return {
            "id": self.site.get("id", ""),
            "name": self.name.text().strip() or self.site.get("name", "自定义来源"),
            "kind": kind,
            "tag": tag,
            "desc": desc,
            "accent": self.site.get("accent", "#6E8BFF"),
            "base_url": self.site.get("base_url", ""),
            "search_url": self.search_url.text().strip(),
            "mapping": mapping,
            "headers": headers,
            "api_key": self.api_key.text().strip(),
            "enabled": 1 if self.enabled.isChecked() else 0,
            "stream_only": stream_only,
            "is_builtin": self.site.get("is_builtin", 0),
        }

    def _test(self) -> None:
        data = self._collect()
        self.test_label.setText("正在测试…")
        worker = HealthWorker({**data, "health": "unknown"}, "")
        worker.signals.done.connect(self._on_test)
        self._pool.start(worker)

    def _on_test(self, _sid: str, result: dict) -> None:
        if result.get("ok"):
            samples = " / ".join(result.get("samples", [])[:2])
            self.test_label.setText(f"可用 · {result['latency']}ms · {samples}")
        else:
            self.test_label.setText(f"不可用 · {result.get('message', '')}")

    def _save(self) -> None:
        data = self._collect()
        if not data["search_url"]:
            QMessageBox.warning(self, "缺少接口", "请填写搜索接口地址。")
            return
        self.result_data = data
        self.accept()


class _ToolDiscoverSignals(QObject):
    done = Signal(dict)
    failed = Signal(str)


class ToolDiscoverWorker(QRunnable):
    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.signals = _ToolDiscoverSignals()

    def run(self) -> None:
        import aggregator_tools
        try:
            self.signals.done.emit(aggregator_tools.discover(self.url))
        except Exception as exc:  # noqa: BLE001
            try:
                self.signals.failed.emit(str(exc)[:220])
            except RuntimeError:
                pass


class _ToolInstallSignals(QObject):
    done = Signal(dict)
    failed = Signal(str)


class ToolInstallWorker(QRunnable):
    def __init__(self, url: str, option: dict) -> None:
        super().__init__()
        self.url = url
        self.option = dict(option)
        self.signals = _ToolInstallSignals()

    def run(self) -> None:
        import aggregator_tools
        try:
            self.signals.done.emit(aggregator_tools.install(self.url, self.option))
        except Exception as exc:  # noqa: BLE001
            try:
                self.signals.failed.emit(str(exc)[:260])
            except RuntimeError:
                pass


class AggregatorToolDialog(QDialog):
    """Paste a GitHub repository, choose a start option, install it as a source."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("添加聚合工具")
        self.setMinimumWidth(680)
        self.result_tool: dict | None = None
        self._options: list[dict] = []
        self._workers: list = []
        self._repo: dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(10)
        title = QLabel("从 GitHub 植入聚合工具")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#FFFFFF;")
        root.addWidget(title)
        hint = QLabel("粘贴仓库链接，读取它提供的启动方式；植入后会作为独立音乐源进入发现页、聚合搜索与跨源匹配。")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://github.com/owner/repo")
        self.url_input.returnPressed.connect(self._parse)
        row.addWidget(self.url_input, 1)
        self.parse_btn = QPushButton("读取仓库")
        self.parse_btn.setObjectName("Ghost")
        self.parse_btn.setIcon(make_icon("search", "#E9EAF2", 15))
        self.parse_btn.clicked.connect(self._parse)
        row.addWidget(self.parse_btn)
        root.addLayout(row)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.combo = QComboBox()
        self.combo.setVisible(False)
        self.combo.currentIndexChanged.connect(self._show_option)
        root.addWidget(self.combo)

        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        root.addWidget(self.detail)

        danger = QLabel("植入会下载并运行该仓库的代码，只选择你信任的来源。工具需要一个可用的 /health 与 Aria 聚合接口。")
        danger.setWordWrap(True)
        danger.setStyleSheet("color:#F0A35E;font-size:12px;")
        root.addWidget(danger)

        foot = QHBoxLayout()
        self.install_btn = QPushButton("植入并启用")
        self.install_btn.setObjectName("Primary")
        self.install_btn.setIcon(make_icon("plus", "#FFFFFF", 15))
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self._install)
        foot.addWidget(self.install_btn)
        foot.addStretch(1)
        close_btn = QDialogButtonBox()
        close_btn.addButton("关闭", QDialogButtonBox.RejectRole)
        close_btn.rejected.connect(self.reject)
        foot.addWidget(close_btn)
        root.addLayout(foot)

    def _parse(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self.status.setText("先粘贴 GitHub 仓库链接")
            return
        self._options = []
        self._repo = {}
        self.combo.clear()
        self.combo.setVisible(False)
        self.detail.setText("")
        self.install_btn.setEnabled(False)
        self.parse_btn.setEnabled(False)
        self.status.setText("正在读取仓库信息…")
        worker = ToolDiscoverWorker(url)
        worker.signals.done.connect(self._on_discover)
        worker.signals.failed.connect(self._on_failed)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_discover(self, data: dict) -> None:
        self.parse_btn.setEnabled(True)
        self._repo = data
        self._options = list(data.get("options") or [])
        if not self._options:
            self.status.setText("这个仓库没有提供 aria-tool.json，也没有 package.json 的 start 脚本，无法作为聚合工具植入。")
            return
        self.combo.clear()
        for option in self._options:
            name = option.get("name") or data.get("repo") or "工具"
            path = option.get("path") or ""
            self.combo.addItem(f"{name}   ·   {path}", option)
        self.combo.setVisible(True)
        self.combo.setCurrentIndex(0)
        self.install_btn.setEnabled(True)
        desc = data.get("description") or ""
        self.status.setText(f"已读取 {data.get('full_name') or ''}"
                            + (f"：{desc}" if desc else ""))
        self._show_option()

    def _show_option(self, _index: int = 0) -> None:
        option = self.combo.currentData()
        if not option:
            self.detail.setText("")
            return
        manifest = option.get("manifest") or {}
        start = manifest.get("start") or manifest.get("command") or option.get("start") or ""
        if isinstance(start, (list, tuple)):
            start = " ".join(str(v) for v in start)
        self.detail.setText(
            f"启动方式：{start or '按入口自动识别'}    "
            f"运行环境：{manifest.get('runtime') or ('Node' if option.get('kind') == 'node' else '见清单')}"
        )

    def _on_failed(self, message: str) -> None:
        self.parse_btn.setEnabled(True)
        self.install_btn.setEnabled(bool(self._options))
        self.status.setText(message)

    def _install(self) -> None:
        option = self.combo.currentData()
        url = self.url_input.text().strip()
        if not option or not url:
            return
        self.install_btn.setEnabled(False)
        self.parse_btn.setEnabled(False)
        self.status.setText("正在下载、启动并检查聚合接口…")
        worker = ToolInstallWorker(url, option)
        worker.signals.done.connect(self._on_installed)
        worker.signals.failed.connect(self._on_install_failed)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_installed(self, entry: dict) -> None:
        self.result_tool = dict(entry)
        self.status.setText(f"已植入并启用：{entry.get('name')}（{entry.get('base_url')}）")
        self.install_btn.setText("已启用")
        self.install_btn.setEnabled(False)
        self.parse_btn.setEnabled(True)
        self.accept()

    def _on_install_failed(self, message: str) -> None:
        self.install_btn.setEnabled(True)
        self.parse_btn.setEnabled(True)
        self.status.setText(message)

