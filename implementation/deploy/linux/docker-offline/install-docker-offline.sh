#!/usr/bin/env bash
# openEuler 22.03 (x86_64) 离线 Docker 安装（execution-plan §7 P3 前置）。
# 采用官方静态二进制（自包含，无 RPM 依赖链）；数据根固定 <prod-path>/docker（§6，
# 避免根分区被镜像/卷占满）。幂等：已存在可用的 dockerd 即退出，不覆盖。
#
# 背景决策：openEuler 22.03-LTS-SP2 官方源仅 docker-engine 18.09（2018），与本发行
# 镜像/Compose v2 特性差距过大；故选静态 29.x 线（与构建机 Docker 29 同代）。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

log() { printf '[docker-offline] %s\n' "$*"; }
die() { printf '[docker-offline] 错误：%s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "需要 root"
cd "$HERE"
[ -f docker-29.9.1.tgz ] || die "缺少 docker-29.9.1.tgz"
sha256sum --quiet -c SHA256SUMS || die "二进制哈希校验失败"

if command -v dockerd >/dev/null 2>&1; then
  log "已存在 dockerd（$(command -v dockerd)）：不覆盖。如需换版按 runbook 人工处理"
  exit 0
fi

DATA_ROOT=<prod-path>/docker
mkdir -p "$DATA_ROOT" /usr/local/lib/docker/cli-plugins /etc/docker

# 0) 基础工具：目标机最小安装无 tar（2026-10-16 实测），先装发行版 tar RPM
#    （后续 backup/restore 脚本也依赖；rpm 依赖已随基础系统存在）
if ! command -v tar >/dev/null 2>&1; then
  TARB_RPM="$(ls "$(dirname "$0")"/tar-*.rpm 2>/dev/null | head -1)"
  [ -n "$TARB_RPM" ] || die "目标缺 tar 且包内无 tar-*.rpm"
  rpm -ivh --replacepkgs "$TARB_RPM" || die "tar RPM 安装失败"
  log "已安装 tar：$(command -v tar)"
fi

# 1) 静态二进制
tar -xzf docker-29.9.1.tgz
install -m 0755 docker/* /usr/local/bin/
rm -rf docker
install -m 0755 docker-compose-linux-x86_64 /usr/local/lib/docker/cli-plugins/docker-compose
ln -sf /usr/local/lib/docker/cli-plugins/docker-compose /usr/local/bin/docker-compose

# 2) daemon.json：数据根在 /home 大分区；日志默认 json-file 轮转
if [ ! -f /etc/docker/daemon.json ]; then
  cat > /etc/docker/daemon.json <<EOF
{
  "data-root": "$DATA_ROOT",
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" },
  "icc": false,
  "no-new-privileges": true
}
EOF
  log "已写 /etc/docker/daemon.json（data-root=$DATA_ROOT）"
else
  log "已存在 /etc/docker/daemon.json：不覆盖；请人工确认 data-root 指向 $DATA_ROOT"
fi

# 3) systemd 单元（静态包不带；采用上游参考单元的精简版）
if [ ! -f /etc/systemd/system/docker.service ]; then
  cat > /etc/systemd/system/docker.service <<'EOF'
[Unit]
Description=Docker Application Container Engine (static)
After=network-online.target
Wants=network-online.target
[Service]
Type=notify
ExecStart=/usr/local/bin/dockerd
ExecReload=/bin/kill -s HUP $MAINPID
Restart=always
RestartSec=5
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now docker
  log "docker.service 已启用"
fi

# 4) 验证
sleep 2
docker version --format 'engine {{.Server.Version}}' || die "docker engine 未就绪"
docker compose version || die "docker compose 插件未就绪"
log "完成：engine+compose 就绪；data-root=$DATA_ROOT"
