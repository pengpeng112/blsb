<!-- RT-02:BEGIN -->
## 既有端点鉴权与行为收紧（2026-11-13，RT-02-20261113，ZCode/GLM-5.3）

> 本批无新增端点；以下既有端点补齐 `target_scopes` 范围校验/审计，机器 token
> （非 user 主体）、`platform_admin`、`["*"]` 账号行为 100% 不变。

| 方法 | 路径 | 收紧内容 |
|---|---|---|
| GET×2/POST×3/GET/POST | /api/v1/medins-emr/*（全部 7 端点） | 入口统一 `require_target_scope("medins_emr_sd")`（角色校验保持：读=任意登录，写=operator/config_editor/platform_admin） |
| PUT | /api/v1/report-targets/{code} | 入口补 `require_target_scope(code)`（403 先于 404/422，防业务存在性枚举） |
| POST | /api/v1/report-targets | 仍限 platform_admin；另加全局范围防御检查（非全局受限账号 403） |
| POST/PUT | /api/v1/alert-rules[/{code}] | 校验原 target_code 与新 target_code：系统级（空 target）要求全局范围，业务级要求该业务授权 |
| POST | /api/v1/schedules/{id}/enable·disable·run-now | 系统级调度（target_code=""）拒绝单业务 scope 受限账号（原仅校验非空 target） |
| GET | /api/v1/schedules/{id}/runs | 同上（系统级调度 runs 对受限账号 403） |
| POST | /api/v1/alerts2/{id}/ack·resolve | 系统级告警（target_code=""）拒绝单业务 scope 受限账号；并补 AuditEvent |
| PUT/POST | /api/v1/alert-rules、/api/v1/notification-channels | 补写 AuditEvent：`alert_rule.create/update`、`notification_channel.create/update`（快照不含通道 config） |
| GET | /api/v1/alerts（旧） | 受限人类账号按 `target_code.in_(授权集)` 过滤（系统级不返回；空范围→空列表） |
| GET | /api/v1/submissions（旧，列表/详情） | 受限人类账号按 `ReportTarget.destination_id` 绑定过滤/归属校验（未授权详情 404） |
| PUT | /api/v1/system/users/{username}/roles、POST /lock | 最后管理员保护重写：id 升序 FOR UPDATE 锁全租户 ACTIVE 管理员集合，剔除锁定账号；锁定活跃管理员同样受保护（409 文案统一） |
<!-- RT-02:END -->

<!-- RT-03:BEGIN -->
## RT-03 分页与行为变更（2026-11-14，RT-03-20261114）

| 接口 | 变更 |
|---|---|
| GET /api/v1/schedules/{id}/runs?limit=&cursor= | 真实 keyset（fire_time DESC, id DESC）；保留 schedule/items，新增 next_cursor；坏游标 400 INVALID_CURSOR，换 schedule/租户 400 CURSOR_CONTEXT_MISMATCH |
| GET /api/v1/alerts2?state=&severity=&target=&limit=&cursor= | 数组→{items,next_cursor}；有效时间 COALESCE(last_seen_at,created_at) DESC, id DESC；severity 归一 INFO/WARN/CRITICAL（warning→WARN，筛选 func.lower 覆盖变体）；实时列表分页（不承诺并发快照，前端按 ID 合并去重） |
| GET /api/v1/notification-logs?kind=&limit=&cursor= | 补 cursor；数组→{items,next_cursor}；行含 id；权限不变（platform_admin/auditor） |
| GET /api/v1/medins-emr/debug-exchanges?code=&limit=&cursor= | 数组→{items,next_cursor}（created_at DESC, id DESC）；medins.ts/历史表同步 |
| GET /api/v1/alerts（旧） | 数组契约不变；共享 alerts2 过滤/归一/RT-02 范围防线；severity 归一输出 |

行为契约：worker claim→mark_run_started 独立事务（RUNNING 跨 Session 可见）；
finish_job True 后仅持久化终态回写；RUN_ONCE 补 [旧 next_fire_at, now] 最近
合法槽位；SKIP 积压合并单条 MISSED（collapsed_from/to）；通道 send() 对
smtp/webhook 一律拒绝（含历史 enabled=True）；早报昨日=院内自然日窗口
（window_kind 区分），滚动 24h 单独标注。
<!-- RT-03:END -->


<!-- LT-07:BEGIN -->
## 新增端点（2026-11-11，LT-07）

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | /api/v1/medins-emr/transactions | 任意登录 | 交易清单（Profile） |
| GET | /api/v1/medins-emr/transactions/{code} | 任意登录 | 字段契约+报文头+字典+合成样例+profile 元信息 |
| POST | /api/v1/medins-emr/validate | operator, config_editor, platform_admin | 契约校验（中文逐字段错误；记 debug_exchange VALIDATE_ONLY，请求打码落库） |
| POST | /api/v1/medins-emr/send-mock | operator, platform_admin | 校验通过才发；进程内合成响应（__mock_infcode 档；不出网）；_mock 标记 |
| POST | /api/v1/medins-emr/export-request | operator, platform_admin | 院内执行包（报文+签名占位头+curl 地址占位）；真实值仅回传不落库（打码版落库） |
| GET | /api/v1/medins-emr/debug-exchanges?code= | 任意登录 | 联调历史（打码版） |
| POST | /api/v1/medins-emr/debug-exchanges/{id}/record-result | operator, platform_admin | 人工回填（success/platform_code/note≥5字；证据级别 MANUAL_OBSERVATION；审计） |
<!-- LT-07:END -->

<!-- LT-06B:BEGIN -->
## 前端消费说明（2026-11-11，LT-06B；无新后端端点）

新页面 → 端点映射（全部复用 LT-01/03/04/05/06A 已登记端点）：
Dashboard=overview+alerts2+digest/latest+schedules+runs；Targets/TargetDetail=
report-targets+summary+ledger+external-imports+destinations（只读）；
Ledger=submissions 3端点；Schedules=schedules 7端点（镜像无操作）；
Alerts=alerts2/ack/resolve+alert-rules+channels+logs+digest；
Users=system/users 5端点；AuditSearch（/audit2）=audit 扩展+详情+export。
旧 /audit 页面保留；菜单/路由更新（驾驶舱置顶；/targets/:code 动态详情）。
<!-- LT-06B:END -->

<!-- LT-06A:BEGIN -->
## 新增/修订端点（2026-11-11，LT-06A，R08）

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | /api/v1/system/users | platform_admin | 用户列表（不返回任何 hash/token） |
| POST | /api/v1/system/users | platform_admin | 新建（用户名3—32/[a-zA-Z0-9_]；密码复用现有强度校验；roles⊆ALL_ROLES 禁 service；target_scopes 默认 []，未知编码 422） |
| PUT | /api/v1/system/users/{username}/roles | platform_admin | 改角色与范围（移除最后 platform_admin→409，行锁串行化并发） |
| POST | /api/v1/system/users/{username}/reset-password | platform_admin | 管理员重置并撤销该用户全部会话 |
| POST | /api/v1/system/users/{username}/lock · /unlock | platform_admin | 锁定（远期+撤销会话）/解锁；不可自锁 |
| GET | /api/v1/audit | auditor, platform_admin | 兼容扩展：新增 actor/action_prefix(前缀)/target(前缀)/at_from/at_to/cursor；不带新参数行为与旧版逐字段一致（action 仍精确） |
| GET | /api/v1/audit/{id} | auditor, platform_admin | 详情（old_ref/new_ref 全文；列表不返回） |
| GET | /api/v1/audit/export | auditor, platform_admin | CSV（UTF-8 BOM+转义+公式注入防护）；count>10000→422；导出写 audit.export 审计 |

范围校验（R08）接入：report-targets 列表按 scope 过滤；get/summary/ledger 列表/详情/
处置/overview 聚合/external-imports 读写/schedules 列表+写+runs/alerts2 列表+ack+resolve/
alert-rules 列表——未授权（含未知 code）一律 **403 先于存在性校验**（防泄露）；
晨报 digest 对受限用户 403（全院聚合）；机器主体（agent/connector/collector）与
药品专页/机器 token 既有鉴权零改变。
<!-- LT-06A:END -->

<!-- LT-05:BEGIN -->
## 新增端点（2026-11-10，LT-05）

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| POST | /api/v1/report-center/external-imports | platform_admin, operator | body {target,source_label,source_instance,source_tz,files[{name,content_base64}],dry_run}；≤20MB(413)；source_tz 必须 UTC/CST(422)；拒绝原因只记 code 无原文 |
| GET | /api/v1/report-center/external-imports?target= | 任意登录 | 导入批次列表 |
<!-- LT-05:END -->

<!-- LT-04:BEGIN -->
## 新增端点（2026-11-10，LT-04）

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | /api/v1/schedules?target= | 任意登录 | 调度列表（含 observe_only 标注） |
| POST | /api/v1/schedules | platform_admin | 新建（enabled 强制 False 起；job_type 未注册→400 JOB_TYPE_UNKNOWN；observe_only 只能种子创建） |
| PUT | /api/v1/schedules/{id} | platform_admin | 更新（revision 乐观锁 409；镜像改 job_type/payload→403 OBSERVE_ONLY_REJECTED） |
| POST | /api/v1/schedules/{id}/enable·/disable | platform_admin, operator | reason≥5字；镜像 enable→403 |
| POST | /api/v1/schedules/{id}/run-now | platform_admin, operator | 幂等键重放同 run；FORBID 在途→409；blackout→400 BLACKOUT（本批无绕过）；镜像→403 |
| GET | /api/v1/schedules/{id}/runs | 任意登录 | 运行记录 |
| GET | /api/v1/schedules/preview?cron=&count= | 任意登录 | 未来触发预览（CST） |
| GET | /api/v1/alerts2?state=&severity=&target= | 任意登录 | 告警聚合视图（新结构）；旧 /api/v1/alerts 原样兼容 |
| POST | /api/v1/alerts2/{id}/ack · /resolve | operator, business_reviewer, platform_admin | ACK 不再重复通知；resolve 仅 DATE_EXPIRY/评估器异常类（业务类由评估器恢复） |
| GET/POST/PUT | /api/v1/alert-rules[/{code}] | 读任意/写 platform_admin(+config_editor 改) | 规则 CRUD（kind 校验 9 种） |
| GET/POST/PUT | /api/v1/notification-channels[/{code}] | platform_admin | config 禁内联秘密（SECRET_INLINE_FORBIDDEN）；smtp/webhook 启用/测试→400 NOT_ALLOWED_THIS_BATCH |
| POST | /api/v1/notification-channels/{code}/test | platform_admin | 固定文本测试；smtp/webhook 本批拒绝；file 可用 |
| GET | /api/v1/notification-logs?kind= | platform_admin, auditor | 外发日志 |
| GET | /api/v1/alerts/digest/latest | 任意登录 | 最近晨报文本 |
<!-- LT-04:END -->

<!-- LT-03-R1:BEGIN -->
## 修订端点行为（2026-11-10，LT-03-R1，落实 REVIEW R02/R08）

| 端点 | 修订 |
|---|---|
| GET /api/v1/report-center/submissions | 游标绑定 tenant+target+筛选上下文：换条件复用旧游标→400 CURSOR_CONTEXT_MISMATCH（新错误码）；游标 v2 形状 base64("v2\|指纹\|ts\|id") |
| GET /api/v1/report-center/submissions/{target}/{id} | 详情按 tenant+target+submission 绑定关系取数：领域钩子（病理按 domain）优先，其余按 target 绑定 destination 集合校验；跨 target/无绑定一律 404（不再按租户内任意 id 返回） |
| POST .../manual-resolution | 处置前提收紧：须有冻结 snapshot_digest 与 ≥1 行 DeliveryAttempt（否则 422）；target 无绑定目的地→404（旧实现空绑定放行）；RESEND_ALLOWED→PREPARED 不入队（worker 全链测试证明不被自动拾取） |
| GET /api/v1/report-targets/{code}/summary | 响应新增 window_kind/backlog_total/quality_waiting/evidence_kind：窗口计数（rolling_days）与全量未解决积压分开；药品补窗外 UNKNOWN 与无 Submission 质量等待；病理应用 days；Mock/真实证据形态标注 |
<!-- LT-03-R1:END -->

# 04 §5 API 目录逐项对照矩阵（W11/G24）
<!-- LT-03:BEGIN -->
## 新增端点（2026-11-08，LT-03）

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | /api/v1/report-center/submissions?target=&bucket=&date_from=&date_to=&q=&cursor=&limit=1..200 | 任意登录 | 统一台账（target 必填→400 TARGET_REQUIRED；先筛后限+游标；无钩子 data_source=none） |
| GET | /api/v1/report-center/submissions/{target}/{submission_id} | 任意登录 | 详情（attempts/receipts；药品 target 返回 detail_route=/drugtrace） |
| POST | /api/v1/report-center/submissions/{target}/{submission_id}/manual-resolution | business_reviewer, platform_admin | UNKNOWN 受控处置（CAS 409；理由≥5字 422；药品 target→400 USE_DOMAIN_WORKFLOW；RESEND_ALLOWED 只改回 PREPARED 不自动发送） |
<!-- LT-03:END -->

<!-- LT-01:BEGIN -->
## 新增端点（2026-11-08，LT-01）

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | /api/v1/report-targets | 任意登录 | 上报业务目录列表（code 序） |
| GET | /api/v1/report-targets/{code} | 任意登录 | 详情；不存在 404 |
| POST | /api/v1/report-targets | platform_admin | 新建；code 重复 409 |
| PUT | /api/v1/report-targets/{code} | platform_admin, config_editor | 更新（body 必带 revision 乐观锁；不符 409） |
| GET | /api/v1/report-targets/{code}/summary?days=1..90 | 任意登录 | 单业务统一状态桶摘要（钩子；无认领 data_source=none；days 越界 422） |
| GET | /api/v1/report-center/overview?days=1..90 | 任意登录 | 全业务摘要+totals 合计；单钩子异常该项 error 局部隔离 |

挂载：apps/core_api/report_target_routes.py::register（app.py 统一挂，不依赖领域启用）。
另有 /api/v1/admin/overview 改为钩子聚合（键集合不变，cases 两键改由病理钩子提供）。
<!-- LT-01:END -->

<!-- DTR-SCOPE-DEV-20261027:BEGIN -->
## 最新：口径扩展开发完成（2026-10-27，DTR-SCOPE-DEV-20261027，ZCode/GLM-5.3）

[RESULT（实测/未测/阻断+部署清单）](../../docs/development/drug-trace-scope-dev-20261027/RESULT.md)：本地开发完成未部署。
**API 变化（唯一新增面）**：`POST /api/v1/drug-trace/collection-runs` 复用扩展——body 新增
`mode='rescan'` + `bill_keys:[{source_system,pharmacy_code,business_kind,source_bill_id}]`
（≤500 键/批、operator|platform_admin、audit `drug_trace.rescan.create`、job 类型
`drug_trace.rescan`）；rescan 模式下 window_* 可省略（run 记录无窗口）。既有
synthetic/oracle 窗口模式请求/响应形状不变；`GET overview` 响应新增
`revisions_not_reportable` 计数字段（只增不改）。上游 TOP uploadretail/
query.billstatus 签名与字段零改动；bill_type/query_bill_type 字典对 r11 新分支
（INPATIENT_DISPENSE/PATIENT_RETURN）改用稳定 business_kind 作键（r10 门诊原键不变），
字典 DRAFT 未批准 → prepare 仍 DICTIONARY_UNMAPPED 拒绝（fail-closed）。
<!-- DTR-SCOPE-DEV-20261027:END -->


<!-- DTR-RECOVERY-STABILITY-20261027:BEGIN -->
## 最新：本批全部确认、旧41已回写、看板与驱动已升级（2026-10-27 15:31）

[实现与验收](../../docs/operations/drug-trace-recovery-stability-20261027/README.md)：<prod-host>当前发行stability-20261027T064000Z-r3，继承r2的采集稳定性修复；无业务库DDL/新依赖，DMZ维持flow发行，视图r10/冻结payload/批准字典/发送范围保持。截至2026-10-27 15:31，10-26 VALID 1995张：1995确认、0受理、0未发送、0发送中、0未知。

已有collection-runs、prepare/send/query和daily-reports路径/权限未变；分页只在worker内部，同run_id/复合断点恢复。失败采集字段进入原collection JSON，新增进度进入原summary JSON，未修改TOP平台签名/字段/类型/终态语义。HTTP200/TOP7仍UNKNOWN先查询；本次特定一张经新鲜完整NOT_FOUND+审计+精确CAS、原perform_send至多一次重试，不成为通用自动重发策略。

日报展示与宿主驱动预算补强亦已部署stability-20261027T064000Z-r3，失败记录不再把未知候选冒充0，详情直显采集原因/回执进度；前端7项与18项合成浏览器通过、零业务POST，worker/API源码与r2相同。宿主driver另补排队/冷却/执行等待统一总预算，局部55通过。UI/driver升级间4818条提交、旧205、配额/断点不变。
最终驱动/应用全量：595通过/7跳过/0失败/0错误，实际退出0。
<!-- DTR-RECOVERY-STABILITY-20261027:END -->

## 最新：上传与限流处理已改进上线（2026-10-26 21:32）

[实现与验收](../../docs/operations/drug-trace-upload-flow-20261026/README.md)：DTR-UPLOAD-FLOW-20261026已部署flow-20261026T132000Z至内网和DMZ；依赖、视图r10、批准字典、冻结payload、scope/配额和业务数据库schema不变。

已有prepare/send/query等API路径及权限未变；send在已知冷却时排队，query遇平台限流返回查询错误和脱敏rate_limit，不冒充未找到。上游仍uploadretail与query.billstatus，冻结零售321/查询A经只读聚合核实。DMZ仅本地429增加Retry-After:60，不放宽白名单/mTLS或开放代理。HTTP受理仍不是3/51业务确认。

## 2026-10-26 药品追溯界面优化（DTR-UI-CLARITY-20261026）

[页面说明、测试与部署](../../docs/operations/drug-trace-ui-clarity-20261026/README.md)：ui-clarity-20261026T063000Z实际上线，五个入口、四状态卡、趋势/问题集中、中文状态和自适应布局。仅展示逻辑变化，无API/枚举/数据库迁移/后端依赖变更；ACCEPTED不算最终成功、UTC提交与北京时间业务日报区分，空/失败不判正常，近期200笔明细及先限后筛边界有提示。同日timer/manual详情按点击来源选择。
页面默认GET只读，真实演练证据入口隐藏，手工发单/停发需确认且后端原角色/授权不变。辅助测试5通过、合成浏览器13项/零业务POST、vue-tsc及Vite成功；生产scope/UI/资产200、哈希一致、六容器healthy、timer恢复。未修改源视图或发送范围，不新增HIS/平台测试。
升级等原补发自然结束并排空，一致性备份后标准升级；旧247、源断点、维护间配额均未变。生产管理电脑TLS/IP SAN与真实浏览器仍待验收，合成截图不替代生产。后端明细分页/先筛后限、旧247核对及补发/日报合并呈现仍待办。

## 2026-10-26 排队超时与查询预算修复（DTR-QUEUE-REPAIR-20261026）

[现场部署/解释/验证](../../docs/operations/drug-trace-queue-repair-20261026/README.md)：queue-20261026T010000Z-r2上线，现有012不变，依赖不变。
send/query现有队列priority50/60；worker每5槽保障1个后台任务，仍使用原lease/fencing。driver按原job区分排队1800s和执行180s等待，不重复投递；过滤12轮用尽及在途查询，记录skipped_exhausted/inflight。
日报列表追加query_errors_count/query_skipped_exhausted；UI分开展示准备/发送错误、查询错误及轮次用尽，“发送请求”不等于CONFIRMED。
生产复用10-25 DONE与567 PREPARED恢复；09:09新增71受理/65确认，仍在运行。旧41+206状态/版本摘要、源断点46均未变，配额按真实attempt递增；未修改批准字典/范围/未知重发规则。
最终局部47 pass、全量482 pass/7 skip/0 fail（var/queue-repair-regression-final.xml）；同源构建和源码/镜像/driver SHA核对通过。恢复最终收尾及药房映射/旧单核清仍待跟踪。
已发现未修：问题明细先limit200再筛可能漏较旧异常，不能凭列表空认定业务清零；聚合统计不受此展示边界影响。

## 2026-10-25 生产看板/限流部署（DTR-NTP-DASHBOARD-20261025）

[现场实录](../../docs/operations/drug-trace-ntp-dashboard-20261025/README.md)：生产ui-20261025T140000Z已上线，迁移012与六容器健康；日报开关1，timer下一次10-26 06:10。
RealTransport按DMZ origin跨实例统一1.5秒间隔（单worker，40次/分），上传/查询分页共用；429冷却后续请求60秒，不自动重试POST。
驱动发现发送结果UNKNOWN/90秒仍在途即中止后续新发送；REJECTED/PARTIAL计失败。日报错误超过50条时保留样例+errors_total/errors_truncated，文件保留完整错误；API列表按准确总数，UI说明截取。
日报单据日期/平台终态原口径不变。补录10-24 manual/PARTIAL_FAILURE明确由数据库重建；654是attempt数，非成功数。当前全库409 CONFIRMED/41 ACCEPTED/206 UNKNOWN，815准备失败缺两药房企业映射，未自动补字典或重发。
DMZ NTP正常；<prod-host>持续轮询但UDP123未通。服务端页面/资源200，本机TLS EOF且证书无IP SAN，浏览器未验收；下一次完整日批亦未验收。

## 2026-10-22 首张真实试报（DTR-ONE-BILL-TRIAL-20261022）

**已实际上传1张4码，平台3/51处理成功/全部成功，本地CONFIRMED；仅一次attempt，quota1/1。**
用户明确延期正式CA/持续NTP/书面及历史确认，并明确不撤销；通过现有API登记有明确disposition的USER_SINGLE_BILL_EXCEPTION，固定冻结配置/一秒窗口/药房/额度1。
配置与审批保留，目的地ENABLED，DMZ仅增加uploadretail/保留query/库存关闭；没有开启自动批量或改源码。
[实际结果与回执](../../docs/development/drug-trace-one-bill-trial-20261022/README.md)。
本次证明该AppKey/医院的uploadretail及321该笔可用；正式CA/NTP/旧厂商未报仍未实证，不把延期写成完成。
本地4条SubmissionItem仍PENDING与整单CONFIRMED分开记录，独立码级对账未做。

## 2026-10-22 来源接入部署补充（DTR-SOURCE-DELIVERY-20261022）

**现场已完成：src-20261022T103000Z/六healthy、专用reader绑定、r10来源PUBLISHED/ENABLED、三字典新revision、真实1张4码采集DONE/VALID及冻结PREPARED，实际五必填DRY_RUN过。上传0，attempt/quota/send_job0。**
[详细实现、现场及剩余](../../docs/development/drug-trace-source-deploy-20261022/README.md)。
原HIS广源登记不改；新app来源固定r10两视图/reader，仅batch，未知分支保持等待。

| 新接口（/api/v1前缀） | 权限/约束 | 验证 |
|---|---|---|
| POST /drug-trace/sources/{connector_id}/config-revisions | editor存草稿；publish/enable须admin，Oracle需collect证据；dry_run零写；固定r10绑定，无密码/DSN明文 | 角色/跨租户/无证据拒绝/幂等换代；现场发布启用成功 |
| POST /drug-trace/sources/{connector_id}/disable | admin，worker页边界复核停止 | API与既有worker安全点回归 |
| POST /drug-trace/destinations/{dest_id}/activation | admin；真实启用须PUBLISHED配置+send精确配置证据；停用无需伪造发送批准 | 缺证据409、越权403测试；生产启用未执行 |

对照对象：设计交付v0.4《04_数据模型与内部接口契约》§5 API目录 ↔ 实现代码
（apps/core_api、apps/storage_service、apps/exchange_gateway、plugins/domains/pathology/routes.py）。
更新：2026-10-01（整改收尾轮）；**2026-10-02 本地收尾轮（第4轮）：登记缺口全部补实现**（见各行"本轮"标注）。

状态取值：**已实现**（有路由+测试）/ **等价实现**（能力存在，入口形态不同，附ADR）/ **未实现**（登记缺口）。

## 共同约定达成情况

| 约定 | 状态 | 说明 |
|---|---|---|
| 统一成功/错误信封 | 已实现 | `{data, request_id}` / `{error:{code,message,retryable,field_errors,details_ref}}`；错误不回显连接串/令牌/患者值（E01） |
| Idempotency-Key | 已实现 | acquisitions、imports、upload-sessions、ingest幂等（同键同内容复用，异内容409）；T2 ingress 按领域唯一键（外部ID+revision）幂等 |
| If-Match/row_version（428/412） | 已实现 | manual-decisions 与 DB decision_version 比对（R12）；UI已发送If-Match（Review.vue） |
| cursor分页 | **已实现（本轮T8统一）** | 全部核心列表（submissions/assets/acquisitions/quality-issues/audit/publications/operations/alerts + workflow-runs）统一 keyset 游标 `cursor=created_at\|id`（audit 为 `at\|id`）；cursor 解析 `datetime.fromisoformat` 后比较；limit 上限200；**无 cursor 请求保持原列表形状（向后兼容），提供 cursor 参数（含空串）返回 `{items, next_cursor}`**；测试 test_T8* |
| 异步202+operation_id | 已实现 | complete/verify/effective-scan/**storage.migrations(T5新增)** 等；Operation 终态与Job同步（R28；T5多资产Job共同结算） |

## 5.1 设备与文件

| 设计端点 | 状态 | 实现/ADR |
|---|---|---|
| POST /agents/enroll | 已实现 | 一次性token、高熵随机凭据轮换（R27）；测试R27/G03 |
| POST /agents/{id}/heartbeat | 已实现 | applied_config/queue/disk上行，命令下行 |
| GET /agents/{id}/configuration | **已实现（本轮T6）** | 只读：device_code/status/applied（最近ACK成功的apply_config version+digest）/watch根摘要；不含令牌/secret/哈希；无已应用配置 applied=null；跨租户404；测试 test_T6* |
| POST /acquisitions | 已实现 | 幂等登记，仅登记不认定完成 |
| POST /upload-sessions | 已实现 | R16终态证明先行 |
| GET /upload-sessions/{id} | 已实现 | 已确认片/缺片列表 |
| PUT chunks/{index} | 已实现 | 204/422/409；W07同index并发仲裁 |
| POST complete | 已实现 | 202+operation（R14单一持久Operation） |
| DELETE /upload-sessions/{id} | 已实现 | R15账本CAS恰好释放一次 |
| GET /operations/{id} | 已实现 | （core与storage双入口） |
| GET/HEAD /assets/{id}/content | 已实现 | 206/416/ETag/If-Range/多段忽略；W08真实socket断流证据 |
| POST /assets/{id}/verify | 已实现 | 校验Job+Operation |
| POST /storage/migrations | **已实现（本轮T5）** | 预览（计划+可行性=free-margin-预留账本）→执行（单一Operation+每资产Job，dedup=migrate:{asset}:{vol}幂等；拷贝→hash校验→replica location→CAS释放预留；源对象保留）；507/503语义；测试 tests/fault/test_local_gaps_storage.py::test_T5* |

## 5.2 治理、配置与编排

| 设计端点 | 状态 | 实现/ADR |
|---|---|---|
| POST /ingress/{connector_id}/events | **已实现（本轮T2，替代原ADR拉取-only）** | 推式摄入：主体必须是该connector（否则404）；先持久化InboxEvent再202；同(租户,连接器,外部ID,revision)同内容200 duplicate/异内容409；已知事件类型置ROUTED并按 _route_event 语义发OutboxEvent，未知保持RECEIVED零副作用；v0.4拉取模式（checkpoint轮询）仍是主通道，二者共用路由语义；测试 test_T2* |
| POST /imports/preview | 已实现 | 限量脱敏+逐行错误 |
| POST /imports | 等价实现 | 同步解析+冻结+outbox触发（未建异步导入Job）；ADR：metrics域无大文件解析负载，同步返回完整逐行结果且坏行全列；异步Job化登记为规模项 |
| GET /packages/{id}/revisions/{revision} | **已实现（本轮T3）** | 固定包读取：payload/digest/schema_ref/provenance/state/created；附件仅引用不内联二进制；payload.index.serialized 掩码返回（T10），digest为冻结原文摘要；跨租户/不存在404；测试 test_T3* |
| POST /connectors/{id}/test | 已实现 | 安全探针（synthetic/真驱动DSN，W09超时/取消/只读） |
| POST /mappings/{id}/simulate | **已实现（本轮T1）** | 逐记录逐字段 {value, source_field, dictionary_applied, error}；字段级错误不中断；dictionary_refs 按租户+version解析；DSL非法/灾难正则 422（field_errors）；权限 config_editor/platform_admin；配套 GET/POST /api/v1/mappings（列表+创建，T11向导）；测试 test_T1* |
| POST /workflows/{id}/validate | 等价实现 | `POST /workflows/validate`（按DSL体校验，环/端口/孤立节点/未知类型）；ADR：无revision路径参数，保存时自动校验+编号revision |
| POST /config-releases | 已实现 | 收尾轮新增：闭包快照+字典审批闭包校验409+回滚端点；G21测试；**本轮T11补UI触发（Pack页"发布 ConfigRelease"按钮，展示digest与条目数）** |
| POST /workflow-runs | 已实现 | dry_run进上下文（R34隔离链） |
| POST /workflow-runs/{id}/cancel | 已实现 | 终态409；未启动步骤CANCELLED；引擎不再推进 |
| POST /manual-decisions | 已实现 | CAS（428/412）+租户校验（R12/R13） |
| （超集）GET workflow-runs / {id} / replay | 已实现 | 列表（keyset游标）/详情（步骤级失败定位+闭包）/固定版本回放 |

## 5.3 交换与业务页面

| 设计端点 | 状态 | 实现/ADR |
|---|---|---|
| POST /submissions | **已实现（本轮T7，替代原ADR工作流-only）** | 手动提交：{package_id, revision?, destination_id}；幂等键 dest:package_revision（重复200返回既有）；真实目的地遇OPEN阻断409 PROFILE_UNAPPROVED；投递经 destination.push_manual Job由worker执行（与send节点共用 ensure_push_submission/perform_push，无复制逻辑）；缺包404；工作流自动创建仍是主通道；测试 test_T7*（原 test_storage_protocol 中"未实现断言"仍满足——空body 422） |
| POST /submissions/{id}/retry | 已实现 | 允许分支重试，不改快照 |
| POST /submissions/{id}/corrections | 已实现 | 新修订关联旧提交，不覆盖 |
| POST /publications | **已实现（本轮T7）** | 手动发布：{package_id, revision?} 或 {case_id}；触发既有publish链（workflow.launch/metrics.run→h_deliver_publish，R05全量成员校验语义不变）；发布唯一性由 (tenant,pr,destination) 既有约束保证；测试 test_T7* |
| POST /publications/{id}/revoke | **已实现（本轮T4+第4轮续）** | 撤回：仅本租户PUBLISHED可撤回；manifest routes 逐个revoke、PublicationItem逐个写墓碑（dataset=entity_type，exchange.003唯一索引幂等）；**批次置REVOKED并移除仍指向该批次的dataset_current指针（LG-13：结构化当前视图与文件路由一致消失，其他批次拥有的键不受影响；历史projection/batch保留）**；state=REVOKED+revoked_at（迁移008）+审计；幂等200；缺reason 422（统一信封的VALIDATION_FAILED）；测试 test_T4*/test_T4b/test_T4c |
| GET /publications/{id}/evidence | 已实现 | 可见/下载/回执分列（R01/R02/R26） |
| POST /receipts/{destination_id} | 已实现 | 先持久化后幂等解析；ACCEPTED≠CONFIRMED |
| POST /reconciliations | 已实现 | 固定范围差异（G12子集在fault测试） |
| GET /pathology/cases[/{id}] | 已实现 | 分页/筛选/版本树/预期清单（R31）；详情仅掩码索引（T10核查） |
| POST /pathology/cases/{id}/associations | 已实现 | CAS+证据+审计（H06契约） |
| POST /pathology/slides/{id}/effective-scan | 已实现 | 重评估不改历史包 |

