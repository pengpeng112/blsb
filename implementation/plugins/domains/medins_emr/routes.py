"""医保联调台 API（LT-07 步骤7；RT-04 B1 深化）。

安全（R09 + RT-04 B1）：
- request/response 落库前经递归脱敏防线（masking.py 三层：契约路径/敏感键名/
  值模式兜底；envelope 与 input 同覆盖；超限明确拒绝）；
- export-request 的真实值由用户输入仅回传浏览器下载，不落库/不写文件/
  不打 console；库中只留打码版与 digest；
- send-mock 只发本机 Mock（进程内合成响应；真实目的地被闸门拒绝）；
- record-result 是人工证据（保留操作者/时间，不冒充机器确认；note/platform_code
  人工输入同样脱敏后落库）。
"""
from __future__ import annotations

import hashlib
import json

from contracts import ok
from contracts.api import ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN
from core_db.engine import utcnow
from core_db.models import AuditEvent, DebugExchange
from mapping_engine.contract_validator import validate_message

from .masking import ensure_limits, mask_free_text, mask_sensitive
from .profile_store import (load_json, load_profile_meta,
                             load_transactions, revision_info)

TARGET = "medins_emr_sd"


def _contracts(code: str):
    """装载 active revision 契约（RT-04 B2：r2-23txn 树形；r1 平面兼容）。"""
    fields = load_json(f"{TARGET}/fields/{code}.json")
    envelope = load_json(f"{TARGET}/fields/_envelope.json")
    dicts_path = f"{TARGET}/dicts/opter_type.json"
    d = load_json(dicts_path)
    dicts = {"opter_type": d} if d else {}
    return fields, envelope, dicts


def _contract_nodes(contract: dict | None) -> list | dict | None:
    """树（children）/平面（nodes）两种契约形状归一（供脱敏第1层）。"""
    if not isinstance(contract, dict):
        return None
    return contract.get("children") or contract.get("nodes")


def _masked_request(message: dict, fields: dict, envelope: dict) -> dict:
    """全部 DB 写入口统一走递归脱敏（B1：公共报文头+input+未知字段全覆盖）。"""
    return mask_sensitive(message, contract_nodes=_contract_nodes(fields),
                          envelope_nodes=_contract_nodes(envelope))


