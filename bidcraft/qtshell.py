"""Qt 原生客户端外壳：真窗口 + 原生菜单/工具栏/托盘，内容仍是 SPA。

C/S 通信沿用 R29 的原生桥（``BidCraftClient.bridge_request``），通过 QWebChannel
暴露给页面，并伪装成 ``window.pywebview.api``，前端无需改动。
"""

from __future__ import annotations

import getpass
import json
import secrets
import threading
from pathlib import Path
from typing import Any

from . import __version__
from .client import BidCraftClient, ClientError
from .desktop import DEFAULT_SPA_DIR, start_gateway

SETTINGS_ORG = "BidCraft"
SETTINGS_APP = "BidCraft"
KEY_SERVER = "client/server"
KEY_GEOMETRY = "window/geometry"
KEY_STATE = "window/state"
KEY_DOWNLOAD_DIR = "client/download_dir"
KEY_TRAY_HINT = "window/tray_hint_shown"

NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("项目", "/"),
    ("我的文档", "/my-docs"),
    ("账单", "/billing"),
)
ADMIN_NAV = ("管理", "/admin")

RELEASE_API = "https://api.github.com/repos/yaoyongli1974-dotcom/BidCraft/releases/latest"
RELEASE_PAGE = "https://github.com/yaoyongli1974-dotcom/BidCraft/releases/latest"

HIDE_MASTHEAD_CSS = """
header.masthead, .masthead { display: none !important; }
body { padding-top: 0 !important; }
"""


def compare_version(current: str, latest: str) -> int:
    """比较版本号：current < latest 返回 -1，相等 0，更新 1。非法串按 0 处理。"""

    def parse(value: str) -> tuple[int, ...]:
        text = (value or "").strip().lstrip("vV")
        parts: list[int] = []
        for chunk in text.split("."):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            if not digits:
                break
            parts.append(int(digits))
        return tuple(parts)

    left, right = parse(current), parse(latest)
    if not left or not right:
        return 0
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return (left > right) - (left < right)


def icon_path() -> Path | None:
    """定位应用图标（打包资源优先，其次仓库 packaging/）。"""
    import sys

    candidates: list[Path] = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(Path(base) / "packaging" / "bidcraft.svg")
    candidates.append(Path(__file__).resolve().parent.parent / "packaging" / "bidcraft.svg")
    for path in candidates:
        if path.is_file():
            return path
    return None


def bridge_script(channel_js: str) -> str:
    """把 QWebChannel 桥包装成 window.pywebview.api（Promise 版）。"""
    return (
        channel_js
        + """
(function () {
  function wrap(obj, name) {
    return function () {
      var args = Array.prototype.slice.call(arguments);
      return new Promise(function (resolve, reject) {
        try {
          obj[name].apply(obj, args.concat([function (raw) {
            try { resolve(JSON.parse(raw)); } catch (err) { reject(err); }
          }]));
        } catch (err) { reject(err); }
      });
    };
  }
  function connect(tries) {
    if (typeof QWebChannel === 'undefined' || !window.qt || !qt.webChannelTransport) {
      if (tries > 200) { return; }
      setTimeout(function () { connect(tries + 1); }, 25);
      return;
    }
    new QWebChannel(qt.webChannelTransport, function (channel) {
      var bridge = channel.objects.bridge;
      window.pywebview = {
        platform: 'qt',
        api: {
          info: wrap(bridge, 'info'),
          api_request: wrap(bridge, 'api_request'),
          pick_and_upload: wrap(bridge, 'pick_and_upload'),
          choose_server: wrap(bridge, 'choose_server')
        }
      };
      window.dispatchEvent(new Event('pywebviewready'));
    });
  }
  connect(0);
})();
"""
    )


def css_script() -> str:
    """隐藏网页顶栏，导航交给原生工具栏。"""
    css = json.dumps(HIDE_MASTHEAD_CSS)
    return (
        "(function(){function apply(){if(!document.head){setTimeout(apply,20);return;}"
        "var s=document.createElement('style');s.textContent=" + css + ";"
        "document.head.appendChild(s);}apply();})();"
    )


def latest_version(timeout: float = 8.0) -> str:
    """查询 GitHub 最新发行版本号；失败返回空串。"""
    import httpx

    try:
        response = httpx.get(RELEASE_API, timeout=timeout, follow_redirects=True)
        if response.status_code != 200:
            return ""
        return str(response.json().get("tag_name") or "")
    except Exception:  # noqa: BLE001 - 更新检查失败不应影响使用
        return ""


