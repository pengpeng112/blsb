# 药品追溯第二轮整改实施（FIX2）

2026-10-17；任务 DTR-FIX2-20261017；工具 ZCode，模型 GLM-5.3。
源码整改基线：`0ca47c6`（review-2 的审查对象）→ 本轮修复后的工作区。
承接 [review-2-20261017](../review-2-20261017/REVIEW.md) R2-01—16 与
[REMEDIATION_PLAN](../review-2-20261017/REMEDIATION_PLAN.md) FIX2-00—08。

**定性：本轮完成全部可离线实施的代码整改与测试转换；这是「离线代码完成」，
不是「现场验收通过」，更不是「正式业务已签认」。** EXT-01—12 外部材料未齐，
真实平台链路（SDK 向量、mTLS 现场证书、Anolis 安装、隔离 PG 恢复演练）仍阻断。

## 1. R2-01—16 处置矩阵

| R2 | 处置 | 关键代码/行为 | 测试证据 | 未决限制 |
|---|---|---|---|---|
| R2-01 请求字段 | **已修** | `service.build_biz_params` 两 profile 独立 schema：公共 bill_code/bill_type/bill_time(GMT+8)/trace_codes；零售 +ref_user_id；库存 +physic_type/from_user_id/to_user_id/client_type；字典在冻结时解析（payload.lookup_mapping_rev，新命名空间 drugtrace.ref_user_id/supplier_user_id/physic_type/client_type）；`_doc`/supplier_ref/warehouse_code 不再透传；缺必填 fail-closed | fix2 `test_t01_official_request_params_two_profiles`、`test_t27b_bill_time_required_at_freeze` | 字典目标值为文档记录值（EXT-08 批准前不作为生产字典） |
| R2-02 响应/查询 | **已修** | `protocol.parse_response` 只认官方 `response_success`（旧 `success` 字段→UNKNOWN 不兼容假象）；`parse_query_response` 展开 `result.model.result_list.bill_deal_status_search_do` + total_num；`perform_query` 请求带 begin_date/end_date/bill_type/ref_ent_id + 全分页（total_num 优先判界，短页但未读满继续翻；QUERY_MAX_PAGES 保护） | `test_t02_official_upload_and_query_fixtures`、`test_t04_query_full_pagination_and_request_fields` | 查询窗口=提交创建日±1天（批准口径 EXT-09 后可调） |
| R2-03 部分成功 | **已修** | `contracts.QUERY_STATUS_MAP`（doc-2026-10-17）：rt3+spf51→CONFIRMED；rt3+spf52→PARTIAL（明细保存）；rt1→PROCESSING；未登记组合→MANUAL_REVIEW。Mock 脚本通道改官方信封 + rt3/spf51 全成功演示 | `test_t03_partial_success_never_confirmed`；域测试 b07 官方信封断言 | 枚举语义以 EXT-08/09 批准口径为准；改映射须附证据 |
| R2-04 Mock 前缀绕过 | **已修** | 签名豁免以 transport 类型强绑定（`protocol.assert_signing_allowed`：非 MockTransport 一律拒绝，与密钥值无关）；真实目的地拒绝 `mock:` SecretRef（build_client 入口） | `test_t06_mock_secret_rejected_on_real_transport`、`test_t06b_real_destination_rejects_mock_secret_ref`；域测试 b07（RealTransport+`__mock__` 密钥零外发） | SIGNATURE_VERIFIED 常量仍为 False（EXT-07 官方向量未取） |
| R2-05 证据/配置 | **已修** | `evidence_state` 按 destination 精确过滤 + config_release 绑定（MISMATCH_CONFIG）+ 必需项永远取代码常量（用户不可缩减）；send 必需项扩为 8 项（+EXT-05/10/12）；`approve_evidence` 支持 scope（授权窗口），`_check_send_scope` 逐单复核 bill_time；prepare 固定 config_release 闭包（缺省=destination 当前 config_revision），perform_send 校验漂移→VERSION_CONFLICT 重新 prepare | `test_t05_evidence_exact_binding`、`test_t05b_send_scope_window_blocks_out_of_range`、`test_t08_config_closure_blocks_drift` | scope 的药房/批量上限字段已存储、逐单复核当前实现窗口维度 |
| R2-06 状态机/CAS/轮询/回执 | **已修** | `perform_send` 仅 PREPARED 可发；CAS（submission_revision 条件更新 PREPARED→SENDING→终态，失租不覆盖）；dry-run 在任何 client/网络/写入前分支（零 DeliveryAttempt/零状态变化）；本地确定性错误（签名/方法）回退 PREPARED 并抛出、网络类→UNKNOWN；`_j_send` UNKNOWN/SENDING→只排查询；查询代次 `q:{sid}:{gen}`（手工重查/PROCESSING 下一代都推进；12 次上限）；回执 `query:{sid}:{gen}:{digest8}` 保留每次状态变化、乱序不回退 | `test_t14_unknown_sending_confirmed_no_resend`、`test_t17_query_generations_and_receipt_history`；域测试 b03（终态禁重发/dry-run 零副作用） | 双 worker 真并发下 CAS 依赖 DB 条件更新语义（SQLite 测试单连接；PG 现场验收待 EXT） |
| R2-07 Oracle 类型 | **已修** | `normalize_row`（列名小写/datetime→ISO/Decimal→str）在 OracleDrugTraceWarehouse 唯一边界；`content_digest` 确定性序列化 datetime/Decimal；`quality.as_bool`（'0'→False，未知值报错）用于 finalized/is_void/is_deleted | `test_t09_oracle_fake_dbapi_normalized`、`test_t09b_oracle_uppercase_columns_import` | 活库驱动数据形态待 EXT-01/02 小范围验证 |
| R2-08 游标/主键/重取 | **已修** | SQL 游标拼接与 ORDER BY 均含 source_system（与 Python 复合键一致）；bill_codes/bill_header 按完整复合键（含 source_system）过滤；重复满页/游标不推进→SOURCE_LOOP_SUSPECTED；持久重取账本 `drug_trace_retry_bill`（010 迁移）：VERSION_CONFLICT 登记账本，下一窗口先重取（成功出账）；登记唯一键改完整来源主键（tenant+connector+source_system+pharmacy+kind+source_bill_id，009 旧约束由 010 替换，重复数据 fail-closed 报错不默删） | `test_t10_same_timestamp_multipage_and_cursor_guard`、`test_t10b_non_advancing_cursor_aborts`、`test_t10c_cross_source_same_display_no_merged`、`test_t11_retry_ledger_persists_and_refetches` | 历史补报与持续增量共用 checkpoint 的分离配置未做（EXT-10 明确范围后做） |
| R2-09 停发/采集复核 | **已修** | `_j_collect` worker 执行期复核：run 非 PENDING/RUNNING→跳过（PAUSED 不改 RUNNING）；connector 须 ENABLED；oracle 模式重查证据；`run_collection` PAUSED/CANCELLED/终态跳过，页边界安全点检查（`should_continue`），中途暂停断点保留（completed_window=False）；新增 cancel API | `test_t13_paused_run_worker_skips`、`test_t13b_oracle_collect_revoked_evidence_blocks`、`test_t13c_pause_midrun_safe_point` | 已发出请求的撤销语义=记录 UNKNOWN+只查询（不能撤回平台已受理数据） |
| R2-10 对账 | **已修** | `run_reconciliation` 接独立平台查询快照（逐单查询、官方嵌套解析）；matched 仅当本地 CONFIRMED 且平台全成功；平台无单→missing_remote；本地 CONFIRMED 但平台部分成功→state_mismatch；通道失败→run.state=UNVERIFIED、unverified_remote（绝不 matched）；profile 严格过滤（business_kind∈profile 集）；Submission 精确限定 destination；版本=单据当前 revision 对应提交；scope 记录 remote_snapshot/platform_ok/remote_error；dedup 键含窗口/药房；码级 code_level_verified=False 如实 | `test_t18_reconciliation_requires_remote_evidence`、`test_t18b_reconciliation_platform_failure_unverified` | 码级核对能力待 EXT-09；补报处置的查询证明依赖平台查询可用 |
| R2-11 RealTransport | **已修** | mTLS 客户端证书（cert_file/key_file→load_cert_chain，构造期可读校验）；http.client 分层超时（connect_timeout 建连→read_timeout 读）+ total_deadline 硬顶；3xx 显式拒绝（redirect-forbidden）；主机白名单 allow_hosts；URL 带路径/查询拒绝 | dmz `test_c06_app_real_transport_mtls_end_to_end`（应用 RealTransport→本地 mTLS 转发器→模拟上游全链路+无证书拒绝） | 现场 DMZ 证书/SAN 与真实上游连通待 EXT-03/04 维护窗口 |
| R2-12 DMZ 材料 | **离线部分已修** | forwarder：TOP 方法白名单（恰一个且∈3方法，重复/未知/空→400）、Content-Type 白名单（415）、上游 3xx→502 不跟随、响应体上限、分层超时+总时限、finally 连接释放；systemd unit 加 AmbientCapabilities/CapabilityBoundingSet=CAP_NET_BIND_SERVICE；install.sh 强制 SHA256SUMS 材料哈希校验 + Python≥3.7 真实检查（Anolis7.9 系统 3.6 明确不满足；README 禁止 el8 冒充 el7） | dmz `test_c05_top_method_whitelist_and_upstream_gates`（T23）+ 7/7 全绿 | Anolis 实机安装/服务用户绑定 443/systemd 版本核对待 EXT-11 维护窗口；Python≥3.7 el7 运行时材料需院方通道取得 |
| R2-13 备份静止 | **已修（脚本）** | backup.sh 静止窗口覆盖全部实际写入口（worker+core+storage+exchange，backup.json 记录四者原状态）；对象归档按 OBJ_DIR 实际路径；umask 077 入口设置（敏感产物创建即私有） | ops 13/13（含 ops04 全写入口静止断言） | 真实多存储一致快照演练待隔离 PG 现场环境（EXT-11） |
| R2-14 恢复保护 | **已修（脚本）** | restore.sh 项目名=blyb 拒绝（除非双确认）；生产路径保护从 runtime.env 读实际自定义目录并做相等/祖先/子孙全比较；--validate-only 输出如实（临时目录解包读取、不写生产/目标路径） | ops 13/13 | 完整 pg_dump→pg_restore→应用恢复的真实隔离栈演练待 EXT-11（本地无 docker/隔离 PG） |
| R2-15 profile 绑定 | **已修** | prepare：destination profile key 必须与请求 profile 完全一致（不 first 撞库）；重复 prepare 先查旧 Submission 返回其真实 package_revision_id（不先建新冻结包）；`route_event` 按业务 profile 精确绑定目的地；readiness/overview 同口径 | `test_t08b_repeat_prepare_returns_real_binding`；fix2 域断言 | — |
| R2-16 隔离/父子码/UI | **已修（离线部分）** | 迁移010：bill 表复合唯一键换完整来源主键 + 各领域表 (tenant_id,id) 复合唯一索引（PG）；跨 tenant 直读/prepare/对账/证据全拒绝（t26）；父子包装码同单→QD-PARENT-CHILD-SAME-BILL INVALID；overview 仅统计药品领域提交；UI：登录令牌复验时序修复（失效回登录页）、后端地址回车提交、发送按钮仅 PREPARED、证据登记绑定目的地、对账平台快照可用性/UNVERIFIED 展示、query 能力展示 | `test_t26_cross_tenant_isolation`、`test_t27_parent_child_same_bill_blocked`；前端构建通过；浏览器闭环截图 | owner/connector 级 API 授权的进一步细分（A15 全矩阵）部分依赖 core 通用授权模型扩展，当前 tenant 级全拒绝已覆盖；UI keyset 分页为既有 limit 分页（列表量级受控） |

