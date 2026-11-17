"""Publish the verified implementation, deployment and remaining access limits."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import ast,hashlib,json,re,shutil,xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];EVID=HERE/'evidence'
RID='auth-20261026T043500Z'
latest=sorted(EVID.glob('app-status-*.json'))[-1]
status=json.loads(latest.read_text('utf8'))['result']
verify=json.loads(sorted(EVID.glob('app-verify-*.json'))[-1].read_text('utf8'))['result']
provision=json.loads((EVID/'provision-login.json').read_text('utf8'))
browser=json.loads((EVID/'browser-production.json').read_text('utf8'))
suite=ET.parse(ROOT/'implementation/var/auth-password-regression-final.xml').getroot().find('testsuite')
assert suite is not None and suite.get('failures')=='0' and suite.get('errors')=='0'
passed=int(suite.get('tests'))-int(suite.get('skipped'))
skipped=int(suite.get('skipped'))
assert passed==499 and skipped==7 and provision['exit_code']==0
assert status['release']==RID and 'running' in status['resumed_unit']
clock=datetime.fromisoformat(json.loads(latest.read_text('utf8'))['captured_at']).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
manifest=json.loads((ROOT/'implementation/var'/RID/'manifest.json').read_text('utf8'))
drift=[p for p in manifest['delta']['files'] if (ROOT/'implementation'/p).read_bytes()!=(ROOT/'implementation/var'/RID/'implementation'/p).read_bytes()]
assert not drift
assert hashlib.sha256((ROOT/'implementation/packages/contracts/passwords.py').read_bytes()).hexdigest()==verify['password_module_sha256']

header=f'''## 最新：网页登录已改用户名/密码并上线（{clock}）

[实现与部署实录](../operations/username-password-login-20261026/README.md)：生产{RID}，迁移013仅新增账号/会话表，六容器healthy。生产登录页改为用户名/密码；账号admin对应console-admin，实际密码仅存本机受限文件，位置见实录，不使用root/HIS密码。
密码加盐PBKDF2，8h会话/退出撤销/改密撤销全部会话/5次失败暂锁15min；原自动化Bearer不轮换。专项17通过，PG并发补验1通过，全量499通过/7跳过/0失败；本机合成浏览器登录、刷新、改密、退出通过，nginx真实限流401→429通过。
生产实际密码登录200、错误401、跨storage200、退出200后旧会话401，原令牌scope200；旧247摘要/配额/源断点在维护期间不变。服务器UI与JS/CSS均200；当前电脑443 TLS仍未验收，跳板明确禁止direct-tcpip，未绕过；生产浏览器验证受阻，本轮未启用HTTP或替换证书。
12:44在发送排空后暂停旧补发runner并留维护中断证据，完成一致性备份/升级；12:49已按原driver/相同两日窗口恢复到blyb-mapping-recovery-auth-20261026，日志/var/log/blyb/mapping-recovery-auth-20261026/。当前仍RUNNING，旧41受理/206未知未重发；旧runner的failed/SIGKILL是计划维护历史，不是新增业务发送失败。每日timer已恢复enabled/waiting；NTP仍选中<intranet-host>。
下一棒只读check.py app status或上述final.json核对补发终态，勿重跑旧launcher/另开driver。正式看板访问、旧247平台核查、outcomes筛选边界仍待办。[交接](sessions/AUTH-PASSWORD-20261026.md)。

'''
readme=f'''# 用户名、密码登录改造与上线实录

## 交付结果

2026-10-26已实际部署`{RID}`。入口仍为`https://<prod-host>/ui/#/drugtrace`；生产页面使用用户名、密码登录，支持左下角“修改密码”和“退出”。旧浏览器如已有登录状态，可先退出再用新账号登录。

用户名：**admin**。实际密码已生成并保存在[本机受限登录资料](../../../.ai/private/console-password-20261026/登录账号_仅本机.md)；同目录console-login.json供受控维护读取。ACL仅当前Windows账户和SYSTEM，目录被Git忽略；本报告、工具输出和聊天不含密码或会话令牌。网页账号不是SSH root、HIS用户或平台AppSecret，三者不受改密影响。

账号对应first-hospital租户的console-admin主体，角色platform_admin。密码没有写死在前端、种子或镜像中；未来升级bootstrap不重置此账号。用户在网页改密后需同步自己的本机留存；维护脚本不会自动覆盖服务器已有密码。

## 认证实现

| 项目 | 实际行为 |
|---|---|
| 密码保存 | 标准库PBKDF2-HMAC-SHA256，600000次迭代，随机16字节盐；只存哈希 |
| 账号 | 3—64位ASCII字母/数字/点/下划线/短横线，去首尾空白、大小写归一，用户名全局唯一 |
| 密码长度 | 12—128个字符，不截断密码、不去密码空白 |
| 防试错 | 已有账号连续5次失败暂锁15分钟，PG行锁避免并发绕过；不同失败原因统一401/通用文案 |
| 网关 | 真实nginx登录路由按客户端IP限5次/分钟、burst5，过量429，登录体上限8k |
| 网页会话 | 独立随机不透明令牌，服务器只存SHA256；固定8小时；每用户最多5个有效会话 |
| 浏览器保存 | 仅sessionStorage，不长期保存密码或新会话令牌；关闭标签/过期需重新登录 |
| 退出/改密 | 退出撤销当前会话；改密/管理员重置撤销全部网页会话 |
| 权限 | 每次请求重读主体状态/角色，校验会话与主体租户一致；停用主体/租户立即拒绝会话 |
| 服务间 | core/storage/exchange共用认证依赖，网页会话可按原角色访问；保持租户过滤 |
| 自动化 | 原有Bearer和token登录API继续支持；不轮换定时任务、Agent或运维令牌；生产UI不显示令牌输入框，开发模式另有开发工具入口 |

实现参考[OWASP密码存储说明](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[认证说明](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)。未引入新密码/JWT依赖。

## API及迁移

| 方法与路径 | 请求/返回 |
|---|---|
| POST /api/v1/auth/login | 新请求username/password；成功返回原主体信息以及access_token、token_type、expires_at；响应no-store。原token请求兼容，不返永久管理令牌 |
| GET /api/v1/auth/me | 当前主体/角色/是否password_session；用于页面刷新校验，不重新签发会话 |
| POST /api/v1/auth/logout | 当前Bearer会话撤销，返回logged_out；机器令牌不会被这个接口撤销 |
| POST /api/v1/auth/password | current_password/new_password，仅密码会话可用；成功后全部网页会话失效 |

迁移`013_console_password_sessions`只新增console_account/console_session及索引，原认证主体、业务表、历史payload、批准字典、证据和发送额度均未改。

主要代码：[密码/会话服务](../../../implementation/packages/contracts/passwords.py)、[统一认证](../../../implementation/packages/contracts/api.py)、[API](../../../implementation/apps/core_api/app.py)、[登录页](../../../implementation/apps/admin_web/src/views/Login.vue)、[账户维护CLI](../../../implementation/apps/manage_console_user.py)。

管理员创建/忘记密码时从服务器交互执行（密码不放命令参数）：

```bash
docker exec -it blyb-core-1 python -m apps.manage_console_user --tenant first-hospital --username admin --role platform_admin --reset-password
```

首次创建去掉`--reset-password`；已存在账户不允许静默覆盖。重置不改变既有角色、不重新启用停用账户；输入/确认新密码后全部网页会话撤销。自动化可用`--password-stdin`通过受控stdin交付，禁止在shell里硬编码真实密码。

## 实测证据

- [专项XML](../../../implementation/var/auth-password-local2.xml)：17通过，含真实隔离PostgreSQL迁移/登录，SQLite旧库增量、加盐、错误/锁定/到期/撤销/改密、租户和实时角色、保留原机器认证。
- [补充PG并发XML](../../../implementation/var/auth-password-pg-concurrency.xml)：1通过，5个同时错误登录触发同一账号锁定，未改密码或取消关键断言。
- [全量XML](../../../implementation/var/auth-password-regression-final.xml)：**499通过、7跳过、0失败**，exit0，639.76秒；跳过仍是未授权真实源、运行live-stack、50GB/2GB专项与Windows FD等既有条件，不冒充这些已测。
- [本机构建页面浏览器实测](evidence/browser-synthetic.json)：错误密码拒绝、正确登录、刷新保持、改密重登、退出撤销全部通过，JS和服务端500错误均0。截图[登录表单](evidence/synthetic-login.png)只含合成环境、填密码前的页面。
- [nginx实测](evidence/nginx-synthetic.json)：用原生产配置，nginx -t退出0；10次请求为6次401、4次429，TLS校验开启，合成接口无业务发送。
- [生产账号API实测](evidence/provision-login.json)：错误密码401、正确200、me200、storage200、退出200、退出后401；没有输出任何密码/令牌。
- [生产维护验证](evidence/{sorted(EVID.glob('app-verify-*.json'))[-1].name})：原管理令牌scope200，证据APPROVED；旧247摘要、断点、额度在维护间保持不变，迁移013已登记，六容器healthy，服务器UI和JS/CSS均200。
- [生产浏览器尝试](evidence/browser-production.json)：受阻于跳板禁止direct-tcpip，ERR_CONNECTION_CLOSED，未点击任何业务操作。**生产浏览器打开尚未验收**，不能用本机合成截图替代。为验证曾使用仅绑定loopback的临时SSH转发并单独固定服务证书SPKI；跳板拒绝后没有绕过，测试监听已关闭。

首轮新增测试有1个错误假设（既有storage.list仅要求认证，不要求特定角色）；已用实际有角色门禁的tenants接口验证动态权限，未放松任何现有门禁。初始全量因未设置Git Bash PATH主动中止，最终独立全量使用现有Git Bash+UTF8通过。nginx首次隔离网络不提供宿主发布端口，修测试网络后实际请求验证通过。首次维护命令因未按root身份操作root驱动被拒，自动恢复旧runner与timer，补正`docker exec -u0`后才排空/升级；失败证据保留。浏览器早期临时主机名末尾为数字不被Chromium接受，测试主机名已修正，随后确证SSH策略阻断。上述均未伪装为一次通过。

## 部署与上报连续性

离线镜像基于queue-20261026T010000Z-r2，依赖pip freeze完全相同，差异11个源码/配置文件；白名单未包含运行库/本机凭据。镜像backend `{manifest['images']['backend']}`。工作源码与冻结发行11文件零漂移，现场密码模块SHA与冻结源码一致。

12:44:19在驱动停止发新请求且SENDING/在途/待发job均0后记录维护中断。旧补发已新增346次受理，保留1张已准备未发送单据，used1572；旧41+206和源断点46均不变。没有清除中断前的空活动回执或改写历史日报为成功。

按已有backup.sh→install.sh完成升级，实际exit0，迁移013、六容器healthy。备份`<prod-path>/backups/20261026T044439Z/`；私有日志`/root/blyb-maintenance-backups/upgrade-auth-20261026T043500Z/`；中断与校验`/root/blyb-maintenance-backups/auth-20261026/`。旧runner另存`/var/log/blyb/mapping-recovery-20261026/maintenance-interruption.json`，原回执和progress保留为历史。

12:49:00已启动`blyb-mapping-recovery-auth-20261026.service`，按原driver SHA继续10-25→10-24，只复用DONE采集，守原幂等/额度/证据/查询预算，不重发已受理或未知单据。当前{clock}仍RUNNING，used{status['db']['quota'][0]['used']}；旧247/断点仍不变。原每日timer已恢复enabled/waiting。旧runner的failed/SIGKILL是这次计划中断的记录，正常daily.service的旧exit1也是此前批次历史，勿误判为新一轮平台失败。

**最新补发日志位置已变更**：`/var/log/blyb/mapping-recovery-auth-20261026/`下baseline.json/progress.json、分日json/.stderr、final.json或error.json。不要继续只盯旧runner的RUNNING快照；不要重跑一次性launch、parallel driver、重置额度或盲重发UNKNOWN。driver窗口完成也不等于平台所有新受理已终态，旧247仍待单独核查。

只读接手命令：

```powershell
$env:PYTHONUTF8='1'
python docs/operations/username-password-login-20261026/check.py app status
```

回退用旧发行queue-20261026T010000Z-r2；先按运行任务边界停新驱动/排空发送、备份，再走原rollback.sh。013为新增表，旧代码忽略这些表，回退后密码网页登录不可用、旧机器认证保留；不回滚业务数据库覆盖已发送回执和配额、不删除账号表造旧状态。

## 尚未完成的访问及业务事项

账号认证方式已经上线，但**当前管理电脑直连443 TLS问题、正式IP SAN证书仍未解决；HTTP80未启用**。这些是访问链路和证书事项，不能靠更换用户名密码解决。本次没有全局关闭TLS验证，没有修改DMZ转发或平台HTTPS。

NTP仍为<prod-host>→<intranet-host>且正常。积压补发最终回执待持续观察；旧41受理/206未知仍需平台核对。其他既有outcomes先限后筛、长采集问题没有混入此次认证改造。

[本次交接](../../ai/sessions/AUTH-PASSWORD-20261026.md)。
'''
(HERE/'README.md').write_text(readme,encoding='utf8')

paths=['docs/ai/STATE.md','docs/ai/HANDOFF.md','docs/ai/BACKLOG.md','数据上报/1、码上放心平台/README.md','docs/catalog.json']
for rel in paths:
    dest=HERE/'baselines'/rel;dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():shutil.copy2(ROOT/rel,dest)
for rel in paths[:3]:
    p=ROOT/rel;first,rest=p.read_text('utf8').split('\n',1)
    p.write_text(first+'\n\n'+header+rest.lstrip('\n'),encoding='utf8')
p=ROOT/paths[3];first,rest=p.read_text('utf8').split('\n',1)
p.write_text(first+'\n\n'+header.replace('../operations/','../../docs/operations/').replace('(sessions/','(../../docs/ai/sessions/')+rest.lstrip('\n'),encoding='utf8')

session=ROOT/'docs/ai/sessions/AUTH-PASSWORD-20261026.md'
session.write_text('# AUTH-PASSWORD-20261026\n\nCodex；模型unknown；会话20261026T041910Z-2984abaf。\n\n'+header.replace('(../operations/','(../../operations/').replace('(sessions/','(')+
    '\n本任务实现、测试、构建、部署及账号交付完成；当前管理机TLS和批次最终终态为明确待办。完整命令/结果、回退与剩余见[运维实录](../../operations/username-password-login-20261026/README.md)。\n\n'
    '无HIS访问/写入、新依赖或真实发送旁路；只恢复原已授权窗口。仅本机受限文件保存密码，公共报告和工具输出没有密码。\n',encoding='utf8')

append='''
## 2026-10-26 用户名/密码登录（AUTH-PASSWORD-20261026）

新增迁移013的console_account/console_session，用户名/密码→独立8h可撤销会话；加盐PBKDF2、5次失败15min锁定、nginx登录限流。生产Login.vue改双输入，刷新校验/auth/me；退出及修改密码均使相应会话失效。core/storage/exchange共用会话认证，原自动化Bearer/设备凭据保持兼容，未改业务scope、配额或历史payload。

接口：POST /api/v1/auth/login新增username/password分支，GET /auth/me、POST /auth/logout、POST /auth/password；legacy token请求兼容。账号维护走apps.manage_console_user交互/受控stdin，不内置默认密码、不通过公开API创建管理员。

专项17通过，PG并发补验1通过，全量499通过/7跳过/0失败；合成浏览器与真实nginx限流通过。生产auth-20261026T043500Z已上线，账号登录/拒绝/会话撤销/跨storage及保留原scope实测；管理电脑443/正式证书、实际浏览器打开仍待访问链路处理，本轮未启用HTTP。积压driver维护中断后已按同窗口幂等恢复，仍RUNNING。

证据及API细节：[本轮实录](../../docs/operations/username-password-login-20261026/README.md)。实际密码只在实录指向的本机受限目录。
'''
for rel in ['implementation/docs/系统架构与流程说明.md','implementation/docs/实施状态矩阵.md','implementation/docs/接口与设计差异.md','implementation/docs/测试执行记录.md','implementation/docs/04-API目录对照矩阵.md']:
    p=ROOT/rel;dest=HERE/'baselines'/rel;dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():shutil.copy2(p,dest)
    with p.open('a',encoding='utf8') as f:f.write('\n'+append)

catalog=ROOT/'docs/catalog.json';obj=json.loads(catalog.read_text('utf8'))
for path,category,title in [('docs/operations/username-password-login-20261026/README.md','operations','用户名密码登录改造与上线实录'),('docs/ai/sessions/AUTH-PASSWORD-20261026.md','shared_ai_state','用户名密码登录开发部署交接')]:
    if not any(d['path']==path for d in obj['documents']):obj['documents'].append({'path':path,'category':category,'title':title})
catalog.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')

broken=[]
for p in [HERE/'README.md',session]:
    for target in re.findall(r'\]\(([^)]+)\)',p.read_text('utf8')):
        if re.match(r'^(https?://|#)',target):continue
        if not (p.parent/target.split('#',1)[0]).exists():broken.append(str(p.parent/target))
for p in HERE.glob('*.py'):ast.parse(p.read_text('utf8'))
check={'source_drift':drift,'runtime_password_source_hash_match':True,'scoped_broken_links':broken,
       'passed':passed,'skipped':skipped,'operation_syntax_ok':True,'release':RID,
       'production_browser_accepted':browser.get('rendered',False),'resumed_batch_running':True}
(EVID/'local-checks.json').write_text(json.dumps(check,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps(check,ensure_ascii=False))
if broken:raise SystemExit(1)
