"""v0.31.3 阶段 3：文件系统副作用与 DB fencing 的边界验证。

明确不把 DB 事务回滚等同于文件系统回滚：批量导出 zip 在终态 fencing 失败
后由 worker 显式清理；成功路径保留归档。测试使用真实 PostgreSQL Job 行 +
临时文件系统目录。
"""

from __future__ import annotations

import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from bidcraft import jobqueue
from bidcraft.clock import utc_now
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.models import ImageAsset, Job, JobEvent, OutlineNode, Project, Section
from bidcraft.workers import export as export_worker
from bidcraft.workers import images as images_worker

TOKEN = "v0313-pg-export-token"


def _seed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "home", tmp_path)
    with session_scope() as db:
        project_id = str(uuid.uuid4())
        db.add(Project(id=project_id, name="PG export", owner_id=None))
        db.flush()
        node_id = str(uuid.uuid4())
        db.add(OutlineNode(id=node_id, project_id=project_id, heading="章节", status="drafted"))
        db.add(Section(id=str(uuid.uuid4()), node_id=node_id, body="正文", status="drafted", words=2))
        job_id = str(uuid.uuid4())
        db.add(
            Job(
                id=job_id,
                kind="batch_export",
                project_id=None,
                status="running",
                claim_token=TOKEN,
                worker_id=jobqueue._worker_id,
                claimed_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=300),
                heartbeat_at=utc_now(),
                payload={"owner_id": ""},
            )
        )
    return job_id, project_id


def _seed_image_job(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "home", tmp_path)
    with session_scope() as db:
        project_id = str(uuid.uuid4())
        db.add(Project(id=project_id, name="PG images", owner_id=None))
        db.flush()
        job_id = str(uuid.uuid4())
        db.add(
            Job(
                id=job_id,
                kind="images",
                project_id=project_id,
                status="running",
                claim_token=TOKEN,
                worker_id=jobqueue._worker_id,
                claimed_at=utc_now(),
                lease_expires_at=utc_now() + timedelta(seconds=300),
                heartbeat_at=utc_now(),
                payload={"owner_id": ""},
            )
        )
    return job_id, project_id


def _fake_export_docx(path: Path, *_args, **_kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake docx")
    return path


def _state(job_id):
    with session_scope() as db:
        row = db.get(Job, job_id)
        events = list(
            db.scalars(select(JobEvent.kind).where(JobEvent.job_id == job_id).order_by(JobEvent.id))
        )
        return row.status, row.result, events


def test_partial_zip_removed_when_final_fence_lost(pg_engine, monkeypatch, tmp_path):
    job_id, project_id = _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(export_worker, "enforce_export_gate", lambda *a, **k: None)
    monkeypatch.setattr(export_worker, "export_docx", _fake_export_docx)

    def lost_fence(*_args, **_kwargs):
        raise jobqueue.JobAborted("终态 fencing 失效")

    monkeypatch.setattr(export_worker, "finalize_job_result", lost_fence)
    with jobqueue.claim_context(TOKEN):
        export_worker._run_batch_export_job(job_id, [project_id], "docx")

    zip_path = settings.home / "output" / f"batch-{job_id[:8]}.zip"
    assert not zip_path.exists()
    status, result, events = _state(job_id)
    assert status == "running" and not result and "done" not in events


def test_zip_kept_after_successful_terminal_commit(pg_engine, monkeypatch, tmp_path):
    job_id, project_id = _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(export_worker, "enforce_export_gate", lambda *a, **k: None)
    monkeypatch.setattr(export_worker, "export_docx", _fake_export_docx)
    with jobqueue.claim_context(TOKEN):
        export_worker._run_batch_export_job(job_id, [project_id], "docx")

    zip_path = settings.home / "output" / f"batch-{job_id[:8]}.zip"
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as bundle:
        names = bundle.namelist()
        assert len(names) == 1 and names[0].endswith(".docx")
        assert bundle.read(names[0]) == b"fake docx"
    status, result, events = _state(job_id)
    assert status == "done" and result["zip_path"] == str(zip_path)
    assert events.count("done") == 1


def test_generated_image_survives_lost_fence_and_retry_skips(pg_engine, monkeypatch, tmp_path):
    job_id, project_id = _seed_image_job(monkeypatch, tmp_path)

    def fake_generate(db, project, *, label, node_id, heading, style, size):
        path = settings.project_dir(project.id) / "images" / f"{uuid.uuid4().hex}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake png")
        asset = ImageAsset(
            project_id=project.id,
            node_id=node_id,
            label=label,
            status="ready",
            file_path=str(path),
            filename=path.name,
            size_bytes=len(b"fake png"),
            provider="fake",
        )
        db.add(asset)
        db.flush()
        return asset

    monkeypatch.setattr(images_worker.images, "generate_image", fake_generate)
    original_finalize = images_worker.finalize_job_result

    def lost_fence(*_args, **_kwargs):
        raise jobqueue.JobAborted("终态 fencing 失效")

    monkeypatch.setattr(images_worker, "finalize_job_result", lost_fence)
    with jobqueue.claim_context(TOKEN):
        images_worker._run_image_batch_job(
            job_id, project_id, [{"label": "配图", "style": "x", "size": "512x512"}]
        )

    with session_scope() as db:
        asset = db.scalar(select(ImageAsset).where(ImageAsset.project_id == project_id))
        assert asset is not None and asset.status == "ready"
        image_path = Path(asset.file_path)
    assert image_path.exists()
    status, _result, events = _state(job_id)
    assert status == "running" and "done" not in events

    monkeypatch.setattr(images_worker, "finalize_job_result", original_finalize)
    with jobqueue.claim_context(TOKEN):
        images_worker._run_image_batch_job(
            job_id, project_id, [{"label": "配图", "style": "x", "size": "512x512"}]
        )
    status, result, events = _state(job_id)
    assert status == "done" and result["skipped"] == 1
    assert events.count("done") == 1
    assert image_path.exists()  # 外部文件不随 DB fencing 回滚自动删除
