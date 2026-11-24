# BidCraft v0.31.1 阶段 7：发版准备报告

## 1. 结论

版本号、变更索引与发版说明已更新为 0.31.1；无新迁移、无 API 路由变化、无生产配置改动。最终全量回归在阶段 8 执行并回填发版说明。

## 2. 交付项

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | 后端版本 | 完成 | `bidcraft/__init__.py:3`、`pyproject.toml:3` |
| 2 | lock 文件 | 完成 | `uv.lock` 仅项目自身版本 1 处 |
| 3 | 部署清单 | 完成 | `deploy/docker-compose.yml` 2 处、`deploy/README.md` 12 处 |
| 4 | 前端版本 | 完成 | `web/package.json` 1 处、`web/package-lock.json` 2 处 |
| 5 | CHANGELOG | 完成 | 新增 v0.31.1 索引节 |
| 6 | 发版说明索引 | 完成 | `docs/release-notes/README.md` 新增行 |
| 7 | 发版说明正文 | 完成 | `docs/release-notes/v0.31.1.md`，9 节 |
| 8 | 历史版本保留 | 完成 | v0.31.0 条目未改动 |
| 9 | 历史迁移 | 不变 | 单 head 仍为 `0012_generation_lease` |
| 10 | API 路由 | 不变 | `tests/test_api_package.py` 通过 |
| 11 | 回滚 | 完成 | 回退版本/文档提交即可，无数据变更 |

## 3. 验证

| 检查 | 结果 |
|---|---|
| `ruff check bidcraft tests scripts` | 通过 |
| `git diff --check` | 通过 |
| 目标 pytest（迁移/路由/阶段1-5/判定一致率） | **55 passed** |
| 版本一致性静态检查 | 通过 |

## 4. 边界

发版说明第 8 节的后端全量结果待阶段 8 回填；本阶段不构建 Release、不推送 main。
