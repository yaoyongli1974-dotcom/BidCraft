"""Batch 4: 真实文件保真度黄金指标（LibreOffice + poppler 可用时执行）。

基线以 JSON 风格的结构内联在测试里：页数、标题文本顺序、占位符数量。
页数与占位符数量允许小幅容差，文本顺序必须严格一致。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from bidcraft.documents.document_ir import (
    DocumentHeaderFooter,
    DocumentIR,
    DocumentPageLayout,
    DocumentParagraph,
    DocumentSection,
    DocumentTable,
    default_styles,
)
from bidcraft.documents.renderer import render_document
from bidcraft.export.pdf import export_pdf

SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")
PDFINFO = shutil.which("pdfinfo")
PDFTOTEXT = shutil.which("pdftotext")

BASELINE = {
    "pages": 2,
    "page_tolerance": 1,
    "headings_in_order": ["第一章 项目理解", "第二章 技术方案"],
    "placeholder_count": 2,
    "placeholder_tolerance": 1,
}


def _representative_ir() -> DocumentIR:
    return DocumentIR(
        sections=[
            DocumentSection(
                heading="第一章 项目理解",
                paragraphs=[
                    DocumentParagraph("本项目为某园区智能化工程。"),
                    DocumentParagraph("负责人：{{待填:项目经理}}"),
                ],
                tables=[
                    DocumentTable(rows=[["名称", "数量"], ["信息点", "480"]]),
                ],
            ),
            DocumentSection(
                heading="第二章 技术方案",
                paragraphs=[
                    DocumentParagraph("本方案采用 Cat6A 综合布线系统。"),
                    DocumentParagraph("投标报价：{{待填:投标报价}}"),
                ],
                page_break_before=True,
            ),
        ],
        styles=default_styles(),
        page_layout=DocumentPageLayout(),
        header_footer=DocumentHeaderFooter(page_number=True),
        core_properties={"title": "真实件保真度测试", "author": "示例公司"},
    )


def _run(tool: str, *args: str) -> str:
    result = subprocess.run([tool, *args], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"{tool} 执行失败：{result.stderr}"
    return result.stdout


@pytest.mark.skipif(
    SOFFICE is None or PDFINFO is None or PDFTOTEXT is None,
    reason="需要 LibreOffice 与 poppler(pdfinfo/pdftotext)",
)
def test_golden_pdf_fidelity(tmp_path):
    docx_path = render_document(_representative_ir(), tmp_path / "golden.docx")
    pdf_path = export_pdf(docx_path, timeout=120)
    assert pdf_path.read_bytes().startswith(b"%PDF")

    info = _run(PDFINFO, str(pdf_path))
    match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
    assert match is not None, info
    pages = int(match.group(1))
    assert abs(pages - BASELINE["pages"]) <= BASELINE["page_tolerance"]

    text = _run(PDFTOTEXT, str(pdf_path), "-")
    squashed = re.sub(r"\s+", "", text)
    positions = [squashed.find(re.sub(r"\s+", "", heading)) for heading in BASELINE["headings_in_order"]]
    assert all(position >= 0 for position in positions), squashed[:500]
    assert positions == sorted(positions)

    placeholders = len(re.findall(r"\{\{待填:[^}]*\}\}", squashed))
    assert (
        abs(placeholders - BASELINE["placeholder_count"]) <= BASELINE["placeholder_tolerance"]
    ), squashed[:500]

    # 基线本身必须是可解析的 JSON 数据，便于外部工具比对。
    assert json.loads(json.dumps(BASELINE)) == BASELINE
