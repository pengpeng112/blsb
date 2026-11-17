"""上报业务目录服务层（LT-01）：CRUD + 乐观锁 + 审计 + 生产闸门 + 幂等种子。

红线（用户 2026-11-08 明确指示，写入交接与四类实施文档）：
- `assert_target_can_send_real` **只供 LT-03 之后的新通用发送路径使用**
  （generic_http_json 等），**严禁接入药品追溯（码上放心）或传染病的现有
  生产发送链路**——药品有自己的 DrugTraceProductionEvidence 门禁在生产运行，
  传染病由平台外 inf_pusher 推送（runtime_kind=EXTERNAL，本闸门管不到也
  不该管）。目录中码上放心 sole_pusher=CONFLICT、传染病 UNCONFIRMED 仅为
  台账展示与提醒（双上报风险待信息科协调），不阻断、不改变任何现有行为。
"""
from __future__ import annotations

from contracts import (ApiError, REPORT_BUCKETS, REPORT_BUCKET_LABELS,
                       SUBMISSION_STATE_TO_BUCKET, TargetLifecycle,
                       TargetRuntimeKind)
from core_db.models import AuditEvent, ReportTarget
from core_db.engine import utcnow

# 服务层校验取值
VALID_MODES = ("PUSH", "PULL", "HYBRID")
VALID_SOLE_PUSHER = ("CONFIRMED", "UNCONFIRMED", "CONFLICT")


def _validate_enum(data: dict) -> None:
    lifecycle = data.get("lifecycle")
    if lifecycle is not None and lifecycle not in TargetLifecycle.ALL:
        raise ApiError("VALIDATION_FAILED",
                       f"unknown lifecycle {lifecycle!r}; "
                       f"allowed: {TargetLifecycle.ALL}")
    runtime = data.get("runtime_kind")
    if runtime is not None and runtime not in TargetRuntimeKind.ALL:
        raise ApiError("VALIDATION_FAILED",
                       f"unknown runtime_kind {runtime!r}; "
                       f"allowed: {TargetRuntimeKind.ALL}")
    mode = data.get("mode")
    if mode is not None and mode not in VALID_MODES:
        raise ApiError("VALIDATION_FAILED",
                       f"unknown mode {mode!r}; allowed: {VALID_MODES}")
    sole = data.get("sole_pusher")
    if sole is not None and sole not in VALID_SOLE_PUSHER:
        raise ApiError("VALIDATION_FAILED",
                       f"unknown sole_pusher {sole!r}; "
                       f"allowed: {VALID_SOLE_PUSHER}")


# 允许通过 upsert 更新的业务列（id/租户/时间戳/revision 由服务层管理）
_UPDATABLE = ("name", "category", "authority", "plugin_key", "destination_id",
              "mode", "lifecycle", "runtime_kind", "owner_dept", "contact",
              "sole_pusher", "sole_pusher_note", "spec_docs", "readme_path",
              "schedule_note", "extra")


def list_targets(db, tenant_id: str) -> list:
    return (db.query(ReportTarget)
            .filter_by(tenant_id=tenant_id)
            .order_by(ReportTarget.code).all())


def get_target(db, tenant_id: str, code: str) -> ReportTarget:
    t = (db.query(ReportTarget)
         .filter_by(tenant_id=tenant_id, code=code).first())
    if t is None:
        raise ApiError("NOT_FOUND", f"report target {code!r} not found")
    return t


def _diff_fields(old: ReportTarget | None, data: dict) -> dict:
    """记录发生变化的字段名与（小字段）前后值；大字段（spec_docs/extra）只记是否变化。"""
    fields: dict = {}
    if old is None:
        return {"created": sorted(k for k in data if k in _UPDATABLE)}
    for k in _UPDATABLE:
        if k not in data:
            continue
        new_v = data[k]
        old_v = getattr(old, k, None)
        if k in ("spec_docs", "extra"):
            if (new_v or ({} if k == "extra" else [])) != (old_v or
                                                           ({} if k == "extra" else [])):
                fields[k] = {"changed": True}
        elif old_v != new_v:
            ov = old_v if isinstance(old_v, (str, int, type(None))) else str(old_v)
            nv = new_v if isinstance(new_v, (str, int, type(None))) else str(new_v)
            if k in ("sole_pusher_note", "readme_path", "schedule_note"):
                fields[k] = {"changed": True}       # 长文本只记变化不记全文
            else:
                fields[k] = {"from": ov, "to": nv}
    return fields


