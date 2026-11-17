# HIS sjzc 核验与接入计划（2026-10-19）

任务 HIS-SJZC-BIND-20261019，工具 Codex，模型 unknown。
用户已授权经 sjzc 限量只读核验，并将结果固化程序、查询方法加入启动文件。
本轮成功访问 **<relay-jump> 平台元数据库**，尚未连接 HIS 活库，未发送任何真实上报。
本轮没有修改<relay-jump>/<prod-host>/DMZ/HIS运行配置、读取凭据内容或安装依赖。

## 1. 本轮实际查到什么

证据为 [platform-metadata.json](evidence/platform-metadata.json)，
采集时间 2026-10-19 17:13:05（Asia/Shanghai）。查询使用 sjzc 的只读 SSH/psql 通道，
只投影登记和元数据字段。可复核脚本为
[collect_platform_metadata.py](collect_platform_metadata.py)，不导入凭据解析器，不读取凭据列。
该文件为本轮事实快照，后续复查应另建日期/revision目录，勿覆盖已交付证据。

| 项目 | 本轮证据 | 能证明的范围 |
|---|---|---|
| 全部数据源登记 | 40条，含多个别名/同系统不同来源 | 平台现有登记；未逐个连库 |
| HIS source_code / system_code | his_source_<his_host_id> / HIS | 当前源登记及对应元数据来源 |
| 目标 | <his-host>:1521；oracle；direct；enabled=true | 登记值；不是本轮TCP或认证测试 |
| 服务名 | service_mode=service_name；service_name=his | 平台登记值；实际会话服务名待核验 |
| 只读策略 | **medical_dict_push** | 不满足 HIS 技能要求的 readonly；未自动修改 |
| 同地址其他源 | 无另一条只读登记 | 不能切换到合规同地址源继续 |
| 追溯表 | DRUG_USER.MED_TRACE_RECORD，30列 | 表和字段记录均归属 his_source_<his_host_id> |
| 约定视图 | 目录未检索到 V_DRUG_TRACE_BILL / V_DRUG_TRACE_CODE | 只能说明目录未检索到，源端存在性/授权待验 |
| 状态值域 | 本表 IN_OUT_TYPE/KIND_TYPE/IS_DELETED/SPLIT_FLAG 记录0条 | 含义待确认，不能将 OUT 自动映射为患者发药 |

平台 system-context 的值域来自平台值域表。本轮未调用该API，
直接只读核对平台值域表的精确 source/owner/table/column 范围，未发现记录，
未伪称调用了该API。离线 value_domains.json 修改时间为2026-08-29，已超过7天，
亦未检索到本表四字段；不能用过期且无匹配的资产补造映射。

HIS 系统另登记 <intranet-host>（hisbase_oracle_<intranet_host_id>），
不是本次指定的<his-host>；未用它替换目标。ODS也未作为源库替代。

## 2. 已固化到程序

[source_profiles.py](../../../implementation/plugins/sources/db_view/source_profiles.py)
新增不可变版本 `his_source_<his_host_id>@20261019-r1`，保存地址、登记服务名、
物理表、30列原始名称/类型、核验层级、证据位置与未决项，不含账号密码。
候选列不代表业务映射已经批准。

连接器配置可显式引用：

```json
{
  "mode": "oracle",
  "source_profile": "his_source_<his_host_id>@20261019-r1",
  "dsn_env": "BLYB_DRUGTRACE_ORACLE_DSN"
}
```

这是待接入配置示意，不是可直接投产配置。
`make_warehouse` 实际读取 source_profile 并在读取DSN、创建Oracle仓储前
报 `SOURCE_PROFILE_UNVERIFIED`，防止将元数据核验冒充源端验收。
未知revision、空revision或synthetic模式使用真实来源档案也拒绝。
不指定这个可选键的既有配置保持原行为及原EXT门禁，不能视为已绑定新档案。
本轮未发布任何配置revision、未启用药品插件、未关闭 EXT-01/02。

