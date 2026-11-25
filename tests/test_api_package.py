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
        collaboration,
        commercial,
        enterprise,
        evaluation,
        health,
        jobs,
        legal,
        my,
        orchestration,
        packages,
        projects,
        similarity,
    )

    modules = [
        account, admin, approvals, auth, batch_jobs, billing, commercial,
        enterprise, evaluation, health, jobs, legal, my, packages, projects, similarity,
        orchestration, collaboration,
    ]
    # v0.32 Phase 1 新增 6 条章节规划路由（projects.py），Phase 2 新增 10 条
    # 知识资产路由（admin.py 5 条 + enterprise.py 5 条），Phase 3 新增 1 条
    # 只读检索预览路由（projects.py），Phase 4 新增 1 条只读工具目录路由
    # （orchestration.py：GET /agents/{agent_type}/tools）。
    assert sum(len(mod.router.routes) for mod in modules) == 271


def test_route_modules_do_not_import_api_module_through_package():
    """路由模块从 app 门面之外的三大共享源取名字；不得绕道 api 包到 bidcraft。

    架构收尾后共享名字的规范来源为 ``..schemas``（请求模型）、``..shared``
    （helper/常量/重导出）与兼容门面 ``..app``。相对层级 level==2 的 import
    只能落在这三处；``..export`` / ``..models`` 会在 shim 后解析到 non-package
    的 bidcraft.api，必须写成 from ...xxx。
    """

    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "bidcraft" / "api" / "routes"
    allowed = {"app", "schemas", "shared"}
    for path in root.glob("*.py"):
        if path.name == "__init__.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 2:
                assert node.module in allowed, (path.name, node.module, node.lineno)


def test_route_modules_do_not_import_app_facade():
    """架构收尾锁定：路由直接从 schemas/shared 取名字，不再反向依赖 app 门面。

    app 门面只用于兼容 ``from bidcraft.api import ...`` 与 monkeypatch；路由若
    重新 ``from ..app import`` 会让依赖方向倒退回双向耦合，这里显式挡住。
    """

    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "bidcraft" / "api" / "routes"
    for path in root.glob("*.py"):
        if path.name == "__init__.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 2:
                assert node.module != "app", (path.name, node.lineno)


def test_request_models_live_in_schemas_not_app():
    """71 个请求模型定义在 schemas.py；app.py 只剩门面与 create_app。"""

    import ast
    from pathlib import Path

    api_dir = Path(__file__).resolve().parent.parent / "bidcraft" / "api"
    app_tree = ast.parse((api_dir / "app.py").read_text())
    facade_models = [
        node.name
        for node in app_tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(b, ast.Name) and b.id == "BaseModel" for b in node.bases
        )
    ]
    assert facade_models == [], "请求模型不得定义在 app.py 门面中"

    schema_tree = ast.parse((api_dir / "schemas.py").read_text())
    schema_models = [
        node.name
        for node in schema_tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(b, ast.Name) and b.id == "BaseModel" for b in node.bases
        )
    ]
    # v0.32 Phase 1 新增 3 个章节规划请求模型，Phase 2 新增 2 个知识资产请求模型。
    assert len(schema_models) == 93, f"schemas.py 应含 93 个请求模型，实际 {len(schema_models)}"


def test_monkeypatch_reaches_route_call_site(monkeypatch):
    from bidcraft import api

    sentinel = object()
    monkeypatch.setattr(api, "_run_kb_reindex_job", sentinel)
    assert sys.modules["bidcraft.api.app"]._run_kb_reindex_job is sentinel
