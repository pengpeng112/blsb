#!/usr/bin/env bash
# <prod-host>/DMZ 时间与 DNS 诊断（DTR-FINALIZE-20261020 FIN-07/FIN-A29）。
# 兼容旧 systemd219/Anolis7.9：只用基础命令；参数不兼容的命令按原样记录
# 失败，不伪称 NTP 通过。只读诊断，不改配置、不跳时、不写文件。
#
# 用法：bash diag_time_dns.sh [dns-server-ip]
set -uo pipefail

section() { printf '\n== %s ==\n' "$1"; }

section "clock"
date -u '+UTC: %Y-%m-%dT%H:%M:%SZ'
date '+LOCAL: %Y-%m-%d %H:%M:%S %Z (%z)'

section "timedatectl (full; old systemd 不支持 --property/-p 子集，整体输出)"
if command -v timedatectl >/dev/null 2>&1; then
  timedatectl 2>&1 | sed 's/^/  /'
  # 10-16/10-20 实测：`timedatectl show --property=...` 在 219 上不兼容；
  # 这里只整体展示，不据此断言同步状态。
else
  echo "  timedatectl 不可用"
fi

section "chrony"
if command -v chronyc >/dev/null 2>&1; then
  chronyc tracking 2>&1 | sed 's/^/  /' || echo "  chronyc tracking 失败（如实记录）"
  chronyc sourcestats 2>&1 | head -8 | sed 's/^/  /' || true
else
  echo "  chronyc 不可用"
fi

section "ntpq"
if command -v ntpq >/dev/null 2>&1; then
  ntpq -p 2>&1 | sed 's/^/  /' || echo "  ntpq -p 失败"
else
  echo "  ntpq 不可用（Anolis7.9 最小安装常见；不代表 NTP 关闭）"
fi

section "RTC vs system (drift 信号)"
if [ -r /proc/driver/rtc ]; then
  grep -E 'rtc_time|rtc_date' /proc/driver/rtc | sed 's/^/  /'
  echo "  （若 rtc_time 与 UTC 相差分钟级，说明硬件钟漂移——由院方决定校时方案，本脚本不动）"
else
  hwclock --show 2>&1 | sed 's/^/  /' || echo "  hwclock 不可用"
fi

section "DNS resolution"
for name in eco.taobao.com gw.api.taobao.com; do
  printf '  %s: ' "$name"
  if command -v getent >/dev/null 2>&1; then
    getent ahostsv4 "$name" 2>/dev/null | awk '{print $1; exit}' || echo "解析失败"
  elif command -v nslookup >/dev/null 2>&1; then
    nslookup "$name" 2>/dev/null | awk '/^Address/ && !/#/ {print $2; exit}' || echo "解析失败"
  else
    echo "无解析工具（getent/nslookup 均缺）"
  fi
done

if [ "${1:-}" != "" ]; then
  section "指定 DNS 服务器 $1 直查"
  if command -v nslookup >/dev/null 2>&1; then
    nslookup eco.taobao.com "$1" 2>&1 | sed 's/^/  /' || echo "  直查失败"
  else
    echo "  nslookup 不可用，无法指定服务器直查"
  fi
fi

section "resolv.conf"
sed 's/^/  /' /etc/resolv.conf 2>/dev/null || echo "  无 /etc/resolv.conf"

printf '\n结论判读：NTP synchronized: no / chrony Stratum=0 / 空 Reference ID 都表示未同步；\n时间源地址与跳变方案由院方提供，本脚本不修改任何配置。\n'
