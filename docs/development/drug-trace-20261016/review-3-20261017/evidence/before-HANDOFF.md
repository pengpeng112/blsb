# 当前交接

最后任务：**DTR-REVIEW2-20261017 · 药品追溯二次代码复核与完善计划**。
工具Codex；模型unknown；源码基线0ca47c6。完整记录见[sessions/DTR-REVIEW2-20261017.md](sessions/DTR-REVIEW2-20261017.md)。

## 本轮结论

当前不能正式上报。上一轮已有离线代码/277 passed与6 skipped，但独立复核发现官方参数/响应、部分误确认、Mock签名豁免、配置未固定、Oracle数据形态/游标、暂停与UNKNOWN重发、查询代次/回执、无远端对账、mTLS/DMZ运行与恢复一致性等缺陷。不能把这些问题全部归为外部材料未齐。

本轮重跑原domain/api/ops/dmz局部测试 **47 passed、0 failed、0 skipped**；另 **16个离线反例全部复现**。前者说明旧测试通过，后者说明覆盖遗漏，两者不矛盾。没有重跑全量，没有读凭据、连接真实HIS/DMZ/<prod-host>、发送真实业务或修改业务源码。

## 下一AI执行入口（2026-10-17 DTR-FIX2 后）

**离线整改已完成**：FIX2-00—08 全部可离线项交付（R2-01—16 处置矩阵见
[fix2-20261017/README.md](../development/drug-trace-20261016/fix2-20261017/README.md)）。
官方协议/证据门禁/状态机 CAS/查询代次/平台快照对账/DMZ 白名单/备份恢复保护/
父子码与 tenant 隔离已按正确行为实现并测试（转换测试 26 项 + dmz 2 项 + 浏览器闭环）；
迁移 010 已在真实隔离 PG 验证。**这是「离线代码完成」——现场验收与正式业务签认均未发生。**

下一步分两类：
1. **现场（SITE2-01）**：EXT-01—05/11 齐 + 维护窗口 → <prod-host> 升级（含 010）→
   DMZ 部署（Python≥3.7 el7 运行时材料 + SHA256SUMS + systemd AmbientCapabilities
   实机核对）→ 禁发状态本地模拟上游合成闭环；真实隔离 PG 完整 backup→restore 演练。
2. **首批真实（LIVE2-01）**：EXT-06—10/12（SDK 向量核对一致后才可动
   SIGNATURE_VERIFIED，须附证据修订；平台查询/码级能力；历史补报范围；首批授权
   scope 登记）→ 首批单据→终态→三方核对→院方签认。

未决实现小项：EXT-10 明确后历史补报与增量 checkpoint 分离；发送 scope 药房/
批量上限维度（当前实现窗口维度）；UI keyset 分页升级。

## 证据与历史

- [独立反例](../development/drug-trace-20261016/review-2-20261017/evidence/probe-results.json)
- [本轮局部XML](../development/drug-trace-20261016/review-2-20261017/evidence/local-regression.xml)
- [上一开发者交接](sessions/DTR-IMPLEMENT-20261016.md)保留原文；其“全部完成/硬阻断/三方对账”受到本轮新证据限制。

病理B01—B09、tenant/owner、0/1/N、前导零、患者就诊分离、固定revision及dry-run零真实副作用继续有效。修复代码、现场验收、业务签认分开报告。
