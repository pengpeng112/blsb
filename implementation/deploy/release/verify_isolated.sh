#!/usr/bin/env bash
# P2 隔离验证（execution-plan §7 P2；DEP-A03—A13 本地可测项）。
# 在构建机 Docker 上以独立项目 blybtest 拉起隔离栈：
#   test-override.yml（命名卷 + 127.0.0.1:8443 + 一次性自签测试证书）
# 产出：逐项 PASS/FAIL 与失败即非零退出。不访问任何真实业务系统。
# 用法：bash deploy/release/verify_isolated.sh <release-id>   （工作目录=implementation/）
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1   # → implementation/

RID="${1:?用法：verify_isolated.sh <release-id>}"
P=blybtest
ENV_FILE=$(mktemp)
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf 'PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf 'FAIL %s\n' "$1"; }

cat > "$ENV_FILE" <<EOF
BLYB_DB_PASSWORD=is0lated-fixed-test-password
BLYB_BOOTSTRAP_ADMIN_TOKEN=is0lated-admin-token-0123456789abcdef0123456789abcdef
BLYB_ENROLL_TOKEN=is0lated-enroll-token-0123456789abcdef0123456789abcdef
BLYB_ALLOWED_ORIGINS=
BLYB_BACKEND_IMAGE=blyb/backend:$RID
BLYB_NGINX_IMAGE=blyb/nginx:$RID
BLYB_PG_IMAGE=blyb/pg:$RID
BLYB_PGDATA_DIR=/tmp/unused-pgdata
BLYB_OBJECTS_DIR=/tmp/unused-objects
BLYB_EXCHANGE_DIR=/tmp/unused-exchange
BLYB_TLS_CERT_DIR=./test-certs
EOF
ADMIN_TOKEN="is0lated-admin-token-0123456789abcdef0123456789abcdef"
CFG=(-p "$P" --env-file "$ENV_FILE" -f deploy/compose/docker-compose.yml -f deploy/compose/test-override.yml)
BASE_URL=https://127.0.0.1:8443

# 仅隔离测试项目 blybtest 自有卷允许 down -v（生产栈严禁；见 deploy/linux/README）。
# 开场先清场：防止上次运行/手工调试遗留卷（旧密码）干扰全新初始化。
cleanup() { docker compose "${CFG[@]}" down -v --remove-orphans >/dev/null 2>&1; rm -f "$ENV_FILE"; }
trap cleanup EXIT
docker compose "${CFG[@]}" down -v --remove-orphans >/dev/null 2>&1 || true

step() { printf '\n== %s ==\n' "$1"; }

step "A13 预检：镜像内无运行库/凭据"
if docker run --rm --entrypoint sh "blyb/backend:$RID" -c \
     'find /app /data -name "*.sqlite3*" -o -name "*.env" -o -name "agent-config.json" 2>/dev/null | grep -q .'; then
  bad "A13 镜像含 sqlite/env/凭据"
else ok "A13 镜像内无 sqlite3/.env/agent-config.json"; fi

step "A12 预检：合并配置无后端宿主机端口"
PORTS=$(docker compose "${CFG[@]}" config | grep -E 'published:' | grep -oE '[0-9]+' | sort -u | tr '\n' ' ' | sed 's/ $//')
[ "$PORTS" = "8443" ] && ok "A12 仅发布 8443（$PORTS）" || bad "A12 端口发布异常：$PORTS"

step "拉起：db→migrate→bootstrap→全栈（显式门禁）"
docker compose "${CFG[@]}" up -d db >/dev/null 2>&1 || { bad "db 启动失败"; exit 1; }
DB_CID=$(docker compose "${CFG[@]}" ps -q db)
for i in $(seq 1 60); do
  [ "$(docker inspect --format '{{.State.Health.Status}}' "$DB_CID")" = "healthy" ] && break
  sleep 2
done
H=$(docker inspect --format '{{.State.Health.Status}}' "$DB_CID")
[ "$H" = "healthy" ] && ok "db healthy" || { bad "db 未健康（$H）"; exit 1; }

M1=$(docker compose "${CFG[@]}" run --rm migrate 2>&1); M1_RC=$?
M2=$(docker compose "${CFG[@]}" run --rm migrate 2>&1); M2_RC=$?
if [ $M1_RC -eq 0 ] && [ $M2_RC -eq 0 ] && echo "$M2" | grep -qi "already"; then
  ok "A05 PG迁移：首次成功+重复 ALREADY_APPLIED"
else bad "A05 迁移异常 rc=$M1_RC/$M2_RC：$(echo "$M2" | tail -3)"; fi

