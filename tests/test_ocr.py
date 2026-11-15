from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter
from sqlalchemy import select

from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.models import SourceFile
from bidcraft.parsing import ocr
from bidcraft.parsing.loader import load_document
from bidcraft.pipeline import add_source, create_project, parse_project


def _blank_pdf(path: Path, pages: int = 2) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _probe(**overrides) -> ocr.OcrProbe:
    values = {
        "available": True,
        "tesseract": "/usr/bin/tesseract",
        "pdftoppm": "/usr/bin/pdftoppm",
        "languages": ("chi_sim", "eng"),
        "requested": "chi_sim+eng",
    }
    values.update(overrides)
    return ocr.OcrProbe(**values)


def test_probe_missing_tesseract(monkeypatch):
    monkeypatch.setattr(
        ocr.shutil,
        "which",
        lambda command: None if command == settings.ocr_tesseract_cmd else "/usr/bin/pdftoppm",
    )
    result = ocr.probe_ocr()
    assert result.available is False
    assert "tesseract" in (result.reason or "")


def test_probe_missing_pdftoppm(monkeypatch):
    monkeypatch.setattr(
        ocr.shutil,
        "which",
        lambda command: None if command == settings.ocr_pdftoppm_cmd else "/usr/bin/tesseract",
    )
    result = ocr.probe_ocr()
    assert result.available is False
    assert "pdftoppm" in (result.reason or "")


