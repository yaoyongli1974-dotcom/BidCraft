"""RC-10：真实/脱敏投标 E2E 的数据集门禁。

只有用户提供的、带人工标注的真实/脱敏文件才会进入 parse→outline→generate→
check→export 全链路；数据集未就绪时用例以 ``DATA BLOCKED`` 明确跳过，绝不
拿仓库里的合成夹具冒充真实样本。数据集根目录由 ``BIDCRAFT_REAL_TENDER_ROOT``
注入（见 tests/conftest.py 的 ``real_tender_root`` fixture）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.evaluation.tender_dataset import dataset_status, load_dataset
from bidcraft.export.docx import export_docx, export_markdown
from bidcraft.generation.agent import ModelClient
from bidcraft.generation.outline import build_outline, load_outline, save_outline
from bidcraft.generation.writer import generate_document
from bidcraft.ledger import render_ledger
from bidcraft.pipeline import add_source, check_project, create_project, parse_project
from bidcraft.rules.loader import load_package
from bidcraft.verdict import judge


def _text(value: object) -> str:
    """与真实件指标同口径：只折叠空白，不做语义推断。"""
    return " ".join(str(value or "").split())


def _run_annotated_file(
    path: Path, entry: dict, output_dir: Path, index: int
) -> None:
    """对单个已标注真实文件跑完整 E2E，并回检人工标注的条款/数值/判定。"""
    annotations = entry["annotations"]
    clauses = [str(item) for item in (annotations.get("required_clauses") or [])]
    values = annotations.get("required_values") or []
    expected_verdicts = annotations.get("expected_verdicts") or {}
    responses = annotations.get("responses") or {}
    assert clauses, f"{entry['path']} 缺少 required_clauses 标注"
    assert values, f"{entry['path']} 缺少 required_values 标注"
    assert expected_verdicts, f"{entry['path']} 缺少 expected_verdicts 标注"

    with session_scope() as session:
        project = create_project(
            session, f"真实回归-{path.stem}-{index}", "weakcurrent-cabling"
        )
        source = add_source(session, project, path)
        assert source.checksum == entry["sha256"], "解析输入必须是清单指纹对应的真实文件"

        # parse
        ledger, _info = parse_project(session, project.id)
        parsed_text = _text(
            "\n".join(
                [
                    "\n".join(str(getattr(row, "text", "") or "") for row in ledger.requirements),
                    render_ledger(ledger),
                ]
            )
        )
        for clause in clauses:
            assert _text(clause) in parsed_text, f"{entry['path']} 未识别必需条款：{clause}"
        for item in values:
            expected = _text(
                str(item.get("value") or "")
                if isinstance(item, dict)
                else str(item)
            )
            assert expected, f"{entry['path']} 的数值标注为空"
            assert expected in parsed_text, f"{entry['path']} 未解析必需数值：{expected}"

        # outline + generate
        package = load_package(project.package)
        spec = build_outline(ledger, package)
        assert spec.items, f"{entry['path']} 未生成大纲"
        save_outline(session, project.id, spec)
        session.commit()
        client = ModelClient(settings)
        assert not client.available, "回归链路离线运行，不依赖外部模型"
        report = generate_document(session, project.id, client, ledger, package)
        assert report.failed == [], f"{entry['path']} 章节生成失败：{report.failed}"
        session.commit()
        nodes = load_outline(session, project.id)
        assert nodes and all(node["body"] for node in nodes)
        bid_text = _text("\n".join(str(node["body"]) for node in nodes))
        for clause in clauses:
            assert (
                _text(clause) in parsed_text or _text(clause) in bid_text
            ), f"{entry['path']} 生成的标书未覆盖必需条款：{clause}"

        # check
        _results, verdict = check_project(session, project.id)
        assert verdict["verdict"] in {"pass", "warn", "blocked"}, "核对必须给出真实结论"
        session.commit()

        # export
        output_dir.mkdir(parents=True, exist_ok=True)
        docx_path = export_docx(output_dir / f"bid-{index}.docx", ledger, nodes)
        md_path = export_markdown(output_dir / f"bid-{index}.md", ledger, nodes)
        assert docx_path.is_file() and docx_path.stat().st_size > 0
        assert md_path.is_file() and md_path.stat().st_size > 0
        markdown = md_path.read_text(encoding="utf-8")
        assert _text(clauses[0]) in _text(markdown) or _text(clauses[0]) in bid_text

        # 判定一致性：只对标注了应答正文的条款复算；缺应答记为未测量，不伪造通过。
        compared = 0
        for clause, expected in expected_verdicts.items():
            body = responses.get(str(clause))
            if body is None or not str(body).strip():
                continue
            requirement = SimpleNamespace(
                text=str(clause), marker="", source_quote=str(clause)
            )
            actual = str(judge(requirement, [{"body": str(body)}]).state or "").strip().lower()
            want = str(expected).strip().lower()
            assert actual == want, (
                f"{entry['path']} 判定不一致：{clause[:40]} 期望 {want}，实测 {actual}"
            )
            compared += 1
        # 要求至少有一条可比判定，否则数据集应补齐 responses 标注。
        assert compared > 0, f"{entry['path']} 没有可比对的判定标注（verdict 未测量）"


def test_real_tender_dataset_gated_e2e(db, real_tender_root: Path, tmp_path: Path) -> None:
    """数据集未就绪时 DATA BLOCKED 跳过；就绪才逐文件跑真实件 E2E。"""
    status = dataset_status(real_tender_root)
    if not status["ready"]:
        pytest.skip(
            "DATA BLOCKED: 未提供真实/脱敏招标文件及人工标注，跳过真实件 E2E；"
            f"root={real_tender_root}；reason={status['reason']}"
        )

    dataset = load_dataset(real_tender_root)
    assert dataset["files"], "ready=True 的清单必须至少包含一个已标注文件"
    for index, entry in enumerate(dataset["files"]):
        _run_annotated_file(real_tender_root / entry["path"], entry, tmp_path, index)