## 汇总

- **已实现+本轮新增**：T1 mappings simulate（+GET/POST mappings）、T2 ingress events、
  T3 packages revisions、T4 publications revoke、T5 storage migrations、
  T6 agents configuration、T7 手动 submissions/publications、T8 cursor全量统一。
- **等价实现（ADR已注明）**：imports（同步）、workflows/validate（DSL体）、
  T2/T7 双通道（推式ingress与手动创建为工作流自动通道的补充入口，语义共用）。
- **登记缺口（后续版本）**：无（原登记缺口清单全部关闭；UI向导串联见T11、
  拖拽画布为P3未做）。

## 第4轮续（2026-10-02）补充

- `GET /api/v1/field-lineage`（设计外补充端点，S15整改）：响应从静态文案字典升级为
  三层真实数据 `{design, configured, evaluated}`——design=10 §3设计基线；
  configured=租户内每个 mapping_key 最新修订的真实血缘（target←source_field+op+字典）；
  evaluated=按 case_id 返回该病例当前冻结包修订+索引记录实际 rule_versions/
  dictionary_revisions+掩码索引串。UI Lineage.vue 同步改版。测试
  test_field_lineage_real_data。
- `POST /api/v1/submissions` 手动投递落 UNKNOWN 时自动排 `destination.query_unknown`
  （与 retry 端点同 Job/同 dedup；LG-11 关闭）。测试 test_T7b。
