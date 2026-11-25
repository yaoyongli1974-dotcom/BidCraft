"""API routes: /admin (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

from ...models import KbDocument  # 文档实体未纳入 api.shared 导出面

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..shared import (  # noqa: F401
    UTC,
    Any,
    AuditLog,
    AuthSession,
    BillingSettingsPayload,
    CreditAdjust,
    File,
    Form,
    HTTPException,
    InvoiceReject,
    Job,
    KbAssetReview,
    KbChunk,
    KbCreate,
    KbDocMeta,
    KbDuplicateGroupResolve,
    KbUpdate,
    KnowledgeBase,
    ModelConfig,
    ModelCreate,
    ModelUpdate,
    Path,
    Plugin,
    PluginCreate,
    PluginUpdate,
    Project,
    QuotaCreate,
    QuotaItem,
    QuotaUpdate,
    RechargeOrder,
    RedeemCode,
    RedeemGenerate,
    Request,
    Response,
    RulePackageDocument,
    RulePackageValidateRequest,
    TermCreate,
    TermItem,
    TermUpdate,
    TextQaUpdate,
    UploadFile,
    User,
    UserCreate,
    UserUpdate,
    VipGrant,
    WechatConfigUpdate,
    __version__,
    _active_kb_reindex,
    _audit_item,
    _clamp_limit,
    _iso,
    _package_usage,
    _page_bounds,
    audit,
    auth,
    billing,
    boq,
    check_package_document,
    check_package_name,
    compliance,
    current_user,
    datetime,
    delete,
    document_package_name,
    encrypt_secret,
    func,
    hash_password,
    invoices,
    jobqueue,
    jobs,
    knowledge,
    knowledge_assets,
    list_packages,
    logger,
    material_policy,
    model_config,
    package_audit,
    package_store,
    plugin_env,
    plugin_items,
    plugin_name,
    plugin_pack,
    plugin_scope,
    plugin_transport,
    plugin_validate,
    plugins,
    quota_name,
    quota_price,
    quote,
    re,
    require_super,
    rule_package_record,
    select,
    session_scope,
    settings,
    shutil,
    terms,
    textqa,
    textqa_payload,
    threading,
    timedelta,
    unset_default_kb,
    unset_default_model,
    validate_kb,
    validate_kind,
    validate_package_text,
    wechat,
    wechat_config_payload,
    wechatpay,
)

router = APIRouter()


@router.get("/admin/models")
def list_models(request: Request, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    require_super(request)
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(ModelConfig)
                .order_by(ModelConfig.kind, ModelConfig.name)
                .limit(capped)
                .offset(start)
            )
        )
        return [model_config.model_payload(row) for row in rows]


@router.post("/admin/models", status_code=201)
def create_model(payload: ModelCreate, request: Request) -> dict[str, Any]:
    require_super(request)
    validate_kind(payload.kind, payload.provider)
    if payload.is_default and not payload.enabled:
        raise HTTPException(status_code=400, detail="默认模型必须启用")
    with session_scope() as db:
        if db.scalar(select(ModelConfig).where(ModelConfig.name == payload.name)):
            raise HTTPException(status_code=400, detail="模型名称已存在")
        if payload.is_default:
            unset_default_model(db, payload.kind)
        row = ModelConfig(
            name=payload.name,
            kind=payload.kind,
            provider=payload.provider,
            base_url=payload.base_url,
            model=payload.model,
            api_key_enc=encrypt_secret(payload.api_key),
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            options=payload.options,
            enabled=payload.enabled,
            is_default=payload.is_default,
        )
        db.add(row)
        db.flush()
        return model_config.model_payload(row)


@router.patch("/admin/models/{model_id}")
def update_model(model_id: str, payload: ModelUpdate, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(ModelConfig, model_id)
        if row is None:
            raise HTTPException(status_code=404, detail="模型不存在")
        data = payload.model_dump(exclude_unset=True)
        clear_key = data.pop("clear_api_key", False)
        api_key = data.pop("api_key", None)
        if "kind" in data or "provider" in data:
            validate_kind(data.get("kind", row.kind), data.get("provider", row.provider))
        if (
            "name" in data
            and data["name"] != row.name
            and db.scalar(select(ModelConfig).where(ModelConfig.name == data["name"]))
        ):
            raise HTTPException(status_code=400, detail="模型名称已存在")
        if data.get("is_default") and not data.get("enabled", row.enabled):
            raise HTTPException(status_code=400, detail="默认模型必须启用")
        if data.get("is_default"):
            unset_default_model(db, data.get("kind", row.kind))
        if clear_key:
            row.api_key_enc = ""
        elif api_key is not None and api_key != "":
            row.api_key_enc = encrypt_secret(api_key)
        for key, value in data.items():
            setattr(row, key, value)
        if row.is_default:
            unset_default_model(db, row.kind)
            row.is_default = True
        db.flush()
        return model_config.model_payload(row)


@router.delete("/admin/models/{model_id}")
def delete_model(model_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(ModelConfig, model_id)
        if row is None:
            raise HTTPException(status_code=404, detail="模型不存在")
        db.delete(row)
        return {"deleted": True, "id": model_id}


@router.post("/admin/models/{model_id}/test")
def test_model(model_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(ModelConfig, model_id)
        if row is None:
            raise HTTPException(status_code=404, detail="模型不存在")
        ok, message, latency = model_config.test_model(row)
        row.last_test_at = datetime.now(UTC).replace(tzinfo=None)
        row.last_test_ok = ok
        row.last_test_message = message[:1000]
        return {"ok": ok, "message": message, "latency_ms": latency}


@router.get("/admin/kbs")
def list_kbs(request: Request, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
    require_super(request)
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(KnowledgeBase)
                .order_by(KnowledgeBase.name)
                .limit(capped)
                .offset(start)
            )
        )
        return [knowledge.kb_payload(row, db) for row in rows]


@router.post("/admin/kbs", status_code=201)
def create_kb(payload: KbCreate, request: Request) -> dict[str, Any]:
    require_super(request)
    validate_kb(payload.kind, payload.provider)
    if payload.is_default and not payload.enabled:
        raise HTTPException(status_code=400, detail="默认知识库必须启用")
    with session_scope() as db:
        if db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == payload.name)):
            raise HTTPException(status_code=400, detail="知识库名称已存在")
        if payload.is_default:
            unset_default_kb(db)
        row = KnowledgeBase(
            name=payload.name,
            kind=payload.kind,
            provider=payload.provider,
            location=payload.location,
            api_key_enc=encrypt_secret(payload.api_key),
            dataset_id=payload.dataset_id,
            options=payload.options,
            enabled=payload.enabled,
            is_default=payload.is_default,
        )
        db.add(row)
        db.flush()
        return knowledge.kb_payload(row, db)


@router.patch("/admin/kbs/{kb_id}")
def update_kb(kb_id: str, payload: KbUpdate, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(KnowledgeBase, kb_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        data = payload.model_dump(exclude_unset=True)
        clear_key = data.pop("clear_api_key", False)
        api_key = data.pop("api_key", None)
        if "kind" in data or "provider" in data:
            validate_kb(data.get("kind", row.kind), data.get("provider", row.provider))
        if data.get("kind") == "api" and row.kind == "local":
            # FK 未开级联，SQLite 也不强制外键：显式清理文档实体与其块。
            db.execute(delete(KbDocument).where(KbDocument.kb_id == row.id))
            db.execute(delete(KbChunk).where(KbChunk.kb_id == row.id))
            row.chunk_count = 0
            row.indexed_at = None
        if (
            "location" in data
            and data["location"] != row.location
            and row.kind == "local"
        ):
            db.execute(delete(KbDocument).where(KbDocument.kb_id == row.id))
            db.execute(delete(KbChunk).where(KbChunk.kb_id == row.id))
            row.chunk_count = 0
            row.indexed_at = None
        if data.get("is_default") and not data.get("enabled", row.enabled):
            raise HTTPException(status_code=400, detail="默认知识库必须启用")
        if data.get("is_default"):
            unset_default_kb(db)
        if clear_key:
            row.api_key_enc = ""
        elif api_key is not None and api_key != "":
            row.api_key_enc = encrypt_secret(api_key)
        for key, value in data.items():
            setattr(row, key, value)
        if row.is_default:
            unset_default_kb(db)
            row.is_default = True
        db.flush()
        return knowledge.kb_payload(row, db)


@router.delete("/admin/kbs/{kb_id}")
def delete_kb(kb_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(KnowledgeBase, kb_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        db.execute(delete(KbDocument).where(KbDocument.kb_id == kb_id))
        db.execute(delete(KbChunk).where(KbChunk.kb_id == kb_id))
        db.delete(row)
        return {"deleted": True, "id": kb_id}


@router.post("/admin/kbs/{kb_id}/test")
def test_kb(kb_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(KnowledgeBase, kb_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        ok, message, latency = knowledge.test_kb(db, row)
        row.last_test_at = datetime.now(UTC).replace(tzinfo=None)
        row.last_test_ok = ok
        row.last_test_message = message[:1000]
        return {"ok": ok, "message": message, "latency_ms": latency}


@router.post("/admin/kbs/{kb_id}/reindex", status_code=202)
def reindex_kb(kb_id: str, request: Request) -> dict[str, Any]:
    """Queue a background rebuild of a local KB index and its vectors."""

    require_super(request)
    user = current_user() or {}
    with session_scope() as db:
        row = db.get(KnowledgeBase, kb_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        if row.kind != "local":
            raise HTTPException(status_code=400, detail="只有本地知识库需要建索引")
        if _active_kb_reindex(db, kb_id) is not None:
            raise HTTPException(status_code=409, detail="该知识库已有重建索引任务在运行")
        error = jobqueue.check_backpressure(db, user.get("id") or "")
        if error:
            raise HTTPException(status_code=429, detail=error)
        job = Job(
            project_id=None,
            kind="kb_reindex",
            status="queued",
            message="知识库重建索引排队中…",
            payload={
                "owner_id": user.get("id") or "",
                "kb_id": kb_id,
                "name": row.name,
            },
        )
        db.add(job)
        db.flush()
        job_id = job.id
        jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
        audit(db, None, "kb.reindex", {"kb_id": kb_id, "job_id": job_id})
        dispatch = job_id
    if dispatch is not None:
        jobqueue.submit(job_id=dispatch)
    return {"job_id": job_id}


@router.post("/admin/kbs/{kb_id}/embed")
def embed_kb(kb_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(KnowledgeBase, kb_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        if row.kind != "local":
            raise HTTPException(status_code=400, detail="仅本地知识库支持向量索引")
        if not row.chunk_count:
            raise HTTPException(status_code=400, detail="请先重建索引")
        if model_config.resolve_embedding_endpoint(db) is None:
            raise HTTPException(
                status_code=400,
                detail="未配置可用的向量模型；请在模型配置中添加 embedding 模型",
            )
        result = knowledge.build_vectors(db, row)
        return {**result, "kb": knowledge.kb_payload(row, db)}


@router.get("/admin/kbs/{kb_id}/documents")
def kb_documents(kb_id: str, request: Request) -> dict[str, Any]:
    """文档实体清单（版本/哈希/页码/有效期/状态），供溯源与运维核对。"""

    require_super(request)
    with session_scope() as db:
        row = db.get(KnowledgeBase, kb_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        return {
            "items": [
                knowledge.document_payload(doc)
                for doc in knowledge.list_documents(db, row)
            ]
        }


@router.get("/admin/kbs/{kb_id}/preview")
def kb_preview(kb_id: str, request: Request, q: str = "", limit: int = 5) -> dict[str, Any]:
    """检索预览：返回带 document_id/page/heading 溯源字段的候选片段。"""

    require_super(request)
    query = str(q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="检索内容不能为空")
    with session_scope() as db:
        row = db.get(KnowledgeBase, kb_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        try:
            if row.kind == "local":
                passages = knowledge.search_local(db, row, query, max(1, min(20, limit)))
            else:
                passages = knowledge.search_api(db, row, query, max(1, min(20, limit)))
        except knowledge.KnowledgeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "kb_id": kb_id,
            "query": query,
            "items": [
                {
                    "kb_id": passage.kb_id or kb_id,
                    "source": passage.source,
                    "text": passage.text,
                    "score": passage.score,
                    "document_id": passage.document_id,
                    "page": passage.page,
                    "heading": passage.heading,
                }
                for passage in passages
            ],
        }


@router.get("/admin/kbs/{kb_id}/docs")
def kb_docs(kb_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(KnowledgeBase, kb_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        docs = material_policy.doc_map(row)
        items = []
        for source, meta in sorted(docs.items()):
            record = dict(meta or {})
            kind = str(record.get("kind") or "")
            fields = {key: value for key, value in record.items() if key != "kind"}
            verdict = material_policy.assess(record)
            items.append(
                {
                    "source": source,
                    "kind": kind,
                    "fields": fields,
                    "ok": verdict.ok,
                    "reason": verdict.reason,
                }
            )
        counts = db.execute(
            select(KbChunk.source, func.count(KbChunk.id))
            .where(KbChunk.kb_id == kb_id)
            .group_by(KbChunk.source)
        ).all()
        sources = [
            {"source": str(source), "chunks": int(count)}
            for source, count in sorted(counts, key=lambda entry: str(entry[0] or ""))
            if str(source or "").strip()
        ]
        return {
            "policy": material_policy.load_policy(),
            "items": items,
            "sources": sources,
            "kinds": material_policy.kinds(),
        }


@router.put("/admin/kbs/{kb_id}/docs")
def kb_docs_upsert(kb_id: str, request: Request, payload: KbDocMeta) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(KnowledgeBase, kb_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        try:
            meta = payload.model_dump()
            extra = meta.pop("fields", None) or {}
            if isinstance(extra, dict):
                for key, value in extra.items():
                    if key in meta and meta.get(key) in ("", None):
                        meta[key] = "" if value is None else str(value)
            record = material_policy.set_meta(db, row, payload.source, meta)
        except knowledge.KnowledgeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(db, None, "kb.doc_meta", {"kb_id": kb_id, "source": payload.source})
        verdict = material_policy.assess(record)
        return {"source": payload.source, **record, "ok": verdict.ok, "reason": verdict.reason}


@router.delete("/admin/kbs/{kb_id}/docs")
def kb_docs_delete(kb_id: str, request: Request, source: str) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(KnowledgeBase, kb_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        options = dict(row.options or {})
        docs = dict(material_policy.doc_map(row))
        if source not in docs:
            raise HTTPException(status_code=404, detail="素材未登记")
        docs.pop(source)
        options["docs"] = docs
        row.options = options
        audit(db, None, "kb.doc_meta", {"kb_id": kb_id, "source": source, "deleted": True})
        return {"deleted": True, "source": source}


# --- v0.32 Phase 2：知识资产引擎 -------------------------------------------
# 查重（静态）路由必须先于 /assets/{document_id} 动态路由注册，避免
# "duplicates" 被当成 document_id 捕获。
@router.get("/admin/kbs/{kb_id}/assets")
def kb_assets_list(kb_id: str, request: Request) -> dict[str, Any]:
    """文档资产清单（结构化字段 + 审核状态），逐文档给出真实来源。"""

    require_super(request)
    with session_scope() as db:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        items = [
            knowledge_assets.asset_payload(doc, db)
            for doc in knowledge.list_documents(db, kb)
        ]
        return {"items": items}


@router.get("/admin/kbs/{kb_id}/assets/duplicates")
def kb_asset_duplicates(kb_id: str, request: Request) -> dict[str, Any]:
    """同库内查重候选组：按文件哈希/规范化题录聚合，供人工裁决。"""

    require_super(request)
    with session_scope() as db:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        try:
            items = knowledge_assets.find_duplicate_candidates(db, kb.id)
        except (ValueError, knowledge.KnowledgeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"items": items}


@router.post("/admin/kbs/{kb_id}/assets/duplicates/{group_id}/resolve")
def kb_asset_duplicate_resolve(
    kb_id: str, group_id: str, payload: KbDuplicateGroupResolve, request: Request
) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        actor = (current_user() or {}).get("username", "")
        try:
            result = knowledge_assets.resolve_duplicate_group(
                db, kb.id, group_id, payload.decision, payload.note, actor
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, knowledge.KnowledgeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "kb.duplicate_resolve",
            {
                "kb_id": kb.id,
                "group_id": group_id,
                "decision": payload.decision,
                "actor": actor,
            },
        )
        return result


@router.post("/admin/kbs/{kb_id}/assets/{document_id}/extract")
def kb_asset_extract(kb_id: str, document_id: str, request: Request) -> dict[str, Any]:
    """确定性抽取：只从文档真实来源生成草稿资产，不编造引用。"""

    require_super(request)
    with session_scope() as db:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        actor = (current_user() or {}).get("username", "")
        try:
            payload = knowledge_assets.propose_document_assets(db, kb.id, document_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, knowledge.KnowledgeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "kb.document_extract",
            {"kb_id": kb.id, "document_id": document_id, "actor": actor},
        )
        return payload


@router.post("/admin/kbs/{kb_id}/assets/{document_id}/review")
def kb_asset_review(
    kb_id: str, document_id: str, payload: KbAssetReview, request: Request
) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        actor = (current_user() or {}).get("username", "")
        try:
            result = knowledge_assets.review_asset(
                db, kb, document_id, payload.decision, payload.note, actor
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, knowledge.KnowledgeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "kb.document_review",
            {
                "kb_id": kb.id,
                "document_id": document_id,
                "decision": payload.decision,
                "actor": actor,
            },
        )
        return result


@router.get("/admin/plugins/catalog")
def plugin_catalog(request: Request) -> list[dict[str, Any]]:
    require_super(request)
    return [dict(item) for item in plugins.PLUGIN_CATALOG]


@router.get("/admin/plugins")
def list_plugins(
    request: Request, limit: int = 500, offset: int = 0
) -> list[dict[str, Any]]:
    require_super(request)
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(Plugin).order_by(Plugin.name).limit(capped).offset(start)
            )
        )
        return [plugins.plugin_payload(row) for row in rows]


@router.post("/admin/plugins", status_code=201)
def create_plugin(payload: PluginCreate, request: Request) -> dict[str, Any]:
    require_super(request)
    name = plugin_name(payload.name)
    transport = plugin_transport(payload.transport)
    command = (payload.command or "").strip()[:500]
    url = (payload.url or "").strip()[:500]
    scope = plugin_scope(payload.scope)
    owner_id = plugins.plugin_owner_id(payload.owner_id) if scope == "owner" else ""
    allow_private_net = bool(payload.allow_private_net)
    plugin_validate(transport, command, url, allow_private_net=allow_private_net)
    args = plugin_items(payload.args, field="args")
    env = plugin_env(payload.env)
    allowlist = plugin_items(payload.tool_allowlist, field="tool_allowlist", limits=200)
    with session_scope() as db:
        if db.scalar(select(Plugin).where(Plugin.name == name)):
            raise HTTPException(status_code=409, detail="插件名称已存在")
        if scope == "owner":
            if not owner_id:
                raise HTTPException(status_code=400, detail="个人插件必须指定 owner_id")
            if db.get(User, owner_id) is None:
                raise HTTPException(status_code=400, detail="owner_id 对应的用户不存在")
        row = Plugin(
            name=name,
            transport=transport,
            command=command,
            args=args,
            env=env,
            url=url,
            enabled=bool(payload.enabled),
            tool_allowlist=allowlist,
            mem_limit_mb=plugins.clamp_mem_limit(payload.mem_limit_mb),
            cpu_seconds=plugins.clamp_cpu_seconds(payload.cpu_seconds),
            scope=scope,
            owner_id=owner_id or None,
            allow_private_net=allow_private_net,
        )
        db.add(row)
        db.flush()
        audit(
            db,
            None,
            "plugin.create",
            {"plugin_id": row.id, "name": name, "transport": transport},
        )
        return plugins.plugin_payload(row)


@router.patch("/admin/plugins/{plugin_id}")
def update_plugin(
    plugin_id: str, payload: PluginUpdate, request: Request
) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(Plugin, plugin_id)
        if row is None:
            raise HTTPException(status_code=404, detail="插件不存在")
        data = payload.model_dump(exclude_unset=True)
        changed: list[str] = []
        if data.get("name") is not None:
            name = plugin_name(data["name"])
            if name != row.name and db.scalar(select(Plugin).where(Plugin.name == name)):
                raise HTTPException(status_code=409, detail="插件名称已存在")
            row.name = name
            changed.append("name")
        if data.get("transport") is not None:
            row.transport = plugin_transport(data["transport"])
            changed.append("transport")
        if data.get("command") is not None:
            row.command = str(data["command"]).strip()[:500]
            changed.append("command")
        if data.get("url") is not None:
            row.url = str(data["url"]).strip()[:500]
            changed.append("url")
        if "args" in data:
            row.args = plugin_items(data["args"], field="args")
            changed.append("args")
        if "env" in data:
            row.env = plugin_env(data["env"])
            changed.append("env")
        if "tool_allowlist" in data:
            row.tool_allowlist = plugin_items(
                data["tool_allowlist"], field="tool_allowlist", limits=200
            )
            changed.append("tool_allowlist")
        if data.get("enabled") is not None:
            row.enabled = bool(data["enabled"])
            changed.append("enabled")
            if row.enabled:
                plugins.reset_guard(db, row)
        if data.get("mem_limit_mb") is not None:
            row.mem_limit_mb = plugins.clamp_mem_limit(data["mem_limit_mb"])
            changed.append("mem_limit_mb")
        if data.get("cpu_seconds") is not None:
            row.cpu_seconds = plugins.clamp_cpu_seconds(data["cpu_seconds"])
            changed.append("cpu_seconds")
        scope = (
            plugin_scope(data["scope"])
            if data.get("scope") is not None
            else plugin_scope(row.scope)
        )
        owner_id = (
            plugins.plugin_owner_id(data["owner_id"])
            if data.get("owner_id") is not None
            else plugins.plugin_owner_id(row.owner_id)
        )
        if scope == "global":
            owner_id = ""
        elif not owner_id:
            raise HTTPException(status_code=400, detail="个人插件必须指定 owner_id")
        elif db.get(User, owner_id) is None:
            raise HTTPException(status_code=400, detail="owner_id 对应的用户不存在")
        if scope != row.scope or owner_id != (row.owner_id or ""):
            row.scope = scope
            row.owner_id = owner_id or None
            changed.extend(["scope", "owner_id"])
        if data.get("allow_private_net") is not None:
            row.allow_private_net = bool(data["allow_private_net"])
            changed.append("allow_private_net")
        plugin_validate(
            row.transport,
            row.command or "",
            row.url or "",
            allow_private_net=bool(row.allow_private_net),
        )
        db.flush()
        audit(
            db,
            None,
            "plugin.update",
            {"plugin_id": row.id, "name": row.name, "changed": sorted(changed)},
        )
        return plugins.plugin_payload(row)


@router.delete("/admin/plugins/{plugin_id}")
def delete_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(Plugin, plugin_id)
        if row is None:
            raise HTTPException(status_code=404, detail="插件不存在")
        audit(db, None, "plugin.delete", {"plugin_id": row.id, "name": row.name})
        db.delete(row)
        db.flush()
        return {"deleted": True, "id": plugin_id}


@router.post("/admin/plugins/{plugin_id}/test")
def test_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(Plugin, plugin_id)
        if row is None:
            raise HTTPException(status_code=404, detail="插件不存在")
        ref = plugins.snapshot(row)
        name = row.name
    ok, message, tools = plugins.test_plugin(ref)
    with session_scope() as db:
        row = db.get(Plugin, plugin_id)
        if row is not None:
            row.last_test_at = datetime.now(UTC).replace(tzinfo=None)
            row.last_test_ok = ok
            row.last_test_message = message[:1000]
            plugins.record_outcome(db, row, ok)
            audit(
                db,
                None,
                "plugin.test",
                {
                    "plugin_id": plugin_id,
                    "name": name,
                    "ok": ok,
                    "tool_count": len(tools),
                },
            )
    return {"ok": ok, "message": message, "tools": tools}


@router.get("/admin/plugins/{plugin_id}/package")
def export_plugin_package(plugin_id: str, request: Request) -> Response:
    """导出 .bidplugin 插件包（zip 附件下载）。"""
    require_super(request)
    with session_scope() as db:
        row = db.get(Plugin, plugin_id)
        if row is None:
            raise HTTPException(status_code=404, detail="插件不存在")
        data = plugin_pack.export_package(row)
        name = row.name
    filename = f"{name}.bidplugin.zip"
    # HTTP 头只能放 ASCII：非 ASCII 名走 RFC 5987 的 filename*。
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-") or "plugin"
    disposition = (
        f'attachment; filename="{ascii_name}.bidplugin.zip"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )


@router.post("/admin/plugins/import")
async def import_plugin_package(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    on_conflict: str = "replace",
    require_signature: bool | None = None,
) -> dict[str, Any]:
    """导入插件包：新建 201，同名升级 200，skip 冲突 409。"""
    require_super(request)
    if on_conflict not in {"replace", "skip"}:
        raise HTTPException(status_code=400, detail="on_conflict 只能是 replace/skip")
    data = await file.read()
    await file.close()
    if len(data) > plugin_pack.MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=400, detail="插件包过大（超过 5MB）")
    try:
        manifest = plugin_pack.parse_package(
            data, require_signature=require_signature
        )
        with session_scope() as db:
            row, created = plugin_pack.apply_manifest(
                db, manifest, on_conflict=on_conflict
            )
            payload = plugins.plugin_payload(row)
            audit(
                db,
                None,
                "plugin.import",
                {
                    "plugin_id": row.id,
                    "name": row.name,
                    "transport": row.transport,
                    "created": created,
                    "updated": not created,
                    "signed": plugin_pack.SIGNATURE_KEY in manifest,
                },
            )
    except plugin_pack.PluginPackConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.status_code = 201 if created else 200
    return payload


@router.get("/admin/billing")
def admin_billing(request: Request, limit: int = 50) -> dict[str, Any]:
    require_super(request)
    capped = max(1, min(200, int(limit)))
    with session_scope() as db:
        settings_payload = billing.billing_settings(db)
        orders = list(
            db.scalars(
                select(RechargeOrder)
                .order_by(RechargeOrder.created_at.desc())
                .limit(capped)
            )
        )
        codes = list(
            db.scalars(
                select(RedeemCode).order_by(RedeemCode.created_at.desc()).limit(capped)
            )
        )
        return {
            "settings": settings_payload,
            "stats": billing.admin_stats(db),
            "orders": [billing.order_payload(order) for order in orders],
            "codes": [
                {
                    "id": code.id,
                    "code": code.code,
                    "credits": code.credits,
                    "amount_cents": code.amount_cents,
                    "status": code.status,
                    "batch": code.batch,
                    "used_by": code.used_by,
                    "used_at": _iso(code.used_at),
                    "expires_at": _iso(code.expires_at),
                    "created_at": _iso(code.created_at),
                }
                for code in codes
            ],
        }


@router.put("/admin/billing")
def save_admin_billing(payload: BillingSettingsPayload, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        try:
            saved = billing.save_billing_settings(db, payload.model_dump(exclude_unset=True))
        except billing.BillingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"settings": saved, "stats": billing.admin_stats(db)}


@router.post("/admin/redeem-codes", status_code=201)
def generate_codes(payload: RedeemGenerate, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        try:
            codes = billing.generate_redeem_codes(
                db,
                count=payload.count,
                credits=payload.credits,
                amount_cents=payload.amount_cents,
                days=payload.days,
                created_by=request.state.user["id"],
            )
        except billing.BillingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"codes": codes}


@router.post("/admin/users/{user_id}/credits")
def adjust_credits(user_id: str, payload: CreditAdjust, request: Request) -> dict[str, Any]:
    require_super(request)
    if payload.amount == 0:
        raise HTTPException(status_code=400, detail="调整数量不能为 0")
    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        try:
            billing.add_credits(
                db,
                user.id,
                payload.amount,
                kind="admin",
                note=payload.note or f"管理员调整 {payload.amount:+d}",
                ref_type="admin",
                ref_id=request.state.user["id"],
            )
        except billing.InsufficientCredits as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "user": auth.user_payload(user),
            "transactions": billing.transactions(db, user.id),
        }


@router.post("/admin/users/{user_id}/vip")
def grant_vip(user_id: str, payload: VipGrant, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.parent_id:
            raise HTTPException(status_code=400, detail="子账号继承主账号 VIP，不能单独设置")
        now = datetime.now(UTC).replace(tzinfo=None)
        if payload.days == 0:
            user.vip_expires_at = None
        else:
            base = user.vip_expires_at if (user.vip_expires_at and user.vip_expires_at > now) else now
            user.vip_expires_at = base + timedelta(days=payload.days)
        db.flush()
        audit(
            db,
            None,
            "user.vip",
            {"user_id": user.id, "days": payload.days, "note": payload.note},
        )
        return {"user": auth.user_payload(user)}


@router.get("/admin/users")
def list_users(
    request: Request, limit: int = 500, offset: int = 0
) -> list[dict[str, Any]]:
    require_super(request)
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(User)
                .order_by(User.created_at)
                .limit(capped)
                .offset(start)
            )
        )
        return [auth.user_payload(row) for row in rows]


@router.post("/admin/users", status_code=201)
def create_user(payload: UserCreate, request: Request) -> dict[str, Any]:
    require_super(request)
    if payload.role not in auth.ROLES:
        raise HTTPException(status_code=400, detail="角色不合法")
    with session_scope() as db:
        if db.scalar(select(User).where(User.username == payload.username)):
            raise HTTPException(status_code=400, detail="用户名已存在")
        row = User(
            username=payload.username,
            display_name=payload.display_name or payload.username,
            password_hash=hash_password(payload.password),
            role=payload.role,
            active=True,
        )
        db.add(row)
        db.flush()
        audit(
            db,
            None,
            "admin.user.create",
            {"user_id": row.id, "username": row.username, "role": row.role},
        )
        return auth.user_payload(row)


@router.patch("/admin/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(User, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        data = payload.model_dump(exclude_unset=True)
        password = data.pop("password", None)
        if data.get("role") is not None and data["role"] not in auth.ROLES:
            raise HTTPException(status_code=400, detail="角色不合法")
        if user_id == request.state.user["id"]:
            if data.get("active") is False:
                raise HTTPException(status_code=400, detail="不能停用当前登录账号")
            if data.get("role") and data["role"] != auth.ROLE_SUPER_ADMIN:
                raise HTTPException(status_code=400, detail="不能降低当前账号权限")
        if row.role == auth.ROLE_SUPER_ADMIN and (
            data.get("role") not in {None, auth.ROLE_SUPER_ADMIN}
            or data.get("active") is False
        ):
            others = db.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.role == auth.ROLE_SUPER_ADMIN,
                    User.active.is_(True),
                    User.id != row.id,
                )
            )
            if not others:
                raise HTTPException(status_code=400, detail="至少保留一个启用的超级管理员")
        old_role = row.role
        changed: list[str] = []
        if password:
            changed.append("password")
        for key, value in data.items():
            if getattr(row, key, None) != value:
                changed.append(key)
        if password:
            row.password_hash = hash_password(password)
            auth.revoke_user_sessions(db, row.id)
        for key, value in data.items():
            setattr(row, key, value)
        db.flush()
        # 审计只记字段名与角色变化，绝不写 password / password_hash。
        if changed:
            audit(
                db,
                None,
                "admin.user.update",
                {"user_id": row.id, "username": row.username, "fields": sorted(changed)},
            )
        if "role" in changed:
            audit(
                db,
                None,
                "admin.user.role",
                {"user_id": row.id, "username": row.username, "from": old_role, "to": row.role},
            )
        if password:
            audit(db, None, "admin.user.password", {"user_id": row.id, "username": row.username})
        if "active" in changed:
            audit(
                db,
                None,
                "admin.user.deactivate" if not row.active else "admin.user.activate",
                {"user_id": row.id, "username": row.username, "active": bool(row.active)},
            )
        return auth.user_payload(row)


@router.delete("/admin/users/{user_id}")
def delete_user(user_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    if user_id == request.state.user["id"]:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    with session_scope() as db:
        row = db.get(User, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if row.role == auth.ROLE_SUPER_ADMIN:
            others = db.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.role == auth.ROLE_SUPER_ADMIN,
                    User.active.is_(True),
                    User.id != row.id,
                )
            )
            if not others:
                raise HTTPException(status_code=400, detail="至少保留一个启用的超级管理员")
        from ...services.purge import delete_user_rows

        audit(
            db,
            None,
            "admin.user.delete",
            {"user_id": row.id, "username": row.username, "role": row.role},
        )
        # 与账户注销共用同一套关联清理：PostgreSQL 强制外键时漏表会直接失败。
        delete_user_rows(db, row.id)
        db.delete(row)
        return {"deleted": True, "id": user_id}


@router.get("/admin/rule-packages")
def admin_list_rule_packages(
    request: Request, include_hidden: bool = False
) -> list[dict[str, Any]]:
    require_super(request)
    with session_scope() as db:
        usage = _package_usage(db)
    return [
        {**item, "usage": usage.get(str(item["name"]), 0)}
        for item in list_packages(include_hidden=include_hidden)
    ]


@router.post("/admin/rule-packages/validate")
def admin_validate_rule_package(
    payload: RulePackageValidateRequest, request: Request
) -> dict[str, Any]:
    require_super(request)
    return validate_package_text(payload.document, payload.name)


@router.get("/admin/rule-packages/{name}")
def admin_get_rule_package(name: str, request: Request) -> dict[str, Any]:
    require_super(request)
    name = check_package_name(name)
    if not package_store.package_exists(name):
        raise HTTPException(status_code=404, detail="规则包不存在")
    try:
        document = package_store.read_document(name)
    except package_store.StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return rule_package_record(name, document)


@router.post("/admin/rule-packages", status_code=201)
def admin_create_rule_package(
    payload: RulePackageDocument, request: Request
) -> dict[str, Any]:
    require_super(request)
    name = document_package_name(payload.document)
    if not name:
        check_package_document(payload.document)
        raise HTTPException(status_code=400, detail="YAML 缺少 package 字段")
    name = check_package_name(name)
    if package_store.package_exists(name):
        raise HTTPException(status_code=409, detail=f"规则包已存在: {name}")
    check_package_document(payload.document, name)
    package_store.save_user_package(name, payload.document)
    package_store.unhide_package(name)
    package_audit("package.create", name)
    return rule_package_record(name, payload.document)


@router.put("/admin/rule-packages/{name}")
def admin_update_rule_package(
    name: str, payload: RulePackageDocument, request: Request
) -> dict[str, Any]:
    require_super(request)
    name = check_package_name(name)
    if not package_store.package_exists(name):
        raise HTTPException(status_code=404, detail="规则包不存在")
    check_package_document(payload.document, name)
    package_store.save_user_package(name, payload.document)
    package_store.unhide_package(name)
    package_audit("package.update", name)
    return rule_package_record(name, payload.document)


@router.delete("/admin/rule-packages/{name}")
def admin_delete_rule_package(name: str, request: Request) -> dict[str, Any]:
    require_super(request)
    name = check_package_name(name)
    if not package_store.package_exists(name):
        raise HTTPException(status_code=404, detail="规则包不存在")
    with session_scope() as db:
        usage = _package_usage(db).get(name, 0)
    if usage:
        raise HTTPException(
            status_code=409, detail=f"仍有 {usage} 个项目使用该规则包"
        )
    package_store.hide_package(name)
    package_audit("package.delete", name)
    return {"name": name, "hidden": True}


@router.post("/admin/rule-packages/{name}/restore")
def admin_restore_rule_package(
    name: str, request: Request, reset_override: bool = False
) -> dict[str, Any]:
    require_super(request)
    name = check_package_name(name)
    if not package_store.package_exists(name):
        raise HTTPException(status_code=404, detail="规则包不存在")
    if reset_override and not package_store.is_builtin(name):
        raise HTTPException(status_code=400, detail="仅内置规则包可恢复内置原文")
    if reset_override:
        package_store.remove_user_package(name)
    package_store.unhide_package(name)
    try:
        document = package_store.read_document(name)
    except package_store.StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    package_audit("package.restore", name)
    return rule_package_record(name, document)


@router.get("/admin/quotas")
def list_quotas(
    request: Request, search: str = "", limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    require_super(request)
    term = (search or "").strip()
    with session_scope() as db:
        statement = select(QuotaItem)
        if term:
            pattern = f"%{term}%"
            statement = statement.where(
                QuotaItem.name.ilike(pattern)
                | QuotaItem.spec.ilike(pattern)
                | QuotaItem.category.ilike(pattern)
            )
        capped = max(1, min(int(limit), 500))
        start = max(0, int(offset))
        rows = list(
            db.scalars(
                statement.order_by(QuotaItem.name, QuotaItem.id)
                .limit(capped)
                .offset(start)
            )
        )
        return [boq.quota_payload(row) for row in rows]


@router.get("/admin/quotas/match")
def match_quota_candidates(
    request: Request, name: str = "", spec: str = ""
) -> list[dict[str, Any]]:
    require_super(request)
    with session_scope() as db:
        quotas = list(
            db.scalars(select(QuotaItem).order_by(QuotaItem.name, QuotaItem.id))
        )
        item = {"name": name, "spec": spec}
        return [
            {
                "id": quota.id,
                "name": quota.name,
                "spec": quota.spec or "",
                "unit": quota.unit or "",
                "unit_price": round(float(quota.unit_price or 0.0), 4),
                "score": round(float(score), 4),
            }
            for score, quota in boq.match_quotas(item, quotas, limit=5)
        ]


@router.post("/admin/quotas", status_code=201)
def create_quota(payload: QuotaCreate, request: Request) -> dict[str, Any]:
    require_super(request)
    name = quota_name(payload.name)
    price = quota_price(payload.unit_price)
    with session_scope() as db:
        quota = QuotaItem(
            name=name,
            spec=(payload.spec or "").strip()[:200],
            unit=(payload.unit or "").strip()[:20],
            unit_price=price,
            category=(payload.category or "").strip()[:40],
            note=(payload.note or "").strip()[:200],
        )
        db.add(quota)
        db.flush()
        audit(db, None, "quota.create", {"quota_id": quota.id, "name": name})
        return boq.quota_payload(quota)


@router.patch("/admin/quotas/{quota_id}")
def update_quota(
    quota_id: str, payload: QuotaUpdate, request: Request
) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        quota = db.get(QuotaItem, quota_id)
        if quota is None:
            raise LookupError(f"定额不存在：{quota_id}")
        changed: dict[str, Any] = {}
        if payload.name is not None:
            quota.name = quota_name(payload.name)
            changed["name"] = quota.name
        if payload.unit_price is not None:
            quota.unit_price = quota_price(payload.unit_price)
            changed["unit_price"] = quota.unit_price
        for field, limit in (
            ("spec", 200),
            ("unit", 20),
            ("category", 40),
            ("note", 200),
        ):
            value = getattr(payload, field)
            if value is not None:
                setattr(quota, field, value.strip()[:limit])
                changed[field] = getattr(quota, field)
        db.flush()
        audit(
            db,
            None,
            "quota.update",
            {"quota_id": quota.id, "name": quota.name, "changed": sorted(changed)},
        )
        return boq.quota_payload(quota)


@router.delete("/admin/quotas/{quota_id}")
def delete_quota(quota_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        quota = db.get(QuotaItem, quota_id)
        if quota is None:
            raise LookupError(f"定额不存在：{quota_id}")
        audit(db, None, "quota.delete", {"quota_id": quota.id, "name": quota.name})
        db.delete(quota)
        db.flush()
        return {"deleted": True, "id": quota_id}


@router.get("/admin/terms")
def list_terms(
    request: Request,
    search: str = "",
    kind: str = "",
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    require_super(request)
    capped, start = _page_bounds(limit, offset)
    with session_scope() as db:
        try:
            rows = terms.list_terms(
                db, search=search, kind=kind, limit=capped, offset=start
            )
        except terms.TermError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return [terms.term_payload(row) for row in rows]


@router.post("/admin/terms", status_code=201)
def create_term(payload: TermCreate, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        try:
            row = terms.create_term(
                db,
                kind=payload.kind,
                source=payload.source,
                target=payload.target,
                note=payload.note,
                enabled=payload.enabled,
            )
        except terms.TermExists as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except terms.TermError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "term.create",
            {"term_id": row.id, "kind": row.kind, "source": row.source},
        )
        return terms.term_payload(row)


@router.patch("/admin/terms/{term_id}")
def update_term(term_id: str, payload: TermUpdate, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(TermItem, term_id)
        if row is None:
            raise LookupError(f"术语不存在：{term_id}")
        changes = {
            field: getattr(payload, field)
            for field in ("kind", "source", "target", "note", "enabled")
            if getattr(payload, field) is not None
        }
        try:
            terms.update_term(db, row, **changes)
        except terms.TermExists as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except terms.TermError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "term.update",
            {
                "term_id": row.id,
                "kind": row.kind,
                "source": row.source,
                "changed": sorted(changes),
            },
        )
        return terms.term_payload(row)


@router.delete("/admin/terms/{term_id}")
def delete_term(term_id: str, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        row = db.get(TermItem, term_id)
        if row is None:
            raise LookupError(f"术语不存在：{term_id}")
        audit(
            db,
            None,
            "term.delete",
            {"term_id": row.id, "kind": row.kind, "source": row.source},
        )
        terms.delete_term(db, row)
        return {"deleted": True, "id": term_id}


@router.get("/admin/textqa")
def get_textqa(request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        return textqa_payload(db)


@router.put("/admin/textqa")
def put_textqa(payload: TextQaUpdate, request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        try:
            saved = textqa.save_custom(db, payload.typos)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(db, None, "textqa.config", {"count": len(saved)})
        return textqa_payload(db)


@router.get("/admin/wechat")
def admin_get_wechat(request: Request) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        return wechat_config_payload(db)


@router.put("/admin/wechat")
def admin_update_wechat(payload: WechatConfigUpdate, request: Request) -> dict[str, Any]:
    require_super(request)
    from ...models import WechatConfig
    from ...security import encrypt_secret

    with session_scope() as db:
        row = db.get(WechatConfig, "default")
        if row is None:
            row = WechatConfig(id="default")
            db.add(row)
        changed: list[str] = []
        if payload.app_id is not None:
            row.app_id = payload.app_id.strip()[:120]
            changed.append("app_id")
        if payload.clear_secret:
            row.app_secret_enc = ""
            changed.append("clear_secret")
        elif payload.app_secret:
            row.app_secret_enc = encrypt_secret(payload.app_secret)
            changed.append("app_secret")
        if payload.redirect_uri is not None:
            row.redirect_uri = payload.redirect_uri.strip()[:500]
            changed.append("redirect_uri")
        if payload.enabled is not None:
            row.enabled = bool(payload.enabled)
            changed.append("enabled")
        if payload.mock is not None:
            row.mock = bool(payload.mock)
            changed.append("mock")
        if not changed:
            raise HTTPException(status_code=400, detail="没有需要更新的字段")
        db.flush()
        audit(db, None, "wechat.config", {"keys": changed})
        return wechat_config_payload(db)


@router.post("/admin/wechat/test")
def admin_test_wechat(request: Request) -> dict[str, Any]:
    require_super(request)
    import httpx

    with session_scope() as db:
        cfg = wechat.effective(db)
    if cfg["mock"]:
        return {"ok": True, "message": "模拟模式可用：登录会生成本地测试账号，不访问微信服务器"}
    if not (cfg["app_id"] and cfg["app_secret"] and cfg["redirect_uri"]):
        return {"ok": False, "message": "未配置完整的 AppID / AppSecret / 回调地址"}
    try:
        response = httpx.get(
            wechat.AUTHORIZE_URL,
            params={"appid": cfg["app_id"], "redirect_uri": cfg["redirect_uri"]},
            timeout=wechat.TIMEOUT,
        )
    except httpx.HTTPError:
        logger.exception("微信开放平台连通性测试失败")
        return {"ok": False, "message": "无法连接微信开放平台，请检查网络或稍后重试"}
    if response.status_code >= 500:
        return {"ok": False, "message": f"微信开放平台返回 {response.status_code}，请稍后重试"}
    return {"ok": True, "message": "配置完整，微信开放平台可达；请用真实扫码验证回调"}


@router.get("/admin/health")
def admin_health(request: Request) -> dict[str, Any]:
    """完整运行状态，仅超级管理员可见（公开 /health 保持最小化）。"""
    import platform as platform_module

    from ...health import global_metrics

    require_super(request)
    with session_scope() as db:
        endpoint = model_config.resolve_chat_endpoint(db)
        embedding = model_config.resolve_embedding_endpoint(db)
        models = db.scalar(select(func.count()).select_from(ModelConfig)) or 0
        kbs = db.scalar(select(func.count()).select_from(KnowledgeBase)) or 0
        users = db.scalar(select(func.count()).select_from(User)) or 0
        # 库内聚合指标随端点本身一并返回；app.create_app 的兼容包装检测到
        # 该键后会跳过二次计算，响应键与既有 /admin/health 完全一致。
        metrics = global_metrics(db)
    started = getattr(_core.app.state, "started_at", None)
    db_path = settings.home / "bidcraft.db"
    db_size = db_path.stat().st_size if db_path.is_file() else 0
    try:
        disk_free = shutil.disk_usage(settings.home).free
    except OSError:  # pragma: no cover - 极少数文件系统不支持
        disk_free = 0
    uptime = int((datetime.now(UTC) - started).total_seconds()) if started else 0
    return {
        "status": "ok",
        "version": __version__,
        "started_at": started.isoformat() if started else None,
        "uptime_seconds": max(0, uptime),
        "python": platform_module.python_version(),
        "platform": platform_module.platform(),
        "home": str(settings.home),
        "db_size_bytes": db_size,
        "disk_free_bytes": disk_free,
        "web_ready": (Path(__file__).resolve().parent.parent / "web_dist" / "index.html").is_file(),
        "offline": settings.offline,
        "docs_enabled": settings.docs_enabled,
        "health_public": settings.health_public,
        "secure_cookie": settings.secure_cookie,
        "model_configured": bool(endpoint),
        "model_label": endpoint.label if endpoint else "",
        "embedding_configured": bool(embedding),
        "embedding_model": embedding.model_name if embedding else "",
        "models": models,
        "knowledge_bases": kbs,
        "users": users,
        "auth_required": True,
        "registration": True,
        "wechat_login": wechat.available(),
        "wechat_pay": wechatpay.available(),
        "global_metrics": metrics,
    }


@router.get("/admin/audit")
def admin_audit(
    request: Request,
    limit: int = 50,
    before: int | None = None,
    project_id: str = "",
) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        statement = (
            select(AuditLog)
            .order_by(AuditLog.id.desc())
            .limit(_clamp_limit(limit))
        )
        if before is not None:
            statement = statement.where(AuditLog.id < before)
        if project_id:
            statement = statement.where(AuditLog.project_id == project_id)
        rows = list(db.scalars(statement))
        ids = {row.project_id for row in rows if row.project_id}
        names = (
            {
                item.id: item.name
                for item in db.scalars(select(Project).where(Project.id.in_(ids)))
            }
            if ids
            else {}
        )
        return {
            "items": [_audit_item(row, names) for row in rows],
            "next_before": rows[-1].id if len(rows) == _clamp_limit(limit) else None,
        }


@router.get("/admin/invoices")
def admin_invoices(request: Request, status: str = "") -> list[dict[str, Any]]:
    require_super(request)
    with session_scope() as db:
        try:
            rows = invoices.list_all(db, status=status)
        except invoices.InvoiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ids = {row.user_id for row in rows}
        names = {
            user.id: (user.display_name or user.username)
            for user in db.scalars(select(User).where(User.id.in_(ids))).all()
        } if ids else {}
        return [invoices.invoice_payload(row, username=names.get(row.user_id, "")) for row in rows]


@router.post("/admin/invoices/{invoice_id}/issue")
async def admin_invoice_issue(
    invoice_id: str,
    request: Request,
    invoice_no: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    require_super(request)
    content = await file.read()
    if len(content) > invoices.MAX_INVOICE_BYTES:
        raise HTTPException(status_code=400, detail="发票文件不能超过 10MB")
    with session_scope() as db:
        try:
            row = invoices.issue_request(
                db,
                invoice_id,
                invoice_no=invoice_no,
                filename=file.filename or "",
                content=content,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except invoices.InvoiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(db, None, "invoice.issue", {"invoice_id": row.id, "invoice_no": row.invoice_no})
        return invoices.invoice_payload(row)


@router.post("/admin/invoices/{invoice_id}/reject")
def admin_invoice_reject(
    invoice_id: str, payload: InvoiceReject, request: Request
) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        try:
            row = invoices.reject_request(db, invoice_id, payload.reason)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except invoices.InvoiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(db, None, "invoice.reject", {"invoice_id": row.id})
        return invoices.invoice_payload(row)


@router.post("/admin/accounts/purge")
def admin_purge_accounts(request: Request, dry_run: bool = False) -> dict[str, Any]:
    require_super(request)
    with session_scope() as db:
        result = compliance.purge_due_accounts(db, dry_run=dry_run)
        audit(db, None, "account.purge_batch", {"purged": len(result["purged"]), "dry_run": dry_run})
        db.flush()
        return result


