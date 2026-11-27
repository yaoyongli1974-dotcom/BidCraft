from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from bidcraft import images
from bidcraft.db import session_scope
from bidcraft.models import Project


def test_sanitize_svg_accepts_self_contained_vector():
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    result = images.sanitize_svg(svg)
    assert 'viewBox="0 0 800 450"' in result
    assert result.endswith("</svg>")


@pytest.mark.parametrize(
    "svg",
    [
        '<svg onload="alert(1)"><rect/></svg>',
        '<svg><script>alert(1)</script></svg>',
        '<svg><image href="https://example.com/x.png"/></svg>',
        '<svg><rect/></svg><p>extra</p>',
    ],
)
def test_sanitize_svg_rejects_unsafe_or_non_self_contained(svg):
    with pytest.raises(images.ImageError):
        images.sanitize_svg(svg)


def test_placeholder_png_is_real_png_and_media_types():
    payload = images.placeholder_png_bytes()
    assert payload.startswith(images.PNG_SIGNATURE)
    assert images.media_type_for("figure.svg") == "image/svg+xml"
    assert images.media_type_for("figure.png") == "image/png"


def test_generate_image_creates_placeholder_when_no_model_configured(db):
    with session_scope() as session:
        project = Project(name="配图占位测试", status="intake")
        session.add(project)
        session.flush()
        project_id = project.id

    with session_scope() as session:
        project = session.get(Project, project_id)
        asset = images.generate_image(session, project, label="系统架构", node_id="")
        assert asset.status == "ready"
        assert asset.provider == "placeholder"
        assert asset.options["kind"] == "placeholder"
        assert asset.file_path
        from pathlib import Path

        assert Path(asset.file_path).read_bytes().startswith(images.PNG_SIGNATURE)


def test_svg_safe_prompt_removes_evidence_like_requests():
    prompt = images._svg_safe_prompt(
        "请生成系统流程图，须显示单位公章、签字、签署日期和扫描件、证件、票据、真实性凭证材料。"
    )
    for term in (
        "公章",
        "签字",
        "扫描件",
        "证件",
        "票据",
        "凭证",
        "真实性",
    ):
        assert term not in prompt
    assert "示意图" in prompt


def test_svg_safe_prompt_removes_seal_and_license_requests():
    prompt = images._svg_safe_prompt(
        "画面内容：加盖投标人公章及签字，显示营业执照和资质证书；图名：系统拓扑图。"
    )
    for term in ("加盖", "公章", "签字", "营业执照", "资质证书"):
        assert term not in prompt
    assert "示意标注" in prompt


def test_svg_system_prompt_avoids_evidence_terms():
    for term in ("证件", "票据", "凭证", "真实性", "公章", "签字"):
        assert term not in images.SVG_SYSTEM_PROMPT
    assert "抽象工程结构" in images.SVG_SYSTEM_PROMPT


def test_svg_generation_retries_with_simple_schematic_prompt(db, monkeypatch):
    with session_scope() as session:
        project = Project(name="SVG 重试测试", status="intake")
        session.add(project)
        session.flush()
        project_id = project.id

    prompts: list[str] = []
    systems: list[str] = []

    class FakeClient:
        def complete(self, messages, **kwargs):
            systems.append(messages[0]["content"])
            prompts.append(messages[-1]["content"])
            if len(prompts) == 1:
                return ""
            return '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'

    endpoint = SimpleNamespace(model_name="fake-chat", model_configured=True)
    monkeypatch.setattr(images, "_chat_svg_client", lambda db: (FakeClient(), endpoint))
    monkeypatch.setattr(
        images,
        "_section_context",
        lambda db, project_id, node_id, heading: ("章节", "公章 签字 扫描件"),
    )

    with session_scope() as session:
        project = session.get(Project, project_id)
        asset = images.generate_svg_figure(session, project, label="系统流程图")

    assert asset.provider == "llm-svg"
    assert len(prompts) == 2
    for text in (*prompts, *systems):
        for term in ("公章", "签字", "扫描件", "证件", "票据", "凭证", "真实性"):
            assert term not in text
    assert "示意图" in prompts[1]


def test_svg_model_prompt_is_structured_and_free_of_evidence_terms():
    prompt = images._svg_model_prompt(
        "前端设备选型配置表及制造商盖章的产品规格书首页扫描件",
        "技术方案",
        "页面含截图、签字、资质证书",
        "加盖公章",
    )
    assert "节点：" in prompt
    assert "方框" in prompt
    assert "箭头" in prompt
    for term in ("公章", "盖章", "扫描件", "截图", "签字", "资质证书", "证件", "票据", "凭证"):
        assert term not in prompt


