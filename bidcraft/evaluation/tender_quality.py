"""真实招标文件质量指标（阶段 6）。

本模块把阶段 5 的 :mod:`bidcraft.evaluation.tender_dataset` 契约转成可审计的
质量指标。**没有真实样本时一律返回 ``status='未测量'``**，绝不用合成夹具的
结果冒充真实件指标；即便数据集就绪，未在标注中提供“应答文本”的条款也不参与
判定一致性计算，该项单列为 ``未测量``。

指标口径（每个文件单独计算后取宏平均）：

* ``clause_recall``：人工标注的 ``required_clauses`` 中，被解析器抽成
  Requirement 的比例（文本包含匹配，保留原句边界）；
* ``value_parse_rate``：人工标注的 ``required_values`` 中，能在解析字段里
  找到同值文本的比例；
* ``verdict_agreement``：仅在标注提供 ``responses``（条款→应答文本）时，
  用与 ``check_project`` 相同的 ``verdict.judge`` 引擎复算并比较
  ``expected_verdicts``；未提供应答文本时返回 ``None`` 并在 ``notes`` 中说明。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..calibrate import _load_calibration_packages, _select_package
from ..parsing.extract import extract
from ..parsing.loader import load_document
from ..verdict import judge
from .tender_dataset import DEFAULT_ROOT, dataset_status, load_dataset

STATUS_MEASURED = "已测量"
STATUS_UNMEASURED = "未测量"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _contains(normalized: str, needle: str) -> bool:
    target = _text(needle)
    return bool(target) and target in normalized


def evaluate_tender_file(path: Path, annotations: dict[str, Any]) -> dict[str, Any]:
    """Evaluate ONE annotated real tender file; caller validates the dataset."""
    parsed = load_document(path)
    packages, fallback = _load_calibration_packages()
    package = _select_package(parsed.text or "", packages, fallback)
    result = extract(
        parsed.text or "",
        package,
        pages=parsed.pages or None,
        tables=parsed.tables or None,
    )
    requirement_text = _text(
        "\n".join(str(getattr(item, "text", "") or "") for item in result.requirements)
    )
    field_text = _text(
        "\n".join(str(value) for value in (result.fields or {}).values())
    )

    clauses = annotations.get("required_clauses") or []
    values = annotations.get("required_values") or []
    expected_verdicts = annotations.get("expected_verdicts") or {}
    responses = annotations.get("responses") or {}

    hit_clauses = [c for c in clauses if _contains(requirement_text, str(c))]
    hit_values: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        label = str(item.get("key") or item.get("name") or "")
        expected = str(item.get("value") or "")
        if _contains(field_text, expected):
            hit_values.append(label or expected)

    notes: list[str] = []
    agreement: float | None = None
    mismatches: list[str] = []
    if expected_verdicts:
        compared = 0
        agreed = 0
        for clause, expected in expected_verdicts.items():
            body = responses.get(str(clause))
            if body is None or not str(body).strip():
                continue
            requirement = SimpleNamespace(
                text=str(clause), marker="", source_quote=str(clause)
            )
            state = str(
                judge(requirement, [{"body": str(body)}]).state or ""
            ).lower()
            want = str(expected).strip().lower()
            compared += 1
            if state == want:
                agreed += 1
            else:
                mismatches.append(f"{_text(clause)[:40]}：期望 {want}，实测 {state}")
        if compared:
            agreement = agreed / compared
            if mismatches:
                notes.append(
                    f"判定不一致 {len(mismatches)}/{compared} 条：" + "；".join(mismatches[:3])
                )
        else:
            notes.append("缺少应答文本标注，无法复算判定一致性，记为未测量")

    return {
        "path": str(path),
        "clause_recall": (len(hit_clauses) / len(clauses)) if clauses else None,
        "value_parse_rate": (len(hit_values) / len(values)) if values else None,
        "verdict_agreement": agreement,
        "matched_clauses": hit_clauses,
        "matched_values": hit_values,
        "notes": notes,
        "normalized": {"clauses": len(clauses), "values": len(values)},
    }


def collect_tender_quality(root: Path | str = DEFAULT_ROOT) -> dict[str, Any]:
    """Return an auditable quality summary; never raises for absent datasets."""
    status = dataset_status(root)
    if not status.get("ready"):
        return {
            "status": STATUS_UNMEASURED,
            "dataset": status,
            "files": [],
            "clause_recall": None,
            "value_parse_rate": None,
            "verdict_agreement": None,
            "notes": [status.get("reason") or "真实招标文件数据集未就绪"],
        }

    dataset = load_dataset(root)
    files: list[dict[str, Any]] = []
    errors: list[str] = []
    base = Path(root)
    for entry in dataset["files"]:
        try:
            files.append(
                evaluate_tender_file(base / entry["path"], entry["annotations"])
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not hide others
            errors.append(f"{entry['path']}: {type(exc).__name__}: {exc}")

    def _macro(key: str) -> float | None:
        values = [float(f[key]) for f in files if f.get(key) is not None]
        return round(sum(values) / len(values), 4) if values else None

    notes = [f"解析失败 {len(errors)} 个文件"] if errors else []
    if any(f["verdict_agreement"] is None for f in files):
        notes.append("部分/全部文件缺少应答文本标注，判定一致性为未测量")
    files_count = len(files)
    return {
        "status": STATUS_MEASURED if files_count and not errors else STATUS_UNMEASURED,
        "dataset": status,
        "files": files,
        "clause_recall": _macro("clause_recall"),
        "value_parse_rate": _macro("value_parse_rate"),
        "verdict_agreement": _macro("verdict_agreement"),
        "notes": notes + errors,
    }
