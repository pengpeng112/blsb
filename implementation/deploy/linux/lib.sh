#!/usr/bin/env bash
# BLYB Linux 部署公共函数（execution-plan §7 P3/§9）。
# 约定：本库只被 deploy/linux/*.sh source，不直接执行。
set -euo pipefail

BLYB_ROOT="${BLYB_ROOT:-/opt/blyb}"
BLYB_RELEASES="$BLYB_ROOT/releases"
BLYB_CURRENT="$BLYB_ROOT/current"
BLYB_ETC="${BLYB_ETC:-/etc/blyb}"
BLYB_RUNTIME_ENV="$BLYB_ETC/runtime.env"
BLYB_DATA_ROOT="${BLYB_DATA_ROOT:-<prod-path>}"
BLYB_COMPOSE_FILE="deploy/compose/docker-compose.yml"
BLYB_APP_UID="${BLYB_APP_UID:-10001}"
BLYB_APP_GID="${BLYB_APP_GID:-10001}"
# DPL-P02：药品追溯覆盖（白名单固定路径；由 BLYB_DRUGTRACE_ENABLED=1 持久
# 开关控制，写入 runtime.env）。开关非"1"一律不加覆盖（fail-closed）；
# 开关开启但发行缺覆盖文件 → 显式失败，绝不静默丢掉 worker 密钥/证书挂载。
BLYB_DRUGTRACE_OVERRIDE="deploy/compose/drug-trace.override.yml"

log()  { printf '[blyb] %s\n' "$*"; }
die()  { printf '[blyb] 错误：%s\n' "$*" >&2; exit 1; }

# 药品追溯开关（从指定 env 文件读取；仅字面 "1" 视为开启）
drugtrace_enabled() {
  [ "$(runtime_env_value "$1" BLYB_DRUGTRACE_ENABLED)" = "1" ]
}

# 输出 compose -f 参数（基础文件 + 可选药品覆盖）到全局数组 COMPOSE_FILES
# 用法：build_compose_files <env-file> <release-impl-dir>
build_compose_files() {
  local env_file="$1" rel_dir="$2"
  COMPOSE_FILES=(-f "$BLYB_COMPOSE_FILE")
  if drugtrace_enabled "$env_file"; then
    [ -f "$rel_dir/$BLYB_DRUGTRACE_OVERRIDE" ] || \
      die "BLYB_DRUGTRACE_ENABLED=1 但 $rel_dir/$BLYB_DRUGTRACE_OVERRIDE 缺失（发行缺件或回退到旧发行？先恢复该次升级前 runtime.env 的开关/插件状态）"
    COMPOSE_FILES+=(-f "$BLYB_DRUGTRACE_OVERRIDE")
  fi
}

# 统一 compose 入口（在发行目录内执行；OPS-02：不依赖调用方 cwd）
# DPL-P02：按 runtime.env 的 BLYB_DRUGTRACE_ENABLED 追加药品覆盖文件——
# 安装/升级/备份/恢复/重启/状态/回退共用本入口，配置闭包一致。
compose() {
  local rel_dir="$BLYB_CURRENT/implementation"
  [ -f "$rel_dir/$BLYB_COMPOSE_FILE" ] || die "缺少 $rel_dir/$BLYB_COMPOSE_FILE（current 指向失效？）"
  build_compose_files "$BLYB_RUNTIME_ENV" "$rel_dir"
  ( cd "$rel_dir" && docker compose --project-name blyb --env-file "$BLYB_RUNTIME_ENV" \
      "${COMPOSE_FILES[@]}" "$@" )
}

