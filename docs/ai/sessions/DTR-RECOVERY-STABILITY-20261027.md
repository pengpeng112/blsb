# DTR-RECOVERY-STABILITY-20261027 · 顺序接力记录

Codex/model unknown，会话20261027T021723Z-ad015cf3。用户批准按核查建议修复并继续处理；无子代理。范围为implementation、既有daily_upload_driver、任务运维目录、docs/ai、catalog和码上放心业务README。所有变化在本次登记范围；原源码/driver、共享文档首次基线保存在任务baselines中，不清理其他AI的既有工作。

当前阶段：**本批全部确认、旧41已回写、看板与驱动已升级**。截至2026-10-27 15:31，10-26 VALID 1995张：1995确认、0受理、0未发送、0发送中、0未知。旧41经新鲜官方查询全部3/51，已由原服务回写CONFIRMED；零上传、未重置12轮，原205 UNKNOWN摘要/配额/attempt数一致。当前累计4613确认/205未知，额度4820/80000。

日报展示与宿主驱动预算补强亦已部署stability-20261027T064000Z-r3，失败记录不再把未知候选冒充0，详情直显采集原因/回执进度；前端7项与18项合成浏览器通过、零业务POST，worker/API源码与r2相同。宿主driver另补排队/冷却/执行等待统一总预算，局部55通过。UI/driver升级间4818条提交、旧205、配额/断点不变。

最终驱动/应用全量：595通过/7跳过/0失败/0错误，实际退出0。最终宿主driver SHA为adbc32b1cec4de368884c97df77285825ee4cfc6b5a50a97d4d03a3d80432314，仅在r3维护后生效；r2在跑恢复副本仍固定旧470b4b...，不会被本机编辑影响。

[完整说明](../../operations/drug-trace-recovery-stability-20261027/README.md)、[缺码核查准备](../../operations/drug-trace-recovery-stability-20261027/SOURCE-REVIEW.md)。共享STATE仍为唯一状态摘要、BACKLOG为待办；原FAILED日报和两次恢复证据保留，不能用最后一次manual发送计数代替全批平台终态。

## 已实测

r2版全量562通过7跳过，exit0，XML/log在implementation/var/stability-regression-r2.*；局部81通过含真实隔离PG，最终guard15通过；新增单TOP7例外26通过，零上传收尾/查询/维护工具14项通过。初版560/7、零lookback复合键失败/fcntl导入失败均保留。离线构建/传输成功，7个允许runtime差异、pip freeze不变，282文件白名单不含运行库/凭据。Linux镜像Python3.14.8，隔离合成3单跨页通过、零生产调用。

15:31最终内容复核：1995张全部有正式3/51回执，6476条码从采集快照到冻结payload/Item一致，1996个业务发送摘要全部一致、差异0（含原TOP7一次和受控重试一次）；原205未知仍在隔离核查中。r3于15:31:05完成，宿主driver已实际换为adbc32b1...，worker/API的7个核心文件与r2相同。

最终一致性备份`<prod-path>/backups/20261027T072904Z/`已做restore.sh --validate-only及pg_restore --list：哈希/归档安全/隔离目标与镜像校验通过，目录项553，目标未创建、生产ledger/配额不变。完整隔离恢复演练尚未执行。验证使用独立/var/tmp目标，未写生产恢复路径。

15:41复验：6容器healthy、无过期租约/新失败job/未路由事件；内网NTP选<intranet-host>且Leap Normal，DMZ复验选<relay-net-host>、Reach377/Normal、forwarder active。原timer保持active/waiting，下一次10-28 06:10；早晨失败service记录继续保留，明日分段Oracle整日性能仍待实跑。

