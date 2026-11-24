"""上线合规：用户协议/隐私政策、账号注销、数据导出、对账与开票信息、备份校验。

这些能力都只依赖本地数据，不引入任何外部服务。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from . import clock
from .models import (
    AppSetting,
    AuditLog,
    CreditTransaction,
    Enterprise,
    EnterpriseMember,
    KbDocument,
    KnowledgeBase,
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
        "accepted_at": clock.iso_utc(accepted),
        "current_version": LEGAL_VERSION,
        "up_to_date": bool(user.terms_version) and user.terms_version == LEGAL_VERSION,
    }


def legal_doc(name: str) -> str:
    text = LEGAL_DOCS.get(name)
    if text is None:
        raise LookupError("文档不存在")
    return text


# --------------------------------------------------------------------- 注销


def _detach_enterprise_memberships(db, user: User) -> dict[str, int]:
    """Remove the user's memberships without deleting enterprise-shared assets.

    - member：只删自己的成员行，企业、企业其他人的共享资产不动；
    - owner：把所有权移交给最早加入的成员；没有其他成员时才删除企业容器
      （容器不是资产，资质/人员/文档行不会被这里删除）；
    - 用户拥有、但挂在企业下的共享资产移交给企业 owner，避免注销把其他成员
      正在使用的共享资产一起带走。
    """

    removed = {"enterprise_members": 0, "enterprises": 0, "assets_transferred": 0}
    rows = list(
        db.scalars(
            select(EnterpriseMember).where(EnterpriseMember.user_id == user.id)
        )
    )
    for row in rows:
        db.delete(row)
        removed["enterprise_members"] += 1
    owned = list(
        db.scalars(select(Enterprise).where(Enterprise.owner_id == user.id))
    )
    for enterprise in owned:
        others = list(
            db.scalars(
                select(EnterpriseMember)
                .where(
                    EnterpriseMember.enterprise_id == enterprise.id,
                    EnterpriseMember.user_id != user.id,
                )
                .order_by(EnterpriseMember.created_at, EnterpriseMember.id)
            )
        )
        if others:
            successor = others[0]
            enterprise.owner_id = successor.user_id
            successor.role = "owner"
            for extra in others[1:]:
                if extra.role == "owner":
                    extra.role = "member"
        else:
            for member in db.scalars(
                select(EnterpriseMember).where(
                    EnterpriseMember.enterprise_id == enterprise.id
                )
            ):
                db.delete(member)
            db.delete(enterprise)
            removed["enterprises"] += 1
    db.flush()
    # 注销者的共享资产交给仍存在的企业 owner，保留成员正在使用的证据。
    for model in (Qualification, Personnel):
        for row in db.scalars(
            select(model).where(
                model.owner_id == user.id, model.enterprise_id.isnot(None)
            )
        ):
            enterprise = db.get(Enterprise, row.enterprise_id)
            if enterprise is not None and enterprise.owner_id != user.id:
                row.owner_id = enterprise.owner_id
                removed["assets_transferred"] += 1
    db.flush()
    return removed


def delete_account(db, user: User) -> dict[str, int]:
    """Delete the account and everything it owns.

    主账号若仍有子账号，要求先处理子账号，避免误删团队数据。关联行统一走
    ``purge.delete_user_rows``：SQLite 上顺手消掉历史孤儿，PostgreSQL 上则
    保证不会因为漏删子表触发外键约束。
    """
    from .services.purge import delete_user_rows

    subs = db.scalars(select(User).where(User.parent_id == user.id)).all()
    if subs:
        raise ComplianceError("请先删除或移交名下子账号，再注销主账号")

    # 用户文档的文件本体要先落盘删除；行本身由 delete_user_rows 统一清理。
    for row in db.scalars(select(UserDoc).where(UserDoc.user_id == user.id)).all():
        try:
            Path(row.stored_path).unlink(missing_ok=True)
        except OSError:
            pass

    enterprise_removed = _detach_enterprise_memberships(db, user)
    removed = delete_user_rows(db, user.id)
    removed.update(enterprise_removed)
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
        "checked_at": clock.iso_utc(utcnow()),
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
        "requested_at": clock.iso_utc(requested),
        "due_at": clock.iso_utc(due),
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
                "requested_at": clock.iso_utc(user.deletion_requested_at),
                "due_at": clock.iso_utc(user.deletion_due_at),
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
    return {"purged": purged, "skipped": skipped, "checked_at": clock.iso_utc(moment)}


# --------------------------------------------------------------------- 导出


def export_account(db, user: User, home: Path) -> Path:
    """Build a ZIP with the user's projects and personal records."""
    stamp = clock.now().strftime("%Y%m%d-%H%M%S")
    target = Path(home) / "exports" / f"bidcraft-export-{stamp}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    projects = db.scalars(select(Project).where(Project.owner_id == user.id)).all()
    manifest: dict[str, Any] = {
        "exported_at": clock.now().isoformat(),
        "user": {"id": user.id, "username": user.username},
        "projects": [],
        "qualifications": [],
        "personnel": [],
        "documents": [],
        "enterprises": [],
        "shared_qualifications": [],
        "shared_personnel": [],
        "enterprise_documents": [],
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
                    "valid_to": clock.iso_utc(row.valid_to),
                    "enterprise_id": row.enterprise_id or None,
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
                    "enterprise_id": row.enterprise_id or None,
                }
            )
        memberships = list(
            db.scalars(
                select(EnterpriseMember).where(EnterpriseMember.user_id == user.id)
            )
        )
        enterprise_ids = {row.enterprise_id for row in memberships}
        for membership in memberships:
            enterprise = db.get(Enterprise, membership.enterprise_id)
            if enterprise is None:
                continue
            manifest["enterprises"].append(
                {
                    "id": enterprise.id,
                    "name": enterprise.name,
                    "role": membership.role,
                }
            )
        if enterprise_ids:
            for row in db.scalars(
                select(Qualification).where(
                    Qualification.enterprise_id.in_(enterprise_ids),
                    Qualification.owner_id != user.id,
                )
            ).all():
                manifest["shared_qualifications"].append(
                    {
                        "id": row.id,
                        "kind": row.kind,
                        "name": row.name,
                        "cert_no": row.cert_no,
                        "valid_to": clock.iso_utc(row.valid_to),
                        "enterprise_id": row.enterprise_id,
                    }
                )
            for row in db.scalars(
                select(Personnel).where(
                    Personnel.enterprise_id.in_(enterprise_ids),
                    Personnel.owner_id != user.id,
                )
            ).all():
                manifest["shared_personnel"].append(
                    {
                        "id": row.id,
                        "name": row.name,
                        "title": row.title,
                        "specialty": row.specialty,
                        "cert_no": row.cert_no,
                        "enterprise_id": row.enterprise_id,
                    }
                )
            kbs = [
                row
                for row in db.scalars(select(KnowledgeBase)).all()
                if str((row.options or {}).get("enterprise_id") or "")
                in enterprise_ids
            ]
            for kb in kbs:
                for row in db.scalars(
                    select(KbDocument).where(KbDocument.kb_id == kb.id)
                ).all():
                    manifest["enterprise_documents"].append(
                        {
                            "id": row.id,
                            "source": row.source,
                            "sha256": row.sha256,
                            "kind": row.kind,
                            "version": row.version,
                            "valid_to": clock.iso_utc(row.valid_to),
                            "enterprise_id": str(
                                (kb.options or {}).get("enterprise_id") or ""
                            ),
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
        # 按北京日历日归档：UTC 00:00 前 8 小时属于北京同一天的更早时段
        stamp = clock.local_date(value).isoformat()
        if start and stamp < start:
            return False
        return not (end and stamp > end)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["类型", "时间（北京时间）", "条目", "数量", "余额", "备注"])
    for tx in db.scalars(
        select(CreditTransaction)
        .where(CreditTransaction.user_id == user_id)
        .order_by(CreditTransaction.created_at)
    ).all():
        if not _in_range(tx.created_at):
            continue
        writer.writerow(
            ["流水", clock.format_local(tx.created_at), tx.kind, tx.amount, tx.balance_after, tx.note or ""]
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
                clock.format_local(order.created_at),
                order.out_trade_no,
                order.amount_cents / 100,
                order.credits,
                order.status,
            ]
        )
    return buffer.getvalue()


