"""传染病生产状态只读流式增量同步器（PT-03）。

数据源：只读挂载目录（环境变量）
- BLYB_INF_PUSHER_STATE_DIR   推送器 state/（receipts-*.jsonl / sent.jsonl /
  watermark.json / last-run-real.json / last-run-reconcile.json /
  reconcile-YYYYMMDD.json / pending-wait.json）
- BLYB_INF_WATCHDOG_STATE_DIR 守护 state/（status.json / daily-YYYYMMDD.txt /
  alerts-YYYYMMDD.jsonl）

红线（任务书步骤 2）：对源目录 **100% O_RDONLY 零写副作用**——绝不创建锁文件/
临时文件/不修改任何字节；文件一律 `open(path, "rb")` 只读打开。

流式增量（external_file_cursor 游标）：
- byte_offset 只推进到完整 `\n` 边界（推送器写一半的尾行下轮再收）；
- head_sha256 = 文件头 4096 字节摘要——变化或 file_size < byte_offset 即判定
  轮转重建，从 0 重读；
- 每次同步每文件最多 max_rows 行 / max_bytes 增量字节（内存恒定 <30MB：
  行字节缓冲 + 解析副本受双预算约束），面对 855 万行 1.2GB 回执文件稳健增量；
- 增量行经 import_files 同一管线入库（幂等行账 offset_bases=全局行号基线，
  跨批次零重复行账；记录级按事件归并幂等）。

未配置目录时返回 `{"configured": false, ...}` 温和提示，绝不抛 500。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta

from core_db.engine import utcnow
from plugins.external_ledger.importers.inf_pusher import (
    INF_TABLES, import_files, parse_snapshot)

TARGET = "inf_cdc_frontend"
HEAD_PROBE_BYTES = 4096
# 双预算：单文件单批最多 50000 行且最多 8MB 增量字节
# （行缓冲+解析副本 ≤3 倍字节预算 ≈ 24MB < 30MB 内存上限）
DEFAULT_MAX_ROWS = 50000
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
_RECEIPTS_RE = re.compile(r"^receipts-(\d{8})\.jsonl$")
_PUSHER_SNAPSHOTS = ("watermark.json", "last-run-real.json",
                     "last-run-reconcile.json", "pending-wait.json")
_RECONCILE_RE = re.compile(r"^reconcile-\d{8}\.json$")
_WATCHDOG_SNAPSHOTS = ("status.json",)
_ALERTS_RE = re.compile(r"^alerts-\d{8}\.jsonl$")
_DAILY_RE = re.compile(r"^daily-\d{8}\.txt$")


def _config_dirs() -> tuple[str, str]:
    return (os.environ.get("BLYB_INF_PUSHER_STATE_DIR", "") or "",
            os.environ.get("BLYB_INF_WATCHDOG_STATE_DIR", "") or "")


def _head_sha256(path: str) -> str:
    """文件头 4096 字节摘要（轮转检测；O_RDONLY 只读）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read(HEAD_PROBE_BYTES))
    return h.hexdigest()