def register(app, sf, auth, registry):
    from fastapi import Body, Depends
    from contracts import ApiError, new_uuid
    from contracts.api import ROLE_OPERATOR

    def _require_txn(code: str):
        txns = load_transactions(TARGET)
        if not any(t["code"] == code for t in txns):
            raise ApiError("NOT_FOUND", f"未知交易 {code}")

    @app.get("/api/v1/medins-emr/transactions")
    def list_txns(ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_target_scope(TARGET)      # RT-02：7 接口入口统一范围校验
        return ok(load_transactions(TARGET), ctx.request_id)

    @app.get("/api/v1/medins-emr/transactions/{code}")
    def txn_detail(code: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_target_scope(TARGET)      # RT-02
        _require_txn(code)
        fields, envelope, dicts = _contracts(code)
        sample = load_json(f"{TARGET}/samples/{code}.json")
        # RT-04 B2：消费方返回 profile revision 与 digest（可追溯）
        return ok({"code": code, "fields": fields, "envelope": envelope,
                   "dicts": dicts, "sample": sample,
                   "profile": load_profile_meta(TARGET),
                   "profile_revision": revision_info(TARGET)}, ctx.request_id)

    @app.post("/api/v1/medins-emr/validate")
    def validate(body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_target_scope(TARGET)      # RT-02
        p.require_any_role(ROLE_OPERATOR, ROLE_CONFIG_EDITOR,
                           ROLE_PLATFORM_ADMIN)
        code = str(body.get("code", ""))
        _require_txn(code)
        message = body.get("message")
        if not isinstance(message, dict):
            raise ApiError("VALIDATION_FAILED", "message 必须为 JSON 对象")
        ensure_limits(message)             # B1：超限明确拒绝（不跳过深层）
        fields, envelope, dicts = _contracts(code)
        errs = validate_message(message, fields, envelope, dicts)
        with sf() as db:
            rec = DebugExchange(
                id=new_uuid(), tenant_id=p.tenant_id, target_code=TARGET,
                transaction_code=code, created_by=p.subject_code,
                mode="VALIDATE_ONLY",
                request_digest=hashlib.sha256(json.dumps(
                    message, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()[:64],
                request_masked=_masked_request(message, fields, envelope),
                validation_errors=errs,
                outcome="PASS" if not [e for e in errs
                                       if e["level"] == "error"] else "FAIL")
            db.add(rec)
            db.commit()
            rid = rec.id
        return ok({"errors": errs, "record_id": rid,
                   "pass": not [e for e in errs if e["level"] == "error"]},
                  ctx.request_id)

    @app.post("/api/v1/medins-emr/send-mock")
    def send_mock(body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_target_scope(TARGET)      # RT-02
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        code = str(body.get("code", ""))
        _require_txn(code)
        message = body.get("message")
        if not isinstance(message, dict):
            raise ApiError("VALIDATION_FAILED", "message 必须为 JSON 对象")
        ensure_limits(message)             # B1：超限明确拒绝
        fields, envelope, dicts = _contracts(code)
        errs = validate_message(message, fields, envelope, dicts)
        hard = [e for e in errs if e["level"] == "error"]
        if hard:
            raise ApiError("VALIDATION_FAILED",
                           f"校验未通过（{len(hard)} 项错误），先修正再发送")
        # Mock 发送：进程内按规范返回结构合成响应（infcode 行为档）——
        # 完全不出网（无需 Mock 服务）；真实发送被闸门拒绝（与 Target 无关）
        infcode = int(body.get("__mock_infcode", 0) or 0)
        if infcode == 0:
            outcome, err = "PASS", ""
        else:
            outcome, err = "FAIL", f"MOCK infcode={infcode}"
        mock_response = {
            "infcode": infcode,
            "err_msg": err or "成功（Mock 合成响应）",
            "inf_refmsgid": "MOCK" + "0" * 26,
            "output": {},
            "_mock": True,          # 明显 Mock 标记（不得当正式回执）
        }
        with sf() as db:
            rec = DebugExchange(
                id=new_uuid(), tenant_id=p.tenant_id, target_code=TARGET,
                transaction_code=code, created_by=p.subject_code, mode="MOCK",
                request_digest=hashlib.sha256(json.dumps(
                    message, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()[:64],
                request_masked=_masked_request(message, fields, envelope),
                validation_errors=errs,
                response_status=200,
                response_masked=mask_sensitive(
                    mock_response, contract_nodes=_contract_nodes(fields),
                    envelope_nodes=_contract_nodes(envelope)),
                outcome=outcome, note="Mock 联调（进程内合成响应，未出网）")
            db.add(rec)
            db.commit()
            rid = rec.id
        return ok({"record_id": rid, "mock": True,
                   "response": mock_response}, ctx.request_id)

    @app.post("/api/v1/medins-emr/export-request")
    def export_request(body: dict = Body(...), ctx=Depends(auth)):
        """生成院内手工执行包：完整报文（用户输入的真实值仅回传，不落库）+
        HTTP 头清单（签名占位）+ curl 模板（地址占位）。"""
        p = ctx.require_principal()
        p.require_target_scope(TARGET)      # RT-02
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        code = str(body.get("code", ""))
        _require_txn(code)
        message = body.get("message")
        if not isinstance(message, dict):
            raise ApiError("VALIDATION_FAILED", "message 必须为 JSON 对象")
        ensure_limits(message)             # B1：超限明确拒绝
        fields, envelope, dicts = _contracts(code)
        errs = validate_message(message, fields, envelope, dicts)
        hard = [e for e in errs if e["level"] == "error"]
        if hard:
            raise ApiError("VALIDATION_FAILED",
                           f"校验未通过（{len(hard)} 项错误），先修正再导出")
        payload = json.dumps(message, ensure_ascii=False, indent=1)
        headers = [
            {"name": "Content-Type", "value": "application/json"},
            {"name": "x-tif-signature" if False else "cainfo",
             "value": "<由院方签名工具生成（算法待医保确认）>"},
            {"name": "signtype",
             "value": message.get("signtype") or "<按当地要求填写>"},
        ]
        curl = ("curl -X POST '<调用地址>' "
                f"-H 'Content-Type: application/json' "
                f"-d @request-{code}.json")
        # 落库：只记打码版与 digest（真实值不落库/不写文件/不进日志）
        with sf() as db:
            rec = DebugExchange(
                id=new_uuid(), tenant_id=p.tenant_id, target_code=TARGET,
                transaction_code=code, created_by=p.subject_code,
                mode="EXPORT",
                request_digest=hashlib.sha256(payload.encode()).hexdigest()[:64],
                request_masked=_masked_request(message, fields, envelope),
                validation_errors=errs, outcome="PASS",
                note="导出院内执行包（真实值未落库）")
            db.add(rec)
            db.commit()
            rid = rec.id
        # B4：导出包补必要元数据（交易码/Profile revision——可追溯）
        return ok({"record_id": rid,
                   "transaction_code": code,
                   "profile_revision": revision_info(TARGET),
                   "message": message,          # 仅本次响应回传（浏览器下载）
                   "headers": headers, "curl": curl,
                   "notice": "真实值不落库；请在院内授权环境执行并回填结果"},
                  ctx.request_id)

    @app.get("/api/v1/medins-emr/debug-exchanges")
    def list_exchanges(code: str = "", limit: int = 50, cursor: str = "",
                       ctx=Depends(auth)):
        """联调历史（RT-03 S08：created_at DESC, id DESC 真实 keyset；
        数组改 items/next_cursor 分页对象；游标绑定租户/业务/交易筛选）。"""
        p = ctx.require_principal()
        p.require_target_scope(TARGET)      # RT-02
        from contracts.api import (keyset_condition, keyset_cursor_decode,
                                   keyset_cursor_encode, keyset_next_cursor)
        from core_db.models import DebugExchange as DE
        with sf() as db:
            q = db.query(DE).filter(DE.tenant_id == p.tenant_id,
                                    DE.target_code == TARGET)
            if code:
                q = q.filter(DE.transaction_code == code)
            context = {"target": TARGET, "code": code}
            cur = None
            if cursor:
                cur = keyset_cursor_decode(
                    cursor, resource="medins_exchanges",
                    tenant_id=p.tenant_id, context=context)
            if cur:
                q = q.filter(keyset_condition(
                    DE.created_at, DE.id, cur[0], cur[1]))
            lim = min(100, max(1, limit))
            rows = (q.order_by(DE.created_at.desc(), DE.id.desc())
                    .limit(lim + 1).all())
            has_more = len(rows) > lim
            rows = rows[:lim]
            return ok({"items": [{
                "id": r.id, "code": r.transaction_code, "mode": r.mode,
                "created_by": r.created_by,
                "created_at": r.created_at.isoformat(),
                "outcome": r.outcome,
                "validation_errors": r.validation_errors,
                "request_masked": r.request_masked,
                "response_masked": r.response_masked,
                "note": r.note} for r in rows],
                "next_cursor": keyset_next_cursor(
                    rows, has_more, resource="medins_exchanges",
                    tenant_id=p.tenant_id, context=context,
                    ts_attr="created_at")}, ctx.request_id)

    @app.post("/api/v1/medins-emr/debug-exchanges/{rec_id}/record-result")
    def record_result(rec_id: str, body: dict = Body(...),
                      ctx=Depends(auth)):
        """院内手工执行后回填（人工证据级别：不冒充机器确认）。

        B1：note/platform_code 是人工输入，可能包含敏感值——脱敏后再落库
        （note 原文只参与长度校验，不进任何存储）。"""
        p = ctx.require_principal()
        p.require_target_scope(TARGET)      # RT-02
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        success = body.get("success")
        if not isinstance(success, bool):
            raise ApiError("VALIDATION_FAILED", "success 必须为布尔")
        note = mask_free_text(str(body.get("note", "") or ""))
        if len(str(body.get("note", "") or "").strip()) < 5:
            raise ApiError("VALIDATION_FAILED", "note 至少 5 字（回填说明）")
        platform_code = mask_free_text(
            str(body.get("platform_code", "") or "")[:64], max_len=64)
        with sf() as db:
            r = db.get(DebugExchange, rec_id)
            if r is None or r.tenant_id != p.tenant_id \
                    or r.target_code != TARGET:
                raise ApiError("NOT_FOUND", "record not found")
            r.note = f"[人工回填] {'成功' if success else '失败'}"
            if platform_code:
                r.note += f" 平台返回码={platform_code}"
            r.note += f"；说明：{note}；操作者={p.subject_code}；" \
                      f"观察时间={utcnow().isoformat()}"
            # 失败回填如实反映（outcome=FAIL），不能只有成功更新结果
            r.outcome = "PASS" if success else "FAIL"
            db.add(AuditEvent(
                id=new_uuid(), tenant_id=p.tenant_id, actor=p.subject_code,
                action="medins.record_result",
                target=f"debug_exchange:{r.id}",
                new_ref={"success": success, "platform_code": platform_code}))
            db.commit()
            return ok({"id": r.id, "recorded": True,
                       "evidence_level": "MANUAL_OBSERVATION"},
                      ctx.request_id)