## 2. FIX2 工作包状态

| 包 | 状态 | 说明 |
|---|---|---|
| FIX2-00 基线/冻结 | 完成 | review-2 全部材料保留原样（review-2-20261017/ 未改动）；探针复现基线 exit0（审查时点）；本轮探针重跑（evidence/probe-rerun-output.txt）在 R2-01 处即被 fail-closed 拒绝——反例转换后的测试才是修复证据 |
| FIX2-01 协议/状态 | 完成（离线） | 上表 R2-01/02/03 |
| FIX2-02 门禁/版本/身份 | 完成（离线） | R2-04/05/11/15；SDK 向量未取（EXT-07）保持硬阻断 |
| FIX2-03 Oracle/来源 | 完成（离线） | R2-07/08 + view-contract-r6 草案（docs/deployment/reporting-20261015/view-contract-r6.md，r5 原文保留） |
| FIX2-04 停发/状态机 | 完成（离线） | R2-06/09 |
| FIX2-05 对账 | 完成（离线） | R2-10 |
| FIX2-06 DMZ/运维 | 完成（离线代码与材料口径）；现场项阻断 | R2-12/13/14；实机安装/真实 PG 恢复演练待 EXT-11 |
| FIX2-07 权限/UI | 完成（离线部分） | R2-16 |
| FIX2-08 回归/交付 | 完成 | 见 §3 测试记录与 §5 文档 |