def test_svg_generation_uses_structured_prompt(db, monkeypatch):
    with session_scope() as session:
        project = Project(name="SVG 结构化提示词测试", status="intake")
        session.add(project)
        session.flush()
        project_id = project.id

    prompts: list[str] = []

    class StructuredClient:
        def complete(self, messages, **kwargs):
            prompts.append(messages[-1]["content"])
            return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450"><rect width="10" height="10"/></svg>'

    endpoint = SimpleNamespace(model_name="fake-chat", model_configured=True)
    monkeypatch.setattr(images, "_chat_svg_client", lambda db: (StructuredClient(), endpoint))
    monkeypatch.setattr(
        images,
        "_section_context",
        lambda db, project_id, node_id, heading: ("技术方案", "制造商盖章的产品规格书扫描件"),
    )

    with session_scope() as session:
        project = session.get(Project, project_id)
        asset = images.generate_svg_figure(
            session,
            project,
            label="前端设备配置图",
            prompt="加盖投标人公章及签字",
        )

    assert asset.provider == "llm-svg"
    assert len(prompts) == 1
    assert "节点：" in prompts[0]
    assert "盖章" not in prompts[0] and "公章" not in prompts[0]


def test_svg_generation_third_attempt_can_recover(db, monkeypatch):
    with session_scope() as session:
        project = Project(name="SVG 三次重试测试", status="intake")
        session.add(project)
        session.flush()
        project_id = project.id

    prompts: list[str] = []

    class ThirdTryClient:
        def complete(self, messages, **kwargs):
            prompts.append(messages[-1]["content"])
            if len(prompts) < 3:
                return ""
            return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450"><rect width="10" height="10"/></svg>'

    endpoint = SimpleNamespace(model_name="fake-chat", model_configured=True)
    monkeypatch.setattr(images, "_chat_svg_client", lambda db: (ThirdTryClient(), endpoint))
    monkeypatch.setattr(
        images,
        "_section_context",
        lambda db, project_id, node_id, heading: ("技术方案", "普通工程上下文"),
    )

    with session_scope() as session:
        project = session.get(Project, project_id)
        asset = images.generate_svg_figure(session, project, label="系统拓扑图")

    assert asset.provider == "llm-svg"
    assert len(prompts) == 3
    assert "工程结构示意图" in prompts[2]


def test_svg_generation_falls_back_to_local_chart_when_model_returns_empty(db, monkeypatch):
    with session_scope() as session:
        project = Project(name="SVG 本地示意图测试", status="intake")
        session.add(project)
        session.flush()
        project_id = project.id

    class EmptyClient:
        def complete(self, messages, **kwargs):
            return ""

    endpoint = SimpleNamespace(model_name="fake-chat", model_configured=True)
    monkeypatch.setattr(images, "_chat_svg_client", lambda db: (EmptyClient(), endpoint))
    monkeypatch.setattr(
        images,
        "_section_context",
        lambda db, project_id, node_id, heading: ("章节", "普通工程上下文"),
    )

    with session_scope() as session:
        project = session.get(Project, project_id)
        asset = images.generate_svg_figure(session, project, label="系统拓扑图", heading="技术方案")
        path = asset.file_path
        provider = asset.provider
        options = dict(asset.options)

    from pathlib import Path

    assert provider == "local-schematic"
    assert options["kind"] == "svg"
    assert Path(path).read_text(encoding="utf-8").startswith("<svg")


def test_local_schematic_svg_varies_by_title_and_context():
    first = images.local_schematic_svg("系统拓扑图", "技术方案", "交换机 服务器 存储")
    second = images.local_schematic_svg("存储架构图", "技术方案", "磁盘阵列 备份 光纤")
    third = images.local_schematic_svg("进度网络图", "实施方案", "六个阶段 控制节点")
    assert first != second != third
    layouts = {
        re.search(r'data-layout="([^"]+)"', svg).group(1)
        for svg in (first, second, third)
    }
    assert len(layouts) >= 2
    for svg in (first, second, third):
        assert "交换机" not in svg or "示意" in svg
        assert not re.search(r"<script|onclick=", svg, re.IGNORECASE)


def test_svg_rasterizes_to_real_png_for_docx():
    svg = images.local_schematic_svg("系统拓扑图", "技术方案", "交换机 服务器 存储")
    png = images.svg_to_png_bytes(svg)
    assert png.startswith(images.PNG_SIGNATURE)
    assert png != images.placeholder_png_bytes()
    assert len(png) > 1000
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    assert (width, height) == (800, 450)


def test_docx_url_resolver_rasterizes_svg_instead_of_grey_placeholder(db):
    from pathlib import Path

    with session_scope() as session:
        project = Project(name="SVG DOCX 测试", status="intake")
        session.add(project)
        session.flush()
        project_id = project.id

    svg_path = images.image_dir(project_id) / "figure.svg"
    svg_path.write_text(
        images.local_schematic_svg("点位分布图", "项目概述", "停车场 园区 摄像机"),
        encoding="utf-8",
    )
    resolved = images.url_resolver(project_id)(f"/projects/{project_id}/images/figure.svg")
    assert resolved is not None
    assert resolved.suffix.lower() == ".png"
    data = Path(resolved).read_bytes()
    assert data.startswith(images.PNG_SIGNATURE)
    assert data != images.placeholder_png_bytes()
