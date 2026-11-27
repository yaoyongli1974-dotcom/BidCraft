from __future__ import annotations

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
