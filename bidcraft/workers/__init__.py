"""后台任务 worker 集合。

5 个 runner 从 ``api.py`` 下沉而来；API 路由仍从 ``bidcraft.api`` 名字空间
按原名调用（见 api.py 顶部的 re-export），保持 URL/响应/业务行为不变。
"""

from .compliance import _run_batch_check_job
from .export import _run_batch_export_job
from .generation import _run_generation_job
from .images import _run_image_batch_job
from .knowledge import _run_kb_reindex_job

__all__ = [
    "_run_batch_check_job",
    "_run_batch_export_job",
    "_run_generation_job",
    "_run_image_batch_job",
    "_run_kb_reindex_job",
]
