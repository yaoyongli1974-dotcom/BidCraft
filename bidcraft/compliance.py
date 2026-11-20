"""上线合规：用户协议/隐私政策、账号注销、数据导出、对账与开票信息、备份校验。

这些能力都只依赖本地数据，不引入任何外部服务。
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .models import (
    AppSetting,
    AuditLog,
    CreditTransaction,
    Material,
    OutlineNode,
    Personnel,
    Project,
    Qualification,
    RechargeOrder,
    Section,
    User,
    UserDoc,
    utcnow,
)
from .security import verify_password

PROFILE_KEY = "billing_profile:{}"
DELETE_CONFIRM = "注销"


class ComplianceError(ValueError):
    """用户可读的合规操作错误。"""


LEGAL_TERMS = """# BidCraft 服务条款（内测版）

生效日期：2026-11-20

## 1. 服务内容
BidCraft 提供投标文件解析、事实台账、章节生成、规则核对、清单报价与导出等工具能力。
内测期间功能可能调整或中断，请勿将本服务作为唯一交付手段。

## 2. 账号
你应妥善保管账号与密码；账号下的操作视为你本人行为。子账号由主账号创建并管理，
其费用与数据归属主账号。

## 3. 内容与数据
你上传的招标文件、企业资料与生成内容归你所有。你需确保对上传材料拥有合法使用权，
不得上传涉密、违法或侵犯第三方权利的内容。

## 4. 生成内容的责任
AI 生成结果仅供参考，必须经人工审阅后使用。因直接采用未审阅内容造成的损失，由使用者承担。

## 5. 计费
按章扣点、套餐与 VIP 服务的价格以页面展示为准；充值点数不可提现。
内测期间的赠送点数不构成长期承诺。

## 6. 服务变更与终止
我们可能调整、暂停或终止部分功能；重大变更会提前在站内公告。
你可随时申请注销账号并删除数据。

## 7. 免责
因不可抗力、网络故障、第三方模型服务中断导致的服务不可用，我们在合理范围内免责。

## 8. 联系
问题与建议请通过项目仓库的 Issue 或内测渠道反馈。
"""

LEGAL_PRIVACY = """# BidCraft 隐私政策（内测版）

生效日期：2026-11-20

## 1. 我们收集什么
- 账号信息：用户名、显示名、密码哈希（scrypt，不保存明文）；
- 业务数据：你上传的招标文件、台账、章节正文、清单、资质与人员信息；
- 运行日志：登录、核对、导出等操作审计，用于安全与排错。

## 2. 我们如何使用
仅用于提供与改进服务：解析文件、生成内容、执行核对、计费结算、故障排查。
不用于广告投放，不出售或交换你的数据。

## 3. 数据存放在哪里
数据存储在你部署或使用的 BidCraft 服务器上（默认 SQLite + 本地目录）。
若你接入第三方模型（OpenAI 兼容端点、Dify、RAGFlow 等），相关文本会发送到该端点，
其处理规则由该服务商决定，请自行评估。

## 4. 我们不做什么
- 不内置统计 SDK，不向第三方发送使用行为；
- 服务端除你显式配置的模型端点外不发起外部请求；
- 桌面客户端仅在「检查更新」时访问 GitHub Releases API。

## 5. 你的权利
你可以随时导出数据、修改资料、申请注销账号。注销后账号与其项目数据将被删除，
法律要求留存的账务记录除外。

## 6. 数据安全
密码加密存储，会话带签名与过期时间，接口按角色鉴权，传输建议启用 HTTPS。

## 7. 未成年人
本服务面向专业用户，不面向未成年人。

## 8. 变更
政策更新会在站内公告；继续使用视为接受更新后的条款。
"""

LEGAL_VERSION = "2026-11-20"

LEGAL_PRIVACY = LEGAL_PRIVACY + """

## 7. 备份与保留期限

