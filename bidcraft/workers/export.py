"""批量导出后台任务。

导出逻辑在 :mod:`bidcraft.export`；这里只负责 job 行/SSE 编排与 zip 打包。
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from typing import Any

from .. import images, jobs
from ..config import settings
from ..db import session_scope
from ..export.docx import export_docx, export_markdown
from ..export_gate import ExportGateError, enforce_export_gate
from ..generation.outline import load_outline
from ..ledger import load_ledger
from ..models import Job, Project
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.api")


def _run_batch_export_job(job_id: str, project_ids: list[str], fmt: str) -> None:
    """Background worker: export each project, then bundle the files into a zip.

    Projects without body text fail only for non-xlsx formats; projects with
    unresolved blocking risks are skipped by the delivery gate and recorded in
    ``failed`` with the same message as the single-project export endpoint.
    The batch keeps going and the final zip contains whatever succeeded
    (fail-closed: background batches have no force channel).
    """

    total = len(project_ids)
    files: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []
    entries: list[tuple[Path, str]] = []
    blocked = 0
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status = "running"
            job.message = f"正在批量导出（0/{total}）…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        for index, project_id in enumerate(project_ids, start=1):
            name = ""
            item: dict[str, Any]
            try:
                with session_scope() as db:
                    project = db.get(Project, project_id)
                    if project is None:
                        raise ValueError("项目不存在")
                    name = project.name
                    # 交付硬门禁与单项目导出同一口径；后台批量无 force 通道（fail-closed）。
                    enforce_export_gate(db, project_id, fmt)
                    ledger = load_ledger(db, project_id)
                    nodes = load_outline(db, project_id)
                    if fmt != "xlsx" and (
                        not nodes or not any(node.get("body") for node in nodes)
                    ):
                        raise ValueError("还没有正文，请先生成或编写章节")
                    output_dir = settings.project_dir(project_id) / "output"
                    if fmt == "docx":
                        from ..export import templates as export_templates

                        path = export_docx(
                            output_dir / "bid.docx",
                            ledger,
                            nodes,
                            image_resolver=images.url_resolver(project_id),
                            image_policy=images.get_policy(project),
                            template=export_templates.load(project),
                        )
                    elif fmt == "md":
                        path = export_markdown(output_dir / "bid.md", ledger, nodes)
                    else:
                        from ..export.xlsx import export_xlsx

                        path = export_xlsx(output_dir / "bid.xlsx", ledger)
                ext = "md" if fmt == "md" else fmt
                safe = re.sub(r'[\\/:*?"<>|]', "", name).strip() or "bid"
                arcname = f"{safe[:40]}-{index}.{ext}"
                entries.append((Path(path), arcname))
                files.append({"project_id": project_id, "name": name, "arcname": arcname})
                item = {
                    "project_id": project_id,
                    "name": name,
                    "ok": True,
                    "arcname": arcname,
                }
            except Exception as exc:  # noqa: BLE001 - recorded per project
                if isinstance(exc, ExportGateError):
                    blocked += 1
                item = {
                    "project_id": project_id,
                    "name": name,
                    "ok": False,
                    "error": str(exc),
                }
                failed_items.append(
                    {"project_id": project_id, "name": name, "error": str(exc)}
                )
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return
                progress = int(index * 100 / total) if total else 100
                job.progress = progress
                job.payload = {
                    **(job.payload or {}),
                    "done": index,
                    "total": total,
                    "ok": len(files),
                    "failed": len(failed_items),
                }
                job.message = f"已导出 {index}/{total}：{name or project_id}"
                jobs.publish_event(
                    job_id,
                    "batch_item",
                    {
                        **item,
                        "index": index,
                        "total": total,
                        "progress": progress,
                        "done": index,
                        "ok_count": len(files),
                        "failed_count": len(failed_items),
                    },
                    db=db,
                )

        zip_dir = settings.home / "output"
        zip_dir.mkdir(parents=True, exist_ok=True)
        zip_path = zip_dir / f"batch-{job_id[:8]}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path, arcname in entries:
                bundle.write(path, arcname=arcname)

        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                job.status = "done"
                job.progress = 100
                message = f"导出完成：成功 {len(files)}，失败 {len(failed_items)}"
                if blocked:
                    message += f"（其中风险阻断 {blocked}）"
                job.message = message
                job.result = {
                    "zip_path": str(zip_path),
                    "files": files,
                    "failed": failed_items,
                    "total": total,
                    "ok": len(files),
                }
                jobs.publish_event(
                    job_id,
                    "done",
                    {**jobs.job_payload(job), "report": job.result},
                    db=db,
                )
    except Exception:  # noqa: BLE001 - surfaced to the UI as a stable message
        logger.exception("批量导出任务 %s 失败", job_id)
        failed_message = "批量导出失败：服务内部错误，详情见服务日志"
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is not None:
                _fail_job(db, job, failed_message)
