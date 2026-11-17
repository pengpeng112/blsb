# 10月26日批次恢复与采集稳定性修复

<!-- DTR-RECOVERY-STABILITY-20261027:BEGIN -->
## 最新阶段：本批全部确认、旧41已回写、看板与驱动已升级（2026-10-27 15:31）

截至2026-10-27 15:31，10-26 VALID 1995张：1995确认、0受理、0未发送、0发送中、0未知。旧41经新鲜官方查询全部3/51，已由原服务回写CONFIRMED；零上传、未重置12轮，原205 UNKNOWN摘要/配额/attempt数一致。当前累计4613确认/205未知，额度4820/80000。

稳定性发行13:57上线且六容器healthy，旧246/配额/断点在维护期间一致。原恢复自然在12:00因单TOP7停止，维护时没有存活driver被杀；pause文件的INTERRUPTED_FOR_STABILITY_UPGRADE是预设阶段标签，不能理解为维护造成原12:00失败。原final.json、FAILED timer回执及五次NOT_FOUND均保留。

13:59已对该唯一TOP7单做新鲜完整NOT_FOUND、冻结身份/配置/范围/无其他在途核对，ManualDecision+ReceiptEvent+AuditEvent和精确UNKNOWN revision3→PREPARED4的CAS同事务提交；经原perform_send唯一一次POST受理，14:01自动查询3/51确认。原第1次限流attempt保留，第2次受理保留，旧205与原12轮账本未清。其保护用例26通过。14:00 resumer继续剩976 PREPARED，本次运行不新采集；未知/已受理/已确认仍跳过。

最终应用全量**562通过、7跳过、0失败/错误，exit0**；[XML](../../../implementation/var/stability-regression-r2.xml)、[日志](../../../implementation/var/stability-regression-r2.log)。新增操作例外另[26项](../../../implementation/var/recovery-top7.xml)，[收尾/查询/维护工具14项](evidence/operations-tests.xml)。[镜像合成验收](evidence/packaged-runtime.json)实际Python3.14.8，零HIS/平台/生产DB；应用新Oracle全天采集性能未测。

一致性备份`<prod-path>/backups/20261027T055526Z/`；维护日志`/root/blyb-maintenance-backups/upgrade-stability-20261027T030000Z-r2/`，旧host driver为其中daily-upload.before.py。发行与driver成对回退到flow；保留新回执，不用旧DB覆盖。运行中不得重跑build/upgrade/resume/top7_send/old41_launch。

[缺码只读核查包](SOURCE-REVIEW.md)：260正常门诊+1079原1/0+3原0/2；3条SQL静态检查通过，HIS活库未执行。sjzc平台登记仍medical_dict_push，当前门禁不满足，不读取/复制凭据绕过；生产专用只读来源未因此停用。

日报展示与宿主驱动预算补强亦已部署stability-20261027T064000Z-r3，失败记录不再把未知候选冒充0，详情直显采集原因/回执进度；前端7项与18项合成浏览器通过、零业务POST，worker/API源码与r2相同。宿主driver另补排队/冷却/执行等待统一总预算，局部55通过。UI/driver升级间4818条提交、旧205、配额/断点不变。
14:46实际本地内容审计1995张/6476条码：采集快照有效行→冻结payload→SubmissionItem完全一致，payload/snapshot/source provenance和当时1576个发送业务摘要均一致，差异0；只读应用库，HIS/平台请求0。6476个Item仍PENDING仅说明未做独立码级对账，不能冒充逐码平台库存已核实。

最终驱动/应用全量：595通过/7跳过/0失败/0错误，实际退出0。[最终XML](../../../implementation/var/stability-regression-r3.xml)、[日志](../../../implementation/var/stability-regression-r3.log)、[实际命令/退出记录](evidence/final-test-result.json)。55项driver局部包含新总时限在排队/冷却/执行中的约束，已观察到的ACCEPTED/CONFIRMED/UNKNOWN/REJECTED在边界仍按真实结果返回。任务超预算不取消/重发原job，也不清原查询预算。

15:31最终内容复核：1995张全部有正式3/51回执，6476条码从采集快照到冻结payload/Item一致，1996个业务发送摘要全部一致、差异0（含原TOP7一次和受控重试一次）；原205未知仍在隔离核查中。r3于15:31:05完成，宿主driver已实际换为adbc32b1...，worker/API的7个核心文件与r2相同。