- 在线备份默认保留最近 10 份（可用 `bidcraft backup-prune --keep N` 调整），按日轮转；
- 备份中的历史数据最长保留至对应备份被轮转删除为止；账号注销后，其数据会从在线库删除，但可能仍存在于轮转期内的备份中；
- 备份仅用于故障恢复，不用于分析或对外提供。
"""

LEGAL_DOCS = {"terms": LEGAL_TERMS, "privacy": LEGAL_PRIVACY}


def record_consent(user: User, version: str = LEGAL_VERSION) -> None:
    """Record that the user accepted the current legal documents."""

    user.terms_version = (version or LEGAL_VERSION)[:20]
    user.terms_accepted_at = utcnow()


def consent_payload(user: User) -> dict[str, Any]:
    accepted = user.terms_accepted_at
    return {
        "version": user.terms_version or "",
        "accepted_at": accepted.isoformat() if accepted else None,
        "current_version": LEGAL_VERSION,
        "up_to_date": bool(user.terms_version) and user.terms_version == LEGAL_VERSION,
    }


def legal_doc(name: str) -> str:
    text = LEGAL_DOCS.get(name)
    if text is None:
        raise LookupError("文档不存在")
    return text


# --------------------------------------------------------------------- 注销


def delete_account(db, user: User) -> dict[str, int]:
    """Delete the account and everything it owns.

    主账号若仍有子账号，要求先处理子账号，避免误删团队数据。
    """
    subs = db.scalars(select(User).where(User.parent_id == user.id)).all()
    if subs:
        raise ComplianceError("请先删除或移交名下子账号，再注销主账号")
    projects = db.scalars(select(Project).where(Project.owner_id == user.id)).all()
    removed = {"projects": 0, "docs": 0, "qualifications": 0, "personnel": 0}
    project_ids = [project.id for project in projects]
    if project_ids:
        db.query(Section).filter(
            Section.node_id.in_(
                select(OutlineNode.id).where(OutlineNode.project_id.in_(project_ids))
            )
        ).delete(synchronize_session=False)
        db.query(OutlineNode).filter(OutlineNode.project_id.in_(project_ids)).delete(
            synchronize_session=False
        )
        db.query(Material).filter(Material.project_id.in_(project_ids)).delete(
            synchronize_session=False
        )
        db.query(Project).filter(Project.id.in_(project_ids)).delete(
            synchronize_session=False
        )
        removed["projects"] = len(project_ids)
    for row in db.scalars(select(UserDoc).where(UserDoc.user_id == user.id)).all():
        try:
            Path(row.stored_path).unlink(missing_ok=True)
        except OSError:
            pass
        db.delete(row)
        removed["docs"] += 1
    for row in db.scalars(
        select(Qualification).where(Qualification.owner_id == user.id)
    ).all():
        db.delete(row)
        removed["qualifications"] += 1
    for row in db.scalars(select(Personnel).where(Personnel.owner_id == user.id)).all():
        db.delete(row)
        removed["personnel"] += 1
    db.query(CreditTransaction).filter(CreditTransaction.user_id == user.id).delete(
        synchronize_session=False
    )
    try:
        from .invoices import delete_user_files

        delete_user_files(user.id)
    except Exception:  # noqa: BLE001, S110 - 清理失败不影响账号删除
        pass
    db.delete(user)
    db.flush()
    return removed


# --------------------------------------------------------------------- 备份保留

BACKUP_KEEP_DEFAULT = 10


def list_backups() -> list[Path]:
    from .config import settings

    folder = settings.home / "backups"
    if not folder.is_dir():
        return []
    return sorted(folder.glob("bidcraft-*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)


def prune_backups(keep: int = BACKUP_KEEP_DEFAULT, *, dry_run: bool = False) -> dict[str, Any]:
    """Keep the newest ``keep`` backups and delete the rest."""

    if keep < 1:
        raise ComplianceError("保留份数至少为 1")
    files = list_backups()
    doomed = files[keep:]
    removed = []
    for path in doomed:
        removed.append(path.name)
        if not dry_run:
            path.unlink(missing_ok=True)
    return {
        "keep": keep,
        "total": len(files),
        "removed": removed,
        "dry_run": dry_run,
        "checked_at": utcnow().isoformat(),
    }


# --------------------------------------------------------------------- 注销冷静期

COOLING_OFF_DAYS = 7


def deletion_payload(user: User) -> dict[str, Any]:
    """Describe the pending deletion window for one account."""

    due = user.deletion_due_at
    requested = user.deletion_requested_at
    days_left = 0
    if due is not None:
        days_left = max(0, (due - utcnow()).days + (1 if (due - utcnow()).seconds else 0))
    return {
        "pending": bool(due),
        "requested_at": requested.isoformat() if requested else None,
        "due_at": due.isoformat() if due else None,
        "days_left": days_left,
        "cooling_off_days": COOLING_OFF_DAYS,
    }


def request_deletion(db, user: User, password: str) -> dict[str, Any]:
    """Start a cooling-off window instead of deleting immediately."""

    if not password:
        raise ComplianceError("请输入密码确认注销")
    if not verify_password(password, user.password_hash):
        raise ComplianceError("密码不正确")
    subs = db.scalars(select(User).where(User.parent_id == user.id)).first()
    if subs is not None:
        raise ComplianceError("请先删除或移交名下子账号，再注销主账号")
    now = utcnow()
    user.deletion_requested_at = now
    user.deletion_due_at = now + timedelta(days=COOLING_OFF_DAYS)
    db.flush()
    return deletion_payload(user)


def cancel_deletion(db, user: User) -> dict[str, Any]:
    """Revoke a pending deletion inside the cooling-off window."""

    if user.deletion_due_at is None:
        raise ComplianceError("当前没有待执行的注销申请")
    user.deletion_requested_at = None
    user.deletion_due_at = None
    db.flush()
    return deletion_payload(user)


def _audit_purge(db, user: User, removed: dict[str, int]) -> None:
    """Keep an append-only record after the user row is gone."""

    db.add(
        AuditLog(
            project_id=None,
            user_id=user.id,
            username=user.username or "",
            display_name=user.display_name or "",
            action="account.purge",
            detail={
                "username": user.username,
                "requested_at": (
                    user.deletion_requested_at.isoformat()
                    if user.deletion_requested_at
                    else None
                ),
                "due_at": user.deletion_due_at.isoformat() if user.deletion_due_at else None,
                "removed": removed,
            },
        )
    )


def purge_due_accounts(db, *, now: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Delete every account whose cooling-off window has elapsed."""

    moment = now or utcnow()
    rows = db.scalars(
        select(User).where(User.deletion_due_at.isnot(None), User.deletion_due_at <= moment)
    ).all()
    purged: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for user in rows:
        entry = {"id": user.id, "username": user.username}
        if dry_run:
            purged.append(entry)
            continue
        subs = db.scalars(select(User).where(User.parent_id == user.id)).first()
        if subs is not None:
            skipped.append({**entry, "reason": "仍有子账号"})
            continue
        try:
            removed = delete_account(db, user)
        except ComplianceError as exc:
            skipped.append({**entry, "reason": str(exc)})
            continue
        _audit_purge(db, user, removed)
        purged.append({**entry, "removed": removed})
    if not dry_run:
        db.flush()
    return {"purged": purged, "skipped": skipped, "checked_at": moment.isoformat()}


