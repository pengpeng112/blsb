#!/usr/bin/env bash
# BLYB 恢复（OPS-02/OPS-03/OPS-07 修订版）。
#
# 安全模型：
#   1) 校验阶段只读：哈希、归档安全（穿越/链接）、备份配置解析、路径映射与
#      空目标检查、镜像兼容——全部通过后才进入执行阶段。
#   2) 默认【隔离演练模式】：独立 compose 项目名 + 独立数据根 + 独立配置根，
#      任何目标都不允许等于生产路径（/etc/blyb、当前 runtime.env 的数据目录），
#      除非显式 --allow-production 且 BLYB_RESTORE_CONFIRM_PRODUCTION=YES。
#   3) 执行阶段统一走 compose_project（显式项目/env/发行目录），不依赖 cwd。
#   4) 不运行 down -v、不 rm -rf、不覆盖非空目标。
#
# 用法：
#   restore.sh <backup-dir> [--project NAME] [--data-root DIR] [--etc-root DIR]
#              [--release DIR] [--validate-only] [--allow-production]
# 环境变量：
#   BLYB_RESTORE_CONFIRM_PRODUCTION=YES  配合 --allow-production 才会写生产路径
#   BLYB_RESTORE_DRAIN_SECONDS=N         恢复前等待在途请求排空（默认5）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"

BACKUP_DIR="${1:-}"
[ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ] || die "用法：restore.sh <backup-dir> [--project|--data-root|--etc-root|--release|--validate-only|--allow-production]"
shift || true

PROJECT="blyb-restore-drill"
DATA_ROOT=""       # 空=默认隔离目录（backup 同级 restore-drill-<ts>），绝不默认生产根
ETC_ROOT=""        # 空=数据根下 etc-blyb
RELEASE_DIR=""     # 空=当前 current 发行目录（只用其 compose 文件与镜像）
VALIDATE_ONLY=0
ALLOW_PRODUCTION=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="${2:-}"; shift 2;;
    --data-root) DATA_ROOT="${2:-}"; shift 2;;
    --etc-root) ETC_ROOT="${2:-}"; shift 2;;
    --release) RELEASE_DIR="${2:-}"; shift 2;;
    --validate-only) VALIDATE_ONLY=1; shift;;
    --allow-production) ALLOW_PRODUCTION=1; shift;;
    *) die "未知参数：$1";;
  esac
done
[ -n "$PROJECT" ] || die "--project 不能为空"
# FIX2-06 修订：演练项目名不得等于生产 compose 项目名（blyb）——否则 down/up
# 会直接作用到现网容器；确需恢复生产必须显式双确认。
if [ "$PROJECT" = "blyb" ] && [ "$ALLOW_PRODUCTION" != "1" ]; then
  die "项目名 blyb 是生产 compose 项目：演练必须换独立 --project；确需生产恢复须 --allow-production 且 BLYB_RESTORE_CONFIRM_PRODUCTION=YES"
fi

# ================================================================ 校验阶段（只读）
log "[校验] 备份目录：$BACKUP_DIR"
[ -f "$BACKUP_DIR/SHA256SUMS" ] || die "备份缺少 SHA256SUMS（不是本工具链产生的完整备份）"
( cd "$BACKUP_DIR" && sha256sum --quiet -c SHA256SUMS ) || die "备份哈希校验失败"
[ -f "$BACKUP_DIR/core.pg_dump" ] || die "备份缺少 core.pg_dump"
[ -f "$BACKUP_DIR/etc-blyb.tar.gz" ] || die "备份缺少 etc-blyb.tar.gz（无法重建配置）"

log "[校验] 归档成员安全（路径穿越/链接拒绝）"
check_archive "$BACKUP_DIR/etc-blyb.tar.gz" >/dev/null
[ -f "$BACKUP_DIR/objects.tar.gz" ] && check_archive "$BACKUP_DIR/objects.tar.gz" >/dev/null

# 备份内配置解到临时目录解析（不改任何现网/目标路径）
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/blyb-restore-check.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
tar --force-local -C "$STAGE" -xzf "$BACKUP_DIR/etc-blyb.tar.gz"
BK_ENV="$STAGE/blyb/runtime.env"
[ -s "$BK_ENV" ] || BK_ENV="$STAGE/etc/blyb/runtime.env"   # 兼容不带顶层目录名的打包
[ -s "$BK_ENV" ] || die "备份配置中找不到 runtime.env（结构异常，中止）"
chmod 600 "$BK_ENV"