- 存储迁移 execute 的 Operation.result 持久化完整计划快照（T5：计划/进度/补偿
  全可查证）。测试 test_T5_execute_dual_location_and_idempotent 计划断言。

## 药品追溯 /api/v1/drug-trace（2026-10-16 新增；2026-10-17 DTR-FIX2 第二轮修订）

| 端点 | 状态 | 实现/测试 |
|---|---|---|
| GET /drug-trace/overview | 已实现 | 计数概览+两 profile 四能力（collect/send/query/reconcile）证据状态；提交仅统计药品领域（FIX2：不混计其他领域） |
| GET /drug-trace/bills[/{id}] | 已实现 | 复合键过滤/质量/码数；列表不含患者与完整码串 |
| GET /drug-trace/revisions/{id}/codes | 已实现 | 码值掩码（首4尾4）；operator/reviewer/admin/auditor 可读 |
| POST /drug-trace/previews | 已实现 | dry-run：零外连、不建提交；字典未批准显示 blocked（A16） |
| POST /drug-trace/collection-runs、/{id}/pause、**/{id}/cancel**、GET 列表 | 已实现 | oracle 模式证据门禁（空证据409）；窗口 begin<end 校验；FIX2：cancel 撤销（worker 安全点停止）；PAUSED run 不被 worker 强改 RUNNING（R2-09） |
| POST /drug-trace/bills/{id}/prepare | 已实现 | 幂等（重复 prepare 返回旧提交真实 package_revision）；FIX2：destination profile 须与请求 profile 完全一致（R2-15）；config_release 闭包固定 |
| POST /drug-trace/submissions/{id}/send | 已实现 | confirm 必填(428)+证据门禁(409，send 必需项含 EXT-05/10/12)+config 闭包校验+来源revision未变校验；FIX2：仅 PREPARED 可发（UNKNOWN/SENDING 409 引导查询；终态 409）；worker CAS/租约 fencing |
| POST /drug-trace/submissions/{id}/query | 已实现 | FIX2：查询代次 q:{sid}:{gen}——手工重查总是新任务（不吞已完成 job）；全分页+begin_date/end_date/bill_type；回执按代次+digest 保留历史；PROCESSING 自动退避排下一代（上限12次） |
| POST /drug-trace/reconciliation-runs、GET runs/{id}、POST reconciliation-items/{id}/dispositions | 已实现 | FIX2：接独立平台查询快照——matched 需本地 CONFIRMED 且平台全成功；平台无单=missing_remote、通道失败=UNVERIFIED（绝不 matched）；dedup 键含窗口/药房；码级 code_level_verified=False；处置 ACCEPTED/COMPENSATE/IGNORE+理由+角色+审计 |
| GET /drug-trace/readiness | 已实现 | 每 profile 必需证据/缺项/destination（按 profile 精确绑定）/签名验证状态；query 独立能力（EXT-09）。**FB轮（2026-10-21）**：可选 `profile`/`destination_id` 查询参数；响应含完整 `report`；层状态统一 READY/NOT_READY/UNKNOWN 三态；同 profile 多目的地返回 `destination_ambiguous`+候选（不任意 first）；分层与 CLI `init_drug_trace --readiness` 同源（domains.drug_trace.readiness） |
| GET/POST /drug-trace/destinations/{id}/config-revisions | 已实现（FB轮新增） | 目的地配置世代管理。GET 列表（摘要级：id/revision/state/digest/content_keys）。POST body {content, publish, set_real, dry_run}：editor 可建 DRAFT、**publish 需 platform_admin**；dry_run 只校验零写入；明文 app_secret/session/password 422 fail-closed；发布=世代切换（旧 PUBLISHED→RETIRED；同 digest 幂等；set_real 仅限无发布配置的 DRAFT 目的地）；真实目标必填 app_key/app_secret_ref(env:)/dmz_base_url(https)/三证书绝对路径 |
| POST /drug-trace/evidence[/{id}/revoke] | 已实现 | platform_admin；缺项审批422（必需项由代码固定不可缩减）；destination 归属校验（跨租户404）；撤销即停发（已排队任务 worker 复核后拒绝）。FIX3-04：body 增 config_release_id（send 精确绑定，空值不通配）与 scope{window_begin,window_end,pharmacies[],max_bills}（send 必填完整——空 scope/空药房/缺配额 422；批准即建配额台账并发送逐单复核） |
| POST /drug-trace/daily-reports | 已实现（TUI轮 2026-10-24 新增；FIX轮修订） | 驱动 v2 日报入库：operator\|platform_admin；幂等 upsert by (tenant,report_date,source)（二次 POST 整体更新 created=false）；ISO 日期/outcome(OK\|PARTIAL_FAILURE\|FAILED)/source(timer\|manual) 枚举校验 422；JSON 深度≤4、三字段合计≤64KB→413；FIX：window_begin≥window_end→422（不加 report_date 新近度限制，manual 补录历史日合法）；audit `drug_trace.daily_report.ingest` |
| GET /drug-trace/daily-reports | 已实现（TUI轮新增） | 日报摘要列表（limit≤200 默认30，report_date 倒序）：行 {report_date,source,outcome,candidate_count,sent,errors_count,elapsed_seconds,created_at}（不含 errors 明细）；单日详情 GET /{report_date} 返回完整含 send.errors/window（无记录 404） |
| GET /drug-trace/upload-stats | 已实现（TUI轮新增；FIX-01 修订） | 按日统计 ?days≤90（clamp 1..90）：by_state 按 created_at(UTC)×state、attempts 每提交取最近一次 delivery_attempt 分桶 accepted/http_error(http≥400)/other、total；仅本租户 drug_trace 域。FIX-01：两条分组聚合 SQL（状态分组 + ROW_NUMBER 窗口子查询），端点内无任何 .in_()（旧实现 IN 列表超 SQLite 32766/PG 65535 参数上限必 500，35k 规模回归锁） |
| GET /drug-trace/send-scope | 已实现（TUI轮新增） | 现行 send 证据+EXT-12 scope+配额台账实时值+目的地只读总览；多目的地 ambiguous=true/未登记显式空字段（不猜） |
| GET /drug-trace/submission-outcomes | 已实现（TUI轮新增；FIX-02 修订） | 近 N 天提交（days clamp 1..90，limit 200 倒序）关联最近一次 delivery_attempt（outcome/http_status/ended_at）与 package→bill source_bill_no/bill_code；only=problem 过滤 state∈{REJECTED,PARTIAL,UNKNOWN} 或最近尝试≠ACCEPTED（无尝试不算异常）。FIX-02：窗口子查询 LEFT JOIN + 单号一次批量回填（≤200 id），不再逐行单查 |