# ------------------------------------------------------------ 备份校验


def _verify_manifest_files(bundle: zipfile.ZipFile, manifest: dict[str, Any]) -> dict[str, Any]:
    """确认清单里列出的数据文件都在包内且哈希一致（旧备份无清单则跳过）。"""

    entries = manifest.get("data_files")
    if not isinstance(entries, list) or not entries:
        return {"checked": 0, "missing": [], "hash_mismatch": []}
    names = set(bundle.namelist())
    missing: list[str] = []
    mismatched: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("path") or "")
        if not name or name not in names:
            missing.append(name or "<empty>")
            continue
        expected = str(entry.get("sha256") or "").strip().lower()
        if expected:
            actual = hashlib.sha256(bundle.read(name)).hexdigest()
            if actual != expected:
                mismatched.append(name)
    if missing:
        raise ComplianceError("备份缺少清单中的数据文件：" + "、".join(missing[:10]))
    if mismatched:
        raise ComplianceError("备份数据文件哈希不一致：" + "、".join(mismatched[:10]))
    return {"checked": len(entries), "missing": [], "hash_mismatch": []}


def verify_backup(path: str | Path) -> dict[str, Any]:
    """校验备份可恢复：SQLite 跑 integrity_check，PostgreSQL 解析 pg_dump SQL。

    两种后端打包出的 zip 分别含 ``bidcraft.db`` 或 ``dump.sql``；旧版纯 .db
    文件也继续支持。带 ``manifest.json`` 的新备份还会逐项确认 data_files
    确实在包内（并校验 sha256），避免只验 DB 就误报"备份完整"。这里只读，
    不执行备份里的任何 SQL。
    """
    import re

    source = Path(path)
    if not source.is_file():
        raise ComplianceError("备份文件不存在")

    sql_text: str | None = None
    sqlite_source: Path = source
    manifest: dict[str, Any] = {}
    data_report: dict[str, Any] | None = None
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as bundle:
            names = set(bundle.namelist())
            if "manifest.json" in names:
                try:
                    loaded = json.loads(bundle.read("manifest.json").decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ComplianceError("备份清单 manifest.json 无法解析") from exc
                if isinstance(loaded, dict):
                    manifest = loaded
            data_report = _verify_manifest_files(bundle, manifest) if manifest else None
            if "dump.sql" in names:
                sql_text = bundle.read("dump.sql").decode("utf-8", errors="replace")
            elif "bidcraft.db" in names:
                with tempfile.TemporaryDirectory(prefix="bidcraft-verify-") as tmp:
                    sqlite_source = Path(tmp) / "restore.db"
                    sqlite_source.write_bytes(bundle.read("bidcraft.db"))
                    # 必须在临时目录仍存在时校验——退出 with 后文件就被删了。
                    report = _verify_sqlite_backup(sqlite_source)
                if data_report is not None:
                    report["data_files"] = data_report
                return report
            else:
                raise ComplianceError("备份包里既没有 bidcraft.db 也没有 dump.sql")
    if sql_text is None:
        return _verify_sqlite_backup(sqlite_source)

    # pg_dump 纯文本：统计 CREATE TABLE 与 COPY 数据行，检查关键表是否齐全。
    tables = re.findall(r"^CREATE TABLE (?:public\.)?[\"']?(\w+)[\"']?", sql_text, re.MULTILINE)
    if not tables:
        raise ComplianceError("dump.sql 中没有找到建表语句，备份可能不完整")
    required = {"projects", "users", "jobs"}
    missing = required - set(tables)
    if missing:
        raise ComplianceError("备份缺少关键表：" + "、".join(sorted(missing)))
    rows = 0
    in_copy = False
    for line in sql_text.splitlines():
        if line.startswith("COPY "):
            in_copy = True
            continue
        if in_copy:
            if line.startswith("\\."):
                in_copy = False
            elif line.strip():
                rows += 1
    report = {
        "ok": True,
        "backend": "postgresql",
        "tables": len(tables),
        "rows": rows,
        "counts": {},
    }
    if data_report is not None:
        report["data_files"] = data_report
    return report


def _verify_sqlite_backup(source: Path) -> dict[str, Any]:
    """原有的 SQLite 备份校验路径；保持行为不变。"""
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
                # 表名仅来自 sqlite_master 枚举并加双引号，不接受外部输入。
                counts[table] = conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"'  # nosec B608
                ).fetchone()[0]
        finally:
            conn.close()
    if integrity != "ok":
        raise ComplianceError(f"备份完整性校验失败：{integrity}")
    return {
        "ok": True,
        "backend": "sqlite",
        "tables": len(counts),
        "rows": sum(counts.values()),
        "counts": counts,
    }
