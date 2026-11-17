# DTR-RATE-LIMIT-CAUSE-20261026 交接

Codex/model unknown；session20261026T122448Z-7d63f104。只读分析，无应用源码/配置/镜像/视图变更。

## 最新：限流原因已分层定位（2026-10-26 20:30:23）

[原因实证与后续整改](../../operations/drug-trace-rate-limit-cause-20261026/README.md)：10-25的HTTP429来自DMZ本地per-IP 60/min、burst10；固定失败窗6903次请求/3679本地拒绝，峰194/min、14/s。旧247共2964查询job放大请求量，旧transport无pacing；本地限速已于10-25修复，当前1.5s/40min并HTTP429冷却，修复后固定窗0本地429。
10-26的HTTP200/TOP7为平台返回accesscontrol.limited-by-api-access-count。官方通用解释是该API所有应用共享的秒/分钟总频率限制，**不能据此断言本院AppKey日额度耗尽**；前一棒建议查应用额度只是待确认方向，现按子码应优先确认平台接口总流控。具体阈值/禁用秒数/医药业务覆盖策略未获确认。
当前缺口实测源码确认：transport只因HTTP429立即冷却，TOP7未建立跨单据共享冷却；query解析未留sub_code/sub_msg；QUERY_ERROR统一120s退避/12轮、进程内预算不跨维护进程共享。建议独立整改，**本轮未修改或部署**。DMZ来源均<relay-net-host>，NAT是否还共享其他系统未核实，不把全量请求都归于<prod-host>。
本轮仅SSH日志聚合/应用库只读SELECT及官方文档检索，平台请求/HIS/发送/业务写入0。既有41平台成功/206 NOT_FOUND仍未本地回写或补报。[交接](DTR-RATE-LIMIT-CAUSE-20261026.md)。


实际命令两条check.py dmz dmz、check.py app app均exit0；内部journalctl/psql也exit0，结果与代码核对。官方原文访问及精确子码含义已登记。未测试应用回归，无应用变更不冒充新增功能通过。后续整改优先保留TOP子码、共享冷却与限流错峰/低频查询；不要增大DMZ限额或换AppKey绕过，不重跑未知补发。