# --------------------------------------------------------------------- 导出


def export_account(db, user: User, home: Path) -> Path:
    """Build a ZIP with the user's projects and personal records."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = Path(home) / "exports" / f"bidcraft-export-{stamp}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    projects = db.scalars(select(Project).where(Project.owner_id == user.id)).all()
    manifest: dict[str, Any] = {
        "exported_at": datetime.now(UTC).isoformat(),
        "user": {"id": user.id, "username": user.username},
        "projects": [],
        "qualifications": [],
        "personnel": [],
        "documents": [],
    }
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for project in projects:
            nodes = db.scalars(
                select(OutlineNode)
                .where(OutlineNode.project_id == project.id)
                .order_by(OutlineNode.position)
            ).all()
            entry = {"id": project.id, "name": project.name, "package": project.package}
            manifest["projects"].append(entry)
            payload = {
                "project": entry,
                "outline": [
                    {
                        "heading": node.heading,
                        "intent": node.intent,
                        "target_words": node.target_words,
                        "status": node.status,
                    }
                    for node in nodes
                ],
                "sections": [],
            }
            for node in nodes:
                section = db.scalar(select(Section).where(Section.node_id == node.id))
                if section is not None:
                    payload["sections"].append(
                        {"heading": node.heading, "body": section.body, "words": section.words}
                    )
            archive.writestr(
                f"projects/{project.id}.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
        for row in db.scalars(
            select(Qualification).where(Qualification.owner_id == user.id)
        ).all():
            manifest["qualifications"].append(
                {
                    "id": row.id,
                    "kind": row.kind,
                    "name": row.name,
                    "cert_no": row.cert_no,
                    "valid_to": row.valid_to,
                    "evidence": row.evidence or [],
                }
            )
            for item in row.evidence or []:
                path = Path(str(item.get("path") or ""))
                if path.is_file():
                    archive.write(path, f"evidence/{row.id}/{path.name}")
        for row in db.scalars(select(Personnel).where(Personnel.owner_id == user.id)).all():
            manifest["personnel"].append(
                {
                    "id": row.id,
                    "name": row.name,
                    "title": row.title,
                    "specialty": row.specialty,
                    "cert_no": row.cert_no,
                    "social_months": row.social_months,
                    "locked": row.locked,
                }
            )
        for row in db.scalars(select(UserDoc).where(UserDoc.user_id == user.id)).all():
            manifest["documents"].append(
                {"id": row.id, "filename": row.filename, "size": row.size, "status": row.status}
            )
            path = Path(row.stored_path)
            if path.is_file():
                archive.write(path, f"documents/{row.id}-{path.name}")
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    return target


# ------------------------------------------------------ 对账与开票信息


def billing_profile(db, user_id: str) -> dict[str, str]:
    row = db.get(AppSetting, PROFILE_KEY.format(user_id))
    if row is None or not isinstance(row.value, dict):
        return {"title": "", "tax_no": "", "email": ""}
    return {
        "title": str(row.value.get("title") or "")[:120],
        "tax_no": str(row.value.get("tax_no") or "")[:40],
        "email": str(row.value.get("email") or "")[:120],
    }


def save_billing_profile(db, user_id: str, payload: dict[str, Any]) -> dict[str, str]:
    data = {
        "title": str(payload.get("title") or "").strip()[:120],
        "tax_no": str(payload.get("tax_no") or "").strip()[:40],
        "email": str(payload.get("email") or "").strip()[:120],
    }
    key = PROFILE_KEY.format(user_id)
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=data))
    else:
        row.value = data
    db.flush()
    return data


def statement_csv(db, user_id: str, *, start: str = "", end: str = "") -> str:
    """流水 + 订单的对账单（CSV 文本）。"""

    def _in_range(value: datetime | None) -> bool:
        if value is None:
            return False
        stamp = value.date().isoformat()
        if start and stamp < start:
            return False
        return not (end and stamp > end)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["类型", "时间", "条目", "数量", "余额", "备注"])
    for tx in db.scalars(
        select(CreditTransaction)
        .where(CreditTransaction.user_id == user_id)
        .order_by(CreditTransaction.created_at)
    ).all():
        if not _in_range(tx.created_at):
            continue
        writer.writerow(
            ["流水", tx.created_at.isoformat(timespec="seconds"), tx.kind, tx.amount, tx.balance_after, tx.note or ""]
        )
    for order in db.scalars(
        select(RechargeOrder)
        .where(RechargeOrder.user_id == user_id)
        .order_by(RechargeOrder.created_at)
    ).all():
        if not _in_range(order.created_at):
            continue
        writer.writerow(
            [
                "订单",
                order.created_at.isoformat(timespec="seconds"),
                order.out_trade_no,
                order.amount_cents / 100,
                order.credits,
                order.status,
            ]
        )
    return buffer.getvalue()


# ------------------------------------------------------------ 备份校验


def verify_backup(path: str | Path) -> dict[str, Any]:
    """Restore a backup into a temp dir and check schema + integrity."""
    source = Path(path)
    if not source.is_file():
        raise ComplianceError("备份文件不存在")
    with tempfile.TemporaryDirectory(prefix="bidcraft-verify-") as tmp:
        copy = Path(tmp) / "restore.db"
        shutil.copy2(source, copy)
        conn = sqlite3.connect(str(copy))
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            counts: dict[str, int] = {}
            for table in tables:
                if table.startswith("sqlite_"):
                    continue
                counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        finally:
            conn.close()
    if integrity != "ok":
        raise ComplianceError(f"备份完整性校验失败：{integrity}")
    return {
        "ok": True,
        "tables": len(counts),
        "rows": sum(counts.values()),
        "counts": counts,
    }