通用端点复用：GET /submissions、GET /reconciliations（scope.domain=drug_trace 过滤）。

FIX2 服务层关键语义（2026-10-17）：TOP 上传表单按官方两 profile 独立 schema
（零售 ref_user_id；库存 +physic_type/from_user_id/to_user_id/client_type；bill_time
必填 GMT+8）；响应只认官方 response_success（旧 success 字段→UNKNOWN）；
查询状态映射 QUERY_STATUS_MAP：rt3+spf51→CONFIRMED、rt3+spf52→PARTIAL、
rt1→PROCESSING、未知→MANUAL_REVIEW。

## 2026-10-20 真实查询诊断补记

本机独立诊断真实调用query.billstatus及query.getbyrefentid均SUCCESS；后者未加入应用/DMZ业务白名单。
没有新增业务API或启用真实上传。仅修正TOP空名/空值签名；正式签名资格、证据/字典/配置门禁保持。
[证据与边界](../../docs/development/platform-live-test-20261020/README.md)。

## 2026-10-20 DTR-FINALIZE API 变更

- `GET /api/v1/drug-trace/readiness`：响应由 profile 列表改为
  `{profiles: [...], layers: {CODE_READY, LOCAL_SERVICE_READY, NETWORK_READY,
  SOURCE_READY, PLATFORM_READY, SEND_SCOPE_READY}}`；profiles 原字段不变。
  NETWORK_READY 恒 UNKNOWN（现场实测另做，不是绿灯）。UI 已同步分层显示。
