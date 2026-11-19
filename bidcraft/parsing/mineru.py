"""MinerU parser adapter (optional, pluggable).

BidCraft keeps the built-in ``pypdf`` / ``pdfplumber`` / OCR chain as the
default.  When a MinerU service is configured (``BIDCRAFT_MINERU_URL``), a PDF
is sent to that service first: its Markdown output handles complex layouts and
image-only pages far better than page-image OCR.  Any failure falls back to the
built-in chain, so a missing or slow service can never block parsing.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from .loader import ParsedDocument, Table

MINERU_TIMEOUT_DEFAULT = 180
PAGE_CHUNK_CHARS = 2600
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_RE = re.compile(r"<[^>]+>")


class MineruError(RuntimeError):
    """MinerU responded, but not with usable content."""


class MineruUnavailable(MineruError):
    """MinerU is not configured or the service cannot be reached."""


def base_url() -> str:
    return (settings.mineru_url or "").rstrip("/")


def available() -> bool:
    return bool(base_url()) and settings.mineru_enabled


def _clean_markdown(markdown: str) -> str:
    text = _IMAGE_RE.sub("", markdown or "")
    text = _HTML_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _pages_from_markdown(markdown: str) -> list[str]:
    """Split MinerU Markdown into page-like chunks.

    MinerU returns one Markdown stream without reliable page numbers.  We keep
    the form-feed separators when the service provides them and otherwise chunk
    by paragraph boundaries so quote offsets still map to a plausible page.
    """

    if "\f" in markdown:
        parts = [part.strip() for part in markdown.split("\f")]
        parts = [part for part in parts if part]
        if parts:
            return parts

    pages: list[str] = []
    current: list[str] = []
    size = 0
    for line in markdown.split("\n"):
        current.append(line)
        size += len(line) + 1
        if size >= PAGE_CHUNK_CHARS and not line.strip():
            pages.append("\n".join(current).strip())
            current, size = [], 0
    tail = "\n".join(current).strip()
    if tail:
        pages.append(tail)
    return pages or [markdown.strip()]


def _tables_from_markdown(markdown: str, pages: list[str]) -> list[Table]:
    """Recover pipe tables so material/scoring extraction keeps working."""

    tables: list[Table] = []
    block: list[str] = []
    page = 1
    cursor = 0
    lengths = [len(item) + 2 for item in pages] or [len(markdown) + 2]

    def flush() -> None:
        nonlocal block
        rows = []
        for row in block:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            if not cells or not any(cells):
                continue
            if all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells):
                continue
            rows.append(cells)
        if len(rows) >= 2:
            tables.append(Table(page=page, rows=rows))
        block = []

    for line in markdown.split("\n"):
        if line.strip().startswith("|") and line.strip().endswith("|"):
            if not block:
                while page < len(lengths) and cursor >= sum(lengths[:page]):
                    page += 1
            block.append(line)
        else:
            if block:
                flush()
        cursor += len(line) + 1
    if block:
        flush()
    return tables


def _extract_markdown(payload: dict[str, Any]) -> str:
    for key in ("md_content", "markdown", "md", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("md_content", "markdown", "md", "content"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def parse_pdf(path: str | Path) -> ParsedDocument:
    """Send one PDF to MinerU and map its Markdown onto ``ParsedDocument``."""

    if not available():
        raise MineruUnavailable("未配置 MinerU（BIDCRAFT_MINERU_URL）")
    file_path = Path(path)
    if not file_path.exists():
        raise MineruError(f"文件不存在: {file_path}")
    timeout = float(settings.mineru_timeout or MINERU_TIMEOUT_DEFAULT)
    started = time.monotonic()
    try:
        with file_path.open("rb") as handle:
            response = httpx.post(
                f"{base_url()}/file_parse",
                files={"files": (file_path.name, handle, "application/pdf")},
                data={"parse_method": "auto", "return_md": "true", "return_content_list": "true"},
                timeout=timeout,
            )
    except httpx.HTTPError as exc:
        raise MineruUnavailable(f"无法连接 MinerU：{exc}") from exc

    if response.status_code >= 400:
        raise MineruError(f"MinerU 返回 {response.status_code}: {response.text[:200]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise MineruError("MinerU 响应不是 JSON") from exc
    if not isinstance(payload, dict):
        raise MineruError("MinerU 响应格式异常")

    markdown = _extract_markdown(payload)
    if not markdown.strip():
        raise MineruError("MinerU 未返回 Markdown 内容")

    cleaned = _clean_markdown(markdown)
    pages = _pages_from_markdown(cleaned)
    tables = _tables_from_markdown(cleaned, pages)
    return ParsedDocument(
        text="\n\n".join(pages),
        pages=pages,
        tables=tables,
        meta={
            "source": file_path.name,
            "pdf_engine": "mineru",
            "mineru_url": base_url(),
            "mineru_elapsed": round(time.monotonic() - started, 2),
            "mineru_table_count": len(tables),
            "mineru_pages_estimated": True,
        },
    )
