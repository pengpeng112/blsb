# socket activation 说明（随发行包部署到 /opt/blyb-dmz/current/）

## 为什么用 socket 托管 443

DMZ 主机为 Anolis OS 7.9 / systemd 219（2026-10-20 实测），且**没有系统 python3**。
本服务进程以专用非 root 用户 `blyb-dmz` 运行；TCP 443 的监听 socket 由 PID1
（systemd）持有，服务启动时通过 FD 传入（sd_listen_fds 语义）。这样：

- 进程不需要 `CAP_NET_BIND_SERVICE`（也不需要 systemd ≥229 的 AmbientCapabilities）；
- 不对任何系统 Python 做 setcap，不升级整机 systemd/OpenSSL；
- 服务崩溃由 systemd 按 Restart 策略拉起，socket 期间连接排队不丢；
- 停止/回退必须**同时控制 socket 与 service**（见下）。

## 服务身份

- unit：`/etc/systemd/system/blyb-dmz-forwarder.socket` 与
  `...service`（由安装脚本从本包模板渲染）。
- 解释器：`/opt/blyb-dmz/runtime/bin/python3.11`（随包私有运行时，
  python-build-standalone cpython-3.11.9，自带 OpenSSL 3.0.13，
  centos:7/glibc2.17 已实测 ssl/SNI/CA）。
- 日志：journal（`journalctl -u blyb-dmz-forwarder`）。systemd 219 无 per-unit
  journald 限额，全局保留策略不修改，亦无 logrotate 依赖。

## 运维命令

```bash
# 状态/校验
/opt/blyb-dmz/current/install.sh --status
/opt/blyb-dmz/current/install.sh --verify                # 本地严格验收（非零=失败）
/opt/blyb-dmz/current/install.sh --verify --with-upstream # 额外探测上游；出口码4=LOCAL_READY/UPSTREAM_BLOCKED
/opt/blyb-dmz/current/install.sh --diagnose              # 分层诊断 JSON

# 升级 / 回退 / 卸载
/opt/blyb-dmz/current/install.sh            # 首装或升级（原子切换，失败自动回退）
/opt/blyb-dmz/current/install.sh --rollback
/opt/blyb-dmz/current/install.sh --uninstall [--purge]
```

**停发/暂停**（不卸载）：必须先停 service 再停 socket，否则 socket 会把
service 重新拉起：

```bash
systemctl stop blyb-dmz-forwarder.service blyb-dmz-forwarder.socket
# 恢复：
systemctl start blyb-dmz-forwarder.socket blyb-dmz-forwarder.service
```

## 健康语义

`GET /health` 仅表示本地进程/TLS 可服务，不冒称上游正常；上游连通性用
`--verify --with-upstream` 或 `--diagnose --diagnose-upstream` 单独探测。
/health 与业务 POST 一样要求 mTLS 客户端证书（本机运维用 ops-probe 证书，
不复用 <prod-host> 客户端私钥）。
