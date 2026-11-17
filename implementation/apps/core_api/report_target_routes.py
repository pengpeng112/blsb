"""上报业务目录 API（LT-01）：目录 CRUD + 单业务摘要 + 上报中心总览。

- 租户一律取 principal.tenant_id，不接受客户端传 tenant；
- 返回格式沿用 app.py 的 ok(data, request_id)/错误处理约定；
- summary/overview 经 PluginRegistry 的 HOOK_TARGET_SUMMARY 钩子取数：
  无钩子认领时返回 data_source="none"、buckets 全 0（不报 500）；
  钩子抛异常时 overview 中该项写 error 字段，其余照常返回（局部失败隔离）。
"""
from __future__ import annotations

from fastapi import Depends

from contracts import REPORT_BUCKETS, ok
from contracts.api import (ROLE_BUSINESS_REVIEWER, ROLE_CONFIG_EDITOR,
                           ROLE_PLATFORM_ADMIN)
from core_db.engine import utcnow
from plugins import report_targets as RT
from plugins.domain_hooks import TargetSummary


def _target_json(t) -> dict:
    return {
        "code": t.code, "name": t.name, "category": t.category,
        "authority": t.authority, "plugin_key": t.plugin_key,
        "destination_id": t.destination_id, "mode": t.mode,
        "lifecycle": t.lifecycle, "runtime_kind": t.runtime_kind,
        "owner_dept": t.owner_dept, "contact": t.contact,
        "sole_pusher": t.sole_pusher, "sole_pusher_note": t.sole_pusher_note,
        "spec_docs": t.spec_docs or [], "readme_path": t.readme_path,
        "schedule_note": t.schedule_note, "extra": t.extra or {},
        "revision": t.revision, "updated_by": t.updated_by,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _destination_summary(db, destination_id: str | None) -> dict | None:
    """RT-01：业务详情页关联目的地的只读脱敏摘要。

    - 前端原先请求不存在的 GET /api/v1/destinations/{id}（404），改为在
      get_target 同一次查询中附带本摘要；
    - host_port 仅解析 scheme://host:port，绝不返回完整 URL path/query 或
      任何凭据引用值；receipt_rules 为回执判定规则（非敏感）原样返回。
    """
    if not destination_id:
        return None
    from core_db.models import ConfigRevision, Destination
    dest = db.get(Destination, destination_id)
    if dest is None:
        return None
    cfg: dict = {}
    if dest.config_revision:
        cr = db.get(ConfigRevision, dest.config_revision)
        if cr is not None and cr.state == "PUBLISHED":
            cfg = dict(cr.content_json or {})
    import re as _re
    base = str(cfg.get("base_url") or "")
    m = _re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]+", base)
    return {
        "id": dest.id,
        "name": dest.name or dest.code,
        "mode": dest.mode,
        "status": dest.status,
        "is_mock": bool(dest.is_mock),
        "profile_code": (dest.profile_revision or "").split("@")[0],
        "host_port": m.group(0) if m else None,
        "receipt_rules": cfg.get("receipt_rules") or [],
    }


def _summary_json(s: TargetSummary | None, target_code: str, days: int) -> dict:
    if s is None:
        return {"target_code": target_code, "days": days,
                "buckets": {b: 0 for b in REPORT_BUCKETS},
                "daily": [], "last_success_at": None, "backlog": 0,
                "unmapped_states": {}, "data_source": "none", "note": "",
                "window_kind": "rolling_days", "backlog_total": None,
                "quality_waiting": 0, "evidence_kind": ""}
    return {
        "target_code": s.target_code, "days": s.window_days,
        "buckets": s.buckets, "daily": s.daily,
        "last_success_at": s.last_success_at, "backlog": s.backlog,
        "unmapped_states": s.unmapped_states, "data_source": s.data_source,
        "note": s.note,
        # LT-03-R1（R02）口径分离：窗口计数/全量积压/质量等待/证据形态
        "window_kind": s.window_kind,
        "backlog_total": s.backlog_total,
        "quality_waiting": s.quality_waiting,
        "evidence_kind": s.evidence_kind,
    }


