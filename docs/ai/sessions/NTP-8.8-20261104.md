# NTP-8.8-20261104

OpenCode；模型 deepseek-v4.1-flash；会话 20261104T093043Z-78e05eeb。记录时间 2026-11-04 17:30 CST。

## 结果

<frontend-host>（ogserver，openEuler 22.03 LTS-SP3）已从默认 `pool.ntp.org` 改为内网 `server <intranet-host> iburst`。chronyd enabled/active，选中 `^*`，Leap Normal，同步后偏差约 17 纳秒，`timedatectl` synchronized yes。首次偏差 17.6 秒由 chronyd 自动步进。

[运维实录](../../operations/ntp-<frontend-host>-20261104/README.md)

## 实际操作与真实结果

| 步骤 | 命令/方式 | 结果 |
|---|---|---|
| 跳板登录 | `ssh -i id_ed25519_med_audit -p <ssh-port> root@<frontend-host>4` | exit 0；openEuler 22.03 SP2 |
| 基线 | 经跳板二次 SSH 到 <frontend-host> 执行只读脚本 | chronyd disabled/inactive；NTP inactive；默认 pool.ntp.org |
| 源探测 | UDP123 手工 NTP 包到 <intranet-host> | 48 字节响应，stratum 1，leap 0；ping 约 1.3 ms |
| 备份 | `cp -a /etc/chrony.conf /root/blyb-ntp-backups/20261104T172856/` | 旧 SHA256 `f18b31e1…301dba` |
| 改配置 | `sed` 替换 pool 行为 `server <intranet-host> iburst` | 第 3 行生效，其余未改 |
| 启用服务 | `systemctl enable chronyd; systemctl restart chronyd` | enabled / active |
| 校准 | chronyd 按 `makestep 1.0 3` 自动步进 | Last offset -17.612125397 s |
| 复验 | `chronyc sources/tracking`、`timedatectl` | `^*`、Reach 17、Leap Normal、synchronized yes |
| 清理 | 删除跳板机 askpass/辅助脚本/ControlMaster socket | `ls` 确认不存在（exit 1 为“未找到”，符合预期） |

## 未做与后续

- 未改业务服务、防火墙、DMZ、<prod-host>、<relay-net-host>；未把 8.8 配成 NTP 服务端。
- RTC 由 `rtcsync` 周期校正，验证时 RTC 比 UTC 快约 8 秒，属正常同步窗口。
- 若内网时钟源调整，只需改 `/etc/chrony.conf` 的 server 行并重启 chronyd。
- 密码与跳板凭据未写入仓库、文档或提交；临时脚本已删除。

## 范围

`docs/operations/ntp-<frontend-host>-20261104`、`docs/ai/sessions/NTP-8.8-20261104.md`。无应用源码、镜像、表结构更改。
