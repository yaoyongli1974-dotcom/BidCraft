"""后台任务拆分的边界守护。

5 个 runner 与共享 helper 已从 api.py 下沉到 workers/ / services/。
这里锁定两条不变量，防止后续改动把耦合又搬回去：

1. workers / services 不 import ``bidcraft.api``（依赖单向：api → 底层）；
2. api 名字空间仍暴露原名，且就是 workers 里的同一对象——路由闭包与测试
   ``monkeypatch.setattr(api, "_run_kb_reindex_job", ...)`` 都依赖这一点。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RUNNERS = (
    "_run_generation_job",
    "_run_image_batch_job",
    "_run_batch_check_job",
    "_run_kb_reindex_job",
    "_run_batch_export_job",
)
HELPERS = (
    "_fail_job",
    "_cancel_stale_jobs",
    "_active_kb_reindex",
    "_recover_interrupted_jobs",
    "_report_payload",
    "_generation_client",
    "_knowledge_provider",
    "_job_refund",
)


def _package_dir(name: str) -> Path:
    import bidcraft

    return Path(bidcraft.__file__).parent / name


def test_workers_and_services_do_not_import_api():
    for package in ("workers", "services"):
        for path in sorted(_package_dir(package).rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module not in {"bidcraft.api", "api"}, (
                        f"{path} 反向依赖了 api 层: {node.module}"
                    )
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name not in {"bidcraft.api"}, (
                            f"{path} 反向依赖了 api 层: {alias.name}"
                        )


def test_api_reexports_the_same_runner_objects():
    from bidcraft import api, workers

    for name in RUNNERS:
        assert hasattr(api, name), f"api 缺少 runner 导出: {name}"
        assert getattr(api, name) is getattr(workers, name)


def test_api_reexports_shared_helpers():
    import bidcraft.services.job_support as support
    from bidcraft import api

    for name in HELPERS:
        assert hasattr(api, name), f"api 缺少 helper 导出: {name}"
        assert getattr(api, name) is getattr(support, name)


@pytest.mark.parametrize("name", RUNNERS)
def test_runner_defined_only_once(name):
    from bidcraft import api, workers

    assert name not in {
        node.name
        for node in ast.walk(ast.parse(Path(api.__file__).read_text()))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert name in workers.__all__
