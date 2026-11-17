# DTR-NTP-CONFIGURE-20261023

- 工具/模型：Codex / unknown；会话20261022T232907Z-7e42c016。
- 当前用户明确授权“请你修改 同步时间”；目标为<prod-host>和<relay-net-host>，已纠正的院内源<relay-net-host>。
- 范围：docs/deployment/drug-trace-ntp-20261023、docs/ai；真实运维动作仅两台chrony配置、备份及chronyd重启。
- 先读共享状态/交接并查看运行登记为空；没有启动子代理。

## 实际结果

[执行说明与结果](../../deployment/drug-trace-ntp-20261023/README.md)。
两台配置均备份后持久写入`server <relay-net-host> iburst`，原公网pool注释保留，其余配置保留。
各只重启chronyd一次、active/enabled；没有手工date改钟，沿用既有makestep 1.0 3策略。

- DMZ：已选`^* <relay-net-host>`，tracking Normal/stratum2；初次直接探针约0.6ms，约两分钟后再次复核约0.01ms、Reach77（八进制），实际保持同步。
- <prod-host>：配置生效但仍^?/Reach0/Not synchronised，实际UDP123无响应；不把它写成同步成功。
- DMZ转发PID5174、Java14438未变；<prod-host>六容器healthy。业务重启0、HIS查询/写入0、平台业务上传0。
- 备份：DMZ `/root/blyb-ntp-backups/20261023-nnbswaix/chrony.conf`，app `/root/blyb-ntp-backups/20261023-5bb2a0u8/chrony.conf`。

## 命令与失败处置

- `python scripts/ai_session.py status`：退出0，在运行任务为空；start登记成功。
- `python docs/deployment/drug-trace-ntp-20261023/run_ntp.py app inspect`及dmz inspect：均退出0；app三个原始UDP探针超时，DMZ三个成功，独立记录，不用SSH成功冒充NTP成功。
- dmz首轮apply：退出1。chrony3.4不支持-p，预校验未过，**未改配置/未重启**；备份已留，错误回执保留。
- 读取远端`chronyd -h`以及chrony官方3.4/4.1文档，改为版本适配：3.4用-Q只测偏差并隔离PID/服务端口；4.1用-p解析。validation-only选项不安装到实际配置。
- dmz第二轮apply：退出0、CONFIGURED_AND_SYNCHRONIZED；app apply退出0、CONFIGURED_AWAITING_NTP_RESPONSE，退出0只代表配置流程成功，不代表同步通过。
- 两台verify均退出0，回执状态分别synchronized=true/false；DMZ复查两次，最终短期采样正常。原配置与备份/应用后哈希均记录在evidence。
- 对4个脚本做语法解析，检查最终配置哈希与apply一致、源唯一正确、备份和证据链接有效、业务状态正常；本轮无业务源码更改，不运行应用全量测试。

## 下一步/外部依赖

<prod-host>至<relay-net-host> UDP目的123及对应动态客户端UDP端口回包，检查跨区策略或NTP服务端客户端ACL；无法仅凭超时认定具体阻断设备。
网络开通后chronyd仍会自动轮询，可执行`chronyc online`和`chronyc burst 4/4`加速再观察，验收^*/Reach/Normal与时间偏差。
无需新建NTP转发服务或开放DMZ为额外时间服务器。保留当前受控配置，不为“全通过”虚构<prod-host>已同步。
本轮配置及DMZ同步完成，<prod-host>同步未完成，交接状态handed_off，待网络恢复后核验。