`check.py app pause/upgrade/upgrade_status/verify/runtime`实际成功：13:55停止timer排空；原recovery早已12:00失败退出，没杀在发业务。13:57新发行stability-20261027T030000Z-r2上线；备份20261027T055526Z，6healthy、scopeAPPROVED、UI/资源200且同SHA、core/worker7文件和镜像吻合、driver SHA470b4bcca8dc28fee9d990a0d77a28e070baf8f156d5fae38b65a2014863213d；timer active/waiting。维护old246摘要/配额3843/断点80不变。内网chrony实际选<intranet-host>、Leap Normal；DMZ未改。

10:20原恢复从已有DONE10-26窗发送，12:00第1019次HTTP200/TOP7停止；1018最终确认，唯一UNKNOWN为8528553c-c9db-4548-bffd-d610ce876e41。5次NOT_FOUND后13:59又新鲜完整查NOT_FOUND，固定hash/三键/冻结配置/来源/范围/租户、原revision3与唯一attempt1/TOP7/无其他在途核对后，只一次审计CAS+原perform_send；HTTP200受理，第2attempt保留，14:01正常查询确认。不是旧205补报，原6代查询job未清。原R1失败、此特例及新恢复都分目录。

`check.py app resume`14:00实际启动现有resumed runner，继续976 PREPARED，严格禁止新collection。目录`/var/log/blyb/recovery-stability-20261027-resumed/`，原目录无resumed后缀。最终只有1995全部CONFIRMED才写ALL_CONFIRMED；driver OK不是平台全部确认。

`source_metadata.py`两次只读<relay-jump>平台元数据：HIS地址<his-host>:1521/service=his，write_policy仍medical_dict_push；实际HIS查询0。3条限定单据三键SQL通过官方本地静态门禁，非Oracle执行/语法认证，不猜字段值域。原始码/单号/密码未进入公开输出。

## 已实现但尚未实测/外部项

新分段Oracle全天采集耗时、次日调度/07:30目标未实测。1342 WAITING（260正常无码/1079原1/0/3原0/2）须源取证，3337应有码数全NULL；sjzc live因登记策略未达readonly及未核此次白天窗口暂停，继续准备包，不转生产凭据绕过。旧205需新鲜平台核查+历史人工记录，不自动重发。住院/退药/拆零独立来源/字典、可靠补码变更源、码级对账、明细分页、正式IP SAN/管理电脑浏览器仍待办。

## 下一步与回退

本机两个一次性流程已正常退出：completion-watch为RECOVERY_AND_OLD41_COMPLETE，phase-completion为VERIFIED_SUCCESS。不得再次启动已claimed的watcher、旧41或升级launcher；当前仅保留原每日timer。r3检查使用phase_runtime/phase_verify，原runtime_remote.py/verify_remote.py限定r2并会故意拒绝新发行。

先`check.py app resumed_status`和`fresh`读真实状态，勿重复运行任何一次性launcher。当前仍RUNNING时保持原unit；若新UNKNOWN，先保留错误和正式查询，不重置状态/预算或任意复制top7例外。固定41清单仅在未执行且final ALL_CONFIRMED后才可`old41_launch`；已有结果则不要再运行。查询接口唯一允许、每单<=4页调用、原S.perform_query写回真实3/51，零upload；异常立即停止。完毕看old41_status、fresh和detail，核205摘要/配额/attempt数与断点、6healthy和timer，再更新本文/STATE/BACKLOG及结束登记。未达到终态时只能checkpoint/handed_off，不能completed。

回退先停timer并排空真实发送，标准rollback.sh flow-20261026T132000Z，同时恢复私有upgrade目录daily-upload.before.py（原SHA cf967102e68c4978cc17ba4739af6860de60344404727f6cc8eedf1b4a851c39），核验再开timer。无新schema，不回灌旧DB覆盖新回执；不清冷却目录规避限流，不开放80/800x或绕SSH转发策略。

分层回退：r3→r2须恢复`/root/blyb-maintenance-backups/upgrade-stability-20261027T064000Z-r3/daily-upload.before.py`（470b4b...）；再从r2→flow才用r2维护目录的cf9671...。两级都保留最新业务库和回执，不直接把最早备份倒灌。
