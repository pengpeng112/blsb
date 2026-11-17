"""RT-04 A1：传染病影子运行状态存储（迁移021 两表的读写层）。

替代旧"写 PUBLISHED ConfigRevision"路径：
- 流水位：InfectiousShadowCursor（键=tenant+target+source_instance+stream），
  source_cursor 保守推进（仅越过已终态处理的记录），max_seen 仅观测；
  last_success_version 独立记录"得到明确投递结果的版本"；
- 记录版本状态：InfectiousShadowRecord（键=+source_record_key），行级唯一
  +条件更新防并发回退；拦截旧源版本/同版本幂等/同时间不同内容显式冲突；
- 旧 ConfigRevision 只读兼容：migrate_legacy_config 一次性把旧
  watermarks/sent 状态迁入新表（幂等；不删除、不修改旧配置行——旧
  revision/content/digest/state 字节语义不变，测试锁定）。
"""
from __future__ import annotations

from contracts import new_uuid
from core_db.engine import utcnow

LEGACY_OWNER = "infectious.shadow"
TARGET_CODE = "inf_cdc_frontend"

# delivered_result 允许的"明确投递结果"（evidence.py A0：UNKNOWN/RETRYABLE
# /质量失败/冲突不得进入已发集合）
DELIVERED_RESULTS = ("ACCEPTED", "CONFIRMED", "PARTIAL")


# ---------------------------------------------------------------- 流水位
def load_cursor(db, tenant_id: str, source_instance: str, stream: str):
    """取流水位行（无则 None；不创建——创建走 advance）。"""
    from core_db.models import InfectiousShadowCursor
    return (db.query(InfectiousShadowCursor)
            .filter_by(tenant_id=tenant_id, target_code=TARGET_CODE,
                       source_instance=source_instance, stream_key=stream)
            .first())


def mark_seen(db, tenant_id: str, source_instance: str, stream: str,
              scan_key_value: str) -> None:
    """观测推进 max_seen（只记录读到过的最大扫描键；不影响 source_cursor）。"""
    from core_db.models import InfectiousShadowCursor
    row = load_cursor(db, tenant_id, source_instance, stream)
    if row is None:
        row = InfectiousShadowCursor(
            id=new_uuid(), tenant_id=tenant_id, target_code=TARGET_CODE,
            source_instance=source_instance, stream_key=stream)
        db.add(row)
    if scan_key_value > (row.max_seen or ""):
        row.max_seen = scan_key_value


def advance_cursor(db, tenant_id: str, source_instance: str, stream: str,
                   scan_key_value: str) -> bool:
    """保守推进 source_cursor（仅前进；条件更新防并发回退）。

    调用前提：该扫描键之前的记录已全部终态处理（成功投递/显式质量失败/
    显式冲突登记）。返回是否实际推进。
    """
    from core_db.models import InfectiousShadowCursor
    row = load_cursor(db, tenant_id, source_instance, stream)
    if row is None:
        row = InfectiousShadowCursor(
            id=new_uuid(), tenant_id=tenant_id, target_code=TARGET_CODE,
            source_instance=source_instance, stream_key=stream)
        db.add(row)
    if scan_key_value > (row.source_cursor or ""):
        row.source_cursor = scan_key_value
        if scan_key_value > (row.max_seen or ""):
            row.max_seen = scan_key_value
        return True
    return False


def set_last_success_version(db, tenant_id: str, source_instance: str,
                             stream: str, version_key: str) -> None:
    from core_db.models import InfectiousShadowCursor
    row = load_cursor(db, tenant_id, source_instance, stream)
    if row is None:
        row = InfectiousShadowCursor(
            id=new_uuid(), tenant_id=tenant_id, target_code=TARGET_CODE,
            source_instance=source_instance, stream_key=stream)
        db.add(row)
    row.last_success_version = version_key


# ---------------------------------------------------------------- 记录版本状态
def get_record(db, tenant_id: str, source_instance: str, table: str,
               record_key: str):
    from core_db.models import InfectiousShadowRecord
    return (db.query(InfectiousShadowRecord)
            .filter_by(tenant_id=tenant_id, target_code=TARGET_CODE,
                       source_instance=source_instance, table_key=table,
                       source_record_key=record_key)
            .first())


def register_record(db, tenant_id: str, source_instance: str, table: str,
                    record_key: str, *, source_op_time: str,
                    source_version_digest: str, state: str = "PENDING",
                    mock: bool = True) -> tuple[object, str]:
    """登记/校验记录版本，返回 (行, 动作)。

    动作 ∈ {new, same_version, newer_version, old_version, conflict}：
    - new：未见过的记录键（或旧状态是终态失败后新版本）→ 新不可变快照；
    - same_version：op_time 与 source_version_digest 均相同 → 幂等（已发/
      已登记，不重复投递，不产生新 Submission）；
    - newer_version：op_time 更新（或同时间内容不同→**conflict** 除外）→
      新不可变快照；
    - old_version：op_time 更旧 → 拒绝（次序保护）；
    - conflict：**同 op_time 不同内容摘要** → 显式冲突，不静默覆盖，
      状态置 CONFLICT 待人工核对。
    """
    row = get_record(db, tenant_id, source_instance, table, record_key)
    from core_db.models import InfectiousShadowRecord
    if row is None:
        row = InfectiousShadowRecord(
            id=new_uuid(), tenant_id=tenant_id, target_code=TARGET_CODE,
            source_instance=source_instance, table_key=table,
            source_record_key=record_key, source_op_time=source_op_time,
            source_version_digest=source_version_digest, state=state,
            mock=mock)
        db.add(row)
        db.flush()
        return row, "new"
    if source_op_time == row.source_op_time:
        if source_version_digest == row.source_version_digest:
            return row, "same_version"
        row.state = "CONFLICT"
        row.last_error = (f"same op_time different content: "
                          f"known={row.source_version_digest[:16]} "
                          f"incoming={source_version_digest[:16]}")
        db.flush()
        return row, "conflict"
    if source_op_time < row.source_op_time:
        return row, "old_version"
    # 更新源版本：新不可变快照（清空旧投递结果——新版本需重新投递）
    row.source_op_time = source_op_time
    row.source_version_digest = source_version_digest
    row.state = state
    row.delivered_result = ""
    row.submission_id = ""
    row.snapshot_digest = ""
    row.attempts = 0
    row.last_error = ""
    row.mock = mock
    db.flush()
    return row, "newer_version"