# 显式项目/配置 compose 入口（OPS-02/OPS-03：独立项目名、独立 env、显式发行目录；
# 供恢复演练/隔离验证使用，绝不隐式落到生产 compose 项目。
# DPL-P02：演练 env 同样支持 BLYB_DRUGTRACE_ENABLED——备份的 runtime.env 带开关
# 时，恢复演练自动带上同一覆盖，保证演练闭包与生产一致。）
compose_project() {
  local project="$1" env_file="$2" release_dir="$3"; shift 3
  [ -n "$project" ] || die "compose_project: 项目名为空"
  [ -s "$env_file" ]  || die "compose_project: env 文件缺失或为空：$env_file"
  [ -f "$release_dir/implementation/$BLYB_COMPOSE_FILE" ] || \
    die "compose_project: 发行目录缺少 compose 文件：$release_dir"
  build_compose_files "$env_file" "$release_dir/implementation"
  ( cd "$release_dir/implementation" && docker compose --project-name "$project" \
      --env-file "$env_file" "${COMPOSE_FILES[@]}" "$@" )
}

# 读取 runtime.env 某变量（缺失输出空串；调用方对空值 fail-closed，OPS-03）
runtime_env_value() {
  local env_file="$1" key="$2" val
  [ -r "$env_file" ] || return 0
  val="$(grep -E "^${key}=" "$env_file" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  printf '%s' "$val"
}

# 归档成员安全检查（OPS-03：路径穿越/绝对路径/符号链接逃逸失败关闭）
# check_archive <tar.gz 路径>；输出成员数，违规即 die
check_archive() {
  local archive="$1" plain listing bad
  [ -f "$archive" ] || die "归档不存在：$archive"
  plain="$(tar --force-local -tzf "$archive" 2>/dev/null)" || die "归档无法读取（损坏或非 tar.gz）：$archive"
  # 1) 绝对路径或 .. 路径段（穿越）
  bad="$(printf '%s\n' "$plain" | grep -E '^/|(^|/)\.\.(/|$)' || true)"
  [ -z "$bad" ] || die "归档含绝对路径/.. 成员（疑似路径穿越）：$archive → $(printf '%s' "$bad" | head -1)"
  # 2) 符号链接/硬链接成员（对象与配置归档不允许链接，失败关闭；
  #    GNU tar：symlink 权限位以 l 开头，硬链接行含 " link to "）
  listing="$(tar --force-local -tzvf "$archive" 2>/dev/null)" || die "归档无法读取（详细清单）：$archive"
  bad="$(printf '%s\n' "$listing" | awk '$1 ~ /^[lh]/ || $0 ~ / link to / {print}' || true)"
  [ -z "$bad" ] || die "归档含链接成员（symlink/hardlink 不允许）：$archive → $(printf '%s' "$bad" | head -1)"
  printf '%s\n' "$plain" | grep -c .
}

