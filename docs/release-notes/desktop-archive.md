# 桌面客户端归档：对外声明与回复模板

桌面客户端（C/S 模式）冻结于 **v0.6.3**。自 2026-11-20 起，GitHub Releases 上的桌面安装包已全部
撤下（v0.6.1 / v0.6.2 / v0.6.3 共 12 个 asset），不再提供更新与官方下载。服务端发行包
（`whl` / `tar.gz`）不受影响。

理由与边界：

- **LGPL 再分发义务附着于「分发」动作**。停止分发即显著减轻义务；继续托管安装包则需持续履行
  Qt/PySide6 的 LGPL-3.0 要求（可替换的共享库、随附许可文本与版权声明、允许重链接等）。
- **代码没有删**：`bidcraft/desktop.py`、`qtshell.py`、`client.py`、`packaging/` 仍在仓库内，
  依赖固化为 `pyproject.toml` 的 `desktop` / `build` extra，精确版本由 `uv.lock` 锁定。
- **不承诺新功能**：不接受桌面端功能 PR；服务端接口变更若破坏客户端，以
  `tests/test_client.py`、`tests/test_desktop.py`、`tests/test_qtshell.py` 为准。

## Release 顶部声明（v0.6.1 / v0.6.2 / v0.6.3 使用）

> 桌面客户端已归档。原 v0.6.X 的 4 个桌面安装包已撤下，不再提供更新与官方下载；已下载者可继续使用。
> 如需最新版本请使用 B/S 部署（`bidcraft serve`，浏览器访问）。第三方组件许可见仓库 NOTICE。
>
> 桌面端冻结于 **v0.6.3**，本页保留为历史发行记录。自行构建或再分发须遵守 Qt/PySide6 的
> **LGPL-3.0** 动态链接与可替换要求，见仓库 `NOTICE` 与 `docs/desktop.md`；依赖已固化为
> `pyproject.toml` 的 `desktop` / `build` extra（`uv sync --frozen --extra desktop --extra build`）。

## 回复模板

### 「下载链接 404 / 安装包在哪？」

> 桌面客户端已归档，安装包已停止官方分发，下载链接不再有效——这是有意为之，不是故障。
> 推荐直接用 B/S 部署：服务器上 `bidcraft serve`，浏览器访问即可，功能与客户端一致且持续更新。
> 已下载的旧安装包可以继续使用，但不再收到修复。

### 「什么时候发新版客户端？」

> 桌面客户端冻结于 v0.6.3，不再发布新版本。后续修复与新功能都只进服务端（B/S），
> 因为你用的是浏览器访问，升级服务端即可生效，无需重装客户端。

### 「我要自己构建客户端」

> 代码与构建脚本都在仓库里，依赖已固化：

```bash
cd web && npm run build && cd ..
uv sync --frozen --extra desktop --extra build
bash packaging/build_pyinstaller.sh
```

> 产物在 `dist/BidCraft/`；Linux 打包脚本另有 `packaging/build_deb.sh` / `build_rpm.sh`。
> 注意：**自行构建后再分发**，你就是 LGPL 意义上的分发者，须履行 Qt/PySide6 的义务
> （动态链接、随附 LGPL 文本与版权声明、允许用户替换/重链接库），详见 `NOTICE`
> 的「桌面客户端」节。

### 「客户端下载后无法连接服务器」

> 客户端只负责连到你指定的服务器地址，不内置任何公共服务。启动时用 `--server URL` 指定，
> 或设置环境变量 `BIDCRAFT_SERVER`，或先执行一次 `bidcraft client login --server URL` 保存地址。
> 需要先在同一台机器（或局域网内）跑起 `bidcraft serve`。

### 「能不能恢复分发安装包 / 出个安全补丁包」

> 可以，但属于需要你确认的对外分发决定：手动触发 `desktop-release.yml` 时勾选
> `attach_to_release`，并先履行 `NOTICE` 中列出的 LGPL-3.0 义务（提供可替换的 Qt 共享库、
> 随附完整许可文本与版权声明、告知用户可替换方式）。不勾选时构建产物只作为 Actions artifact
> 留存。
