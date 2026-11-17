#!/usr/bin/env bash
# BLYB TLS 证书工具（OPS-01 → DTR-FINALIZE FIN-07 扩展）。
#
# 子命令：
#   make-test-ca <dir>                    生成离线测试根 CA（CN 含 BLYB-TEST-CA 标记）
#   issue-server <dir> --san DNS:x --san IP:y [--cn name] [--days N] [--force]
#                                         用测试 CA 签发带 SAN 的 serverAuth 服务端证书
#   issue-client <dir> [--cn N] [--days D] [--force]
#                                         签发 clientAuth 客户端证书（<prod-host> 签名/查询侧）
#   issue-ops-probe <dir> [--days D] [--force]
#                                         签发 clientAuth 运维探针证书（DMZ 本机只读
#                                         /health 用；不复用 <prod-host> 客户端私钥）
#   verify <dir> --host name-or-ip        校验证书链与 SAN 名称匹配（不依赖 -k）
#
# 安全约束：私钥 600 且不出目录；不覆盖既有文件（--force 除外）；产物仅供测试。
# serverAuth 与 clientAuth 证书用途分离（FIN-A28）：不得拿服务端证书当客户端证书。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"

usage() { die "用法：certs.sh make-test-ca <dir> | issue-server <dir> --san DNS:x [--san IP:y] [--cn N] [--days D] [--force] | issue-client <dir> [--cn N] [--days D] [--force] | issue-ops-probe <dir> [--days D] [--force] | verify <dir> --host H"; }

CMD="${1:-}"; shift || true
DIR="${1:-}"; shift || true
[ -n "$CMD" ] && [ -n "$DIR" ] || usage
mkdir -p "$DIR"

CA_KEY="$DIR/test-root-ca.key"; CA_CRT="$DIR/test-root-ca.crt"
SRV_KEY="$DIR/tls.key"; SRV_CRT="$DIR/tls.crt"
CLI_KEY="$DIR/client.key"; CLI_CRT="$DIR/client.crt"
OPS_KEY="$DIR/ops-probe.key"; OPS_CRT="$DIR/ops-probe.crt"

require_openssl() { command -v openssl >/dev/null 2>&1 || die "需要 openssl"; }

_issue_client_auth() {  # $1=key $2=crt $3=CN $4=days [$5=force]
  require_openssl
  [ -f "$CA_KEY" ] && [ -f "$CA_CRT" ] || die "先执行 make-test-ca"
  local key="$1" crt="$2" cn="$3" days="$4" force="${5:-0}"
  if [ -f "$crt" ] && [ "$force" != "1" ]; then
    die "证书已存在：$crt（--force 覆盖；轮换流程见 runbook）"
  fi
  local conf="$DIR/.eku.$$"
  printf 'basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=clientAuth\n' > "$conf"
  openssl genrsa -out "$key" 2048 2>/dev/null
  openssl req -new -key "$key" -subj "$(subj_flag "C=CN/O=BLYB-TEST-ONLY/CN=$cn")" 2>/dev/null \
    | openssl x509 -req -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
        -days "$days" -sha256 -extfile "$conf" -out "$crt" 2>/dev/null
  rm -f "$conf"
  chmod 600 "$key"
  log "clientAuth 证书已签发：$crt（CN=$cn，${days} 天，测试用途）"
}

# Git Bash(MSYS) 测试便利：-subj 的 /C=CN/... 用双斜杠前缀避免被当路径改写（Linux 上无副作用）
subj_flag() {
  local s="/$1"
  case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) s="/$s";; esac
  printf '%s' "$s"
}

