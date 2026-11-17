"""Synchronize task-scoped facts from immutable evidence; never ends a session."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,re,shutil,xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
RID='stability-20261027T030000Z-r2'
UI_RID='stability-20261027T064000Z-r3'
BEGIN='<!-- DTR-RECOVERY-STABILITY-20261027:BEGIN -->'
END='<!-- DTR-RECOVERY-STABILITY-20261027:END -->'

def evidence(phase):
    files=sorted((HERE/'evidence').glob('app-'+phase+'-*.json'))
    rows=[(f,json.loads(f.read_text(encoding='utf8'))) for f in files]
    return next(((f,j) for f,j in reversed(rows) if j['exit_code']==0), (None,None))

def latest(phase):
    f,j=evidence(phase);return j['result'] if j else None

def update(path,section):
    p=ROOT/path;text=p.read_text(encoding='utf-8-sig')
    backup=HERE/'baselines/shared'/path
    if not backup.exists():backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,backup)
    block=BEGIN+'\n'+section.strip()+'\n'+END
    if BEGIN in text:
        assert text.count(BEGIN)==text.count(END)==1
        text=re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END),lambda _:block,text,flags=re.S)
    else:
        head,rest=text.split('\n',1);text=head+'\n\n'+block+'\n'+rest
    p.write_text(text,encoding='utf8')

resumed=latest('resumed_status');assert resumed
progress=resumed.get('final.json') or resumed.get('progress.json') or {}
snap=progress.get('snapshot') or {};counts=snap.get('window_states') or {}
confirmed=counts.get('CONFIRMED',0);accepted=counts.get('ACCEPTED',0);prepared=counts.get('PREPARED',0)
unknown=counts.get('UNKNOWN',0);sending=counts.get('SENDING',0)
old=latest('old41_status') or {};old_result=(old.get('result.json') or {}).get('result') or {}
old_ok=old_result.get('outcome')=='ALL_41_CONFIRMED'
batch_ok=progress.get('outcome')=='ALL_CONFIRMED'
ui_verify=latest('phase_verify') or {};ui_runtime=latest('phase_runtime') or {}
ui_ok=bool(ui_verify.get('all_4818_submissions_unchanged') and ui_verify.get('old_205_unchanged') and ui_runtime.get('release')==UI_RID)
current_release=UI_RID if ui_ok else RID
ui_text=('日报展示与宿主驱动预算补强亦已部署'+UI_RID+'，失败记录不再把未知候选冒充0，详情直显采集原因/回执进度；前端7项与18项合成浏览器通过、零业务POST，worker/API源码与r2相同。宿主driver另补排队/冷却/执行等待统一总预算，局部55通过。UI/driver升级间4818条提交、旧205、配额/断点不变。' if ui_ok else '日报展示修复已在本机完成、7项显示测试与18项合成浏览器通过，UI-only镜像包'+UI_RID+'已构建/传输但尚未部署；另已补强宿主driver排队/冷却/执行等待的总预算（55项局部通过），完整回归后随包升级。等待本批恢复及旧41全部结束后再维护，不在发送中重启。')
try:
    suites=list(ET.parse(ROOT/'implementation/var/stability-regression-r3.xml').getroot().iter('testsuite'))
    full={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    proof=HERE/'evidence/final-test-result.json'
    exited=proof.exists() and json.loads(proof.read_text(encoding='utf8'))['full_regression']['observed_process_exit_code']==0
    full_text=f"最终驱动/应用全量：{full['tests']-full['skipped']}通过/{full['skipped']}跳过/{full['failures']}失败/{full['errors']}错误"+('，实际退出0。' if exited else '，进程退出码待核对。')
except (FileNotFoundError,ET.ParseError):full_text='最终驱动预算补强的全量回归正在运行，未声称完成。'
content=latest('content') or {};backup=latest('backup_validate') or {}
final_acceptance=''
if ui_ok and content.get('confirmed_bills_with_platform_3_51')==1995:
    final_acceptance=f"15:31最终内容复核：1995张全部有正式3/51回执，6476条码从采集快照到冻结payload/Item一致，1996个业务发送摘要全部一致、差异0（含原TOP7一次和受控重试一次）；原205未知仍在隔离核查中。r3于15:31:05完成，宿主driver已实际换为adbc32b1...，worker/API的7个核心文件与r2相同。"
if backup.get('validate_only_exit')==0:
    final_acceptance+=f"\n\n最终一致性备份`{backup['backup']}/`已做restore.sh --validate-only及pg_restore --list：哈希/归档安全/隔离目标与镜像校验通过，目录项{backup['pg_dump_toc_entries']}，目标未创建、生产ledger/配额不变。完整隔离恢复演练尚未执行。验证使用独立/var/tmp目标，未写生产恢复路径。"
ops=latest('system') or {}
if ops.get('release')==UI_RID and '<intranet-host>' in ops.get('ntp_tracking','') and 'Normal' in ops.get('ntp_tracking',''):
    final_acceptance+='\n\n15:41复验：6容器healthy、无过期租约/新失败job/未路由事件；内网NTP选<intranet-host>且Leap Normal，DMZ复验选<relay-net-host>、Reach377/Normal、forwarder active。原timer保持active/waiting，下一次10-28 06:10；早晨失败service记录继续保留，明日分段Oracle整日性能仍待实跑。'
proof_rows=[]
for key,label in [('resumed_status','本批恢复终态'),('old41_status','旧41正式查询回写'),('phase_upgrade_status','r3升级与新驱动'),('phase_verify','页面/全部账本保护'),('phase_runtime','运行代码与镜像SHA'),('content','1995张/6476码完整性和平台回执'),('backup_validate','一致性备份只读验证'),('system','最终主机/定时/NTP/授权')]:
    f,j=evidence(key)
    if f:proof_rows.append('| '+label+' | [实际证据](evidence/'+f.name+') |')
proof_md='| 核验 | 回执 |\n| --- | --- |\n'+'\n'.join(proof_rows)
local_done=HERE/'evidence/phase-completion.json'
done=local_done.exists() and json.loads(local_done.read_text(encoding='utf8')).get('outcome')=='VERIFIED_SUCCESS'
runner_note=('本机两个一次性流程已正常退出：completion-watch为RECOVERY_AND_OLD41_COMPLETE，phase-completion为VERIFIED_SUCCESS。不得再次启动已claimed的watcher、旧41或升级launcher；当前仅保留原每日timer。r3检查使用phase_runtime/phase_verify，原runtime_remote.py/verify_remote.py限定r2并会故意拒绝新发行。' if done else '本机一次性watch_completion.py按真实终态等待并受控处理旧41；finish_phase.py仅在它RECOVERY_AND_OLD41_COMPLETE且最终回归通过后，才执行phase_pause/phase_upgrade/phase_verify/phase_runtime及内容复核。对应evidence/completion-watch.json和phase-completion.json先看状态，不重复启动已claimed流程；第二阶段只有VERIFIED_SUCCESS才算升级完成。r3之后原r2检查会故意拒绝，应使用phase_runtime/phase_verify，不通过改SHA绕闸门。')
captured_dt=datetime.fromisoformat(progress.get('updated_utc') or progress['started_utc'])
if old_ok:
    _,last_old=evidence('old41_status')
    captured_dt=max(captured_dt,datetime.fromisoformat(last_old['captured_at']))
if ui_ok:
    _,last_ui=evidence('phase_verify');captured_dt=max(captured_dt,datetime.fromisoformat(last_ui['captured_at']))
captured=captured_dt.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
phase='本批全部确认、旧41已回写' if batch_ok and old_ok else '本批全部确认，旧41待处理' if batch_ok else '稳定性修复已部署，日批恢复中'
if batch_ok and old_ok and ui_ok:phase='本批全部确认、旧41已回写、看板与驱动已升级'
business=f'截至{captured}，10-26 VALID 1995张：{confirmed}确认、{accepted}受理、{prepared}未发送、{sending}发送中、{unknown}未知。'
old_status='旧41经新鲜官方查询全部3/51，已由原服务回写CONFIRMED；零上传、未重置12轮，原205 UNKNOWN摘要/配额/attempt数一致。' if old_ok else '旧41的query-only回写工具已准备，等待本批ALL_CONFIRMED后执行；此刻不能称已回写。'
if old_ok:
    final=latest('fresh')
    old_status+=f"当前累计{final['states'].get('CONFIRMED',0)}确认/{final['states'].get('UNKNOWN',0)}未知，额度{final['quota'][0]['used']}/80000。"
summary=f'''## 最新：{phase}（{captured}）

[实现、证据与恢复说明](LINK)：<prod-host>当前发行{current_release}，采集25张分页让出、Oracle每页会话复用/耗时、独立续租/进度心跳、失租整页回滚与driver阶段错误已修复。r2应用全量562通过/7跳过，新增单TOP7维护保护另26通过；Linux镜像实测Python3.14.8。13:57稳定性版升级exit0，一致性备份20261027T055526Z；六容器健康，页面/7个源码/镜像/宿主driver SHA一致，timer active/waiting。该次维护间旧246摘要、配额3843和断点80均未变。

{business}10:20原恢复在12:00第1019次遇HTTP200/TOP7后安全停止；1018已确认，1张经5次NOT_FOUND及13:59新鲜完整NOT_FOUND后，只重试一次受理，14:01已确认。原失败timer及中止恢复证据保留，14:00按同DONE窗口继续976张，不新采集、不重发已确认/受理/未知。{old_status}

{ui_text}
{full_text}

待核查1342=正常门诊无码260+原1/0分支1079+原0/2分支3；不是1342次平台失败。expected_code_count仍全NULL。HIS补充核查只完成<relay-jump>元数据，登记write_policy仍medical_dict_push，sjzc live未执行；已准备3条绑定单据键的只读SQL及有限执行计划。旧205不盲重发，住院/退药/拆零及迟到码需独立来源规则；码级对账、管理电脑IP SAN/浏览器、次日日批耗时和07:30SLA未验收。发送授权到北京时间11-22 00:00，需到期前续批；不能把业务窗口11-23结束当证据到期。后续按[交接](HANDOFF)处理，勿重复启动本批runner。
'''
remaining='''

下一轮按以下剩余事项处理，本批1995恢复、旧41回写与r3上线已完成，不重复执行。

| 优先级 | 真正待办 | 下一步 |
| --- | --- | --- |
| P1 | 新分段Oracle首个完整日批实测 | 10-28 06:10观察slices/progress/source_metrics、租约/心跳、outbox与最终确认；07:30目标尚未验收 |
| P1 | 正常门诊260张快照缺码 | 按[只读核查包](../operations/drug-trace-recovery-stability-20261027/SOURCE-REVIEW.md)落实readonly来源与窗口，限量三键比对，不重复索要已登记凭据 |
| P1 | 1079原1/0、3原0/2及历史拆零分支 | 药学/HIS确认事件、方向/原单关系后独立来源和批准字典revision，不放宽当前321门诊门禁 |
| P1 | 205历史UNKNOWN | 新鲜完整查询、人工历史核对、固定名单审计恢复；原12代/attempt/payload不清、不批量盲发 |
| P1 | 迟到码/退款变更和应有码数 | 可靠且可索引的变更来源/独立数量依据，不能用现行发药时间日窗或扫码行数自证完整 |
| P2 | 码级闭环与问题明细分页 | 6476 Item仍PENDING，单据3/51已确认；独立码级证据与先筛后限/分页另行完善 |
| P2 | 正式IP SAN/管理电脑浏览器与完整恢复演练 | 本轮服务器UI、合成浏览器、backup validate-only/TOC已验；上述真实使用/隔离恢复仍待落实 |
| 运维 | 发送证据续期 | 北京时间11-22 00:00到期，提前按既有流程续批；业务窗口结束11-23不能替代证据期限 |
'''
for path in ['docs/ai/STATE.md','docs/ai/HANDOFF.md','docs/ai/BACKLOG.md']:
    section=summary.replace('LINK','../operations/drug-trace-recovery-stability-20261027/README.md').replace('HANDOFF','sessions/DTR-RECOVERY-STABILITY-20261027.md')
    if path.endswith('BACKLOG.md'):section+=remaining
    update(path,section)
update('数据上报/1、码上放心平台/README.md',summary.replace('LINK','../../docs/operations/drug-trace-recovery-stability-20261027/README.md').replace('HANDOFF','../../docs/ai/sessions/DTR-RECOVERY-STABILITY-20261027.md'))

common=f'''## 最新：{phase}（{captured}）

[实现与验收](../../docs/operations/drug-trace-recovery-stability-20261027/README.md)：<prod-host>当前发行{current_release}，继承r2的采集稳定性修复；无业务库DDL/新依赖，DMZ维持flow发行，视图r10/冻结payload/批准字典/发送范围保持。{business}
'''
details={
 '实施状态矩阵.md':f'采集分段、Oracle页连接复用/计时、epoch续租/页提交栅栏、工作进度心跳、driver4h采集/20min停滞/5h50总预算及失败原因已实现。生产已验证新发行运行和真实新发送/查询，尚未用新实现再采集HIS完整一天，不把合成验收当Oracle性能达标。{old_status}1342数据待核查和205历史未知保留。',
 '测试执行记录.md':'应用全量`var/stability-regression-r2.xml`：562 passed/7 skipped/0 failures/0 errors，退出0、730.80秒；新增TOP7操作例外另`var/recovery-top7.xml`26通过，收尾/查询/展示维护工具的零上传、预算、失效门禁、一次性及排空保护另14项通过。局部81通过含真实隔离PG，最终guard专项15通过；初版560/7和修正前失败记录保留。Linux不可变镜像Python3.14.8，3条合成主单跨两页运行通过，零HIS/平台/生产DB请求。离线构建pip freeze一致、白名单282文件/7源码差异，源/镜像/driver SHA和TLS页面/6容器生产核验通过。新Oracle分段全天性能/07:30目标未实测。',
 '接口与设计差异.md':'没有新增REST路由或上传字段；collection-run既有summary JSON增加slices_completed/progress_at/window_completed/source_metrics（connect_count/connect_seconds/query_count/query_seconds），供已有列表/详情读取。driver失败日报collection保存run/state/summary/error_code/candidate_count_known=false，区分采集失败尚未统计与真实0。JobYield代表已提交页后让出，复用同job且退还领取attempt；RequestDeferred仍仅指该次HTTP未发生。正常完成须finish_job CAS成功才写Operation成功，失租页回滚，不改变新owner状态。',
 '04-API目录对照矩阵.md':'已有collection-runs、prepare/send/query和daily-reports路径/权限未变；分页只在worker内部，同run_id/复合断点恢复。失败采集字段进入原collection JSON，新增进度进入原summary JSON，未修改TOP平台签名/字段/类型/终态语义。HTTP200/TOP7仍UNKNOWN先查询；本次特定一张经新鲜完整NOT_FOUND+审计+精确CAS、原perform_send至多一次重试，不成为通用自动重发策略。',
 '系统架构与流程说明.md':'采集job领取epoch→独立Session定时续租→Oracle每页共享连接读取整单/版本复核→同页事务写快照/血缘/质量/outbox/断点/summary并栅栏提交→原job RETRY_WAIT让出→worker处理outbox/其他任务→续页或DONE。页内异常整页回滚；300s无业务进度停止续租，不能无限伪造healthy。批次驱动仍等DONE后按原门禁发PREPARED；UNKNOWN先查，未来失败日仍需显式恢复，未增加跨日自动盲补。'
}
for name,detail in details.items():update('implementation/docs/'+name,common+'\n'+detail+'\n\n'+ui_text+'\n'+full_text)

update('docs/operations/drug-trace-recovery-stability-20261027/README.md',f'''## 最新阶段：{phase}（{captured}）

{business}{old_status}

稳定性发行13:57上线且六容器healthy，旧246/配额/断点在维护期间一致。原恢复自然在12:00因单TOP7停止，维护时没有存活driver被杀；pause文件的INTERRUPTED_FOR_STABILITY_UPGRADE是预设阶段标签，不能理解为维护造成原12:00失败。原final.json、FAILED timer回执及五次NOT_FOUND均保留。

13:59已对该唯一TOP7单做新鲜完整NOT_FOUND、冻结身份/配置/范围/无其他在途核对，ManualDecision+ReceiptEvent+AuditEvent和精确UNKNOWN revision3→PREPARED4的CAS同事务提交；经原perform_send唯一一次POST受理，14:01自动查询3/51确认。原第1次限流attempt保留，第2次受理保留，旧205与原12轮账本未清。其保护用例26通过。14:00 resumer继续剩976 PREPARED，本次运行不新采集；未知/已受理/已确认仍跳过。

最终应用全量**562通过、7跳过、0失败/错误，exit0**；[XML](../../../implementation/var/stability-regression-r2.xml)、[日志](../../../implementation/var/stability-regression-r2.log)。新增操作例外另[26项](../../../implementation/var/recovery-top7.xml)，[收尾/查询/维护工具14项](evidence/operations-tests.xml)。[镜像合成验收](evidence/packaged-runtime.json)实际Python3.14.8，零HIS/平台/生产DB；应用新Oracle全天采集性能未测。

一致性备份`<prod-path>/backups/20261027T055526Z/`；维护日志`/root/blyb-maintenance-backups/upgrade-stability-20261027T030000Z-r2/`，旧host driver为其中daily-upload.before.py。发行与driver成对回退到flow；保留新回执，不用旧DB覆盖。运行中不得重跑build/upgrade/resume/top7_send/old41_launch。

[缺码只读核查包](SOURCE-REVIEW.md)：260正常门诊+1079原1/0+3原0/2；3条SQL静态检查通过，HIS活库未执行。sjzc平台登记仍medical_dict_push，当前门禁不满足，不读取/复制凭据绕过；生产专用只读来源未因此停用。

{ui_text}
14:46实际本地内容审计1995张/6476条码：采集快照有效行→冻结payload→SubmissionItem完全一致，payload/snapshot/source provenance和当时1576个发送业务摘要均一致，差异0；只读应用库，HIS/平台请求0。6476个Item仍PENDING仅说明未做独立码级对账，不能冒充逐码平台库存已核实。

{full_text}[最终XML](../../../implementation/var/stability-regression-r3.xml)、[日志](../../../implementation/var/stability-regression-r3.log)、[实际命令/退出记录](evidence/final-test-result.json)。55项driver局部包含新总时限在排队/冷却/执行中的约束，已观察到的ACCEPTED/CONFIRMED/UNKNOWN/REJECTED在边界仍按真实结果返回。任务超预算不取消/重发原job，也不清原查询预算。

{final_acceptance}

{proof_md}

14:27运行检查：无过期租约、无新失败job/未路由事件，系统资源充足，timer下一次10-28 06:10；原daily-upload.service仍保留早晨collect_timeout/exit3的FAILED历史，不通过reset-failed掩盖。发送授权实际有效至北京时间11-22 00:00，80000额度，批准药房050121/050102/050101/050116/050117，业务窗口10-24至11-23半开区间；到期前需正常续批。首轮检查工具误取evidence.scope为空，修正为API顶层scope后复验真实值，原证据保留，未改生产授权。
''')
p=HERE/'README.md';s=p.read_text(encoding='utf8')
s=s.replace('最终全量运行中，以最终XML为准。','最终全量562通过/7跳过/0失败或错误，exit0，以stability-regression-r2.xml为准。')
s=s.replace('它尚未执行，不能称41张已经回写。','执行状态以页首最新阶段及old41实际证据为准；只有ALL_41_CONFIRMED才算回写完成。')
p.write_text(s,encoding='utf8')

handoff=ROOT/'docs/ai/sessions/DTR-RECOVERY-STABILITY-20261027.md'
handoff.write_text(f'''# DTR-RECOVERY-STABILITY-20261027 · 顺序接力记录

Codex/model unknown，会话20261027T021723Z-ad015cf3。用户批准按核查建议修复并继续处理；无子代理。范围为implementation、既有daily_upload_driver、任务运维目录、docs/ai、catalog和码上放心业务README。所有变化在本次登记范围；原源码/driver、共享文档首次基线保存在任务baselines中，不清理其他AI的既有工作。

当前阶段：**{phase}**。{business}{old_status}

{ui_text}

{full_text}最终宿主driver SHA为adbc32b1cec4de368884c97df77285825ee4cfc6b5a50a97d4d03a3d80432314，仅在r3维护后生效；r2在跑恢复副本仍固定旧470b4b...，不会被本机编辑影响。

[完整说明](../../operations/drug-trace-recovery-stability-20261027/README.md)、[缺码核查准备](../../operations/drug-trace-recovery-stability-20261027/SOURCE-REVIEW.md)。共享STATE仍为唯一状态摘要、BACKLOG为待办；原FAILED日报和两次恢复证据保留，不能用最后一次manual发送计数代替全批平台终态。

## 已实测

r2版全量562通过7跳过，exit0，XML/log在implementation/var/stability-regression-r2.*；局部81通过含真实隔离PG，最终guard15通过；新增单TOP7例外26通过，零上传收尾/查询/维护工具14项通过。初版560/7、零lookback复合键失败/fcntl导入失败均保留。离线构建/传输成功，7个允许runtime差异、pip freeze不变，282文件白名单不含运行库/凭据。Linux镜像Python3.14.8，隔离合成3单跨页通过、零生产调用。

{final_acceptance}

`check.py app pause/upgrade/upgrade_status/verify/runtime`实际成功：13:55停止timer排空；原recovery早已12:00失败退出，没杀在发业务。13:57新发行{RID}上线；备份20261027T055526Z，6healthy、scopeAPPROVED、UI/资源200且同SHA、core/worker7文件和镜像吻合、driver SHA470b4bcca8dc28fee9d990a0d77a28e070baf8f156d5fae38b65a2014863213d；timer active/waiting。维护old246摘要/配额3843/断点80不变。内网chrony实际选<intranet-host>、Leap Normal；DMZ未改。

10:20原恢复从已有DONE10-26窗发送，12:00第1019次HTTP200/TOP7停止；1018最终确认，唯一UNKNOWN为8528553c-c9db-4548-bffd-d610ce876e41。5次NOT_FOUND后13:59又新鲜完整查NOT_FOUND，固定hash/三键/冻结配置/来源/范围/租户、原revision3与唯一attempt1/TOP7/无其他在途核对后，只一次审计CAS+原perform_send；HTTP200受理，第2attempt保留，14:01正常查询确认。不是旧205补报，原6代查询job未清。原R1失败、此特例及新恢复都分目录。

`check.py app resume`14:00实际启动现有resumed runner，继续976 PREPARED，严格禁止新collection。目录`/var/log/blyb/recovery-stability-20261027-resumed/`，原目录无resumed后缀。最终只有1995全部CONFIRMED才写ALL_CONFIRMED；driver OK不是平台全部确认。

`source_metadata.py`两次只读<relay-jump>平台元数据：HIS地址<his-host>:1521/service=his，write_policy仍medical_dict_push；实际HIS查询0。3条限定单据三键SQL通过官方本地静态门禁，非Oracle执行/语法认证，不猜字段值域。原始码/单号/密码未进入公开输出。

## 已实现但尚未实测/外部项

新分段Oracle全天采集耗时、次日调度/07:30目标未实测。1342 WAITING（260正常无码/1079原1/0/3原0/2）须源取证，3337应有码数全NULL；sjzc live因登记策略未达readonly及未核此次白天窗口暂停，继续准备包，不转生产凭据绕过。旧205需新鲜平台核查+历史人工记录，不自动重发。住院/退药/拆零独立来源/字典、可靠补码变更源、码级对账、明细分页、正式IP SAN/管理电脑浏览器仍待办。

## 下一步与回退

{runner_note}

先`check.py app resumed_status`和`fresh`读真实状态，勿重复运行任何一次性launcher。当前仍RUNNING时保持原unit；若新UNKNOWN，先保留错误和正式查询，不重置状态/预算或任意复制top7例外。固定41清单仅在未执行且final ALL_CONFIRMED后才可`old41_launch`；已有结果则不要再运行。查询接口唯一允许、每单<=4页调用、原S.perform_query写回真实3/51，零upload；异常立即停止。完毕看old41_status、fresh和detail，核205摘要/配额/attempt数与断点、6healthy和timer，再更新本文/STATE/BACKLOG及结束登记。未达到终态时只能checkpoint/handed_off，不能completed。

回退先停timer并排空真实发送，标准rollback.sh flow-20261026T132000Z，同时恢复私有upgrade目录daily-upload.before.py（原SHA cf967102e68c4978cc17ba4739af6860de60344404727f6cc8eedf1b4a851c39），核验再开timer。无新schema，不回灌旧DB覆盖新回执；不清冷却目录规避限流，不开放80/800x或绕SSH转发策略。

分层回退：r3→r2须恢复`/root/blyb-maintenance-backups/upgrade-stability-20261027T064000Z-r3/daily-upload.before.py`（470b4b...）；再从r2→flow才用r2维护目录的cf9671...。两级都保留最新业务库和回执，不直接把最早备份倒灌。
''',encoding='utf8')

catalog=ROOT/'docs/catalog.json';data=json.loads(catalog.read_text(encoding='utf8'))
for path,category,title in [
 ('docs/operations/drug-trace-recovery-stability-20261027/README.md','operations','10-26日批恢复与采集稳定性修复实录'),
 ('docs/operations/drug-trace-recovery-stability-20261027/SOURCE-REVIEW.md','operations','10-26缺码只读核查准备'),
 ('docs/ai/sessions/DTR-RECOVERY-STABILITY-20261027.md','shared_ai_state','日批恢复与采集稳定性修复交接')]:
    found=[d for d in data['documents'] if d['path']==path];assert len(found)<=1
    if found:found[0].update(category=category,title=title)
    else:data['documents'].append(dict(path=path,category=category,title=title))
catalog.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps({'phase':phase,'captured':captured,'window_states':counts,'old41_confirmed':old_ok,'session_ended':False},ensure_ascii=False))
