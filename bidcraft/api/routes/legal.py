"""API routes: /legal (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..app import HTTPException, compliance  # noqa: F401

router = APIRouter()


@router.get("/legal/versions")
def legal_versions() -> dict[str, str]:
    return {"version": compliance.LEGAL_VERSION, "terms": "terms", "privacy": "privacy"}


@router.get("/legal/{name}")
def legal_document(name: str) -> dict[str, str]:
    try:
        text = compliance.legal_doc(name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="文档不存在") from exc
    return {"name": name, "text": text}


