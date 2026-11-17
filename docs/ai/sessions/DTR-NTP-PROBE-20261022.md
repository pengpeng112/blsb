# DTR-NTP-PROBE-20261022

- Codex / unknown；会话20261022T150725Z-8e333909。
- 用户让试院内时钟服务器，随后纠正<relay-net-host>→<relay-net-host>；纠正发生于实际探测前，未请求错误IP。
- 范围：docs/deployment/drug-trace-ntp-20261022、docs/ai；只读连通性和时钟状态检查，无子代理。

[实测与后续网络要求](../../deployment/drug-trace-ntp-20261022/README.md)。

执行：

1. `python scripts/ai_session.py status`筛选进行中为空；start登记任务成功。
2. `python docs/deployment/drug-trace-ntp-20261022/probe_ntp.py app`：退出1，SSH退出0；<prod-host>→<relay-net-host> UDP123三次均超时，0/3。
3. 同一脚本`dmz`：退出0，SSH退出0；<relay-net-host>→<relay-net-host> UDP123三次有效，stratum1/leap0，DMZ比该源慢约2.144秒。
4. 两台chronyd均已安装/运行/开机启用，但tracking均Not synchronised；DMZ旧源203.107.6.88不通，上报主机无source。
5. 两个脚本语法、证据完整性、只读标记和配置哈希一致性、本轮链接均本地检查。无业务代码变更，不运行应用全量回归。

本轮时间设置0、服务重启0、配置改0、HIS访问0、上传0。测试使用客户端动态UDP端口，网络要求需包含对应回包，不能把UDP当TCP或要求客户端源口固定123。
原生timedatectl show在DMZ旧系统返回1，chronyc正常，可明确判断未同步；未隐藏为全部命令通过。

下一步：协调<prod-host>→<relay-net-host> UDP123及回包/客户端ACL；随后备份chrony配置并接入该源、查看^*/Normal和后续持续同步。当前仅探测，不声称已经持久配置或完成持续NTP验收。
