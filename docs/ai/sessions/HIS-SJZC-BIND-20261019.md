# HIS-SJZC-BIND-20261019

- 工具：Codex；实际模型标识不可获知，记unknown。
- session：20261019T090736Z-cfe37fe9；2026-10-19；范围implementation、docs、AI_START.md。
- 用户要求：经sjzc只读核验HIS，把确认结果固化程序，把各库查询方法写入统一启动入口。
- 接手时已有FIX2/FIX3大量未提交修改；未重置、覆盖或提交这些改动，未开启并行代理。
- 阅读根AGENTS、共享三入口、implementation/AGENTS及全局sjzc；按其流程阅读数据资产
  AGENTS、hisuser-readonly-sql及连接参考、ops-runbook；未读取任何凭据或私钥内容。

## 实际完成

通过已有SSH key/known_hosts到<relay-jump>平台元数据库，使用sjzc同一只读通道查询：

1. `sjzc_query.py tables HIS MED_TRACE`：exit 0；定位DRUG_USER.MED_TRACE_RECORD。
2. `sjzc_query.py columns HIS MED_TRACE_RECORD DRUG_USER`：exit 0；30列。
3. `collect_platform_metadata.py`：exit 0；平台登记/精确表与字段的source_code/状态值域/
   约定视图及40条源登记；证据[platform-metadata](../../development/his-sjzc-20261019/evidence/platform-metadata.json)。

当前HIS源write_policy仍medical_dict_push，同地址无另一只读登记；服务名登记为his，
不是成功认证结果。故在凭据解析/活库连接前停止。无HIS、<prod-host>、DMZ登录或业务查询。
无写库、源端DDL、平台注册变更、依赖安装、真实发送。平台表未检索到状态值域或约定视图记录，
不能据此断言源库不存在。离线值域资产过期且无本表匹配，不采用推测映射。

新增不可变来源档案source_profiles.py；make_warehouse可选source_profile绑定，
已知r1为PLATFORM_METADATA_ONLY、始终阻断Oracle初始化，未知/空版本或模式冲突拒绝。
不改变既有synthetic/无来源档案配置的行为，仍保留原有EXT门禁。
档案保存登记及候选字段事实，不含账号密码，不将元数据当生产视图/业务映射。

统一数据库入口已加入AI_START、START_HERE、startup/README、docs/README，
细节在[DATABASE_ACCESS](../../startup/DATABASE_ACCESS.md)。
[本轮计划](../../development/his-sjzc-20261019/README.md)明确S1—S5、缺口、5条准备SQL与验收要求。
四类实施文档和STATE/BACKLOG/HANDOFF同步；未给工具另建独立状态体系。

修改前18份相关现有文件已保留本机恢复基线：
`.ai/backups/HIS-SJZC-BIND-20261019/{baseline.zip,manifest.json}`；不含运行库和凭据。

## 验证

- 5条SQL经HIS技能validate_his_sql.py：各exit 0，仅静态检查；
  [证据](../../development/his-sjzc-20261019/evidence/sql-static-checks.json)。
- implementation下运行：

```powershell
python -m pytest tests/integration/test_his_source_profile.py tests/integration/test_drug_trace_fix3.py -q -ra --junitxml=var/his-sjzc-20261019/local-tests.xml
```

exit 0，31 passed / 1 skipped，11.12s；XML在implementation/var/his-sjzc-20261019/。
skip为openssl不可用导致既有TLS慢流用例未跑；未声称全量回归或源端实测通过。
来源档案与本轮平台快照逐项一致（30列、地址、服务、策略、时间），
[一致性证据](../../development/his-sjzc-20261019/evidence/local-verification.json)。
`git diff --check` exit 0（仅换行符提示）；对任务备份的service.py独立差异只有7行新增，
保留原FIX3全部代码：[任务内差异](../../development/his-sjzc-20261019/evidence/service-change.diff)。
`python scripts/project_docs.py inventory` exit 0，目录196份文档。
`python scripts/project_docs.py check` exit 1：233处失效链接均在本轮未改的历史快照/证据文档；
本轮编辑及新增文档失效链接0，来源/归档hash异常0。
未修改冻结历史或增加豁免；[文档检查证据](../../development/his-sjzc-20261019/evidence/document-checks.json)。

## 未完成及下一步

HIS live尚未执行，当前任务以handed_off交接，不能标完整连库完成。
管理员确认合规只读登记/用途，保留原字典推送能力；后续AI核对live执行窗口与审批，
再执行本轮准备的限量SQL。视图、业务值域、主单身份/完成状态、F/S/T层级及变更口径确认后，
交付新视图契约与r2来源档案，再做只读预览。真实发送仍需独立授权和其余EXT证据。
不要向用户重复索要数据库密码，不改SIGNATURE_VERIFIED常量，不通过移除source_profile绕过验收。
