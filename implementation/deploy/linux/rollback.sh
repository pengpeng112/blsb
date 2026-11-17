#!/usr/bin/env bash
# BLYB 回滚（execution-plan §9：优先切回旧发行；数据库不自动降级）。
# 用法：./rollback.sh <old-release-id>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"

OLD_ID="${1:-}"
[ -n "$OLD_ID" ] || die "用法：rollback.sh <old-release-id>（见 $BLYB_RELEASES）"
OLD_DIR="$BLYB_RELEASES/$OLD_ID"
[ -d "$OLD_DIR" ] || die "不存在旧发行：$OLD_DIR"

log "回滚前提示：若已应用不兼容迁移，切回旧应用可能启动失败；此时应按 runbook 用备份恢复（restore.sh），不要反复横跳"
read -r -p "确认回滚到 $OLD_ID（输入 YES）：" ans
[ "$ans" = "YES" ] || die "未确认，中止"

verify_release "$OLD_DIR"
ln -sfn "$OLD_DIR" "$BLYB_CURRENT"
write_runtime_env "$OLD_ID"   # 只回写镜像 tag，不动密钥/数据
load_images "$OLD_DIR"
compose up -d db
wait_healthy db 180
compose run --rm migrate      # 旧代码迁移链（幂等；不执行降级）
compose up -d
wait_healthy core 180 && wait_healthy storage 120 && wait_healthy exchange 120 \
  && wait_healthy worker 120 && wait_healthy nginx 120
log "已回滚到 $OLD_ID；核对远端可能已受理单据与本地游标后再恢复发送"
