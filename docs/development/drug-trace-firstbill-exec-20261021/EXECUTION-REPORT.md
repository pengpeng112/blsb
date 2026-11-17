# DTR-FIRSTBILL-EXEC-20261021 执行报告

执行：ZCode / GLM-5.3，2026-10-21（UTC）。依据
[NEXT_AI_PROMPT](../drug-trace-push-review-20261021/NEXT_AI_PROMPT.md) FB-00—07。

**总结论：开发/测试/部署/真实配置发布/签名验收/正式链路只读查询全部完成并实测通过；
首张真实上传 0 张——生产取数链被 DBA 侧前置（r7 视图+只读账号）阻断，
发送证据门禁按设计保持关闭。**

## FB-00 冻结基线与现场快照 — 完成

- 只读快照两机三份（排他创建）：[baseline-target](evidence/baseline-target.json)、
  [baseline-dmz](evidence/baseline-dmz.json)、[baseline-followup](evidence/baseline-followup.json)。
  接手事实与上棒一致：<prod-host>=dpl-20261021T111000Z/6容器/迁移001—011；
  DMZ=20261021T113750Z 仅放行 query.billstatus；目的地 Mock DRAFT；DSN 空。
- git 基线：[git-baseline.txt](evidence/git-baseline.txt)（HEAD 0ca47c6，
  50 modified + 49 untracked 均为既有未提交成果，全程未 reset/clean）。
- 变更前备份：升级前一致性备份 `<prod-path>/backups/20261021T165233Z`
  （--with-objects，静止窗口 16:52:34Z→16:52:55Z；SHA256SUMS 复核通过）。

## FB-01 动态就绪判断与操作工具 — 完成（实测）

- 新共享模块 `plugins/domains/drug_trace/readiness.py`：CLI（init_drug_trace
  --readiness --profile/--destination/--config-release）、API
  （/drug-trace/readiness）与 UI 同源；SOURCE/PLATFORM/SEND_SCOPE 由当前
  证据/字典/签名实际状态动态计算；同 profile 多目的地不再任意 first
  （ambiguous+候选，CLI 退出2）；NETWORK 恒 UNKNOWN（外部实测不折算为绿灯）。
- 配置发布工具：`service.publish_destination_config` + API
  `POST /drug-trace/destinations/{id}/config-revisions`（publish 需
  platform_admin；dry-run 校验零写入）+ CLI `apps/publish_drug_trace_config.py`
  （默认 check/dry-run；apply 必须显式 tenant+destination；明文 Secret 字段
  fail-closed 拒绝；世代切换旧 PUBLISHED→RETIRED；同 digest 幂等）。
- 现场实测（升级后）：[api-setup](evidence/fb05-api-setup-20261021T165647Z.json)、
  [config-retry](evidence/fb05-config-retry-20261021T165751Z.json)——发布前
  LOCAL_SERVICE NOT_READY（DRAFT/mock/无配置）→ 发布后 READY
  （is_mock=False、PUBLISHED rev1）；readiness 查看零副作用有测试。
- 测试：`test_drug_trace_readiness_fb.py` 14 项（同源/动态翻转/歧义/跨租户/
  dry-run 零写入/角色/幂等/世代）。

## FB-02 签名资格与官方契约 — 完成（EXT-07 闭合）

详见 [SIGNATURE-QUALIFICATION.md](SIGNATURE-QUALIFICATION.md)。要点：

- 三源一致：官方 C# 参考 4 向量（上棒）+ 官方 PyPI TOP SDK Top-1.0.0
  （sha256 9ca70d83…，源码移植对照 4 向量，
  [evidence/fb02-sign-sdk-comparison.json](evidence/fb02-sign-sdk-comparison.json)）
  + **平台实际受理本实现签名**（[receipt-r2](evidence/fb02-our-signer-query-receipt-r2.json)：
  本实现 top_sign 构造 query → 平台 SUCCESS/total_num=0）。
