from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from bidcraft import __version__
from bidcraft.db import session_scope
from bidcraft.mcp import TOOL_SPECS, handle_line, handle_message
from bidcraft.models import OutlineNode, Section
from bidcraft.pipeline import create_project

TOOL_NAMES = {
    "list_projects",
    "get_project",
    "get_ledger",
    "run_check",
    "export_document",
    "search_knowledge",
}


def _rpc(method: str, params: dict | None = None, *, request_id: int = 1) -> dict:
    message: dict = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return message


def _call(name: str, arguments: dict | None = None, *, readonly: bool = False):
    response = handle_message(
        _rpc("tools/call", {"name": name, "arguments": arguments or {}}),
        readonly=readonly,
    )
    assert response is not None
    result = response["result"]
    return result, result["content"][0]["text"]


def _payload(name: str, arguments: dict | None = None, *, readonly: bool = False):
    result, text = _call(name, arguments, readonly=readonly)
    assert result["isError"] is False, text
    return json.loads(text)


def _new_project(name: str = "MCP 测试项目") -> str:
    with session_scope() as db:
        project = create_project(db, name)
        return project.id


def _seed_chapter(project_id: str, body: str = "本方案采用 Cat6A 综合布线系统，满足招标要求。") -> str:
    with session_scope() as db:
        node = OutlineNode(project_id=project_id, heading="总体方案", position=1)
        db.add(node)
        db.flush()
        db.add(
            Section(
                node_id=node.id,
                body=body,
                status="written",
                words=len(body),
                review_status="confirmed",
            )
        )
        return node.id


def test_initialize_reports_protocol_and_server_info():
    response = handle_message(_rpc("initialize"))
    assert response["jsonrpc"] == "2.0"
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["serverInfo"] == {"name": "bidcraft-mcp", "version": __version__}


def test_ping_and_notifications():
    assert handle_message(_rpc("ping"))["result"] == {}
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None


def test_unknown_method_returns_32601():
    response = handle_message(_rpc("tools/unknown"))
    assert response["error"]["code"] == -32601


def test_bad_json_line_returns_32700_and_loop_continues():
    response = handle_line("{this is not json")
    assert response["error"]["code"] == -32700
    assert handle_line("") is None
    assert handle_line(json.dumps(_rpc("ping")))["result"] == {}


def test_tools_list_has_six_object_schemas():
    tools = handle_message(_rpc("tools/list"))["result"]["tools"]
    assert len(tools) == 6
    assert {tool["name"] for tool in tools} == TOOL_NAMES
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_list_projects_empty_then_created(db):
    assert _payload("list_projects") == []

    project_id = _new_project("某园区智能化工程")
    items = _payload("list_projects")
    assert [item["id"] for item in items] == [project_id]
    assert items[0]["name"] == "某园区智能化工程"
    assert items[0]["package"] == "weakcurrent-cabling"


def test_get_project_and_get_ledger(db):
    result, text = _call("get_project", {"ref": "不存在的项目"})
    assert result["isError"] is True
    assert "找不到" in text

    project_id = _new_project()
    project = _payload("get_project", {"ref": project_id})
    assert project["id"] == project_id
    assert project["name"] == "MCP 测试项目"
    assert "ledger_summary" in project
    assert isinstance(project["meta"], dict)

    ledger = _payload("get_ledger", {"ref": project_id})
    assert isinstance(ledger["text"], str)
    assert ledger["summary"]["fields_total"] == 0

    result, _text = _call("get_ledger", {"ref": "不存在的项目"})
    assert result["isError"] is True


def test_run_check_on_empty_project_and_readonly(db):
    project_id = _new_project()
    payload = _payload("run_check", {"ref": project_id})
    assert isinstance(payload["issues"], list)
    assert isinstance(payload["summary"], dict)
    assert "total" in payload["summary"]

    result, text = _call("run_check", {"ref": project_id}, readonly=True)
    assert result["isError"] is True
    assert "只读" in text


def test_export_markdown_contains_body(db):
    project_id = _new_project()
    _seed_chapter(project_id)

    payload = _payload("export_document", {"ref": project_id, "format": "md"})
    path = Path(payload["path"])
    assert payload["format"] == "md"
    assert path.name == "bid.md"
    assert path.is_file()
    assert "Cat6A 综合布线系统" in path.read_text(encoding="utf-8")


def test_export_docx_writes_pk_zip(db):
    project_id = _new_project()
    _seed_chapter(project_id)

    payload = _payload("export_document", {"ref": project_id, "format": "docx"})
    path = Path(payload["path"])
    assert path.name == "bid.docx"
    assert path.is_file()
    assert path.read_bytes()[:2] == b"PK"


def test_export_readonly_and_missing_body(db):
    project_id = _new_project()
    _seed_chapter(project_id)

    result, text = _call(
        "export_document", {"ref": project_id, "format": "md"}, readonly=True
    )
    assert result["isError"] is True
    assert "只读" in text

    empty_id = _new_project("空正文项目")
    result, text = _call("export_document", {"ref": empty_id, "format": "md"})
    assert result["isError"] is True
    assert "还没有正文" in text

    result, text = _call("export_document", {"ref": empty_id, "format": "pdf"})
    assert result["isError"] is True
    assert "PDF" in text


def test_export_xlsx_without_body_succeeds(db):
    project_id = _new_project()
    payload = _payload("export_document", {"ref": project_id, "format": "xlsx"})
    path = Path(payload["path"])
    assert path.name == "bid.xlsx"
    assert path.is_file()


def test_search_knowledge_without_kb_returns_empty(db):
    assert _payload("search_knowledge", {"query": "综合布线"}) == []
    assert _payload("search_knowledge", {"query": "综合布线", "limit": 99}) == []


def test_unknown_tool_is_error(db):
    result, text = _call("no_such_tool")
    assert result["isError"] is True
    assert "未知工具" in text


def test_stdio_subprocess_smoke(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["BIDCRAFT_HOME"] = str(tmp_path / "home")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root), env.get("PYTHONPATH", "")] if env.get("PYTHONPATH") else [str(root)]
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from bidcraft.mcp import serve_stdio; serve_stdio()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(root),
        env=env,
    )
    requests = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]
    )
    try:
        out, _err = proc.communicate(requests + "\n", timeout=90)
    finally:
        if proc.poll() is None:
            proc.kill()
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) >= 2
    init = json.loads(lines[0])
    tools = json.loads(lines[1])
    assert init["result"]["serverInfo"]["name"] == "bidcraft-mcp"
    assert len(tools["result"]["tools"]) == len(TOOL_SPECS) == 6