def upsert_target(db, tenant_id: str, data: dict, actor: str,
                  expected_revision: int | None = None) -> ReportTarget:
    """新建或更新（乐观锁）：expected_revision 与库内不符抛 CONFLICT。

    - data 必含 code；新建时 name 必填；枚举字段校验见 _validate_enum；
    - 成功后 revision +1、updated_by=actor，并写 AuditEvent
      （action="report_target.upsert"，只记变化字段名与短值，不记大字段全文）。
    调用方负责 commit（与 core_api 既有路由风格一致）。
    """
    code = str(data.get("code", "") or "")
    if not code or len(code) > 64:
        raise ApiError("VALIDATION_FAILED", "report target code required (≤64)")
    _validate_enum(data)
    t = (db.query(ReportTarget)
         .filter_by(tenant_id=tenant_id, code=code).first())
    # RT-05 U02：生命周期升级到 PILOT/PRODUCTION 必填理由（服务端校验并
    # 审计；修改目录 lifecycle 不等于真实发送授权——生产闸门不松动，
    # assert_target_can_send_real 的 sole_pusher/证据要求不变）。
    reason = str(data.get("reason", "") or "").strip()
    if t is not None and "lifecycle" in data and \
            str(data.get("lifecycle") or "") in ("PILOT", "PRODUCTION") and \
            str(getattr(t, "lifecycle", "")) != str(data.get("lifecycle")):
        if len(reason) < 5:
            raise ApiError(
                "VALIDATION_FAILED",
                "lifecycle 升级到 PILOT/PRODUCTION 必填理由（≥5 字，进审计）")
    if t is None:
        if "name" not in data or not str(data["name"] or "").strip():
            raise ApiError("VALIDATION_FAILED",
                           f"name required to create report target {code!r}")
        t = ReportTarget(id=__import__("contracts").new_uuid(),
                         tenant_id=tenant_id, code=code,
                         revision=1, created_at=utcnow(), updated_at=utcnow(),
                         updated_by=actor)
        db.add(t)
        diff = _diff_fields(None, data)
    else:
        if expected_revision is not None and \
                int(expected_revision) != int(t.revision):
            raise ApiError(
                "CONFLICT",
                f"report target {code!r} revision mismatch: "
                f"expected {expected_revision}, current {t.revision}")
        diff = _diff_fields(t, data)
    for k in _UPDATABLE:
        if k in data:
            setattr(t, k, data[k])
    if not diff.get("created"):
        t.revision = int(t.revision) + 1
    t.updated_at = utcnow()
    t.updated_by = actor
    new_ref: dict = {"diff": diff, "revision": int(t.revision)}
    if reason:
        new_ref["reason"] = reason
    db.add(AuditEvent(
        id=__import__("contracts").new_uuid(), tenant_id=tenant_id,
        actor=actor or "", action="report_target.upsert",
        target=f"report_target:{code}",
        new_ref=new_ref))
    return t


def assert_target_can_send_real(db, tenant_id: str, code: str) -> None:
    """真实（非 Mock）发送前的统一生产闸门（LT-01；LT-03-R1 按 R02 收紧）。

    放行条件（全部满足）：
      lifecycle ∈ {PILOT, PRODUCTION} 且 runtime_kind == PLATFORM
      且 **sole_pusher == CONFIRMED**（LT-03-R1/R02：UNCONFIRMED 不再放行——
      唯一推送方未确认即真实发送=双上报风险；CONFLICT 待人工协调）。
    否则抛 PROFILE_UNAPPROVED（fail-closed，带具体原因）。
    独立启用证据（real_send_evidence_ref）由调用侧配置检查（perform_push
    generic 分支），与本闸门的 target 字段校验分开。

    【作用边界——再强调】本闸门只由 LT-03 之后的新通用发送路径调用；
    药品追溯/传染病现有生产链路不经过也不得接入本闸门。
    """
    t = get_target(db, tenant_id, code)
    reasons: list[str] = []
    if t.lifecycle not in (TargetLifecycle.PILOT, TargetLifecycle.PRODUCTION):
        reasons.append(f"lifecycle={t.lifecycle} (PILOT/PRODUCTION required)")
    if t.runtime_kind != TargetRuntimeKind.PLATFORM:
        reasons.append(f"runtime_kind={t.runtime_kind} (PLATFORM required)")
    if t.sole_pusher == "CONFLICT":
        reasons.append("sole_pusher=CONFLICT (唯一推送方冲突，待人工协调)")
    elif t.sole_pusher != "CONFIRMED":
        reasons.append("sole_pusher=UNCONFIRMED (唯一推送方未确认，"
                       "CONFIRMED required；台账登记≠推送权确认)")
    if reasons:
        raise ApiError(
            "PROFILE_UNAPPROVED",
            f"report target {code!r} not approved for real sending: "
            + "; ".join(reasons))


