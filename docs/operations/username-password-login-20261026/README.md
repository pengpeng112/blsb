# 用户名、密码登录改造与上线实录

## 交付结果

2026-10-26已实际部署`auth-20261026T043500Z`。入口仍为`https://<prod-host>/ui/#/drugtrace`；生产页面使用用户名、密码登录，支持左下角“修改密码”和“退出”。旧浏览器如已有登录状态，可先退出再用新账号登录。

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
- [生产维护验证](evidence/app-verify-20261026T044844198738Z.json)：原管理令牌scope200，证据APPROVED；旧247摘要、断点、额度在维护间保持不变，迁移013已登记，六容器healthy，服务器UI和JS/CSS均200。
- [生产浏览器尝试](evidence/browser-production.json)：受阻于跳板禁止direct-tcpip，ERR_CONNECTION_CLOSED，未点击任何业务操作。**生产浏览器打开尚未验收**，不能用本机合成截图替代。为验证曾使用仅绑定loopback的临时SSH转发并单独固定服务证书SPKI；跳板拒绝后没有绕过，测试监听已关闭。

首轮新增测试有1个错误假设（既有storage.list仅要求认证，不要求特定角色）；已用实际有角色门禁的tenants接口验证动态权限，未放松任何现有门禁。初始全量因未设置Git Bash PATH主动中止，最终独立全量使用现有Git Bash+UTF8通过。nginx首次隔离网络不提供宿主发布端口，修测试网络后实际请求验证通过。首次维护命令因未按root身份操作root驱动被拒，自动恢复旧runner与timer，补正`docker exec -u0`后才排空/升级；失败证据保留。浏览器早期临时主机名末尾为数字不被Chromium接受，测试主机名已修正，随后确证SSH策略阻断。上述均未伪装为一次通过。

## 部署与上报连续性

离线镜像基于queue-20261026T010000Z-r2，依赖pip freeze完全相同，差异11个源码/配置文件；白名单未包含运行库/本机凭据。镜像backend `sha256:fc403496734382f87b36b81bb9a1d22aaedff040abfd1d23abe3e18bc706cd11`。工作源码与冻结发行11文件零漂移，现场密码模块SHA与冻结源码一致。

12:44:19在驱动停止发新请求且SENDING/在途/待发job均0后记录维护中断。旧补发已新增346次受理，保留1张已准备未发送单据，used1572；旧41+206和源断点46均不变。没有清除中断前的空活动回执或改写历史日报为成功。

按已有backup.sh→install.sh完成升级，实际exit0，迁移013、六容器healthy。备份`<prod-path>/backups/20261026T044439Z/`；私有日志`/root/blyb-maintenance-backups/upgrade-auth-20261026T043500Z/`；中断与校验`/root/blyb-maintenance-backups/auth-20261026/`。旧runner另存`/var/log/blyb/mapping-recovery-20261026/maintenance-interruption.json`，原回执和progress保留为历史。

12:49:00已启动`blyb-mapping-recovery-auth-20261026.service`，按原driver SHA继续10-25→10-24，只复用DONE采集，守原幂等/额度/证据/查询预算，不重发已受理或未知单据。当前2026-10-26 12:50:36仍RUNNING，used1590；旧247/断点仍不变。原每日timer已恢复enabled/waiting。旧runner的failed/SIGKILL是这次计划中断的记录，正常daily.service的旧exit1也是此前批次历史，勿误判为新一轮平台失败。

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
