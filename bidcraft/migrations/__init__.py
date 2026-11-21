"""Alembic 迁移脚本目录。

迁移 URL 由 :func:`bidcraft.db.init_db` 在运行时注入，不依赖 alembic.ini，
因此 Docker 与测试（任意临时库）都能直接用 programmatic API 调用。
"""
