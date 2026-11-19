"""Qt 原生页面（混合方案 C）：登录、项目列表、我的文档、账单。

这些页面用 Qt 控件直接调用 :mod:`bidcraft.client`，不经过网页；
复杂的项目工作区（大纲/正文/清单/核对）仍由内嵌网页承载。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

try:  # pragma: no cover - 仅在装有 PySide6 的桌面环境执行
    from PySide6.QtCore import QObject, Qt, QThreadPool, Signal
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QFormLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except Exception:  # noqa: BLE001 - 无 Qt 时仅纯函数可用  # pragma: no cover
    QT_AVAILABLE = False

from .client import BidCraftClient


def human_size(size: int) -> str:
    """把字节数格式化为便于阅读的字符串。"""
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def doc_status_label(status: str) -> str:
    return {
        "ready": "可检索",
        "indexing": "索引中",
        "failed": "索引失败",
    }.get(status or "", status or "—")


def project_columns() -> tuple[str, ...]:
    return ("名称", "标段/包件", "更新时间", "我的角色")


def doc_columns() -> tuple[str, ...]:
    return ("文件名", "大小", "状态", "上传时间")


if QT_AVAILABLE:

    class Async(QObject):
        """在线程池里跑阻塞调用，结果回到 GUI 线程。"""

        done = Signal(object, object)

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.pool = QThreadPool.globalInstance()

        def run(self, fn: Callable[[], Any], on_done: Callable[[Any, Exception | None], None]) -> None:
            def worker() -> None:
                try:
                    result = fn()
                    error: Exception | None = None
                except Exception as exc:  # noqa: BLE001 - 统一回传
                    result, error = None, exc
                # Signal 跨线程默认排队到 GUI 线程
                self.done.emit(result, error)

            self.done.connect(on_done)
            self.pool.start(worker)

    class LoginPage(QWidget):
        def __init__(self, client: BidCraftClient, on_success: Callable[[], None], parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.client = client
            self.on_success = on_success
            self.async_ = Async(self)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(64, 48, 64, 48)
            layout.setSpacing(14)
            title = QLabel("BidCraft 投标工作台")
            title.setStyleSheet("font-size: 22px; font-weight: 600;")
            layout.addWidget(title)
            subtitle = QLabel("使用账号登录，或在工作区右上角切换服务器。")
            subtitle.setStyleSheet("color: palette(mid);")
            layout.addWidget(subtitle)

            form = QFormLayout()
            form.setSpacing(10)
            self.username = QLineEdit()
            self.username.setPlaceholderText("用户名")
            self.password = QLineEdit()
            self.password.setPlaceholderText("密码")
            self.password.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow("用户名", self.username)
            form.addRow("密　码", self.password)
            layout.addLayout(form)

            self.button = QPushButton("登录")
            self.button.setDefault(True)
            self.button.clicked.connect(self.submit)
            self.password.returnPressed.connect(self.submit)
            layout.addWidget(self.button)

            self.message = QLabel("")
            self.message.setWordWrap(True)
            layout.addWidget(self.message)
            layout.addStretch(1)

        def submit(self) -> None:
            username = self.username.text().strip()
            password = self.password.text()
            if not username or not password:
                self.message.setText("请输入用户名和密码。")
                return
            self.button.setEnabled(False)
            self.message.setText("正在登录…")

            def done(result: Any, error: Exception | None) -> None:
                self.button.setEnabled(True)
                if error is not None:
                    self.message.setText(str(error))
                    return
                self.message.setText("")
                self.password.clear()
                self.on_success()

            self.async_.run(lambda: self.client.login(username, password), done)

    class ProjectsPage(QWidget):
        def __init__(
            self,
            client: BidCraftClient,
            on_open: Callable[[dict[str, Any]], None],
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self.client = client
            self.on_open = on_open
            self.async_ = Async(self)
            self.items: list[dict[str, Any]] = []

            layout = QVBoxLayout(self)
            bar = QHBoxLayout()
            self.search = QLineEdit()
            self.search.setPlaceholderText("搜索项目名称 / 标段")
            self.search.textChanged.connect(self.apply_filter)
            bar.addWidget(self.search, 1)
            self.refresh_button = QPushButton("刷新")
            self.refresh_button.clicked.connect(self.reload)
            bar.addWidget(self.refresh_button)
            self.new_button = QPushButton("新建项目")
            self.new_button.clicked.connect(self.create)
            bar.addWidget(self.new_button)
            layout.addLayout(bar)

            self.table = QTableWidget(0, len(project_columns()), self)
            self.table.setHorizontalHeaderLabels(list(project_columns()))
            self.table.verticalHeader().setVisible(False)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.table.doubleClicked.connect(lambda *_: self.open_selected())
            layout.addWidget(self.table, 1)

            actions = QHBoxLayout()
            open_button = QPushButton("打开项目")
            open_button.clicked.connect(self.open_selected)
            actions.addWidget(open_button)
            delete_button = QPushButton("删除")
            delete_button.clicked.connect(self.delete_selected)
            actions.addWidget(delete_button)
            actions.addStretch(1)
            layout.addLayout(actions)

            self.message = QLabel("")
            layout.addWidget(self.message)

        def reload(self) -> None:
            self.refresh_button.setEnabled(False)
            self.message.setText("加载中…")

            def done(result: Any, error: Exception | None) -> None:
                self.refresh_button.setEnabled(True)
                if error is not None:
                    self.message.setText(str(error))
                    return
                self.items = list(result or [])
                self.message.setText(f"共 {len(self.items)} 个项目")
                self.apply_filter()

            self.async_.run(self.client.projects, done)

        def apply_filter(self) -> None:
            needle = self.search.text().strip().lower()
            rows = [
                item
                for item in self.items
                if not needle
                or needle in str(item.get("name", "")).lower()
                or needle in str(item.get("bid_section", "")).lower()
            ]
            self.table.setRowCount(len(rows))
            for row, item in enumerate(rows):
                values = (
                    item.get("name", ""),
                    item.get("bid_section", "") or "—",
                    str(item.get("updated_at", "") or "")[:16].replace("T", " "),
                    item.get("my_role", "") or "—",
                )
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(str(value))
                    cell.setData(Qt.ItemDataRole.UserRole, item)
                    self.table.setItem(row, column, cell)

        def selected(self) -> dict[str, Any] | None:
            row = self.table.currentRow()
            if row < 0:
                return None
            cell = self.table.item(row, 0)
            return cell.data(Qt.ItemDataRole.UserRole) if cell else None

        def open_selected(self) -> None:
            item = self.selected()
            if item:
                self.on_open(item)

        def create(self) -> None:
            from PySide6.QtWidgets import QInputDialog

            name, ok = QInputDialog.getText(self, "新建项目", "项目名称")
            if not ok or not name.strip():
                return
            self.new_button.setEnabled(False)

            def done(result: Any, error: Exception | None) -> None:
                self.new_button.setEnabled(True)
                if error is not None:
                    QMessageBox.warning(self, "新建失败", str(error))
                    return
                self.reload()
                if isinstance(result, dict) and result.get("id"):
                    self.on_open(result)

            self.async_.run(lambda: self.client.create_project(name.strip()), done)

        def delete_selected(self) -> None:
            item = self.selected()
            if not item:
                return
            if QMessageBox.question(self, "删除项目", f"确定删除「{item.get('name')}」？此操作不可恢复。") != QMessageBox.StandardButton.Yes:
                return
            self.async_.run(lambda: self.client.delete_project(item["id"]), lambda _r, e: (QMessageBox.warning(self, "删除失败", str(e)) if e else self.reload()))

    class MyDocsPage(QWidget):
        def __init__(self, client: BidCraftClient, on_pick: Callable[[], None], parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.client = client
            self.on_pick = on_pick
            self.async_ = Async(self)
            self.items: list[dict[str, Any]] = []

            layout = QVBoxLayout(self)
            bar = QHBoxLayout()
            hint = QLabel("上传的文件会保存到服务器个人目录并自动建立索引，仅自己可检索。")
            hint.setStyleSheet("color: palette(mid);")
            bar.addWidget(hint, 1)
            upload_button = QPushButton("上传文件…")
            upload_button.clicked.connect(self.on_pick)
            bar.addWidget(upload_button)
            refresh_button = QPushButton("刷新")
            refresh_button.clicked.connect(self.reload)
            bar.addWidget(refresh_button)
            layout.addLayout(bar)

            self.table = QTableWidget(0, len(doc_columns()), self)
            self.table.setHorizontalHeaderLabels(list(doc_columns()))
            self.table.verticalHeader().setVisible(False)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            layout.addWidget(self.table, 1)

            actions = QHBoxLayout()
            download = QPushButton("下载")
            download.clicked.connect(self.download_selected)
            actions.addWidget(download)
            delete = QPushButton("删除")
            delete.clicked.connect(self.delete_selected)
            actions.addWidget(delete)
            actions.addStretch(1)
            layout.addLayout(actions)
            self.message = QLabel("")
            layout.addWidget(self.message)

        def reload(self) -> None:
            self.message.setText("加载中…")

            def done(result: Any, error: Exception | None) -> None:
                if error is not None:
                    self.message.setText(str(error))
                    return
                self.items = list(result or [])
                self.message.setText(f"共 {len(self.items)} 个文档")
                self.table.setRowCount(len(self.items))
                for row, item in enumerate(self.items):
                    values = (
                        item.get("filename", ""),
                        human_size(int(item.get("size") or 0)),
                        doc_status_label(str(item.get("status") or "")),
                        str(item.get("created_at") or "")[:16].replace("T", " "),
                    )
                    for column, value in enumerate(values):
                        cell = QTableWidgetItem(str(value))
                        cell.setData(Qt.ItemDataRole.UserRole, item)
                        self.table.setItem(row, column, cell)

            self.async_.run(self.client.list_docs, done)

        def selected(self) -> dict[str, Any] | None:
            row = self.table.currentRow()
            if row < 0:
                return None
            cell = self.table.item(row, 0)
            return cell.data(Qt.ItemDataRole.UserRole) if cell else None

        def download_selected(self) -> None:
            from PySide6.QtWidgets import QFileDialog

            item = self.selected()
            if not item:
                return
            target, _ = QFileDialog.getSaveFileName(self, "保存文档", item.get("filename", ""))
            if not target:
                return

            def done(_result: Any, error: Exception | None) -> None:
                self.message.setText(str(error) if error else f"已保存到 {target}")

            self.async_.run(lambda: self.client.download(f"/my/docs/{item['id']}/file", target), done)

        def delete_selected(self) -> None:
            item = self.selected()
            if not item:
                return
            if QMessageBox.question(self, "删除文档", f"确定删除「{item.get('filename')}」？") != QMessageBox.StandardButton.Yes:
                return

            def done(_result: Any, error: Exception | None) -> None:
                if error is not None:
                    QMessageBox.warning(self, "删除失败", str(error))
                    return
                self.reload()

            self.async_.run(lambda: self.client.delete_doc(item["id"]), done)

    class BillingPage(QWidget):
        def __init__(self, client: BidCraftClient, on_recharge: Callable[[], None], parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.client = client
            self.on_recharge = on_recharge
            self.async_ = Async(self)

            layout = QVBoxLayout(self)
            self.balance = QLabel("余额 —")
            self.balance.setStyleSheet("font-size: 20px; font-weight: 600;")
            layout.addWidget(self.balance)
            self.vip = QLabel("会员状态 —")
            layout.addWidget(self.vip)

            bar = QHBoxLayout()
            self.code = QLineEdit()
            self.code.setPlaceholderText("兑换码")
            bar.addWidget(self.code, 1)
            redeem = QPushButton("兑换")
            redeem.clicked.connect(self.redeem)
            bar.addWidget(redeem)
            recharge = QPushButton("在线充值 / 购买会员…")
            recharge.clicked.connect(self.on_recharge)
            bar.addWidget(recharge)
            layout.addLayout(bar)

            layout.addWidget(QLabel("最近流水"))
            self.table = QTableWidget(0, 4, self)
            self.table.setHorizontalHeaderLabels(["时间", "类型", "金额", "备注"])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            layout.addWidget(self.table, 1)
            self.message = QLabel("")
            layout.addWidget(self.message)

        def reload(self) -> None:
            def done(result: Any, error: Exception | None) -> None:
                if error is not None:
                    self.message.setText(str(error))
                    return
                data = result or {}
                summary = data.get("summary") or {}
                self.balance.setText(f"余额 {summary.get('balance', 0)} 点")
                if summary.get("is_vip"):
                    self.vip.setText(f"VIP 会员 · 到期 {str(summary.get('vip_expires_at') or '')[:10]}")
                elif summary.get("is_sub"):
                    self.vip.setText(f"子账号 · 费用由主账号 {summary.get('parent_username', '')} 承担")
                else:
                    self.vip.setText("普通用户")
                kinds = {
                    "signup": "注册赠送",
                    "recharge": "充值",
                    "redeem": "兑换",
                    "consume": "生成消耗",
                    "refund": "退还",
                    "admin": "管理调整",
                    "vip": "VIP 续期",
                }
                rows = list(data.get("transactions") or [])
                self.table.setRowCount(len(rows))
                for row, item in enumerate(rows):
                    values = (
                        str(item.get("created_at") or "")[:16].replace("T", " "),
                        kinds.get(str(item.get("kind") or ""), str(item.get("kind") or "")),
                        str(item.get("amount", "")),
                        str(item.get("note") or ""),
                    )
                    for column, value in enumerate(values):
                        self.table.setItem(row, column, QTableWidgetItem(value))
                self.message.setText("")

            self.async_.run(self.client.billing, done)

        def redeem(self) -> None:
            code = self.code.text().strip()
            if not code:
                return

            def done(_result: Any, error: Exception | None) -> None:
                if error is not None:
                    self.message.setText(str(error))
                    return
                self.code.clear()
                self.message.setText("兑换成功")
                self.reload()

            self.async_.run(lambda: self.client.redeem(code), done)