bk_pg_dir="$(runtime_env_value "$BK_ENV" BLYB_PGDATA_DIR)"
bk_obj_dir="$(runtime_env_value "$BK_ENV" BLYB_OBJECTS_DIR)"
bk_ex_dir="$(runtime_env_value "$BK_ENV" BLYB_EXCHANGE_DIR)"
bk_backend_img="$(runtime_env_value "$BK_ENV" BLYB_BACKEND_IMAGE)"
[ -n "$bk_pg_dir" ] && [ -n "$bk_obj_dir" ] && [ -n "$bk_ex_dir" ] || \
  die "备份 runtime.env 缺少 BLYB_PGDATA_DIR/BLYB_OBJECTS_DIR/BLYB_EXCHANGE_DIR（空值不猜，中止）"

# 目标路径映射（隔离演练：统一映射到 DATA_ROOT 下固定子目录；备份原路径仅记录）
if [ -z "$DATA_ROOT" ]; then
  DATA_ROOT="$BLYB_DATA_ROOT/restore-drill-$(date -u +%Y%m%dT%H%M%SZ)"
fi
[ -n "$ETC_ROOT" ] || ETC_ROOT="$DATA_ROOT/etc-blyb"
[ -n "$RELEASE_DIR" ] || RELEASE_DIR="$(readlink -f "$BLYB_CURRENT" 2>/dev/null || true)"
[ -f "$RELEASE_DIR/implementation/$BLYB_COMPOSE_FILE" ] || \
  die "发行目录无 compose 文件：$RELEASE_DIR（用 --release 指定）"

TGT_PG="$DATA_ROOT/postgres"; TGT_OBJ="$DATA_ROOT/objects"; TGT_EX="$DATA_ROOT/exchange"

# 生产路径保护（FIX2-06 修订：相等/祖先/子孙全覆盖，从当前 runtime.env 读取
# 实际自定义生产目录，不再只比固定清单；除非显式双确认）
prod_paths="$BLYB_ETC $BLYB_DATA_ROOT"
for k in BLYB_PGDATA_DIR BLYB_OBJECTS_DIR BLYB_EXCHANGE_DIR BLYB_TLS_CERT_DIR; do
  v="$(runtime_env_value "$BLYB_RUNTIME_ENV" "$k")"
  [ -n "$v" ] && prod_paths="$prod_paths $v"
done
path_overlaps_prod() {
  # 目标与任一生产路径 相等 / 是其祖先 / 是其子孙 → 判重叠
  local t="$1" p rp rt
  for p in $prod_paths; do
    [ -n "$p" ] || continue
    rp="$(realpath -m "$p" 2>/dev/null)" || continue
    rt="$(realpath -m "$t" 2>/dev/null)" || continue
    # case patterns never undergo filesystem glob expansion. The former
    # [ "$rt" = "$rp"/* ] expanded existing directories into multiple test
    # arguments, errored and could silently miss a production overlap.
    case "$rt/" in "$rp/"*) return 0;; esac
    case "$rp/" in "$rt/"*) return 0;; esac
  done
  return 1
}
prod_hit=""
for t in "$ETC_ROOT" "$TGT_PG" "$TGT_OBJ" "$TGT_EX" "$DATA_ROOT"; do
  if path_overlaps_prod "$t"; then
    prod_hit="$prod_hit $t"
  fi
done
if [ -n "$prod_hit" ]; then
  if [ "$ALLOW_PRODUCTION" = "1" ] && [ "${BLYB_RESTORE_CONFIRM_PRODUCTION:-}" = "YES" ]; then
    log "警告：--allow-production 已确认，目标含生产重叠路径：$prod_hit（记录在案继续）"
  else
    die "目标路径与生产重叠（相等/祖先/子孙：$prod_hit）：恢复演练必须使用独立 --data-root/--etc-root；确需恢复生产须显式 --allow-production 且 BLYB_RESTORE_CONFIRM_PRODUCTION=YES"
  fi
fi

# 路径包含与符号链接检查：三个数据目录与配置根必须落在 DATA_ROOT 内
for t in "$TGT_PG" "$TGT_OBJ" "$TGT_EX" "$ETC_ROOT"; do
  path_under "$DATA_ROOT" "$t" || die "目标路径越出数据根（$t 不在 $DATA_ROOT 内，疑似符号链接绕过，中止）"
done

