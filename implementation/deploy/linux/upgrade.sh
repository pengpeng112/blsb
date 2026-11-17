#!/usr/bin/env bash
# BLYB 升级（execution-plan §9：备份→切发行→迁移→健康门禁；失败指引回滚）。
# 用法：./upgrade.sh <new-release-dir-or-tarball>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib.sh"

SRC="${1:-}"; [ -n "$SRC" ] || die "用法：upgrade.sh <new-release-dir-or-tarball>"
OLD_ID="$(basename "$(readlink -f "$BLYB_CURRENT" 2>/dev/null)" || echo none)"

# 1) 先备份（升级失败可回数据）
"$HERE/backup.sh"

# 2) 校验并安装新发行（幂等：保留密钥，只换镜像 tag）
"$HERE/install.sh" "$SRC"

log "升级完成：$OLD_ID -> $(basename "$(readlink -f "$BLYB_CURRENT")")"
log "冒烟：浏览器登录 + 一次合成只读查询；异常回滚：$HERE/rollback.sh $OLD_ID"
log "注意：编号迁移只前进。若新版本迁移不兼容旧回滚目标，按 runbook 用备份恢复，不直接覆盖现库"