- 转发器新增 CLI：`--diagnose [--diagnose-upstream]`、`--listen-mode
  systemd|explicit|auto`；`--check-config` 升级为严格校验（端口/预算/IP/
  方法白名单/证书配对/SAN/EKU/有效期）。
- 安装器 CLI：`install.sh [install|--verify [--with-upstream]|--status|
  --diagnose|--rollback|--uninstall [--purge]]`（旧 install/verify/rollback
  语义升级；--with-upstream 独立出口码 4=LOCAL_READY/UPSTREAM_BLOCKED）。
- 查询日期语义：`query_date_semantics ∈ {both(默认), bill_time,
  upload_time}` 经批准 ConfigRevision 固定；默认双窗口合并防历史单漏查。
- session 可选授权：destination 配置 `session_ref: env:...`（file: 拒绝）。
- 无新增对外业务方法；真实发送门禁（EXT 证据链）不变。


## 2026-10-22 药品追溯采集实现修正

| 能力 | 当前实现状态 | 当前验收边界 |
|---|---|---|
| 现有drug-trace采集的Oracle码明细分页 | 本地修复NULL/空串首屏条件；接口契约/路由/鉴权无新增变化 | 相关110项回归及真实Oracle单据4码分页通过；现网未升级，不等于采集API现场闭环通过 |
| 当前视图账号 | 新账号/私有同义词/PHARMACY两视图已实际只读核验 | 来源尚无DSN、业务口径UNKNOWN/finalized空，真实发送仍阻断 |

