"""API routes: /jobs (从 app.py 迁移，URL 与响应格式不变)。"""

import asyncio
import sys

from anyio.to_thread import run_sync
from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..shared import Any, Job, Request, StreamingResponse, _job, jobs, session_scope, time  # noqa: F401

router = APIRouter()


def _poll_events(job_id: str, cursor: int) -> list:
    """DB I/O for one SSE poll; always executed in a worker thread."""

    return jobs.events_since(job_id, cursor, limit=200)


def _job_status(job_id: str) -> Job | None:
    """Read the job row in a short session (worker thread, never the loop)."""

    with session_scope() as db:
        return db.get(Job, job_id)


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    with session_scope() as db:
        return jobs.job_payload(_job(db, job_id))


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, after: int = 0) -> StreamingResponse:
    """Resumable Server-Sent Events for one generation job.

    The browser sends ``Last-Event-ID`` automatically when EventSource
    reconnects; ``?after=`` is available for non-EventSource clients.

    Every DB read in the polling loop runs via ``anyio.to_thread.run_sync`` so
    a slow SQL statement cannot block the event loop; disconnection checks and
    the yielded frames stay on the loop with the original payload shape.
    """

    header = request.headers.get("last-event-id") or ""
    try:
        last_id = max(int(header), int(after or 0))
    except ValueError:
        last_id = max(0, int(after or 0))
    # 一次性快照需要请求身份（_job 读 current_user ContextVar），留在事件循环；
    # 循环内的轮询不依赖请求上下文，全部下沉到线程。
    with session_scope() as db:
        job = _job(db, job_id)
        snapshot = jobs.job_payload(job)

    async def event_stream():
        cursor = last_id
        last_ping = time.monotonic()
        terminal = False
        yield "retry: 3000\n\n"
        yield jobs.sse_frame(None, "snapshot", snapshot)
        while True:
            # 客户端断开（标签页关闭/导航离开）后立即停止轮询；旧版同步生成器
            # 会一直查库直到任务进入终态，长任务期间白白占用连接与 worker。
            if await request.is_disconnected():
                break
            rows = await run_sync(_poll_events, job_id, cursor)
            current = await run_sync(_job_status, job_id)
            if current is None:
                break
            for row in rows:
                cursor = int(row.id)
                yield jobs.sse_frame(cursor, row.kind, row.data or {})
                if row.kind in {"done", "failed"}:
                    terminal = True
            if terminal or (current.status in jobs.TERMINAL_STATUSES and not rows):
                break
            if not rows and time.monotonic() - last_ping >= 15:
                yield ": ping\n\n"
                last_ping = time.monotonic()
            # 非阻塞、有上限的等待，既避免忙轮询也保持事件循环可响应断开。
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
