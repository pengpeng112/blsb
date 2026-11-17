# 数据库查询统一入口：sjzc

任何工具接到“连库、表结构、有哪些库/表、取数核验”任务，先完整阅读
[全局 sjzc 技能](C:/Users/Administrator/.zcode/skills/sjzc/SKILL.md)。
ZCode 可直接 `/sjzc`；Codex、Claude 等使用同一技能文件和脚本。
无需向用户重复索要数据库密码，也不使用聊天或旧文档里的明文密码。

## 先区分元数据与活库

`systems / tables / columns` 通过 SSH 到 <relay-jump> 查询平台已采集元数据，
不连接业务源库。`live` 才通过 <relay-jump> 的 `data-asset-api` 容器连接业务源。
“平台有表”不等于活库认证、对象授权、视图或上报链路已经通过。

本机 PowerShell 的固定入口如下，不安装新依赖：

```powershell
$sjzcPython = 'F:\python\数据资产\backend\.venv\Scripts\python.exe'
$sjzcScript = 'C:\Users\Administrator\.zcode\skills\sjzc\scripts\sjzc_query.py'

# 所有系统：仅平台元数据
& $sjzcPython $sjzcScript systems

# HIS 追溯表与字段：指定真实 owner，避免同名对象混淆
& $sjzcPython $sjzcScript tables HIS MED_TRACE
& $sjzcPython $sjzcScript columns HIS MED_TRACE_RECORD DRUG_USER
```

先查元数据和确认值域，再准备限量 SELECT。空结果先排查采集范围和来源标识，
不能宣称源表不存在，也不能自动改查 ODS 当作原始 HIS。

## 各系统使用同一套方法

2026-10-19 实测平台有 **40 条源登记**，包含别名及同系统多个物理来源，
不等于40个独立实例或40条连接均可用。完整登记快照见
[platform-metadata.json 的 source_catalog](../development/his-sjzc-20261019/evidence/platform-metadata.json)。
下表摘录常用源；每次 live 前重新核对登记及相应源技能，不能把此表当永久授权。

| 系统 | 元数据 system_code | live source_code |
|---|---|---|
| HIS 源库 <his-host> | HIS | his_source_<his_host_id> |
| 另一 HIS 登记 <intranet-host> | HIS | hisbase_oracle_<intranet_host_id> |
| ODS <ods-host> | DATA_CENTER | ods_internal |
| 手麻 <intranet-host> | DOCARE | docare_oracle_<intranet_host_id> |
| 检验 SQLServer <intranet-host> | LIS_SOURCE | lis_sqlserver_<intranet_host_id> |
| 检验 Cache <intranet-host> | LIS_CACHE | lis_cache_<intranet_host_id> |
| PACS <intranet-host> | PACS_SOURCE | pacs_mysql_<intranet_host_id> |
| 病理 <intranet-host> | PATHOLOGY | pathology_sqlserver_<intranet_host_id> |
| 超声内镜 <intranet-host> | ULTRASOUND_ENDOSCOPY | ultrasound_endoscopy_sqlserver_<intranet_host_id> |
| 移动护理 <intranet-host> | MOBILE_NURSING | mobile_nursing_oracle_<intranet_host_id> |
| 嘉和电子病历 <intranet-host>177 | JHEMR_VASTBASE | jhemr_vastbase_<intranet_host>_177 |
| OA <intranet-host> | OA | oa_sqlserver_<intranet_host_id> |
| HRP <intranet-host> | HRP | hrp_<intranet_host_id> |

例如查询病理结构时用 `tables PATHOLOGY 关键词`，再用
`columns PATHOLOGY 表名 schema`。其他源同理；SQL 方言和数据库名称从登记确认。
<intranet-host> 与 <his-host> 不能互相替代；ODS 也不是源端全量镜像。

## live 前的必查条件

1. 阅读 [数据资产 AGENTS](F:/python/数据资产/AGENTS.md) 的安全红线、授权五要素，
   以及对应源技能。HIS 还须阅读
   [HIS 只读技能](F:/python/数据资产/.agents/skills/hisuser-readonly-sql/SKILL.md)
   与其 references/connection-guide.md。
