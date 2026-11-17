# DTR-ONE-BILL-TRIAL-20261022

工具/模型：Codex / unknown；session 20261022T141220Z-71096f38；结果completed。

## 授权

用户明确要求“先都忽略，然后推送一条数据测试”，执行中又明确“不用撤销，发送即可，我看看后续没问题就启用了”。
仅执行所选冻结单的一次真实试报；保留配置，不扩大自动批量范围。现有TLS校验、固定配置、单据范围、额度与幂等不删除。

## 实际执行

- 使用原固定SSH指纹/加密凭据入口，未读取或输出托管凭据内容；原业务源码/发行/依赖均不改。
- 编写本轮受控操作脚本并执行语法编译，退出0；没有运行或声称新增源码回归。
- run_trial.py preflight：退出0；只读复查同单HIS摘要等于冻结，4码VALID；strict mTLS health200；worker时钟偏差-3.339秒，HIS写0/上传0。
- run_trial.py dmz-enable：退出0；先保存/root/forwarder-before-single-trial-20261022.json，再校验并原子更新forwarder.json，仅增加uploadretail，query保留、库存关闭；服务active。按用户最新指令未回退该配置。
- run_trial.py approve：退出0；经现有API登记8项带实际disposition的USER_SINGLE_BILL_EXCEPTION；延期项未标实测完成，旧厂商状态保持UNKNOWN；冻结目的地配置精确绑定、该单一秒窗口/药房/max_bills=1，有效1小时；目的地经API置ENABLED。
- run_trial.py send：退出0；仅一次POST send，job ffed1591-1019-4210-88ea-c3bdfaa70738。
- run_trial.py status：退出0；真实attempt1/HTTP200/ACCEPTED，平台查询0处理中；继续原系统自动查询，不重发。
- 再次status：退出0；平台result_type3/sub_process_flag51，本地CONFIRMED。两查询job和发送job均SUCCEEDED，配额used1/max1，审批未撤销。
- 更新STATE/BACKLOG/HANDOFF、四类实施文档和外部清单；结果/result.json及全部脱敏receipt留存。

## 结果和边界

首张真实上传成功：1单4码/平台全部成功/本地CONFIRMED，没有重发、无HIS写入。
source/destination配置、DMZ方法及审批记录按用户要求保留；审批有原有到期机制，至北京时间2026-10-22 23:18:32；不是主动撤销。
未开启批量自动发送；后续范围/额度由用户进一步安排，不能重置已用计数或重发此单。
平台权限和321本笔已真实验证；正式CA、持续NTP、历史去重/住院退药拆零/变更发现仍属后续完善。
4条SubmissionItem本地仍PENDING，已登记P1显示/核验边界；整单3/51确认属实，独立码级对账未另做，未手工改明细状态。

[完整结果与证据](../../development/drug-trace-one-bill-trial-20261022/README.md)。