## 3. 测试记录（真实命令与结果）

环境：Windows + 本机既有 Git Bash/OpenSSL（PATH 前置
`D:\Program Files\Git\bin;D:\Program Files\Git\usr\bin`，沿用 run_local_tests.py 口径；
未安装任何依赖）。

| 范围 | 命令（implementation/ 内） | 结果 |
|---|---|---|
| 新增反例转换测试 | `python -m pytest tests/integration/test_drug_trace_fix2.py -q` | **26 passed / 0 failed**（T01—T27） |
| 领域回归 | `python -m pytest tests/integration/test_drug_trace_domain.py -q` | **24 passed / 0 failed**（含更新后的官方协议断言） |
| API 回归 | `python -m pytest tests/integration/test_drug_trace_api.py -q` | 通过（并入下方合并运行） |
| 运维/备份恢复 | `python -m pytest tests/integration/test_drug_trace_ops.py tests/integration/test_w15_restore.py tests/integration/test_deploy_gaps.py -q` | **26 passed / 0 failed**（ops04 更新为全写入口断言） |
| DMZ 转发器 | `python -m pytest tests/integration/test_dmz_forwarder.py -q` | **7 passed / 0 failed**（新增 c05 白名单/重定向/响应上限、c06 RealTransport mTLS 全链路） |
| 药品全相关合并 | `python -m pytest tests/integration/test_drug_trace_domain.py test_drug_trace_api.py test_drug_trace_ops.py test_drug_trace_fix2.py test_dmz_forwarder.py test_w09_fake_dbapi.py test_w10_w11.py test_w12_ui_closure.py test_w14_concurrency.py -q` | **95 passed / 0 failed**（113s） |
| 前端构建 | `cd apps/admin_web && npm run build` | 成功（2s） |
| 全量回归 | `python -m pytest tests -q --junitxml=var/fix2-full-regression.xml` | **305 passed / 6 skipped / 0 failed**（519.5s，退出0；XML：evidence/full-regression.xml） |
| 浏览器闭环 | serve_drug_trace_ui.py + IAB | 采集→质量分流→证据门禁拒绝→登记证据（绑定目的地）→发送 ACCEPTED→查询 CONFIRMED(rt3/spf51)→对账 matched=1（平台快照可用、码级未核验）→手工重查 generation=2/3 执行且不回退确认；截图 evidence/browser/01—03 |

