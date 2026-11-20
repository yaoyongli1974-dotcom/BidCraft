"""桌面客户端归档后的「冻结配方」护栏。

桌面端冻结于 v0.6.3，不再演进；但依赖必须固化在 pyproject/uv.lock 里，
且 CI 默认不得把安装包重新附加到 Release（那等于恢复对外分发）。
这些用例守住这两条决定，避免半年后重建时撞墙或误发布。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_frozen_desktop_and_build_extras_declared():
    project = _pyproject()["project"]
    extras = project["optional-dependencies"]
    assert {"desktop", "build"} <= set(extras)

    desktop = " ".join(extras["desktop"]).lower()
    for package in ("qtpy", "pyside6", "pywebview"):
        assert package in desktop, f"desktop extra 缺少 {package}"
    assert "pyinstaller" in " ".join(extras["build"]).lower()

    # 归档说明必须留在配方旁边，否则后人会以为它还在演进
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "桌面客户端已归档" in text
    assert "uv.lock" in text


def test_lock_pins_desktop_dependencies():
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    for package in ("pyside6", "qtpy", "pywebview", "pyinstaller", "shiboken6"):
        assert f'name = "{package}"' in lock, f"uv.lock 未锁定 {package}"


def test_metadata_drops_weak_current_positioning():
    project = _pyproject()["project"]
    assert "弱电" not in project["description"]
    assert "weak-current" not in project["keywords"]
    assert "boq" in project["keywords"]
    assert "compliance" in project["keywords"]
    assert (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0] == (
        "# BidCraft · 投标文件编制系统"
    )


def test_build_script_checks_all_four_dependencies():
    script = (ROOT / "packaging" / "build_pyinstaller.sh").read_text(encoding="utf-8")
    for module in ("PyInstaller", "qtpy", "webview", "PySide6"):
        assert module in script, f"构建脚本未检查 {module}"
    assert "uv sync --frozen --extra desktop --extra build" in script


def test_release_workflow_is_manual_and_does_not_republish_by_default():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
    )
    # PyYAML 会把裸 on 解析成布尔 True
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"workflow_dispatch"}, "客户端构建不得由 tag push 自动触发"
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert inputs["attach_to_release"]["default"] is False

    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if step.get("name") == "Attach to release":
                assert "inputs.attach_to_release" in step["if"]
            if step.get("name") == "Install Python deps":
                run = step["run"]
                assert "--extra desktop" in run and "--extra build" in run
                assert "uv pip install" not in run, "依赖应来自冻结配方，而非临时安装"