def test_probe_falls_back_to_eng(monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(settings, "ocr_languages", "chi_sim+eng")
    monkeypatch.setattr(ocr.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        ocr,
        "_list_languages",
        lambda tesseract, timeout: ["eng", "osd"],
    )
    result = ocr.probe_ocr()
    assert result.available is True
    assert result.languages == ("eng",)
    assert "chi_sim" in (result.warning or "")


def test_probe_rejects_unknown_languages(monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(settings, "ocr_languages", "chi_sim+eng")
    monkeypatch.setattr(ocr.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(ocr, "_list_languages", lambda tesseract, timeout: ["osd"])
    result = ocr.probe_ocr()
    assert result.available is False
    assert "chi_sim" in (result.reason or "")


def test_probe_honours_disable_switch(monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", False)
    result = ocr.probe_ocr()
    assert result.available is False
    assert "禁用" in (result.reason or "")


def test_ocr_pdf_selected_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_max_pages", 100)
    monkeypatch.setattr(ocr, "probe_ocr", lambda: _probe())
    rendered: list[int] = []

    def fake_render(path, page, prefix, probe, timeout):
        rendered.append(page)
        return tmp_path / f"page-{page}.png"

    monkeypatch.setattr(ocr, "_render_page", fake_render)
    monkeypatch.setattr(
        ocr,
        "_ocr_image",
        lambda image, probe, timeout: f"第 {image.stem} 页文字",
    )

    result = ocr.ocr_pdf(tmp_path / "scan.pdf", [3, 1, 3])

    assert rendered == [1, 3]
    assert result.page_numbers == [1, 3]
    assert result.pages == ["第 page-1 页文字", "第 page-3 页文字"]
    assert result.warnings == []


def test_ocr_pdf_caps_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_max_pages", 1)
    monkeypatch.setattr(ocr, "probe_ocr", lambda: _probe())
    monkeypatch.setattr(
        ocr,
        "_render_page",
        lambda path, page, prefix, probe, timeout: tmp_path / f"page-{page}.png",
    )
    monkeypatch.setattr(ocr, "_ocr_image", lambda image, probe, timeout: "文字")

    result = ocr.ocr_pdf(tmp_path / "scan.pdf", [1, 2])

    assert result.page_numbers == [1]
    assert any("仅识别前 1 页" in item for item in result.warnings)


def test_ocr_pdf_continues_after_page_error(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_max_pages", 100)
    monkeypatch.setattr(ocr, "probe_ocr", lambda: _probe())
    monkeypatch.setattr(
        ocr,
        "_render_page",
        lambda path, page, prefix, probe, timeout: tmp_path / f"page-{page}.png",
    )

    def fake_ocr(image, probe, timeout):
        if image.stem == "page-2":
            raise ocr.OcrError("模拟识别失败")
        return "可用文字"

    monkeypatch.setattr(ocr, "_ocr_image", fake_ocr)

    result = ocr.ocr_pdf(tmp_path / "scan.pdf", [1, 2])

    assert result.pages == ["可用文字", ""]
    assert any("第 2 页 OCR 失败" in item for item in result.warnings)
    assert any("第 2 页未识别出文字" in item for item in result.warnings)


def test_ocr_pdf_all_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_max_pages", 0)
    monkeypatch.setattr(ocr, "probe_ocr", lambda: _probe())
    monkeypatch.setattr(ocr, "_render_all", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        ocr,
        "_collect_images",
        lambda prefix: [(1, tmp_path / "page-1.png"), (2, tmp_path / "page-2.png")],
    )
    monkeypatch.setattr(ocr, "_ocr_image", lambda image, probe, timeout: image.stem)

    result = ocr.ocr_pdf(tmp_path / "scan.pdf")

    assert result.page_numbers == [1, 2]
    assert result.pages == ["page-1", "page-2"]


def test_loader_uses_ocr_for_blank_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(
        ocr,
        "ocr_pdf",
        lambda path, pages: ocr.OcrResult(
            pages=["第一页识别文字", "第二页识别文字"],
            page_numbers=[1, 2],
            languages=("chi_sim", "eng"),
            warnings=[],
        ),
    )
    parsed = load_document(_blank_pdf(tmp_path / "scan.pdf"))
    assert "第一页识别文字" in parsed.text
    assert "第二页识别文字" in parsed.text
    assert len(parsed.pages) == 2
    assert parsed.meta["ocr_engine"] == "pdftoppm+tesseract"
    assert parsed.meta["ocr_pages"] == 2
    assert parsed.meta.get("needs_ocr") is None


def test_loader_records_unavailable_ocr(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(
        ocr,
        "ocr_pdf",
        lambda path, pages: (_ for _ in ()).throw(ocr.OcrUnavailable("缺少 tesseract")),
    )
    parsed = load_document(_blank_pdf(tmp_path / "scan.pdf", pages=1))
    assert parsed.text == ""
    assert parsed.meta["needs_ocr"] is True
    assert "缺少 tesseract" in parsed.meta["ocr_unavailable"]


def test_loader_skips_ocr_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_enabled", False)

    def unexpected(*args, **kwargs):
        raise AssertionError("OCR should not run when disabled")

    monkeypatch.setattr(ocr, "ocr_pdf", unexpected)
    parsed = load_document(_blank_pdf(tmp_path / "scan.pdf", pages=1))
    assert parsed.meta["needs_ocr"] is True
    assert "ocr_attempted" not in parsed.meta


def test_parse_project_accepts_ocr_text(db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(
        ocr,
        "ocr_pdf",
        lambda path, pages: ocr.OcrResult(
            pages=["项目名称：某园区智能化工程\n信息点数量：480 个"],
            page_numbers=[1],
            languages=("chi_sim",),
            warnings=[],
        ),
    )
    with session_scope() as session:
        project = create_project(session, "扫描件项目", "weakcurrent-cabling")
        source = add_source(session, project, _blank_pdf(tmp_path / "scan.pdf", pages=1))
        _, meta = parse_project(session, project.id)
        session.refresh(source)
        saved = session.scalars(
            select(SourceFile).where(SourceFile.project_id == project.id)
        ).one()
    assert any("已对 1 页扫描页执行 OCR" in note for note in meta["notes"])
    assert saved.parse_state == "parsed"
    assert "480" in (saved.text_content or "")