B1=$(docker compose "${CFG[@]}" run --rm bootstrap 2>&1); B1_RC=$?
B2=$(docker compose "${CFG[@]}" run --rm bootstrap 2>&1); B2_RC=$?
[ $B1_RC -eq 0 ] && [ $B2_RC -eq 0 ] && ok "bootstrap 幂等（两次 rc=0）" || bad "bootstrap rc=$B1_RC/$B2_RC：$(echo "$B1$B2" | tail -3)"

docker compose "${CFG[@]}" up -d >/dev/null 2>&1
for svc in core storage exchange worker nginx; do
  CID=$(docker compose "${CFG[@]}" ps -q "$svc"); ST=starting
  for i in $(seq 1 60); do
    ST=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CID" 2>/dev/null)
    [ "$ST" = "healthy" ] && break; sleep 3
  done
  [ "$ST" = "healthy" ] && ok "$svc healthy" || bad "$svc 未健康（$ST）"
done

step "A06 认证：bootstrap令牌OK；dev-* 401；默认注册密钥失效"
code() { curl -sk -o /dev/null -w '%{http_code}' "$@"; }
LOGIN=$(code -X POST "$BASE_URL/api/v1/auth/login" -H 'Content-Type: application/json' \
        -d "{\"token\":\"$ADMIN_TOKEN\"}")
[ "$LOGIN" = "200" ] && ok "A06 管理员令牌登录 200" || bad "A06 管理员登录=$LOGIN"
DEV=$(code -X POST "$BASE_URL/api/v1/auth/login" -H 'Content-Type: application/json' \
      -d '{"token":"dev-admin-token"}')
[ "$DEV" = "401" ] && ok "A06 dev-admin-token 401" || bad "A06 dev令牌=$DEV（应401）"
ENR=$(code -X POST "$BASE_URL/api/v1/agents/enroll" -H 'Content-Type: application/json' \
      -d '{"enroll_token":"dev-enroll-token","device_code":"X"}')
[ "$ENR" = "401" ] && ok "A06 默认注册密钥 401" || bad "A06 默认注册密钥=$ENR（应401）"

step "A07 同源入口：/ui 与 /storage 前缀剥离"
UI=$(code "$BASE_URL/ui/")
[ "$UI" = "200" ] && ok "A07 /ui/ 200" || bad "A07 /ui/=$UI"
STH=$(code "$BASE_URL/storage/health")
[ "$STH" = "200" ] && ok "A07 /storage/health 200（前缀剥离）" || bad "A07 /storage/health=$STH"
CORE=$(code "$BASE_URL/health")
[ "$CORE" = "200" ] && ok "A07 /health（core经网关）200" || bad "A07 /health=$CORE"

step "A12 后端不可直连（宿主机无 8000/8001/8002）"
for p in 8000 8001 8002; do
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$p/health"; then
    bad "A12 端口 $p 宿主机可直连"
  else ok "A12 端口 $p 不可直达"; fi
done

step "A11 worker 心跳与日志轮转"
WH=$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose "${CFG[@]}" ps -q worker)")
[ "$WH" = "healthy" ] && ok "A11 worker心跳健康" || bad "A11 worker=$WH"
LOGCFG=$(docker inspect --format '{{.HostConfig.LogConfig.Config}}' "$(docker compose "${CFG[@]}" ps -q core)")
echo "$LOGCFG" | grep -q "max-size" && ok "A11 json-file轮转配置" || bad "A11 日志轮转缺失：$LOGCFG"

step "A08 持久化：重启 core/db 后令牌仍可登录"
docker compose "${CFG[@]}" restart core db >/dev/null 2>&1
# 显式等待 core 回到 healthy（与生产 install 同一语义；不用固定 sleep 赌时序）
CORE_CID=$(docker compose "${CFG[@]}" ps -q core)
for i in $(seq 1 60); do
  CST=$(docker inspect --format '{{.State.Health.Status}}' "$CORE_CID" 2>/dev/null)
  [ "$CST" = "healthy" ] && break
  sleep 3
done
[ "$CST" = "healthy" ] && ok "A08 core 重启后回 healthy" || bad "A08 core 重启后未恢复（$CST）"
LOGIN2=$(code -X POST "$BASE_URL/api/v1/auth/login" -H 'Content-Type: application/json' \
         -d "{\"token\":\"$ADMIN_TOKEN\"}")
[ "$LOGIN2" = "200" ] && ok "A08 重启后登录 200" || bad "A08 重启后登录=$LOGIN2"

printf '\n==== 结果：PASS=%d FAIL=%d ====\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
