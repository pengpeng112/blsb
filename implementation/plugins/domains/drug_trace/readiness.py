"""药品追溯统一就绪判定（FB-01，DTR-FIRSTBILL-EXEC-20261021）。

单一事实源：API（/api/v1/drug-trace/readiness）、CLI（apps/init_drug_trace
--readiness）与 UI（消费 API 输出）共用本模块，不再各自维护一套口径。

设计约束（NEXT_AI_PROMPT FB-01）：
- 全部层状态**动态计算**（配置/证据/字典/签名的当前实际值）；CODE_READY、
  LOCAL_SERVICE_READY 只是本层状态，绝不据此返回"可真实发送"。
- 目的地解析精确化：显式 destination_id 优先；同 profile 多目的地且未指定时
  返回 ambiguous（不任意 first 造成证据串用）。
- Mock/DRAFT、缺 active config、签名未验、证据缺项/过期/撤销、配置换代均如实
  反映在 detail；不因 readiness 查看触发任何写操作（不连 HIS、不发 POST、
  不批准证据、不重建草稿）。
- NETWORK_READY 恒为 UNKNOWN：应用层不做网络探测，也不持久化"永久网络已通"
  布尔；外部实测（mTLS health 等）记录在任务 evidence，由 detail 指引查看，
  不把外部实测自动折算成应用层绿灯。
"""
from __future__ import annotations

from datetime import datetime, timezone

from core_db.models import (ConfigRevision, ConnectorInstance, Destination,
                            DictionaryRevision)

from . import contracts as C

# 就绪查看涉及的药品追溯字典命名空间（批准状态参与 PLATFORM_READY）
DICT_NAMESPACES = (
    "drugtrace.bill_type",
    C.DICT_NS_REF_ENT_ID,
    C.DICT_NS_QUERY_BILL_TYPE,
)

SOURCE_PLUGIN_ID = "sources.db_view.drug_trace"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_destinations(db, tenant_id: str, profile: str,
                         destination_id: str = "") -> dict:
    """精确目的地解析（不任意 first）。

    返回 {destination, candidates, ambiguous, reason}：
    - destination_id 给定：tenant 内精确查找；profile 不匹配 → destination=None
      + reason=PROFILE_MISMATCH（候选保留供提示）。
    - 未给定：按 profile key 前缀取全部（不过滤 status——DRAFT/ENABLED 都列出，
      由 detail 展示）；多于一个 → ambiguous=True 不自动选择。
    """
    profile_key = profile.split("@")[0]
    out: dict = {"destination": None, "candidates": [],
                 "ambiguous": False, "reason": ""}
    if destination_id:
        dest = db.get(Destination, destination_id)
        if dest is None or dest.tenant_id != tenant_id:
            out["reason"] = "NOT_FOUND"
            return out
        if not (dest.profile_revision or "").startswith(profile_key):
            out["reason"] = "PROFILE_MISMATCH"
            out["candidates"] = [dest.id]
            return out
        out["destination"] = dest
        return out
    rows = (db.query(Destination)
            .filter(Destination.tenant_id == tenant_id,
                    Destination.profile_revision.like(f"{profile_key}%"))
            .order_by(Destination.created_at).all())
    out["candidates"] = [d.id for d in rows]
    if not rows:
        out["reason"] = "NO_DESTINATION"
    elif len(rows) == 1:
        out["destination"] = rows[0]
    else:
        out["ambiguous"] = True
        out["reason"] = "AMBIGUOUS_MULTIPLE_DESTINATIONS"
    return out


def _dest_view(dest: Destination | None, db) -> dict | None:
    if dest is None:
        return None
    cfg_state = None
    if dest.config_revision:
        cr = db.get(ConfigRevision, dest.config_revision)
        cfg_state = {"id": cr.id, "state": cr.state,
                     "revision": cr.revision} if cr else "MISSING"
    return {"id": dest.id, "code": dest.code, "status": dest.status,
            "is_mock": bool(dest.is_mock),
            "profile_revision": dest.profile_revision,
            "config_revision": dest.config_revision,
            "config_state": cfg_state}