def register(app, sf, auth, registry):
    from fastapi import Body, Query

    # ================================================================ 插件元数据（RT-05 U02）
    @app.get("/api/v1/plugins")
    def list_plugins(ctx=Depends(auth)):
        """已注册领域插件只读元数据（manifest 驱动；不含任何租户数据）。

        U02 新建业务下拉的数据来源——不杜撰"所有插件已支持"；每个条目
        key/domain/description/capabilities/report_targets 与
        plugins/domains/*/manifest.json 一致（discover fail-closed）。"""
        p = ctx.require_principal()
        from app_runtime import PLUGIN_MANIFESTS
        out = []
        for key in sorted(PLUGIN_MANIFESTS):
            m = PLUGIN_MANIFESTS[key]
            out.append({
                "key": m.get("key"),
                "domain": m.get("domain"),
                "description": m.get("description", ""),
                "capabilities": list(m.get("capabilities") or []),
                "report_targets": list(m.get("report_targets") or []),
            })
        return ok(out, ctx.request_id)

    @app.get("/api/v1/report-targets")
    def list_targets(ctx=Depends(auth)):
        p = ctx.require_principal()
        sc = p.target_scopes_effective()
        with sf() as db:
            rows = RT.list_targets(db, p.tenant_id)
            if sc is not None:
                # R08：受限用户不返回未授权业务的编码/标题（无 "*" 时按 code 过滤）
                rows = [t for t in rows if "*" in sc or t.code in sc]
            return ok([_target_json(t) for t in rows], ctx.request_id)

    @app.get("/api/v1/report-targets/{code}")
    def get_target(code: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_target_scope(code)          # R08：先范围（含未知 code 403，防泄露）
        with sf() as db:
            t = RT.get_target(db, p.tenant_id, code)
            data = _target_json(t)
            # RT-01：附带只读脱敏目的地摘要（前端不再请求不存在的 destinations 接口）
            dest_summary = _destination_summary(db, t.destination_id)
            if dest_summary is not None:
                data["destination_summary"] = dest_summary
            return ok(data, ctx.request_id)

    @app.post("/api/v1/report-targets")
    def create_target(body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        # RT-02：新建业务是全局目录写操作——仅全局范围账号可执行
        # （platform_admin 的生效范围恒为不受限；防御未来角色放宽时收紧）
        _sc = p.target_scopes_effective()
        if _sc is not None and "*" not in _sc:
            from contracts import ApiError
            raise ApiError("FORBIDDEN", "新建上报业务要求全局业务范围")
        with sf() as db:
            existing = (db.query(RT.ReportTarget)
                        .filter_by(tenant_id=p.tenant_id,
                                   code=str(body.get("code", "")))
                        .first())
            if existing is not None:
                from contracts import ApiError
                raise ApiError(
                    "CONFLICT",
                    f"report target {body.get('code')!r} already exists")
            t = RT.upsert_target(db, p.tenant_id, body,
                                 actor=p.subject_code)
            db.commit()
            return ok(_target_json(t), ctx.request_id)

    @app.put("/api/v1/report-targets/{code}")
    def update_target(code: str, body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        p.require_target_scope(code)        # RT-02：受限 config_editor 不得改未授权业务
        if "revision" not in body:
            from contracts import ApiError
            raise ApiError("VALIDATION_FAILED",
                           "body must carry current revision (optimistic lock)")
        data = dict(body)
        data["code"] = code
        with sf() as db:
            t = RT.upsert_target(db, p.tenant_id, data,
                                 actor=p.subject_code,
                                 expected_revision=body.get("revision"))
            db.commit()
            return ok(_target_json(t), ctx.request_id)

    @app.get("/api/v1/report-targets/{code}/summary")
    def target_summary(code: str, days: int = Query(7), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_target_scope(code)          # R08：先范围防泄露
        if days < 1 or days > 90:
            from contracts import ApiError
            raise ApiError("VALIDATION_FAILED", "days must be within 1..90")
        with sf() as db:
            RT.get_target(db, p.tenant_id, code)          # 不存在→404
            s = (registry.target_summary(db, p.tenant_id, code, days)
                 if registry is not None else None)
            return ok(_summary_json(s, code, days), ctx.request_id)

    # ================================================================ 分类统计（RT-04 A3）
    @app.get("/api/v1/report-targets/{code}/classification")
    def target_classification(code: str, days: int = Query(7),
                              source_instance: str = Query(""),
                              ctx=Depends(auth)):
        """六类文书 × 状态桶分类统计（只读；外部副本与影子 Mock 分栏）。

        契约（RT-04 A3）：GET /report-targets/{code}/classification?
        days=1..90&source_instance=（空=全部实例）→
        {doc_types:[{doc_type_code,doc_type_name,buckets,total}...]（六类
        +其他/未分类，含零值行）, sum_in_window, ledger_window_total,
        unknown_time_records, source_instances, observed_until,
        last_import_at, shadow_mock, window}；总和与同窗口台账可对齐
        （sum_in_window==ledger_window_total）。角色：任意登录主体（与
        summary 同为只读）；scope：require_target_scope(code)。"""
        p = ctx.require_principal()
        p.require_target_scope(code)          # 范围守卫（先于存在性）
        if days < 1 or days > 90:
            from contracts import ApiError
            raise ApiError("VALIDATION_FAILED", "days must be within 1..90")
        with sf() as db:
            RT.get_target(db, p.tenant_id, code)          # 不存在→404
            data = (registry.target_classification(
                db, p.tenant_id, code, days,
                source_instance=source_instance or None)
                if registry is not None else None)
            if data is None:
                from contracts import ApiError
                raise ApiError(
                    "NOT_IMPLEMENTED",
                    f"业务 {code} 未提供分类统计（无插件认领）")
            return ok(data, ctx.request_id)

    # ================================================================ 统一台账（LT-03）
    @app.get("/api/v1/report-center/submissions")
    def ledger_list(target: str = Query(""), bucket: str = Query(""),
                    date_from: str = Query(""), date_to: str = Query(""),
                    q: str = Query(""), cursor: str = Query(""),
                    limit: int = Query(50), ctx=Depends(auth)):
        p = ctx.require_principal()
        if not target:
            from contracts import ApiError
            raise ApiError("TARGET_REQUIRED", "target 查询参数必填")
        p.require_target_scope(target)        # R08：台账列表同样范围校验
        if bucket and bucket not in REPORT_BUCKETS:
            from contracts import ApiError
            raise ApiError("VALIDATION_FAILED",
                           f"bucket 非法：{bucket!r}；允许 {'/'.join(REPORT_BUCKETS)}")
        if limit < 1 or limit > 200:
            from contracts import ApiError
            raise ApiError("VALIDATION_FAILED", "limit 必须在 1—200")
        from plugins import report_ledger as RL
        from plugins.domain_hooks import LedgerFilter
        with sf() as db:
            t = RT.get_target(db, p.tenant_id, target)  # 404 未知 target
            flt = LedgerFilter(bucket=bucket or None,
                               date_from=date_from or None,
                               date_to=date_to or None, q=q or None)
            page = (registry.ledger_search(db, p.tenant_id, target, flt,
                                           cursor or None, limit)
                    if registry is not None else None)
            if page is None:
                return ok({"target": target, "items": [], "next_cursor": None,
                           "total_estimate": 0, "data_source": "none"},
                          ctx.request_id)
            # RT-01：data_source 如实返回——钩子页对象自带 data_source 时优先；
            # 否则 EXTERNAL 运行形态的业务（传染病外部导入副本）标
            # external_ledger，不再一律硬编码 "platform"。
            ds = getattr(page, "data_source", None)
            if not ds:
                ds = ("external_ledger" if t.runtime_kind == "EXTERNAL"
                      else "platform")
            return ok({
                "target": target,
                "items": [r.__dict__ for r in page.items],
                "next_cursor": page.next_cursor,
                "total_estimate": page.total_estimate,
                "data_source": ds,
            }, ctx.request_id)

    @app.get("/api/v1/report-center/submissions/{target}/{submission_id}")
    def ledger_detail(target: str, submission_id: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_target_scope(target)        # R08：详情范围校验
        with sf() as db:
            RT.get_target(db, p.tenant_id, target)
            # 药品 target → 前端跳专页（返回 detail_route，不代理其数据）
            t = RT.get_target(db, p.tenant_id, target)
            if t.plugin_key == "drug_trace":
                return ok({"target": target, "id": submission_id,
                           "detail_route": "/drugtrace",
                           "note": "药品详情走药品专页（受控流程）"},
                          ctx.request_id)
            # LT-03-R1：详情按 tenant+target+submission 绑定关系取数——
            # 领域插件认领（HOOK_LEDGER_DETAIL，如病理按 domain）优先，
            # 其余走通用兜底（target 绑定 destination 集合校验）。
            from plugins import report_ledger as RL
            d = None
            if registry is not None:
                d = registry.ledger_detail(db, p.tenant_id, target,
                                           submission_id)
            if d is None:
                d = RL.submission_detail(db, p.tenant_id, submission_id,
                                         target_code=target)
            if d is None:
                from contracts import ApiError
                raise ApiError("NOT_FOUND", "submission not found")
            return ok({"target": target, **d}, ctx.request_id)

    @app.post("/api/v1/report-center/submissions/{target}/{submission_id}/manual-resolution")
    def manual_resolution(target: str, submission_id: str, body: dict = Body(...),
                          ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_BUSINESS_REVIEWER, ROLE_PLATFORM_ADMIN)
        p.require_target_scope(target)        # R08：处置范围校验
        with sf() as db:
            t = RT.get_target(db, p.tenant_id, target)
            if t.plugin_key == "drug_trace":
                from contracts import ApiError
                raise ApiError("USE_DOMAIN_WORKFLOW",
                               "药品台账处置请走药品专页受控流程（不开放通用人工处置）")
            if t.runtime_kind == "EXTERNAL":
                # RT-04 A3：外部副本观测记录不是 Submission——外部历史观察
                # 不开放平台 UNKNOWN 手工重发（外部 record 不能当 Submission 修改）
                from contracts import ApiError
                raise ApiError(
                    "USE_DOMAIN_WORKFLOW",
                    "外部副本观测业务不开放通用人工处置（外部 record 不是 "
                    "Submission；处置须在来源系统/前置机侧重做并重新导入）")
            from plugins import report_ledger as RL
            try:
                r = RL.manual_resolution(
                    db, p.tenant_id, target, submission_id,
                    resolution=str(body.get("resolution", "")),
                    reason=str(body.get("reason", "")),
                    evidence_ref=str(body.get("evidence_ref", "")),
                    expected_state=str(body.get("expected_state", "")),
                    actor=p.subject_code)
            except RT.ApiError:
                raise
            db.commit()
            return ok(r, ctx.request_id)

    # ================================================================ 外部台账导入（LT-05 A5）
    @app.post("/api/v1/report-center/external-imports")
    def external_import(body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN, "operator")
        from contracts import ApiError
        from plugins.external_ledger import import_files
        target = str(body.get("target", "") or "")
        p.require_target_scope(target)        # R08：导入范围校验
        with sf() as db:
            RT.get_target(db, p.tenant_id, target)   # 404 未知 target
            files = body.get("files") or []
            if not isinstance(files, list) or not files:
                raise ApiError("VALIDATION_FAILED", "files 必填（数组）")
            total = sum(len(str(f.get("content_base64", "") or ""))
                        for f in files)
            if total > 20 * 1024 * 1024:
                raise ApiError("REQUEST_TOO_LARGE",
                               "单次导入 ≤20MB（base64 计）")
            try:
                stats = import_files(
                    db, p.tenant_id, target, files=files,
                    source_label=str(body.get("source_label", "")),
                    source_instance=str(body.get("source_instance", "")),
                    source_tz=str(body.get("source_tz", "")),
                    receipts_tz=body.get("receipts_tz") or None,
                    sent_tz=body.get("sent_tz") or None,
                    actor=p.subject_code,
                    dry_run=bool(body.get("dry_run", False)))
                db.commit()
            except ValueError as exc:
                raise ApiError("VALIDATION_FAILED", str(exc))
            return ok(stats, ctx.request_id)

    @app.get("/api/v1/report-center/external-imports")
    def external_import_list(target: str = Query(""), limit: int = Query(20),
                             ctx=Depends(auth)):
        p = ctx.require_principal()
        from core_db.models import ExternalImportBatch
        sc = p.target_scopes_effective()
        with sf() as db:
            q = db.query(ExternalImportBatch).filter_by(
                tenant_id=p.tenant_id)
            if target:
                p.require_target_scope(target)
                q = q.filter_by(target_code=target)
            elif sc is not None:
                # R08：受限用户的批次列表只含授权业务（空范围→__none__ 恒空集）
                allowed = [t.code for t in RT.list_targets(db, p.tenant_id)
                           if "*" in sc or t.code in sc]
                q = q.filter(ExternalImportBatch.target_code.in_(
                    allowed or ["__none__"]))
            rows = q.order_by(ExternalImportBatch.started_at.desc()).limit(
                min(100, max(1, limit))).all()
            return ok([{
                "id": b.id, "target": b.target_code,
                "source_label": b.source_label,
                "source_instance": b.source_instance, "source_tz": b.source_tz,
                "files": b.files, "rows_read": b.rows_read,
                "rows_applied": b.rows_applied,
                "rows_skipped_duplicate": b.rows_skipped_duplicate,
                "rows_rejected": b.rows_rejected,
                "started_at": b.started_at.isoformat(),
                "ended_at": b.ended_at.isoformat() if b.ended_at else None,
                "imported_by": b.imported_by, "dry_run": b.dry_run}
                for b in rows], ctx.request_id)

    @app.get("/api/v1/report-center/overview")
    def report_center(days: int = Query(7), ctx=Depends(auth)):
        p = ctx.require_principal()
        if days < 1 or days > 90:
            from contracts import ApiError
            raise ApiError("VALIDATION_FAILED", "days must be within 1..90")
        sc = p.target_scopes_effective()
        items: list = []
        totals = {b: 0 for b in REPORT_BUCKETS}
        with sf() as db:
            targets = RT.list_targets(db, p.tenant_id)
            if sc is not None:
                # R08：受限用户的 overview 只聚合授权业务（不返回未授权总数/标题）
                targets = [t for t in targets if "*" in sc or t.code in sc]
            for t in targets:
                try:
                    s = (registry.target_summary(db, p.tenant_id, t.code, days)
                         if registry is not None else None)
                    item = _summary_json(s, t.code, days)
                except Exception as exc:                   # noqa: BLE001
                    item = {"target_code": t.code, "days": days,
                            "buckets": {b: 0 for b in REPORT_BUCKETS},
                            "data_source": "none",
                            "error": type(exc).__name__ if not hasattr(exc, "code")
                            else getattr(exc, "code")}
                for b, v in item["buckets"].items():
                    if b in totals:
                        totals[b] += int(v)
                item["name"] = t.name
                item["lifecycle"] = t.lifecycle
                item["runtime_kind"] = t.runtime_kind
                item["sole_pusher"] = t.sole_pusher
                items.append(item)
        return ok({"days": days, "targets": items, "totals": totals},
                  ctx.request_id)
