from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import settings

#: 需要按 ZIP 目录声明值做解压体积检查的 OOXML 后缀。
OOXML_SUFFIXES = frozenset({".docx", ".xlsx", ".xlsm"})


class OversizedUploadError(ValueError):
    """解压后（或原始文本）体积超过安全上限。

    继承 ``ValueError`` 以便既有的 HTTP 错误映射继续把它转成 400；用独立
    子类让解析阶段可以只重抛这一类错误，其它解析失败仍是"该文件 failed"
    的旧行为。
    """


def _guard_ooxml_size(path: Path) -> None:
    """在 OOXML 解析前按 ZIP 目录声明的未压缩体积拦截解压炸弹。

    只读取 central directory 的 ``file_size``，不解压任何条目：几十 MB 的
    压缩包可以声明数 GB 的解压体积，真正解开才会 OOM。损坏的 ZIP 仍交给
    对应的读取器按原逻辑报错。
    """
    limit = settings.max_uncompressed_upload_bytes
    try:
        with zipfile.ZipFile(path) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except (OSError, zipfile.BadZipFile, ValueError):
        return
    if total > limit:
        raise OversizedUploadError(
            f"文件解压后体积约 {total / 1024 / 1024:.1f}MB，超过安全上限 "
            f"{limit / 1024 / 1024:.1f}MB；请拆分文档或调高 "
            "BIDCRAFT_MAX_UNCOMPRESSED_UPLOAD_BYTES 后重试"
        )


@dataclass
class Table:
    page: int
    rows: list[list[str]]

    def header(self) -> list[str]:
        return [cell.strip() for cell in (self.rows[0] if self.rows else [])]

    def as_dicts(self) -> list[dict[str, str]]:
        if len(self.rows) < 2:
            return []
        header = self.header()
        out = []
        for row in self.rows[1:]:
            if not any(cell.strip() for cell in row):
                continue
            out.append(
                {
                    (header[i] if i < len(header) and header[i] else f"col{i}"): (
                        row[i].strip() if i < len(row) else ""
                    )
                    for i in range(max(len(header), len(row)))
                }
            )
        return out


@dataclass
class ParsedDocument:
    text: str = ""
    pages: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page_for_offset(self, offset: int) -> int:
        if not self.pages:
            return 0
        cursor = 0
        for index, page in enumerate(self.pages, start=1):
            cursor += len(page) + 2
            if offset < cursor:
                return index
        return len(self.pages)

    def page_text(self, page: int) -> str:
        if 1 <= page <= len(self.pages):
            return self.pages[page - 1]
        return ""


def _read_text_file(path: Path) -> ParsedDocument:
    limit = settings.max_uncompressed_upload_bytes
    raw_size = path.stat().st_size
    if raw_size > limit:
        raise OversizedUploadError(
            f"文本文件 {path.name} 大小 {raw_size / 1024 / 1024:.1f}MB，超过安全上限 "
            f"{limit / 1024 / 1024:.1f}MB；请拆分后重试或调高 "
            "BIDCRAFT_MAX_UNCOMPRESSED_UPLOAD_BYTES"
        )
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:  # pragma: no cover - latin-1 always succeeds
        text = path.read_bytes().decode("utf-8", errors="ignore")
    return ParsedDocument(text=text, pages=[text], meta={"source": path.name})


