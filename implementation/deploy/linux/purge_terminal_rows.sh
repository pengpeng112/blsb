#!/usr/bin/env bash
# BLYB 终态行留存清理（DTR-BATCH-FIX-20261108 Fix5）。
#
# 背景：job/outbox_event 只增不清——2026-11-05 生产实测 job 69,797 行、
# outbox_event 61,730 行，worker 每 0.2s 的领取/投递查询退化为顺序扫描
# （job seq_scan 127 万次），持续烧 CPU 并拖慢采集。清理后 planner 回到
# ix_job_poll / ix_outbox_unpublished 索引。
#
# 清理口径（保守）：
#   job          state='SUCCEEDED'      AND finished_at  < now()-N days
#   outbox_event published_at IS NOT NULL AND published_at < now()-N days
# 不动：任何非终态 job、未发布 outbox、quality_issue（业务质量历史保留）。
#
# 用法：
#   ./purge_terminal_rows.sh [--days 14] [--apply]     # 默认 dry-run（只 COUNT）
#   ./purge_terminal_rows.sh --apply                   # 真删（分批 ≤5000 行/事务）
# systemd：blyb-purge-terminal.timer（每日 12:30；默认安装不启用，用户确认后 enable）。
set -euo pipefail

DAYS=14
APPLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --days) DAYS="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    *) echo "未知参数: $1（支持 --days N / --apply）" >&2; exit 2 ;;
  esac
done
[ "$DAYS" -ge 1 ] || { echo "--days 必须 ≥1" >&2; exit 2; }

PSQL=(docker exec blyb-db-1 psql -U blyb -d blyb -v ON_ERROR_STOP=1 -qAt)

cnt() { "${PSQL[@]}" -c "$1"; }

purge_table() {
  local table=$1 cond=$2
  local total
  total=$(cnt "SELECT count(*) FROM ${table} WHERE ${cond}")
  echo "[$(date '+%F %T')] ${table}: 待清理（>${DAYS} 天）= ${total}"
  [ "$APPLY" = "1" ] || return 0
  local deleted=0 batch
  while :; do
    batch=$(cnt "WITH victims AS (
        SELECT ctid FROM ${table} WHERE ${cond} LIMIT 5000)
      DELETE FROM ${table} WHERE ctid IN (SELECT ctid FROM victims) RETURNING 1" \
      | wc -l)
    deleted=$((deleted + batch))
    [ "$batch" -lt 5000 ] && break
    sleep 1   # 批间让步，避免长时间压住 autovacuum/业务查询
  done
  echo "[$(date '+%F %T')] ${table}: 已删除 ${deleted} 行"
}

JOB_COND="state='SUCCEEDED' AND finished_at < now() - interval '${DAYS} days'"
OUTBOX_COND="published_at IS NOT NULL AND published_at < now() - interval '${DAYS} days'"

if [ "$APPLY" = "1" ]; then
  echo "== APPLY 模式：分批删除（每批 5000，批间 1s）=="
else
  echo "== DRY-RUN 模式（默认；只计数不删除。真删加 --apply）=="
fi
purge_table job "$JOB_COND"
purge_table outbox_event "$OUTBOX_COND"

if [ "$APPLY" = "1" ]; then
  echo "== ANALYZE 刷新统计（恢复索引选择）=="
  "${PSQL[@]}" -c "ANALYZE job; ANALYZE outbox_event; ANALYZE quality_issue;"
  echo "== 完成 =="
else
  echo "== DRY-RUN 结束（0 行被删除）=="
fi
