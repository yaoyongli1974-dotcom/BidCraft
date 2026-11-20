# 发版记录

按版本归档的**对外口径**：GitHub Release 正文以本目录文件为源，issue / 邮件回复引用同一份措辞。
补记历史版本（v0.6.4–v0.6.8）时，本仓库尚未打 tag，故正文先行入库再补 tag，避免口径散落在
聊天记录或临时文件里。

| 版本 | 日期 | 主题 | 文件 |
| --- | --- | --- | --- |
| v0.6.9 | 2026-11-20 | 交付前风险总览 + 导出硬门禁 | [v0.6.9.md](v0.6.9.md) |
| v0.6.8 | 2026-11-20 | 桌面端冻结配方、元信息去「弱电」 | [v0.6.8.md](v0.6.8.md) |
| v0.6.7 | 2026-11-20 | 素材 `other` 不再免检 + 桌面端归档声明 | [v0.6.7.md](v0.6.7.md) |
| v0.6.6 | 2026-11-20 | 全仓时区排查（北京时间口径） | [v0.6.6.md](v0.6.6.md) |
| v0.6.5 | 2026-11-20 | 素材有效期按北京日历日、未知类型 fail-closed | [v0.6.5.md](v0.6.5.md) |
| v0.6.4 | 2026-11-20 | 超管只读运行状态 `/admin/health` | [v0.6.4.md](v0.6.4.md) |

另见 [desktop-archive.md](desktop-archive.md)：桌面客户端归档的对外声明与统一回复模板。

## 发版规矩（避免踩客户端）

- **服务端发版打 tag 是安全的**：`.github/workflows/desktop-release.yml` 只有 `workflow_dispatch`，
  tag push 不再触发客户端构建；且 `attach_to_release` 默认 `false`，即使手动重建也不会把安装包
  重新附加到 Release。
- **不得**发布桌面安装包：v0.6.3 起客户端冻结，公开分发会重新激活 Qt/PySide6 的 LGPL-3.0
  再分发义务（见根目录 `NOTICE` 与 `docs/desktop.md`）。
- 一个版本的 tag 打在**该版本号最后一次出现**的提交上（版本号 bump 之后可能还有文档提交）。
- Release 正文用 `gh release create <tag> --notes-file docs/release-notes/<版本>.md`，
  不手工在网页里另写一份。