见[本轮报告](../../docs/development/drug-trace-oracle-first-page-20261022/README.md)，没有把平台只读查询成功写为上传成功。


## 2026-10-26 用户名/密码登录（AUTH-PASSWORD-20261026）

新增迁移013的console_account/console_session，用户名/密码→独立8h可撤销会话；加盐PBKDF2、5次失败15min锁定、nginx登录限流。生产Login.vue改双输入，刷新校验/auth/me；退出及修改密码均使相应会话失效。core/storage/exchange共用会话认证，原自动化Bearer/设备凭据保持兼容，未改业务scope、配额或历史payload。

接口：POST /api/v1/auth/login新增username/password分支，GET /auth/me、POST /auth/logout、POST /auth/password；legacy token请求兼容。账号维护走apps.manage_console_user交互/受控stdin，不内置默认密码、不通过公开API创建管理员。

专项17通过，PG并发补验1通过，全量499通过/7跳过/0失败；合成浏览器与真实nginx限流通过。生产auth-20261026T043500Z已上线，账号登录/拒绝/会话撤销/跨storage及保留原scope实测；管理电脑443/正式证书、实际浏览器打开仍待访问链路处理，本轮未启用HTTP。积压driver维护中断后已按同窗口幂等恢复，仍RUNNING。

证据及API细节：[本轮实录](../../docs/operations/username-password-login-20261026/README.md)。实际密码只在实录指向的本机受限目录。