- `SIGNATURE_VERIFIED=True` 按批准修订置位（常量处注释含完整证据链），随新发行
  fb-20261021T164800Z 交付；新增永久安全属性：mock 标记密钥禁上真实 transport。
- 官方 2026.01 指南取证（[快照](reference/official-guide-202601.md)）：
  uploadretail 单据类型 **321=使用出库**（323 为 open.taobao.com API 页冲突值，
  待平台消歧）；result_type **0=处理中/1=上传成功/3=处理成功/4=处理失败**、
  sub_process_flag **51/52**——已落码（QUERY_STATUS_MAP 补 0→PROCESSING、
  4→REJECTED 终态推进）+ 回归测试。
- 真实探针发现并修复解析缺陷：total_num=0 空结果被误判 unknown
  （[首轮回执](evidence/fb02-our-signer-query-receipt.json)保留现场；
  修复后 [r2](evidence/fb02-our-signer-query-receipt-r2.json)）。
- 上传权限/服务有效期/医院绑定未验证（查询成功≠上传权限）：见
  [EXTERNAL-RECEIPT.md](EXTERNAL-RECEIPT.md)。

## FB-03 生产 HIS 来源与整单采集 — BLOCKED（交付物已备）

- 平台元数据无 V_DRUG_TRACE_*（快照无行≠不存在，但无任何已建证据）；源登记
  write_policy 仍 medical_dict_push → live 按规则阻断，本轮 HIS 活库查询 0。
- DBA 交付包（审阅版+回退说明）：[DBA-PACKAGE.md](DBA-PACKAGE.md)
  （指向既有 r7 四件 SQL，未改动）。缺：视图 DDL 执行、只读账号+受控凭据、
  业务规则批准、可靠变更轴答复。
- 本侧就绪：Oracle Thick 19.32 实测、DSN 注入路径、显式批次采集模式、
  质量等待规则全部在位；等 DBA 解锁后走 30-verify → SourceProfile r3 → 小窗口整单。

## FB-04 校时与正式证书 — 诊断完成；正式项 BLOCKED（院方输入）

- [诊断证据](evidence/fb04-clock-certs.json)：**<prod-host> 时钟与平台权威时间差
  ≤10 秒**（17:02:49Z vs 平台 Date 17:02:59 GMT，[diagnose](evidence/fb05-diagnose-20261021T170259Z.json)）；
  但 chronyd **无可用源**（配置的公池 pool.ntp.org 不通）未同步。
  **DMZ 慢约 32.4 分**（16:30:35Z vs 17:02:59）；chronyd 源 ntp.aliyun.com
  不可达（203.107.6.88 ^?）。→ 院内 NTP 源=网络/运维输入；未猜公共源未改配置。
- 证书：现网 pki-r3 **TEST-ONLY** 链（DMZ 侧核验 notBefore/notAfter/SAN
  IP:<relay-net-host>；CN 明示 NOT FOR PRODUCTION）。正式院方 CA/SAN/EKU 链=院方
  输入；未轮换（不把测试链当正式链验收）。

## FB-05 真实配置发布与正式链路只读查询 — 完成（实测 SUCCESS）

升级后经 API（platform_admin 身份）实际执行，证据链完整：

1. **目的地真实配置发布**：`alihealth-drugtrace-retail` is_mock=False、
   ConfigRevision **PUBLISHED rev1**（2e138e50-…）；内容=AppKey 35085469 +
   `env:BLYB_ALIHEALTH_APP_SECRET`（仅 worker）+ https://<relay-net-host> mTLS 证书
   路径 + allow_hosts。历史 Mock 草稿未篡改（换代发布）。
2. **官方字典批准**：drugtrace.bill_type（PATIENT_DISPENSE→321，官方指南
   证据）、drugtrace.query_bill_type（→A）。ref_ent_id 字典保持草稿
   （真实药房代码未知，不猜）。
