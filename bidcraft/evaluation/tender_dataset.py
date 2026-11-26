"""真实招标文件回归数据集框架。

本模块只做**数据集契约校验**：确认真实文件存在、指纹匹配、人工标注字段
完整，并给出可机器读取的就绪状态。它不执行判定，也不把合成夹具冒充真实
样本：每个条目必须显式标注 ``source='user-provided'``，且整文件分块扫描不得
出现合成标记；没有用户提供的数据集时 ``load_dataset`` 会返回明确的未就绪原因。

真实标书可能包含商业信息，因此 ``tests/fixtures/tender_real/`` 下的实际
文件被 `.gitignore` 排除；仓库只保留 README 与 ``dataset.example.json``。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

DATASET_SCHEMA = 1
DEFAULT_ROOT = Path("tests/fixtures/tender_real")
MANIFEST_NAME = "dataset.json"
REQUIRED_ANNOTATIONS = ("required_clauses", "required_values", "expected_verdicts")
SYNTHETIC_MARKERS = ("SYNTHETIC", "模拟", "示例数据")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_synthetic_marker(path: Path) -> bool:
    """分块扫描**整个文件**的合成标记；块间保留重叠，避免标记跨边界漏检。"""
    overlap = 64  # 大于最长标记 UTF-8 字节数，足以覆盖跨块的多字节字符。
    tail = b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            text = (tail + chunk).decode("utf-8", errors="ignore")
            if any(marker in text for marker in SYNTHETIC_MARKERS):
                return True
            tail = (tail + chunk)[-overlap:]
    return False


def dataset_path(root: Path | str = DEFAULT_ROOT) -> Path:
    return Path(root) / MANIFEST_NAME


def load_dataset(root: Path | str = DEFAULT_ROOT) -> dict[str, Any]:
    """Validate and return a normalized dataset manifest.

    Raises ``FileNotFoundError`` when the manifest is absent and ``ValueError``
    when its schema, entries or annotations are invalid.  A manifest with zero
    entries is valid but reported as not ready via :func:`dataset_status`.
    """
    manifest_path = dataset_path(root)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"真实招标文件数据集清单不存在：{manifest_path}")
    raw = manifest_path.read_text(encoding="utf-8")
    import json

    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema") != DATASET_SCHEMA:
        raise ValueError(f"数据集 schema 必须为 {DATASET_SCHEMA}")
    if data.get("synthetic") is True:
        raise ValueError("数据集标记为 synthetic，不能作为真实招标文件回归集")
    entries = data.get("files")
    if not isinstance(entries, list):
        raise TypeError("数据集 files 必须是数组")
    base = Path(root)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise TypeError(f"files[{index}] 必须是对象")
        # 只有明确标注为用户提供的文件才允许作为真实件；合成/生成来源一律拒绝。
        source = str(entry.get("source") or "").strip()
        if source != "user-provided":
            raise ValueError(
                f"files[{index}].source 必须为 user-provided，实际为 {source or '空'}"
            )
        name = str(entry.get("path") or "").strip()
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError(f"files[{index}].path 必须是安全的相对路径")
        if name in seen:
            raise ValueError(f"数据集文件重复：{name}")
        seen.add(name)
        file_path = base / name
        if not file_path.is_file():
            raise ValueError(f"数据集文件缺失：{name}")
        expected = str(entry.get("sha256") or "").lower()
        actual = _sha256(file_path)
        if expected and expected != actual:
            raise ValueError(f"数据集文件指纹不匹配：{name}")
        annotations = entry.get("annotations")
        if not isinstance(annotations, dict):
            raise TypeError(f"files[{index}].annotations 必须是对象")
        missing = [key for key in REQUIRED_ANNOTATIONS if not annotations.get(key)]
        if missing:
            raise ValueError(f"{name} 缺少人工标注：{', '.join(missing)}")
        if _has_synthetic_marker(file_path):
            raise ValueError(f"{name} 含合成/示例标记，不能作为真实样本")
        # 可选标注：条款 → 应答正文。缺它时判定一致性只能记为未测量。
        responses = annotations.get("responses") or {}
        if not isinstance(responses, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in responses.items()
        ):
            raise ValueError(f"{name}.annotations.responses 必须是条款到应答文本的字符串映射")
        normalized.append(
            {
                "path": name,
                "sha256": actual,
                "size": file_path.stat().st_size,
                "annotations": {
                    key: annotations[key] for key in REQUIRED_ANNOTATIONS
                }
                | ({"responses": responses} if responses else {}),
                "language": str(entry.get("language") or "zh"),
                "source": source,
            }
        )
    return {
        "schema": DATASET_SCHEMA,
        "label": str(data.get("label") or "real-tender-regression"),
        "files": normalized,
    }


def dataset_status(root: Path | str = DEFAULT_ROOT) -> dict[str, Any]:
    """Return a machine-readable readiness summary; never raises for absent data."""
    try:
        dataset = load_dataset(root)
    except (FileNotFoundError, TypeError, ValueError, OSError) as exc:
        return {"ready": False, "files": 0, "reason": str(exc)}
    if not dataset["files"]:
        return {
            "ready": False,
            "files": 0,
            "reason": "数据集清单为空，需要用户提供脱敏真实招标文件及人工标注",
        }
    return {
        "ready": True,
        "files": len(dataset["files"]),
        "reason": "",
        "label": dataset["label"],
        "sha256": [item["sha256"] for item in dataset["files"]],
    }
