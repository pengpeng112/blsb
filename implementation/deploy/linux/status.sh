#!/usr/bin/env bash
# BLYB 运行状态检查（DEP-A11/A12；不输出密钥）。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"

log "current -> $(readlink -f "$BLYB_CURRENT" 2>/dev/null || echo 未设置)"
log "=== compose ps ==="
compose ps
echo
log "=== 服务健康 ==="
for svc in db core storage exchange worker nginx; do
  cid="$(compose ps -q "$svc" 2>/dev/null || true)"
  if [ -n "$cid" ]; then
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$cid" 2>/dev/null || echo gone)"
    restarts="$(docker inspect --format '{{.RestartCount}}' "$cid" 2>/dev/null || echo '?')"
    log "$svc: health=$state restarts=$restarts"
  else
    log "$svc: 未运行"
  fi
done
echo
log "=== 宿主机监听（应只有登记的 nginx 入口，无 8000/8001/8002 直映）==="
(ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null) | grep -E ':(443|8000|8001|8002|8443)\b' || log "（未发现 443/8000/8001/8002/8443 监听）"
echo
log "=== 入口连通（TLS 校验；OPS-01：验收不使用 -k）==="
PORT="$(runtime_env_value "$BLYB_RUNTIME_ENV" BLYB_HTTPS_PORT)"; PORT="${PORT:-443}"
CERT_DIR="$(runtime_env_value "$BLYB_RUNTIME_ENV" BLYB_TLS_CERT_DIR)"; CERT_DIR="${CERT_DIR:-$BLYB_ETC/certs}"
if command -v curl >/dev/null 2>&1 && command -v openssl >/dev/null 2>&1; then
  # 从部署证书的 SAN 中取校验用名称（按 IP 或 DNS）；无 SAN 即失败（不允许 -k 兜底）
  SAN="$(openssl x509 -in "$CERT_DIR/tls.crt" -noout -ext subjectAltName 2>/dev/null | tail -n +2 | tr -d ' ' | sed 's/IPAddress:/IP:/g' || true)"
  if [ -z "$SAN" ]; then
    log "TLS 校验失败：$CERT_DIR/tls.crt 无 subjectAltName（OPS-01；更换带 SAN 的证书）"
  else
    CHECK_HOST=""
    HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    for entry in ${SAN//,/ }; do
      [ -z "$CHECK_HOST" ] || break
      case "$entry" in
        IP:*)  [ "${entry#IP:}" = "127.0.0.1" ] || [ -n "$HOST_IP" -a "${entry#IP:}" = "$HOST_IP" ] && CHECK_HOST="${entry#IP:}";;
        DNS:*) CHECK_HOST="${entry#DNS:}";;
      esac
    done
    if [ -n "$CHECK_HOST" ]; then
      CA="$CERT_DIR/test-root-ca.crt"; [ -f "$CA" ] || CA="$CERT_DIR/tls.crt"
      if curl -sS --cacert "$CA" -o /dev/null -w "%{http_code}" \
          "https://$CHECK_HOST:$PORT/health" 2>/dev/null | grep -qE '^[12345]'; then
        log "TLS 验收通过：https://$CHECK_HOST:$PORT/health（链+SAN 校验，未关闭验证）"
      else
        log "TLS 验收失败：https://$CHECK_HOST:$PORT/health 校验未通过（证书链/名称/过期；不得用 -k 充当验收）"
      fi
    else
      log "TLS 校验失败：SAN（$SAN）不含本机 IP/DNS，无法按名称验证（OPS-01）"
    fi
  fi
else
  log "警告：缺少 curl/openssl，跳过 TLS 验收（人工按 runbook 验证，不得以 -k 通过）"
fi
echo
log "=== worker 心跳 ==="
cid="$(compose ps -q worker 2>/dev/null || true)"
[ -n "$cid" ] && docker exec "$cid" python -c \
  "import os,time;p='/tmp/<worker-container>-heartbeat.json';print('heartbeat age: %.1fs'%(time.time()-os.path.getmtime(p)) if os.path.isfile(p) else 'heartbeat 文件不存在')" || true
echo
log "=== 数据目录水位 ==="
df -h "$BLYB_DATA_ROOT" / | tail -2
du -sh "$BLYB_DATA_ROOT"/* 2>/dev/null || true
