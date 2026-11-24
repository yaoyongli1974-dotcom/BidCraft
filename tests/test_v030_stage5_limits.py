"""v0.30 stage 5 part B：上传解压炸弹防护、解析产物上限、页面查找预计算。

G2 只信 ZIP 目录里声明的未压缩体积，不实际解压；G3 采用确定性截断 +
显式标记（正文标记 / parse_note / notes / 审计日志），解析照常成功；
G4 每轮 extract() 只 compact 每页一次。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from bidcraft.config import (
    DEFAULT_MAX_PARSED_CHARS,
    DEFAULT_MAX_PARSED_PAGES,
    DEFAULT_MAX_PARSED_TABLES,
    DEFAULT_MAX_UNCOMPRESSED_UPLOAD_BYTES,
    Settings,
    settings,
)
from bidcraft.parsing import extract as extract_module
from bidcraft.parsing.loader import OversizedUploadError, ParsedDocument, Table, load_document
from bidcraft.rules.loader import load_package


def _small_docx(path: Path) -> Path:
    from docx import Document

    document = Document()
    document.add_paragraph("项目名称：正常小文档")
    document.add_paragraph("项目编号：ZB-2026-0501")
    document.save(str(path))
    return path


def _sparse_bomb_docx(path: Path, declared_size: int = 2 * 1024**3) -> Path:
    """真的写一个 DOCX，然后把 ZIP 中央目录的未压缩体积改成数 GB。

    压缩数据仍然只有几十 KB；这是典型的"声明体积"解压炸弹，防护必须在
    python-docx 打开之前生效，否则 worker 会按声明size分配内存而 OOM。
    """

    data = bytearray(_small_docx(path).read_bytes())
    patched = 0
    offset = 0
    while True:
        index = data.find(b"PK\x01\x02", offset)
        if index < 0:
            break
        struct.pack_into("<I", data, index + 24, declared_size)
        patched += 1
        offset = index + 4
    assert patched >= 1
    path.write_bytes(data)
    return path


def test_sparse_zip_bomb_docx_rejected_before_parsing(tmp_path):
    bomb = _sparse_bomb_docx(tmp_path / "bomb.docx")
    with pytest.raises(ValueError, match="解压后体积|BIDCRAFT_MAX_UNCOMPRESSED_UPLOAD_BYTES"):
        load_document(bomb)


def test_normal_small_docx_still_loads(tmp_path):
    parsed = load_document(_small_docx(tmp_path / "ok.docx"))
    assert "项目名称：正常小文档" in parsed.text
    assert parsed.meta["docx_engine"] == "python-docx"


def test_plain_text_size_cap_uses_same_setting(tmp_path, monkeypatch):
    path = tmp_path / "huge.txt"
    path.write_text("项目编号：ABC\n" * 40, encoding="utf-8")
    monkeypatch.setattr(settings, "max_uncompressed_upload_bytes", 128)
    with pytest.raises(OversizedUploadError, match="文本文件|安全上限"):
        load_document(path)
    assert isinstance(OversizedUploadError("x"), ValueError)


def test_settings_limits_defaults_env_overrides_and_clamping(monkeypatch):
    assert DEFAULT_MAX_UNCOMPRESSED_UPLOAD_BYTES == 512 * 1024 * 1024
    assert DEFAULT_MAX_PARSED_CHARS == 8_000_000
    assert DEFAULT_MAX_PARSED_TABLES == 2_000
    assert DEFAULT_MAX_PARSED_PAGES == 5_000
    default = Settings()
    assert default.max_uncompressed_upload_bytes == DEFAULT_MAX_UNCOMPRESSED_UPLOAD_BYTES
    assert default.max_parsed_chars == DEFAULT_MAX_PARSED_CHARS
    assert default.max_parsed_tables == DEFAULT_MAX_PARSED_TABLES
    assert default.max_parsed_pages == DEFAULT_MAX_PARSED_PAGES

    monkeypatch.setenv("BIDCRAFT_MAX_UNCOMPRESSED_UPLOAD_BYTES", "4096")
    monkeypatch.setenv("BIDCRAFT_MAX_PARSED_CHARS", "500")
    monkeypatch.setenv("BIDCRAFT_MAX_PARSED_TABLES", "7")
    monkeypatch.setenv("BIDCRAFT_MAX_PARSED_PAGES", "9")
    custom = Settings()
    assert custom.max_uncompressed_upload_bytes == 4096
    assert custom.max_parsed_chars == 500
    assert custom.max_parsed_tables == 7
    assert custom.max_parsed_pages == 9

    for name in (
        "BIDCRAFT_MAX_UNCOMPRESSED_UPLOAD_BYTES",
        "BIDCRAFT_MAX_PARSED_CHARS",
        "BIDCRAFT_MAX_PARSED_TABLES",
        "BIDCRAFT_MAX_PARSED_PAGES",
    ):
        monkeypatch.setenv(name, "-5")
    clamped = Settings()
    assert clamped.max_uncompressed_upload_bytes == 1
    assert clamped.max_parsed_chars == 1
    assert clamped.max_parsed_tables == 1
    assert clamped.max_parsed_pages == 1


def _big_document(text: str = "甲" * 5_000) -> ParsedDocument:
    return ParsedDocument(
        text=text,
        pages=[f"第{i}页 内容" for i in range(1, 9)],
        tables=[Table(page=i, rows=[["名称", "数量"], ["面板", "10"]]) for i in range(1, 6)],
        meta={"source": "big.txt", "docx_engine": "test"},
    )


def test_parse_limits_truncate_with_marker_and_audit_payload(db, tmp_path, monkeypatch):
    """超限产物在 extract()/落库前被截断，标记进正文、parse_note 与 notes。"""

    from bidcraft import pipeline

    monkeypatch.setattr(settings, "max_parsed_chars", 120)
    monkeypatch.setattr(settings, "max_parsed_pages", 3)
    monkeypatch.setattr(settings, "max_parsed_tables", 1)
    monkeypatch.setattr(pipeline, "load_document", lambda _path: _big_document())

    path = tmp_path / "big.txt"
    path.write_text("占位", encoding="utf-8")
    ledger, info, updates = pipeline.parse_documents(
        "weakcurrent-cabling",
        [{"id": "src-1", "filename": "big.txt", "stored_path": str(path)}],
    )
    assert info["truncated"] is True
    source_warnings = [warning for warning in info["parse_warnings"] if "big.txt" in warning]
    assert len(source_warnings) == 3
    assert any("正文" in warning and "120" in warning for warning in source_warnings)
    assert any("页数" in warning for warning in source_warnings)
    assert any("表格" in warning for warning in source_warnings)
    assert any("项目汇总" in warning for warning in info["parse_warnings"])

    stored_text = updates["src-1"]["text_content"]
    assert len(stored_text) < 300  # 没有 GB 级正文进入 SourceFile
    assert stored_text.count("【解析截断") == 3
    assert "120字符" in stored_text and "3页" in stored_text and "1个表格" in stored_text
    assert updates["src-1"]["page_count"] == 3
    assert "parse_limits=" in updates["src-1"]["parse_note"]

    from bidcraft.db import session_scope
    from bidcraft.models import Project, SourceFile

    with session_scope() as session:
        project = Project(name="截断测试", package="weakcurrent-cabling")
        session.add(project)
        session.flush()
        row = SourceFile(
            project_id=project.id,
            role="tender",
            filename="big.txt",
            stored_path=str(path),
            parse_state="pending",
        )
        row.id = "src-1"
        session.add(row)
        session.flush()
        project_id = project.id
        pipeline.persist_parse(session, project.id, ledger, updates)
    with session_scope() as session:
        saved = session.get(SourceFile, "src-1")
        assert saved is not None and saved.project_id == project_id
        assert len(saved.text_content) < 300
        assert "【解析截断" in saved.text_content
        assert "parse_limits=" in saved.parse_note


def test_aggregate_text_cap_applies_after_join(db, tmp_path, monkeypatch):
    """每份来源各自未超限，拼起来超限也要截，并记录汇总警告。"""

    from bidcraft import pipeline

    monkeypatch.setattr(settings, "max_parsed_chars", 200)
    monkeypatch.setattr(settings, "max_parsed_pages", 50)
    monkeypatch.setattr(settings, "max_parsed_tables", 50)
    documents = [
        ParsedDocument(text="乙" * 150, pages=["第一份"], meta={"source": "one.txt"}),
        ParsedDocument(text="丙" * 150, pages=["第二份"], meta={"source": "two.txt"}),
    ]
    monkeypatch.setattr(pipeline, "load_document", lambda _path: documents.pop(0))

    sources = []
    for name in ("one.txt", "two.txt"):
        path = tmp_path / name
        path.write_text("占位", encoding="utf-8")
        sources.append({"id": f"src-{name}", "filename": name, "stored_path": str(path)})
    _ledger, info, _updates = pipeline.parse_documents("weakcurrent-cabling", sources)
    assert any("项目汇总" in warning for warning in info["parse_warnings"])
    assert any("项目汇总" in note for note in info["notes"])


def test_extract_compacts_each_page_once(monkeypatch):
    """页面紧凑化每页恰好一次，不随条款/候选数线性增长。"""

    package = load_package("weakcurrent-cabling")
    pages = [f"第{i}页内容 ★ 投标人必须满足第{i}项资格要求" for i in range(1, 7)]
    text = "第三章 资格要求\n" + "\n".join(
        f"★ 投标人必须满足第{i}项资格要求" for i in range(1, 7)
    )
    real_compact = extract_module.compact
    calls: list[str] = []

    def _counting_compact(value: str) -> str:
        calls.append(value)
        return real_compact(value)

    monkeypatch.setattr(extract_module, "compact", _counting_compact)
    result = extract_module.extract(text, package, pages=pages)
    assert len(result.requirements) >= 6  # 确实走过多条引用定位
    assert calls == pages  # 每页一次，顺序一致；引用文本没有再调用 compact


def _api_client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    login = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert login.status_code == 200, login.text
    return client


def test_upload_bomb_maps_to_http_400(db, tmp_path):
    client = _api_client()
    project_id = client.post(
        "/projects", json={"name": "炸弹测试", "package": "weakcurrent-cabling"}
    ).json()["id"]
    bomb = _sparse_bomb_docx(tmp_path / "bomb.docx")
    with bomb.open("rb") as handle:
        response = client.post(
            f"/projects/{project_id}/sources",
            files={"file": ("bomb.docx", handle, "application/octet-stream")},
        )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "解压后体积" in detail
    assert "BIDCRAFT_MAX_UNCOMPRESSED_UPLOAD_BYTES" in detail
    assert client.get(f"/projects/{project_id}/sources").json() == []


def test_upload_truncation_visible_in_notes_and_audit(db, tmp_path, monkeypatch):
    from sqlalchemy import select

    from bidcraft import pipeline
    from bidcraft.models import AuditLog

    monkeypatch.setattr(settings, "max_parsed_chars", 80)
    monkeypatch.setattr(settings, "max_parsed_pages", 2)
    monkeypatch.setattr(settings, "max_parsed_tables", 1)
    monkeypatch.setattr(pipeline, "load_document", lambda _path: _big_document(text="丁" * 400))

    client = _api_client()
    project_id = client.post(
        "/projects", json={"name": "截断接口测试", "package": "weakcurrent-cabling"}
    ).json()["id"]
    path = tmp_path / "big.txt"
    path.write_text("占位", encoding="utf-8")
    with path.open("rb") as handle:
        response = client.post(
            f"/projects/{project_id}/sources", files={"file": ("big.txt", handle, "text/plain")}
        )
    assert response.status_code == 200, response.text
    notes = response.json()["notes"]
    assert any("超过上限 80" in note for note in notes)

    from bidcraft.db import session_scope

    with session_scope() as session:
        actions = list(
            session.scalars(select(AuditLog.action).where(AuditLog.project_id == project_id))
        )
        assert "source.parse_truncated" in actions
        detail = next(
            iter(
                session.scalars(
                    select(AuditLog.detail).where(
                        AuditLog.project_id == project_id,
                        AuditLog.action == "source.parse_truncated",
                    )
                )
            )
        )
    assert any("80" in warning for warning in detail["warnings"])


def test_reparse_bomb_returns_400(db, tmp_path, monkeypatch):
    """同一防护在 /parse 也走 ValueError → 400，不放宽成静默 failed。"""

    project_id = _upload_small_docx_project(tmp_path)
    monkeypatch.setattr(settings, "max_uncompressed_upload_bytes", 100)
    client = _api_client()
    response = client.post(f"/projects/{project_id}/parse")
    assert response.status_code == 400, response.text
    assert "解压后体积" in response.json()["detail"]


def test_reparse_truncation_audit(db, tmp_path, monkeypatch):
    from sqlalchemy import select

    from bidcraft import pipeline
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    project_id = _upload_small_docx_project(tmp_path)
    monkeypatch.setattr(settings, "max_parsed_chars", 80)
    monkeypatch.setattr(pipeline, "load_document", lambda _path: _big_document(text="戊" * 400))
    client = _api_client()
    response = client.post(f"/projects/{project_id}/parse")
    assert response.status_code == 200, response.text
    assert any("超过上限 80" in note for note in response.json()["notes"])
    with session_scope() as session:
        actions = list(
            session.scalars(select(AuditLog.action).where(AuditLog.project_id == project_id))
        )
    assert "parse.truncated" in actions


def _upload_small_docx_project(tmp_path: Path) -> str:
    """先正常上传一份小 DOCX，得到可重解析的项目。"""

    client = _api_client()
    project_id = client.post(
        "/projects", json={"name": "重解析测试", "package": "weakcurrent-cabling"}
    ).json()["id"]
    docx = _small_docx(tmp_path / "seeded.docx")
    with docx.open("rb") as handle:
        response = client.post(
            f"/projects/{project_id}/sources",
            files={"file": ("seeded.docx", handle, "application/octet-stream")},
        )
    assert response.status_code == 200, response.text
    return project_id