## 2026-11-15 RT-04：医保脱敏深化、23 交易 Profile revision、影子状态/分类（RT-04-20261115）

### 变更接口（契约细化，路径不变）

| 接口 | 变化 | 兼容性 |
|---|---|---|
| GET /api/v1/medins-emr/transactions | 23 交易（清单表19 ∪ 正文标题 12601~12604；active revision r2-23txn 树形 schema_version=2） | 响应字段不变；条目 19→23 |
| GET /api/v1/medins-emr/transactions/{code} | 契约改树形（children/items）；**新增 profile_revision 字段**（revision/digest/schema_version/transactions）；样例根对象只含协议字段（无 _synthetic） | fields.nodes→fields.children（前端已同步）；旧 r1 平面契约经 revisions/index.json active 切换保留 |
| POST /api/v1/medins-emr/validate / send-mock / export-request | 递归校验（完整数组/类型冒充拒/真实日历/未知 schema_version 报错）；request/response/validation_errors/note 全列递归脱敏；报文>256KB 或深度>12 明确拒绝（REQUEST_TOO_LARGE/PAYLOAD_TOO_DEEP）；export 响应补 transaction_code/profile_revision | 路径/角色/scope 不变 |
| POST /api/v1/medins-emr/debug-exchanges/{id}/record-result | note/platform_code 人工输入脱敏后落库；失败回填 outcome=FAIL（如实反映） | 路径/角色不变 |

