from __future__ import annotations

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
        "请生成系统流程图，须显示单位公章、签字、签署日期和扫描件。"
    )
    assert "公章" not in prompt
    assert "签字" not in prompt
    assert "扫描件" not in prompt
    assert "示意图" in prompt


def test_svg_generation_retries_with_simple_schematic_prompt(db, monkeypatch):
    with session_scope() as session:
        project = Project(name="SVG 重试测试", status="intake")
        session.add(project)
        session.flush()
        project_id = project.id

    prompts: list[str] = []

    class FakeClient:
        def complete(self, messages, **kwargs):
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
    assert "公章" not in prompts[1]
    assert "示意图" in prompts[1]
