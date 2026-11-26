"""v0.32 Phase 5 经验学习后端：真实 DB 回归（只读、去重、范围与提示词隔离）。

所有来源行都直接落在仓库的 ``db`` fixture（SQLite 真库）里，不用 mock 伪造
finding / revision / audit / plan；模型调用只用本地可捕获的桩客户端，核心断言
全部针对真实落库数据与真实提示词拼装路径。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select

from bidcraft.audit import record_audit
from bidcraft.db import session_scope
from bidcraft.evaluation.learning import (
    LESSON_LABEL,
    MAX_LESSONS,
    lesson_items,
    lessons_block,
    lessons_payload,
)
from bidcraft.generation.chapter_plan import PROMPT_VERSION, build_plan_context
from bidcraft.generation.writer import generate_document, suspicious_numbers
from bidcraft.models import (
    AuditLog,
    ChapterPlan,
    KbChunk,
    KbDocument,
    KnowledgeBase,
    OutlineNode,
    Project,
    QualityFinding,
    QualityReport,
    Section,
    SectionRevision,
)
from bidcraft.rules.loader import load_package
from bidcraft.schemas import FactLedger

# v0.33.0 RC-08：治理字段加入契约（只新增，不删旧断言）。
LESSON_KEYS = {
    "id",
    "source",
    "kind",
    "text",
    "node_id",
    "created_at",
    "author",
    "status",
    "grade",
    "trust",
    "expires_at",
    "revoked",
}


def _project(db, name: str) -> Project:
    row = Project(id=f"p-{uuid.uuid4().hex}", name=name, package="weakcurrent-cabling")
    db.add(row)
    db.flush()
    return row


def _node(db, project_id: str, heading: str = "技术方案") -> OutlineNode:
    node = OutlineNode(
        project_id=project_id, heading=heading, intent="逐条响应评分标准", target_words=1200
    )
    db.add(node)
    db.flush()
    return node


def _handled_finding(db, project_id: str, node_id: str, *, note: str) -> QualityFinding:
    report = QualityReport(project_id=project_id, report_version=1, summary={})
    db.add(report)
    db.flush()
    finding = QualityFinding(
        report_id=report.id,
        node_id=node_id,
        category="核对问题",
        level="major",
        title="资质页码不一致",
        status="handled",
        evidence={
            "resolution_note": note,
            "resolved_by": "处置人甲",
            "resolved_at": "2026-02-02T10:00:00+08:00",
        },
    )
    db.add(finding)
    db.flush()
    return finding


def _revision(
    db, project_id: str, node_id: str, *, note: str, change_type: str = "edit", revision: int = 1
) -> SectionRevision:
    row = SectionRevision(
        project_id=project_id,
        node_id=node_id,
        revision=revision,
        change_type=change_type,
        change_note=note,
        changed_by="编辑乙",
        created_at=datetime(2026, 1, revision, 0, 0),
    )
    db.add(row)
    db.flush()
    return row


def _seed_all_sources(db, project_id: str, node_id: str) -> dict[str, str]:
    finding = _handled_finding(db, project_id, node_id, note="资质附件已按招标文件页码重排")
    edit = _revision(db, project_id, node_id, note="报价表按清单重新核对", revision=2)
    restore = _revision(
        db, project_id, node_id, note="回滚到上一版正文", change_type="restore", revision=3
    )
    audit = record_audit(
        db,
        project_id,
        "chapter.review",
        {"node_id": node_id, "heading": "技术方案", "note": "复核时补充了验收标准"},
        actor={"username": "复核人丙", "display_name": "复核人丙"},
    )
    plan = ChapterPlan(
        project_id=project_id,
        node_id=node_id,
        version=2,
        status="CONFIRMED",
        user_adjustments=[
            {"by": "规划人丁", "at": "2026-03-03T09:00:00+08:00", "note": "增加实施路径"}
        ],
    )
    db.add(plan)
    db.flush()
    # 未确认规划和未处置 finding 不应沉淀经验。
    db.add(
        QualityFinding(
            report_id=finding.report_id,
            node_id=node_id,
            title="未处置问题",
            status="open",
            evidence={"resolution_note": "还没处理完"},
        )
    )
    db.add(
        ChapterPlan(
            project_id=project_id,
            node_id=node_id,
            version=3,
            status="DRAFT",
            user_adjustments=[{"note": "草稿阶段的临时想法"}],
        )
    )
    db.flush()
    return {
        "finding": finding.id,
        "edit": edit.id,
        "restore": restore.id,
        "audit": str(audit.id),
        "plan": plan.id,
    }


def test_lesson_payload_contract_and_real_sources(db):
    with session_scope() as sess:
        project = _project(sess, "learning-contract")
        node = _node(sess, project.id)
        ids = _seed_all_sources(sess, project.id, node.id)

        payload = lessons_payload(sess, project)
        assert set(payload) == {"items", "node_id", "status", "policy"}
        assert payload["status"] == "未审核建议经验"
        assert payload["node_id"] == ""
        rows = lesson_items(sess, project)
        assert rows == payload["items"]
        assert rows
        assert all(set(row) == LESSON_KEYS for row in rows)
        assert all(isinstance(row["id"], str) for row in rows)
        assert all(row["created_at"] is None or isinstance(row["created_at"], str) for row in rows)
        by_id = {row["id"]: row for row in rows}
        assert {ids["finding"], ids["edit"], ids["audit"], ids["plan"]} <= set(by_id)
        assert ids["restore"] not in by_id

        finding = by_id[ids["finding"]]
        assert finding["source"] == "quality_finding"
        assert finding["kind"] == "质量问题处置"
        assert finding["status"] == "handled"
        assert finding["author"] == "处置人甲"
        assert finding["node_id"] == node.id
        assert finding["created_at"] == "2026-02-02T10:00:00+08:00"
        assert "资质附件已按招标文件页码重排" in finding["text"]

        assert by_id[ids["edit"]]["source"] == "section_revision"
        assert by_id[ids["edit"]]["status"] == "edit"
        assert by_id[ids["audit"]]["source"] == "audit_log"
        assert by_id[ids["audit"]]["status"] == "chapter.review"
        assert by_id[ids["plan"]]["source"] == "chapter_plan"
        assert by_id[ids["plan"]]["status"] == "CONFIRMED"
        assert "增加实施路径" in by_id[ids["plan"]]["text"]

        block = lessons_block(sess, project)
        assert LESSON_LABEL in block
        for source_id in (ids["finding"], ids["edit"], ids["audit"], ids["plan"]):
            assert source_id in block
        assert "回滚到上一版正文" not in block
        assert "还没处理完" not in block
        assert "草稿阶段的临时想法" not in block
        assert "不会自动写入知识库或企业资产" in block


def test_lessons_dedupe_by_text_and_cap_at_twelve(db):
    with session_scope() as sess:
        project = _project(sess, "learning-cap")
        node = _node(sess, project.id)
        for index in range(1, 16):
            _revision(
                sess,
                project.id,
                node.id,
                note=f"独立经验-{index:02d}",
                revision=index,
            )
        # 重复说明（跨来源也一样）只保留一条。
        _revision(sess, project.id, node.id, note="重复经验", revision=20)
        _handled_finding(sess, project.id, node.id, note="重复经验")

        rows = lesson_items(sess, project)
        assert len(rows) == MAX_LESSONS <= 12
        texts = [row["text"] for row in rows]
        assert len(set(texts)) == len(texts)
        assert sum("重复经验" in text for text in texts) == 1
        block = lessons_block(sess, project)
        assert block.count("\n- [") == MAX_LESSONS


def test_lessons_are_project_and_node_scoped(db):
    with session_scope() as sess:
        first = _project(sess, "learning-scope-a")
        second = _project(sess, "learning-scope-b")
        node_a = _node(sess, first.id, "第一章")
        node_b = _node(sess, first.id, "第二章")
        other_node = _node(sess, second.id, "其他项目章节")
        ids_a = _seed_all_sources(sess, first.id, node_a.id)
        ids_b = _seed_all_sources(sess, second.id, other_node.id)
        _revision(sess, first.id, node_b.id, note="第二章专属经验", revision=7)
        # 项目级审计（无章节锚点）在按章节过滤时不应混入。
        record_audit(
            sess,
            first.id,
            "export.force",
            {"reason": "强制导出原因已记录", "format": "docx"},
        )

        all_rows = lesson_items(sess, first)
        ids = {row["id"] for row in all_rows}
        assert ids_a["finding"] in ids
        assert not ({ids_b["finding"], ids_b["audit"]} & ids)
        assert "强制导出原因已记录" in lessons_block(sess, first)

        scoped = lesson_items(sess, first, node_id=node_a.id)
        scoped_ids = {row["id"] for row in scoped}
        assert ids_a["finding"] in scoped_ids
        assert ids_a["edit"] in scoped_ids
        assert not (set(ids_b) & scoped_ids)
        assert all(row["node_id"] == node_a.id for row in scoped)
        assert "第二章专属经验" not in lessons_block(sess, first, node_id=node_a.id)
        assert "强制导出原因已记录" not in lessons_block(sess, first, node_id=node_a.id)

        # 传入别的项目的真实节点 ID 也不能绕过项目边界。
        assert lesson_items(sess, first, node_id=other_node.id) == []
        assert lesson_items(sess, "不存在的章节", node_id=node_a.id) == []
        assert lessons_payload(sess, first, node_id=other_node.id)["items"] == []


def test_lessons_never_write_kb_enterprise_or_audit(db):
    def counts(sess):
        return tuple(
            sess.scalar(select(func.count()).select_from(model))
            for model in (KnowledgeBase, KbDocument, KbChunk, AuditLog)
        )

    with session_scope() as sess:
        project = _project(sess, "learning-readonly")
        node = _node(sess, project.id)
        _seed_all_sources(sess, project.id, node.id)
        before = counts(sess)
        lesson_items(sess, project)
        lessons_payload(sess, project)
        lessons_block(sess, project, node_id=node.id)
        sess.flush()
        assert counts(sess) == before


class _CaptureClient:
    """只捕获真实 write_chapter 路径生成的提示词，不发起网络调用。"""

    available = True

    def __init__(self):
        from types import SimpleNamespace

        self.usage = SimpleNamespace(prompt_tokens=0, completion_tokens=0)
        self.settings = SimpleNamespace(model_context_tokens=32768, model_name="stub")
        self.messages: list[list[dict[str, str]]] = []

    def complete(self, messages, **_kwargs) -> str:
        self.messages.append(messages)
        return "本章依据招标文件逐项响应技术要求。" * 80


def test_generate_document_appends_lessons_as_advisory_not_fact(db):
    with session_scope() as sess:
        project = _project(sess, "learning-writer")
        project.meta = {"writing_profile": {"project_name": "示范项目"}}
        node = _node(sess, project.id)
        sess.add(Section(node_id=node.id, body="", status="pending"))
        sess.flush()
        _handled_finding(
            sess,
            project.id,
            node.id,
            note="历史建议：验收口径复核；示例编号 987654 不得当作事实",
        )
        kb_before = sess.scalar(select(func.count()).select_from(KnowledgeBase))

        client = _CaptureClient()
        ledger = FactLedger(package=project.package)
        result = generate_document(
            sess,
            project.id,
            client,
            ledger,
            load_package(project.package),
            only_node=node.id,
        )
        assert result.written == [node.heading]
        prompts = [message[1]["content"] for message in client.messages]
        assert prompts
        prompt = prompts[0]
        assert "【全局事实（必须严格遵守，前后一致）】" in prompt
        assert "- 项目名称：示范项目" in prompt
        assert "【未审核建议经验（非核实事实" in prompt
        assert "历史建议：验收口径复核" in prompt
        assert prompt.index("- 项目名称：示范项目") < prompt.index("【未审核建议经验（非核实事实")
        assert "不属于事实台账，正文中不得当作已核实事实使用" in prompt
        # 建议里的数字不会被当成台账事实白名单。
        assert suspicious_numbers("正文引用 987654", ledger) == ["987654"]
        assert sess.scalar(select(func.count()).select_from(KnowledgeBase)) == kb_before


def test_plan_context_accepts_exact_prompt_version_and_lessons(db):
    assert PROMPT_VERSION == "chapter_plan_v3"
    with session_scope() as sess:
        project = _project(sess, "learning-plan-context")
        node = _node(sess, project.id)
        lessons = lessons_block(sess, project) or "【未审核建议经验】历史提示"
        context = build_plan_context(sess, project.id, node, lessons_text=lessons)
        assert context["lessons_text"] == lessons
        assert context["node_id"] == node.id