def mark_delivery(db, row, *, result: str, submission_id: str = "",
                   snapshot_digest: str = "", mock: bool = True) -> None:
    """记录投递结果：明确结果才置 DELIVERED；UNKNOWN/失败保持待处理。"""
    row.attempts = int(getattr(row, "attempts", 0) or 0) + 1
    row.submission_id = submission_id or row.submission_id
    row.snapshot_digest = snapshot_digest or row.snapshot_digest
    row.mock = mock
    if result in DELIVERED_RESULTS:
        row.delivered_result = result
        row.state = "DELIVERED"
    elif result in ("UNKNOWN",):
        row.state = "PENDING"           # 不确定：不盲重发标记，待下一班次
        row.last_error = "delivery UNKNOWN"
    else:
        row.state = "FAILED"
        row.last_error = f"delivery {result}"[:200]


def mark_quality(db, row, reason: str) -> None:
    """质量等待（缺源时间/必填缺失/缺父记录）——可见、可重试，不是已发。"""
    row.state = "QUALITY_WAIT"
    row.last_error = reason[:200]


def delivered_map(db, tenant_id: str, source_instance: str,
                  table: str) -> dict[str, str]:
    """兼容视图：{record_key: source_op_time}（仅明确投递结果已发的记录）。

    供旧 iter_rows_for_send 语义（跳过同/旧源版本）从持久层取状态——
    进程重启/新 Session 后仍有效（A1 验收点）。
    """
    from core_db.models import InfectiousShadowRecord
    rows = (db.query(InfectiousShadowRecord)
            .filter_by(tenant_id=tenant_id, target_code=TARGET_CODE,
                       source_instance=source_instance, table_key=table,
                       state="DELIVERED").all())
    return {r.source_record_key: r.source_op_time for r in rows}


# ---------------------------------------------------------------- 旧配置只读迁入
def migrate_legacy_config(db, tenant_id: str) -> dict:
    """旧 PUBLISHED ConfigRevision（infectious.shadow）→ 新状态表一次性迁入。

    - 只在新表无该租户任何行时执行（幂等：重复调用零动作）；
    - 旧 watermarks → source_cursor/max_seen（同值，保守推进语义下首迁
      等价）；旧 sent {table/rid: op} → DELIVERED 记录行（digest 空=旧证据
      只有源时间，冲突检测退化为"同时间即同版本"）；
    - **不删除、不修改旧 ConfigRevision**（旧 revision/content/digest/state
      字节语义不变；只读 SELECT）。
    """
    from core_db.models import (ConfigRevision, InfectiousShadowCursor,
                                InfectiousShadowRecord)
    out = {"imported_cursors": 0, "imported_records": 0, "skipped": False}
    has_new = (db.query(InfectiousShadowCursor.id)
               .filter_by(tenant_id=tenant_id).first() is not None) or \
              (db.query(InfectiousShadowRecord.id)
               .filter_by(tenant_id=tenant_id).first() is not None)
    if has_new:
        out["skipped"] = True
        return out
    cr = (db.query(ConfigRevision)
          .filter_by(tenant_id=tenant_id, config_type="plugin_state",
                     owner_id=LEGACY_OWNER, state="PUBLISHED")
          .order_by(ConfigRevision.revision.desc()).first())
    if cr is None:
        out["skipped"] = True
        return out
    content = dict(cr.content_json or {})
    for table, wm in (content.get("watermarks") or {}).items():
        db.add(InfectiousShadowCursor(
            id=new_uuid(), tenant_id=tenant_id, target_code=TARGET_CODE,
            source_instance="default", stream_key=str(table),
            source_cursor=str(wm or ""), max_seen=str(wm or ""),
            legacy_imported=True))
        out["imported_cursors"] += 1
    for skey, op in (content.get("sent") or {}).items():
        try:
            table, rid = str(skey).split("/", 1)
        except ValueError:
            continue
        db.add(InfectiousShadowRecord(
            id=new_uuid(), tenant_id=tenant_id, target_code=TARGET_CODE,
            source_instance="default", table_key=table,
            source_record_key=rid, source_op_time=str(op or ""),
            source_version_digest="", state="DELIVERED",
            delivered_result="ACCEPTED", mock=True))
        out["imported_records"] += 1
    return out
