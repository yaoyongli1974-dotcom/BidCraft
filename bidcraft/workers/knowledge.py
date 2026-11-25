"""知识库索引重建后台任务。

索引/向量逻辑在 :mod:`bidcraft.knowledge`；这里只负责 job 行/SSE 编排。
"""

from __future__ import annotations

import logging
from typing import Any

from .. import jobs, knowledge, model_config
from ..config import settings
from ..db import session_scope
from ..jobqueue import (
    JobAborted,
    assert_claim_current,
    fenced_job,
    finalize_job_result,
    log_job_failure,
    raise_if_aborted,
)
from ..services.job_support import _fail_job

logger = logging.getLogger("bidcraft.workers")


def _run_kb_reindex_job(job_id: str, kb_id: str) -> None:
    """Background worker: rebuild a local KB token index, then its vectors.

    The HTTP request returns as soon as the job row exists, so a large knowledge
    base no longer blocks the API worker. Each stage uses its own short session
    and publishes a progress event; the final ``done`` event carries the payload
    the old synchronous endpoint used to return.
    """

    from ..models import KnowledgeBase  # imported inside create_app at request time

    try:
        raise_if_aborted()
        assert_claim_current(job_id)
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is None:
                return
            if job.status != "running":
                return
            job.status = "running"
            job.progress = 5
            job.message = "正在重建知识库索引…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)

        raise_if_aborted()  # 文本索引前检查租约/关闭信号
        with session_scope() as db:
            kb = db.get(KnowledgeBase, kb_id)
            if kb is None:
                raise knowledge.KnowledgeError("知识库不存在")
            indexed = knowledge.index_local(db, kb)
            db.flush()
            kb_data = knowledge.kb_payload(kb, db)
            job = fenced_job(db, job_id)
            if job is not None:
                job.progress = 60
                job.message = f"文本索引完成：{indexed} 个文本块"
                jobs.publish_event(
                    job_id,
                    "reindex",
                    {**jobs.job_payload(job), "indexed": indexed, "kb": kb_data},
                    db=db,
                )

        vector: dict[str, Any] | None = None
        if settings.kb_vector_enabled:
            raise_if_aborted()  # 向量索引前检查租约/关闭信号
            with session_scope() as db:
                kb = db.get(KnowledgeBase, kb_id)
                if kb is None:
                    raise knowledge.KnowledgeError("知识库不存在")
                if model_config.resolve_embedding_endpoint(db) is not None:
                    job = fenced_job(db, job_id)
                    if job is not None:
                        job.progress = 70
                        job.message = "正在重建向量索引…"
                        jobs.publish_event(
                            job_id, "status", jobs.job_payload(job), db=db
                        )
                    vector = knowledge.build_vectors(db, kb)
                    db.flush()
                    kb_data = knowledge.kb_payload(kb, db)
                    job = fenced_job(db, job_id)
                    if job is not None:
                        job.progress = 95
                        job.message = "向量索引完成"
                        jobs.publish_event(
                            job_id,
                            "vector",
                            {
                                **jobs.job_payload(job),
                                "vector": vector,
                                "kb": kb_data,
                            },
                            db=db,
                        )

        raise_if_aborted()
        assert_claim_current(job_id)
        finalize_job_result(
            job_id,
            result={"indexed": indexed, "vector": vector, "kb": kb_data},
            message=f"索引重建完成：{indexed} 个文本块",
        )
    except JobAborted:
        # 中止不是业务失败：保持 running，由 reaper 重新排队。
        logger.info("知识库索引任务 %s 已中止，等待重新排队", job_id)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a stable message
        log_job_failure(logger)
        if isinstance(exc, knowledge.KnowledgeError):
            failed_message = "知识库索引失败：本地目录不可用或内容无法解析，详情见服务日志"
        else:
            failed_message = "知识库索引失败：服务内部错误，详情见服务日志"
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is not None:
                _fail_job(db, job, failed_message)