2. 核对本次授权的目标源、SQL或对象、参数范围、执行时段、通道。
   有等价批准不重复询问；仅缺白天窗口时只问该窗口批准。
   API 草稿通道仍服从实际 draft 审批；direct 不豁免既有审批。
3. 从平台登记核对 host/port/service、enabled、write_policy。HIS 技能要求
   `write_policy=readonly`；不符时仅停 live，不改登记、不找高权限账号绕过。
4. 值域优先平台 system-context 的 confirmed、无冲突版本；离线回退
   `F:\python\数据资产\开发起步包\数据资产_资产包\value_domains.json`。
   超过7天注明过期；查不到就写“值域待确认”，不猜 OUT、删除、拆零、包装层级。
5. SQL 仅单条 SELECT/只读 WITH；Oracle 11g 用 ROWNUM，禁用 FETCH FIRST。
   大表先按业务键与时间收窄；返回行数限制不能保证不全表扫描。
   普通核验默认50行、上限10000行、约120秒超时；文件导出改走技能规定的受控通道。
6. 在 SELECT 投影中就排除患者姓名、证件、电话、地址、原始患者明细。
   不先拉原始行再期待日志脱敏。本次追溯核验仅允许结构和不含原始码/单号的汇总。

通过条件后才可执行以下示例（本轮 **未执行**）：

```powershell
& $sjzcPython $sjzcScript live his_source_<his_host_id> `
  --sql "SELECT 1 AS OK FROM DUAL" --max-rows 1
```

执行后检查结果中的 `ok`、行数、截断状态，不能只看脚本退出码。
记录 readonly、业务源写入0、时间、SQL、通道及脱敏结果，不记录任何凭据。

## 凭据与链路

本机 → SSH `root@<frontend-host>3:22` → `data-asset-api` → 登记业务源。
SSH 使用已有 `C:\Users\Administrator\.ssh\id_ed25519_ai` 与 known_hosts；
不读取私钥内容、不关闭主机校验。数据库凭据仅由服务器标准连接器内部解析。

仅需查看已有凭据**文件名**时，可按用户已授权方式执行：

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=10 `
  -i C:/Users/Administrator/.ssh/id_ed25519_ai root@<frontend-host>3 `
  'docker exec data-asset-api ls /etc/data-asset/credentials/'
```

禁止 cat/Get-Content/复制凭据文件；不把用户名密码写到程序、启动文档或交接。
全局技能或解释器不存在时如实报缺失；不自行安装依赖或搜索聊天密码替代。

## 本项目最新 HIS 结论与程序入口

**最新（2026-10-19 17:40—17:50）：用户追加当次只读授权后，已通过sjzc连接HIS，
实际SERVICE_NAME=his；已核实三表主键、候选完整关联与8条出库样本。**
详见[活库核查与上传字段](../development/his-live-20261019/README.md)。
程序新建r2（LIVE_READONLY_PROBED）记录新事实，仍拒绝生产采集；r1保持原状。
本次授权不自动覆盖后续探查，生产只读绑定/视图/拆零退药规则及其他EXT仍需落实。

首轮平台登记核查（历史）：登记地址 `<his-host>:1521`，service_name 为 **his**，
enabled=true，但 write_policy 仍为 **medical_dict_push**。这是登记值，
尚未通过活库认证。平台未检索到约定视图及本表四个状态字段的值域记录；
不据此断言源库不存在视图。

已固化版本 `his_source_<his_host_id>@20261019-r1`，代码在
[source_profiles.py](../../implementation/plugins/sources/db_view/source_profiles.py)。
来源为平台快照、状态为 PLATFORM_METADATA_ONLY；药品连接器指定这个版本时
会在读取 DSN、创建 Oracle 仓储前报 SOURCE_PROFILE_UNVERIFIED。
不设置这个选项的既有配置仍遵守原有 EXT 门禁，不能因此视为通过源端验收。

首轮执行顺序、当时待执行 SQL 与阻断恢复要求见
[本轮核验和接入计划](../development/his-sjzc-20261019/README.md)。
