"""企业共享资产业务（对标易标第二批 v0.25.0）。

模块边界：

* :mod:`bidcraft.enterprise.assets` — 企业 / 成员 / 企业知识库容器与权限判定；
* :mod:`bidcraft.enterprise.evidence` — 资质、人员、文档证据的可追溯描述；
* :mod:`bidcraft.enterprise.matching` — 项目要求 ↔ 企业资产匹配与材料包。

所有写操作都在 Service 层写 ``AuditLog``（通过
:func:`bidcraft.audit.record_audit`），HTTP 路由只做参数与异常映射。
"""

from __future__ import annotations

from . import assets, evidence, matching

__all__ = ["assets", "evidence", "matching"]