def _dict_state(db, tenant_id: str) -> dict:
    """三字典批准状态：approved=存在 approved_by 非空版本（映射只读批准版）。
    draft_only=只有未批准版本；absent=无任何版本。"""
    out = {}
    for ns in DICT_NAMESPACES:
        revs = (db.query(DictionaryRevision)
                .filter_by(tenant_id=tenant_id, namespace=ns).all())
        if not revs:
            out[ns] = {"state": "ABSENT"}
            continue
        approved = [r for r in revs if r.approved_by]
        out[ns] = {
            "state": "APPROVED" if approved else "DRAFT_ONLY",
            "versions": [r.version for r in revs],
            "approved_versions": [r.version for r in approved],
        }
    return out


def _ev_layer(status: str, label: str, missing=None) -> dict:
    """证据状态 → 层状态：APPROVED 才 READY；其余 NOT_READY（缺项列出）。"""
    detail = f"{label}：evidence status={status}"
    if missing:
        keys = [m.get("key") if isinstance(m, dict) else str(m)
                for m in (missing or [])]
        if keys:
            detail += f"；缺 {keys}"
    return {"status": "READY" if status == "APPROVED" else "NOT_READY",
            "checked_at": _now(), "detail": detail}


def layer_report(db, tenant_id: str, *, profile: str = C.PROFILE_RETAIL,
                 destination_id: str = "",
                 config_release_id: str | None = None,
                 evidence_state_fn=None) -> dict:
    """统一分层就绪报告（只读；供 API 与 CLI 共用）。

    evidence_state_fn 注入 service.evidence_state（避免循环导入；缺省时尝试
    延迟导入）。config_release_id 传入时按该配置代次核对证据闭包（send）。
    """
    if evidence_state_fn is None:
        from . import service as _S
        evidence_state_fn = _S.evidence_state

    # CODE_READY：领域表可访问（插件已加载+迁移已跑）
    try:
        from .models import DrugTraceBill
        db.query(DrugTraceBill).filter_by(tenant_id=tenant_id).limit(1).first()
        code_ready = {"status": "READY", "checked_at": _now(),
                      "detail": "drug_trace 模型/迁移可用（本 API/CLI 可答）"}
    except Exception as exc:  # noqa: BLE001
        code_ready = {"status": "NOT_READY", "checked_at": _now(),
                      "detail": f"drug_trace 表不可用：{type(exc).__name__}"
                                "（BLYB_PLUGINS 未含 drug_trace 或迁移未跑）"}

    # 目的地精确解析
    res = resolve_destinations(db, tenant_id, profile, destination_id)
    dest = res["destination"]
    dest_view = _dest_view(dest, db)

    # 连接器（来源侧）现状
    conns = (db.query(ConnectorInstance)
             .filter_by(tenant_id=tenant_id, plugin_id=SOURCE_PLUGIN_ID).all())
    conn_view = [{"id": c.id, "status": c.status,
                  "active_config": bool(c.active_config_revision)}
                 for c in conns]

    # LOCAL_SERVICE_READY：目的地存在 + 显式 active config（PUBLISHED）
    if res["ambiguous"]:
        local = {"status": "NOT_READY", "checked_at": _now(),
                 "detail": f"同 profile 存在多个目的地 {res['candidates']}；"
                           "必须显式指定 destination_id（不自动选择）"}
    elif dest is None:
        local = {"status": "NOT_READY", "checked_at": _now(),
                 "detail": f"未找到 profile={profile} 的目的地（{res['reason']}）"}
    elif not dest.config_revision:
        local = {"status": "NOT_READY", "checked_at": _now(),
                 "detail": f"目的地 {dest.code} status={dest.status}"
                           f" is_mock={bool(dest.is_mock)}；无 active config"
                           "（ConfigRevision 未发布）"}
    else:
        cfg_state = (dest_view or {}).get("config_state")
        published = isinstance(cfg_state, dict) and \
            cfg_state.get("state") == "PUBLISHED"
        local = {
            "status": "READY" if published else "NOT_READY",
            "checked_at": _now(),
            "detail": f"目的地 {dest.code} status={dest.status} "
                      f"is_mock={bool(dest.is_mock)} "
                      f"config_revision={dest.config_revision} "
                      f"config_state={cfg_state}"
                      + ("" if published else "（需 PUBLISHED 配置）")}

    # NETWORK_READY：应用层恒 UNKNOWN（外部实测记录在任务 evidence，不折算）
    network = {"status": "UNKNOWN", "checked_at": _now(),
               "detail": "本端点/工具不做网络探测；外部实测（worker→DMZ mTLS "
                         "health、DMZ→平台出口）见任务 evidence 目录"
                         "（外部实测通过≠本层 READY）"}

    # 证据状态（四能力）
    ev = {}
    for cap in ("collect", "send", "query", "reconcile"):
        ev[cap] = evidence_state_fn(
            db, tenant_id, profile=profile, capability=cap,
            destination_id=dest.id if dest else "",
            config_release_id=config_release_id if cap == "send" else None)

    source_ready = _ev_layer(
        ev["collect"]["status"], "HIS 生产只读来源（EXT-01/02）",
        ev["collect"].get("missing"))
    if conns and all(not c.active_config_revision for c in conns):
        source_ready["detail"] += "；连接器无 active config（DRAFT）"

    # PLATFORM_READY：签名资格 + query 证据 + bill_type 字典批准
    from destinations.alihealth_drugtrace.protocol import SIGNATURE_VERIFIED
    dicts = _dict_state(db, tenant_id)
    platform_reasons = []
    if not SIGNATURE_VERIFIED:
        platform_reasons.append("SIGNATURE_VERIFIED=False（EXT-07 未通过）")
    if ev["query"]["status"] != "APPROVED":
        platform_reasons.append(
            f"query 证据 {ev['query']['status']}（EXT-09）")
    bill_type_dict = dicts.get("drugtrace.bill_type", {})
    if bill_type_dict.get("state") != "APPROVED":
        platform_reasons.append(
            f"drugtrace.bill_type 字典 {bill_type_dict.get('state', 'ABSENT')}"
            "（EXT-08；批准前不预置 321/323）")
    platform = {"status": "READY" if not platform_reasons else "NOT_READY",
                "checked_at": _now(),
                "detail": "；".join(platform_reasons) or
                          "签名资格+查询证据+bill_type 字典均已具备"}

    send_ready = _ev_layer(
        ev["send"]["status"], "首批发送授权（EXT-03..05/06/07/08/10/12）",
        ev["send"].get("missing"))

    layers = {"CODE_READY": code_ready,
              "LOCAL_SERVICE_READY": local,
              "NETWORK_READY": network,
              "SOURCE_READY": source_ready,
              "PLATFORM_READY": platform,
              "SEND_SCOPE_READY": send_ready}
    statuses = [v["status"] for v in layers.values()]
    overall = ("READY" if all(s == "READY" for s in statuses)
               else "UNKNOWN" if "UNKNOWN" in statuses else "NOT_READY")
    return {
        "overall": overall,
        "tenant_id": tenant_id,
        "profile": profile,
        "destination": dest_view,
        "destination_candidates": res["candidates"],
        "destination_ambiguous": res["ambiguous"],
        "connectors": conn_view,
        "dictionaries": dicts,
        "signature_verified": bool(SIGNATURE_VERIFIED),
        "capabilities": {cap: {"status": st["status"],
                               "missing": [m.get("key") if isinstance(m, dict)
                                           else str(m)
                                           for m in (st.get("missing") or [])]}
                         for cap, st in ev.items()},
        "layers": layers,
        "note": "UNKNOWN 不是绿灯；层状态≠发送许可（发送仍走证据门禁+配额）",
    }
