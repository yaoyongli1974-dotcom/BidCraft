from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, terms, textqa
from .config import settings
from .ledger import build_ledger, load_ledger, save_ledger
from .models import Issue, Project, SourceFile
from .parsing.extract import extract
from .parsing.loader import Table, load_document
from .rules import store as package_store
from .rules.engine import run_checks
from .rules.loader import load_package
from .schemas import CheckResult


def create_project(db: Session, name: str, package: str = "weakcurrent-cabling") -> Project:
    load_package(package)  # fail fast on a bad package name
    if package_store.is_hidden(package):
        raise ValueError("该规则包已隐藏；请在管理控制台恢复后再新建项目")
    project = Project(name=name, package=package, status="intake")
    db.add(project)
    db.flush()
    settings.project_dir(project.id)
    return project


def resolve_project(db: Session, ref: str) -> Project:
    project = db.get(Project, ref)
    if project is not None:
        return project
    prefix = list(db.scalars(select(Project).where(Project.id.like(f"{ref}%")).limit(5)))
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        ids = ", ".join(f"{p.id[:8]} {p.name}" for p in prefix)
        raise ValueError(f"ID 前缀不唯一：{ids}")
    matches = list(
        db.scalars(select(Project).where(Project.name.like(f"%{ref}%")).limit(5))
    )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"找不到项目: {ref}")
    ids = ", ".join(f"{p.id[:8]} {p.name}" for p in matches)
    raise ValueError(f"项目名不唯一，请用 ID 前缀：{ids}")