def sync_status(db, tenant_id: str, *, source_instance: str = "live",
                target_code: str = TARGET, max_rows: int = DEFAULT_MAX_ROWS,
                max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    """一次只读增量同步。返回统计（不抛目录类错误——未配置温和提示）。"""
    pusher_dir, watchdog_dir = _config_dirs()
    if not pusher_dir and not watchdog_dir:
        return {"configured": False,
                "reason": "未配置实时状态挂载目录，当前展示导入副本数据"}
    out = {"configured": True, "files_scanned": 0, "rows_read": 0,
           "records_created": 0, "records_updated": 0, "sent_only_records": 0,
           "snapshots_parsed": 0, "cursors": [], "errors": []}
    if pusher_dir:
        _sync_pusher_dir(db, tenant_id, target_code, source_instance,
                         pusher_dir, out, max_rows, max_bytes)
    if watchdog_dir:
        _sync_watchdog_dir(db, tenant_id, target_code, source_instance,
                           watchdog_dir, out)
    return out


def _load_cursor(db, tenant_id, target_code, source_instance, file_name):
    from core_db.models import ExternalFileCursor
    return (db.query(ExternalFileCursor)
            .filter_by(tenant_id=tenant_id, target_code=target_code,
                       source_instance=source_instance,
                       file_name=file_name).first())


def _sync_pusher_dir(db, tenant_id, target_code, source_instance,
                     directory, out, max_rows, max_bytes):
    from core_db.models import ExternalFileCursor
    try:
        names = sorted(os.listdir(directory))
    except OSError as e:
        out["errors"].append({"dir": directory, "error": str(e)[:200]})
        return
    now = utcnow()
    cutoff = (now - timedelta(days=3)).strftime("%Y%m%d")
    # 1) 快照类（小文件整体解析，幂等 upsert 遥测/快照）
    for name in names:
        if name in _PUSHER_SNAPSHOTS or _RECONCILE_RE.match(name):
            path = os.path.join(directory, name)
            try:
                with open(path, "rb") as fh:        # O_RDONLY 只读
                    content = fh.read(_SNAPSHOT_MAX)
                stats = import_files(
                    db, tenant_id, target_code,
                    files=[{"name": name, "content": content}],
                    source_label="live-sync", source_instance=source_instance,
                    source_tz="AUTO_MIXED", actor="monitor_sync")
                out["snapshots_parsed"] += stats["snapshots_parsed"]
                out["files_scanned"] += 1
            except OSError as e:
                out["errors"].append({"file": name, "error": str(e)[:200]})
    # 2) 行类（receipts 近 3 天 + sent.jsonl）流式尾读
    line_files = [n for n in names
                  if n == "sent.jsonl"
                  or (_RECEIPTS_RE.match(n) and _RECEIPTS_RE.match(n).group(1) >= cutoff)]
    for name in line_files:
        path = os.path.join(directory, name)
        try:
            _stream_file(db, tenant_id, target_code, source_instance,
                         directory, name, path, out, max_rows, max_bytes)
        except OSError as e:
            out["errors"].append({"file": name, "error": str(e)[:200]})
    db.flush()


_SNAPSHOT_MAX = 8 * 1024 * 1024


def _stream_file(db, tenant_id, target_code, source_instance, directory,
                 name, path, out, max_rows, max_bytes):
    """单文件字节偏移流式尾读（O_RDONLY；只推进完整行；轮转从 0 重读）。"""
    from core_db.models import ExternalFileCursor
    cur = _load_cursor(db, tenant_id, target_code, source_instance, name)
    try:
        size = os.path.getsize(path)
        head = _head_sha256(path)
    except OSError as e:
        out["errors"].append({"file": name, "error": str(e)[:200]})
        return
    byte_offset = 0
    abs_line = 0
    if cur is None:
        cur = ExternalFileCursor(
            id=cur_id(), tenant_id=tenant_id, target_code=target_code,
            source_instance=source_instance, file_name=name, updated_at=utcnow())
        db.add(cur)
    else:
        byte_offset = int(cur.byte_offset or 0)
        abs_line = int(cur.abs_line_start or 0)
        # PT-03 轮转判据：变小/截断/越界 → 轮转；同尺寸但头摘要变化 → 同为
        # 重建（等长重建场景）；追加场景 size 必然变大 → 不误判
        # （生产 receipts 按日期命名轮转，cursor 按文件名天然隔离）
        prev_size = int(cur.file_size or 0)
        rotate = (size < byte_offset or size < prev_size
                  or (size == prev_size and size > 0
                      and head != str(cur.head_sha256 or "")))
        if rotate:
            byte_offset = 0
            abs_line = 0
    if byte_offset > size:
        byte_offset = 0
        abs_line = 0
    out["files_scanned"] += 1
    if byte_offset >= size:
        cur.file_size = size
        cur.byte_offset = byte_offset
        cur.abs_line_start = abs_line
        cur.head_sha256 = head
        cur.updated_at = utcnow()
        return
    out["cursors"].append({"file": name, "from": byte_offset})
    # 流式尾读：tail=跨 chunk 半行缓冲（不消费）；只消费完整 \n 结尾行；
    # 双预算（行数/字节）先到即停；超预算的完整行整体放回（下轮再收）
    rows = []
    consumed = 0         # 本次消费的完整行字节（含行尾 \n）
    n_rows = 0
    tail = b""
    eof = False
    with open(path, "rb") as fh:        # O_RDONLY 只读，绝不写源目录
        fh.seek(byte_offset)
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                eof = True
                break
            buf = tail + chunk
            cut = buf.rfind(b"\n")
            if cut < 0:
                # 全块无完整行（写一半的尾行/超长行）——留在半行缓冲；
                # 超 8MB 半行放弃（防御异常文件，真实回执行远小于此）
                if len(buf) <= 8 * 1024 * 1024:
                    tail = buf
                    continue
                break
            tail = buf[cut + 1:]
            complete = buf[:cut + 1]
            # 字节预算截断：从 complete 里只吃预算内最后一条完整行
            if consumed + len(complete) > max_bytes:
                budget = max_bytes - consumed
                c2 = complete.rfind(b"\n", 0, budget + 1)
                if c2 <= 0:
                    tail = buf            # 一个完整行都放不下：整块退回
                    break
                tail = complete[c2 + 1:] + tail
                complete = complete[:c2 + 1]
            consumed += len(complete)
            for line in complete.splitlines():
                if line.strip():
                    rows.append(line)
                    n_rows += 1
            if n_rows >= max_rows or consumed >= max_bytes:
                break
    _ = eof
    if not rows:
        cur.file_size = size
        cur.byte_offset = byte_offset      # 无完整行消费：偏移不动
        cur.abs_line_start = abs_line
        cur.head_sha256 = head
        cur.updated_at = utcnow()
        return
    content = b"\n".join(rows) + b"\n"
    stats = import_files(
        db, tenant_id, target_code,
        files=[{"name": name, "content": content}],
        source_label="live-sync", source_instance=source_instance,
        source_tz="AUTO_MIXED", actor="monitor_sync",
        offset_bases={name: abs_line})
    out["rows_read"] += stats["rows_read"]
    out["records_created"] += stats["records_created"]
    out["records_updated"] += stats["records_updated"]
    out["sent_only_records"] += stats["sent_only_records"]
    cur.file_size = size
    cur.byte_offset = byte_offset + consumed
    cur.abs_line_start = abs_line + n_rows
    cur.head_sha256 = head
    cur.updated_at = utcnow()


def cur_id():
    from contracts import new_uuid
    return new_uuid()


def _sync_watchdog_dir(db, tenant_id, target_code, source_instance,
                       directory, out):
    try:
        names = sorted(os.listdir(directory))
    except OSError as e:
        out["errors"].append({"dir": directory, "error": str(e)[:200]})
        return
    for name in names:
        if name in _WATCHDOG_SNAPSHOTS or _ALERTS_RE.match(name) \
                or _DAILY_RE.match(name):
            path = os.path.join(directory, name)
            try:
                with open(path, "rb") as fh:        # O_RDONLY 只读
                    content = fh.read(_SNAPSHOT_MAX)
                stats = import_files(
                    db, tenant_id, target_code,
                    files=[{"name": name, "content": content}],
                    source_label="live-sync", source_instance=source_instance,
                    source_tz="AUTO_MIXED", actor="monitor_sync")
                out["snapshots_parsed"] += stats["snapshots_parsed"]
                out["files_scanned"] += 1
            except OSError as e:
                out["errors"].append({"file": name, "error": str(e)[:200]})
    db.flush()


def sync_configured() -> bool:
    return bool(_config_dirs()[0] or _config_dirs()[1])
