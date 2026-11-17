"""inf_pusher state 副本导入器（LT-05 A3；RT-04 A3 计数口径；PT-03 三硬伤修复）。

文件（只处理用户拷贝来的副本/只读同步增量，不触生产）：
- receipts-YYYYMMDD.jsonl：每行 {id,result,errorCode,desc,attempt,stage,mode,table,ts}
  ——行内 ts 为**容器 UTC**；
- sent.jsonl：每行 {id:"<table>/<记录ID>",opTime,table,ts}——opTime 为**源端 CST**；
- watermark.json：{table: 最大 operationTime}——源端 CST；
- （PT-03）last-run-real.json / last-run-reconcile.json / reconcile-YYYYMMDD.json /
  pending-wait.json / watchdog status.json / daily-YYYYMMDD.txt / alerts-YYYYMMDD.jsonl
  ——完整 JSON/文本快照，解析进遥测/快照表（白名单键，零患者字段）。

PT-03 三硬伤修复：
1. **混合时区**：`import_files(..., source_tz="AUTO_MIXED")`（推荐默认）= receipts 按
   UTC、sent/watermark 按 CST 解释；显式 `receipts_tz=`/`sent_tz=` 优先；旧调用
   `source_tz="UTC"|"CST"` 行为完全不变（两侧同口径）。根除"必差 8 小时"错位；
2. **parent-wait 单列**：`classify` 显式识别 `stage=="parent-wait"` →
   `("PARENT_WAIT", "PARENT_WAIT")` → 桶 `PENDING`/证据 `PARENT_WAITING`
   （等待父表受理，绝不再混入 REJECTED 被拒桶）；
3. **13 表规范映射**：`INF_TABLES` 按《03-接口总览与状态矩阵》权威编码对齐全部
   13 张在推接口（含中文名/端点/主子依赖）——旧映射系统性错位（death→07 应为
   17 等），迁移 024 按business_type 修正存量行 doc_type_code。

保留既有语义（R06/RT-04 A3，测试锁定）：
- 字段白名单（id/result/errorCode/desc/attempt/stage/mode/table/ts/opTime），
  其余字段一律丢弃；desc 脱敏+截断200；id 形如 18 位证件号 → 整行拒绝；
- result=true → ACCEPTED/FRONTEND_ACCEPTED，不升 CONFIRMED；MOCK 单列；
- 幂等身份 tenant+target+source_instance+logical_file+行偏移+digest；
  dry-run 零写；事件按 (ts,attempt,stage) 归并：较新真实成功不被旧失败覆盖；
- 行级恒等式 rows_read = rows_valid + parse/shape/privacy 三类拒绝；
  记录级 records_* 与行级不得相加；SENT_LEDGER_ONLY 观测单列 ACCEPTED。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

from contracts import new_uuid
from core_db.engine import utcnow
from core_db.models import (ExternalImportBatch, ExternalImportLine,
                            ExternalSubmissionRecord)
from plugins.destinations.generic_http_json.redact import redact_text

CST = timezone(timedelta(hours=8))
UTC = timezone.utc

WHITELIST = ("id", "result", "errorCode", "desc", "attempt", "stage", "mode",
             "table", "ts", "opTime")
_IDCARD_RE = re.compile(r"^\d{17}[\dXx]$")
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_LINE_CHARS = 64 * 1024
MAX_JSON_DEPTH = 8

# PT-03：13 张在推接口权威映射（编码依据《数据上报/4、传染病病历数据上报/
# 03-接口总览与状态矩阵.md》——旧映射 death→07/exam→09/… 系统性错位，已修正；
# 存量行由迁移 024 按 business_type 修正 doc_type_code）。
INF_TABLES = {
    "admission":      {"code": "09", "name": "住院病案首页", "endpoint": "emrAdmissionRecord",   "parent": ""},
    "admission_info": {"code": "06", "name": "入院记录",     "endpoint": "emrAdmissionInfo",     "parent": ""},
    "discharge_info": {"code": "10", "name": "出院记录",     "endpoint": "emrDischargeInfo",     "parent": ""},
    "death":          {"code": "17", "name": "死亡记录",     "endpoint": "emrDeathInfo",         "parent": ""},
    "first_course":   {"code": "07", "name": "首次病程记录", "endpoint": "emrFirstCourse",       "parent": ""},
    "daily_course":   {"code": "08", "name": "日常病程记录", "endpoint": "emrDailyCourse",       "parent": ""},
    "exam":           {"code": "11", "name": "检查报告主表", "endpoint": "emrExClinical",        "parent": ""},
    "exam_item":      {"code": "12", "name": "检查报告项目", "endpoint": "emrExClinicalItem",    "parent": "exam"},
    "ex_lab":         {"code": "13", "name": "检验报告主表", "endpoint": "emrExLab",             "parent": ""},
    "ex_lab_item":    {"code": "14", "name": "检验报告项目", "endpoint": "emrExLabItem",         "parent": "ex_lab"},
    "order":          {"code": "15", "name": "医嘱处方主表", "endpoint": "emrOrder",             "parent": ""},
    "order_item":     {"code": "16", "name": "医嘱药品明细", "endpoint": "emrOrderItem",         "parent": "order"},
    "vital_signs":    {"code": "18", "name": "生命体征记录", "endpoint": "emrVitalSignsRecord",  "parent": ""},
}
DOC_TYPE_CODES = {t: v["code"] for t, v in INF_TABLES.items()}
DOC_TYPE_NAMES = {v["code"]: v["name"] for v in INF_TABLES.values()}

_RESULT_TO_BUCKET = {
    "ACCEPTED": ("ACCEPTED", "FRONTEND_ACCEPTED"),
    "REJECTED": ("REJECTED", "CLIENT_EVIDENCE"),
    "CLIENT_INVALID": ("REJECTED", "CLIENT_EVIDENCE"),
    # PT-03：等待父表受理单列——主子门禁保护中的正常等待，绝非被拒
    "PARENT_WAIT": ("PENDING", "PARENT_WAITING"),
    "RETRYABLE": ("UNKNOWN", "INCONCLUSIVE"),
    "NETWORK_ERROR": ("UNKNOWN", "INCONCLUSIVE"),
    "MOCK": ("NOT_REPORTABLE", "MOCK_ONLY"),
    # 纯 sent.jsonl 观测：生产只读代码证明=平台受理（见模块 docstring）
    "SENT_LEDGER_ONLY": ("ACCEPTED", "SENT_LEDGER_ONLY"),
}

# PT-03：完整 JSON/文本快照文件名 → 解析类别（不再走单行 JSONL 报 NOT_JSON）
_SNAPSHOT_JSON = ("watermark.json", "last-run-real.json", "last-run-reconcile.json",
                  "pending-wait.json", "status.json")
_SNAPSHOT_RE_JSON = re.compile(r"^reconcile-\d{8}\.json$")
_SNAPSHOT_RE_TXT = re.compile(r"^daily-\d{8}\.txt$")
_SNAPSHOT_RE_JSONL = re.compile(r"^alerts-\d{8}\.jsonl$")
_SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024
_WATCHDOG_CHECK_NAMES = (
    "c1_freshness", "c2_lastrun_error", "c3_skip_streak", "c4_lock_hang",
    "c5_push_failures", "c6_reconcile", "c7_pusher_alerts", "c8_watermark",
    "c9_disk", "c10_container", "c11_relay_tunnel")


def classify(rec: dict) -> tuple[str, str]:
    """一行回执 → (last_result, bucket/evidence)。

    R06：MOCK 永远单列；PT-03：parent-wait 单列（PENDING/PARENT_WAITING），
    绝不再落入 REJECTED 被拒桶；client-validate 单列必填尾差。"""
    if str(rec.get("mode", "")) == "mock" or rec.get("stage") == "mock":
        return "MOCK", "MOCK"
    if rec.get("result") is True:
        code = str(rec.get("errorCode", "") or "")
        if code in ("01", "99"):
            return "RETRYABLE", "RETRYABLE"      # result=true 但服务端异常码保守归可重试
        return "ACCEPTED", "ACCEPTED"
    stage = str(rec.get("stage", "") or "")
    code = str(rec.get("errorCode", "") or "")
    if stage == "parent-wait":
        return "PARENT_WAIT", "PARENT_WAIT"      # PT-03：等待父表受理（主子门禁保护中）
    if stage == "client-validate":
        return "CLIENT_INVALID", "CLIENT_INVALID"
    if stage == "network":
        return "NETWORK_ERROR", "NETWORK_ERROR"
    if code in ("01", "99"):
        return "RETRYABLE", "RETRYABLE"
    return "REJECTED", "REJECTED"


def _parse_ts(raw: str, tz_name: str):
    """无偏移时间串按 tz_name(UTC|CST) 解释 → aware UTC datetime；失败返回 None。"""
    s = str(raw or "").strip().replace("T", " ")
    naive = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    if naive is None:
        return None
    tz = CST if str(tz_name).upper() == "CST" else UTC
    return naive.replace(tzinfo=tz).astimezone(UTC)


def _depth_ok(obj, d=0) -> bool:
    if d > MAX_JSON_DEPTH:
        return False
    if isinstance(obj, dict):
        return all(_depth_ok(v, d + 1) for v in obj.values())
    if isinstance(obj, list):
        return all(_depth_ok(v, d + 1) for v in obj)
    return True


def _read_lines(name: str, content: bytes, errors: list):
    """文件内容 → [(offset, 原行字节, 解析 dict)]。

    行级拒绝（NOT_JSON/LINE_TOO_LONG/SHAPE_INVALID）计入 errors（带 offset）
    并计入该文件非空行数——A3：rows_read ⊇ 全部行级拒绝。"""
    if len(content) > MAX_FILE_BYTES:
        errors.append({"file": name, "reason": "FILE_TOO_LARGE",
                       "size": len(content)})
        return [], 0
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        errors.append({"file": name, "reason": "NOT_UTF8"})
        return [], 0
    out = []
    nonempty = 0
    for offset, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        nonempty += 1
        if len(line) > MAX_LINE_CHARS:
            errors.append({"file": name, "offset": offset,
                           "reason": "LINE_TOO_LONG"})
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            errors.append({"file": name, "offset": offset,
                           "reason": "NOT_JSON"})
            continue
        if not isinstance(obj, dict) or not _depth_ok(obj):
            errors.append({"file": name, "offset": offset,
                           "reason": "SHAPE_INVALID"})
            continue
        out.append((offset, line.encode("utf-8"), obj))
    return out, nonempty


# ------------------------------------------------------------ PT-03 快照解析
def parse_snapshot(name: str, content: bytes) -> dict | None:
    """完整 JSON/文本状态快照 → 白名单结构化 dict（零患者字段）；非快照文件
    返回 None（走既有 JSONL 行管线）。解析失败返回 {"_error": 原因}。"""
    if name in _SNAPSHOT_JSON or _SNAPSHOT_RE_JSON.match(name):
        kind = "reconcile" if _SNAPSHOT_RE_JSON.match(name) else name
        if len(content) > _SNAPSHOT_MAX_BYTES:
            return {"_kind": kind, "_error": "FILE_TOO_LARGE"}
        try:
            obj = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {"_kind": kind, "_error": "NOT_JSON"}
        # last-run-real/last-run-reconcile 顶层为每表班次统计数组
        if name in ("last-run-real.json", "last-run-reconcile.json"):
            if not isinstance(obj, list) or not _depth_ok(obj):
                return {"_kind": name, "_error": "SHAPE_INVALID"}
            return _whitelist_shifts(name, obj)
        if not isinstance(obj, dict) or not _depth_ok(obj):
            return {"_kind": kind, "_error": "SHAPE_INVALID"}
        return _whitelist_snapshot(kind, obj)
    if _SNAPSHOT_RE_TXT.match(name):
        if len(content) > _SNAPSHOT_MAX_BYTES:
            return {"_kind": "daily", "_error": "FILE_TOO_LARGE"}
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return {"_kind": "daily", "_error": "NOT_UTF8"}
        m = re.match(r"^daily-(\d{8})\.txt$", name)
        return {"_kind": "daily", "ymd": m.group(1) if m else "",
                "text": redact_text(text[:20000])}
    if _SNAPSHOT_RE_JSONL.match(name):
        if len(content) > _SNAPSHOT_MAX_BYTES:
            return {"_kind": "alerts", "_error": "FILE_TOO_LARGE"}
        alerts = []
        m = re.match(r"^alerts-(\d{8})\.jsonl$", name)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return {"_kind": "alerts", "_error": "NOT_UTF8"}
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                a = json.loads(line)
            except ValueError:
                continue
            if not isinstance(a, dict):
                continue
            alerts.append({"ts": str(a.get("ts", ""))[:32],
                           "level": str(a.get("level", ""))[:16],
                           "subject": redact_text(str(a.get("subject", ""))[:120])})
        return {"_kind": "alerts", "ymd": m.group(1) if m else "",
                "items": alerts[-50:]}
    return None


def _whitelist_shifts(kind: str, arr: list) -> dict:
    """last-run-real/last-run-reconcile（每表班次统计数组）白名单投影：
    仅保留 13 表键与计数字段（table/rows/sent/parent_wait/skipped_invalid/
    failed/truncated/since/reason/error），供遥测表 last_shift_* 列。"""
    shifts = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        tkey = str(it.get("table", ""))[:64]
        if not re.fullmatch(r"[a-z_0-9]+", tkey):
            continue
        shifts.append({
            "table": tkey,
            "since": str(it.get("since", ""))[:32],
            "reason": str(it.get("reason", ""))[:32],
            "rows": int(it.get("rows", 0) or 0),
            "sent": int(it.get("sent", 0) or 0),
            "parent_wait": int(it.get("parent_wait", 0) or 0),
            "skipped_invalid": int(it.get("skipped_invalid", 0) or 0),
            "failed": int(it.get("failed", 0) or 0),
            "truncated": int(it.get("truncated", 0) or 0),
            "error": str(it.get("error", "") or "")[:500],
        })
    return {"_kind": kind, "_arr_shifts": shifts}


def _whitelist_snapshot(kind: str, obj: dict) -> dict:
    """状态快照白名单投影（守护/推送器自身已是白名单输出；此处再收紧一层）。"""
    out: dict = {"_kind": kind}
    if kind == "watermark.json":
        wm = {}
        for k, v in obj.items():
            if re.fullmatch(r"[a-z_0-9]+", str(k)) and \
                    re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", str(v)):
                wm[str(k)] = str(v)
        out["watermark"] = wm
        return out
    if kind == "pending-wait.json":
        items = obj.get("items")
        out["pending_wait_total"] = len(items) if isinstance(items, dict) else 0
        return out
    if kind == "status.json":
        out["checked_at"] = str(obj.get("ts", ""))[:32]
        out["host_label"] = redact_text(str(obj.get("host", ""))[:64])
        out["overall_level"] = str(obj.get("level", ""))[:16]
        out["relay_reachable"] = obj.get("relay_reachable")
        out["driver_errors"] = len(obj.get("driver_errors") or [])
        checks = obj.get("checks") or {}
        out["checks"] = {k: {"level": str(v.get("level", ""))[:16],
                             "elapsed_ms": int(v.get("elapsed_ms", 0) or 0),
                             "detail": redact_text(str(v.get("detail", ""))[:160])}
                         for k, v in checks.items()
                         if k in _WATCHDOG_CHECK_NAMES and isinstance(v, dict)}
        return out
    if kind == "reconcile":
        out["day"] = str(obj.get("day", ""))[:8]
        out["ran_at"] = str(obj.get("ran_at", ""))[:32]
        out["total_repushed"] = int(obj.get("total_repushed", 0) or 0)
        out["total_failed"] = int(obj.get("total_failed", 0) or 0)
        out["total_parent_wait"] = int(obj.get("total_parent_wait", 0) or 0)
        out["total_skipped_invalid"] = int(obj.get("total_skipped_invalid", 0) or 0)
        tables = obj.get("tables") or {}
        out["tables"] = {str(k)[:64]: {
            "window_rows": int(v.get("window_rows", 0) or 0),
            "repushed": int(v.get("repushed", 0) or 0),
            "recovered_wait": int(v.get("recovered_wait", 0) or 0),
            "parent_wait": int(v.get("parent_wait", 0) or 0),
            "skipped_invalid": int(v.get("skipped_invalid", 0) or 0),
            "failed": int(v.get("failed", 0) or 0)}
            for k, v in tables.items()
            if isinstance(v, dict) and re.fullmatch(r"[a-z_0-9]+", str(k))}
        return out
    return out


def import_files(db, tenant_id: str, target_code: str, *, files: list[dict],
                 source_label: str = "", source_instance: str = "",
                 source_tz: str = "", actor: str = "",
                 dry_run: bool = False,
                 receipts_tz: str | None = None,
                 sent_tz: str | None = None,
                 offset_bases: dict | None = None,
                 persist_snapshots: bool = True) -> dict:
    """files=[{name, content_base64}]（或 {"name","content":bytes}）。

    PT-03 时区：`source_tz="AUTO_MIXED"`（生产推荐默认）= receipts 按 UTC、
    sent/watermark 按 CST；显式 receipts_tz/sent_tz 优先；旧调用
    source_tz="UTC"|"CST" 两侧同口径（完全向后兼容）。
    PT-03 增量：offset_bases={文件名: 起始全局行号}——流式同步按字节偏移续读时
    行账 offset 用全局行号（同文件跨批次零重复行账）。
    dry-run：零写（不建批次、不写行、不写台账）；返回统计与拒绝原因。"""
    src_tz_up = str(source_tz).upper()
    receipts_tz = (str(receipts_tz).upper() if receipts_tz else None)
    sent_tz = (str(sent_tz).upper() if sent_tz else None)
    for tz in (receipts_tz, sent_tz):
        if tz is not None and tz not in ("UTC", "CST"):
            raise ValueError("receipts_tz/sent_tz 只允许 UTC 或 CST")
    if src_tz_up == "AUTO_MIXED":
        receipts_tz = receipts_tz or "UTC"
        sent_tz = sent_tz or "CST"
    elif src_tz_up in ("UTC", "CST"):
        receipts_tz = receipts_tz or src_tz_up
        sent_tz = sent_tz or src_tz_up
    else:
        raise ValueError("source_tz 必须显式为 UTC/CST/AUTO_MIXED"
                         "（无偏移时间戳的解释时区；不默认不猜）")
    if src_tz_up == "AUTO_MIXED":
        source_tz_store = "AUTO_MIXED(UTC+CST)"
    else:
        source_tz_store = src_tz_up
    source_instance = source_instance or "default"
    errors: list[dict] = []
    parsed: dict[str, list] = {}
    file_meta: list[dict] = []
    snapshots: list[dict] = []
    rows_read = 0                    # A3：非空行读取数（含行级拒绝）
    reject_parse = reject_shape = reject_privacy = 0
    for f in files:
        name = str(f.get("name", "") or "")
        content = f.get("content")
        if content is None:
            b64 = str(f.get("content_base64", "") or "")
            try:
                content = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                errors.append({"file": name, "reason": "BASE64_INVALID"})
                continue
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
            errors.append({"file": name, "reason": "FILENAME_INVALID"})
            continue
        # PT-03：完整 JSON/文本快照（watermark/last-run/reconcile/pending-wait/
        # status/daily/alerts）不再走单行 JSONL（旧行为整文件报 NOT_JSON）——
        # 结构化解析进遥测/快照表（白名单键，零患者字段）
        snap = parse_snapshot(name, content)
        if snap is not None:
            snapshots.append({"name": name, "sha256":
                              hashlib.sha256(content).hexdigest(), **snap})
            file_meta.append({"name": name,
                              "sha256": hashlib.sha256(content).hexdigest(),
                              "rows": 0})
            continue
        lines, nonempty = _read_lines(name, content, errors)
        rows_read += nonempty
        parsed[name] = lines
        file_meta.append({"name": name,
                          "sha256": hashlib.sha256(content).hexdigest(),
                          "rows": len(lines)})

    receipts: dict[str, list] = {}     # record_key → [(ts_naive_utc, attempt, rec)]
    sent: dict[str, datetime] = {}
    for name, lines in parsed.items():
        for offset, raw, obj in lines:
            keep = {k: obj[k] for k in WHITELIST if k in obj}
            rid = str(keep.get("id", "") or "")
            if _IDCARD_RE.match(rid):
                errors.append({"file": name, "offset": offset,
                               "reason": "ID_LOOKS_LIKE_IDCARD"})
                reject_privacy += 1
                continue
            table = str(keep.get("table", "") or "")
            if name.startswith("receipts-") or "result" in keep:
                if not rid or not table:
                    errors.append({"file": name, "offset": offset,
                                   "reason": "MISSING_ID_OR_TABLE"})
                    reject_shape += 1
                    continue
                ts = _parse_ts(keep.get("ts", ""), receipts_tz)
                receipts.setdefault(f"{table}/{rid}", []).append(
                    (ts, int(keep.get("attempt", 0) or 0), keep, name,
                     offset, ts is None))
            elif name == "sent.jsonl" or "opTime" in keep:
                key = rid if "/" in rid else f"{table}/{rid}"
                # PT-03：sent.opTime 为源端 CST（混合口径根除 8 小时错位）
                ot = _parse_ts(keep.get("opTime", ""), sent_tz)
                if key and ot is not None:
                    sent[key] = ot.replace(tzinfo=None)   # 统一 naive UTC 存储
                else:
                    # 无法归属的 sent 行（缺键/源时间不可解析）按形状拒绝计入
                    errors.append({"file": name, "offset": offset,
                                   "reason": "SENT_ROW_UNATTRIBUTABLE"})
                    reject_shape += 1
            # watermark.json 已走快照解析（PT-03），此处不再可达

    # 行级拒绝分类计数（A3 恒等式分母已含全部拒绝行）
    for e in errors:
        if "offset" not in e:
            continue
        if e.get("reason") == "NOT_JSON" or e.get("reason") == "LINE_TOO_LONG":
            reject_parse += 1
        elif e.get("reason") == "SHAPE_INVALID":
            reject_shape += 1
    rows_rejected = reject_parse + reject_shape + reject_privacy
    rows_valid = rows_read - rows_rejected

    stats = {
        # 行级（A3 恒等式：rows_read = rows_valid + 三类拒绝）
        "rows_read": rows_read, "rows_valid": rows_valid,
        "rows_rejected": rows_rejected,
        "rows_rejected_parse": reject_parse,
        "rows_rejected_shape": reject_shape,
        "rows_rejected_privacy": reject_privacy,
        "lines_duplicate_position": 0, "lines_conflict_position": 0,
        # 记录级（多行回执合并为一个记录；不得与 rows_* 强相加）
        "records_total": 0, "records_created": 0, "records_updated": 0,
        "records_unchanged": 0, "sent_only_records": 0,
        "sent_entries": len(sent),
        # PT-03：快照解析统计
        "snapshots_parsed": len(snapshots),
        "snapshots": [{k: s.get(k) for k in ("name", "_kind", "_error")}
                      for s in snapshots],
        "timezone": {"receipts": receipts_tz, "sent": sent_tz},
        "dry_run": dry_run, "files": file_meta,
        "rejects": errors[:20]}

    if dry_run:
        stats["records_total"] = len(set(receipts) | set(sent))
        return stats

    batch = ExternalImportBatch(
        id=new_uuid(), tenant_id=tenant_id, target_code=target_code,
        source_label=source_label[:255], source_instance=source_instance,
        source_tz=source_tz_store[:64], files=file_meta,
        rows_read=rows_read, rows_rejected=rows_rejected,
        started_at=utcnow(), imported_by=actor[:64], dry_run=False,
        report={"rejects": errors[:20]})
    db.add(batch)
    db.flush()

    def _logical(name: str) -> str:
        # 逻辑源文件身份：重命名不换身份——按行首特征归类为 receipts/sent/watermark
        if name.startswith("receipts-"):
            return "receipts:" + re.sub(r"\d{8}", "YYYYMMDD", name)
        if name == "sent.jsonl":
            return "sent.jsonl"
        return name

    # PT-03：快照持久化（遥测/守护快照表；白名单键；零患者字段）
    if persist_snapshots:
        _persist_snapshots(db, tenant_id, target_code, source_instance,
                           snapshots, batch)

    created = updated = unchanged = 0
    for key, events in receipts.items():
        table = key.split("/", 1)[0]
        ev_sorted = sorted(
            [e for e in events if e[0] is not None],
            key=lambda e: (e[0], e[1]), reverse=True)
        chosen = ev_sorted[0] if ev_sorted else None
        unresolved = [e for e in events if e[0] is None]
        last_result, detail = classify(chosen[2]) if chosen else \
            ("RETRYABLE", "UNKNOWN_TS")
        bucket, evidence = _RESULT_TO_BUCKET.get(last_result,
                                                 ("UNKNOWN", "UNMAPPED"))
        ts_utc = (chosen[0].replace(tzinfo=None) if chosen
                  and chosen[0] is not None else None)   # naive UTC 存储
        # 幂等行账（同来源同位置同 digest → duplicate；同位置不同 digest →
        # 冲突待核：保留既有行账与新证据，计数入批次报告，不静默丢弃）；
        # PT-03：流式增量时 offset_bases 提供全局行号基线（跨批次零重复行账）
        for e in events:
            _, _, keep, fname, offset, _ = e
            digest = hashlib.sha256(
                json.dumps(keep, sort_keys=True,
                           ensure_ascii=False).encode()).hexdigest()
            base = int((offset_bases or {}).get(fname, 0) or 0)
            g_offset = base + int(offset)
            line = (db.query(ExternalImportLine)
                    .filter_by(tenant_id=tenant_id, target_code=target_code,
                               source_instance=source_instance,
                               logical_file=_logical(fname),
                               line_offset=g_offset).first())
            if line is None:
                db.add(ExternalImportLine(
                    id=new_uuid(), tenant_id=tenant_id, target_code=target_code,
                    source_instance=source_instance,
                    logical_file=_logical(fname), line_offset=g_offset,
                    line_digest=digest, batch_id=batch.id))
            elif line.line_digest != digest:
                stats["lines_conflict_position"] += 1
            else:
                stats["lines_duplicate_position"] += 1
        # A3：记录唯一键含 source_instance（迁移022）；跨实例不合并
        rec = (db.query(ExternalSubmissionRecord)
               .filter_by(tenant_id=tenant_id, target_code=target_code,
                          source_instance=source_instance,
                          record_key=key).first())
        op_time = sent.get(key)
        attempts_visible = len(events)          # 下界，不伪造精确值
        if rec is None:
            db.add(ExternalSubmissionRecord(
                id=new_uuid(), tenant_id=tenant_id, target_code=target_code,
                source_instance=source_instance,
                record_key=key, business_type=table,
                doc_type_code=DOC_TYPE_CODES.get(table, ""),
                first_seen_at=ts_utc or utcnow(), last_attempt_at=ts_utc,
                attempts=attempts_visible, last_result=last_result,
                bucket=bucket, evidence_level=evidence,
                last_error_code=str((chosen[2].get("errorCode") or "")
                                    if chosen else "")[:64],
                last_error_message=redact_text(
                    str((chosen[2].get("desc") or "") if chosen else ""))[:200],
                op_time=op_time.replace(tzinfo=None) if op_time else None,
                source_file=(chosen[3] if chosen else "")[:255],
                import_batch_id=batch.id))
            created += 1
        else:
            # 已有记录：只有"更晚且可解析"的事件才覆盖（旧失败/Mock 不覆盖新成功）
            newer = ts_utc is not None and (
                rec.last_attempt_at is None or ts_utc > rec.last_attempt_at)
            changed = False
            if newer:
                rec.last_attempt_at = ts_utc
                rec.last_result = last_result
                rec.bucket = bucket
                rec.evidence_level = evidence
                rec.last_error_code = str(
                    (chosen[2].get("errorCode") or ""))[:64]
                rec.last_error_message = redact_text(
                    str(chosen[2].get("desc") or ""))[:200]
                rec.import_batch_id = batch.id
                rec.source_file = (chosen[3])[:255]
                changed = True
            if op_time is not None and rec.op_time is None:
                rec.op_time = op_time.replace(tzinfo=None)   # 只补缺，不覆盖
                changed = True
            if rec.attempts < attempts_visible:
                rec.attempts = attempts_visible
                changed = True
            updated += 1 if changed else 0
            unchanged += 0 if changed else 1
        _ = unresolved

    # 纯 sent.jsonl 观测记录（A3：不丢弃；生产只读代码证明=平台受理→
    # ACCEPTED，evidence/last_result=SENT_LEDGER_ONLY 单列；不映射 CONFIRMED）
    for key, op_time in sent.items():
        if key in receipts:
            continue
        table = key.split("/", 1)[0]
        rec = (db.query(ExternalSubmissionRecord)
               .filter_by(tenant_id=tenant_id, target_code=target_code,
                          source_instance=source_instance,
                          record_key=key).first())
        if rec is None:
            db.add(ExternalSubmissionRecord(
                id=new_uuid(), tenant_id=tenant_id, target_code=target_code,
                source_instance=source_instance,
                record_key=key, business_type=table,
                doc_type_code=DOC_TYPE_CODES.get(table, ""),
                first_seen_at=utcnow(), last_attempt_at=None,
                attempts=1, last_result="SENT_LEDGER_ONLY",
                bucket="ACCEPTED", evidence_level="SENT_LEDGER_ONLY",
                op_time=op_time.replace(tzinfo=None),
                source_file="sent.jsonl", import_batch_id=batch.id))
            created += 1
            stats["sent_only_records"] += 1
        elif rec.op_time is None:
            rec.op_time = op_time.replace(tzinfo=None)   # 只补缺，不覆盖
            updated += 1
        else:
            unchanged += 1
    batch.rows_applied = created
    batch.rows_skipped_duplicate = unchanged
    batch.ended_at = utcnow()
    if stats["lines_conflict_position"]:
        batch.report = {**(batch.report or {}),
                        "conflicts": stats["lines_conflict_position"],
                        "note": "同来源同位置 digest 不一致——保留新证据，"
                                "冲突待核（不覆盖旧证据）"}
    db.flush()
    stats.update({"rows_applied": created, "rows_skipped_duplicate": unchanged,
                  "conflicts": stats["lines_conflict_position"],
                  "batch_id": batch.id,
                  "records_total": len(set(receipts) | set(sent)),
                  "records_created": created, "records_updated": updated,
                  "records_unchanged": unchanged})
    return stats


def _persist_snapshots(db, tenant_id: str, target_code: str,
                       source_instance: str, snapshots: list, batch) -> None:
    """PT-03：快照写遥测/守护快照表（幂等 upsert；白名单键零患者字段）。"""
    from core_db.models import (InfectiousTableTelemetry,
                                InfectiousWatchdogSnapshot)
    from datetime import datetime as _dt
    for snap in snapshots:
        kind = snap.get("_kind")
        if snap.get("_error"):
            continue
        if kind == "watermark.json":
            wm = snap.get("watermark") or {}
            # 滞后计算用 aware UTC（core_db utcnow 为 naive——与 aware CST 相减
            # 会 TypeError；入库列仍按工程惯例存 naive UTC）
            now_aware = datetime.now(timezone.utc)
            now = utcnow()
            for table_key, op_str in wm.items():
                meta = INF_TABLES.get(table_key)
                row = (db.query(InfectiousTableTelemetry)
                       .filter_by(tenant_id=tenant_id, target_code=target_code,
                                  source_instance=source_instance,
                                  table_key=table_key).first())
                # PT-03：水位为源端 CST 时间——按混合口径转 naive UTC 存储
                wm_dt = _parse_ts(op_str, "CST")
                if wm_dt is not None:
                    wm_dt = wm_dt.replace(tzinfo=None)
                lag_h = None
                if wm_dt is not None:
                    lag_h = max(0.0, (now_aware - wm_dt.replace(
                        tzinfo=CST)).total_seconds() / 3600.0)
                if row is None:
                    db.add(InfectiousTableTelemetry(
                        id=new_uuid(), tenant_id=tenant_id,
                        target_code=target_code,
                        source_instance=source_instance,
                        table_key=table_key,
                        doc_type_code=meta["code"] if meta else "",
                        watermark_op_time=wm_dt, watermark_lag_hours=lag_h,
                        updated_at=now))
                else:
                    row.watermark_op_time = wm_dt
                    row.watermark_lag_hours = lag_h
                    if meta:
                        row.doc_type_code = meta["code"]
                    row.updated_at = now
        elif kind == "last-run-real.json":
            db.flush()                      # 同批 watermark 行先落盘再查重
            arr = snap.get("_arr_shifts") or []
            now = utcnow()
            for shift in arr:
                tkey = str(shift.get("table", ""))[:64]
                if not re.fullmatch(r"[a-z_0-9]+", tkey):
                    continue
                meta = INF_TABLES.get(tkey)
                row = (db.query(InfectiousTableTelemetry)
                       .filter_by(tenant_id=tenant_id, target_code=target_code,
                                  source_instance=source_instance,
                                  table_key=tkey).first())
                if row is None:
                    row = InfectiousTableTelemetry(
                        id=new_uuid(), tenant_id=tenant_id,
                        target_code=target_code,
                        source_instance=source_instance, table_key=tkey,
                        doc_type_code=meta["code"] if meta else "",
                        updated_at=now)
                    db.add(row)
                if meta:
                    row.doc_type_code = meta["code"]
                row.last_shift_at = now
                row.shift_rows = int(shift.get("rows", 0) or 0)
                row.shift_sent = int(shift.get("sent", 0) or 0)
                row.shift_parent_wait = int(shift.get("parent_wait", 0) or 0)
                row.shift_skipped_invalid = int(
                    shift.get("skipped_invalid", 0) or 0)
                row.shift_failed = int(shift.get("failed", 0) or 0)
                row.shift_truncated = int(shift.get("truncated", 0) or 0)
                row.shift_error = str(shift.get("error", "") or "")[:500]
                row.updated_at = now
        elif kind == "reconcile":
            db.flush()
            now = utcnow()
            for tkey, v in (snap.get("tables") or {}).items():
                meta = INF_TABLES.get(tkey)
                row = (db.query(InfectiousTableTelemetry)
                       .filter_by(tenant_id=tenant_id, target_code=target_code,
                                  source_instance=source_instance,
                                  table_key=tkey).first())
                if row is None:
                    row = InfectiousTableTelemetry(
                        id=new_uuid(), tenant_id=tenant_id,
                        target_code=target_code,
                        source_instance=source_instance, table_key=tkey,
                        doc_type_code=meta["code"] if meta else "",
                        updated_at=now)
                    db.add(row)
                if meta:
                    row.doc_type_code = meta["code"]
                row.reconcile_day = str(snap.get("day", ""))[:8]
                row.reconcile_repushed = int(v.get("repushed", 0) or 0)
                row.reconcile_parent_wait = int(v.get("parent_wait", 0) or 0)
                row.reconcile_failed = int(v.get("failed", 0) or 0)
                row.updated_at = now
        elif kind == "status.json":
            now = utcnow()
            row = (db.query(InfectiousWatchdogSnapshot)
                   .filter_by(tenant_id=tenant_id, target_code=target_code,
                              source_instance=source_instance).first())
            checks = snap.get("checks") or {}
            if row is None:
                row = InfectiousWatchdogSnapshot(
                    id=new_uuid(), tenant_id=tenant_id,
                    target_code=target_code,
                    source_instance=source_instance, updated_at=now)
                db.add(row)
            row.checked_at = _parse_ts(snap.get("checked_at", ""), "CST")
            if row.checked_at is not None:
                row.checked_at = row.checked_at.replace(tzinfo=None)
            row.host_label = snap.get("host_label", "")
            row.overall_level = snap.get("overall_level", "")
            row.checks_json = checks
            row.relay_reachable = snap.get("relay_reachable")
            row.driver_errors_json = {"count": snap.get("driver_errors", 0)}
            row.updated_at = now
        elif kind == "daily":
            db.flush()
            now = utcnow()
            row = (db.query(InfectiousWatchdogSnapshot)
                   .filter_by(tenant_id=tenant_id, target_code=target_code,
                              source_instance=source_instance).first())
            if row is None:
                row = InfectiousWatchdogSnapshot(
                    id=new_uuid(), tenant_id=tenant_id,
                    target_code=target_code,
                    source_instance=source_instance, updated_at=now)
                db.add(row)
            row.daily_ymd = str(snap.get("ymd", ""))[:8]
            row.daily_text = str(snap.get("text", ""))[:20000]
            row.updated_at = now
        elif kind == "alerts":
            db.flush()
            now = utcnow()
            row = (db.query(InfectiousWatchdogSnapshot)
                   .filter_by(tenant_id=tenant_id, target_code=target_code,
                              source_instance=source_instance).first())
            if row is None:
                row = InfectiousWatchdogSnapshot(
                    id=new_uuid(), tenant_id=tenant_id,
                    target_code=target_code,
                    source_instance=source_instance, updated_at=now)
                db.add(row)
            row.recent_alerts_json = {"ymd": snap.get("ymd", ""),
                                      "items": snap.get("items") or []}
            row.updated_at = now
    db.flush()


def import_directory(db, tenant_id: str, target_code: str, directory: str, *,
                     source_label: str = "", source_instance: str = "",
                     source_tz: str = "", actor: str = "",
                     dry_run: bool = False,
                     receipts_tz: str | None = None,
                     sent_tz: str | None = None) -> dict:
    files = []
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            files.append({"name": name,
                          "content": open(path, "rb").read()})
    if not files:
        raise ValueError(f"目录无文件: {directory}")
    return import_files(db, tenant_id, target_code, files=files,
                        source_label=source_label or directory,
                        source_instance=source_instance,
                        source_tz=source_tz, actor=actor, dry_run=dry_run,
                        receipts_tz=receipts_tz, sent_tz=sent_tz)
