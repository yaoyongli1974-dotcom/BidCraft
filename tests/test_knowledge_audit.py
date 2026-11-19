"""知识库素材体检与章节覆盖度对比。"""

from __future__ import annotations

import json
from pathlib import Path

from bidcraft import knowledge_audit as audit_mod


def _write(root: Path, name: str, text: str, encoding: str = "utf-8") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


def test_normalize_heading_strips_numbering():
    assert audit_mod.normalize_heading("第3章 施工组织与进度计划") == "施工组织与进度计划"
    assert audit_mod.normalize_heading("（二）质量保证") == "质量保证"
    assert audit_mod.normalize_heading("3.2 安全文明施工") == "安全文明施工"
    assert audit_mod.normalize_heading("  ## 售后服务 ") == "售后服务"
    assert audit_mod.normalize_heading("项目理解：需求分析") == "项目理解需求分析"


def test_extract_headings_mixed_forms_and_fences():
    text = """# 投标书范本
## 项目理解与需求分析
正文
### 1.1 点位规模
```bash
# 这是注释，不是标题
```
第2章 商务条款响应
一、施工组织
（三）售后服务
"""
    headings = audit_mod.extract_headings(text)
    assert "投标书范本" in headings
    assert "项目理解与需求分析" in headings
    assert "1.1 点位规模" in headings
    assert "这是注释，不是标题" not in headings
    assert "商务条款响应" in headings
    assert "施工组织" in headings
    assert "售后服务" in headings


def test_similarity_containment_and_jaccard():
    assert audit_mod.similarity("施工组织", "施工组织与进度计划") == 1.0
    assert audit_mod.similarity("售后服务", "售后服务与质保承诺") == 1.0
    assert audit_mod.similarity("售后服务", "商务条款") < 0.4
    assert 0.0 <= audit_mod.similarity("", "x") <= 1.0


def test_collect_and_duplicates(tmp_path: Path):
    _write(tmp_path, "a.md", "# 甲\n## 项目理解\n正文甲")
    _write(tmp_path, "sub/b.md", "# 甲\n## 项目理解\n正文甲")
    _write(tmp_path, "c.txt", "no headings here")
    _write(tmp_path, "gbk.txt", "## 质量保证\n中文内容", encoding="gb18030")
    _write(tmp_path, "skip.docx", "ignored")

    docs = audit_mod.collect(tmp_path)
    assert {doc.path for doc in docs} == {"a.md", "sub/b.md", "c.txt", "gbk.txt"}
    assert all(doc.error == "" for doc in docs)

    audit_mod.mark_duplicates(docs, tmp_path)
    duplicates = [doc.path for doc in docs if doc.duplicate_of]
    assert duplicates == ["sub/b.md"]
    assert docs[[doc.path for doc in docs].index("sub/b.md")].duplicate_of == "a.md"


def test_collect_flags_suspicious_and_missing_dir(tmp_path: Path):
    _write(tmp_path, "x.md", "版权所有，转载请联系公众号，来源网络 www.example.com")
    docs = audit_mod.collect(tmp_path)
    assert docs[0].suspicious
    assert "版权所有" in docs[0].suspicious
    assert audit_mod.collect(tmp_path / "nope") == []


def test_coverage_reports_matched_missing_extra(tmp_path: Path):
    _write(
        tmp_path,
        "doc.md",
        "# 投标书\n## 项目理解与需求分析\n## 商务条款响应与承诺\n## 施工组织与进度计划\n## 自创章节\n",
    )
    docs = audit_mod.collect(tmp_path)
    outline = [
        "项目理解与需求分析",
        "商务条款响应与承诺",
        "施工组织与进度计划",
        "信息点位表与材料清单",
    ]
    result = audit_mod.coverage(docs, outline)
    assert len(result["matched"]) == 3
    assert result["missing"] == ["信息点位表与材料清单"]
    assert "自创章节" in result["extra"]
    assert result["coverage"] == 0.75


def test_package_outlines_merges_extends():
    outlines = audit_mod.package_outlines()
    assert outlines["common-bid-facts"]
    cabling = outlines["weakcurrent-cabling"]
    # 综合布线是 replace 模式：只保留自身 12 条
    assert "综合布线系统总体设计" in cabling
    assert len(cabling) >= 10


def test_audit_and_render(tmp_path: Path):
    _write(tmp_path, "a.md", "# 投标书\n## 项目理解与需求分析\n## 商务条款响应与承诺")
    report = audit_mod.audit(tmp_path, threshold=0.7)
    assert report["files"] == 1
    assert report["total_chars"] > 0
    assert "weakcurrent-cabling" in report["packages"]
    text = audit_mod.render(report)
    assert "章节覆盖度" in text
    assert "weakcurrent-cabling" in text
    assert json.loads(audit_mod.to_json(report))["files"] == 1


def test_cli_json_and_output(tmp_path: Path):
    from typer.testing import CliRunner

    from bidcraft.cli import app

    _write(tmp_path, "a.md", "## 项目理解与需求分析")
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        app, ["knowledge-audit", str(tmp_path), "--json", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["files"] == 1


def test_cli_rejects_unknown_package(tmp_path: Path):
    from typer.testing import CliRunner

    from bidcraft.cli import app

    _write(tmp_path, "a.md", "## 项目理解")
    result = CliRunner().invoke(
        app, ["knowledge-audit", str(tmp_path), "--package", "no-such-pack"]
    )
    assert result.exit_code == 2
    assert "未知行业包" in result.output
