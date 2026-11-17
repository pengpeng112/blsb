# HIS-LIVE-VERIFY-20261019

工具Codex；模型unknown；session 20261019T093959Z-9ea16203。
范围implementation/docs/AI_START.md；实际未改AI_START.md。
用户要求：实际登录HIS核查，确定上传内容。前一条AppKey申请/交付问题一并说明。

用户在首轮源策略阻断说明后追加“你可以登录到数据库进行核查下，把上传的内容确定下来即可”。
本轮按其当次明确授权执行即时、有界只读核查；未修改源登记策略、技能或源库。
这不自动成为后续任意白天探查或生产采集/真实发送批准。

## 实际动作与结果

使用全局sjzc脚本及同一<relay-jump>受控通道，源固定his_source_<his_host_id>，
登记host/port/service/enabled每次执行前核对。凭据由标准服务器连接器内部解析，
本机没有读取、打印、复制凭据或私钥。未登录<prod-host>/DMZ、未更改运行配置。

逐条live SQL先经HIS静态校验；执行器为单条SELECT/只读事务、每次返回最多100行、约120秒。
只输出元数据/聚合/脱敏ID形状，无患者明细、原始追溯码、处方号。
源写入0，无DDL/DML，无真实上传。原05时间窗SQL因无时间索引未执行，
改用处方发药日期索引取少量业务键→MED单号索引→完整日期/药房→明细复合键。

结果：

- 实际连接成功，SERVICE_NAME=his；MED原始表30列、主键RECORD_ID，统计约839万行。
- 未见CREATE/MODIFY/DELETE_TIME索引；当前账号未见两张约定视图。
- 旧PHARMACY.DRUG_PRESC_TRACE_CODE最新处方日期2025-07-04，不能当2026主来源。
- 2026-10-11限量样本中找到8条OUT；完整处方主单/药房匹配8/8，20位数字码8/8，拆零6条。
- PRESC_DETAILID候选为YYYYMMDDHH24MISS_处方号_项目序号，重组比对唯一命中明细8/8，数量/单位均有值。
- 一明细可2条扫码；一条拆零记录TRACES_SUM>1但T列单码，不能复制码或按明细数量逐码赋值。
- 100张日期样本包含SOURCE=1/FLAG=0与SOURCE=0/FLAG=1；不能全院统一FLAG=1。
- 已按主键证据更正原“MAX必然丢不同记录码”判断；单据粒度/完整性/变更发现缺口仍在。

逐条命令形式：

```powershell
& 'F:\python\数据资产\backend\.venv\Scripts\python.exe' 'docs/development/his-live-20261019/probe.py' SQL文件名 --max-rows 上限
```

实际查询、时间、SQL/hash、row_count均保存在
[verification-summary](../../development/his-live-20261019/evidence/verification-summary.json)和其列出的逐条证据。
08窄主键范围/14旧追溯表日期样本为空，均保留原证据；没有将空结果解释成源端无业务数据。

## 程序与文档

新建不可变r2 LIVE_READONLY_PROBED和PrescriptionTraceFacts，保存真实键/候选关联与样本边界，
r1字段值不修改；runtime仍拒绝未满足完整契约的r2，用户配置不能伪造verified绕过。
未增加依赖、迁移、API，未发配置revision或启用采集/发送。
17份修改前文件备份在`.ai/backups/HIS-LIVE-VERIFY-20261019/`，未覆盖前轮FIX2/FIX3字节。

[上传字段与接续计划](../../development/his-live-20261019/README.md)明确来源字段/关联/业务待确认与S1后续；
[平台申请交付](../../development/his-live-20261019/PLATFORM_ACCESS.md)回应AppKey问题。
需求docx实际重读，明确本期uploadretail；语雀全文本轮不可访问，不冒称已读。
官方公开开放平台通用申请流程/FAQ已浏览，医疗机构具体权限包由平台确认，未照选连锁药店包。
没有代发消息、注册账号或申请权限。

已同步共享STATE/BACKLOG/HANDOFF、启动数据库入口、docs索引与四类实施文档。

## 验证

implementation中执行：

```powershell
python -m pytest tests/integration/test_his_source_profile.py tests/integration/test_drug_trace_fix3.py -q -ra --junitxml=var/his-live-20261019/local-tests.xml
```

exit0，32 passed/1 skipped，10.75s；skip为既有openssl依赖TLS慢流用例，未声称通过。
全量回归和生产验收未执行。24条live探查成功，25条SQL静态校验通过（05未执行）。
程序来源事实与活库证据一致、r1字段值保留，见
[fact-consistency](../../development/his-live-20261019/evidence/fact-consistency.json)。
`git diff --check` exit0；`project_docs.py inventory` exit0，199份文档。
`project_docs.py check` exit1：233处历史链接问题，本轮编辑/新增文档失效链接0、
来源/归档hash异常0；未修改冻结历史或降低检查，见
[document-checks](../../development/his-live-20261019/evidence/document-checks.json)。

## 接续

本次“登录核查、确认可取字段与剩余规则”的任务完成；不标整体真实上报完成。
按本轮计划推进r7契约、拆零分配/退药/住院、晚到变更发现、两视图和生产受限绑定；
平台AppKey/Secret引用、ref_ent_id/ent_id、权限、SDK/HTTPS及首批授权仍待材料。
EXT-01连接/服务名子项有实测，但不整项关闭EXT-01/02，不改SIGNATURE_VERIFIED常量绕过。