最终一致性备份`<prod-path>/backups/20261027T072904Z/`已做restore.sh --validate-only及pg_restore --list：哈希/归档安全/隔离目标与镜像校验通过，目录项553，目标未创建、生产ledger/配额不变。完整隔离恢复演练尚未执行。验证使用独立/var/tmp目标，未写生产恢复路径。

15:41复验：6容器healthy、无过期租约/新失败job/未路由事件；内网NTP选<intranet-host>且Leap Normal，DMZ复验选<relay-net-host>、Reach377/Normal、forwarder active。原timer保持active/waiting，下一次10-28 06:10；早晨失败service记录继续保留，明日分段Oracle整日性能仍待实跑。

| 核验 | 回执 |
| --- | --- |
| 本批恢复终态 | [实际证据](evidence/app-resumed_status-20261027T072422971083Z.json) |
| 旧41正式查询回写 | [实际证据](evidence/app-old41_status-20261027T072737217512Z.json) |
| r3升级与新驱动 | [实际证据](evidence/app-phase_upgrade_status-20261027T073113608183Z.json) |
| 页面/全部账本保护 | [实际证据](evidence/app-phase_verify-20261027T073121392919Z.json) |
| 运行代码与镜像SHA | [实际证据](evidence/app-phase_runtime-20261027T073127347332Z.json) |
| 1995张/6476码完整性和平台回执 | [实际证据](evidence/app-content-20261027T073136674168Z.json) |
| 一致性备份只读验证 | [实际证据](evidence/app-backup_validate-20261027T073304911592Z.json) |
| 最终主机/定时/NTP/授权 | [实际证据](evidence/app-system-20261027T074138134832Z.json) |

14:27运行检查：无过期租约、无新失败job/未路由事件，系统资源充足，timer下一次10-28 06:10；原daily-upload.service仍保留早晨collect_timeout/exit3的FAILED历史，不通过reset-failed掩盖。发送授权实际有效至北京时间11-22 00:00，80000额度，批准药房050121/050102/050101/050116/050117，业务窗口10-24至11-23半开区间；到期前需正常续批。首轮检查工具误取evidence.scope为空，修正为API顶层scope后复验真实值，原证据保留，未改生产授权。
<!-- DTR-RECOVERY-STABILITY-20261027:END -->

任务DTR-RECOVERY-STABILITY-20261027，Codex/model unknown。用户批准按前轮详细建议处理。本文件记录真实阶段；不能把RUNNING恢复、实现完成或本机测试通过视为全天业务已完成。最终部署/业务验收将追加实际证据。

## 范围与保护

优先恢复10-26已批准日批，复用已有DONE采集和PREPARED，不重新查询HIS建立新采集，不重传ACCEPTED/CONFIRMED/UNKNOWN。保留原06:10运行及09:10 FAILED回执，恢复使用source=manual日报，不覆盖timer失败事实。旧41ACCEPTED和205UNKNOWN在本批恢复期间冻结其状态/版本摘要；采集断点80/2026-10-27T00:00:00及原配额保留，成功新尝试按正常服务消耗额度。

住院/退药尚未确认规则、无码及expected_code_count为空仍是业务/来源边界；本轮不改HIS/视图、不补猜值域、不改旧payload/已批准字典。缺码的源端取证应继续走sjzc规则及已有等价授权范围，本阶段没有额外查询HIS。正式证书/IP SAN、码级对账和未报历史人工记录等不因本轮自动验收。

## P0实际恢复

10:17只读实测：原采集已于09:47:30自然结束，3337张（1995 VALID、1342 WAITING），窗口completed=true；断点80。旧任务3小时超时先于采集完成，但未丢采集数据。后台当时已准备656张，其余准备job继续处理。

10:20启动一次性`blyb-recovery-stability-20261027.service`，日志`/var/log/blyb/recovery-stability-20261027/`。运行前固定发行/driver SHA、日期、DONE窗口及1995 VALID、发送授权；克隆普通driver，仅改source=manual并显式阻止POST新collection。仍经过原发送API、worker证据/来源/配置闭包/配额/CAS和共享pacer。

真实进度：10:36时170次新HTTP200受理、147确认；10:50时313次受理、290确认，未新增UNKNOWN，旧246摘要/源断点不变。该数字是当时进度，不是最终1995张结果。一次性runner/目录防重复启动，正常timer不重复创建；维护中断和恢复分目录保留。

## 实现的稳定性改进