# ---------------------------------------------------------------- 种子
def _seed_rows() -> list[dict]:
    return [
        {"code": "drug_trace_alihealth", "name": "码上放心药品追溯",
         "category": "药品追溯", "authority": "阿里健康码上放心平台",
         "plugin_key": "drug_trace", "mode": "PUSH",
         "lifecycle": "PRODUCTION", "runtime_kind": "PLATFORM",
         "sole_pusher": "CONFLICT",
         "sole_pusher_note": "原厂家程序与本平台并行上传中（用户已确认正在上传，"
                             "不得阻断现有使用；待信息科后续协调唯一推送方）",
         "spec_docs": [
             "数据上报/1、码上放心平台/(260721)需求确认单-码上放心平台HIS对接出库销售上传(1).docx",
             "数据上报/1、码上放心平台/关于印发《山东省药品经营使用环节全品种信息化追溯体系建设"
             "及药品追溯码采集应用工作方案》的通知(1).pdf",
             "数据上报/1、码上放心平台/接口地址.txt"],
         "readme_path": "数据上报/1、码上放心平台/README.md",
         "schedule_note": "每日 01:00 窗口采集（宿主 blyb-daily-upload.timer）→ 白天补发；"
                          "详情见业务 README"},
        {"code": "inf_cdc_frontend", "name": "传染病智能监测预警前置软件",
         "category": "传染病", "authority": "国家疾控（前置软件 <frontend-host>）",
         "plugin_key": "infectious", "mode": "PUSH",
         "lifecycle": "PRODUCTION", "runtime_kind": "EXTERNAL",
         "sole_pusher": "UNCONFIRMED",
         "sole_pusher_note": "crbsb/FXHIS 是否并行推送待信息科确认"
                             "（不得影响现有 inf_pusher 推送）",
         "spec_docs": [
             "数据上报/4、传染病病历数据上报/附件2.国家传染病智能监.txt",
             "数据上报/4、传染病病历数据上报/03-接口总览与状态矩阵.md"],
         "readme_path": "数据上报/4、传染病病历数据上报/README.md",
         "schedule_note": "宿主 crontab */15 班次 + 00:05 reconcile（平台外 inf_pusher，"
                          "平台只读观测）"},
        {"code": "medins_emr_sd", "name": "山东医保电子病历上报",
         "category": "医保", "authority": "山东省医疗保障信息平台",
         "plugin_key": "medins_emr", "mode": "PUSH",
         # LT-07（2026-11-11）：PLANNED→DEVELOPING（联调台交付：本地校验/
         # Mock/执行包导出）；真实联调仍外部阻断（网关/签名/授权待院方）
         "lifecycle": "DEVELOPING", "runtime_kind": "PLATFORM",
         "sole_pusher": "UNCONFIRMED", "sole_pusher_note": "",
         "spec_docs": [
             "数据上报/2、医保电子病历上报/山东省医疗保障信息平台定点医疗机构"
             "医保电子病历接口规范V2.0(1)(2).docx",
             "数据上报/2、医保电子病历上报/11401入参.txt",
             "数据上报/2、医保电子病历上报/调用地址.txt"],
         "readme_path": "数据上报/2、医保电子病历上报/README.md",
         "schedule_note": "未开始（LT-07 联调计划）"},
        {"code": "pathology_cloud", "name": "医保病理云索引",
         "category": "病理", "authority": "医保病理云（协议未发布）",
         "plugin_key": "pathology", "mode": "HYBRID",
         "lifecycle": "MOCK_ONLY", "runtime_kind": "PLATFORM",
         "sole_pusher": "UNCONFIRMED", "sole_pusher_note": "",
         "spec_docs": [],
         "readme_path": "数据上报/3、病理上报/README.md",
         "schedule_note": "本地合成闭环；B01—B09 证据未获，协议未发布"},
        {"code": "medins_imaging_jn", "name": "济南医保影像云索引",
         "category": "影像", "authority": "济南市医保影像云",
         # LT-09：plugin_key 待定→medins_imaging（脚手架补填；其余字段不动）
         "plugin_key": "medins_imaging", "mode": "HYBRID",
         "lifecycle": "PLANNED", "runtime_kind": "PLATFORM",
         "sole_pusher": "UNCONFIRMED", "sole_pusher_note": "",
         "spec_docs": [],
         "readme_path": "数据上报/5、医保影像云上报/README.md",
         "schedule_note": "字段已提取（16 数据集/288 字段），未实现"},
    ]


def seed_default_targets(db, tenant_id: str, actor: str = "system") -> int:
    """幂等种子：按 code 补缺（已存在的不覆盖任何字段）；返回新增条数。

    连续性说明：药品 PRODUCTION/CONFLICT 与传染病 PRODUCTION/EXTERNAL 均为
    台账登记——不改任何现有链路行为（见模块 docstring 红线）。
    调用方负责 commit。
    """
    existing = {t.code for t in (db.query(ReportTarget)
                                .filter_by(tenant_id=tenant_id).all())}
    added = 0
    from contracts import new_uuid
    for row in _seed_rows():
        if row["code"] in existing:
            continue
        db.add(ReportTarget(id=new_uuid(), tenant_id=tenant_id, revision=1,
                            created_at=utcnow(), updated_at=utcnow(),
                            updated_by=actor, **row))
        added += 1
    return added