3. **EXT-09 query 证据登记 APPROVED**（官方 2026.01 指南 + 平台受理实测）。
4. **就绪动态翻转实测**：LOCAL_SERVICE_READY=READY、PLATFORM_READY=READY；
   SOURCE_READY/SEND_SCOPE_READY 如实 NOT_READY；NETWORK_READY=UNKNOWN。
5. **正式链路只读查询 SUCCESS**（[diagnose](evidence/fb05-diagnose-20261021T170259Z.json)）：
   真实 worker（真实配置 revision+真实签名）→mTLS→DMZ 转发器→平台
   query.billstatus：`found=true, total_num=0, unknown=false`（随机探针单号，
   零上传、零患者数据）。total_num=0 仅证明该查询成功，不证明上传权限。
6. 过程修复：DMZ 出口实际看到的客户端 IP 是跨区 NAT 出口 **<relay-net-host>**
   （非 <prod-host>，转发器日志实证）——allow_ips 追加该地址（保留原值；
   mTLS+方法白名单+限流仍为主门禁；配置备份 forwarder.json.pre-fb05；
   verify 通过）。教训已入 OPERATIONS.md：源 IP 白名单在医院跨区 NAT 下以
   NAT 出口为准。

## FB-06 首张真实上传 — BLOCKED（0 张）

发送前置逐项：来源采集（EXT-01/02）BLOCKED→无真实单据可发；send 证据
（EXT-03/04/05/06/07/08/10/12）中 EXT-04 正式证书、EXT-06 上传权限、
EXT-08 321/323 消歧、EXT-10 既往上报核对、EXT-12 院方 scope 未闭合；
DMZ 白名单仍仅 query（未加 uploadretail——无必要也不应先加）。
**实际真实上传 0 张**；无任何伪造回执。见
[FIRST-BILL-RESULT.md](FIRST-BILL-RESULT.md)。

## FB-07 本地验证、打包、现场升级与交接 — 完成

- 局部：FB-01/FB-02 新增+修订测试全绿（readiness_fb 14、fb02_official 9、
  top_official_reference 6、域/API 回归 53+39）。
- **最终全量回归：405 passed / 7 skipped / 0 failed**（含 JUnit XML
  `implementation/var/fb-final-regression.xml`；7 skipped 均为既有环境依赖项
  ——真PG/实测栈/Windows句柄/真实驱动DSN/50GB压力，理由单列于测试执行记录）。
  上棒 380/7/2 的 2 个 R22 fake 失败在本轮已不复现（此前已修）。
- 前端 `npm run build` 通过（1.62s，产物入镜像）。
- 新发行 **fb-20261021T164800Z**（150 文件 SHA256SUMS+manifest；跳板中继
  传输+哈希校验）→ 备份 → install.sh 升级 → 6 容器 healthy → 迁移 001—011
  不变 → postcheck（SIGNATURE_VERIFIED=True 实测、动态 readiness、mTLS
  health 200）。
- 回退路径：旧发行目录+runtime.env 备份保留；数据库只进不退；
  DMZ 配置回退=恢复 forwarder.json.pre-fb05。
- 四类实施文档/STATE/BACKLOG/HANDOFF/session 已同步（见各文件）。

## 真实执行与未测边界

| 已实测 | 未测/阻断 |
|---|---|
| 全链路只读查询（worker→mTLS→DMZ→平台，SUCCESS） | 真实上传（0张；采集+审批前置未齐） |
| 真实配置发布/字典/EXT-09 证据（API 审计留痕） | HIS 活库（策略阻断，本轮 0 查询） |
| 签名三源对照+平台受理 | uploadretail 权限/服务期/医院绑定 |
| 两机时钟/证书诊断（<prod-host> 与平台差≤10s） | NTP 同步（无院内源）、正式证书链 |
| 升级/回退材料与备份哈希校验 | 生产实际恢复演练（仅 validate-only 级） |