def add_source(db: Session, project: Project, file_path: str | Path, role: str = "tender") -> SourceFile:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))
    target_dir = settings.project_dir(project.id) / "sources"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        stem, suffix = path.stem, path.suffix
        index = 1
        while target.exists():
            target = target_dir / f"{stem}-{index}{suffix}"
            index += 1
    try:
        shutil.copy2(path, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        source = SourceFile(
            project_id=project.id,
            role=role,
            filename=path.name,
            stored_path=str(target),
            size=target.stat().st_size,
            mime=path.suffix.lower(),
            checksum=digest,
            parse_state="pending",
        )
        db.add(source)
        db.flush()
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return source


def parse_project(db: Session, project_id: str) -> tuple[object, dict]:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    package = load_package(project.package)
    sources = list(
        db.scalars(
            select(SourceFile)
            .where(SourceFile.project_id == project_id, SourceFile.role == "tender")
            .order_by(SourceFile.created_at)
        )
    )
    if not sources:
        raise ValueError("项目没有招标文件，请先导入 PDF/DOCX/TXT")

    texts: list[str] = []
    pages: list[str] = []
    tables: list[Table] = []
    notes: list[str] = []
    for source in sources:
        try:
            parsed = load_document(source.stored_path)
        except Exception as exc:  # noqa: BLE001
            source.parse_state = "failed"
            source.parse_note = str(exc)
            notes.append(f"{source.filename}: {exc}")
            continue
        source.text_content = parsed.text
        source.page_count = parsed.page_count
        source.parse_state = "needs_ocr" if parsed.meta.get("needs_ocr") else "parsed"
        source.parse_note = ", ".join(f"{k}={v}" for k, v in parsed.meta.items())
        if parsed.meta.get("needs_ocr"):
            reason = parsed.meta.get("ocr_unavailable") or parsed.meta.get("ocr_error")
            if reason:
                notes.append(f"{source.filename}: 未提取到文本，OCR 未完成（{reason}）")
            elif parsed.meta.get("ocr_pages"):
                notes.append(f"{source.filename}: 扫描页 OCR 未识别出文字")
            else:
                notes.append(f"{source.filename}: 未提取到文本，可能是扫描件，需要 OCR")
        elif parsed.meta.get("ocr_pages"):
            notes.append(
                f"{source.filename}: 已对 {parsed.meta['ocr_pages']} 页扫描页执行 OCR"
            )
        texts.append(parsed.text)
        pages.extend(parsed.pages)
        tables.extend(parsed.tables)

    if not any(text.strip() for text in texts):
        raise ValueError(
            "招标文件未提取到文本（可能是扫描件）；请安装 pdftoppm + tesseract 中文语言包，"
            "或提供可复制文本的 PDF"
        )

    raw_text = "\n\n".join(texts)
    extracted = extract(raw_text, package, pages=pages, tables=tables)
    ledger = build_ledger(package, extracted, source_filename=", ".join(s.filename for s in sources))
    save_ledger(db, project_id, ledger)
    project.status = "analyzed"
    project.buyer = ledger.value("project.buyer")
    project.meta = {
        **(project.meta or {}),
        "number": ledger.value("project.number"),
        "tender_number": ledger.value("tender.number"),
    }
    db.flush()
    return ledger, {"notes": notes, "tables": len(tables), "pages": len(pages)}


def _term_results(db: Session, sections: list[dict]) -> list[CheckResult]:
    """Turn terminology findings into check results for the issue ledger."""

    return [
        CheckResult(
            code=finding["id"],
            level=finding["level"],
            category=finding["category"],
            message=finding["message"],
            location=finding["location"],
            finding=finding["message"],
            recommendation=finding["recommendation"],
            evidence=finding["evidence"],
            passed=False,
        )
        for finding in terms.scan_sections(db, sections, limit=terms.MAX_SCAN_FINDINGS)
    ]


def _profile_results(db: Session, project: Project, sections: list[dict]) -> list[CheckResult]:
    """Flag chapter values that contradict the project's global facts."""

    from . import profile as profile_module

    anchors = profile_module.anchors(profile_module.load(project))
    if not anchors:
        return []
    findings = textqa.profile_conflicts(sections, anchors)
    return [
        CheckResult(
            code=finding["id"],
            level=finding["level"],
            category=finding["category"],
            message=finding["message"],
            location=finding["location"],
            finding=finding["message"],
            recommendation=finding["recommendation"],
            evidence=finding["evidence"],
            passed=False,
        )
        for finding in findings
    ]


def _textqa_results(db: Session, sections: list[dict]) -> list[CheckResult]:
    """Turn typo / consistency / duplication findings into check results."""

    return [
        CheckResult(
            code=finding["id"],
            level=finding["level"],
            category=finding["category"],
            message=finding["message"],
            location=finding["location"],
            finding=finding["message"],
            recommendation=finding["recommendation"],
            evidence=finding["evidence"],
            passed=False,
        )
        for finding in textqa.scan_sections(db, sections)
    ]


def _issue_anchor(location: str, anchors: list[dict]) -> dict | None:
    """把核对结果的 location 锚定到大纲节点；匹配不到返回 None（不猜）。

    先精确匹配 heading，再退化为包含匹配；一致性/重复类的 location 是
    多 heading 拼接（``、`` 或 ``/`` 分隔），取第一个能匹配上的。
    """

    loc = (location or "").strip()
    if not loc or not anchors:
        return None
    parts = [part.strip() for part in re.split(r"[、/／]", loc) if part.strip()]
    candidates = [loc, *parts]
    for candidate in candidates:
        for anchor in anchors:
            if anchor["heading"] and anchor["heading"] == candidate:
                return anchor
    for candidate in candidates:
        for anchor in anchors:
            heading = anchor["heading"]
            if heading and (heading in candidate or candidate in heading):
                return anchor
    return None


def check_project(db: Session, project_id: str) -> tuple[list, dict]:
    from .generation.outline import load_outline

    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    package = load_package(project.package)
    ledger = load_ledger(db, project_id)
    nodes = load_outline(db, project_id)
    all_results = run_checks(package, ledger, nodes, include_passed=True)
    all_results.extend(_term_results(db, nodes))
    all_results.extend(_textqa_results(db, nodes))
    all_results.extend(_profile_results(db, project, nodes))
    results = [result for result in all_results if not result.passed]
    anchors = [
        {
            "heading": str(node.get("heading") or "").strip(),
            "node_id": node["id"],
            "section_id": node.get("section_id"),
            "position": position,
        }
        for position, node in enumerate(nodes)
    ]
    # 重插前快照人工处置结论：key=(code, location, finding) 命中且 finding
    # 完全相同时沿用旧状态，否则回到 open。必须在 DELETE 前取值快照，
    # 删除后 ORM 对象不可再读。
    previous = {
        (row.code, row.location, row.finding): (
            row.status,
            row.resolved_at,
            row.resolved_by,
            row.resolution_note or "",
        )
        for row in db.scalars(select(Issue).where(Issue.project_id == project_id))
    }
    checked_at = clock.utc_now()
    db.query(Issue).filter(Issue.project_id == project_id).delete()
    for result in results:
        anchor = _issue_anchor(result.location, anchors)
        if anchor is not None:
            result.node_id = anchor["node_id"]
            result.section_id = anchor["section_id"]
            result.heading = anchor["heading"]
            result.position = anchor["position"]
        finding = result.finding or result.message
        old = previous.get((result.code, result.location, finding))
        status, resolved_at, resolved_by, resolution_note = (
            old if old is not None else ("open", None, None, "")
        )
        db.add(
            Issue(
                project_id=project_id,
                node_id=result.node_id,
                section_id=result.section_id,
                code=result.code,
                level=result.level,
                category=result.category,
                location=result.location,
                finding=finding,
                recommendation=result.recommendation,
                status=status,
                evidence=result.evidence,
                checked_at=checked_at,
                resolved_at=resolved_at,
                resolved_by=resolved_by,
                resolution_note=resolution_note,
            )
        )
    project.status = "review"
    db.flush()
    from .rules.engine import summarize_results

    return results, summarize_results(all_results)
