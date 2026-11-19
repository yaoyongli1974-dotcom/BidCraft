from __future__ import annotations

import json
import sys

import pytest

from bidcraft import dedup

PARA_A = (
    "我方将严格按照招标文件要求组织施工，配备专职项目经理与技术负责人，"
    "建立质量管理体系并落实三级检查制度，明确各工序交接与隐蔽验收流程，"
    "确保工程按期保质完成并通过竣工验收，交付完整的技术资料与培训服务。"
)
PARA_B = (
    "本工程综合布线系统采用六类非屏蔽双绞线，信息点面板按楼层弱电间集中配线，"
    "机柜内配线架与交换机预留百分之二十余量，链路测试执行国家标准，"
    "竣工时提交完整的点位表与测试报告，并对运维人员进行现场培训。"
)


def _write(tmp_path, name, *paragraphs):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(paragraphs), encoding="utf-8")
    return path


def test_split_paragraphs_and_normalize():
    text = "  第一段第一行\n第一段第二行  \n\n\n  第二段  \n"
    assert dedup.split_paragraphs(text) == ["第一段第一行 第一段第二行", "第二段"]
    assert dedup.split_paragraphs("") == []
    assert dedup.normalize(" Hello，World！ 的\n测试。 ") == "helloworld的测试"
    assert dedup.similarity(PARA_A, PARA_A) == 1.0
    assert dedup.similarity(PARA_A, PARA_B) < 0.5


def test_collect_summary_sorted_and_counted(tmp_path):
    _write(tmp_path, "b.txt", PARA_A)
    _write(tmp_path, "sub/a.md", PARA_B, "短段落。")
    report = dedup.collect(tmp_path)
    assert [f["name"] for f in report["files"]] == ["a.md", "b.txt"]
    assert report["total_paragraphs"] == 2
    by_name = {f["name"]: f for f in report["files"]}
    assert by_name["a.md"]["paragraphs"] == 1
    assert by_name["a.md"]["path"] == "sub/a.md"
    assert by_name["b.txt"]["chars"] == len(PARA_A)
    assert report["skipped"] == []


def test_identical_paragraph_cross_file_hit(tmp_path):
    _write(tmp_path, "a.txt", PARA_A)
    _write(tmp_path, "b.txt", PARA_A)
    report = dedup.compare(tmp_path)
    assert len(report["pairs"]) == 1
    pair = report["pairs"][0]
    assert pair["similarity"] == 1.0
    assert {pair["a"]["file"], pair["b"]["file"]} == {"a.txt", "b.txt"}
    assert pair["a"]["index"] == 0 and pair["b"]["index"] == 0
    assert pair["a"]["excerpt"] == PARA_A[:80] + "…"
    assert report["same_file_pairs"] == []
    assert report["stats"]["cross_pairs"] == 1
    assert report["stats"]["coverage"] == 1.0
    assert report["stats"]["truncated"] is False


def test_distinct_text_no_hit(tmp_path):
    _write(tmp_path, "a.txt", PARA_A)
    _write(tmp_path, "b.txt", PARA_B)
    report = dedup.compare(tmp_path)
    assert report["pairs"] == []
    assert report["same_file_pairs"] == []


def test_short_paragraphs_ignored(tmp_path):
    short = "完全相同但不足八十字的短段落。"
    _write(tmp_path, "a.txt", short)
    _write(tmp_path, "b.txt", short)
    report = dedup.compare(tmp_path)
    assert report["pairs"] == []
    assert report["stats"]["paragraphs"] == 0
    assert report["stats"]["coverage"] == 0.0
    # 放宽 min_chars 后应命中
    relaxed = dedup.compare(tmp_path, min_chars=5)
    assert len(relaxed["pairs"]) == 1


def test_threshold_boundary(tmp_path):
    variant = PARA_A.replace("三级检查制度", "四级检查制度").replace("竣工验收", "完工核验")
    score = dedup.similarity(PARA_A, variant)
    assert 0.5 < score < 1.0
    _write(tmp_path, "a.txt", PARA_A)
    _write(tmp_path, "b.txt", variant)
    above = dedup.compare(tmp_path, threshold=score + 1e-6)
    assert above["pairs"] == []
    below = dedup.compare(tmp_path, threshold=score - 1e-6)
    assert len(below["pairs"]) == 1
    assert below["pairs"][0]["similarity"] == round(score, 3)


def test_same_file_duplicate(tmp_path):
    _write(tmp_path, "a.txt", PARA_A, PARA_A)
    report = dedup.compare(tmp_path)
    assert report["pairs"] == []
    assert len(report["same_file_pairs"]) == 1
    pair = report["same_file_pairs"][0]
    assert pair["a"]["file"] == pair["b"]["file"] == "a.txt"
    assert (pair["a"]["index"], pair["b"]["index"]) == (0, 1)
    assert report["stats"]["same_file_pairs"] == 1


