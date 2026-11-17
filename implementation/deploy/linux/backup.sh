#!/usr/bin/env bash
# BLYB 一致性备份（OPS-04 修订版 → FIX2-06 第二轮修订）。
#
# 与旧版的关键差异：
#   1) 静止窗口先于所有快照：停止全部实际写入口——worker、core、storage、
#      exchange（FIX2-06 修订：storage 仍提供上传写 PG/对象、exchange 写交换库，
#      旧版只停 worker/core 的说法不成立）。nginx 拒绝后到达的业务请求不产生
#      新写入；排空在途请求后才依次做 PG dump / 交换快照 / 配置归档 /
#      （可选）对象卷打包——全部落在同一静止窗口内，构成多存储共同静止点。
#   2) 失败恢复：trap 只恢复【原先就在运行】的服务；原本停着的服务不会被
#      擅自启动。静止窗口起止与各存储快照时间写入 backup.json（一致性口径证据）。
#   3) 路径统一从当前 runtime.env 读取（空值 fail-closed），无硬编码；
#      对象归档按 OBJ_DIR 实际自定义路径打包（不再硬编码 objects/）。
#   4) umask 077 在脚本入口设置：含密钥的备份产物从创建一刻起即私有权限
#      （旧版末尾才 chmod，失败残留宽权限文件）。
#
# 用法：backup.sh [--with-objects] [--no-stop]
#   --with-objects  对象卷一并打包（仍在静止窗口内执行）
#   --no-stop       不停服务直接备份（一致性降级为 DB 内部一致；backup.json 如实记录）
set -euo pipefail
umask 077
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"

WITH_OBJECTS=0; NO_STOP=0
for a in "$@"; do
  case "$a" in
    --with-objects) WITH_OBJECTS=1;;
    --no-stop) NO_STOP=1;;
    *) die "未知参数：$a";;
  esac
done

# 实际挂载路径来自 runtime.env（OPS-04：无硬编码；空值 fail-closed）
PG_DIR="$(runtime_env_value "$BLYB_RUNTIME_ENV" BLYB_PGDATA_DIR)"
OBJ_DIR="$(runtime_env_value "$BLYB_RUNTIME_ENV" BLYB_OBJECTS_DIR)"
EX_DIR="$(runtime_env_value "$BLYB_RUNTIME_ENV" BLYB_EXCHANGE_DIR)"
[ -n "$PG_DIR" ] && [ -n "$OBJ_DIR" ] && [ -n "$EX_DIR" ] || \
  die "runtime.env 缺少 BLYB_PGDATA_DIR/BLYB_OBJECTS_DIR/BLYB_EXCHANGE_DIR（空值不猜）"
[ -d "$PG_DIR" ] || die "PG 目录不存在：$PG_DIR"
[ -d "$EX_DIR" ] || die "交换目录不存在：$EX_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BLYB_DATA_ROOT/backups/$TS"
mkdir -p "$DEST"

# ---- 静止窗口管理 ------------------------------------------------------------
# 只恢复原先在运行的服务；原本停止的服务保持停止（OPS-04）
svc_was_running() {
  local cid
  cid="$(compose ps -q "$1" 2>/dev/null || true)"
  [ -n "$cid" ] && [ "$(docker inspect --format '{{.State.Running}}' "$cid" 2>/dev/null || echo false)" = "true" ]
}
# FIX2-06：全部实际写入口（worker/core 调度与 API、storage 上传写 PG/对象、
# exchange 写交换库）；一个都不遗漏，否则“静止”不成立。
QUIESCE_SERVICES="worker core storage exchange"
RUNNING_CORE=0; RUNNING_WORKER=0; RUNNING_STORAGE=0; RUNNING_EXCHANGE=0
svc_was_running core && RUNNING_CORE=1
svc_was_running worker && RUNNING_WORKER=1
svc_was_running storage && RUNNING_STORAGE=1
svc_was_running exchange && RUNNING_EXCHANGE=1
STOPPED_LIST=""
restore_services() {
  local rc=$?
  for s in $STOPPED_LIST; do
    log "失败恢复：重新启动 $s"
    compose start "$s" >/dev/null 2>&1 || log "警告：$s 恢复启动失败，需人工检查"
  done
  exit $rc
}
DRAIN_SECONDS="${BLYB_BACKUP_DRAIN_SECONDS:-5}"
QUIESCE_MODE="none"
if [ "$NO_STOP" != "1" ]; then
  # EXIT trap 兜底（bash 的 ERR trap 不会因函数整体失败触发）：只要静止窗口
  # 仍开着（STOPPED_LIST 非空）就恢复原先在运行的服务；成功路径在窗口关闭时
  # 清空 STOPPED_LIST，使本 trap 空转。
  trap restore_services EXIT INT TERM
  QUIESCE_MODE="worker+core+storage+exchange"
  STILL_START="$(date -u +%FT%TZ)"
  log "静止窗口开始（$STILL_START）：停止 worker、core、storage、exchange（原状态 core=$RUNNING_CORE worker=$RUNNING_WORKER storage=$RUNNING_STORAGE exchange=$RUNNING_EXCHANGE）"
  for s in $QUIESCE_SERVICES; do
    case "$s" in
      worker)   [ "$RUNNING_WORKER" = "1" ] && { compose stop worker >/dev/null; STOPPED_LIST="worker $STOPPED_LIST"; };;
      core)     [ "$RUNNING_CORE" = "1" ] && { compose stop core >/dev/null; STOPPED_LIST="core $STOPPED_LIST"; };;
      storage)  [ "$RUNNING_STORAGE" = "1" ] && { compose stop storage >/dev/null; STOPPED_LIST="storage $STOPPED_LIST"; };;
      exchange) [ "$RUNNING_EXCHANGE" = "1" ] && { compose stop exchange >/dev/null; STOPPED_LIST="exchange $STOPPED_LIST"; };;
    esac
  done
  log "排空在途请求 ${DRAIN_SECONDS}s"
  sleep "$DRAIN_SECONDS"