后续证据齐备后建立r2，绑定真实只读连接及批准对象；不得把r1的登记值改成
“已通过活库”，也不能仅删除source_profile绕过验收。
sjzc是开发/核验入口；生产worker继续使用批准连接器和视图，不通过AI脚本定时抽数。

## 3. 用户原SQL的处置

字段在平台目录中存在，但原SQL不能直接作为正式上报视图。

| 原SQL/字段 | 需要处理 |
|---|---|
| 未指定owner的 MED_TRACE_RECORD | 已定位候选真实owner为 DRUG_USER；活库授权仍待验 |
| LISTAGG(PRESC_DETAILID) 当单据号 | 不保证稳定主单身份；聚合可能溢出或使幂等号随明细变化 |
| MAX(T_TRACE_CODE) | 多码组可能丢码；只有实际核验能判断是否存在多行组，不能宣称已观测到丢码 |
| GROUP BY RECORD_ID, CREATE_TIME | RECORD_ID 在目录中非空不等于已确认主键，也不等于整张发药单 |
| 固定历史 BETWEEN | 结束点为10-12零点；需明确是否包括10-12整日，再采用半开窗口 |
| 仅 CREATE_TIME | 看不到历史单的补码、修改、删除；需确认三类变更的增量口径 |
| OUT、IS_DELETED、SPLIT_FLAG、KIND_TYPE | 值域全部待确认；不猜出库场景、正常记录、拆零和包装层级 |
| F/S/T_TRACE_CODE | 三列确为VARCHAR2；哪级是真实上传码、父子关系及拆零数量均待确认 |
| TRACE_BILLNO / PRESC_DETAILID / STORE_CODE | 都是候选关联字段；不能根据名字自动JOIN或当成医院/药房批准码 |

下一版视图仍按现有 [r6契约](../../deployment/reporting-20261015/view-contract-r6.md)
分主单与逐码明细，不提前聚合，不转追溯码为数值，不虚构finalized/revision/数量。
此处仅提供核验与实现顺序，不执行源库建视图DDL。

## 4. 按依赖推进的执行计划

### S1 平台管理员确认真正只读的接入登记

HIS 技能当前明确停止于 `write_policy != readonly`。
请平台管理员核实原登记的字典推送用途与读取凭据权限，提供合规的只读源登记/用途绑定，
必要时独立登记同物理库的只读source；不要直接把原 medical_dict_push 改掉影响现有字典推送。
凭据仍由服务器托管，只需向后续AI交付source_code与权限确认，不提供明文密码。
这属于平台权限/配置变更，本轮没有代为执行。

验收：平台登记命中<his-host>:1521，enabled、只读策略及账号权限满足技能，
服务名从登记读取。登记修复本身不等于活库已通。

### S2 核对本次 live 时段与通道

已有当前连接请求，不重复问是否开始；仍需按数据资产AGENTS核对授权五要素。
本轮白天没有明确的限量live时段豁免，未擅自执行。S1恢复后，若缺白天批准，
只针对下述已准备SQL的本次窗口请求批准；不能问一遍笼统“能否登录”。
使用sjzc direct限量通道；若改API草稿执行，检查具体draft审批，不绕过。

### S3 连通、字段、索引、视图只读核验

以下5条SQL均已通过静态检查（非活库执行），证据见
[sql-static-checks.json](evidence/sql-static-checks.json)。单条约120秒超时，顺序执行：

| SQL | 允许范围 | 本轮状态 |
|---|---|---|
| [01 连接](sql/01-connection.sql) | DUAL 1行，OK与实际SERVICE_NAME，无患者数据 | 未执行 |
| [02 字段](sql/02-columns.sql) | 精确owner/table的数据字典，最多50行 | 未执行 |
| [03 索引](sql/03-indexes.sql) | 同一表索引元数据，最多50行 | 未执行 |
| [04 视图](sql/04-contract-views.sql) | 两个精确视图名，最多20行 | 未执行 |
| [05 样本汇总](sql/05-sample-summary.sql) | 历史区间内2026-10-11一天，最多100条源记录；只输出分组计数 | 未执行，先确认索引与查询代价 |