测试修改原则执行情况：旧测试中编码错误协议的期望（`success` 信封、UNKNOWN 直发、
dry-run 改状态、部分成功当 CONFIRMED）均替换为正确业务断言，未删除断言造绿；
迁移测试更新为 009+010 双步断言；ops04 更新为四写入口静止断言。

## 4. 迁移与兼容

- **010_drug_trace_fix2**（`plugins/domains/drug_trace/models.py:migration_fix2`）：新表
  drug_trace_retry_bill；evidence.scope 列（SQLite/PG 均幂等）；PG 上替换
  uq_dt_bill_composite→uq_dt_bill_source_key（替换前重复检查，坏数据报错隔离不默删）；
  其余领域表补 (tenant_id,id) 唯一索引。009 原文未改。
- 旧冻结包（W09 之前冻结、缺 ref_user_id 等字段）在发送时 fail-closed
  （VALIDATION_FAILED missing），重新 prepare 即按新字典解析——不自动兼容旧包。

## 5. 文档同步

- 四类实施文档已更新：`implementation/docs/04-API目录对照矩阵.md`（query 能力/取消接口/代次语义）、`实施状态矩阵.md`（FIX2 行）、`外部阻断清单.md`（EXT-05/10/12 补充）、`测试执行记录.md`（本轮记录）。
- `docs/deployment/reporting-20261015/view-contract-r6.md` 新建（r5 保留）。
- `deploy/dmz/README.md`、`deploy/linux/backup.sh|restore.sh` 头部说明与实际行为一致。
- STATE/BACKLOG/HANDOFF 已更新；交接文件 `docs/ai/sessions/DTR-FIX2-20261017.md`。

## 6. 未关闭 EXT 清单（现场/材料阻断）

| EXT | 阻断内容 | 对应未完成验收 |
|---|---|---|
| EXT-01/02 | 合规 readonly 登记、SERVICE_NAME、院方建视图（r6 草案待签认）、小样本数据 | Oracle 活库验证；SITE2-01 |
| EXT-03/04/05 | DMZ→平台443出口/DNS、两段 SAN 证书与 mTLS 身份、院内 NTP | 真实链路 mTLS 闭环（代码已就绪：T07 本地全链路通过） |
| EXT-06/07/08 | 平台密钥、官方 SDK 签名向量、批准字典（bill_type/企业标识/状态映射） | SIGNATURE_VERIFIED 保持 False；正式发送硬阻断 |
| EXT-09 | query 覆盖范围/分页/终态规则 + 码级明细能力 | 码级对账（当前 code_level_verified=False） |
| EXT-10 | 历史补报范围与既往已上报核对 | 补报窗口配置与 checkpoint 分离 |
| EXT-11 | Anolis7.9 匹配运行材料（Python≥3.7 el7）、维护窗口、异机备份 | DMZ 实机安装、真实 PG 备份→恢复演练 |
| EXT-12 | 首批真实发送授权（机构/药房/业务/时间/单据/上限/签认人） | LIVE2-01 |

停发/回退操作：撤销证据（UI「撤销全部药品证据」或 evidence/{id}/revoke）→
未开始任务拒绝、在途任务记 UNKNOWN 只查询、采集 run cancel/pause 安全点停止、
停用 destination 停止调度；DMZ 回退 `install.sh --rollback`；数据库回退按
runbooks（迁移不自动回滚，010 新表可保留不启用）。