try:  # pragma: no cover - 依赖系统 Qt
    from PySide6.QtCore import QFile, QObject, QSettings, QTimer, QUrl, Slot
    from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineScript
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QStackedWidget,
        QSystemTrayIcon,
        QToolBar,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except Exception:  # noqa: BLE001 - 无 Qt/无显示时回退
    QT_AVAILABLE = False


if QT_AVAILABLE:  # pragma: no cover - GUI 代码由手工/集成验证

    class _Bridge(QObject):
        """暴露给页面的原生桥（返回 JSON 字符串）。"""

        def __init__(self, server: str, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.client = BidCraftClient(server)
            self.on_server_changed = None

        @Slot(result=str)
        def info(self) -> str:
            return json.dumps(
                {"server": self.client.server, "version": __version__, "bridge": "api"}
            )

        @Slot(str, str, str, str, result=str)
        def api_request(
            self, method: str, path: str, body_b64: str = "", content_type: str = ""
        ) -> str:
            return json.dumps(
                self.client.bridge_request(
                    method, path, body_b64=body_b64, content_type=content_type
                )
            )

        @Slot(result=str)
        def choose_server(self) -> str:
            """在窗口内切换服务器（登录页亦可调用）。"""
            dialog = _ServerDialog(self.client.server)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return json.dumps({"changed": False, "server": self.client.server})
            server = dialog.value()
            changed = server.rstrip("/") != self.client.server.rstrip("/")
            if changed:
                self.client = BidCraftClient(server)
                settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
                settings.setValue(KEY_SERVER, self.client.server)
                if self.on_server_changed is not None:
                    self.on_server_changed(self.client.server)
            return json.dumps({"changed": changed, "server": self.client.server})

        @Slot(str, str, result=str)
        def pick_and_upload(self, kind: str = "doc", project: str = "") -> str:
            path, _ = QFileDialog.getOpenFileName(None, "选择要上传的文件")
            if not path:
                return json.dumps({"ok": False, "error": "未选择文件"})
            try:
                if kind == "project_source":
                    data = self.client.upload_source(project, path)
                else:
                    data = self.client.upload_doc(path)
            except ClientError as exc:
                return json.dumps({"ok": False, "error": str(exc)})
            return json.dumps({"ok": True, "result": data})

    class _ServerDialog(QDialog):
        def __init__(self, current: str) -> None:
            super().__init__()
            self.setWindowTitle("连接到 BidCraft 服务器")
            self.setMinimumWidth(420)
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("服务器地址（例：https://bid.example.com）"))
            self.edit = QLineEdit(current or "http://127.0.0.1:8000")
            layout.addWidget(self.edit)
            self.hint = QLabel("")
            layout.addWidget(self.hint)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            test = buttons.addButton("测试连接", QDialogButtonBox.ButtonRole.ActionRole)
            test.clicked.connect(self._test)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _test(self) -> None:
            try:
                health = BidCraftClient(self.edit.text().strip()).health()
            except ClientError as exc:
                self.hint.setText(f"连接失败：{exc}")
                return
            self.hint.setText(f"连接成功，服务器版本 {health.get('version', '未知')}")

        def value(self) -> str:
            return self.edit.text().strip()

    class _Window(QMainWindow):
        def __init__(self, server: str, base: str, width: int, height: int) -> None:
            super().__init__()
            self.server = server
            self.base = base
            self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
            self.bridge = _Bridge(server, self)
            self.bridge.on_server_changed = self._on_server_changed
            self.setWindowTitle("BidCraft 投标工作台")
            self.resize(width, height)
            self.setMinimumSize(960, 640)
            icon = icon_path()
            if icon:
                self.setWindowIcon(QIcon(str(icon)))

            self.view = QWebEngineView(self)
            self.stack = QStackedWidget(self)
            self.setCentralWidget(self.stack)
            self._install_scripts()
            self.channel = QWebChannel(self.view.page())
            self.channel.registerObject("bridge", self.bridge)
            self.view.page().setWebChannel(self.channel)
            self.view.page().profile().downloadRequested.connect(self._on_download)
            self.stack.addWidget(self.view)
            self._build_native_pages()

            self._build_actions()
            self._build_menu()
            self._build_toolbar()
            self._build_status()
            self._build_tray()
            self._restore_geometry()
            self._initial_page()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.refresh_status)
            self.timer.start(30_000)
            self.refresh_status()

        def _on_server_changed(self, server: str) -> None:
            """登录页/菜单切换服务器后刷新页面并更新标题。"""
            self.server = server
            self.setWindowTitle(f"BidCraft 投标工作台 — {server}")
            self._initial_page()
            self.refresh_status()

        # ------------------------------------------------------ 原生页面（混合）
        def _build_native_pages(self) -> None:
            from .qtnative import BillingPage, LoginPage, MyDocsPage, ProjectsPage

            self.login_page = LoginPage(self.bridge.client, self._after_login)
            self.projects_page = ProjectsPage(self.bridge.client, self._open_project)
            self.docs_page = MyDocsPage(self.bridge.client, lambda: self.upload("doc"))
            self.billing_page = BillingPage(
                self.bridge.client, lambda: self._show_web("/billing")
            )
            for page in (
                self.login_page,
                self.projects_page,
                self.docs_page,
                self.billing_page,
            ):
                self.stack.addWidget(page)

        def _initial_page(self) -> None:
            try:
                self.bridge.client.me()
            except ClientError:
                self._show_native("login")
                return
            self._show_native("projects")

        def _after_login(self) -> None:
            self._show_native("projects")

        def _guarded(self, action: Any) -> Any:
            """未登录时拦截操作：提示并回到登录页。"""

            def handler(*_args: Any) -> None:
                try:
                    self.bridge.client.me()
                except ClientError:
                    QMessageBox.information(
                        self, "需要登录", "请先登录后再使用该功能。"
                    )
                    self._show_native("login")
                    return
                action()

            return handler

        def _show_native(self, name: str) -> None:
            pages = {
                "login": self.login_page,
                "projects": self.projects_page,
                "docs": self.docs_page,
                "billing": self.billing_page,
            }
            page = pages[name]
            self.stack.setCurrentWidget(page)
            self._set_web_actions(False)
            if name == "projects":
                self.projects_page.reload()
            elif name == "docs":
                self.docs_page.reload()
            elif name == "billing":
                self.billing_page.reload()

        def _show_web(self, path: str) -> None:
            self._sync_cookies()
            self.stack.setCurrentWidget(self.view)
            self._set_web_actions(True)
            self.view.load(QUrl(self.base + path))

        def _set_web_actions(self, enabled: bool) -> None:
            """后退/前进/刷新只对网页工作区有意义。"""
            for action in (
                getattr(self, "act_back", None),
                getattr(self, "act_forward", None),
                getattr(self, "act_reload", None),
            ):
                if action is not None:
                    action.setEnabled(enabled)

        def _open_project(self, item: dict[str, Any]) -> None:
            ref = str(item.get("id") or "")
            if ref:
                self._show_web(f"/app/projects/{ref}")

        def _sync_cookies(self) -> None:
            """把客户端会话 Cookie 注入 WebEngine，工作区网页才能复用登录态。"""
            try:
                cookies = self.bridge.client._current_cookies()
            except Exception:  # noqa: BLE001 - 同步失败不阻断页面
                return
            from PySide6.QtNetwork import QNetworkCookie

            store = self.view.page().profile().cookieStore()
            url = QUrl(self.base)
            for item in cookies:
                name = str(item.get("name") or "")
                value = str(item.get("value") or "")
                if not name:
                    continue
                cookie = QNetworkCookie(name.encode(), value.encode())
                domain = str(item.get("domain") or "")
                if domain:
                    cookie.setDomain(domain)
                cookie.setPath(str(item.get("path") or "/"))
                store.setCookie(cookie, url)

        # ---------------------------------------------------------- 初始化
        def _install_scripts(self) -> None:
            handle = QFile(":/qtwebchannel/qwebchannel.js")
            channel_js = ""
            if handle.open(QFile.OpenModeFlag.ReadOnly):
                channel_js = bytes(handle.readAll()).decode("utf-8")
                handle.close()
            scripts = self.view.page().scripts()
            for name, source in (
                ("bidcraft-bridge", bridge_script(channel_js)),
                ("bidcraft-css", css_script()),
            ):
                script = QWebEngineScript()
                script.setName(name)
                script.setSourceCode(source)
                script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
                script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
                script.setRunsOnSubFrames(False)
                scripts.insert(script)

        def _build_actions(self) -> None:
            self.act_upload = QAction("上传文档到个人知识库", self)
            self.act_upload.setShortcut(QKeySequence("Ctrl+O"))
            self.act_upload.triggered.connect(lambda: self._guarded(lambda: self.upload("doc")))
            self.act_source = QAction("上传招标原件到项目…", self)
            self.act_source.triggered.connect(self._guarded(self.upload_source))
            self.act_quit = QAction("退出", self)
            self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))
            self.act_quit.triggered.connect(QApplication.instance().quit)
            self.act_back = QAction("后退", self)
            self.act_back.setShortcut(QKeySequence("Alt+Left"))
            self.act_back.triggered.connect(self.view.back)
            self.act_forward = QAction("前进", self)
            self.act_forward.setShortcut(QKeySequence("Alt+Right"))
            self.act_forward.triggered.connect(self.view.forward)
            self.act_reload = QAction("刷新", self)
            self.act_reload.setShortcut(QKeySequence("Ctrl+R"))
            self.act_reload.triggered.connect(self.view.reload)
            self.act_zoom_in = QAction("放大", self)
            self.act_zoom_in.setShortcut(QKeySequence("Ctrl+="))
            self.act_zoom_in.triggered.connect(
                lambda: self.view.setZoomFactor(self.view.zoomFactor() + 0.1)
            )
            self.act_zoom_out = QAction("缩小", self)
            self.act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
            self.act_zoom_out.triggered.connect(
                lambda: self.view.setZoomFactor(max(0.4, self.view.zoomFactor() - 0.1))
            )
            self.act_zoom_reset = QAction("实际大小", self)
            self.act_zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
            self.act_zoom_reset.triggered.connect(lambda: self.view.setZoomFactor(1.0))
            self.act_full = QAction("全屏", self)
            self.act_full.setShortcut(QKeySequence("F11"))
            self.act_full.triggered.connect(self._toggle_full)
            self.act_devtools = QAction("开发者工具", self)
            self.act_devtools.setShortcut(QKeySequence("F12"))
            self.act_devtools.triggered.connect(self._open_devtools)
            self.act_update = QAction("检查更新…", self)
            self.act_update.triggered.connect(self.check_update)
            self.act_about = QAction("关于 BidCraft", self)
            self.act_about.triggered.connect(self._about)
            # 文件菜单：直达原生页面与常用动作
            self.act_new_project = QAction("新建项目…", self)
            self.act_new_project.setShortcut(QKeySequence("Ctrl+N"))
            self.act_new_project.triggered.connect(
                self._guarded(
                    lambda: (self._show_native("projects"), self.projects_page.create())
                )
            )
            self.act_projects = QAction("项目列表", self)
            self.act_projects.triggered.connect(
                self._guarded(lambda: self._show_native("projects"))
            )
            self.act_docs_page = QAction("我的文档", self)
            self.act_docs_page.triggered.connect(
                self._guarded(lambda: self._show_native("docs"))
            )
            self.act_billing_page = QAction("账单与充值…", self)
            self.act_billing_page.triggered.connect(
                self._guarded(lambda: self._show_native("billing"))
            )
            self.act_download_dir = QAction("下载目录设置…", self)
            self.act_download_dir.triggered.connect(self._choose_download_dir)

        def _build_menu(self) -> None:
            bar = self.menuBar()
            file_menu = bar.addMenu("文件")
            file_menu.addAction(self.act_new_project)
            file_menu.addAction(self.act_projects)
            file_menu.addAction(self.act_docs_page)
            file_menu.addAction(self.act_billing_page)
            file_menu.addSeparator()
            file_menu.addAction(self.act_upload)
            file_menu.addAction(self.act_source)
            file_menu.addSeparator()
            file_menu.addAction(self.act_download_dir)
            file_menu.addAction(self.act_quit)
            edit_menu = bar.addMenu("编辑")
            page = self.view.page()
            for label, action in (
                ("撤销", page.WebAction.Undo),
                ("重做", page.WebAction.Redo),
                ("剪切", page.WebAction.Cut),
                ("复制", page.WebAction.Copy),
                ("粘贴", page.WebAction.Paste),
                ("全选", page.WebAction.SelectAll),
            ):
                item = QAction(label, self)
                item.triggered.connect(lambda _=False, a=action: page.triggerAction(a))
                edit_menu.addAction(item)
            view_menu = bar.addMenu("视图")
            view_menu.addAction(self.act_projects)
            view_menu.addAction(self.act_docs_page)
            view_menu.addAction(self.act_billing_page)
            view_menu.addSeparator()
            for act in (
                self.act_back,
                self.act_forward,
                self.act_reload,
            ):
                view_menu.addAction(act)
            view_menu.addSeparator()
            for act in (
                self.act_zoom_in,
                self.act_zoom_out,
                self.act_zoom_reset,
                self.act_full,
                self.act_devtools,
            ):
                view_menu.addAction(act)
            help_menu = bar.addMenu("帮助")
            help_menu.addAction(self.act_update)
            help_menu.addAction(self.act_about)

        def _build_toolbar(self) -> None:
            bar = QToolBar("主导航", self)
            bar.setObjectName("mainToolbar")
            bar.setMovable(False)
            self.addToolBar(bar)
            items = list(NAV_ITEMS)
            try:
                me = self.bridge.client.me()
                user = me.get("user") if isinstance(me, dict) else None
                role = (user or me or {}).get("role", "")
            except ClientError:
                role = ""
            if role == "super_admin":
                items.append(ADMIN_NAV)
            for label, path in items:
                act = QAction(label, self)
                if path in ("/", "/my-docs", "/billing"):
                    target = {"/": "projects", "/my-docs": "docs", "/billing": "billing"}[path]
                    act.triggered.connect(
                        self._guarded(lambda name=target: self._show_native(name))
                    )
                else:
                    act.triggered.connect(
                        self._guarded(lambda p=path: self._show_web(p))
                    )
                bar.addAction(act)
            spacer = QWidget(self)
            spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                                 spacer.sizePolicy().verticalPolicy().Preferred)
            bar.addWidget(spacer)
            self.server_label = QLabel(self.server)
            bar.addWidget(self.server_label)

        def _build_status(self) -> None:
            self.status_state = QLabel("连接中…")
            self.status_hint = QLabel("")
            self.status_version = QLabel(f"v{__version__}")
            bar = self.statusBar()
            bar.addWidget(self.status_state)
            bar.addWidget(self.status_hint, 1)
            bar.addPermanentWidget(self.status_version)

        def _build_tray(self) -> None:
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            menu = QMenu(self)
            show = menu.addAction("显示主窗口")
            show.triggered.connect(self._restore)
            hide = menu.addAction("隐藏")
            hide.triggered.connect(self.hide)
            menu.addAction(self.act_update)
            menu.addSeparator()
            menu.addAction(self.act_quit)
            self.tray.setContextMenu(menu)
            self.tray.setToolTip("BidCraft 投标工作台")
            self.tray.activated.connect(
                lambda reason: self._restore()
                if reason == QSystemTrayIcon.ActivationReason.DoubleClick
                else None
            )
            self.tray.show()

        def _restore_geometry(self) -> None:
            geometry = self.settings.value(KEY_GEOMETRY)
            if geometry:
                self.restoreGeometry(geometry)
            state = self.settings.value(KEY_STATE)
            if state:
                self.restoreState(state)

        # ---------------------------------------------------------- 行为
        def _restore(self) -> None:
            self.showNormal()
            self.raise_()
            self.activateWindow()

        def _toggle_full(self) -> None:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()

        def _open_devtools(self) -> None:
            self.devtools = QMainWindow(self)
            self.devtools.setWindowTitle("BidCraft 开发者工具")
            tools = QWebEngineView(self.devtools)
            tools.setPage(self.view.page().devToolsPage())
            self.devtools.setCentralWidget(tools)
            self.devtools.resize(900, 640)
            self.devtools.show()

        def _about(self) -> None:
            QMessageBox.about(
                self,
                "关于 BidCraft",
                f"BidCraft 投标工作台\n客户端 v{__version__}\n服务器：{self.server}\n"
                "通信：原生 API 桥（离线只读缓存）",
            )

        def upload(self, kind: str, project: str = "") -> None:
            raw = self.bridge.pick_and_upload(kind, project)
            data = json.loads(raw)
            if data.get("ok"):
                doc = data.get("doc") or {}
                name = str(doc.get("filename") or "文件")
                self.status_hint.setText(f"已上传：{name}")
                self.tray.showMessage(
                    "BidCraft", f"已上传 {name}，索引将在后台完成",
                    QSystemTrayIcon.MessageIcon.Information,
                )
                if kind == "doc":
                    self._show_native("docs")
                else:
                    QMessageBox.information(
                        self, "上传成功", f"{name} 已上传，解析与索引将在后台完成。"
                    )
            else:
                message = str(data.get("error") or "未知错误")
                self.status_hint.setText(f"上传失败：{message}")
                QMessageBox.warning(self, "上传失败", message)

        def _choose_download_dir(self) -> None:
            current = self.settings.value(KEY_DOWNLOAD_DIR) or str(Path.home())
            directory = QFileDialog.getExistingDirectory(self, "选择下载目录", str(current))
            if directory:
                self.settings.setValue(KEY_DOWNLOAD_DIR, directory)
                self.status_hint.setText(f"下载目录：{directory}")

        def upload_source(self) -> None:
            try:
                projects = self.bridge.client.projects()
            except ClientError as exc:
                self.status_hint.setText(f"无法获取项目：{exc}")
                return
            if not projects:
                self.status_hint.setText("暂无项目，请先在网页端创建")
                return
            names = [f"{p.get('name', '')}（{p.get('id', '')}）" for p in projects]
            choice, ok = QInputDialog.getItem(self, "选择项目", "上传到哪个项目？", names, 0, False)
            if not ok:
                return
            index = names.index(choice)
            self.upload("project_source", str(projects[index].get("id", "")))

        def _on_download(self, item: Any) -> None:
            default_dir = self.settings.value(KEY_DOWNLOAD_DIR) or str(Path.home())
            suggested = getattr(item, "downloadFileName", lambda: "download")()
            target, _ = QFileDialog.getSaveFileName(self, "保存文件", f"{default_dir}/{suggested}")
            if not target:
                item.cancel()
                return
            self.settings.setValue(KEY_DOWNLOAD_DIR, str(Path(target).parent))
            item.setDownloadDirectory(str(Path(target).parent))
            item.setDownloadFileName(Path(target).name)
            item.accept()
            self.status_hint.setText(f"开始下载：{Path(target).name}")

        def refresh_status(self) -> None:
            try:
                self.bridge.client.health()
            except ClientError:
                self.status_state.setText("离线只读（显示本地缓存）")
                return
            self.status_state.setText("在线")

        def check_update(self) -> None:
            latest = latest_version()
            if not latest:
                self.status_hint.setText("检查更新失败（网络不可达）")
                return
            if compare_version(__version__, latest) < 0:
                answer = QMessageBox.question(
                    self,
                    "发现新版本",
                    f"当前 v{__version__}，最新 {latest}。是否打开下载页？",
                )
                if answer == QMessageBox.StandardButton.Yes:
                    QDesktopServices.openUrl(QUrl(RELEASE_PAGE))
            else:
                self.status_hint.setText(f"已是最新版本（v{__version__}）")

        def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
            self.settings.setValue(KEY_GEOMETRY, self.saveGeometry())
            self.settings.setValue(KEY_STATE, self.saveState())
            if self.tray.isVisible() and not QApplication.instance().closingDown():
                event.ignore()
                self.hide()
                if not self.settings.value(KEY_TRAY_HINT):
                    self.settings.setValue(KEY_TRAY_HINT, True)
                    self.tray.showMessage(
                        "BidCraft 仍在运行",
                        "已最小化到托盘，双击图标可恢复；退出请用菜单「文件→退出」。",
                        QSystemTrayIcon.MessageIcon.Information,
                    )
                return
            event.accept()


