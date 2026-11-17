"""影子 collect/send（LT-05 B4；RT-04 A1 重写）。

只能 Mock；生产仍由 deploy/inf_pusher 执行。

A1 要点：
- PackageRevision.digest = 冻结 payload 按仓库 canonical_json（contracts.
  canonical_json）的**完整 SHA256**（digest_of），Submission.snapshot_digest
  与其一致——禁止空串/只对源 ID 哈希；
- 幂等身份 = tenant+target+source_instance+table/源业务键+规范化
  source_op_time+源内容摘要（上传 operationTime 每次重写不同，**不参与
  身份**）——同一源版本跨上传时刻重采只产生一笔投递；新源版本=新不可变
  快照；同 op_time 不同内容=显式 CONFLICT（不静默覆盖）；缺 source_op_time
  =质量失败（不得用上传时刻伪造源版本）；
- 已发状态持久化在 infectious_shadow_record（迁移021），进程重启/新
  Session 后旧版本拦截仍有效；发送主路径 send_pending 从持久化待处理
  队列消费（collect 冻结的快照），send_batch(rows_by_table) 保留为显式
  测试/演练入口；
- DataPackage 组织策略：**每批新包、批内 revision 递增**（batch 包），
  源版本身份由 InfectiousShadowRecord+幂等键独立承载——测试不得把
  "批内 revision 序号"当作"源版本"。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from contracts import new_uuid
from contracts.api import digest_of
from core_db.engine import utcnow
from core_db.models import (DataPackage, Destination, PackageRevision,
                            Submission)

from . import mapping, state
from .evidence import normalize_op_time
from .tables import ROLL_OUT_ORDER, TABLES

CST = timezone(timedelta(hours=8))

# 明确投递结果（进入"已发"集合）；UNKNOWN 不盲重发、不计已发
_DELIVERED = ("ACCEPTED", "CONFIRMED", "PARTIAL")
_DELIVERED_STATES = _DELIVERED


def iter_rows_for_send(rows_by_table: dict[str, list[dict]],
                       sent_state: dict[str, str]):
    """按 ROLL_OUT_ORDER 遍历 + 源版本去重（sent_state: key→op_time）。

    yield (table_key, payload, source_op, skey)；depends_on 主表先于子表
    （exam→exam_item 的 parent_ops 语义）。"""
    parent_ops: dict[str, str] = {}
    for name in ROLL_OUT_ORDER:
        rows = rows_by_table.get(name) or []
        for row in rows:
            payload = mapping.build_payload(name, row, parent_ops)
            op = mapping.source_op_time(payload)
            rid = payload.get("id")
            skey = f"{name}/{rid}"
            if sent_state.get(skey) and sent_state[skey] >= op:
                continue                          # 同/旧源版本：跳过
            yield name, payload, op, skey


def source_version_digest(payload: dict) -> str:
    """源版本摘要：**源报文**（上传时间重写前）的完整 SHA256。"""
    return digest_of(payload)


def _parent_gate(db, tenant_id: str, source_instance: str, name: str,
                 payload: dict) -> tuple[bool, str]:
    """PT-01/R5：子表父门禁（与生产 pusher ParentAcceptanceTracker 语义对齐）。

    子表（tables.py 带 parent_fk）发送前核对父表记录在影子已发账
    （infectious_shadow_record）中 state=DELIVERED；主表未发/失败/等待时
    子表标 PARENT_WAIT 不误发 Mock——生产侧绝不允许父未受理先发子。"""
    t = TABLES[name]
    pfk = t.get("parent_fk")
    if not pfk:
        return True, ""
    parent_tbl = t.get("depends_on")
    if not parent_tbl:
        return True, ""
    pval = str(payload.get(pfk) or "")
    if not pval:
        return False, "PARENT_WAIT:missing-%s" % pfk
    prow = state.get_record(db, tenant_id, source_instance, parent_tbl, pval)
    if prow is None:
        return False, "PARENT_WAIT:%s=%s not-collected" % (pfk, pval)
    if prow.state != "DELIVERED":
        return False, "PARENT_WAIT:%s=%s state=%s" % (pfk, pval, prow.state)
    return True, prow.state


def idempotency_key(tenant_id: str, target_code: str, source_instance: str,
                    table: str, record_key: str, op_time: str,
                    content_digest: str) -> str:
    """A1 幂等身份（≤128 列宽）：tenant+target+source_instance+table/id+
    规范化 source_op_time+源内容摘要前 16。"""
    return (f"inf:{tenant_id[:8]}:{target_code}:{source_instance[:32]}"
            f":{table}/{record_key}@{op_time}:{content_digest[:16]}")[:128]


class _BatchPackages:
    """每批新包、批内 revision 递增（组织策略见模块 docstring）。"""

    def __init__(self, db, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id
        self.pkg = DataPackage(id=new_uuid(), tenant_id=tenant_id,
                               domain="infectious",
                               business_key=f"shadow:{utcnow().isoformat()}")
        db.add(self.pkg)
        db.flush()
        self.seq = 0

    def freeze(self, payload: dict, *, table: str, source_instance: str,
               op: str, content_digest: str) -> PackageRevision:
        self.seq += 1
        pr_payload = {"infectious": payload, "table": table,
                      "source_op_time": op,
                      "source_version_digest": content_digest}
        pr = PackageRevision(
            id=new_uuid(), tenant_id=self.tenant_id, package_id=self.pkg.id,
            revision=self.seq, state="FROZEN",
            payload=pr_payload,
            digest=digest_of(pr_payload),       # A1：完整 SHA256
            provenance={"domain": "infectious", "table": table,
                        "source_instance": source_instance,
                        "source_op_time": op})
        self.db.add(pr)
        self.db.flush()
        return pr


def _deliver_frozen(db, tenant_id: str, dest: Destination,
                    source_instance: str, name: str, payload: dict,
                    op: str, rid: str, content_digest: str, rec_state,
                    batches: _BatchPackages, now_cst: datetime,
                    stats: dict) -> None:
    """冻结（真实 digest）→ 幂等键 → Submission → generic 回环 Mock →
    结果账（A2.6：任何失败不得把该记录标成已发）。"""
    mapping.apply_truncation(name, payload)
    real = mapping.rewrite_upload_time(payload, now_cst)
    # __endpoint 仅用于 path_template 寻址（pusher: base/receive/<endpoint>）；
    # 记录字段等价（test_inf_payload_equivalence）不含该传输键
    real = {**real, "__endpoint": TABLES[name]["endpoint"]}
    pr = batches.freeze(real, table=name, source_instance=source_instance,
                        op=op, content_digest=content_digest)
    # 幂等键=源版本身份（上传时刻不参与）
    idem = idempotency_key(tenant_id, state.TARGET_CODE, source_instance,
                           name, rid, op, content_digest)
    existing = (db.query(Submission)
                .filter_by(destination_id=dest.id,
                           idempotency_key=idem).first())
    if existing is not None:
        # 同源版本已建 Submission（跨批次重采/断点恢复）——幂等跳过；
        # 仅当既有 Submission 已有明确结果才回填记录已发状态
        stats["skipped_same"] += 1
        if existing.state in _DELIVERED_STATES \
                and rec_state.state != "DELIVERED":
            rec_state.delivered_result = existing.state
            rec_state.state = "DELIVERED"
        return
    sub = Submission(
        id=new_uuid(), tenant_id=tenant_id, package_revision_id=pr.id,
        destination_id=dest.id, submission_revision=1,
        snapshot_digest=pr.digest, state="PREPARED",
        idempotency_key=idem)
    db.add(sub)
    db.flush()
    from core_handlers import perform_push
    result = perform_push(db, tenant_id, sub, real)
    outcome = result.get("outcome", "UNKNOWN")
    state.mark_delivery(db, rec_state, result=outcome,
                        submission_id=sub.id,
                        snapshot_digest=pr.digest, mock=True)
    if outcome in _DELIVERED:
        stats["sent"] += 1
        state.set_last_success_version(
            db, tenant_id, source_instance, name,
            f"{op}|{content_digest[:16]}")
    elif outcome == "UNKNOWN":
        stats["unknown"] += 1          # 不确定：不计失败也不计已发、不盲重发
    else:
        stats["failed"] += 1


def send_batch(db, tenant_id: str, dest: Destination, rows_by_table: dict,
               *, source_instance: str = "default",
               now_cst: datetime | None = None) -> dict:
    """整批发送（显式测试/演练入口；只允许 is_mock=True 目的地）。

    状态流转全部持久化（infectious_shadow_record）；本函数自行 commit。
    """
    _require_mock_dest(dest, tenant_id)
    now_cst = now_cst or utcnow()
    stats = dict(sent=0, skipped_same=0, skipped_invalid=0, failed=0,
                 skipped_conflict=0, skipped_old_version=0, unknown=0,
                 parent_wait=0)
    batches = _BatchPackages(db, tenant_id)
    parent_ops: dict[str, str] = {}     # 跨表父关联（exam→exam_item 等垫片）
    for name in ROLL_OUT_ORDER:
        rows = rows_by_table.get(name) or []
        for row in rows:
            payload = mapping.build_payload(name, row, parent_ops)
            op = normalize_op_time(mapping.source_op_time(payload))
            rid = str(payload.get("id", "") or "")     # 字符串，前导零保留
            content_digest = source_version_digest(payload)
            rec_state, action = state.register_record(
                db, tenant_id, source_instance, name, rid,
                source_op_time=op,
                source_version_digest=content_digest)
            if action in ("same_version", "old_version"):
                stats["skipped_same" if action == "same_version"
                      else "skipped_old_version"] += 1
                continue
            if action == "conflict":
                stats["skipped_conflict"] += 1
                continue
            if not op:
                state.mark_quality(db, rec_state, "MISSING_SOURCE_OP_TIME")
                stats["skipped_invalid"] += 1
                continue
            missing = mapping.validate(name, payload)
            if missing:
                state.mark_quality(db, rec_state,
                                   "REQUIRED_MISSING:" + ",".join(missing[:8]))
                stats["skipped_invalid"] += 1
                continue
            ready, why = _parent_gate(db, tenant_id, source_instance, name,
                                      payload)
            if not ready:
                state.mark_quality(db, rec_state, why)
                stats["parent_wait"] += 1
                continue
            _deliver_frozen(db, tenant_id, dest, source_instance, name,
                            payload, op, rid, content_digest, rec_state,
                            batches, now_cst, stats)
    db.commit()
    return stats


def send_pending(db, tenant_id: str, dest: Destination, *,
                 source_instance: str = "default", limit: int = 500,
                 now_cst: datetime | None = None) -> dict:
    """发送主路径：从持久化待处理队列消费（collect 冻结的快照）。

    - 只处理 state ∈ {PENDING, FAILED} 且有快照的记录（QUALITY_WAIT/
      CONFLICT/DELIVERED 不动——质量等待可见、冲突待人工、已发不重发）；
    - 依赖顺序按 ROLL_OUT_ORDER（主表先于子表）；FAILED 重试按同一源版本
      幂等键（不造新版本）；
    - 回环限制由 perform_push 的 generic Mock 装配强制（is_mock=True）。
    """
    _require_mock_dest(dest, tenant_id)
    from core_db.models import InfectiousShadowRecord
    now_cst = now_cst or utcnow()
    stats = dict(sent=0, skipped_same=0, skipped_invalid=0, failed=0,
                 skipped_conflict=0, skipped_old_version=0, unknown=0,
                 pending_drained=0, parent_wait=0)
    batches = _BatchPackages(db, tenant_id)
    for name in ROLL_OUT_ORDER:
        rows = (db.query(InfectiousShadowRecord)
                .filter_by(tenant_id=tenant_id, target_code=state.TARGET_CODE,
                           source_instance=source_instance, table_key=name)
                .filter(InfectiousShadowRecord.state.in_(("PENDING",
                                                          "FAILED")))
                .order_by(InfectiousShadowRecord.source_op_time.asc(),
                          InfectiousShadowRecord.source_record_key.asc())
                .limit(limit).all())
        for r in rows:
            payload = dict(r.payload_json or {})
            if not payload:
                stats["skipped_invalid"] += 1
                state.mark_quality(r, "SNAPSHOT_MISSING")
                continue
            stats["pending_drained"] += 1
            op = r.source_op_time
            rid = r.source_record_key
            content_digest = r.source_version_digest or \
                source_version_digest(payload)
            ready, why = _parent_gate(db, tenant_id, source_instance, name,
                                      payload)
            if not ready:
                state.mark_quality(db, r, why)
                stats["parent_wait"] += 1
                continue
            _deliver_frozen(db, tenant_id, dest, source_instance, name,
                            payload, op, rid, content_digest, r,
                            batches, now_cst, stats)
    db.commit()
    return stats


def _require_mock_dest(dest, tenant_id: str) -> None:
    if dest is None or dest.tenant_id != tenant_id:
        raise ValueError("destination not found for tenant")
    if not dest.is_mock:
        # 真实发送走 perform_push 闸门（EXTERNAL 目标必拒）；此处提前显式拒绝，
        # 不给影子任务任何触发真实发送的路径
        from contracts import ApiError
        raise ApiError("PROFILE_UNAPPROVED",
                       "影子插件只允许 Mock 目的地（生产由平台外 inf_pusher"
                       " 推送；切换须用户在场并按 LT-05 README 前提清单）")


# ---------------------------------------------------------------- 水位（兼容视图）
def watermark_key(table: str) -> str:
    return f"inf.shadow.watermark.{table}"


def load_watermark(db, tenant_id: str, source_instance: str,
                   table: str) -> str:
    """水位=source_cursor（保守推进；兼容旧函数名）。"""
    row = state.load_cursor(db, tenant_id, source_instance, table)
    return row.source_cursor if row else ""


def window_for(table: str, watermark: str) -> tuple[str, str, str]:
    """水位+overlap / 首跑 full_days（照 pusher Watermark 语义）。"""
    t = TABLES[table]
    if watermark:
        return watermark, "watermark+overlap", ""
    return "", "full(first-run)", f"{t['full_days']}d"
