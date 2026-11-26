from __future__ import annotations

import importlib
import os
import shutil
import subprocess

import pytest

import bidcraft.export.pdf as pdf_module
from bidcraft.export.docx import export_docx
from bidcraft.export.pdf import PdfExportError, export_pdf
from bidcraft.schemas import FactItem, FactLedger

SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")


def test_pdf_timeout_env_override(monkeypatch):
    monkeypatch.setenv("BIDCRAFT_PDF_TIMEOUT", "600")
    try:
        module = importlib.reload(pdf_module)
        assert module.PDF_EXPORT_TIMEOUT == 600
    finally:
        if "BIDCRAFT_PDF_TIMEOUT" in os.environ:
            monkeypatch.delenv("BIDCRAFT_PDF_TIMEOUT", raising=False)
        importlib.reload(pdf_module)


@pytest.mark.skipif(SOFFICE is None, reason="LibreOffice 未安装")
def test_pdf_export_and_toc(tmp_path):
    ledger = FactLedger(
        package="test",
        facts=[FactItem(key="project.name", value="测试项目", status="confirmed")],
    )
    nodes = [
        {"heading": "第一章 项目理解", "body": "这是第一章正文。"},
        {"heading": "第二章 技术方案", "body": "这是第二章正文。"},
    ]
    docx_path = export_docx(tmp_path / "bid.docx", ledger, nodes)
    pdf_path = export_pdf(docx_path)
    assert pdf_path.exists()
    assert pdf_path.read_bytes().startswith(b"%PDF")
    assert pdf_path.stat().st_size > 1000

    if shutil.which("pdftotext"):
        text = subprocess.run(
            ["pdftotext", str(pdf_path), "-"], capture_output=True, text=True, check=False
        ).stdout
        assert "第一章 项目理解" in text
        assert "这是第二章正文" in text
        # UNO path refreshes the TOC; plain conversion would leave it empty.
        assert "第一章 项目理解........" in text or "第一章 项目理解" in text


@pytest.mark.skipif(SOFFICE is None, reason="LibreOffice 未安装")
def test_pdf_missing_docx(tmp_path):
    with pytest.raises(PdfExportError):
        export_pdf(tmp_path / "nope.docx")
