from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, provenance, substantive, terms, textqa
from .config import settings
from .ledger import build_ledger, load_ledger, save_ledger
from .models import Issue, Project, Requirement, SourceFile
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


def read_parse_inputs(db: Session, project_id: str) -> tuple[str, list[dict[str, str]]]:
    """读取解析所需的只读快照（规则包名 + 招标文件路径）。

    调用方可在短事务里取完快照后关闭会话，把耗时的 OCR/抽取放到事务外，
    再用 :func:`persist_parse` 在短写事务里落库（见 API 的 ``reparse``）。
    """

    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    sources = list(
        db.scalars(
            select(SourceFile)
            .where(SourceFile.project_id == project_id, SourceFile.role == "tender")
            .order_by(SourceFile.created_at)
        )
    )
    if not sources:
        raise ValueError("项目没有招标文件，请先导入 PDF/DOCX/TXT")
    return project.package, [
        {"id": source.id, "filename": source.filename, "stored_path": source.stored_path}
        for source in sources
    ]


def parse_documents(
    package_name: str, sources: list[dict[str, str]]
) -> tuple[object, dict, dict[str, dict]]:
    """纯计算阶段：打开文件、OCR、抽取与台账构建，不访问数据库。

    返回 ``(ledger, info, source_updates)``；``source_updates`` 以 source id
    为键，交给 :func:`persist_parse` 在写事务里逐字段回写。
    """

    package = load_package(package_name)
    texts: list[str] = []
    pages: list[str] = []
    tables: list[Table] = []
    notes: list[str] = []
    updates: dict[str, dict] = {}
    for source in sources:
        filename = source["filename"]
        try:
            parsed = load_document(source["stored_path"])
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{filename}: {exc}")
            updates[source["id"]] = {"parse_state": "failed", "parse_note": str(exc)}
            continue
        updates[source["id"]] = {
            "text_content": parsed.text,
            "page_count": parsed.page_count,
            "parse_state": "needs_ocr" if parsed.meta.get("needs_ocr") else "parsed",
            "parse_note": ", ".join(f"{k}={v}" for k, v in parsed.meta.items()),
        }
        if parsed.meta.get("needs_ocr"):
            reason = parsed.meta.get("ocr_unavailable") or parsed.meta.get("ocr_error")
            if reason:
                notes.append(f"{filename}: 未提取到文本，OCR 未完成（{reason}）")
            elif parsed.meta.get("ocr_pages"):
                notes.append(f"{filename}: 扫描页 OCR 未识别出文字")
            else:
                notes.append(f"{filename}: 未提取到文本，可能是扫描件，需要 OCR")
        elif parsed.meta.get("ocr_pages"):
            notes.append(f"{filename}: 已对 {parsed.meta['ocr_pages']} 页扫描页执行 OCR")
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
    ledger = build_ledger(
        package, extracted, source_filename=", ".join(s["filename"] for s in sources)
    )
    return ledger, {"notes": notes, "tables": len(tables), "pages": len(pages)}, updates


def persist_parse(db: Session, project_id: str, ledger: object, updates: dict) -> None:
    """短写事务：把解析结果回写 SourceFile / Project 与事实台账。"""

    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    source_ids = list(updates)
    if source_ids:
        rows = list(db.scalars(select(SourceFile).where(SourceFile.id.in_(source_ids))))
        by_id = {source.id: source for source in rows}
        for source_id, values in updates.items():
            source = by_id.get(source_id)
            if source is None:
                continue
            for key, value in values.items():
                setattr(source, key, value)
    save_ledger(db, project_id, ledger)
    project.status = "analyzed"
    project.buyer = ledger.value("project.buyer")
    project.meta = {
        **(project.meta or {}),
        "number": ledger.value("project.number"),
        "tender_number": ledger.value("tender.number"),
    }
    db.flush()


def parse_project(db: Session, project_id: str) -> tuple[object, dict]:
    """兼容原接口：在同一会话内完成读取、解析与落库。"""

    package_name, source_specs = read_parse_inputs(db, project_id)
    ledger, info, updates = parse_documents(package_name, source_specs)
    persist_parse(db, project_id, ledger, updates)
    return ledger, info


def _finding_results(findings) -> list[CheckResult]:
    """把 LLM/规则返回的 finding 字典统一映射为 CheckResult。

    术语扫描、正文自洽、画像冲突三处返回的是同一种 finding 结构，字段映射
    集中在这里，避免复制粘贴时漏字段或把 ``finding`` 与 ``message`` 写反。
    """

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


