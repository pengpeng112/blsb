"""Task-scoped documentation and verification inputs; no production mutation."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
TASK = 'DTR-UPLOAD-FLOW-20261026'
RID = 'flow-20261026T132000Z'


def insert(path, section):
    p = ROOT / path
    text = p.read_text(encoding='utf-8-sig')
    assert '## 最新：上传与限流处理已改进上线（2026-10-26 21:32）' not in text
    first, rest = text.split('\n', 1)
    p.write_text(first + '\n\n' + section.strip() + '\n' + rest, encoding='utf8')


summary = '''## 最新：上传与限流处理已改进上线（2026-10-26 21:32）

[实现、证据及恢复说明](LINK)：<prod-host>与DMZ均已切换flow-20261026T132000Z；六容器healthy、前置机active/config校验0、正式mTLS健康200、原timer active/waiting。新增跨进程持久节奏和TOP7/HTTP429共享冷却，保留脱敏子错误/等待秒数；真实受理首查120s、未知300s、后续指数错峰、重叠日期窗去重。只在明确本次未触网时复用原job延后，不增加查询代次；宿主driver同时适配等待。没有改视图r10、冻结payload/批准字典或发送授权。
全量522通过/7跳过/0失败，局部69通过1跳过、API/driver54通过、实际隔离PG1通过；一次正式已成功单只读查询返回3/51 CONFIRMED。没有新增真实上传或HIS访问，维护间旧247摘要、配额2823与断点46均不变；旧41仍待受控回写，206不得盲重发。
下一步观察首个完整日批的积压、冷却收尾与07:30日报；平台实际阈值/共享NAT仍待确认。旧247轮询预算没有重置，管理电脑TLS/IP SAN和真实浏览器验收也不因本轮自动完成。[交接](HANDOFF)。
'''
for path in ['docs/ai/STATE.md', 'docs/ai/HANDOFF.md', 'docs/ai/BACKLOG.md']:
    insert(path, summary.replace('LINK', '../operations/drug-trace-upload-flow-20261026/README.md').replace('HANDOFF', 'sessions/DTR-UPLOAD-FLOW-20261026.md'))
insert('数据上报/1、码上放心平台/README.md', summary.replace('LINK', '../../docs/operations/drug-trace-upload-flow-20261026/README.md').replace('HANDOFF', '../../docs/ai/sessions/DTR-UPLOAD-FLOW-20261026.md'))

common = '''## 最新：上传与限流处理已改进上线（2026-10-26 21:32）

[实现与验收](../../docs/operations/drug-trace-upload-flow-20261026/README.md)：DTR-UPLOAD-FLOW-20261026已部署flow-20261026T132000Z至内网和DMZ；依赖、视图r10、批准字典、冻结payload、scope/配额和业务数据库schema不变。
'''
specific = {
    '实施状态矩阵.md': '实测完成：TOP7/HTTP429共享冷却、持久多进程节奏、延迟/错峰查询、重叠日期窗合并、明确未发请求的同job延后、宿主driver等待适配、DMZ Retry-After。六容器与timer正常，正式只读查询3/51，mTLS健康200；新流程完整日批和07:30SLA尚未观察。旧41/206账本未变，不当作本轮已处置。',
    '测试执行记录.md': '局部69通过/1跳过、API/driver54通过、隔离真实PG1通过；全量529项：522通过/7跳过/0失败/0错误，退出0，XML和log见implementation/var/upload-flow-*.xml及README。覆盖多进程锁、等待提示、触网后存储故障不得伪装未发送、配额预检、SQLite/PG租约fencing、真实本机TLS。生产仅查询一笔已确认单3/51，无新上传/HIS访问；维护前后旧247摘要、配额2823、断点46一致。跳过原因和首日批未测边界见README。',
    '接口与设计差异.md': '没有新增REST路由或修改上传字段。内部TransportResult新增rate_limit、协议错误保留白名单脱敏code/msg/sub_code/sub_msg/request_id与等待提示；HTTP200的TOP7不得当成功/NOT_FOUND。新增RequestDeferred严格只表示此次未触网；queue按原epoch/state更新RETRY_WAIT并退还该次领取计数，复用原查询代次，不改12轮历史账本。真实上传一次POST一次结果，超时仍UNKNOWN先查。',
    '04-API目录对照矩阵.md': '已有prepare/send/query等API路径及权限未变；send在已知冷却时排队，query遇平台限流返回查询错误和脱敏rate_limit，不冒充未找到。上游仍uploadretail与query.billstatus，冻结零售321/查询A经只读聚合核实。DMZ仅本地429增加Retry-After:60，不放宽白名单/mTLS或开放代理。HTTP受理仍不是3/51业务确认。',
    '系统架构与流程说明.md': '采集/冻结payload→授权与配额检查→共享节流预检→一次正式POST→受理后延迟状态查询→按平台终态落账。共享目录/data/exchange/drugtrace-rate沿用exchange卷，仅存哈希/时间/计数，文件原子写和短锁；origin1.5s、query4.5s为本地保守策略，非官方QPS承诺。长冷却转原job RETRY_WAIT释放worker，文件失效时失败关闭。宿主timer驱动只对明确等待分支延后执行计时，保留30min排队上限。'
}
for name, detail in specific.items():
    insert('implementation/docs/' + name, common + '\n' + detail + '\n')

readme = HERE / 'README.md'
s = readme.read_text(encoding='utf8')
s = s.replace('本文件先记录实现和验证设计；实际上线以末尾部署证据及共享STATE最新项为准，未通过生产验证前不称已上线。', '2026-10-26 21:32复验完成：内网服务和DMZ均已上线flow-20261026T132000Z，原定时任务已恢复；新流程首个完整日批仍待实际运行观察。')
s = s.replace('全量回归结果待实际XML收尾后另录；未跑完不称通过。', '''全量实际结果：**522通过/7跳过/0失败/0错误，退出0**，529项，耗时约674秒。证据：[XML](../../../implementation/var/upload-flow-regression.xml)、[日志](../../../implementation/var/upload-flow-regression.log)。7跳过为：>2GB PG专用环境、live-stack整栈、Windows FD布局、SQLServer/Oracle真实DSN、pyodbc已安装的反向分支、50GB专门压力窗口。隔离PG租约测试另有1通过，不把它等同>2GB压测。''')
s += '''
## 实际部署与核验

内网升级21:25:41开始、21:27:36完成，标准upgrade.sh退出0；一致性备份`<prod-path>/backups/20261026T132541Z/`。当前`/opt/blyb/current`指向flow发行，六容器healthy，timer为active/waiting，迁移仍1—13，无本轮DDL。宿主driver SHA绑定见[deployment-bindings.json](evidence/deployment-bindings.json)，旧driver保存在`/root/blyb-maintenance-backups/upgrade-flow-20261026T132000Z/daily-upload.before.py`。

DMZ初次保护检查后因维护父目录不存在而退出，未创建候选发行、未切换服务；只读检查证实后创建指定维护目录，21:31:41升级成功。新current为`/opt/blyb-dmz/releases/flow-20261026T132000Z`；原发行20261021T113750Z保留，check-config退出0、service active。没有改证书/时钟/配置或上游限额。

| 核验 | 真实结果 | 证据 |
| --- | --- | --- |
| 内网升级 | exit0，driver已更新，timer恢复 | [升级状态](evidence/app-upgrade_status-20261026T132807133231Z.json) |
| 生产临时合成TOP7 | 一次假网络调用后第二次被阻挡；不触真实平台 | [运行时验收](evidence/app-smoke-20261026T132831415939Z.json) |
| 正式已成功单只读查询 | CONFIRMED，result_type3/sub_process_flag51，1页1窗 | 同上 |
| 共享目录 | 实际创建，mode0700，限流文件持久化 | 同上 |
| DMZ上线 | 退出0，实际源码SHA一致，active | [升级结果](evidence/dmz-dmz_upgrade-20261026T133141960785Z.json) |
| 内网→新DMZ | 实际mTLS GET /health=200；上游调用0 | [健康检查](evidence/app-health-20261026T133210584539Z.json) |
| 最后内网复验 | 六healthy、原授权APPROVED、UI/资产200、SHA一致、timer等待；旧247/配额/断点一致 | [最终复验](evidence/app-verify-20261026T133227730545Z.json) |

证据链接以本目录实际文件名为准；自动整理时逐项匹配真实采集时间，不重写证据内容。本轮真实新上传0、HIS查询0、业务账本写入0（升级运维记录/共享限速文件除外）。正式只读查询验证当前客户端、mTLS转发、签名、平台状态响应链路；不等同已经实测新版本上传一张新单。

## 后续执行与恢复

原timer继续按既有批准范围采集并发送，无需再启用一个定时任务。下一轮先看`systemctl status blyb-daily-upload.timer blyb-daily-upload.service`、`/var/log/blyb/daily-upload/`日报和看板；核对候选数、已发送/受理/确认、QUERY_ERROR、平台等待秒数、待处理job和耗时。遇TOP7应等待，不能启动另一个人工补发脚本绕过共享状态。每日超过30min排队或1天无进展会明确失败，应按证据核查，而非放任无限等待。

已核实的旧41平台成功仍需带原查询证据受控回写；旧206 NOT_FOUND先核人工历史和冻结身份，制定批准恢复单再发，不自动清UNKNOWN/重置12轮。管理电脑正式IP SAN证书、真实浏览器验收、明细分页等原待办仍按原记录处理。

回退须先暂停timer、等在途请求结束并保存新回执。内网用现有发行回退流程恢复ui-clarity-20261026T063000Z，且**同时**把上述daily-upload.before.py恢复到`/opt/blyb/tools/blyb-daily-upload.py`，核验旧SHA，再恢复timer；DMZ原子切回20261021T113750Z、restart该service并验证mTLS健康。采用现有维护规范操作，不直接在有在途请求时切换。业务库不恢复旧备份覆盖新回执；本轮只新增运维JSON，旧版本忽略，不手工删除冷却文件冒充恢复。
'''

# Bind evidence links to actual filenames, not guessed microsecond suffixes.
for phase, needle in [
    ('app-upgrade_status-', 'app-upgrade_status-20261026T132807133231Z.json'),
    ('app-smoke-', 'app-smoke-20261026T132831415939Z.json'),
    ('dmz-dmz_upgrade-', 'dmz-dmz_upgrade-20261026T133141960785Z.json'),
    ('app-health-', 'app-health-20261026T133210584539Z.json'),
    ('app-verify-', 'app-verify-20261026T133227730545Z.json'),
]:
    candidates = sorted((HERE/'evidence').glob(phase+'*.json'))
    valid = [p for p in candidates if json.loads(p.read_text(encoding='utf8'))['exit_code'] == 0]
    assert valid, phase
    s = s.replace(needle, valid[-1].name)
readme.write_text(s, encoding='utf8')

handoff = ROOT/'docs/ai/sessions/DTR-UPLOAD-FLOW-20261026.md'
assert not handoff.exists()
handoff.write_text('''# DTR-UPLOAD-FLOW-20261026 · 上传与限流改进交接

工具Codex，模型unknown；会话20261026T125856Z-42c112e3。用户要求改进并按接口上传，本轮延续现有定时授权，未扩范围。初始已读START_HERE/STATE/HANDOFF及工作区约定；没有并行代理。修改范围为登记的implementation、docs/ai、docs/catalog、任务运维目录、业务README，并按协议扩登记既有定时driver单文件。

[完整实现、真实证据和恢复步骤](../../operations/drug-trace-upload-flow-20261026/README.md)。上线版本flow-20261026T132000Z，基于ui-clarity-20261026T063000Z；manifest只含11个实际源码/部署差异，无依赖变更，无业务库迁移。另单独更新SHA受保护的宿主driver，DMZ单独只替换forwarder。既有基线在任务baselines和服务端私有维护目录，其他AI的未提交工作保留。

## 已实测

- TOP7/429完整错误与等待提示，跨进程持久节奏、共享冷却、触网前额度预检、真实查询延迟/退避、日期窗合并、终态跳过、原job fenced延后、driver等待适配、DMZ Retry-After，详见README。
- `python -m pytest ...test_drug_trace_upload_flow.py ...test_drug_trace_transport_pacing.py ...test_drug_trace_fix3.py ...test_dmz_forwarder.py -q -ra`：69通过1跳过，exit0；API/driver局部54通过exit0，PG专项1通过exit0。完整命令、PATH和XML见README。
- `python -m pytest tests -q -ra --junitxml=var/upload-flow-regression.xml`：522通过/7跳过/0失败/0错误，exit0。7项环境/大型压力跳过明列README，未声称全环境通过。
- `python docs/operations/drug-trace-upload-flow-20261026/build_release.py`：离线构建exit0、pip freeze相同；transfer_release.py：固定主机指纹/SHA传输成功；`check.py app upgrade`标准升级并安全更换driver，最终upgrade_status exit0、timer恢复0。
- `check.py dmz dmz_upgrade`：初次维护父目录缺失保护退出，无现场变更；只读确认、仅创建指定父目录后重跑成功exit0，原发行保留。新DMZ源码SHA绑定文件一致、check-config0、active。
- `check.py app smoke`：生产运行时临时合成TOP7一次假网络、第二次零触网；正式一笔已确认单只读查3/51，1页1窗。新共享目录真实0700且创建状态文件。`check.py app health`：新DMZ实际mTLS健康200，上游0。
- `check.py app verify`最终exit0：六healthy、原授权APPROVED、UI200且JS/CSS hash一致、timer active/waiting、旧247摘要/配额/断点不变。没有新上传、没有HIS查询、没有业务状态写回。

## 实现未测与待办

首个新流程完整日批、持续限流后的真实收尾、07:30日报SLA尚待下一轮；平台接口总频率阈值和NAT共享系统待平台/网络确认。不能承诺零限流。旧41平台3/51尚未本地回写，206仍UNKNOWN且人工核查为NOT_FOUND，先核人工历史再制定恢复清单，未重发、未重置原12轮。管理电脑TLS/IP SAN、生产浏览器、分页等原待办不变。

## 下一AI

先读共享STATE本任务最新项和README，确认当前发行、timer和业务日报；不要重跑build/upgrade生成器覆盖不可变包，不要重复创建定时任务。查平台须加载同一生产env共享限速；等待提示不是成功，超时仍UNKNOWN。若需要回退，必须内网发行和宿主driver成对恢复，DMZ恢复旧current；保留新回执，不倒灌旧业务库。下一批若失败先留脱敏sub_code/sub_msg/request_id、job状态及等待时间，再定修复范围。
''', encoding='utf8')

catalog_path = ROOT/'docs/catalog.json'
catalog = json.loads(catalog_path.read_text(encoding='utf8'))
for path, category, title in [
    ('docs/operations/drug-trace-upload-flow-20261026/README.md', 'operations', '追溯上传与限流改进实录'),
    ('docs/ai/sessions/DTR-UPLOAD-FLOW-20261026.md', 'shared_ai_state', '追溯上传与限流改进交接')
]:
    assert not any(d['path']==path for d in catalog['documents'])
    catalog['documents'].append(dict(path=path,category=category,title=title))
catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+'\n', encoding='utf8')

bundle=ROOT/'implementation/var'/RID
manifest=json.loads((bundle/'manifest.json').read_text())
hashes={p:hashlib.sha256((bundle/'implementation'/p).read_bytes()).hexdigest()
        for p in manifest['delta']['files'] if not p.startswith('deploy/')}
for p in manifest['delta']['files']:
    assert (ROOT/'implementation'/p).read_bytes()==(bundle/'implementation'/p).read_bytes(),p
driver=ROOT/'docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py'
binding=json.loads((HERE/'evidence/deployment-bindings.json').read_text())
assert hashlib.sha256(driver.read_text(encoding='utf8').encode()).hexdigest()==binding['driver_after']
remote="""from pathlib import Path
import hashlib,json,subprocess
expected=EXPECTED
code='from pathlib import Path;import hashlib,json;expected='+repr(expected)+';print(json.dumps({p:hashlib.sha256((Path(\"/app\")/p).read_bytes()).hexdigest()==h for p,h in expected.items()}))'
p=subprocess.run(['docker','exec','<worker-container>','python','-c',code],capture_output=True,text=True,timeout=20)
assert p.returncode==0,'SOURCE_HASH_CHECK_FAILED'
out={'runtime_source_hashes':json.loads(p.stdout)}
assert all(out['runtime_source_hashes'].values())
out['driver_sha256']=hashlib.sha256(Path('/opt/blyb/tools/blyb-daily-upload.py').read_bytes()).hexdigest()
assert out['driver_sha256']=='DRIVERHASH'
sql=\"SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x\"
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=20)
assert p.returncode==0
out['states']=json.loads(p.stdout)
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
""".replace('EXPECTED',repr(hashes)).replace('DRIVERHASH',binding['driver_after'])
compile(remote,'final_remote','exec')
(HERE/'final_remote.py').write_text(remote,encoding='utf8')
print(json.dumps({'docs_updated':12,'frozen_delta_verified':len(manifest['delta']['files']),'driver_matches':True}))
