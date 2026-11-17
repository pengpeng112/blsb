#!/usr/bin/env bash
# BLYB 基础平台安装（execution-plan §7 P3；DEP-A01—A13）。
# 用法：./install.sh <release-dir-or-tarball> [--skip-image-load]
# 幂等：重复执行不清库、不轮换密钥、不覆盖已生效配置。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"

SRC="${1:-}"; [ -n "$SRC" ] || die "用法：install.sh <release-dir-or-tarball> [--skip-image-load]"
shift || true
SKIP_LOAD=0
[ "${1:-}" = "--skip-image-load" ] && SKIP_LOAD=1

# 1) 定位/展开发行包
if [ -d "$SRC" ]; then
  RELEASE_DIR="$(cd "$SRC" && pwd)"
elif [ -f "$SRC" ] && [[ "$SRC" == *.tar.gz ]]; then
  ensure_layout
  base="$(basename "$SRC" .tar.gz)"
  mkdir -p "$BLYB_RELEASES"
  tar -xzf "$SRC" -C "$BLYB_RELEASES"
  RELEASE_DIR="$BLYB_RELEASES/$base"
else
  die "$SRC 不是发行目录或 *.tar.gz"
fi
[ -f "$RELEASE_DIR/manifest.json" ] || die "$RELEASE_DIR 缺少 manifest.json（不是本脚本管理的发行包）"
RELEASE_ID="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["release_id"])' "$RELEASE_DIR/manifest.json" 2>/dev/null \
  || grep -o '"release_id"[^,]*' "$RELEASE_DIR/manifest.json" | head -1 | cut -d'"' -f4)"
[ -n "$RELEASE_ID" ] || die "无法从 manifest.json 读取 release_id"

# 2) 哈希校验
verify_release "$RELEASE_DIR"

# 3) 目录/权限（不重分区、不动既有数据）
ensure_layout

# 4) 归位到 releases 并切换 current（不可变发行目录）
if [ "$RELEASE_DIR" != "$BLYB_RELEASES/$RELEASE_ID" ]; then
  mkdir -p "$BLYB_RELEASES/$RELEASE_ID"
  cp -a "$RELEASE_DIR"/. "$BLYB_RELEASES/$RELEASE_ID/"
  RELEASE_DIR="$BLYB_RELEASES/$RELEASE_ID"
fi
[ -f "$BLYB_ETC/certs/tls.crt" ] && [ -f "$BLYB_ETC/certs/tls.key" ] \
  || die "缺少 TLS 证书：$BLYB_ETC/certs/tls.crt|tls.key（正式环境用院方证书；不关闭校验）"

# 5) 镜像加载
[ "$SKIP_LOAD" = "1" ] || load_images "$RELEASE_DIR"

# 6) runtime.env（首装生成高熵密钥；重装只换 tag）
write_runtime_env "$RELEASE_ID"

# 7) 切 current 并校验合并配置
ln -sfn "$RELEASE_DIR" "$BLYB_CURRENT"
compose config --quiet
log "compose 配置校验通过（无 8000/8001/8002 宿主机直映；入口=nginx 443）"

# 8) 显式健康门禁部署（db→migrate→bootstrap→全栈）
bring_up_stack

# 9) 防火墙放行登记入口（firewalld 保持开启；仅加 https 服务，不动其他规则）
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  if ! firewall-cmd --query-service=https >/dev/null 2>&1; then
    firewall-cmd --permanent --add-service=https >/dev/null
    firewall-cmd --reload >/dev/null
    log "firewalld 已放行 https(443)"
  else
    log "firewalld https 已放行"
  fi
else
  log "警告：firewalld 不可用或未运行——443 入站放行须人工确认（不关闭防火墙）"
fi

# 9) 结果摘要（不含密钥）
PORT="$(grep -E '^BLYB_HTTPS_PORT=' "$BLYB_RUNTIME_ENV" | cut -d= -f2)"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
log "安装完成：release=$RELEASE_ID"
log "访问入口：https://${IP:-<管理网IP>}:${PORT:-443}/ui/（证书/域名以院方交付为准）"
log "网页登录：用户名和密码；首装用 apps.manage_console_user 交互创建，凭据勿写进命令参数"
log "自动化管理令牌仍见 $BLYB_RUNTIME_ENV（600 权限），与网页密码独立"
log "状态检查：$HERE/status.sh"
