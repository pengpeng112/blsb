# 首张真实试报成功（2026-10-22）

**已实际上传1张单据、4个追溯码，平台终态为处理成功/全部成功；本地CONFIRMED。**
用户明确要求“先都忽略，然后推送一条数据测试”，随后要求“不用撤销，发送即可，后续没问题再启用”。
本轮使用现有发行与正式API执行，没有另写旁路上传器、改业务源码、伪造平台响应或重发。

## 实测结果

| 项目 | 结果 |
|---|---|
| 发行 | <prod-host>仍`src-20261022T103000Z`，不再重新部署应用 |
| 发前来源复核 | 专用只读账号读取同一单；当前HIS内容摘要与冻结快照一致，4码/VALID |
| TLS与时间 | worker严格mTLS健康200，DMZ→官方HTTPS200；签名worker与官方Date偏差约-3.339秒 |
| 上传方法 | `alibaba.alihealth.drugtrace.top.yljg.uploadretail`，沿用批准的bill_type=321 |
| 上传次数 | **1次**，发送job SUCCEEDED/attempts=1，HTTP200/ACCEPTED，无重发 |
| 平台查询 | 先`result_type=0`处理中；后`result_type=3`、`sub_process_flag=51` |
| 本地终态 | **CONFIRMED**，对应平台处理成功/全部成功 |
| 单笔额度 | used=1 / max_bills=1；未通过重建审批或修改计数增加额度 |
| HIS写入 | **0** |
| 后续配置 | 依用户新指令保留，不主动撤销、不恢复query-only；自动批量发送未开启 |

真实操作约在北京时间22:18—22:20完成。依据：

- [发前检查](evidence/preflight_worker-141647.json)：冻结内容一致、严格mTLS、当前时钟、数量及既往未核实事实。
- [DMZ方法](evidence/dmz-policy-141755.json)：仅query+uploadretail；`enable_inout_upload=false`。
- [单笔例外登记](evidence/approve-141835.json)：正常admin API审批和目的地启用，未直接SQL改状态。
- [发送入队](evidence/send-141912.json)：系统API HTTP200仅代表入队，不单独据此判上传成功。
- [受理与处理中](evidence/status_worker-141935.json)：真实attempt ACCEPTED、平台查到1条、状态0。
- [最终确认](evidence/status_worker-142041.json)：真实回执3/51、本地CONFIRMED，发送/查询job均SUCCEEDED。

## 定位标识

- submission：`7ed95aaa-ee66-4510-b80d-630f6c9dc381`
- package revision：`93fc0afb-b068-471a-bd3a-4b03f72799a1`
- destination：`345df8f4-410e-4c89-a258-a34957eb4151`
- 冻结目的地配置：`2e138e50-2be7-4574-8e48-61264be8946d`
- 发送job：`ffed1591-1019-4210-88ea-c3bdfaa70738`
- 本次审批：`0eb0b906-3a3d-497b-abba-62117dd1e672`

真实码及原始HIS单号未输出到报告或聊天；已在受控应用数据库留存完整冻结、attempt及receipt。

## 用户例外怎样记录

沿用系统已有证据审批API，为选定冻结单登记**USER_SINGLE_BILL_EXCEPTION**：
每个提供项保存disposition、用户本次指令、指定submission、实际检查或延期说明；
scope固定该单1秒窗口、实际药房、max_bills=1，并绑定准确冻结目的地配置。
通用APPROVED状态表示此范围获准发送，不表示延期的生产标准已实测完成。

- 正式医院CA：DEFERRED_BY_USER；明确现有链TEST-ONLY，未声称已换正式CA。
- 持续NTP：延期，发前实际时钟核验通过；未声称chrony已同步。
- 上传权限：登记时UNKNOWN/待本次调用验证；**随后实际受理且处理成功，证明本次uploadretail调用有效**。
- 字典：原批准321及医院映射继续使用；此次321真实处理成功，不再把等待321书面回复作为同类试报阻断。
- 旧厂商既往是否已报：UNKNOWN，用户明确延期核对；不把新平台单号查无记录当未报证明。
- 实际授权：仅本次这一张。未修改冻结包、原批准字典或必需项常量，未删除任何防重复/额度/租户校验。

审批当前未撤销；有效期至`2026-10-22 23:18:32`北京时间，保留已有到期机制，记录会继续保留。
DMZ上传方法与目的地配置保留；此单额度已耗尽。用户尚未授权扩大自动批量范围，不能直接增大额度或循环发历史数据。

## 已证实和仍有限制的区别

本次已证实：真实来源取数/整单、冻结映射、签名、mTLS/网络、该AppKey与医院的uploadretail能力、
321对该笔业务可用、平台异步受理与终态查询闭环。后续不再重复索要这些已验证的凭据或说“未发过真实数据”。

仍未整体验收：持续NTP、正式CA、历史补报和旧系统去重、住院/退药/拆零/晚到变更、自动调度。
平台3/51是本次整单全部成功的依据；没有另外调用独立码级核对接口。
**已登记的小项**：本地4条SubmissionItem仍PENDING，而整单CONFIRMED；不能把本地明细状态当成平台失败，
也不能手工SQL改明细冒充逐码独立对账。后续完善UI的整单确认与码级核验区分。

本轮仅运行脚本语法编译及上述真实受控联调，没有重跑源码回归；源码未变更。
控制脚本无自动上传循环，发送入口只调用一次。结果未知时只查状态，本轮无需处理未知重试。
