from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings


class OcrError(RuntimeError):
    """OCR 执行失败。"""


class OcrUnavailable(OcrError):
    """本机缺少 OCR 命令或语言包。"""


@dataclass(frozen=True)
class OcrProbe:
    available: bool
    tesseract: str | None = None
    pdftoppm: str | None = None
    languages: tuple[str, ...] = ()
    requested: str = ""
    warning: str | None = None
    reason: str | None = None


@dataclass
class OcrResult:
    pages: list[str]
    page_numbers: list[int]
    languages: tuple[str, ...]
    warnings: list[str] = field(default_factory=list)


def _decode(raw: bytes | None) -> str:
    return (raw or b"").decode("utf-8", errors="replace").strip()


def _which(command: str) -> str | None:
    command = (command or "").strip()
    return shutil.which(command) if command else None


def _list_languages(tesseract: str, timeout: float) -> list[str]:
    result = _run([tesseract, "--list-langs"], timeout, "tesseract --list-langs")
    if result.returncode != 0:
        detail = _decode(result.stderr) or _decode(result.stdout)
        raise OcrError(f"tesseract --list-langs 失败：{detail[:200]}")
    raw = f"{_decode(result.stdout)}\n{_decode(result.stderr)}"
    languages = []
    for line in raw.splitlines():
        item = line.strip()
        if item and " " not in item and ":" not in item:
            languages.append(item)
    return languages


def probe_ocr() -> OcrProbe:
    """Detect the optional pdftoppm + tesseract pipeline without running OCR."""
    requested = (settings.ocr_languages or "eng").strip()
    if not settings.ocr_enabled:
        return OcrProbe(
            available=False,
            requested=requested,
            reason="OCR 已禁用（BIDCRAFT_OCR=0）",
        )

    tesseract = _which(settings.ocr_tesseract_cmd)
    pdftoppm = _which(settings.ocr_pdftoppm_cmd)
    if tesseract is None:
        return OcrProbe(
            available=False,
            pdftoppm=pdftoppm,
            requested=requested,
            reason=f"未找到 tesseract 可执行文件（BIDCRAFT_TESSERACT={settings.ocr_tesseract_cmd}）",
        )
    if pdftoppm is None:
        return OcrProbe(
            available=False,
            tesseract=tesseract,
            requested=requested,
            reason=f"未找到 pdftoppm 可执行文件（BIDCRAFT_PDFTOPPM={settings.ocr_pdftoppm_cmd}）",
        )

    try:
        available_languages = _list_languages(tesseract, settings.ocr_timeout)
    except OcrError as exc:
        return OcrProbe(
            available=False,
            tesseract=tesseract,
            pdftoppm=pdftoppm,
            requested=requested,
            reason=str(exc),
        )

    wanted = [item.strip() for item in requested.split("+") if item.strip()]
    usable = [item for item in wanted if item in available_languages]
    missing = [item for item in wanted if item not in available_languages]
    warning = None
    if missing and usable:
        warning = f"缺少语言包 {'+'.join(missing)}，已使用 {'+'.join(usable)}"
    elif not usable:
        if "eng" in available_languages:
            usable = ["eng"]
            warning = f"缺少语言包 {'+'.join(wanted) or requested}，已回退 eng"
        else:
            return OcrProbe(
                available=False,
                tesseract=tesseract,
                pdftoppm=pdftoppm,
                requested=requested,
                reason=(
                    f"tesseract 缺少语言包 {'+'.join(wanted) or requested}；"
                    f"可用：{', '.join(available_languages) or '无'}"
                ),
            )

    return OcrProbe(
        available=True,
        tesseract=tesseract,
        pdftoppm=pdftoppm,
        languages=tuple(usable),
        requested=requested,
        warning=warning,
    )


