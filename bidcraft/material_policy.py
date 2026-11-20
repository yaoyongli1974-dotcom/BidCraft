"""Material trust policy: 素材「可引用性」分级。

知识库检索目前只判断「命中与否」，不判断「可不可信」。召回越多，过期证书与
无出处业绩被注入模型的风险越大。本模块按素材类型声明必填的生命周期字段，
缺失或过期时把该片段降级为**题录引用**（只给标题/出处/日期，不给正文）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from . import clock
from .knowledge import KnowledgeError, Passage  # noqa: F401 (便于调用方捕获)

POLICY_KEY = "material_policy"

DEFAULT_POLICY: dict[str, dict[str, Any]] = {
    "qualification": {
        "label": "资质证书",
        "required": ["cert_no", "valid_to"],
        "hint": "证书编号与有效期",
    },
    "performance": {
        "label": "业绩合同",
        "required": ["contract_no", "owner", "amount", "acceptance"],
        "hint": "合同编号、业主、金额、验收结论",
    },
    "personnel": {
        "label": "人员证书",
        "required": ["cert_no", "social_months", "locked"],
        "hint": "证书号、社保月份、是否在建锁定",
    },
    "standard": {
        "label": "标准规范",
        "required": ["source_url"],
        "hint": "官方出处",
    },
    "other": {"label": "其他素材", "required": [], "hint": ""},
}

FIELD_LABELS = {
    "cert_no": "证书编号",
    "valid_to": "有效期至",
    "contract_no": "合同编号",
    "owner": "业主",
    "amount": "金额",
    "acceptance": "验收结论",
    "social_months": "社保缴纳月份",
    "locked": "在建锁定",
    "source_url": "官方出处",
    "verifiable_at": "可核验平台",
}


@dataclass
class Verdict:
    ok: bool
    kind: str
    missing: list[str] = field(default_factory=list)
    expired: bool = False
    reason: str = ""


def load_policy(packages_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """读取规则包里的 material_policy（多包合并，缺省回退默认）。"""
    policy = {key: dict(value) for key, value in DEFAULT_POLICY.items()}
    if packages_dir is None:
        packages_dir = Path(__file__).resolve().parent / "resources" / "packages"
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML 是运行依赖
        return policy
    for path in sorted(packages_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        block = data.get(POLICY_KEY)
        if not isinstance(block, dict):
            continue
        for kind, spec in block.items():
            if not isinstance(spec, dict):
                continue
            entry = dict(policy.get(kind, {}))
            entry.update({k: v for k, v in spec.items() if k in {"label", "required", "hint"}})
            required = entry.get("required")
            if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
                entry["required"] = []
            policy[kind] = entry
    return policy


def kinds(policy: dict[str, dict[str, Any]] | None = None) -> list[str]:
    return sorted((policy or load_policy()).keys())


#: 有效期按北京时间日历日判定（见 ``bidcraft.clock``）。
BEIJING_TZ = clock.BEIJING_TZ


def _now() -> dt.datetime:
    """当前北京时间（测试可替换 ``_now`` 来锁定边界时刻）。"""
    return clock.now()


def _today() -> dt.date:
    return _now().date()


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.date):
        return value if not isinstance(value, dt.datetime) else value.date()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def assess(meta: dict[str, Any] | None, policy: dict[str, dict[str, Any]] | None = None) -> Verdict:
    """判断一条素材是否可全文引用。"""
    rules = policy or load_policy()
    meta = dict(meta or {})
    kind = str(meta.get("kind") or "").strip()
    # 未知/缺失的类型不能落回 other（required 为空会整段放行）。写入侧本就硬校验，
    # 读取侧同样按失败关闭处理，避免旧元数据或拼错的 kind 绕过生命周期检查。
    if not kind:
        return Verdict(ok=False, kind="unknown", reason="缺少素材类型，未注入正文")
    if kind not in rules:
        return Verdict(ok=False, kind=kind, reason="未知素材类型，未注入正文")
    spec = rules[kind]
    missing = [key for key in spec.get("required", []) if not str(meta.get(key) or "").strip()]
    expired = False
    valid_to = _as_date(meta.get("valid_to"))
    if valid_to is not None and valid_to < _today():
        expired = True
    if missing:
        labels = "、".join(FIELD_LABELS.get(key, key) for key in missing)
        reason = f"缺少{labels}"
    elif expired:
        reason = f"有效期至 {valid_to.isoformat()} 已过期"
    else:
        reason = ""
    return Verdict(ok=not missing and not expired, kind=kind, missing=missing, expired=expired, reason=reason)


def doc_map(kb) -> dict[str, dict[str, Any]]:
    docs = (kb.options or {}).get("docs")
    return docs if isinstance(docs, dict) else {}


def find_meta(db, source: str) -> tuple[Any, dict[str, Any]]:
    """按片段来源找到所属知识库与文档元数据。"""
    from .models import KnowledgeBase

    source = str(source or "")
    best: tuple[Any, dict[str, Any]] | None = None
    for kb in db.scalars(select(KnowledgeBase)):
        docs = doc_map(kb)
        location = str(kb.location or "")
        relative = ""
        if location and source.startswith(location):
            relative = source[len(location) :].lstrip("/").lstrip("\\")
        for key, meta in docs.items():
            if not isinstance(meta, dict):
                continue
            if key == source or (relative and key == relative) or (key and source.endswith(key)):
                return kb, dict(meta)
        if best is None and relative:
            best = (kb, {})
    return best if best is not None else (None, {})


def set_meta(db, kb, source: str, meta: dict[str, Any]) -> dict[str, Any]:
    """写入/更新单个来源的文档元数据（存于 knowledge_bases.options）。"""

    source = str(source or "").strip()
    if not source:
        raise KnowledgeError("来源不能为空")
    rules = load_policy()
    kind = str(meta.get("kind") or "other")
    if kind not in rules:
        raise KnowledgeError(f"素材类型无效：{kind}")
    valid_to = str(meta.get("valid_to") or "").strip()
    if valid_to and _as_date(valid_to) is None:
        raise KnowledgeError("有效期格式应为 YYYY-MM-DD")
    record = {
        "kind": kind,
        "title": str(meta.get("title") or "").strip()[:200],
        "cert_no": str(meta.get("cert_no") or "").strip()[:120],
        "contract_no": str(meta.get("contract_no") or "").strip()[:120],
        "owner": str(meta.get("owner") or "").strip()[:120],
        "amount": str(meta.get("amount") or "").strip()[:60],
        "acceptance": str(meta.get("acceptance") or "").strip()[:200],
        "social_months": str(meta.get("social_months") or "").strip()[:60],
        "locked": str(meta.get("locked") or "").strip()[:60],
        "valid_to": valid_to,
        "source_url": str(meta.get("source_url") or "").strip()[:500],
        "verifiable_at": str(meta.get("verifiable_at") or "").strip()[:200],
        "note": str(meta.get("note") or "").strip()[:200],
    }
    options = dict(kb.options or {})
    docs = dict(doc_map(kb))
    docs[source] = record
    options["docs"] = docs
    kb.options = options
    db.flush()
    return record


def citation(source: str, meta: dict[str, Any], reason: str) -> str:
    name = meta.get("title") or Path(source).name or "未命名素材"
    bits = [f"[题录] {name}"]
    if meta.get("valid_to"):
        bits.append(f"有效期至 {meta['valid_to']}")
    if meta.get("source_url"):
        bits.append(f"出处 {meta['source_url']}")
    if reason:
        bits.append(f"未注入正文：{reason}")
    return "；".join(bits)


def context_block(db, passages: Iterable[Passage], *, policy: dict[str, dict[str, Any]] | None = None) -> tuple[str, int]:
    """构造带可信度分级的检索注入块，返回 (文本, 降级条数)。"""
    rules = policy or load_policy()
    rows: list[str] = []
    downgraded = 0
    for index, passage in enumerate(passages, start=1):
        _, meta = find_meta(db, passage.source)
        verdict = assess(meta, rules) if meta else Verdict(ok=False, kind="other", reason="未登记素材元数据")
        if verdict.ok:
            rows.append(f"[{index}] （{passage.source}）\n{passage.text}")
        else:
            downgraded += 1
            rows.append(f"[{index}] {citation(passage.source, meta, verdict.reason)}")
    if not rows:
        return "", 0
    block = (
        "【知识库参考】\n"
        "以下资料只用于补充行业背景、产品能力与实施方法；资质、业绩、价格、人员与参数"
        "仍必须以事实台账为准，不得据此编造。\n"
        "标记为「题录」的素材因缺少生命周期字段或已过期，只提供出处，不得据其复述细节。\n\n"
        + "\n\n".join(rows)
    )
    return block, downgraded
