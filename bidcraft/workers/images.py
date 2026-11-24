"""配图批量生成后台任务。

业务绘图逻辑在 :mod:`bidcraft.images`；这里只负责 job 行/SSE 编排。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from .. import images, jobs
from ..db import session_scope
from ..jobqueue import JobAborted, assert_claim_current, fenced_job, raise_if_aborted
from ..models import ImageAsset, Project
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.api")


def _run_image_batch_job(job_id: str, project_id: str, targets: list[dict[str, Any]]) -> None:
    """Background worker for plan-driven image generation.

    Each image uses its own short session so the provider call does not hold a
    long write transaction. Provider failures are already persisted as failed
    assets by ``generate_image``; they count toward the job result and the
    worker continues. Configuration errors abort the whole job.
    """
    total = len(targets)
    succeeded = 0
    failed = 0
    skipped = 0
    failed_labels: list[str] = []
    policy: dict = dict(images.DEFAULT_POLICY)
    try:
        raise_if_aborted()
        assert_claim_current(job_id)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is None:
                return
            project = db.get(Project, project_id)
            if project is not None:
                policy = images.get_policy(project)
            job.status = "running"
            job.message = f"正在准备配图（0/{total}）…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        for index, target in enumerate(targets, start=1):
            # 放在内层 try 之外：中止不能被 ImageError/ImageConfigError 分支吞掉。
            raise_if_aborted()
            label = str(target.get("label") or "配图")
            style = str(target.get("style") or policy.get("style") or "").strip()
            policy_size = str(policy.get("size") or "").strip()
            if policy_size.lower() == "auto":
                # auto 表示不覆盖：沿用模型配置 image_size（再回落 1024x1024）
                policy_size = ""
            size = str(target.get("size") or policy_size or "").strip()
            status = "failed"
            asset: ImageAsset | None = None
            try:
                with session_scope() as db:
                    project = db.get(Project, project_id)
                    if project is None:
                        raise ValueError("项目不存在")
                    existing = db.scalar(
                        select(ImageAsset)
                        .where(
                            ImageAsset.project_id == project.id,
                            ImageAsset.label == label,
                            ImageAsset.status == "ready",
                        )
                        .order_by(ImageAsset.created_at.desc())
                    )
                    if existing is not None:
                        status = "skipped"
                        asset = existing
                    else:
                        source = None
                        asset_id = str(target.get("asset_id") or "")
                        if asset_id:
                            source = db.get(ImageAsset, asset_id)
                            if source is not None and source.project_id != project.id:
                                source = None
                        if source is not None and source.status == "failed":
                            asset = images.regenerate_image(
                                db, project, source, style=style, size=size
                            )
                        else:
                            asset = images.generate_image(
                                db,
                                project,
                                label=label,
                                node_id=str(target.get("node_id") or ""),
                                heading=str(target.get("heading") or ""),
                                style=style,
                                size=size,
                            )
                        status = asset.status
            except images.ImageConfigError:
                raise
            except images.ImageError as exc:
                status = "failed"
                failed_labels.append(label)
                with session_scope() as db:
                    job = fenced_job(db, job_id)
                    if job is not None:
                        job.message = f"配图失败：{label}（{exc}）"
                        db.flush()

            raise_if_aborted()
            if status == "ready":
                succeeded += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
                if label not in failed_labels:
                    failed_labels.append(label)

            with session_scope() as db:
                job = fenced_job(db, job_id)
                if job is None:
                    return
                progress = int(index * 100 / total) if total else 100
                job.progress = progress
                job.payload = {
                    **(job.payload or {}),
                    "done": index,
                    "total": total,
                    "succeeded": succeeded,
                    "failed": failed,
                    "skipped": skipped,
                    "current": label,
                }
                if status == "ready":
                    job.message = f"配图 {index}/{total}：{label}"
                elif status == "skipped":
                    job.message = f"配图 {index}/{total}：{label}（已存在，跳过）"
                else:
                    job.message = f"配图 {index}/{total}：{label} 失败"
                event = {
                    "index": index,
                    "total": total,
                    "progress": progress,
                    "label": label,
                    "status": status,
                    "asset_id": asset.id if asset is not None else "",
                    "url": images.asset_payload(asset)["url"] if asset is not None else "",
                    "done": index,
                    "succeeded": succeeded,
                    "failed": failed,
                    "skipped": skipped,
                }
                jobs.publish_event(job_id, "image", event, db=db)
                db.flush()

        raise_if_aborted()
        assert_claim_current(job_id)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                job.status = "done"
                job.progress = 100
                job.message = f"配图完成：成功 {succeeded}，失败 {failed}"
                if skipped:
                    job.message += f"，跳过 {skipped}"
                job.result = {
                    "total": total,
                    "succeeded": succeeded,
                    "failed": failed,
                    "skipped": skipped,
                    "failed_labels": failed_labels,
                }
                jobs.publish_event(
                    job_id,
                    "done",
                    {**jobs.job_payload(job), "report": job.result},
                    db=db,
                )
                db.flush()
    except JobAborted:
        # 中止不是业务失败：保持 running，由 reaper 重新排队。
        logger.info("配图任务 %s 已中止，等待重新排队", job_id)
    except Exception:  # noqa: BLE001 - surfaced to the UI as a stable message
        logger.exception("配图任务 %s 失败", job_id)
        failed_message = "配图任务失败：图片服务不可用或响应异常，详情见服务日志"
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                _fail_job(db, job, failed_message)
