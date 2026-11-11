"""Site library: manage, test, batch add and auto-discover sources."""

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import aggregator_tools
import theme
from components import SectionHeading, ToggleSwitch, gradient_badge_art
from dialogs import (HealthWorker, HtmlAutoDialog, HtmlRuleDialog,
                     SiteEditDialog)
from project_wizard import ProjectWizardDialog
from icons import make_icon


class SiteCard(QFrame):
    toggleRequested = Signal(str, bool)
    testRequested = Signal(str)
    editRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, site: dict, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFixedHeight(92)
        self.site = site
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(14)

        badge = QLabel()
        badge.setFixedSize(52, 52)
        badge.setPixmap(gradient_badge_art(site.get("name", "A"), site.get("accent", "#6E8BFF"), 52))
        row.addWidget(badge)

        col = QVBoxLayout()
        col.setSpacing(5)
        head = QHBoxLayout()
        head.setSpacing(8)
        name = QLabel(site.get("name", ""))
        name.setStyleSheet("color:#FFFFFF;font-size:15px;font-weight:600;")
        head.addWidget(name)
        official_kinds = {"itunes", "audius", "deezer", "jamendo", "openverse", "archive", "radio", "official"}
        badge_text = site.get("badge") or (
            "内置" if site.get("is_builtin") else "官方 API" if site.get("kind") in official_kinds
            else "网页规则" if site.get("kind") == "html_rule" else "自定义"
        )
        kind = QLabel(badge_text)
        kind.setObjectName("SourceBadge")
        kind.setFixedHeight(20)
        head.addWidget(kind)
        head.addStretch(1)
        col.addLayout(head)
        desc = QLabel(site.get("desc") or site.get("tag") or "自定义 JSON 搜索接口")
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        desc.setMinimumWidth(0)
        desc.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(desc)
        row.addLayout(col, 1)

        self.health = QLabel()
        self.health.setFixedWidth(132)
        self.health.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.health)
        self._paint_health()

        self.switch = ToggleSwitch(bool(site.get("enabled", 1)))
        self.switch.setToolTip("启用 / 停用该来源")
        self.switch.toggled.connect(lambda checked: self.toggleRequested.emit(self.site["id"], checked))
        row.addWidget(self.switch)

        self.test_btn = self._icon_button("refresh", "测试接口")
        self.test_btn.clicked.connect(lambda: self.testRequested.emit(self.site["id"]))
        row.addWidget(self.test_btn)

        self.edit_btn = self._icon_button("edit", "编辑映射")
        self.edit_btn.clicked.connect(lambda: self.editRequested.emit(self.site["id"]))
        row.addWidget(self.edit_btn)

        self.del_btn = self._icon_button("trash", "删除来源")
        self.del_btn.clicked.connect(lambda: self.deleteRequested.emit(self.site["id"]))
        row.addWidget(self.del_btn)

    def _icon_button(self, kind: str, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("IconBtn")
        btn.setFixedSize(32, 32)
        btn.setIcon(make_icon(kind, "#8A8FA3", 16))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def _paint_health(self) -> None:
        health = self.site.get("health", "unknown")
        latency = self.site.get("latency", 0)
        if health == "ok":
            self.health.setText(f"● 可用 · {latency}ms")
            self.health.setStyleSheet(f"color:{theme.OK};font-size:12px;")
        elif health == "error":
            self.health.setText("● 不可用")
            self.health.setStyleSheet(f"color:{theme.DANGER};font-size:12px;")
            self.health.setToolTip("接口探测失败，检查地址、请求头或 Key")
        else:
            self.health.setText("● 未检测")
            self.health.setStyleSheet(f"color:{theme.TEXT_DIM};font-size:12px;")

    def refresh(self) -> None:
        self._paint_health()


class ToolCard(QFrame):
    """A GitHub-installed aggregator; it lives in providers.json, not sites."""

    toggleRequested = Signal(str, bool)
    testRequested = Signal(str)
    folderRequested = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, tool: dict, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFixedHeight(92)
        self.tool = dict(tool)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(14)

        badge = QLabel()
        badge.setFixedSize(52, 52)
        badge.setPixmap(gradient_badge_art(self.tool.get("name", "T"),
                                           self.tool.get("accent", "#6E8BFF"), 52))
        row.addWidget(badge)

        col = QVBoxLayout()
        col.setSpacing(5)
        head = QHBoxLayout()
        head.setSpacing(8)
        name = QLabel(self.tool.get("name", ""))
        name.setStyleSheet("color:#FFFFFF;font-size:15px;font-weight:600;")
        head.addWidget(name)
        kind = QLabel("开源项目")
        kind.setObjectName("SourceBadge")
        kind.setFixedHeight(20)
        head.addWidget(kind)
        head.addStretch(1)
        col.addLayout(head)
        repo = self.tool.get("repo") or self.tool.get("base_url") or "GitHub"
        desc = QLabel(f"{repo} · {self.tool.get('base_url', '')}")
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        desc.setMinimumWidth(0)
        desc.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(desc)
        row.addLayout(col, 1)

        self.health = QLabel()
        self.health.setFixedWidth(132)
        self.health.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.health)
        self.site = {"health": self.tool.get("health", "unknown"),
                     "latency": self.tool.get("latency", 0)}
        self._paint_health()

        self.switch = ToggleSwitch(not bool(self.tool.get("disabled")))
        self.switch.setToolTip("启用 / 停用该开源项目")
        self.switch.toggled.connect(lambda checked: self.toggleRequested.emit(
            str(self.tool.get("id") or ""), checked))
        row.addWidget(self.switch)

        self.test_btn = self._icon_button("refresh", "测试聚合接口")
        self.test_btn.clicked.connect(lambda: self.testRequested.emit(
            str(self.tool.get("id") or "")))
        row.addWidget(self.test_btn)

        self.folder_btn = self._icon_button("list", "打开工具目录")
        self.folder_btn.clicked.connect(lambda: self.folderRequested.emit(
            str(self.tool.get("id") or "")))
        row.addWidget(self.folder_btn)

        self.del_btn = self._icon_button("trash", "移除开源项目")
        self.del_btn.clicked.connect(lambda: self.deleteRequested.emit(
            str(self.tool.get("id") or "")))
        row.addWidget(self.del_btn)

    def _icon_button(self, kind: str, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("IconBtn")
        btn.setFixedSize(32, 32)
        btn.setIcon(make_icon(kind, "#8A8FA3", 16))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def _paint_health(self) -> None:
        health = self.site.get("health", "unknown")
        latency = self.site.get("latency", 0)
        if health == "ok":
            self.health.setText(f"● 可用 · {latency}ms")
            self.health.setStyleSheet(f"color:{theme.OK};font-size:12px;")
        elif health == "error":
            self.health.setText("● 不可用")
            self.health.setStyleSheet(f"color:{theme.DANGER};font-size:12px;")
            self.health.setToolTip("工具没有响应健康检查，检查日志或重新植入")
        elif self.tool.get("disabled"):
            self.health.setText("● 已停用")
            self.health.setStyleSheet(f"color:{theme.TEXT_DIM};font-size:12px;")
        else:
            self.health.setText("● 未检测")
            self.health.setStyleSheet(f"color:{theme.TEXT_DIM};font-size:12px;")

    def set_health(self, ok: bool, latency: int) -> None:
        self.site["health"] = "ok" if ok else "error"
        self.site["latency"] = latency
        self._paint_health()


class _ToolTestSignals(QObject):
    done = Signal(str, bool, int, str)


class _ToolTestWorker(QRunnable):
    def __init__(self, tool_id: str) -> None:
        super().__init__()
        self.tool_id = tool_id
        self.signals = _ToolTestSignals()

    def run(self) -> None:
        try:
            ok, latency, message = aggregator_tools.test_tool(self.tool_id)
        except Exception as exc:  # noqa: BLE001
            ok, latency, message = False, 0, str(exc)[:120]
        try:
            self.signals.done.emit(self.tool_id, ok, latency, message)
        except RuntimeError:
            pass



class SitesPage(QWidget):
    changed = Signal()

    def __init__(self, db, parent=None) -> None:
        super().__init__(parent)
        self.db = db
        self.cards: dict[str, SiteCard] = {}
        self.tool_cards: dict[str, ToolCard] = {}
        self.pool = QThreadPool.globalInstance()
        self._messages = []

        root = QVBoxLayout(self)
        root.setContentsMargins(34, 24, 34, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(SectionHeading("音乐站点", "全新安装默认 Listen1"))
        head.addStretch(1)
        self.restore_btn = QPushButton("恢复内置来源")
        self.restore_btn.setObjectName("Ghost")
        self.restore_btn.setIcon(make_icon("refresh", "#E9EAF2", 15))
        self.restore_btn.setToolTip("把删掉的内置来源全部加回来")
        self.restore_btn.clicked.connect(self.restore_builtins)
        head.addWidget(self.restore_btn)
        self.tool_btn = QPushButton("添加开源项目")
        self.tool_btn.setObjectName("Ghost")
        self.tool_btn.setIcon(make_icon("plus", "#E9EAF2", 15))
        self.tool_btn.setToolTip("输入项目网址，识别为侧车或远程服务后接入")
        self.tool_btn.clicked.connect(self.add_project)
        head.addWidget(self.tool_btn)
        self.rule_btn = QPushButton("添加网页音源")
        self.rule_btn.setObjectName("Ghost")
        self.rule_btn.setIcon(make_icon("plus", "#E9EAF2", 15))
        self.rule_btn.setToolTip("输入网站地址自动分析并添加；识别失败可改用手动规则")
        self.rule_btn.clicked.connect(self.add_web_rule)
        head.addWidget(self.rule_btn)
        root.addLayout(head)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        root.addWidget(self.status)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.list_layout = QVBoxLayout(self.body)
        self.list_layout.setContentsMargins(0, 4, 8, 4)
        self.list_layout.setSpacing(10)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        footer = QLabel("来源仅返回搜索与试听地址，音频版权归各平台所有；请遵守对应站点的使用条款。")
        footer.setObjectName("Muted")
        footer.setWordWrap(True)
        root.addWidget(footer)

        self.refresh()

    # ------------------------------------------------------------------ list
    def refresh(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards = {}
        self.tool_cards = {}
        sites = self.db.sites()
        for site in sites:
            card = SiteCard(site)
            card.toggleRequested.connect(self._toggle)
            card.testRequested.connect(self.test_one)
            card.editRequested.connect(self.edit_site)
            card.deleteRequested.connect(self.delete_site)
            self.cards[site["id"]] = card
            self.list_layout.addWidget(card)

        tools = []
        try:
            tools = aggregator_tools.list_tools()
        except Exception:  # noqa: BLE001
            tools = []
        if tools:
            label = QLabel("开源项目（侧车）")
            label.setObjectName("Muted")
            label.setStyleSheet("color:#8A8FA3;font-size:12px;font-weight:600;padding-top:8px;")
            self.list_layout.addWidget(label)
            for tool in tools:
                card = ToolCard(tool)
                card.toggleRequested.connect(self._toggle_tool)
                card.testRequested.connect(self.test_tool)
                card.folderRequested.connect(self.open_tool_folder)
                card.deleteRequested.connect(self.delete_tool)
                self.tool_cards[str(tool.get("id") or "")] = card
                self.list_layout.addWidget(card)

        self.list_layout.addStretch(1)
        enabled = sum(1 for s in sites if s.get("enabled"))
        enabled_tools = sum(1 for t in tools if not t.get("disabled"))
        text = f"{len(sites)} 个来源 · {enabled} 个已启用"
        if tools:
            text += f" · {len(tools)} 个开源项目（{enabled_tools} 个已启用）"
        self.status.setText(text)

    def _toggle(self, site_id: str, enabled: bool) -> None:
        self.db.set_enabled(site_id, enabled)
        self.refresh()
        self.changed.emit()

    # ------------------------------------------------------------------ health
    def test_one(self, site_id: str) -> None:
        site = self.db.get_site(site_id)
        if not site:
            return
        card = self.cards.get(site_id)
        if card:
            card.health.setText("检测中…")
            card.health.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:12px;")
        worker = HealthWorker(site, self.db.get_setting("proxy", "") or "")
        worker.signals.done.connect(self._on_health)
        self.pool.start(worker)

    def _on_health(self, site_id: str, result: dict) -> None:
        self.db.set_health(site_id, "ok" if result.get("ok") else "error",
                           result.get("latency", 0))
        site = self.db.get_site(site_id)
        card = self.cards.get(site_id)
        if site and card:
            card.site = site
            card.refresh()
        self.changed.emit()

    # ------------------------------------------------------------------ aggregator tools
    def add_project(self) -> None:
        dialog = ProjectWizardDialog(self, self.db.get_setting("proxy", "") or "")
        if not dialog.exec():
            return
        tool = dialog.result_tool or {}
        site = dialog.result_site or {}
        if site.get("id"):
            self.db.upsert_site(site)
            self.refresh()
            self.status.setText(f"已添加开源项目：{site.get('name', '')}")
            self.changed.emit()
            return
        if tool:
            self.refresh()
            self.status.setText(f"已添加开源项目：{tool.get('name', '')}")
            self.changed.emit()

    def add_web_rule(self) -> None:
        dialog = HtmlAutoDialog(self.db.get_setting("proxy", "") or "", self)
        if dialog.exec():
            site = dialog.result_site or {}
            if not site.get("id"):
                return
            self.db.upsert_site(site)
            self.refresh()
            mode = "流式播放，不缓存" if site.get("stream_only") else "先缓存再播"
            self.status.setText(f"已添加网页音源：{site.get('name', '')}（{mode}）")
            self.changed.emit()

    def _toggle_tool(self, tool_id: str, enabled: bool) -> None:
        try:
            aggregator_tools.set_enabled(tool_id, enabled)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "开源项目", str(exc)[:200])
        self.refresh()
        self.changed.emit()

    def test_tool(self, tool_id: str) -> None:
        card = self.tool_cards.get(tool_id)
        if card:
            card.health.setText("检测中…")
            card.health.setStyleSheet(f"color:{theme.TEXT_MUTED};font-size:12px;")
        worker = _ToolTestWorker(tool_id)
        worker.signals.done.connect(self._on_tool_health)
        self.pool.start(worker)

    def _on_tool_health(self, tool_id: str, ok: bool, latency: int, message: str) -> None:
        card = self.tool_cards.get(tool_id)
        if card:
            card.set_health(ok, latency)
            if message:
                card.health.setToolTip(message)
        self.status.setText(f"开源项目可用 · {latency}ms" if ok else f"开源项目不可用：{message}")

    def open_tool_folder(self, tool_id: str) -> None:
        try:
            path = aggregator_tools.open_folder(tool_id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "开源项目", str(exc)[:200])
            return
        import subprocess
        import os
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            subprocess.Popen(["explorer", path])

    def delete_tool(self, tool_id: str) -> None:
        card = self.tool_cards.get(tool_id)
        name = card.tool.get("name", "") if card else "该开源项目"
        answer = QMessageBox.question(
            self, "移除开源项目",
            f"确定卸载「{name}」吗？工具目录和配置都会被移除。")
        if answer != QMessageBox.Yes:
            return
        try:
            aggregator_tools.remove_tool(tool_id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "开源项目", str(exc)[:200])
        self.refresh()
        self.changed.emit()

    # ------------------------------------------------------------------ dialogs
    def edit_site(self, site_id: str) -> None:
        site = self.db.get_site(site_id)
        if not site:
            return
        dialog = (HtmlRuleDialog(site, self) if site.get("kind") == "html_rule"
                  else SiteEditDialog(site, self))
        if dialog.exec():
            data = getattr(dialog, "result_site", None) or getattr(dialog, "result_data", {})
            if not data:
                return
            self.db.upsert_site(data)
            self.refresh()
            self.changed.emit()

    def delete_site(self, site_id: str) -> None:
        site = self.db.get_site(site_id)
        if not site:
            return
        extra = "（内置来源，删掉后可用「恢复内置来源」找回）" if site.get("is_builtin") else ""
        answer = QMessageBox.question(self, "删除来源", f"确定移除「{site['name']}」吗？{extra}")
        if answer == QMessageBox.Yes:
            self.db.delete_site(site_id)
            self.refresh()
            self.changed.emit()

    def restore_builtins(self) -> None:
        removed = self.db.removed_builtins()
        if not removed:
            self.status.setText("内置来源都在，没有被删掉的")
            return
        added = self.db.restore_builtins()
        self.refresh()
        self.changed.emit()
        self.status.setText(f"已恢复 {len(added)} 个内置来源" if added else "没有需要恢复的来源")
