"""导出后 DOCX 校验：结构、标题层级、表格、图片与占位符。"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn

from .document_ir import PLACEHOLDER_PATTERN, DocumentIR, heading_level, table_kind_from_marker

_MAX_PLACEHOLDER_ISSUES = 50


def _issue(code: str, severity: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, **extra}


def _has_section_properties(paragraph) -> bool:
    paragraph_properties = paragraph._p.find(qn("w:pPr"))
    if paragraph_properties is None:
        return False
    return paragraph_properties.find(qn("w:sectPr")) is not None


def _has_drawing(paragraph) -> bool:
    return bool(paragraph._p.findall(".//" + qn("w:drawing")))


def _body_paragraphs(document) -> list:
    return [p for p in document.paragraphs if not _has_section_properties(p)]


def _check_headings(paragraphs: list) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    previous_level = 0
    for index, paragraph in enumerate(paragraphs):
        style_name = paragraph.style.name if paragraph.style is not None else ""
        level = heading_level(style_name)
        if level is None:
            continue
        if previous_level and level > previous_level + 1:
            issues.append(
                _issue(
                    "heading_skip",
                    "error",
                    f"标题层级跳级：H{previous_level} 之后直接出现 H{level}",
                    paragraph_index=index,
                    from_level=previous_level,
                    to_level=level,
                    text=paragraph.text[:80],
                )
            )
        elif not previous_level and level > 1:
            issues.append(
                _issue(
                    "heading_skip",
                    "error",
                    f"文档标题层级从 H{level} 开始，缺少 H1",
                    paragraph_index=index,
                    from_level=0,
                    to_level=level,
                    text=paragraph.text[:80],
                )
            )
        previous_level = level
    return issues


def _check_empty_paragraphs(paragraphs: list) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    run_start: int | None = None
    run_length = 0
    for index, paragraph in enumerate(paragraphs):
        empty = not paragraph.text.strip() and not _has_drawing(paragraph)
        if empty:
            if run_start is None:
                run_start = index
            run_length += 1
            continue
        if run_length >= 3:
            issues.append(
                _issue(
                    "empty_paragraphs",
                    "warning",
                    f"连续 {run_length} 个空段落（从第 {run_start} 段开始），可能产生空白页",
                    paragraph_index=run_start,
                    count=run_length,
                )
            )
        run_start, run_length = None, 0
    if run_length >= 3:
        issues.append(
            _issue(
                "empty_paragraphs",
                "warning",
                f"文档末尾连续 {run_length} 个空段落，可能是空白页",
                paragraph_index=run_start,
                count=run_length,
            )
        )
    return issues


def _table_kind(table) -> str:
    """读取渲染器写入的表格类型标记；无标记的旧文档按数据表格处理。"""

    properties = table._tbl.find(qn("w:tblPr"))
    if properties is None:
        return "data"
    description = properties.find(qn("w:tblDescription"))
    marker = description.get(qn("w:val")) if description is not None else None
    return table_kind_from_marker(marker)


def _check_tables(document) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, table in enumerate(document.tables):
        rows = table.rows
        if not rows:
            issues.append(
                _issue(
                    "empty_table", "warning", f"第 {index + 1} 个表格没有任何行", table_index=index
                )
            )
            continue
        # 签字栏、封面键值表、表单等内容表格只做结构性校验（空表已覆盖），
        # 首行留空是合法版式，不能因此阻断交付。
        if _table_kind(table) != "data":
            continue
        header_cells = [cell.text.strip() for cell in rows[0].cells]
        if not all(header_cells):
            issues.append(
                _issue(
                    "table_missing_header",
                    "error",
                    f"第 {index + 1} 个表格首行存在空单元格，缺少有效表头",
                    table_index=index,
                )
            )
        if len(rows) == 1:
            issues.append(
                _issue(
                    "table_no_data",
                    "warning",
                    f"第 {index + 1} 个表格只有表头，没有数据行",
                    table_index=index,
                )
            )
        tr_pr = rows[0]._tr.find(qn("w:trPr"))
        if tr_pr is None or tr_pr.find(qn("w:tblHeader")) is None:
            issues.append(
                _issue(
                    "table_header_not_repeated",
                    "warning",
                    f"第 {index + 1} 个表格未设置跨页重复标题行（w:tblHeader）",
                    table_index=index,
                )
            )
    return issues


def _check_images(document) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    relationships = document.part.rels
    for blip in document.element.body.iter(qn("a:blip")):
        relationship_id = blip.get(qn("r:embed"))
        if not relationship_id:
            continue
        try:
            relationship = relationships[relationship_id]
        except KeyError:
            issues.append(
                _issue(
                    "missing_image_relationship",
                    "error",
                    f"图片关系 {relationship_id} 在包内不存在",
                    relationship_id=relationship_id,
                )
            )
            continue
        if getattr(relationship, "is_external", False):
            issues.append(
                _issue(
                    "external_image",
                    "warning",
                    "图片引用指向外部链接，导出后可能失效",
                    relationship_id=relationship_id,
                )
            )
            continue
        target = getattr(relationship, "target_part", None)
        if target is None or not getattr(target, "blob", b""):
            issues.append(
                _issue(
                    "empty_image",
                    "error",
                    f"图片关系 {relationship_id} 对应的图片数据为空",
                    relationship_id=relationship_id,
                )
            )
    return issues


def _check_placeholders(paragraphs: list, document) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for index, paragraph in enumerate(paragraphs):
        matches = PLACEHOLDER_PATTERN.findall(paragraph.text)
        if matches:
            issues.append(
                _issue(
                    "placeholder",
                    "warning",
                    f"第 {index} 段有 {len(matches)} 处待填占位符",
                    location=f"paragraph:{index}",
                    paragraph_index=index,
                    count=len(matches),
                    text=paragraph.text[:120],
                )
            )
        if len(issues) >= _MAX_PLACEHOLDER_ISSUES:
            return issues
    for table_index, table in enumerate(document.tables):
        for row_index, row in enumerate(table.rows):
            for column_index, cell in enumerate(row.cells):
                text = cell.text
                matches = PLACEHOLDER_PATTERN.findall(text)
                if matches:
                    issues.append(
                        _issue(
                            "placeholder",
                            "warning",
                            f"表格 {table_index + 1} 第 {row_index + 1} 行"
                            f"第 {column_index + 1} 列有 {len(matches)} 处待填占位符",
                            location=f"table:{table_index}:r{row_index}c{column_index}",
                            table_index=table_index,
                            row_index=row_index,
                            column_index=column_index,
                            count=len(matches),
                            text=text[:120],
                        )
                    )
                if len(issues) >= _MAX_PLACEHOLDER_ISSUES:
                    return issues
    return issues


def _check_core_properties(document) -> list[dict[str, Any]]:
    properties = document.core_properties
    missing = [
        name for name in ("title", "author") if not str(getattr(properties, name, "") or "").strip()
    ]
    if not missing:
        return []
    return [
        _issue(
            "missing_core_properties",
            "warning",
            "文档核心属性缺失：" + "、".join(missing),
            fields=missing,
        )
    ]


def validate_docx_package(path: str | Path) -> dict[str, Any]:
    """校验导出的 DOCX 包，返回 ``{"valid": bool, "issues": [...]}``。"""

    package_path = Path(path)
    if not package_path.exists():
        return {
            "valid": False,
            "issues": [_issue("missing_file", "error", f"文件不存在：{package_path}")],
        }
    # ZIP 层 CRC 校验：截断/篡改的 DOCX 可能仍能被部分解析器打开，必须先拦下。
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            broken_member = archive.testzip()
    except (zipfile.BadZipFile, OSError) as exc:
        return {
            "valid": False,
            "issues": [_issue("invalid_docx", "error", f"DOCX 不是有效的 ZIP 包：{exc}")],
        }
    if broken_member is not None:
        return {
            "valid": False,
            "issues": [
                _issue(
                    "invalid_docx",
                    "error",
                    f"DOCX 包内条目 {broken_member} 的 CRC 校验失败",
                    member=broken_member,
                )
            ],
        }
    try:
        document = Document(str(package_path))
    except Exception as exc:  # noqa: BLE001 - 校验器不能向调用方抛异常
        return {
            "valid": False,
            "issues": [
                _issue("invalid_docx", "error", f"无法打开 DOCX：{type(exc).__name__}: {exc}")
            ],
        }

    try:
        paragraphs = _body_paragraphs(document)
        issues: list[dict[str, Any]] = []
        issues.extend(_check_headings(paragraphs))
        issues.extend(_check_empty_paragraphs(paragraphs))
        issues.extend(_check_tables(document))
        issues.extend(_check_images(document))
        issues.extend(_check_placeholders(paragraphs, document))
        issues.extend(_check_core_properties(document))

        if not any(p.text.strip() for p in paragraphs) and not document.tables:
            issues.insert(
                0,
                _issue("blank_document", "error", "文档没有任何正文内容或表格"),
            )
    except Exception as exc:  # noqa: BLE001 - 异常文档结构降级为校验失败
        return {
            "valid": False,
            "issues": [
                _issue("invalid_docx", "error", f"解析文档结构失败：{type(exc).__name__}: {exc}")
            ],
        }

    valid = not any(issue["severity"] == "error" for issue in issues)
    return {"valid": valid, "issues": issues}


def build_document_report(ir: DocumentIR, validation: dict[str, Any]) -> dict[str, Any]:
    """把 IR 摘要与校验结果汇总成可读报告。"""

    headings = [section.heading for section in ir.sections if section.heading]
    paragraph_count = sum(len(section.paragraphs) for section in ir.sections)
    table_count = sum(len(section.tables) for section in ir.sections)
    image_paths: list[str] = []
    for section in ir.sections:
        for image in section.images:
            if isinstance(image, dict):
                image_paths.append(str(image.get("path") or ""))
            else:
                image_paths.append(str(image))
    missing_images = [path for path in image_paths if path and not Path(path).exists()]

    placeholder_locations = [
        {"location": location, "count": count} for location, count in ir.iter_placeholders()
    ]
    placeholder_count = sum(item["count"] for item in placeholder_locations)

    issues = list(validation.get("issues") or [])
    if missing_images:
        issues.append(
            _issue(
                "ir_missing_image",
                "warning",
                "IR 中引用的图片文件不存在：" + "、".join(missing_images[:5]),
                paths=missing_images,
            )
        )
    error_count = sum(1 for issue in issues if issue.get("severity") == "error")
    warning_count = sum(1 for issue in issues if issue.get("severity") == "warning")
    valid = bool(validation.get("valid", False)) and not missing_images

    title = str(ir.core_properties.get("title") or (headings[0] if headings else ""))
    summary_lines = [
        f"文档标题：{title or '（未命名）'}",
        (
            f"章节数：{len(ir.sections)}，标题数：{len(headings)}，段落数：{paragraph_count}，"
            f"表格数：{table_count}，图片数：{len(image_paths)}"
        ),
        f"待填占位符：{placeholder_count} 处，分布在 {len(placeholder_locations)} 个位置",
        (
            f"校验结果：{'通过' if valid else '未通过'}"
            f"（错误 {error_count} 项，警告 {warning_count} 项）"
        ),
    ]
    if missing_images:
        summary_lines.append("缺失图片：" + "、".join(missing_images[:5]))

    return {
        "title": title,
        "sections": len(ir.sections),
        "headings": headings,
        "paragraph_count": paragraph_count,
        "table_count": table_count,
        "image_count": len(image_paths),
        "missing_images": missing_images,
        "placeholder_count": placeholder_count,
        "placeholder_locations": placeholder_locations,
        "validation": validation,
        "valid": valid,
        "issue_count": len(issues),
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issues,
        "summary": "\n".join(summary_lines),
    }


__all__ = ["build_document_report", "validate_docx_package"]
