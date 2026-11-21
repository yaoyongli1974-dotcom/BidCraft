"""API routes: /batch-jobs (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..app import FileResponse, Job, Path, auth, current_user, session_scope, settings  # noqa: F401

router = APIRouter()


@router.get("/batch-jobs/{job_id}/download")
def download_batch_zip(job_id: str) -> FileResponse:
    """Download the zip produced by a finished batch export."""

    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None or job.kind != "batch_export" or job.status != "done":
            raise LookupError("任务不存在")
        user = current_user() or {}
        owner_id = str((job.payload or {}).get("owner_id") or "")
        if user.get("role") != auth.ROLE_SUPER_ADMIN and owner_id != str(
            user.get("id") or ""
        ):
            raise LookupError("任务不存在")
        zip_path = str((job.result or {}).get("zip_path") or "")
        root = (settings.home / "output").resolve()
        path = Path(zip_path).resolve() if zip_path else None
        if path is None or root not in path.parents or not path.is_file():
            raise LookupError("导出包不存在")
    return FileResponse(path, filename=path.name)


