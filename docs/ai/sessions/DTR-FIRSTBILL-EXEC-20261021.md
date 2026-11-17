# DTR-FIRSTBILL-EXEC-20261021 session

- 工具/模型：ZCode / GLM-5.3
- 时间：2026-10-21 15:57 UTC — 2026-10-22 01:1x UTC（本地）
- 任务书：[NEXT_AI_PROMPT](../../development/drug-trace-push-review-20261021/NEXT_AI_PROMPT.md)
- 范围：drug_trace 领域/适配器/来源、apps/init_drug_trace+publish_drug_trace_config、
  UI DrugTrace.vue、tests、deploy、任务目录 docs/development/drug-trace-firstbill-exec-20261021/、docs/ai
- 交付：EXECUTION-REPORT / ACCEPTANCE / EXTERNAL-RECEIPT / FIRST-BILL-RESULT /
  OPERATIONS / SIGNATURE-QUALIFICATION / DBA-PACKAGE + evidence 25 份 + reference 1

## 实际改动

**代码**（发行 fb-20261021T164800Z，150 文件）：
1. `plugins/domains/drug_trace/readiness.py` 新增——CLI/API/UI 共用分层就绪
   （动态证据/字典/签名；多目的地歧义拒绝；NETWORK 恒 UNKNOWN）。
2. `routes.py`：readiness 接共享模块+query 参数；新增
   GET/POST `/drug-trace/destinations/{id}/config-revisions`（dry-run/DRAFT/publish
   分级权限；明文密钥 fail-closed）。
3. `service.py`：`validate_destination_config`/`publish_destination_config`
   （世代切换/同 digest 幂等/set_real 限 DRAFT）；query 结果 REJECTED 终态推进。
4. `contracts.py`：QUERY_STATUS_MAP 补 0→PROCESSING、4→REJECTED（官方 2026.01
   指南取证）。
5. `protocol.py`：SIGNATURE_VERIFIED=True（批准修订，常量处证据链注释）；
   assert_signing_allowed 改为 mock 标记密钥永久拒绝；parse_query_response
   修复 total_num=0 空结果误判 unknown（平台实测发现）。
6. `apps/init_drug_trace.py`：readiness 委托共享模块；--profile/--destination/
   --config-release；check_state 配置口径修正（connector@dest）。
7. `apps/publish_drug_trace_config.py` 新增（check/dry-run/apply）。
8. UI DrugTrace.vue：目的地/MOCK/歧义列。
9. 测试：test_drug_trace_readiness_fb.py（14）、test_drug_trace_fb02_official.py（9）
   新增；test_drug_trace_api/domain/fix3 按新语义修订（不弱化断言）。

**现场**：<prod-host> 升级 fb-…164800Z（备份 20261021T165233Z→install→postcheck；
restore --validate-only 过）；经 API 发布真实配置 PUBLISHED rev1+两官方字典+
EXT-09 query/reconcile 证据；worker 全链路只读查询 SUCCESS（403 NAT 定位→
DMZ allow_ips 加 <relay-net-host>→SUCCESS）；DMZ 配置备份可回退。

## 真实命令/结果（关键）

- 全量：`python -m pytest tests -q -ra --junitxml=var/fb-final-regression.xml`
  → **405 passed / 7 skipped / 0 failed**（exit 0）
- UI：`npm run build` → ✓ 1.62s
- SDK 对照：`fb02_sign_sdk_comparison.py` → non-empty vectors agree（4/4）
- 签名探针：with-credentials 包装 `fb02_probe_our_signer_query.py r2` →
  平台 SUCCESS/total_num=0（receipt-r2）
- 部署/联调：`deploy_fb05.py transfer|backup|upgrade|postcheck|api-setup|diagnose`
  各步 evidence 25 份（含 403 现场与修复后 SUCCESS 对照）

## 未完成/阻断（全部外部输入）

DBA 视图+只读账号（FB-06 采集前置）；正式证书链；院内 NTP 源；
uploadretail 权限/服务期书面确认；321/323 消歧；既往上报核对+首张 scope。
**真实上传 0 张**，无伪造回执。

## 下一步

见 HANDOFF「下一棒」五步；系统侧无再开发项。
