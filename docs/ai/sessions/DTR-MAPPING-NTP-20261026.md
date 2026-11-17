# DTR-MAPPING-NTP-20261026

Codex；模型unknown；会话20261026T040317Z-660db0e1。最新快照2026-10-26 12:11:52。

## 最新：两药房映射与内网NTP已修复，补发运行中（2026-10-26 12:11:52）

[本轮运维实录](../../operations/drug-trace-mapping-ntp-20261026/README.md)：用户确认050121、050116属于本院，新增批准ref_ent_id字典hospital-pharmacies-20261026-r1，旧字典/历史payload未改。未来日批可使用两药房映射。
<prod-host>已改为<intranet-host> UDP123，选中^*、Reach377、Leap Normal、chronyd enabled/active；DMZ仍用正常的<relay-net-host>。旧“待确认药房归属”和“<prod-host>校时未通”已由本轮证据解决。
上午567 PREPARED恢复已结束：567全部HTTP200受理，原回执因782缺映射为PARTIAL_FAILURE。现单独blyb-mapping-recovery-20261026按10-25（782张）→10-24（815张）顺序恢复，只复用DONE采集；当前22受理/16确认，仍RUNNING，不称1597张完成。旧41受理/206未知及断点46均不变，配额不重置，普通每日timer保持enabled。
观察<prod-host> /var/log/blyb/mapping-recovery-20261026/progress.json、final.json、error.json；用本轮check.py app status实时核验。窗口driver完成不等于所有平台终态确认；日报send为本次调用计数。
看板https://<prod-host>/ui/#/drugtrace使用访问令牌，无独立用户名/密码；用户所需令牌已存本机ACL受限文件，路径见运维实录。当前电脑443仍TLS握手失败、80未开，本轮未启用HTTP；HTTP可另行作为院内同源入口，但明文传输令牌，平台/DMZ HTTPS/mTLS仍保留。
本轮无应用源码/镜像/表结构更改，语法检查与API登录/字典实际查找/NTP/补发实测；未重跑全量回归。旧247平台核对、看板证书与访问、outcomes先limit后筛问题仍待办。[交接](DTR-MAPPING-NTP-20261026.md)。


范围：docs/operations/drug-trace-mapping-ntp-20261026、共享docs/ai、catalog、码上放心README。独立私有登录资料会话20261026T040656Z-cc1522a2已completed，私有交接保存在资料目录，未读取托管数据库凭据。

应用源码/镜像/表结构零更改；旧保护数据/配额/审批未绕过。真实命令/退出码、备份、证据和后续路径均见[运维实录](../../operations/drug-trace-mapping-ntp-20261026/README.md)。

本任务配置与校时实测完成；补发仍在后台RUNNING、HTTP未启用、浏览器访问未通过，所以阶段结果handed_off。下一棒先check.py app status或读取final.json/error.json；不要重启已运行任务或并发补跑。若用户选择HTTP，单独处理同源nginx管理入口，保留DMZ平台TLS，不开放后端直映。
