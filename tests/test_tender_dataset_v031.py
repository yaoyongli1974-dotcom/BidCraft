"""v0.31 阶段 5：真实招标文件数据集契约测试。

这里的临时文件只是**框架自检**，不会被当作真实招标文件；仓库自带的
`tender_real` 目录必须没有 dataset.json，确保没有把合成夹具冒充真实样本。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bidcraft.evaluation import DATASET_SCHEMA, dataset_status, load_dataset


def _write_dataset(root: Path, *, files: list[dict], **extra: object) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {"schema": DATASET_SCHEMA, "label": "framework-self-check", "files": files}
    payload.update(extra)
    (root / "dataset.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _entry(
    root: Path,
    name: str = "tender.md",
    *,
    synthetic: bool = False,
    marker_offset: int = 0,
    source: str = "user-provided",
) -> dict:
    body = "★投标人须具备独立法人资格\n预算金额：壹仟万元\n"
    if marker_offset:
        body += "\n" * marker_offset + "<!-- SYNTHETIC: framework self-check -->\n"
    elif synthetic:
        body = "<!-- SYNTHETIC: framework self-check -->\n" + body
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return {
        "path": name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": source,
        "annotations": {
            "required_clauses": ["★投标人须具备独立法人资格"],
            "required_values": [{"key": "预算金额", "value": "壹仟万元"}],
            "expected_verdicts": {"★投标人须具备独立法人资格": "SATISFIED"},
        },
    }


def test_missing_manifest_is_reported_as_not_ready(tmp_path: Path) -> None:
    status = dataset_status(tmp_path)
    assert status["ready"] is False
    assert "dataset.json" in status["reason"]
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path)


def test_empty_manifest_is_not_ready(tmp_path: Path) -> None:
    _write_dataset(tmp_path, files=[])
    status = dataset_status(tmp_path)
    assert status["ready"] is False
    assert "用户提供" in status["reason"]


def test_valid_dataset_is_ready_and_normalized(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    _write_dataset(tmp_path, files=[entry])
    dataset = load_dataset(tmp_path)
    assert dataset["files"][0]["path"] == "tender.md"
    assert dataset["files"][0]["sha256"] == entry["sha256"]
    assert dataset["files"][0]["size"] > 0
    assert dataset_status(tmp_path)["ready"] is True


def test_wrong_sha256_is_rejected(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    entry["sha256"] = "0" * 64
    _write_dataset(tmp_path, files=[entry])
    with pytest.raises(ValueError, match="指纹不匹配"):
        load_dataset(tmp_path)


def test_synthetic_marker_is_rejected_as_real_sample(tmp_path: Path) -> None:
    entry = _entry(tmp_path, synthetic=True)
    entry["annotations"]["required_clauses"] = ["★投标人须具备独立法人资格"]
    _write_dataset(tmp_path, files=[entry])
    with pytest.raises(ValueError, match="合成/示例标记"):
        load_dataset(tmp_path)


def test_synthetic_marker_beyond_first_chunk_is_rejected(tmp_path: Path) -> None:
    """合成标记出现在 4096 字节之后也必须被整文件扫描发现。"""
    entry = _entry(tmp_path, marker_offset=6000)
    assert (tmp_path / "tender.md").stat().st_size > 4096
    _write_dataset(tmp_path, files=[entry])
    with pytest.raises(ValueError, match="合成/示例标记"):
        load_dataset(tmp_path)
    assert dataset_status(tmp_path)["ready"] is False


def test_non_user_provided_source_is_rejected(tmp_path: Path) -> None:
    """只有用户提供的真实/脱敏文件才能进入回归集；synthetic 来源一律拒绝。"""
    for index, source in enumerate(("synthetic", "synthetic-fixture", "", "user_generated")):
        root = tmp_path / f"case-{index}"
        entry = _entry(root, source=source)
        _write_dataset(root, files=[entry])
        with pytest.raises(ValueError, match="user-provided"):
            load_dataset(root)


def test_missing_annotation_is_rejected(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    entry["annotations"] = {"required_clauses": ["★"]}
    _write_dataset(tmp_path, files=[entry])
    with pytest.raises(ValueError, match="缺少人工标注"):
        load_dataset(tmp_path)


def test_unsafe_path_is_rejected(tmp_path: Path) -> None:
    entry = _entry(tmp_path)
    entry["path"] = "../outside.md"
    _write_dataset(tmp_path, files=[entry])
    with pytest.raises(ValueError, match="安全的相对路径"):
        load_dataset(tmp_path)


def test_repository_ships_no_real_tender_dataset() -> None:
    root = Path(__file__).parents[1] / "tests" / "fixtures" / "tender_real"
    assert not (root / "dataset.json").exists(), "真实数据集清单不得提交到仓库"
    assert (root / "README.md").is_file()
    assert (root / "dataset.example.json").is_file()
    status = dataset_status(root)
    assert status["ready"] is False
