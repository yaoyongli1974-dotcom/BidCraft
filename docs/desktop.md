# 桌面客户端（C/S 模式）

BidCraft 默认是 B/S 部署：服务器跑 `bidcraft serve`，用户用浏览器访问。
**桌面客户端**在 B/S 之上增加一个本机进程，用同一套服务端接口提供「客户端」体验：

```
[Qt 原生窗口 · 本地静态资源]  ──JS 桥（QWebChannel）──▶  [Python 客户端]  ──HTTPS/JSON──▶  [BidCraft 服务器 :8000]
        │
        └── 原生文件对话框 / 下载保存 / 托盘 / 单实例 / 离线只读缓存
```

它解决的核心问题是：**用户的文件在自己电脑上**。桌面模式用原生文件对话框直接读取本机文件并上传，
无需先把文件 scp 到服务器目录。

### 原生外壳（默认）

`bidcraft desktop` 默认启动 **Qt 原生外壳**（PySide6 + QtWebEngine），是一个真正的桌面应用窗口，
而不是浏览器标签页：

| 部件 | 内容 |
| --- | --- |
| 菜单栏 | 文件（上传文档 `Ctrl+O`、上传原件到项目、下载目录、退出 `Ctrl+Q`）、编辑（撤销/重做/剪切/复制/粘贴/全选）、视图（后退/前进/刷新 `Ctrl+R`、缩放 `Ctrl+=`/`Ctrl+-`/`Ctrl+0`、全屏 `F11`、开发者工具 `F12`）、帮助（使用说明、检查更新、关于） |
| 工具栏 | 后退/前进/刷新 + 原生导航（项目、我的文档、账单；超级管理员另有「管理」）+ 服务器地址与连接状态 |
| 状态栏 | 在线 / 离线只读、最近操作提示、客户端版本 |
| 系统集成 | 应用图标与窗口标题、系统托盘（最小化到托盘、双击恢复、托盘菜单）、**单实例**（再次启动激活已有窗口）、窗口几何与服务器地址记忆（QSettings）、原生下载保存对话框、托盘通知、30 秒在线状态轮询、GitHub Releases 检查更新 |
| 界面 | 网页顶栏被隐藏，导航由原生工具栏承担；首次运行可用「连接服务器」对话框填写地址并测试连通性 |

窗口内的界面仍是同一套 React 应用，但**请求全部经原生桥直连服务端 API**（见下节），
因此 Cookie 由客户端持有，不依赖浏览器同源策略。

三种运行模式：

```bash
bidcraft desktop --server https://bid.example.com   # 默认：Qt 原生外壳
bidcraft desktop --server ... --webview             # pywebview 内嵌窗口（无 Qt 时的备选）
bidcraft desktop --server ... --browser             # 浏览器 + 本地网关（旧行为）
```

Qt 不可用（缺 PySide6 / 无图形环境）时自动逐级回退：Qt → pywebview → 浏览器。

### 通信方式：API 直连

窗口模式下**不做反向代理**：本机只托管前端静态资源，所有业务请求由 Python 客户端
（`BidCraftClient`）直接调用服务端 REST API，再把结果回传给窗口内的 JS：

```
[原生窗口 SPA] --pywebview JS 桥--> [Python 客户端] --HTTPS/JSON--> [BidCraft 服务器]
```

- 会话 Cookie 由客户端保存，不依赖浏览器同源策略；
- 请求体/响应体以 base64 在桥上传输，前端 `apiFetch` 自动改走桥；
- 文件上传走原生对话框（`pick_and_upload`），不经过网页文件选择器。

### 离线只读缓存

GET 请求成功时按「服务器 + 路径」缓存到 `~/.config/bidcraft-client/cache/`；
服务器不可达时回放缓存并标记 `offline`，界面顶部显示「离线只读」横幅，
写操作会明确失败提示，不会静默丢数据。缓存范围仅限项目/我的文档/待我审批等只读接口。

### 平台依赖

- **Windows**：WebView2（Win10+ 通常自带）
- **macOS**：系统 WKWebView
- **Linux**：需要 `PySide6` + `qtpy`（推荐，`uv pip install PySide6 qtpy`）或 `python3-gi` + WebKitGTK；缺失时自动回退浏览器模式。Wayland 桌面建议 `QT_QPA_PLATFORM=wayland`，容器/root 环境加 `QTWEBENGINE_DISABLE_SANDBOX=1`