def _read_pdf(path: Path) -> ParsedDocument:
    from . import mineru

    if mineru.available():
        try:
            return mineru.parse_pdf(path)
        except Exception as exc:  # noqa: BLE001 - MinerU must never block parsing
            mineru_failure = f"{type(exc).__name__}: {exc}"
        else:  # pragma: no cover - defensive
            mineru_failure = ""
    else:
        mineru_failure = ""

    pages: list[str] = []
    tables: list[Table] = []
    meta: dict[str, Any] = {"source": path.name}
    if mineru_failure:
        meta["mineru_error"] = mineru_failure
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        meta["pdf_engine"] = "pypdf"
    except Exception as exc:  # noqa: BLE001
        meta["pdf_error"] = str(exc)

    if not any(page.strip() for page in pages):
        # Fall back to pdfplumber, which also gives us table candidates.
        try:
            import pdfplumber

            with pdfplumber.open(str(path)) as pdf:
                pages = [(page.extract_text() or "") for page in pdf.pages]
                for index, page in enumerate(pdf.pages, start=1):
                    for raw in page.extract_tables() or []:
                        rows = [[(cell or "").strip() for cell in row] for row in raw if row]
                        if len(rows) >= 2:
                            tables.append(Table(page=index, rows=rows))
            meta["pdf_engine"] = "pdfplumber"
        except Exception as exc:  # noqa: BLE001
            meta["pdf_error"] = str(exc)

    blank_pages = [index for index, page in enumerate(pages, start=1) if not page.strip()]
    if settings.ocr_enabled and (blank_pages or not pages):
        try:
            from .ocr import OcrError, OcrUnavailable, ocr_pdf

            result = ocr_pdf(path, blank_pages or None)
        except OcrUnavailable as exc:
            meta["ocr_attempted"] = True
            meta["ocr_unavailable"] = str(exc)
        except OcrError as exc:
            meta["ocr_attempted"] = True
            meta["ocr_error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - OCR must never break parsing
            meta["ocr_attempted"] = True
            meta["ocr_error"] = f"{type(exc).__name__}: {exc}"
        else:
            meta["ocr_attempted"] = True
            meta["ocr_engine"] = "pdftoppm+tesseract"
            meta["ocr_languages"] = "+".join(result.languages)
            meta["ocr_pages"] = len(result.pages)
            if result.warnings:
                meta["ocr_warnings"] = result.warnings
            for page_number, page_text in zip(result.page_numbers, result.pages):
                if 1 <= page_number <= len(pages):
                    pages[page_number - 1] = page_text
                elif page_number == len(pages) + 1:
                    pages.append(page_text)

    if not any(page.strip() for page in pages):
        meta["needs_ocr"] = True
    elif any(not page.strip() for page in pages):
        meta["blank_pages"] = sum(1 for page in pages if not page.strip())
    text = "\n\n".join(pages)
    return ParsedDocument(text=text, pages=pages, tables=tables, meta=meta)


def _read_docx(path: Path) -> ParsedDocument:
    from docx import Document

    document = Document(str(path))
    lines: list[str] = []
    tables: list[Table] = []
    for paragraph in document.paragraphs:
        style = (paragraph.style.name or "").lower() if paragraph.style else ""
        text = paragraph.text.strip()
        if not text:
            continue
        if style.startswith(("heading", "标题")):
            lines.append(text)
        else:
            lines.append(text)
    for table in document.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
            if any(cells):
                rows.append(cells)
        if len(rows) >= 2:
            tables.append(Table(page=0, rows=rows))
    text = "\n".join(lines)
    return ParsedDocument(
        text=text,
        pages=[text],
        tables=tables,
        meta={"source": path.name, "docx_engine": "python-docx"},
    )


def _read_xlsx(path: Path) -> ParsedDocument:
    """Treat each sheet as a page and each contiguous row block as a table."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise ValueError("读取 Excel 需要 openpyxl，请先安装项目依赖") from exc

    workbook = load_workbook(str(path), read_only=True, data_only=True)
    pages: list[str] = []
    tables: list[Table] = []
    try:
        for sheet_index, sheet in enumerate(workbook.worksheets, start=1):
            lines = [f"## {sheet.title}"]
            block: list[list[str]] = []
            for row in sheet.iter_rows(values_only=True):
                cells = [
                    str(value).replace("\n", " ").strip()
                    for value in row
                    if value is not None and str(value).strip()
                ]
                if not cells:
                    if len(block) >= 2:
                        tables.append(Table(page=sheet_index, rows=block))
                    block = []
                    continue
                block.append(cells)
                lines.append(" | ".join(cells))
            if len(block) >= 2:
                tables.append(Table(page=sheet_index, rows=block))
            pages.append("\n".join(lines))
    finally:
        workbook.close()
    return ParsedDocument(
        text="\n\n".join(pages),
        pages=pages,
        tables=tables,
        meta={"source": path.name, "xlsx_engine": "openpyxl", "sheets": len(pages)},
    )


def load_document(path: str | Path) -> ParsedDocument:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".text"}:
        return _read_text_file(file_path)
    if suffix == ".pdf":
        return _read_pdf(file_path)
    if suffix in OOXML_SUFFIXES:
        _guard_ooxml_size(file_path)
    if suffix == ".docx":
        return _read_docx(file_path)
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(file_path)
    if suffix == ".xls":
        raise ValueError("旧版 .xls 不受支持，请另存为 .xlsx 或 PDF")
    if suffix == ".doc":
        raise ValueError("旧版 .doc 不受支持，请另存为 .docx 或 PDF")
    raise ValueError(f"不支持的文件类型: {suffix}")


def normalize_fullwidth(text: str) -> str:
    out = []
    for char in text:
        code = ord(char)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(char)
    return "".join(out)


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")
