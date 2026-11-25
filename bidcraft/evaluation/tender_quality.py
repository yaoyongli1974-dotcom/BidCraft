"""真实招标文件质量指标（阶段 6）。

本模块把阶段 5 的 :mod:`bidcraft.evaluation.tender_dataset` 契约转成可审计的
质量指标。**没有真实样本时一律返回 ``status='未测量'``**，绝不用合成夹具的
结果冒充真实件指标；即便数据集就绪，未在标注中提供“应答文本”的条款也不参与
判定一致性计算，该项单列为 ``未测量``。输出同时带 ``coverage``（分子/分母/
宏平均）、``samples``（可抽查的具体命中样本）与 ``mismatches``（判定不一致、
未抽取条款、未解析值）明细；样本截断不影响任何比率。

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
    missed_clauses = [c for c in clauses if c not in hit_clauses]
    hit_values: list[str] = []
    missed_values: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        label = str(item.get("key") or item.get("name") or "")
        expected = str(item.get("value") or "")
        if _contains(field_text, expected):
            hit_values.append(label or expected)
        else:
            missed_values.append(label or expected)

    notes: list[str] = []
    agreement: float | None = None
    mismatches: list[dict[str, Any]] = []
    verdict_rows: list[dict[str, Any]] = []
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
            ok = state == want
            if ok:
                agreed += 1
            else:
                mismatches.append(
                    {
                        "kind": "verdict",
                        "clause": _text(clause)[:120],
                        "expected": want,
                        "actual": state,
                    }
                )
            verdict_rows.append(
                {
                    "clause": _text(clause)[:120],
                    "expected": want,
                    "actual": state,
                    "agree": ok,
                }
            )
        if compared:
            agreement = agreed / compared
            if mismatches:
                notes.append(
                    f"判定不一致 {len(mismatches)}/{compared} 条："
                    + "；".join(
                        f"{m['clause'][:40]}：期望 {m['expected']}，实测 {m['actual']}"
                        for m in mismatches[:3]
                    )
                )
        else:
            notes.append("缺少应答文本标注，无法复算判定一致性，记为未测量")

    for clause in missed_clauses[:5]:
        mismatches.append(
            {"kind": "clause_not_extracted", "clause": _text(clause)[:120]}
        )
    for value in missed_values[:5]:
        mismatches.append({"kind": "value_not_parsed", "value": value})

    return {
        "path": str(path),
        "clause_recall": (len(hit_clauses) / len(clauses)) if clauses else None,
        "value_parse_rate": (len(hit_values) / len(values)) if values else None,
        "verdict_agreement": agreement,
        "matched_clauses": hit_clauses,
        "matched_values": hit_values,
        "coverage": {
            "clauses": {
                "hit": len(hit_clauses),
                "total": len(clauses),
                "ratio": (len(hit_clauses) / len(clauses)) if clauses else None,
            },
            "values": {
                "hit": len(hit_values),
                "total": len(values),
                "ratio": (len(hit_values) / len(values)) if values else None,
            },
            "verdict": {
                "compared": compared if expected_verdicts else 0,
                "agreed": agreed if expected_verdicts else 0,
                "annotated": len(expected_verdicts),
                "ratio": agreement,
            },
        },
        "samples": {
            "matched_clauses": [str(c)[:120] for c in hit_clauses[:5]],
            "matched_values": hit_values[:5],
            "verdict_checks": verdict_rows[:5],
        },
        "mismatches": mismatches,
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
            "coverage": {},
            "samples": {},
            "mismatches": [],
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
        "coverage": {
            "clauses": {
                "macro_ratio": _macro("clause_recall"),
                "covered": sum(
                    int(f["coverage"]["clauses"]["hit"]) for f in files
                ),
                "total": sum(int(f["coverage"]["clauses"]["total"]) for f in files),
                "files": files_count,
            },
            "values": {
                "macro_ratio": _macro("value_parse_rate"),
                "covered": sum(int(f["coverage"]["values"]["hit"]) for f in files),
                "total": sum(int(f["coverage"]["values"]["total"]) for f in files),
                "files": files_count,
            },
            "verdict": {
                "macro_ratio": _macro("verdict_agreement"),
                "compared": sum(int(f["coverage"]["verdict"]["compared"]) for f in files),
                "agreed": sum(int(f["coverage"]["verdict"]["agreed"]) for f in files),
                "annotated": sum(
                    int(f["coverage"]["verdict"]["annotated"]) for f in files
                ),
            },
        },
        "samples": {
            "files": [f["samples"] for f in files[:5]],
        },
        "mismatches": [
            {"file": f["path"], **mismatch}
            for f in files
            for mismatch in f["mismatches"]
        ],
        "notes": notes + errors,
    }