### 新增接口

| Method/Path | 角色/Scope | 说明 |
|---|---|---|
| GET /api/v1/report-targets/{code}/classification?days=&source_instance= | 任意登录 + require_target_scope(code) | 六类文书×REPORT_BUCKETS 分类统计（含零值行/总和与同窗口台账对齐/影子 Mock 分栏/观测截至/最近导入）；无插件认领→NOT_IMPLEMENTED |

### 行为语义变化（如实登记）

- POST /api/v1/report-center/submissions/{target}/{id}/manual-resolution：runtime_kind=EXTERNAL 业务显式拒绝（USE_DOMAIN_WORKFLOW——外部副本观测记录不是 Submission，不开放平台人工重发）。
- POST /api/v1/report-center/external-imports 返回统计改两层口径（行级 rows_read=rows_valid+parse+shape+privacy 恒等式；记录级 records_total/created/updated/unchanged/sent_only_records；旧 rows_applied/rows_skipped_duplicate 保留为别名语义=created/unchanged）。
- 外部台账记录唯一键含 source_instance（迁移022）——跨实例同 record_key 不再合并。


## 2026-11-15 RT-05：运营写闭环后端契约（RT-05-20261116）

### 变更接口（写语义修复，如实登记——非"只改前端"）

| 接口 | 变化 | 兼容性 |
|---|---|---|
| POST /api/v1/config-releases/{id}/rollback | **reason 必填（≥5 字）**并进同事务 AuditEvent（new_ref.reason/already_rolled_back）；响应补 already_rolled_back；幂等保留（重复调用不重设时间戳） | **破坏性**：旧无 body 调用 422；仓库内唯一旧调用方（W10 测试）已迁移 |
| POST /api/v1/workflow-runs（run 闭包解析） | API 侧与 worker `_launch` 同语义：存在未回退 release 且闭包含 hospital_pack → pack 固定为闭包修订（修复 API 侧无视 rolled_back_at、不按 release 固定 pack 的不一致）；rollback 后回落 latest PUBLISHED | 行为对齐 W10 设计意图；无 release 环境行为不变 |

### 新增接口

| Method/Path | 角色 | 说明 |
|---|---|---|
| POST /api/v1/config-releases/{id}/restore | config_editor/platform_admin | **恢复到此版本**（与 rollback 分立的真实语义）：以源发布不可变闭包为来源，逐条校验存在性+digest（漂移→CLOSURE_DRIFTED 409；字典批准/有效期→PROFILE_UNAPPROVED 409），创建**新 ConfigRelease**（闭包=源闭包、digest=源 digest、test_report.restored_from）；幂等（同源同闭包的最新未回退发布原样返回 idempotent=true）；reason 必填进审计；不改写历史冻结包/不退休较新修订/不自动启用调度发送 |
| GET /api/v1/plugins | 任意登录 | 已注册领域插件只读元数据（manifest 驱动：key/domain/description/capabilities/report_targets；U02 新建业务下拉数据源） |
| GET /api/v1/schedules/meta | 任意登录 | 调度表单元数据：job_types=真实 registry 已注册集合 + misfire/concurrency 枚举（U03 不展示未注册类型） |

### 行为语义变化

- PUT /api/v1/report-targets/{code}（plugins/report_targets.upsert_target）：lifecycle 升级到 PILOT/PRODUCTION 必填 reason（≥5 字），进 AuditEvent new_ref；生产闸门（sole_pusher/独立证据/真实链路门禁）不松动。
- 新错误码：`CLOSURE_DRIFTED`（409，contracts ERROR_CODES 登记）。


## 2026-11-16 RT-06：影像域路由与元数据端点（RT-06-20261117）

| Method/Path | 角色/Scope | 说明 |
|---|---|---|
| GET /api/v1/medins-imaging/datasets | 任意登录 + target_scope(medins_imaging_jn) | 16 数据集清单（真实大写代码/字段数/revision/未确认计数） |
| GET /api/v1/medins-imaging/datasets/{code} | 同上（scope 先校验） | 字段树+合成样例；未知 code 404 |
| POST /api/v1/medins-imaging/validate | operator/config_editor/admin + scope | 本地校验（≤20 条；错误不含原始敏感值；不出网） |
| POST /api/v1/medins-imaging/project | operator/admin + scope | 本地示范投影：校验→冻结（domain=medins_imaging，digest 幂等）→Submission→generic 回环 Mock→审计；仅 Mock 目的地 |
| GET /api/v1/plugins | 任意登录 | 已注册插件只读元数据（U02 下拉源） |
| GET /api/v1/schedules/meta | 任意登录 | 调度表单元数据（真实 job_types/枚举） |
| GET /api/v1/report-targets/{code}/classification | 任意登录 + scope | 分类统计（RT-04 A3；此处补登归档） |

全部经 domain.routes 通用挂载（app 无业务特判）；路由核对报告
var/rt06_route_audit.json：missing 0 / unresolved 0。