# 空目标检查（非空即拒绝；不猜、不清空）
for d in "$TGT_PG" "$TGT_OBJ" "$TGT_EX" "$ETC_ROOT"; do
  dir_empty "$d" || die "$d 非空：restore 仅支持空目标；非空恢复按 runbook 人工处理"
done

# 镜像兼容：备份镜像 tag 本地是否存在（缺失仅告警，可用 BLYB_RESTORE_SKIP_IMAGE_CHECK=1 跳过）
IMG_NOTE=""
if [ "${BLYB_RESTORE_SKIP_IMAGE_CHECK:-0}" != "1" ] && command -v docker >/dev/null 2>&1; then
  for img in "$bk_backend_img" "$(runtime_env_value "$BK_ENV" BLYB_NGINX_IMAGE)" "$(runtime_env_value "$BK_ENV" BLYB_PG_IMAGE)"; do
    [ -n "$img" ] || continue
    if ! docker image inspect "$img" >/dev/null 2>&1; then
      IMG_NOTE="$IMG_NOTE 缺镜像:$img"
    fi
  done
  [ -z "$IMG_NOTE" ] || log "警告：镜像兼容${IMG_NOTE}（先 docker load 对应发行包，或确认回退方案后 BLYB_RESTORE_SKIP_IMAGE_CHECK=1）"
fi

log "[校验通过] 恢复计划："
log "  项目=$PROJECT  发行=$RELEASE_DIR"
log "  PG    $bk_pg_dir → $TGT_PG"
log "  对象  $bk_obj_dir → $TGT_OBJ"
log "  交换  $bk_ex_dir → $TGT_EX"
log "  配置  → $ETC_ROOT（生产 /etc/blyb 不动）"
[ "$VALIDATE_ONLY" = "1" ] && { log "--validate-only：校验完成，未改动业务/目标数据（如实记录：过程在 \$TMPDIR 创建并已清理的临时目录用于解包读取备份内配置，不写任何生产/目标路径）"; exit 0; }