def _term_results(db: Session, sections: list[dict]) -> list[CheckResult]:
    """Turn terminology findings into check results for the issue ledger."""

    return _finding_results(
        terms.scan_sections(db, sections, limit=terms.MAX_SCAN_FINDINGS)
    )


def _profile_results(db: Session, project: Project, sections: list[dict]) -> list[CheckResult]:
    """Flag chapter values that contradict the project's global facts."""

    from . import profile as profile_module

    anchors = profile_module.anchors(profile_module.load(project))
    if not anchors:
        return []
    return _finding_results(textqa.profile_conflicts(sections, anchors))


def _textqa_results(db: Session, sections: list[dict]) -> list[CheckResult]:
    """Turn typo / consistency / duplication findings into check results."""

    return _finding_results(textqa.scan_sections(db, sections))


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


def _annotate_requirement_coverage(
    requirements: list[Requirement],
    nodes: list[dict],
    *,
    deadline=None,
    matcher=None,
) -> None:
    """把「这一条被哪一章覆盖了」写回 Requirement，供风险总览展示与跳转。

    复用与 ``requirement_issues`` 相同的 judge() 口径（含截止日期与可选语义层），
    避免 auto_covered=True 但该条款被判未响应的自相矛盾。只改自动匹配结果，
    不动人工确认过的 status / note。
    """

    for requirement in requirements:
        covered, section = substantive.coverage_from_verdict(
            requirement, nodes, deadline=deadline, matcher=matcher
        )
        if not covered:
            requirement.auto_covered = False
            if requirement.response_status == "pending":
                requirement.section_id = None
                requirement.node_id = None
            continue
        requirement.auto_covered = True
        requirement.section_id = section.get("section_id")
        requirement.node_id = section.get("id")


def _generation_suspicious_results(ledger, nodes: list[dict]) -> list:
    """Surface generation-time suspicious numbers as check issues.

    The writer flags ungrounded long numbers while drafting; checks used to
    ignore them.  Recomputing from the persisted body keeps this read-only and
    lets the risk overview / export gate consume the same anti-fabrication
    signal without a new Section column.
    """

    from .generation.writer import suspicious_numbers

    results = []
    for node in nodes:
        body = str(node.get("body") or "")
        if not body.strip():
            continue
        flagged = suspicious_numbers(body, ledger)
        if not flagged:
            continue
        results.append(
            CheckResult(
                code="GEN-SUSPICIOUS-NUMBER",
                level="major",
                category="生成质量",
                message="正文出现台账之外的长数字，需人工复核来源",
                location=str(node.get("heading") or ""),
                finding="、".join(flagged[:8]),
                recommendation="核对招标文件/事实台账，无依据的数字改为 {{待填:具体信息}}",
                evidence={"numbers": flagged, "node_id": node.get("id")},
            )
        )
    return results


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
    all_results.extend(_generation_suspicious_results(ledger, nodes))
    # R03：招标截止日期是业绩时间窗的校验基准——优先 facts 台账，meta 兜底；
    # 拿不到时 judge 会把带明确日期的业绩转人工复核，绝不用服务器当前时间。
    from .verdict import resolve_deadline
    from .verdict_llm import build_verdict_matcher

    deadline = resolve_deadline(ledger.facts, project.meta)
    # 可选 LLM 语义层：默认关闭或模型不可用时自动回退规则，语义分不替代硬约束。
    matcher = build_verdict_matcher(db)
    # 实质性条款 → 正文响应：未响应的条款在这里变成 blocking/critical 问题，
    # 直接进入导出硬门禁；人工标记 responded/na 的条款由 requirement_issues 跳过。
    requirements = list(
        db.scalars(select(Requirement).where(Requirement.project_id == project_id))
    )
    _annotate_requirement_coverage(requirements, nodes, deadline=deadline, matcher=matcher)
    all_results.extend(
        substantive.requirement_issues(requirements, nodes, deadline=deadline, matcher=matcher)
    )
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
    # 核对同时重扫来源追溯：生成的正文也要能被追溯到台账字段，
    # 不依赖用户是否打开过某一章。
    provenance.refresh_project(db, project_id)
    db.flush()
    from .rules.engine import summarize_results

    return results, summarize_results(all_results)