case "$CMD" in
  make-test-ca)
    require_openssl
    [ -f "$CA_KEY" ] && die "测试 CA 已存在：$CA_KEY（勿混用；换目录或先归档）"
    # DPL 修订：测试根 CA 必须带规范 CA 扩展——无 basicConstraints/keyUsage 的
    # 自签 CA 会被 OpenSSL 3.5（Debian trixie / Python 3.14 的严格链校验）以
    # "CA cert does not include key usage extension" 拒绝（<prod-host> 实测）。
    openssl genrsa -out "$CA_KEY" 3072 2>/dev/null
    openssl req -x509 -new -key "$CA_KEY" -sha256 -days 3650 \
      -subj "$(subj_flag 'C=CN/O=BLYB-TEST-ONLY/CN=BLYB Test Root CA (NOT FOR PRODUCTION)')" \
      -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
      -addext "keyUsage=critical,keyCertSign,cRLSign" \
      -addext "subjectKeyIdentifier=hash" \
      -out "$CA_CRT" 2>/dev/null
    chmod 600 "$CA_KEY"
    log "测试根 CA 已生成（含 CA:TRUE/keyCertSign 扩展；仅测试；正式链以院方 PKI 为准）：$CA_CRT"
    ;;

  issue-server)
    require_openssl
    [ -f "$CA_KEY" ] && [ -f "$CA_CRT" ] || die "先执行 make-test-ca"
    CN=""; DAYS=30; FORCE=0; SAN_ARGS=(); SAN_LIST=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --san) SAN_ARGS+=("${2:-}"); SAN_LIST="${SAN_LIST}${2:-},"; shift 2;;
        --cn) CN="${2:-}"; shift 2;;
        --days) DAYS="${2:-}"; shift 2;;
        --force) FORCE=1; shift;;
        *) die "未知参数：$1";;
      esac
    done
    [ "${#SAN_ARGS[@]}" -gt 0 ] || die "至少一个 --san DNS:<name> 或 --san IP:<addr>（无 SAN 的证书按 IP/域名校验必然失败）"
    [ -n "$CN" ] || CN="${SAN_ARGS[0]#*:}"
    if [ -f "$SRV_KEY" ] && [ "$FORCE" != "1" ]; then die "服务端证书已存在（--force 覆盖；轮换流程见 runbook）"; fi
    SAN_CONF="$DIR/.san.$$"
    # 去掉尾部逗号
    SAN_LIST="${SAN_LIST%,}"
    printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n' \
      "$SAN_LIST" > "$SAN_CONF"
    openssl genrsa -out "$SRV_KEY" 2048 2>/dev/null
    openssl req -new -key "$SRV_KEY" -subj "$(subj_flag "C=CN/O=BLYB-TEST-ONLY/CN=$CN")" 2>/dev/null \
      | openssl x509 -req -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
          -days "$DAYS" -sha256 -extfile "$SAN_CONF" -out "$SRV_CRT" 2>/dev/null
    rm -f "$SAN_CONF"
    chmod 600 "$SRV_KEY"
    log "服务端证书已签发：$SRV_CRT（SAN=$SAN_LIST；有效期 ${DAYS} 天，测试用途）"
    ;;

  issue-client)
    CN="blyb-app-client"; DAYS=365; FORCE=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --cn) CN="${2:-}"; shift 2;;
        --days) DAYS="${2:-}"; shift 2;;
        --force) FORCE=1; shift;;
        *) die "未知参数：$1";;
      esac
    done
    _issue_client_auth "$CLI_KEY" "$CLI_CRT" "$CN" "$DAYS" "$FORCE"
    ;;

  issue-ops-probe)
    DAYS=365; FORCE=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --days) DAYS="${2:-}"; shift 2;;
        --force) FORCE=1; shift;;
        *) die "未知参数：$1";;
      esac
    done
    _issue_client_auth "$OPS_KEY" "$OPS_CRT" "blyb-dmz-ops-probe" "$DAYS" "$FORCE"
    ;;

  verify)
    require_openssl
    HOST=""; while [ $# -gt 0 ]; do case "$1" in --host) HOST="${2:-}"; shift 2;; *) die "未知参数：$1";; esac; done
    [ -n "$HOST" ] || usage
    [ -f "$SRV_CRT" ] || die "缺少 $SRV_CRT"
    CA="$CA_CRT"; [ -f "$CA" ] || CA="$SRV_CRT"   # 无独立CA时自签自验（链有效即可，名称仍须匹配）
    openssl verify -CAfile "$CA" "$SRV_CRT" >/dev/null || die "证书链校验失败"
    SAN="$(openssl x509 -in "$SRV_CRT" -noout -ext subjectAltName 2>/dev/null | tail -n +2 | tr -d ' ' | sed 's/IPAddress:/IP:/g' || true)"
    [ -n "$SAN" ] || die "证书无 subjectAltName：按现代校验必然失败（OPS-01），拒绝"
    # 名称匹配：IP 精确；DNS 支持最左通配
    HIT=0
    for entry in ${SAN//,/ }; do
      [ -z "$entry" ] && continue
      if [[ "$entry" == IP:* ]]; then
        [ "${entry#IP:}" = "$HOST" ] && HIT=1
      elif [[ "$entry" == DNS:* ]]; then
        name="${entry#DNS:}"
        if [ "$name" = "$HOST" ]; then HIT=1
        elif [[ "$name" == \*.* ]]; then
          # *.example.com 匹配 host.example.com（恰好一段）
          suffix="${name#*.}"
          [[ "$HOST" == *."$suffix" && "$HOST" != *."$suffix"."$suffix" ]] && HIT=1
        fi
      fi
    done
    [ "$HIT" = "1" ] || die "名称不匹配：host=$HOST 不在 SAN（$SAN）中"
    log "证书校验通过：链有效且 SAN 覆盖 $HOST"
    ;;

  *) usage;;
esac
