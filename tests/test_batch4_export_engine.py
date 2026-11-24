"""Batch 4: 专业引擎接线、完整性校验、模板与确定性导出。"""

from __future__ import annotations

import shutil
import subprocess
import time
from datetime import date
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn

from bidcraft.documents.document_ir import (
    DocumentCover,
    DocumentHeaderFooter,
    DocumentTOC,
    from_markdown_sections,
)
from bidcraft.documents.renderer import render_document
from bidcraft.export import templates
from bidcraft.export.docx import export_docx
from bidcraft.schemas import FactItem, FactLedger

EAST_ASIA = qn("w:eastAsia")


def _ledger() -> FactLedger:
    return FactLedger(
        project_id="p1",
        facts=[
            FactItem(key="project.name", value="测试项目", status="confirmed"),
            FactItem(key="project.number", value="ZB-2026-001", status="confirmed"),
            FactItem(key="project.buyer", value="某管委会", status="confirmed"),
            FactItem(key="bidder.name", value="示例公司", status="confirmed"),
        ],
    )


def _nodes() -> list[dict]:
    return [
        {
            "heading": "第一章 项目理解",
            "body": (
                "## 项目背景\n\n"
                "见 [招标公告](https://example.com/tender) 与 {{待填:联系人}}。\n\n"
                "| 名称 | 数量 |\n| --- | --- |\n| 信息点 | 480 |\n\n"
                "#### 深层小节\n\n深层正文。"
            ),
        },
        {"heading": "第二章 技术方案", "body": "本方案采用 Cat6A 综合布线。"},
    ]


def _base_template(path: Path) -> Path:
    document = Document()
    document.styles["Normal"].font.size = None
    document.sections[0].header.paragraphs[0].text = "继承页眉：示例项目"
    document.sections[0].footer.paragraphs[0].text = "模板页脚"
    document.core_properties.title = "模板标题"
    document.save(str(path))
    return path


def _east_asian_font(style) -> str | None:
    properties = style.element.find(qn("w:rPr"))
    fonts = properties.find(qn("w:rFonts")) if properties is not None else None
    return fonts.get(EAST_ASIA) if fonts is not None else None


def test_professional_export_with_base_template(tmp_path):
    base = _base_template(tmp_path / "base.docx")
    path = export_docx(
        tmp_path / "professional.docx",
        _ledger(),
        _nodes(),
        professional=True,
        base_template=base,
        template={"id": "standard"},
    )

    document = Document(str(path))
    # 2 节（基础模板 1 节 + IR 第 2 节），页眉来自模板。
    assert len(document.sections) == 2
    assert document.sections[0].header.paragraphs[0].text == "继承页眉：示例项目"
    assert "NUMPAGES" in document.sections[0].footer._element.xml
    # 表头跨页重复、深层标题、外部超链接均由专业引擎生成。
    assert "w:tblHeader" in document.tables[0].rows[0]._tr.xml
    styles = [paragraph.style.name for paragraph in document.paragraphs]
    assert "Heading 4" in styles
    assert "w:hyperlink" in document.element.body.xml
    links = [
        relation
        for relation in document.part.rels.values()
        if relation.reltype.endswith("/hyperlink") and getattr(relation, "is_external", False)
    ]
    assert any(relation.target_ref == "https://example.com/tender" for relation in links)
    assert document.core_properties.title == "测试项目"


def test_professional_export_without_template_has_cover_toc_and_table(tmp_path):
    path = export_docx(
        tmp_path / "plain.docx",
        _ledger(),
        _nodes(),
        professional=True,
        as_of=date(2026, 1, 2),
    )
    document = Document(str(path))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "投 标 文 件" in text
    assert "目  录" in text
    assert 'TOC \\o "1-4"' in document.element.body.xml
    assert "2026 年 01 月 02 日" in text
    assert document.tables[0].cell(1, 1).text == "480"


def test_export_is_byte_identical_with_fixed_as_of(tmp_path):
    first = export_docx(
        tmp_path / "a.docx", _ledger(), _nodes(), as_of=date(2026, 3, 4)
    )
    second = export_docx(
        tmp_path / "b.docx", _ledger(), _nodes(), as_of=date(2026, 3, 4)
    )
    assert first.read_bytes() == second.read_bytes()

    professional_a = export_docx(
        tmp_path / "pa.docx",
        _ledger(),
        _nodes(),
        professional=True,
        as_of=date(2026, 3, 4),
    )
    professional_b = export_docx(
        tmp_path / "pb.docx",
        _ledger(),
        _nodes(),
        professional=True,
        as_of=date(2026, 3, 4),
    )
    assert professional_a.read_bytes() == professional_b.read_bytes()