# ================================================================ 执行阶段
log "[执行] 建目录并落位配置（不触碰 /etc）"
mkdir -p "$TGT_PG" "$TGT_EX" "$ETC_ROOT"
rm -rf "$STAGE"; trap - EXIT
tar --force-local -C "$ETC_ROOT" -xzf "$BACKUP_DIR/etc-blyb.tar.gz"
# 归档可能带 blyb/ 顶层目录：拍平到 ETC_ROOT（保持 certs/runtime.env 相对结构）
if [ -d "$ETC_ROOT/blyb" ] && [ ! -f "$ETC_ROOT/runtime.env" ]; then
  ( shopt -s dotglob; mv "$ETC_ROOT/blyb"/* "$ETC_ROOT/" )
  rmdir "$ETC_ROOT/blyb"
fi
chmod 600 "$ETC_ROOT/runtime.env"
RESTORE_ENV="$ETC_ROOT/runtime.env"
# 数据目录映射写入恢复用 env（镜像 tag 沿用备份时刻；现发行不同时人工核对）
sed -i -E \
  -e "s#^BLYB_PGDATA_DIR=.*#BLYB_PGDATA_DIR=${TGT_PG}#" \
  -e "s#^BLYB_OBJECTS_DIR=.*#BLYB_OBJECTS_DIR=${TGT_OBJ}#" \
  -e "s#^BLYB_EXCHANGE_DIR=.*#BLYB_EXCHANGE_DIR=${TGT_EX}#" \
  -e "s#^BLYB_TLS_CERT_DIR=.*#BLYB_TLS_CERT_DIR=${ETC_ROOT}/certs#" "$RESTORE_ENV"
grep -q '^BLYB_TLS_CERT_DIR=' "$RESTORE_ENV" || \
  printf 'BLYB_TLS_CERT_DIR=%s/certs\n' "$ETC_ROOT" >> "$RESTORE_ENV"
# 演练端口隔离：非生产恢复时 nginx 宿主端口改绑本地高位端口，避免与生产 443 冲突
if [ "$ALLOW_PRODUCTION" != "1" ]; then
  DRILL_PORT="${BLYB_RESTORE_DRILL_PORT:-18443}"
  sed -i -E -e "s#^BLYB_HTTPS_BIND=.*#BLYB_HTTPS_BIND=127.0.0.1#" \
            -e "s#^BLYB_HTTPS_PORT=.*#BLYB_HTTPS_PORT=${DRILL_PORT}#" "$RESTORE_ENV"
  grep -q '^BLYB_HTTPS_PORT=' "$RESTORE_ENV" || printf 'BLYB_HTTPS_PORT=%s\n' "$DRILL_PORT" >> "$RESTORE_ENV"
  grep -q '^BLYB_HTTPS_BIND=' "$RESTORE_ENV" || printf 'BLYB_HTTPS_BIND=127.0.0.1\n' >> "$RESTORE_ENV"
fi

log "[执行] 停演练项目（如残留；不影响生产项目 blyb）"
compose_project "$PROJECT" "$RESTORE_ENV" "$RELEASE_DIR" down >/dev/null 2>&1 || true

if [ -f "$BACKUP_DIR/exchange.sqlite3" ]; then
  install -m 0640 -o "$BLYB_APP_UID" -g "$BLYB_APP_GID" \
    "$BACKUP_DIR/exchange.sqlite3" "$TGT_EX/exchange.sqlite3" 2>/dev/null || \
    install -m 0640 "$BACKUP_DIR/exchange.sqlite3" "$TGT_EX/exchange.sqlite3"
  log "exchange.sqlite3 已落位"
fi
if [ -f "$BACKUP_DIR/objects.tar.gz" ]; then
  tar --force-local -C "$DATA_ROOT" -xzf "$BACKUP_DIR/objects.tar.gz"
  chown -R "$BLYB_APP_UID:$BLYB_APP_GID" "$TGT_OBJ" 2>/dev/null || log "警告：无法 chown 对象目录（非root？容器内属主须为 ${BLYB_APP_UID}）"
  log "objects 已落位"
fi

log "[执行] PG：空 PGDATA 首启 + pg_restore（独立项目）"
compose_project "$PROJECT" "$RESTORE_ENV" "$RELEASE_DIR" up -d db >/dev/null
# 复用 wait_healthy 语义但走独立项目
cid="$(compose_project "$PROJECT" "$RESTORE_ENV" "$RELEASE_DIR" ps -q db)"
[ -n "$cid" ] || die "演练项目 db 无容器"
deadline=$(( $(date +%s) + 180 ))
until [ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$cid" 2>/dev/null || echo gone)" = "healthy" ]; do
  [ "$(date +%s)" -ge "$deadline" ] && { docker logs --tail 30 "$cid" >&2 || true; die "等待演练 db healthy 超时"; }
  sleep 3
done
compose_project "$PROJECT" "$RESTORE_ENV" "$RELEASE_DIR" exec -T db \
  pg_restore -U blyb --dbname blyb --no-owner < "$BACKUP_DIR/core.pg_dump" \
  || die "pg_restore 失败（检查备份与镜像版本兼容性；目标目录保留现场）"
log "core.pg_dump 已恢复"

log "[执行] 拉起演练全栈并健康门禁"
compose_project "$PROJECT" "$RESTORE_ENV" "$RELEASE_DIR" up -d >/dev/null
for svc in core storage exchange worker nginx; do
  cid="$(compose_project "$PROJECT" "$RESTORE_ENV" "$RELEASE_DIR" ps -q "$svc")"
  [ -n "$cid" ] || die "演练项目 $svc 无容器"
  deadline=$(( $(date +%s) + 180 ))
  until [ "$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$cid" 2>/dev/null || echo gone)" = "healthy" ]; do
    [ "$(date +%s)" -ge "$deadline" ] && { docker logs --tail 30 "$cid" >&2 || true; die "等待演练 $svc healthy 超时"; }
    sleep 3
  done
done

printf '{"restored_at":"%s","project":"%s","backup":"%s","release":"%s","data_root":"%s","etc_root":"%s","mapped":{"pg":"%s→%s","objects":"%s→%s","exchange":"%s→%s"},"image_note":"%s"}\n' \
  "$(date -u +%FT%TZ)" "$PROJECT" "$(cd "$BACKUP_DIR" && pwd)" "$RELEASE_DIR" "$DATA_ROOT" "$ETC_ROOT" \
  "$bk_pg_dir" "$TGT_PG" "$bk_obj_dir" "$TGT_OBJ" "$bk_ex_dir" "$TGT_EX" "$IMG_NOTE" \
  > "$DATA_ROOT/restore-manifest.json"
log "恢复完成（隔离项目 $PROJECT）：$DATA_ROOT/restore-manifest.json"
log "验证后清理：compose_project down（不加 -v 则数据保留）由操作者确认后执行"