1. Oracle采集每个job执行25张的一页，提交整页快照、血缘、质量、outbox、断点及进度后让出原job；下一次复用该job/epoch链，不花失败attempt。整单明细、版本复核、复合键和前导零规则保持。后续页不重复600秒回看，不清掉同一时间戳下的复合键。
2. 每页由同一warehouse会话复用一个Oracle连接，页结束或异常关闭；单独fetch仍独立连接。记录connect/query次数及累计耗时，不存SQL参数/患者/码串。不自动重试失败源查询，不删除版本复核；生产完整日批性能需后续实测，不能承诺仅靠复用连接就满足07:30目标。
3. 采集执行期独立数据库会话每20秒按原id/epoch/LEASED/未过期条件续租，源读取前后更新进度。300秒无进度则失败关闭，不无限刷新健康状态。长任务写busy心跳，常规主循环仍保留原心跳。
4. 在页事务提交前对同一job做条件UPDATE锁住epoch/有效租约，失租整页回滚、不把新owner的run标FAILED。正常完成只有finish_job成功才更新操作成功；退出guard已失租也不冒充成功。后台续租线程不共享主Session。
5. 新JobYield与RequestDeferred分开：前者表示已持久化部分工作，后者仍严格表示本次HTTP未发生。共享TOP7/429等待、一次POST一次结果、UNKNOWN先查、12轮账本规则不变。
6. driver采集等待上限改为4小时，已有分段进度20分钟不变则显式collect_progress_stalled；失败日报保留run/state/summary/error_code和candidate_count_known=false，不把未进入候选阶段误当无数据。总运行在5小时50分预算边界前停止新发送/查询并落回执，与现有unit6小时保护协调，不无限延时。

实际源码：worker.engine、workflow_core.jobs/lease_guard、drug_trace.service/plugin/ingest、db_view.drug_trace，共7个发行差异；另有受SHA保护的宿主daily_upload_driver。无业务库DDL/编号迁移，无API新路由/权限放宽。新增进度/性能/等待code均存既有JSON，旧历史记录不回填覆盖。

## 验证状态

局部既有回归108通过/1环境跳过；找到现有D盘Git/openssl后专项81通过，含真实隔离PG。分段/连接/租约/driver新增测试还覆盖过期/抢占、整页回滚、同job多页、独立线程私有Session、异常连接关闭和零副作用恢复约束。首轮全量560通过/7跳过；其后补强guard结束失租与旧epoch不可标成功，最终全量562通过/7跳过/0失败或错误，exit0，以stability-regression-r2.xml为准。

实际发现并修正：零秒lookback仍清复合键导致同刻页反复重扫，被新测试拦下；Windows导入Linux专用fcntl时失败，移到runner.main中；部署生成器先嵌入大字符串再全局替换OLD会破坏COOLDOWN字样，compile闸门在本机拦下，已改为先替换模板、最后嵌入源码。失败证据保留，未用失败脚本部署。

初版不可变stability-20261027T030000Z包已构建/传输但未上线；补强后的最终包为stability-20261027T030000Z-r2。两包都保留，不覆盖旧包。离线构建依赖pip freeze一致、白名单不含运行库/凭据，manifest仅7个允许差异；最终包SHA传输通过后才允许维护升级。

## 维护和恢复约束

最终回归通过后，停止普通timer，按固定PID/命令暂停该一次性runner及容器内driver，等待SENDING/inflight/pending_send连续5次为0。保留原未完成回执和maintenance-interruption证据，停止原driver；非发送prepare/query按数据库事务和原租约安全重启，不丢弃队列。

走现有upgrade.sh一致性备份→不可变发行/镜像→迁移健康门禁，替换已校验宿主driver，恢复timer和独立resumed runner。恢复原10-26日期并复用PREPARED，已受理/确认/未知跳过；恢复结束后只读等待最多30分钟核平台终态，1995张全部CONFIRMED才写ALL_CONFIRMED，遇未知/拒绝/部分成功明确转核查。

回退须同时恢复flow发行和宿主daily-upload.before.py，不把旧业务库覆盖新回执；原批次失败、维护中断及恢复报告均保留。新进度JSON旧代码可忽略，pending job不删除。DMZ保持前一版本，本轮无需更改前置机、网络、证书或NTP。

## 旧41张成功回写

已准备固定41个历史ID的query-only工具，入口强制等待当前1995张恢复ALL_CONFIRMED。每张重新用原冻结身份查询，经原perform_query保存实际回执/确认，再写人工决策和审计；无upload能力，不清原12轮job。执行状态以页首最新阶段及old41实际证据为准；只有ALL_41_CONFIRMED才算回写完成。205 UNKNOWN没有批量重发授权清单和人工历史核对，不在本次日批恢复中盲补。