def test_export_guards_reject_oversized_documents(tmp_path):
    with pytest.raises(ValueError, match="章节数超过"):
        export_docx(
            tmp_path / "many.docx", _ledger(), _nodes() * 2, max_nodes=1
        )
    image_nodes = [
        {"heading": "章节", "body": "![图](/projects/p/images/a.png)"},
    ]
    with pytest.raises(ValueError, match="图片数超过"):
        export_docx(
            tmp_path / "images.docx", _ledger(), image_nodes, max_images=0
        )


def test_markdown_links_become_hyperlinks_in_both_engines(tmp_path):
    nodes = [{"heading": "章节", "body": "参考 [规范](https://example.com/spec)。"}]
    ir = from_markdown_sections(nodes)
    assert ir.sections[0].paragraphs[0].links == [
        {"text": "规范", "url": "https://example.com/spec"}
    ]

    rendered = Document(str(render_document(ir, tmp_path / "ir.docx")))
    assert "w:hyperlink" in rendered.element.body.xml

    simple = Document(
        str(export_docx(tmp_path / "simple.docx", _ledger(), nodes))
    )
    assert "w:hyperlink" in simple.element.body.xml
    assert any(
        relation.target_ref == "https://example.com/spec"
        for relation in simple.part.rels.values()
        if relation.reltype.endswith("/hyperlink")
    )


def test_ir_cover_toc_and_deep_headings(tmp_path):
    cover = DocumentCover(title="测试项目", subtitle="投 标 文 件")
    toc = DocumentTOC()
    ir = from_markdown_sections(
        _nodes(),
        cover=cover,
        toc=toc,
        header_footer=DocumentHeaderFooter(page_number=True),
        core_properties={"title": "测试项目"},
    )
    path = render_document(ir, tmp_path / "cover.docx")
    document = Document(str(path))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "投 标 文 件" in text
    assert 'TOC \\o "1-4"' in document.element.body.xml
    assert "Heading 4" in [p.style.name for p in document.paragraphs]
    assert "NUMPAGES" in document.sections[0].footer._element.xml

    # 封面用段前间距而不是连续空段落。
    empty_runs = 0
    for paragraph in document.paragraphs:
        if not paragraph.text.strip():
            empty_runs += 1
            assert empty_runs < 2
        else:
            empty_runs = 0


def test_large_document_has_no_consecutive_empty_paragraphs_and_is_fast(tmp_path):
    nodes = [
        {"heading": f"第{index}章 技术方案", "body": f"这是第 {index} 章正文内容。"}
        for index in range(1, 501)
    ]
    started = time.monotonic()
    path = export_docx(tmp_path / "large.docx", _ledger(), nodes)
    elapsed = time.monotonic() - started

    document = Document(str(path))
    assert len(document.paragraphs) > 500
    empty_runs = 0
    for paragraph in document.paragraphs:
        if not paragraph.text.strip():
            empty_runs += 1
            assert empty_runs < 2, f"出现连续 {empty_runs} 个空段落"
        else:
            empty_runs = 0
    assert elapsed < 20, f"500 章导出耗时 {elapsed:.1f}s"


@pytest.mark.skipif(shutil.which("fc-match") is None, reason="fontconfig 未安装")
def test_preset_fonts_resolve_to_cjk_and_have_substitutions():
    for preset_item in templates.preset_list():
        fonts = templates.required_fonts(preset_item)
        assert fonts
        for font in fonts:
            assert font in templates.FONT_SUBSTITUTIONS
            result = subprocess.run(
                ["fc-match", "-f", "%{family}", font],
                capture_output=True,
                text=True,
                check=False,
            )
            family = result.stdout
            assert any(
                marker in family
                for marker in ("CJK", "Han", "SC", "Song", "Hei", "Fang", "Kai", "宋", "黑")
            ), f"{font} 被替换为 {family!r}，可能不是中文字体"


def test_commercial_docx_honors_compact_preset(tmp_path):
    from bidcraft.export.commercial_docx import export_commercial_docx

    payload = {
        "project": {"name": "商务标项目", "number": "ZB-1", "buyer": "管委会"},
        "quote": {"name": "定稿报价", "reviewed_by": "李总", "reviewed_at": "2026-01-01"},
        "sections": {
            "bid_letter": {
                "kind": "fields",
                "fields": [{"key": "bidder", "value": "示例公司"}],
            }
        },
        "validation": {"ok": True, "errors": []},
    }
    path = export_commercial_docx(
        tmp_path / "commercial.docx", payload, template={"id": "compact"}
    )
    document = Document(str(path))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "商 务 标" not in text  # compact 跳过封面
    assert "目  录" in text
    assert document.styles["Normal"].font.size.pt == 10.5
    assert _east_asian_font(document.styles["Normal"]) == "宋体"


def test_validator_blocks_corrupt_package(tmp_path):
    from bidcraft.documents.validator import validate_docx_package

    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a docx")
    report = validate_docx_package(broken)
    assert report["valid"] is False
    assert any(item["severity"] == "error" for item in report["issues"])
