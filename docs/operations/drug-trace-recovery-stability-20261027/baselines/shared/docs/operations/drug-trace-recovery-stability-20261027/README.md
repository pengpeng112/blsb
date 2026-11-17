# 10月26日批次恢复与采集稳定性修复

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

局部既有回归108通过/1环境跳过；找到现有D盘Git/openssl后专项81通过，含真实隔离PG。分段/连接/租约/driver新增测试还覆盖过期/抢占、整页回滚、同job多页、独立线程私有Session、异常连接关闭和零副作用恢复约束。首轮全量560通过/7跳过；其后补强guard结束失租与旧epoch不可标成功，最终全量运行中，以最终XML为准。

实际发现并修正：零秒lookback仍清复合键导致同刻页反复重扫，被新测试拦下；Windows导入Linux专用fcntl时失败，移到runner.main中；部署生成器先嵌入大字符串再全局替换OLD会破坏COOLDOWN字样，compile闸门在本机拦下，已改为先替换模板、最后嵌入源码。失败证据保留，未用失败脚本部署。

初版不可变stability-20261027T030000Z包已构建/传输但未上线；补强后的最终包为stability-20261027T030000Z-r2。两包都保留，不覆盖旧包。离线构建依赖pip freeze一致、白名单不含运行库/凭据，manifest仅7个允许差异；最终包SHA传输通过后才允许维护升级。

## 维护和恢复约束

最终回归通过后，停止普通timer，按固定PID/命令暂停该一次性runner及容器内driver，等待SENDING/inflight/pending_send连续5次为0。保留原未完成回执和maintenance-interruption证据，停止原driver；非发送prepare/query按数据库事务和原租约安全重启，不丢弃队列。

走现有upgrade.sh一致性备份→不可变发行/镜像→迁移健康门禁，替换已校验宿主driver，恢复timer和独立resumed runner。恢复原10-26日期并复用PREPARED，已受理/确认/未知跳过；恢复结束后只读等待最多30分钟核平台终态，1995张全部CONFIRMED才写ALL_CONFIRMED，遇未知/拒绝/部分成功明确转核查。

回退须同时恢复flow发行和宿主daily-upload.before.py，不把旧业务库覆盖新回执；原批次失败、维护中断及恢复报告均保留。新进度JSON旧代码可忽略，pending job不删除。DMZ保持前一版本，本轮无需更改前置机、网络、证书或NTP。

## 旧41张成功回写

已准备固定41个历史ID的query-only工具，入口强制等待当前1995张恢复ALL_CONFIRMED。每张重新用原冻结身份查询，经原perform_query保存实际回执/确认，再写人工决策和审计；无upload能力，不清原12轮job。它尚未执行，不能称41张已经回写。205 UNKNOWN没有批量重发授权清单和人工历史核对，不在本次日批恢复中盲补。
