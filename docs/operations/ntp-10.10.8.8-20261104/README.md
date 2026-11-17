# <frontend-host> 前置机 NTP 校时（2026-11-04）

## 结论

<frontend-host>（主机名 `ogserver`，openEuler 22.03 LTS-SP3，aarch64）已改为仅使用内网时钟源 `<intranet-host>`：

- `/etc/chrony.conf` 的默认 `pool pool.ntp.org iburst` 已替换为 `server <intranet-host> iburst`。
- `chronyd` 已设置开机自启并处于 active；`chronyc sources` 选中 `^* <intranet-host>`，`Leap status: Normal`。
- 首次同步实测系统时间快 `17.612125397` 秒，由 chronyd 按原有 `makestep 1.0 3` 自动步进，**未手动执行 date/makestep**。
- 校准后 `System time` 偏差约 17 纳秒，`timedatectl` 显示 `System clock synchronized: yes`、`NTP service: active`。

未重启任何业务服务，未改防火墙、DMZ、<prod-host> 或 <relay-net-host>。

## 变更前基线

| 项目 | 实测值 |
|---|---|
| 时钟服务 | chrony 4.1 已安装；`chronyd` disabled / inactive |
| 配置 | `/etc/chrony.conf` 仍为默认 `pool pool.ntp.org iburst` |
| 同步状态 | `timedatectl`：NTP service inactive，System clock synchronized: no |
| 目标源连通性 | `ping <intranet-host>` 约 1.3 ms；UDP123 探测返回 48 字节，stratum 1，leap 0 |
| 防火墙 | firewalld 未运行；SELinux Disabled |

## 变更内容

| 项目 | 值 |
|---|---|
| 备份 | `/root/blyb-ntp-backups/20261104T172856/chrony.conf` |
| 旧配置 SHA256 | `f18b31e1b333529de95f13e9a3d30fa880b63edb9e58b595d097b230c7301dba` |
| 新配置关键行 | `/etc/chrony.conf` 第 3 行 `server <intranet-host> iburst` |
| 新配置 SHA256 | `4833c29cce6ebb9f5d6ac9bf2a7eb6412a7c58a75822f510e93fdd5230dd97c1` |
| 服务 | `systemctl enable chronyd`、`systemctl restart chronyd` |

其余 chrony 配置项（driftfile、makestep、rtcsync、logdir 等）保持原样；未配置 `allow`，本机只作 NTP 客户端，不作服务端。

## 验证证据（2026-11-04 17:29–17:30 CST）

```text
MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
^* <intranet-host>                  1   6    17    41   +821ns[  -17us] +/- 1540us

Reference ID    : 0A0A0A6F (<intranet-host>)
Stratum         : 2
System time     : 0.000000017 seconds fast of NTP time
Last offset     : -0.000017396 seconds
Leap status     : Normal

System clock synchronized: yes
NTP service: active
```

`systemctl is-enabled chronyd` = enabled，`systemctl is-active chronyd` = active。

## 回滚

```bash
cp -a /root/blyb-ntp-backups/20261104T172856/chrony.conf /etc/chrony.conf
systemctl restart chronyd
```

旧配置指向 `pool.ntp.org`，院内网络不可达，回滚会重新出现未校时状态，非必要不回退。

## 访问与证据边界

- 经跳板机 `<frontend-host>4:<ssh-port>` 两跳 SSH 到 `<frontend-host>`；密码只在会话内使用，未写入本目录或聊天记录。
- 跳板机上的临时 askpass 脚本、辅助脚本和 ControlMaster socket 已全部删除。
- 命令与输出摘要见 [会话记录](../../ai/sessions/NTP-8.8-20261104.md)。
