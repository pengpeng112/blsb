# 04 §5 API 目录逐项对照矩阵（W11/G24）

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