def run_qt(
    server: str,
    *,
    port: int = 0,
    width: int = 1280,
    height: int = 860,
) -> bool:
    """启动 Qt 原生外壳；Qt 不可用返回 False 供上层回退。"""
    if not QT_AVAILABLE:
        return False
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(SETTINGS_APP)
    app.setOrganizationName(SETTINGS_ORG)
    app.setDesktopFileName("bidcraft")
    app.setApplicationDisplayName("BidCraft 投标工作台")
    icon = icon_path()
    if icon:
        app.setWindowIcon(QIcon(str(icon)))

    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    name = f"bidcraft-desktop-{getpass.getuser()}"
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(200):
        probe.write(b"show")
        probe.flush()
        probe.disconnectFromServer()
        return True
    QLocalServer.removeServer(name)
    instance = QLocalServer()
    instance.listen(name)

    target = server or str(settings.value(KEY_SERVER) or "")
    if not target:
        dialog = _ServerDialog("")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return True
        target = dialog.value()
    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    settings.setValue(KEY_SERVER, target)

    httpd = start_gateway(target, port=port, spa_dir=DEFAULT_SPA_DIR,
                          token=secrets.token_urlsafe(24), proxy_enabled=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    window = _Window(target, base, width, height)
    instance.newConnection.connect(lambda: window._restore())  # noqa: SLF001
    window.show()
    try:
        app.exec()
    finally:
        httpd.shutdown()
        httpd.server_close()
    return True