def _run(command: list[str], timeout: float, label: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise OcrUnavailable(f"找不到 {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"{label}超时（{timeout:g} 秒）") from exc
    except OSError as exc:
        raise OcrError(f"{label}无法执行：{exc}") from exc


def _render_page(
    pdf_path: Path,
    page: int,
    prefix: Path,
    probe: OcrProbe,
    timeout: float,
) -> Path:
    if probe.pdftoppm is None:  # pragma: no cover - guarded by probe
        raise OcrUnavailable("未找到 pdftoppm")
    command = [
        probe.pdftoppm,
        "-f",
        str(page),
        "-l",
        str(page),
        "-r",
        str(settings.ocr_dpi),
        "-png",
        "-singlefile",
        str(pdf_path),
        str(prefix),
    ]
    result = _run(command, timeout, f"pdftoppm 第 {page} 页渲染")
    if result.returncode != 0:
        detail = _decode(result.stderr) or _decode(result.stdout)
        raise OcrError(f"pdftoppm 第 {page} 页渲染失败：{detail[:200]}")
    image = prefix.with_suffix(".png")
    if not image.is_file():
        matches = sorted(prefix.parent.glob(f"{prefix.name}*.png"))
        if not matches:  # pragma: no cover - pdftoppm normally writes the image
            raise OcrError(f"pdftoppm 第 {page} 页未生成图片")
        image = matches[0]
    return image


def _render_all(
    pdf_path: Path,
    prefix: Path,
    probe: OcrProbe,
    timeout: float,
    max_pages: int,
) -> None:
    if probe.pdftoppm is None:  # pragma: no cover - guarded by probe
        raise OcrUnavailable("未找到 pdftoppm")
    command = [probe.pdftoppm, "-png", "-r", str(settings.ocr_dpi)]
    if max_pages > 0:
        command.extend(["-l", str(max_pages)])
    command.extend([str(pdf_path), str(prefix)])
    result = _run(command, timeout, "pdftoppm 渲染")
    if result.returncode != 0:
        detail = _decode(result.stderr) or _decode(result.stdout)
        raise OcrError(f"pdftoppm 渲染失败：{detail[:200]}")


def _collect_images(prefix: Path) -> list[tuple[int, Path]]:
    images: list[tuple[int, Path]] = []
    for index, path in enumerate(sorted(prefix.parent.glob(f"{prefix.name}-*.png")), start=1):
        match = re.search(r"-(\d+)\.png$", path.name)
        images.append((int(match.group(1)) if match else index, path))
    return sorted(images)


def _ocr_image(
    image: Path,
    probe: OcrProbe,
    timeout: float,
) -> str:
    if probe.tesseract is None:  # pragma: no cover - guarded by probe
        raise OcrUnavailable("未找到 tesseract")
    command = [
        probe.tesseract,
        str(image),
        "stdout",
        "-l",
        "+".join(probe.languages),
        "--dpi",
        str(settings.ocr_dpi),
    ]
    result = _run(command, timeout, "tesseract 识别")
    if result.returncode != 0:
        detail = _decode(result.stderr) or _decode(result.stdout)
        raise OcrError(f"tesseract 识别失败：{detail[:200]}")
    text = _decode(result.stdout).replace("\x0c", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ocr_pdf(
    pdf_path: str | Path,
    page_numbers: list[int] | None = None,
    *,
    probe: OcrProbe | None = None,
) -> OcrResult:
    """OCR selected PDF pages (or every page when ``page_numbers`` is None)."""
    path = Path(pdf_path)
    detected = probe or probe_ocr()
    if not detected.available:
        raise OcrUnavailable(detected.reason or "OCR 不可用")

    warnings = [detected.warning] if detected.warning else []
    requested = None
    if page_numbers:
        requested = sorted({int(page) for page in page_numbers if int(page) > 0})
        if not requested:
            requested = None

    cap = settings.ocr_max_pages
    if requested is not None and cap and len(requested) > cap:
        warnings.append(f"清单/招标文件页数较多，仅识别前 {cap} 页")
        requested = requested[:cap]

    pages: list[str] = []
    used: list[int] = []
    with tempfile.TemporaryDirectory(prefix="bidcraft-ocr-") as tmp:
        tmp_path = Path(tmp)
        if requested is None:
            prefix = tmp_path / "page"
            _render_all(path, prefix, detected, settings.ocr_timeout, cap)
            images = _collect_images(prefix)
            if not images:
                raise OcrError("pdftoppm 未生成任何页面图片")
            if cap and len(images) > cap:
                warnings.append(f"清单/招标文件页数较多，仅识别前 {cap} 页")
                images = images[:cap]
            selected = images
        else:
            selected = []
            for page in requested:
                selected.append((page, None))

        for page, image in selected:
            try:
                if image is None:
                    image = _render_page(path, page, tmp_path / f"page-{page}", detected, settings.ocr_timeout)
                text = _ocr_image(image, detected, settings.ocr_timeout)
            except OcrError as exc:
                warnings.append(f"第 {page} 页 OCR 失败：{exc}")
                text = ""
            if not text:
                warnings.append(f"第 {page} 页未识别出文字")
            pages.append(text)
            used.append(page)

    return OcrResult(
        pages=pages,
        page_numbers=used,
        languages=detected.languages,
        warnings=warnings,
    )