## 启动

```bash
# 在本机（不是服务器）安装客户端
uv sync

# 连接远端服务器，启动本地网关并打开窗口
bidcraft desktop --server https://bid.example.com          # 原生窗口
bidcraft desktop --server https://bid.example.com --browser  # 浏览器模式（调试）

# 指定端口 / 不自动打开浏览器（便于脚本或二次封装）
bidcraft desktop --server http://127.0.0.1:8000 --port 8765 --no-browser
```

启动后终端会打印本地地址与**客户端令牌**；窗口内即是完整的 BidCraft 界面，登录状态、
Cookie、SSE 进度全部由网关透明转发。

## 本地文件桥

「我的文档」页在桌面模式下会多出「从本机选择文件」：

1. 点击后弹出本机文件选择窗（网关在客户端机器上渲染目录列表）；
2. 选中文件后由客户端进程读取并通过网关上传到服务器；
3. 上传成功通过 `postMessage` 通知主窗口刷新列表，索引在后台完成。

安全边界：

- 网关只监听 `127.0.0.1`，不对外网暴露；
- `/__client/*` 接口（info / upload / picker-upload）均校验客户端令牌，令牌每次启动随机生成；
- 文件读取仅发生在客户端进程，服务器只收到上传后的文件内容，不会访问你的本地路径；
- 单文件上限 50MB，格式与服务器端白名单一致。

## 命令行客户端

同一套客户端库也可脚本化使用（适合运维、批量上传、定时下载）：

```bash
bidcraft client login --server https://bid.example.com -u admin
bidcraft client status                     # 健康检查 + 当前用户
bidcraft client projects                   # 项目列表
bidcraft client upload ./手册.pdf           # 默认上传到「我的文档」
bidcraft client upload ./招标文件.pdf --project PRJ-001
bidcraft client download DOC_ID --out ./downloads/
```

会话 Cookie 保存在 `~/.config/bidcraft-client/cookies.json`（权限 0600），
可用 `BIDCRAFT_CLIENT_HOME` 指定其他目录。

## 与 B/S 的关系

| 能力 | 浏览器（B/S） | 桌面客户端（C/S） |
| --- | --- | --- |
| 全部业务功能 | ✅ | ✅（同一套 SPA 与 API） |
| 登录与权限 | Cookie | Cookie（网关透传，同源） |
| 本机文件直传 | 需先传到服务器目录 | ✅ 本地文件桥 |
| 离线可用 | ❌ | ❌（仍需连接服务器） |

桌面模式不改变服务端行为，也不引入独立数据库；它是服务端 API 之上的一个轻客户端。

## 打包与安装包

仓库提供三种平台的打包脚本，PyInstaller 负责生成独立可执行文件（无需用户安装 Python），
再由平台工具封装成安装包：

| 平台 | 命令 | 产物 |
| --- | --- | --- |
| Linux（含 deb/rpm） | `bash packaging/build_deb.sh` / `bash packaging/build_rpm.sh` | `dist/bidcraft-desktop_<版本>_<arch>.deb`、`dist/*.rpm` |
| Windows | `packaging/build_windows.ps1` | `dist/bidcraft-desktop-<版本>-windows.zip`（内含 `BidCraft.exe`） |
| macOS | `bash packaging/build_macos_app.sh` | `dist/bidcraft-desktop-<版本>-macos.zip`（内含 `BidCraft.app`） |

前置：`npm run build`（web）→ `uv pip install pyinstaller` → `bash packaging/build_pyinstaller.sh`。
deb 使用 `dpkg-deb` 直接构建（无 debhelper 依赖），rpm 使用 `nfpm`。

安装后：

- Linux：`/usr/bin/bidcraft-desktop`，菜单项「BidCraft 投标工作台」；
- Windows / macOS：双击 `BidCraft.exe` / `BidCraft.app`。

启动时可用 `--server URL` 指定服务器，或设置环境变量 `BIDCRAFT_SERVER`，
或先执行一次 `bidcraft client login --server URL` 保存地址，之后双击即自动连接。

跨平台构建由 `.github/workflows/desktop-release.yml` 负责：推送 `v*` 标签时在
Windows / macOS / Ubuntu 三个原生 runner 上构建并把 `.zip`、`.deb`、`.rpm` 附加到 Release。