else
  log "警告：--no-stop 降级模式（仅 DB 内部一致；对象/交换与库可能不一致），backup.json 将如实记录"
  STILL_START="$(date -u +%FT%TZ)"
fi

# ---- 快照（全部在静止窗口内）-------------------------------------------------
# 1) PG：pg_dump 自定义格式
compose exec -T db pg_dump -U blyb -Fc blyb > "$DEST/core.pg_dump"
log "core.pg_dump 完成（$(du -h "$DEST/core.pg_dump" | cut -f1)）"

# 2) 交换 SQLite：backup API 一致性快照（写同卷再移动；静止窗口内 storage/
#    exchange 已停，快照期间无并发写）
compose run --rm --no-deps -T exchange python -c \
  "import sqlite3;s=sqlite3.connect('/data/exchange/exchange.sqlite3');d=sqlite3.connect('/data/exchange/.backup-tmp.sqlite3');s.backup(d);d.close();s.close()"
mv "$EX_DIR/.backup-tmp.sqlite3" "$DEST/exchange.sqlite3"
log "exchange.sqlite3 快照完成"

# 3) 配置（含 runtime.env 密钥）：tar（umask 077 已保证产物私有权限）
tar --force-local -C "$(dirname "$BLYB_ETC")" -czf "$DEST/etc-blyb.tar.gz" "$(basename "$BLYB_ETC")"
chmod 600 "$DEST/etc-blyb.tar.gz"
log "etc-blyb.tar.gz 完成（含密钥，已限权）"

# 4) 对象卷（可选；仍在静止窗口内）——按 OBJ_DIR 实际自定义路径打包
#    （FIX2-06：不再硬编码 DATA_ROOT/objects）
if [ "$WITH_OBJECTS" = "1" ]; then
  if [ "$NO_STOP" = "1" ]; then
    die "--with-objects 不能与 --no-stop 同用（对象与库将必然不一致）"
  fi
  [ -d "$OBJ_DIR" ] || die "OBJ_DIR 不存在：$OBJ_DIR"
  tar --force-local -C "$(dirname "$OBJ_DIR")" -czf "$DEST/objects.tar.gz" "$(basename "$OBJ_DIR")"
  log "objects.tar.gz 完成（$(du -h "$DEST/objects.tar.gz" | cut -f1)，源=$OBJ_DIR）"
fi

STILL_END="$(date -u +%FT%TZ)"
# 静止窗口正常关闭：恢复原运行服务并解除 EXIT 兜底（改为空 trap）

# ---- 结束静止窗口：恢复原运行服务 -------------------------------------------
for s in $STOPPED_LIST; do
  log "恢复服务：$s"
  compose start "$s" >/dev/null || log "警告：$s 启动失败，需人工检查"
done
trap - EXIT INT TERM

# ---- 清单与哈希 --------------------------------------------------------------
FILES="core.pg_dump exchange.sqlite3 etc-blyb.tar.gz"
[ "$WITH_OBJECTS" = "1" ] && FILES="$FILES objects.tar.gz"
( cd "$DEST" && sha256sum $FILES > SHA256SUMS )
jsonesc() { printf '%s' "$1" | sed 's/\\/\\\\/g'; }
printf '{"ts":"%s","with_objects":%s,"quiesce_mode":"%s","still_window":{"start":"%s","end":"%s","drain_seconds":%s},"stopped_services":"%s","core_was_running":%s,"worker_was_running":%s,"storage_was_running":%s,"exchange_was_running":%s,"paths":{"pg":"%s","objects":"%s","exchange":"%s"},"host":"%s"}\n' \
  "$TS" "$WITH_OBJECTS" "$QUIESCE_MODE" "$STILL_START" "$STILL_END" "$DRAIN_SECONDS" \
  "$(printf '%s' "$STOPPED_LIST" | sed 's/ $//')" "$RUNNING_CORE" "$RUNNING_WORKER" \
  "$RUNNING_STORAGE" "$RUNNING_EXCHANGE" \
  "$(jsonesc "$PG_DIR")" "$(jsonesc "$OBJ_DIR")" "$(jsonesc "$EX_DIR")" "$(hostname)" > "$DEST/backup.json"
chmod -R go-rwx "$DEST"
log "备份完成：$DEST（静止窗口 ${STILL_START} → ${STILL_END}）"
