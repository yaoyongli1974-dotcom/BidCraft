"""第三轮审计：多 worker 部署的反代层限流示例必须存在且被文档引用。"""

from __future__ import annotations

from pathlib import Path


def test_nginx_example_covers_login_register_and_process_local_note():
    root = Path(__file__).resolve().parents[1]
    snippet = (root / "deploy" / "nginx.conf.example").read_text(encoding="utf-8")

    assert "limit_req_zone" in snippet
    assert "zone=bidcraft_login_ip" in snippet
    assert "zone=bidcraft_register_ip" in snippet
    assert "location = /auth/login" in snippet
    assert "limit_req zone=bidcraft_login_ip" in snippet
    assert "location = /auth/register" in snippet
    assert "limit_req zone=bidcraft_register_ip" in snippet
    # 示例必须解释为何应用内进程级计数器不可替代反代层限流。
    assert "每进程内存" in snippet

    readme = (root / "deploy" / "README.md").read_text(encoding="utf-8")
    assert "nginx.conf.example" in readme