def test_docx_skipped_without_python_docx(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "docx", None)
    (tmp_path / "fake.docx").write_bytes(b"not a real docx")
    _write(tmp_path, "a.txt", PARA_A)
    report = dedup.compare(tmp_path)
    assert len(report["skipped"]) == 1
    entry = report["skipped"][0]
    assert entry["name"] == "fake.docx"
    assert "python-docx" in entry["reason"]
    by_name = {f["name"]: f for f in report["files"]}
    assert by_name["fake.docx"]["skipped"] is True
    assert by_name["fake.docx"]["paragraphs"] == 0
    assert by_name["a.txt"]["skipped"] is False


def test_docx_read_when_available(tmp_path):
    pytest.importorskip("docx")
    from docx import Document

    document = Document()
    document.add_paragraph(PARA_A)
    document.add_paragraph("短段落。")
    document.save(str(tmp_path / "a.docx"))
    _write(tmp_path, "b.txt", PARA_A)
    report = dedup.compare(tmp_path)
    assert report["skipped"] == []
    assert len(report["pairs"]) == 1
    assert {report["pairs"][0]["a"]["file"], report["pairs"][0]["b"]["file"]} == {
        "a.docx",
        "b.txt",
    }


def test_max_pairs_truncation(tmp_path):
    for name in ("a.txt", "b.txt", "c.txt"):
        _write(tmp_path, name, PARA_A)
    report = dedup.compare(tmp_path, max_pairs=2)
    assert len(report["pairs"]) == 2
    assert report["stats"]["truncated"] is True
    sims = [p["similarity"] for p in report["pairs"]]
    assert sims == sorted(sims, reverse=True)
    full = dedup.compare(tmp_path)
    assert len(full["pairs"]) == 3
    assert full["stats"]["truncated"] is False


def test_top_three_per_file_pair(tmp_path):
    _write(tmp_path, "a.txt", PARA_A, PARA_A, PARA_A, PARA_A)
    _write(tmp_path, "b.txt", PARA_A, PARA_A, PARA_A, PARA_A)
    report = dedup.compare(tmp_path)
    assert len(report["pairs"]) == 3
    assert all(p["a"]["file"] == "a.txt" and p["b"]["file"] == "b.txt" for p in report["pairs"])
    assert report["same_file_pairs"]  # 同文件内重复另行统计


def test_max_paragraphs_truncation_and_coverage(tmp_path):
    _write(tmp_path, "a.txt", PARA_A)
    _write(tmp_path, "b.txt", PARA_A)
    report = dedup.compare(tmp_path, max_paragraphs=1)
    assert report["stats"]["paragraphs"] == 2
    assert report["stats"]["coverage"] == 0.5
    assert report["stats"]["truncated"] is True
    assert report["pairs"] == []


def test_render_contains_headers(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "docx", None)
    (tmp_path / "fake.docx").write_bytes(b"x")
    _write(tmp_path, "a.txt", PARA_A)
    _write(tmp_path, "b.txt", PARA_A)
    markdown = dedup.render(dedup.compare(tmp_path))
    assert "# 跨文件查重报告" in markdown
    assert "| 指标 | 数值 |" in markdown
    assert "| 文件数 |" in markdown and "| 跨文件重复对 |" in markdown
    assert "| 相似度 |" in markdown
    assert "a.txt:0" in markdown and "b.txt:0" in markdown
    assert "## 跳过文件" in markdown
    assert "fake.docx" in markdown and "python-docx" in markdown


def test_to_json_and_write_json(tmp_path):
    _write(tmp_path, "a.txt", PARA_A)
    _write(tmp_path, "b.txt", PARA_A)
    report = dedup.compare(tmp_path)
    parsed = json.loads(dedup.to_json(report))
    assert parsed["stats"]["cross_pairs"] == len(parsed["pairs"])
    target = tmp_path / "out" / "report.json"
    dedup.write_json(report, target)
    assert json.loads(target.read_text(encoding="utf-8")) == report


def test_cli_missing_dir_exit_1(tmp_path):
    from typer.testing import CliRunner

    from bidcraft.cli import app

    result = CliRunner().invoke(app, ["dedup", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "目录不存在" in result.output


def test_cli_json_and_output(tmp_path):
    from typer.testing import CliRunner

    from bidcraft.cli import app

    _write(tmp_path, "a.txt", PARA_A)
    _write(tmp_path, "b.txt", PARA_A)
    target = tmp_path / "report.json"
    result = CliRunner().invoke(
        app, ["dedup", str(tmp_path), "--json", "-o", str(target)]
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output[: result.output.index("报告已写入")])
    assert parsed["stats"]["cross_pairs"] == 1
    assert json.loads(target.read_text(encoding="utf-8"))["root"] == str(tmp_path)
    markdown = CliRunner().invoke(app, ["dedup", str(tmp_path)])
    assert markdown.exit_code == 0
    assert "| 相似度 |" in markdown.output
