from __future__ import annotations

from docx import Document

from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.export.docx import export_docx, export_markdown
from bidcraft.generation.agent import ModelClient
from bidcraft.generation.outline import build_outline, load_outline, save_outline
from bidcraft.generation.writer import count_words, generate_document
from bidcraft.ledger import ledger_summary, render_ledger
from bidcraft.pipeline import add_source, check_project, create_project, parse_project
from bidcraft.rules.engine import ledger_gate
from bidcraft.rules.loader import load_package


def test_full_pipeline_without_model(db, tender_path, tmp_path):
    with session_scope() as session:
        project = create_project(session, "某园区智能化工程（一期）", "weakcurrent-cabling")
        add_source(session, project, tender_path)
        ledger, _ = parse_project(session, project.id)

        assert ledger_gate(ledger) == []
        summary = ledger_summary(ledger)
        assert summary["missing_required"] == 0
        assert "某园区智能化工程（一期）" in render_ledger(ledger)
        assert "{{待填" in render_ledger(ledger)  # missing optional facts stay placeholders
        assert ledger.risks
        assert all(not risk.text.startswith("第") for risk in ledger.risks)
        assert len({risk.text for risk in ledger.risks}) == len(ledger.risks)

        package = load_package(project.package)
        spec = build_outline(ledger, package)
        assert spec.mode == "package"
        save_outline(session, project.id, spec)
        session.commit()

        client = ModelClient(settings)
        assert not client.available
        report = generate_document(session, project.id, client, ledger, package)
        assert report.failed == []
        assert len(report.written) == len(spec.items)
        assert len(report.fallback) == len(report.written)
        session.commit()

        nodes = load_outline(session, project.id)
        assert all(node["body"] for node in nodes)
        assert any("投标有效期" in node["body"] for node in nodes)
        assert count_words(nodes[0]["body"]) > 100

        results, verdict = check_project(session, project.id)
        assert verdict["verdict"] in {"pass", "warn"}
        assert not any(result.level == "blocking" for result in results)
        session.commit()

        docx_path = export_docx(tmp_path / "bid.docx", ledger, nodes)
        md_path = export_markdown(tmp_path / "bid.md", ledger, nodes)
        assert docx_path.exists() and docx_path.stat().st_size > 5000
        assert md_path.exists()
        document = Document(str(docx_path))
        assert len(document.tables) >= 5  # fact/parameter tables become real Word tables
        all_text = "\n".join(p.text for p in document.paragraphs)
        assert "某园区智能化工程（一期）" in all_text
        assert "{{待填:投标人名称}}" in all_text
        assert "| ---" not in all_text  # no raw markdown table leaked into prose


def test_scoring_outline_includes_commercial_chapters(db, tender_path):
    with session_scope() as session:
        project = create_project(session, "评分项目", "weakcurrent-cabling")
        add_source(session, project, tender_path)
        ledger, _ = parse_project(session, project.id)
        package = load_package(project.package)
        spec = build_outline(ledger, package, mode="scoring", total_words=30000)
        headings = [item.heading for item in spec.items]
        assert any("投标函" in h for h in headings)
        assert any("商务响应" in h for h in headings)
        assert any(h == "技术方案" for h in headings)
        assert sum(item.target_words for item in spec.items) > 20000


def test_tender_format_outline(db, tender_path):
    with session_scope() as session:
        project = create_project(session, "格式项目", "weakcurrent-cabling")
        add_source(session, project, tender_path)
        ledger, _ = parse_project(session, project.id)
        package = load_package(project.package)
        spec = build_outline(ledger, package, mode="tender-format")
        assert len(spec.items) == 7
        assert "投标函" in spec.items[0].heading
