"""PDF delivery via a locally installed LibreOffice (no cloud, no extra Python deps).

A plain ``--convert-to pdf`` run leaves the Word TOC field empty. When a Python
interpreter with ``uno`` is available (typically the system Python shipped with
LibreOffice), we drive a headless instance over UNO, refresh fields, update the
TOC and only then export. Otherwise we fall back to a plain conversion.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

UNO_HELPER = Path(__file__).with_name("_uno_pdf.py")


class PdfExportError(RuntimeError):
    pass


def _soffice() -> str:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    raise PdfExportError("未找到 LibreOffice（soffice），无法转换 PDF；可先导出 DOCX 后手动转换")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _uno_python() -> str | None:
    candidates = [
        os.environ.get("BIDCRAFT_UNO_PYTHON", ""),
        "/usr/bin/python3",
        "/usr/lib/libreoffice/program/python",
        sys.executable,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            probe = subprocess.run(
                [candidate, "-c", "import uno"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def _export_with_uno(source: Path, target: Path, *, timeout: int) -> bool:
    python = _uno_python()
    if python is None or not UNO_HELPER.exists():
        return False
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="bidcraft-lo-") as tmp:
        profile = Path(tmp) / "profile"
        server = subprocess.Popen(
            [
                _soffice(),
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--invisible",
                "--nologo",
                "--norestore",
                "--nodefault",
                f"--accept=socket,host=127.0.0.1,port={port};urp;",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            result = subprocess.run(
                [python, str(UNO_HELPER), str(source), str(target), str(port)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.SubprocessError:
            result = None
        finally:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=8)
    if result is None or result.returncode != 0 or not target.exists():
        return False
    return target.stat().st_size > 0


def _export_plain(source: Path, target: Path, *, timeout: int) -> None:
    with tempfile.TemporaryDirectory(prefix="bidcraft-lo-") as tmp:
        command = [
            _soffice(),
            f"-env:UserInstallation=file://{Path(tmp) / 'profile'}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(target.parent),
            str(source),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        produced = target.parent / f"{source.stem}.pdf"
        if result.returncode != 0 or not produced.exists():
            detail = (result.stderr or result.stdout or "").strip()[-500:]
            raise PdfExportError(f"LibreOffice 转换失败（exit {result.returncode}）：{detail}")
        if produced != target:
            shutil.move(str(produced), str(target))


def export_pdf(
    docx_path: str | Path,
    pdf_path: str | Path | None = None,
    *,
    timeout: int = 240,
    update_toc: bool = True,
) -> Path:
    """Convert an existing DOCX to PDF using headless LibreOffice."""
    source = Path(docx_path)
    if not source.exists():
        raise PdfExportError(f"DOCX 不存在: {source}")
    target = Path(pdf_path) if pdf_path else source.with_suffix(".pdf")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    started = time.monotonic()
    if update_toc and _export_with_uno(source, target, timeout=timeout):
        return target
    remaining = max(30, timeout - int(time.monotonic() - started))
    _export_plain(source, target, timeout=remaining)
    return target
