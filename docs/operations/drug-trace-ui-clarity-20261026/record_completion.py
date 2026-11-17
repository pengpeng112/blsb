"""Publish handoff only after successful production verification evidence exists."""
from pathlib import Path
import json
from datetime import datetime,timezone,timedelta
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
def evidence(phase):
    p=sorted((HERE/'evidence').glob('app-'+phase+'-*.json'))[-1]
    data=json.loads(p.read_text('utf8'));assert data['exit_code']==0
    return p,data['result']
vp,v=evidence('verify');sp,s=evidence('status');up,u=evidence('upgrade_status')
assert v['old_247_unchanged'] and v['checkpoint_unchanged'] and v['quota_unchanged']
assert u['result']['exit_code']==0 and u['result']['timer_restart_exit']==0
final=s['resumed']['final.json'];assert final['outcome']=='DRIVER_WINDOWS_COMPLETED'
now=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
backup=u['backups']['summary'].splitlines()[0]
operation='docs/operations/drug-trace-ui-clarity-20261026/README.md'
handoff='docs/ai/sessions/DTR-UI-CLARITY-20261026.md'
common=f'''## 最新：药品追溯界面已优化上线（{now}）

[使用说明与证据](../operations/drug-trace-ui-clarity-20261026/README.md)：生产ui-clarity-20261026T063000Z，默认四状态卡、进度、趋势和需关注事项；分总览/上报记录/单据与追溯码/对账核查/接入与授权五入口。英文状态改中文，技术信息折叠；受理与最终成功分开，失败统计不显示虚假零，最多200笔明细边界明确提示。
前端4文件变更，后端/依赖/数据库迁移不变；构建exit0、辅助测试5通过、合成浏览器13项通过且零业务POST。生产六容器healthy、UI与资产200且SHA一致、原授权scope200、timer已恢复active/waiting；服务器验证成功，当前管理电脑TLS/正式IP SAN和生产浏览器验收仍未解决，未启用HTTP或绕过SSH转发策略。
本轮等原批准补发自然结束后才升级，未中断/重跑driver。两个窗口均OK，结束汇总以final.json为准；当前{s['db']['states']}，used={s['db']['quota'][0]['used']}/80000。旧41受理/206未知摘要、源断点46与升级间额度均不变；旧247仍须平台核对，未因界面上线变成已处理。
一致性备份<prod-path>/backups/{backup}/；私有维护日志/root/blyb-maintenance-backups/upgrade-ui-clarity-20261026T063000Z/。回退上一auth发行，不恢复旧业务库。访问仍https://<prod-host>/ui/#/drugtrace，admin及原独立密码不变，Ctrl+F5刷新。[交接](sessions/DTR-UI-CLARITY-20261026.md)。

'''
def insert(path,text):
    p=ROOT/path;old=p.read_text('utf8');head,rest=old.split('\n',1)
    p.write_text(head+'\n\n'+text+rest.lstrip('\n'),encoding='utf8')
for name in ['STATE.md','HANDOFF.md','BACKLOG.md']:insert('docs/ai/'+name,common)
business=common.replace('../operations/','../../docs/operations/').replace('(sessions/','(../../docs/ai/sessions/')
insert('数据上报/1、码上放心平台/README.md',business)
implementation='''## 2026-10-26 药品追溯界面优化（DTR-UI-CLARITY-20261026）

[页面说明、测试与部署](../../docs/operations/drug-trace-ui-clarity-20261026/README.md)：ui-clarity-20261026T063000Z实际上线，五个入口、四状态卡、趋势/问题集中、中文状态和自适应布局。仅展示逻辑变化，无API/枚举/数据库迁移/后端依赖变更；ACCEPTED不算最终成功、UTC提交与北京时间业务日报区分，空/失败不判正常，近期200笔明细及先限后筛边界有提示。同日timer/manual详情按点击来源选择。
页面默认GET只读，真实演练证据入口隐藏，手工发单/停发需确认且后端原角色/授权不变。辅助测试5通过、合成浏览器13项/零业务POST、vue-tsc及Vite成功；生产scope/UI/资产200、哈希一致、六容器healthy、timer恢复。未修改源视图或发送范围，不新增HIS/平台测试。
升级等原补发自然结束并排空，一致性备份后标准升级；旧247、源断点、维护间配额均未变。生产管理电脑TLS/IP SAN与真实浏览器仍待验收，合成截图不替代生产。后端明细分页/先筛后限、旧247核对及补发/日报合并呈现仍待办。

'''
for name in ['实施状态矩阵.md','测试执行记录.md','接口与设计差异.md','04-API目录对照矩阵.md','系统架构与流程说明.md']:insert('implementation/docs/'+name,implementation)
readme=ROOT/operation
text=readme.read_text('utf8').replace('当前源码、离线发行和本机浏览器验证已完成；生产上线结果以本目录后续维护验证和共享STATE最新记录为准。',f'已于{now}完成生产上线，发行ui-clarity-20261026T063000Z。入口仍为https://<prod-host>/ui/#/drugtrace，用户名admin及原密码不变；Ctrl+F5刷新。')
text+='\n\n## 实际上线结果\n\n'+common.split('\n\n',1)[1].replace('../operations/drug-trace-ui-clarity-20261026/README.md','README.md').replace('(sessions/','(../../ai/sessions/')
text+=f'\n实测证据：[维护结果](evidence/{up.name})、[生产验证](evidence/{vp.name})、[补发及最终状态](evidence/{sp.name})。\n'
readme.write_text(text,encoding='utf8')
(ROOT/handoff).write_text('# DTR-UI-CLARITY-20261026 交接\n\n实际工具Codex，模型unknown；会话20261026T055958Z-0e2004ac。\n\n'+common.replace('../operations/','../../operations/').replace('(sessions/','(')+'\n## 修改与真实命令\n\n范围仅admin_web四文件；baseline及生成器、只读检查、离线构建/传输/安全升级工具在任务目录。执行记录和失败更正详见[实录](../../operations/drug-trace-ui-clarity-20261026/README.md)。各本机命令exit0，生产upgrade/verify/status exit0；后端全量未重跑，因为与auth基线零后端变更，没有声称本轮全量通过。\n\n## 剩余工作\n\n旧247平台核对、明细先筛后限/分页、正式证书与管理电脑访问、定时日批07:30验收、补发与日报呈现。不得重跑一次性升级/补发launcher或重置额度。\n',encoding='utf8')
catalog=ROOT/'docs/catalog.json';data=json.loads(catalog.read_text('utf8'))
for path,category,title in [(operation,'operations','药品追溯界面优化与上线实录'),(handoff,'shared_ai_state','药品追溯界面优化交接')]:
    if not any(x['path']==path for x in data['documents']):data['documents'].append({'path':path,'category':category,'title':title})
catalog.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps({'recorded':True,'at':now,'backup':backup,'production_verified':True},ensure_ascii=False))
