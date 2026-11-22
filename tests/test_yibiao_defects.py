"""对标易标第一批：3 个确定性缺陷回归。

- PDF 导出分支漏传 template（与 DOCX 分支排版不一致）。
- 章节 needs 字段在模型生成路径被丢弃（只在确定性降级路径生效）。
- 生成检索把个人库 id 塞进排他 kb_ids，导致全局企业知识库被排除。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")


# ---------------------------------------------------------------- needs 注入


def test_chapter_messages_injects_needs():
    """needs 字段应作为「本章必须引用的事实字段」注入 user prompt。"""
    from bidcraft.generation.prompts import chapter_messages

    msgs = chapter_messages(
        project_name="某项目",
        heading="技术方案",
        intent="",
        target_words=600,
        ledger_text="台账",
        outline_text="大纲",
        previous_summary="",
        package_title="弱电",
        needs=["project.name", "materials.交换机", "parameters.带宽"],
    )
    text = msgs[1]["content"]
    assert "本章必须引用的事实字段" in text
    assert "project.name" in text
    assert "materials.交换机" in text
    assert "parameters.带宽" in text
    # 位置：needs 段在台账之前
    assert text.index("本章必须引用的事实字段") < text.index("台账")


def test_chapter_messages_needs_is_optional():
    """needs 缺省/空时行为与旧版一致，保持向后兼容。"""
    from bidcraft.generation.prompts import chapter_messages

    base = {
        "project_name": "某项目",
        "heading": "技术方案",
        "intent": "",
        "target_words": 600,
        "ledger_text": "台账",
        "outline_text": "大纲",
        "previous_summary": "",
        "package_title": "弱电",
    }
    without = chapter_messages(**base)
    empty = chapter_messages(**base, needs=[])
    assert without[1]["content"] == empty[1]["content"]
    assert "本章必须引用的事实字段" not in without[1]["content"]


def test_write_chapter_passes_needs_to_model_prompt(db):
    """write_chapter 模型路径把 node['needs'] 传给 chapter_messages。"""
    from bidcraft.generation import writer

    captured: dict = {}

    class FakeClient:
        @property
        def available(self):
            return True

        def complete(self, messages, **kwargs):
            captured["messages"] = messages
            return "正文本。"

    node = {
        "heading": "技术方案",
        "intent": "",
        "target_words": 100,
        "needs": ["project.name", "parameters.带宽"],
    }
    ledger = _ledger_with_project()
    from bidcraft.rules.loader import Package

    package = Package(name="test", title="测试包")
    writer.write_chapter(
        FakeClient(),
        ledger,
        package,
        node,
        [node],
        "",
    )
    text = captured["messages"][1]["content"]
    assert "parameters.带宽" in text


def _ledger_with_project():
    from bidcraft.schemas import FactItem, FactLedger

    return FactLedger(
        package="test",
        facts=[FactItem(key="project.name", value="测试项目", status="confirmed")],
    )


# ------------------------------------------------------------ PDF 模板传递


def test_pdf_export_uses_project_template(db):
    """PDF 分支必须先按项目模板生成 DOCX，再转 PDF（与 DOCX 分支一致）。"""
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app
    from bidcraft.api.routes import projects as projects_route

    client = TestClient(create_app())
    assert client.post("/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}).status_code == 200
    project_id = client.post(
        "/projects", json={"name": "PDF 模板测试", "package": "weakcurrent-cabling"}
    ).json()["id"]

    outline = client.post(f"/projects/{project_id}/outline", json={"mode": "package"})
    node_id = outline.json()["chapters"][0]["id"]
    client.put(
        f"/projects/{project_id}/chapters/{node_id}",
        json={"body": "第一章正文，带宽不低于1000Mbps。"},
    )
    from helpers import confirm_project_chapters

    confirm_project_chapters(project_id)

    # 设定 google 风格模板（body_size 16），便于验证 template 被传入
    client.put(
        f"/projects/{project_id}/export-template",
        json={"id": "gov"},
    )

    # 拦截 PDF 转换（无需 LibreOffice），并捕获 DOCX 生成时的 template 参数。
    captured: dict = {}
    real_export_docx = projects_route.export_docx

    def spy_export_docx(*args, **kwargs):
        captured["template"] = kwargs.get("template")
        return real_export_docx(*args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(projects_route, "export_docx", spy_export_docx)

    def fake_export_pdf(docx_path, *args, **kwargs):
        # 直接复用已生成的 DOCX 路径（Path 对象），跳过 LibreOffice 转换。
        captured["pdf_docx_path"] = docx_path
        return docx_path

    # PDF 分支在函数内部 import，需替换模块全局
    import bidcraft.export.pdf as pdf_mod

    monkeypatch.setattr(pdf_mod, "export_pdf", fake_export_pdf)
    try:
        resp = client.post(f"/projects/{project_id}/export", json={"format": "pdf"})
    finally:
        monkeypatch.undo()

    assert resp.status_code == 200, resp.text
    tmpl = captured["template"]
    assert tmpl is not None, "PDF 分支必须传 template"
    assert tmpl.get("body_font") == "仿宋_GB2312", tmpl


# ------------------------------------------------------------ 知识库联合检索


def test_search_extra_kb_ids_includes_global_and_personal(db):
    """extra_kb_ids 追加个人库，同时全局库仍参与检索（不排他）。"""
    from bidcraft import knowledge, user_docs
    from bidcraft.db import session_scope
    from bidcraft.models import KnowledgeBase

    with session_scope() as sess:
        # 一个全局启用库 + 一个个人库
        global_kb = KnowledgeBase(
            name="企业全局库", kind="local", provider="local",
            location=str(__import__("pathlib").Path(__file__).parent / "fixtures" / "regression"),
            enabled=True, is_default=True, options={},
        )
        sess.add(global_kb)
        sess.flush()
        personal = user_docs.personal_kb(sess, "user-scope-test")
        from bidcraft.knowledge import index_local
        index_local(sess, global_kb)

        # 旧排他语义：只给个人库 id → 全局库被排除
        exclusive = knowledge.search(
            sess, "投标人", kb_ids=[personal.id], include_personal=True
        )
        assert all(h.kb != global_kb.name for h in exclusive)

        # 追加语义：全局库 + 个人库
        combined = knowledge.search(
            sess, "投标人", extra_kb_ids=[personal.id]
        )
        # 至少全局库参与（个人库此时无内容，但全局库应有命中）
        assert any(h.kb == global_kb.name for h in combined), combined


def test_generation_provider_keeps_global_kb(db, monkeypatch):
    """_knowledge_provider 用追加语义，不再把全局库排除。"""
    from bidcraft import knowledge
    from bidcraft.services import job_support

    captured: dict = {}

    def fake_search(db_, query, *, limit=4, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(knowledge, "search", fake_search)
    from bidcraft.db import session_scope

    with session_scope() as session:
        provide = job_support._knowledge_provider(session, user_id="u1")
        provide("测试查询")

    assert "kb_ids" not in captured, "不得再使用排他 kb_ids"
    assert captured.get("extra_kb_ids"), "必须追加当前用户个人库"