# 路径包含检查（OPS-03：目标必须落在指定根内，符号链接绕过失败关闭）
# path_under <root> <candidate>；不在根内或解析失败返回非零
path_under() {
  local root="$1" cand="$2" rroot rcand
  rroot="$(realpath -m "$root" 2>/dev/null)" || return 1
  rcand="$(realpath -m "$cand" 2>/dev/null)" || return 1
  [ "$rcand" = "$rroot" ] || [[ "$rcand" == "$rroot"/* ]]
}

# 目录为空（不存在视为空）
dir_empty() {
  local d="$1"
  [ ! -d "$d" ] && return 0
  [ -z "$(ls -A "$d" 2>/dev/null)" ]
}

# 显式健康门禁：wait_healthy <service> <timeout_seconds>（不依赖 compose 默认等待语义，D5）
wait_healthy() {
  local svc="$1" timeout="${2:-120}" cid state
  cid="$(compose ps -q "$svc")"
  [ -n "$cid" ] || die "服务 $svc 无容器（compose ps -q 为空）"
  local deadline=$(( $(date +%s) + timeout ))
  while :; do
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$cid" 2>/dev/null || echo gone)"
    [ "$state" = "healthy" ] && { log "$svc healthy"; return 0; }
    if [ "$(date +%s)" -ge "$deadline" ]; then
      docker logs --tail 50 "$cid" >&2 || true
      die "等待 $svc healthy 超时（${timeout}s，当前 $state）"
    fi
    sleep 3
  done
}

# 高熵密钥（优先 openssl，回退 /dev/urandom；不回显由调用方决定）
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then openssl rand -hex 32
  else od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; fi
}

# 发行目录哈希校验：verify_release <dir>
verify_release() {
  local dir="$1"
  [ -f "$dir/SHA256SUMS" ] || die "$dir 缺少 SHA256SUMS"
  ( cd "$dir" && sha256sum --quiet -c SHA256SUMS ) || die "$dir 哈希校验失败"
  log "release 哈希校验通过：$dir"
}

ensure_layout() {
  mkdir -p "$BLYB_RELEASES" "$BLYB_DATA_ROOT"/{postgres,exchange,objects,backups} \
           /var/log/blyb "$BLYB_ETC/certs"
  # 对象/交换目录归应用账户（PG 目录由官方镜像入口自行初始化属主）
  chown "$BLYB_APP_UID:$BLYB_APP_GID" "$BLYB_DATA_ROOT/objects" "$BLYB_DATA_ROOT/exchange" 2>/dev/null || \
    log "警告：无法 chown 数据目录（非root运行？容器内属主须为 ${BLYB_APP_UID}）"
}

# 幂等生成 runtime.env：已存在则只更新镜像 tag，不轮换既有密钥
write_runtime_env() {
  local release_id="$1" env_file="$BLYB_RUNTIME_ENV"
  if [ -f "$env_file" ]; then
    sed -i -E \
      -e "s#^BLYB_BACKEND_IMAGE=.*#BLYB_BACKEND_IMAGE=blyb/backend:${release_id}#" \
      -e "s#^BLYB_NGINX_IMAGE=.*#BLYB_NGINX_IMAGE=blyb/nginx:${release_id}#" \
      -e "s#^BLYB_PG_IMAGE=.*#BLYB_PG_IMAGE=blyb/pg:${release_id}#" "$env_file"
    log "runtime.env 已存在：仅更新镜像 tag，密钥保持不变（幂等，不轮换）"
    return 0
  fi
  cat > "$env_file" <<EOF
# 由 install.sh 生成 $(date -u +%FT%TZ)；权限须为 600。含密钥，禁止入发行包/日志。
BLYB_DB_PASSWORD=$(gen_secret)
BLYB_BOOTSTRAP_ADMIN_TOKEN=$(gen_secret)
BLYB_ENROLL_TOKEN=$(gen_secret)
BLYB_ALLOWED_ORIGINS=
BLYB_BACKEND_IMAGE=blyb/backend:${release_id}
BLYB_NGINX_IMAGE=blyb/nginx:${release_id}
BLYB_PG_IMAGE=blyb/pg:${release_id}
BLYB_PGDATA_DIR=${BLYB_DATA_ROOT}/postgres
BLYB_OBJECTS_DIR=${BLYB_DATA_ROOT}/objects
BLYB_EXCHANGE_DIR=${BLYB_DATA_ROOT}/exchange
BLYB_TLS_CERT_DIR=${BLYB_ETC}/certs
BLYB_HTTPS_BIND=0.0.0.0
BLYB_HTTPS_PORT=443
BLYB_TENANT_CODE=first-hospital
BLYB_TENANT_NAME=首院
EOF
  chmod 600 "$env_file"
  log "runtime.env 已生成（高熵密钥；bootstrap 管理员令牌见该文件，勿回显）"
}

# 镜像加载（幂等：本地已有同 tag 且 digest 一致则跳过）
load_images() {
  local rel_dir="$1" tar name id
  for tar in "$rel_dir"/images/*.tar; do
    [ -f "$tar" ] || continue
    name="$(basename "$tar" .tar)"
    if docker image inspect "$name" >/dev/null 2>&1; then
      log "镜像已存在，跳过加载：$name"
    else
      docker load -i "$tar"
    fi
  done
}

# 健康门禁拉起全栈（install/upgrade/rollback 共用）
bring_up_stack() {
  compose up -d db
  wait_healthy db 180
  # 显式执行迁移与初始化（退出码检查）；随后的全栈 up 会因依赖再次幂等执行
  compose run --rm migrate
  compose run --rm bootstrap
  compose up -d
  wait_healthy core 180
  wait_healthy storage 120
  wait_healthy exchange 120
  wait_healthy worker 120
  wait_healthy nginx 120
}
