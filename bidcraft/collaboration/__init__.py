"""项目协作与版本管理（对标易标第三批 v0.26.0）。

模块划分：

* :mod:`bidcraft.collaboration.locks` —— 章节编辑锁（与审核锁分离）；
* :mod:`bidcraft.collaboration.revisions` —— 正文修订快照 / 对比 / 回滚；
* :mod:`bidcraft.collaboration.assignments` —— 章节责任分工。

领域层只操作 ``Session``，不感知 HTTP；事务提交由调用方（``session_scope``）
负责。路由层见 :mod:`bidcraft.api.routes.collaboration`。
"""

from __future__ import annotations

from .assignments import (
    ASSIGNMENT_STATUSES,
    AssignmentError,
    assign_chapter,
    assignment_payload,
    complete_assignment,
    list_assignments,
    update_assignment,
)
from .locks import (
    DEFAULT_LOCK_TTL_SECONDS,
    acquire_section_lock,
    check_section_lock,
    reap_expired_locks,
    release_section_lock,
    renew_section_lock,
)
from .revisions import (
    RestoreBlocked,
    diff_section_revision,
    get_section_revision,
    list_section_revisions,
    restore_section_revision,
    revision_payload,
    save_section_revision,
    snapshot_on_save,
)

__all__ = [
    "ASSIGNMENT_STATUSES",
    "DEFAULT_LOCK_TTL_SECONDS",
    "AssignmentError",
    "RestoreBlocked",
    "acquire_section_lock",
    "assign_chapter",
    "assignment_payload",
    "check_section_lock",
    "complete_assignment",
    "diff_section_revision",
    "get_section_revision",
    "list_assignments",
    "list_section_revisions",
    "reap_expired_locks",
    "release_section_lock",
    "renew_section_lock",
    "restore_section_revision",
    "revision_payload",
    "save_section_revision",
    "snapshot_on_save",
    "update_assignment",
]
