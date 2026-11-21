"""API 包结构守护：路由已按域拆分到 api/routes/*，但对外契约完全不变。

这里只验证结构层面的不变量；URL / 请求响应格式由既有接口测试覆盖。
"""

from __future__ import annotations

import sys


def test_api_package_shim_points_to_app_module():
    from bidcraft import api

    core = sys.modules["bidcraft.api.app"]
    assert api is core
    assert api.__name__ == "bidcraft.api.app"


def test_domain_routers_are_importable_and_populated():
    from bidcraft.api.routes import (
        account,
        admin,
        approvals,
        auth,
        batch_jobs,
        billing,
        health,
        jobs,
        legal,
        my,
        packages,
        projects,
    )

    modules = [account, admin, approvals, auth, batch_jobs, billing, health, jobs, legal, my, packages, projects]
    assert sum(len(mod.router.routes) for mod in modules) == 194


def test_route_modules_do_not_import_api_module_through_package():
    """路由模块从 ..app 取共享名字；本地相对 import 必须跨过 api 包到 bidcraft。"""

    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "bidcraft" / "api" / "routes"
    for path in root.glob("*.py"):
        if path.name == "__init__.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 2:
                # from ..app import ... 是允许的；from ..export / ..models 会在
                # shim 后解析到 non-package 的 bidcraft.api，必须写成 from ...xxx。
                assert node.module == "app", (path.name, node.module, node.lineno)


def test_monkeypatch_reaches_route_call_site(monkeypatch):
    from bidcraft import api

    sentinel = object()
    monkeypatch.setattr(api, "_run_kb_reindex_job", sentinel)
    assert sys.modules["bidcraft.api.app"]._run_kb_reindex_job is sentinel
