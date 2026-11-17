# FB-06 首张真实上传结果（DTR-FIRSTBILL-EXEC-20261021）

## 直接回答

- **实际上传：0 张。**
- **是否最终成功：未执行（BLOCKED，非失败）**——发送所需的真实单据不存在
  （生产采集链未解锁），发送证据门禁按设计保持关闭。
- **逐码核对：未发生（无单可核）**；本轮完成的只读查询为探针单号
  （total_num=0），不构成任何业务对账。

## 为什么 BLOCKED（逐项）

| 发送前置 | 状态 | 阻断点 |
|---|---|---|
| 真实单据（整单采集） | **缺** | r7 视图未建/只读账号未批/DSN 空（[DBA-PACKAGE](DBA-PACKAGE.md)）；HIS 活库按登记策略阻断 |
| EXT-03 DMZ→平台出口 | 已具备 | 10-21 实测 HEAD200/TLS0（既有证据） |
| EXT-04 正式域名/SAN/mTLS | **缺** | 现网 TEST-ONLY 链；正式院方 CA=院方输入 |
| EXT-05 校时 | 部分 | <prod-host> 与平台差≤10s（实测）；但 NTP 未同步、院内源缺 |
| EXT-06 权限包/有效期/绑定 | **缺** | uploadretail 权限未验（查询成功≠上传权限） |
| EXT-07 签名资格 | **已闭合** | 三源对照+平台受理（[SIGNATURE-QUALIFICATION](SIGNATURE-QUALIFICATION.md)） |
| EXT-08 bill_type 字典 | 部分 | 321 已按官方指南批准；323 冲突待平台消歧 |
| EXT-10 既往已报核对 | **缺** | 院方/原厂商未答复 |
| EXT-12 首批 scope | **缺** | 未报单据范围/药房/窗口/max_bills=1 未获院方签认 |
| DMZ 上传方法白名单 | 未开（设计如此） | 仅 query.billstatus；无必要不先开 |

系统内证据状态（升级后实测）：query=APPROVED；collect/send/reconcile 均
未登记（reconcile 与 query 同键 EXT-09 但本轮只登记了 query 能力）。
**未发送任何伪造单据/假码测试权限，未改 bill_code，未手改任何门禁。**

## 已为「下一张」准备就绪的能力（实测在位）

- 真实目的地配置已发布（PUBLISHED rev1，is_mock=False）+ 官方字典（321/A）
  + EXT-09 证据；正式链路查询全通（worker→mTLS→DMZ→平台 SUCCESS）。
- 发送编排链（冻结/幂等/配额/世代/UNKNOWN 先查/对账）经 405 项回归验证。
- 解锁路径：DBA 交付包落实 → 30-verify 限量核验 → SourceProfile r3 →
  DSN 注入 → collect 证据（EXT-01/02）→ 小窗口采集 1 张 → EXT-04/06/10/12
  逐项闭合 → DMZ 白名单加 uploadretail → max_bills=1 发送 → 终态查询核对。

## 配额/授权状态

send quota 0 条（无 send 证据即无台账）；本轮平台调用仅 3 次只读查询
（2 次诊断脚本 + 1 次 worker 链路），无上传、无配额消耗、无患者数据。
