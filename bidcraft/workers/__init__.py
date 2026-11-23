"""后台任务 worker 集合。

5 个 runner 从 ``api.py`` 下沉而来；API 路由仍从 ``bidcraft.api`` 名字空间
按原名调用（见 api.py 顶部的 re-export），保持 URL/响应/业务行为不变。
"""

from .. import jobqueue
from .agent import _run_agent_job
from .compliance import _run_batch_check_job
from .evaluation import _run_evaluation_job
from .export import _run_batch_export_job
from .generation import _run_generation_job
from .images import _run_image_batch_job
from .knowledge import _run_kb_reindex_job

# Register runners so the DB-driven dispatcher can rebuild them by ``job.kind``.
# jobqueue._runner_for lazily imports this module, closing the import loop.
jobqueue._register("batch_check", _run_batch_check_job)
jobqueue._register("batch_export", _run_batch_export_job)
jobqueue._register("generate", _run_generation_job)
jobqueue._register("images", _run_image_batch_job)
jobqueue._register("kb_reindex", _run_kb_reindex_job)
jobqueue._register("agent", _run_agent_job)
jobqueue._register("evaluation", _run_evaluation_job)

__all__ = [
    "_run_agent_job",
    "_run_batch_check_job",
    "_run_batch_export_job",
    "_run_evaluation_job",
    "_run_generation_job",
    "_run_image_batch_job",
    "_run_kb_reindex_job",
]
