"""图片查重：相同字节、缩放感知哈希、降级路径与候选提示。"""

from __future__ import annotations

import io

import pytest

from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.models import ImageAsset, OutlineNode, Project, Section
from bidcraft.similarity import image as image_engine


def _sample_png() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, 56, 40), fill="black")
    draw.ellipse((40, 44, 88, 88), fill="gray")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _rescaled_png(data: bytes, size: tuple[int, int] = (48, 48)) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        resized = image.resize(size)
        buffer = io.BytesIO()
        resized.save(buffer, format="PNG")
        return buffer.getvalue()


def _other_png() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, 96, 12):
        draw.line((x, 0, x, 96), fill="black", width=4)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_calculate_image_hash_same_and_different():
    data = b"not-really-an-image-but-bytes"
    assert image_engine.calculate_image_hash(data) == image_engine.calculate_image_hash(data)
    assert image_engine.calculate_image_hash(data) != image_engine.calculate_image_hash(b"other")
    assert image_engine.calculate_image_hash("/path/does/not/exist") == ""


def test_perceptual_hash_survives_scaling():
    pytest.importorskip("PIL")
    data = _sample_png()
    scaled = _rescaled_png(data)
    assert image_engine.calculate_image_hash(data) != image_engine.calculate_image_hash(scaled)
    phash_a = image_engine.calculate_perceptual_hash(data)
    phash_b = image_engine.calculate_perceptual_hash(scaled)
    assert phash_a and phash_b
    pairs = image_engine.compare_image_hashes(
        [{"sha256": "a", "phash": phash_a}],
        [{"sha256": "b", "phash": phash_b}],
        threshold=0.9,
    )
    assert len(pairs) == 1
    assert pairs[0]["method"] == "perceptual"
    assert pairs[0]["similarity"] >= 0.9


def test_different_images_not_matched():
    pytest.importorskip("PIL")
    left = _sample_png()
    right = _other_png()
    pairs = image_engine.compare_image_hashes(
        [
            {
                "sha256": image_engine.calculate_image_hash(left),
                "phash": image_engine.calculate_perceptual_hash(left),
            }
        ],
        [
            {
                "sha256": image_engine.calculate_image_hash(right),
                "phash": image_engine.calculate_perceptual_hash(right),
            }
        ],
        threshold=0.9,
    )
    assert pairs == []


def test_perceptual_fallback_only_matches_exact_bytes(monkeypatch):
    data = _sample_png()
    scaled = _rescaled_png(data)
    monkeypatch.setattr(image_engine, "calculate_perceptual_hash", lambda source, **_: "")
    monkeypatch.setattr(image_engine, "perceptual_available", lambda: False)
    images = [
        {
            "id": "a",
            "sha256": image_engine.calculate_image_hash(data),
            "phash": "",
        },
        {
            "id": "b",
            "sha256": image_engine.calculate_image_hash(scaled),
            "phash": "",
        },
    ]
    assert image_engine.compare_image_hashes(images, threshold=0.9) == []
    assert image_engine.calculate_perceptual_hash(data) == ""


def test_find_image_duplicates_from_project_assets(db):
    project_id = ""
    data = _sample_png()
    with session_scope() as session:
        project = Project(name="图片项目")
        session.add(project)
        session.flush()
        project_id = project.id

    image_dir = settings.project_dir(project_id) / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "a.png").write_bytes(data)
    (image_dir / "b.png").write_bytes(data)
    (image_dir / "c.png").write_bytes(_other_png())
    with session_scope() as session:
        for filename in ("a.png", "b.png", "c.png"):
            session.add(
                ImageAsset(
                    project_id=project_id,
                    filename=filename,
                    file_path=str(image_dir / filename),
                    status="ready",
                    label=filename,
                )
            )

    with session_scope() as session:
        result = image_engine.find_image_duplicates(session, project_id, threshold=0.9)
    assert result["stats"]["assets"] == 3
    assert result["stats"]["advisory"] is True
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["kind"] == "image"
    assert hit["similarity"] == 1.0
    assert hit["evidence"]["advisory"] is True
    assert "不直接认定抄袭" in hit["evidence"]["note"]
    assert hit["loc_a"] and hit["loc_b"]


def test_extract_document_images_includes_markdown_references(db):
    project_id = ""
    data = _sample_png()
    with session_scope() as session:
        project = Project(name="正文配图项目")
        session.add(project)
        session.flush()
        project_id = project.id
        node = OutlineNode(project_id=project_id, heading="系统架构", position=1)
        session.add(node)
        session.flush()
        session.add(
            Section(
                node_id=node.id,
                body=f"系统架构如下图：\n\n![架构图](/projects/{project_id}/images/arch.png)",
                status="written",
            )
        )
    image_dir = settings.project_dir(project_id) / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "arch.png").write_bytes(data)

    with session_scope() as session:
        images = image_engine.extract_document_images(session, project_id)
    assert len(images) == 1
    assert images[0]["filename"] == "arch.png"
    assert images[0]["source"].startswith("正文配图")
    assert images[0]["sha256"]