执行器不接受注释时机械去除注释生成临时执行版，再静态校验；不改变WHERE/JOIN/聚合。
max-rows分别设置1/50/50/20/100。第05条只能证明有界样本，不能宣称全量唯一性、
全值域、全部有索引或全期数据完整；空结果也不能证明整个历史没有数据。
若无适用时间索引，暂停05并请院方提供脱敏业务键，不能强扫整表。
不输出原始追溯码、单号、患者或操作员姓名。

失败按错误类型登记，停止依赖项；不试历史聊天密码、不扫描网络、不自动改服务名。
验证成功后保存新revision证据，更新EXT-01中已完成的具体子项；视图与业务口径未齐则EXT-02保持OPEN。

### S4 院方确认映射，再形成可评审视图SQL

确认主单/明细/扫码键、真实处方父子关联、跨药房唯一性、最终发药/撤销状态、
OUT等值域、F/S/T包装层级、补码修改删除事件、药品单位/拆零依据与历史已报边界。
字段缺失时只登记缺口，不能填常量假装有完成状态。
先经同系统元数据限定候选关联表，再做已授权的业务键小样本核验。

形成r7契约及建视图/授权交付SQL，由院方DBA单独执行；采集账号仅获两个视图SELECT。
用实际视图进行只读预览、0/1/N码、补码/删码/退药、跨药房重号与重跑幂等核验。
不得将SQL模板存在标记为真实视图已验收。

### S5 固定运行来源并完成平台联调

程序新增经过源端契约验证的r2档案，连接器发布新revision，绑定正式视图owner/对象和
受限只读凭据引用；不把<relay-jump>凭据文件复制到工作站/<prod-host>，也不将密码写入DSN示例。
<prod-host>生产账号如何安全注入由部署配置确认，不能因开发入口可查就认为生产worker已配置。

用户此前提供uploadinoutbill SDK，需求材料曾指向uploadretail，程序已分两profile。
根据实际业务和批准单据字典选择；不能从 IN_OUT_TYPE='OUT' 推断某一个接口。
还需平台AppKey/Secret的安全引用、企业ref_ent_id/ent_id、权限包/服务有效期、
SDK签名对照、官方状态和单据枚举、DMZ正式HTTPS链路/证书/NTP、历史去重口径。
沿用 EXT-03—12；本轮未替代这些证据。

先现场禁发升级和只读预览，再按独立真实发送授权传首批小样本、查询终态和码级对账，
医院签认后才扩大范围。HIS查询成功或HTTP受理均不能当作追溯上报成功。

## 5. 交接与证据

统一方法已加入 [AI_START](../../../AI_START.md)、
[START_HERE](../../ai/START_HERE.md)、[启动说明](../../startup/README.md)，
详细通道见 [DATABASE_ACCESS](../../startup/DATABASE_ACCESS.md)。
后续AI先从这些入口读取，不需要用户重新解释连库方法或发送密码。

本轮验证与交接见 [session](../../ai/sessions/HIS-SJZC-BIND-20261019.md)。
修改前18份相关文件已备份在本机 `.ai/backups/HIS-SJZC-BIND-20261019/`，
包含manifest与baseline.zip，不含运行库或凭据；可恢复本轮接手前的未提交FIX3字节。
现有Git修改未重置、未提交、未推送。

验证：相关局部测试31 passed/1 skipped（openssl缺失，既有TLS慢流用例未跑）；
5条SQL静态检查均exit 0，源端执行0。来源档案与平台快照30列、地址/服务/策略逐项一致，
见[local-verification](evidence/local-verification.json)。
全仓`project_docs.py check` exit 1：233处失效链接位于原有快照/证据文档，
本轮编辑及新增文档失效链接0，来源/归档hash异常0；没有改历史文件或降低检查。
文档目录inventory已更新，exit 0。详见[document-checks](evidence/document-checks.json)。
