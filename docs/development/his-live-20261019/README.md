# HIS 活库核查与上传内容确认（2026-10-19）

任务 HIS-LIVE-VERIFY-20261019，Codex / unknown。
**HIS已实际连接成功，服务名为his；已找到门诊发药主单—处方明细—逐码记录的样本关联。**
这更新了上一轮“仅平台元数据、未连接”的状态；上一轮r1证据保留。
没有发送真实上报、执行源库DDL/DML、修改平台注册策略或读取/打印凭据内容。

## 1. 授权、范围与执行方式

用户在收到上一轮登记策略阻断说明后，明确追加：
“你可以登录到数据库进行核查下，把上传的内容确定下来即可”。
本轮据此执行当次限量只读核验，窗口为本轮即时，目标仍为<his-host>，
仅元数据及按主键/处方键/索引日期收窄的脱敏汇总，经sjzc/<relay-jump>受控连接器执行。
这是本次用户授权范围内的核验，不修改技能全局规则或把平台登记标记为readonly。

当前源write_policy仍为medical_dict_push。本次明确授权只适用于此次只读核验，
不作为后续任意白天探查、生产自动采集、配置修改或真实发送授权。
程序仍拒绝未满足正式契约的来源档案；生产受限账号/用途绑定仍需落实。
数据库凭据只在<relay-jump>标准连接器内部解析，本机没有读取内容。
连接器使用只读SQL校验与Oracle只读事务；业务源写入0。

可复核执行器为 [probe.py](probe.py)，只接受本任务sql目录中的SQL文件名，
单次返回上限100行、约120秒超时；SQL先静态校验。
输出保存为新的evidence文件，使用排他创建防覆盖。错误只输出错误代码，不输出原始异常或凭据。
所有成功查询的SQL、摘要、注册端点、时间、结果均在[evidence](evidence/)中。

## 2. 实测结论

| 项目 | 活库证据与限制 |
|---|---|
| 连接 | [01](evidence/01-connection.json)：OK=1、实际SERVICE_NAME=his |
| 追溯表/字段 | [02](evidence/02-columns.json)：DRUG_USER.MED_TRACE_RECORD，30列；T_TRACE_CODE是VARCHAR2(255) |
| 主键 | [06](evidence/06-keys-and-stats.json)：RECORD_ID为启用主键 |
| 索引 | [03](evidence/03-indexes.json)：RECORD_ID、T_TRACE_CODE、TRACE_BILLNO；未见创建/修改/删除时间索引 |
| 规模 | [09](evidence/09-table-stats.json)：8392907行，2026-10-13统计估计；不是本轮精确COUNT |
| 两张约定视图 | [04](evidence/04-contract-views.json)：当前账号未见V_DRUG_TRACE_BILL/CODE；不等于其他账号也没有 |
| 对象来源 | [18](evidence/18-trace-object-resolution.json)：可见同名对象为DRUG_USER.MED_TRACE_RECORD |
| 处方主键/索引 | [12](evidence/12-prescription-indexes.json)、[15](evidence/15-prescription-keys.json)：主单(PRESC_DATE,PRESC_NO)，明细再加ITEM_NO；发药时间有索引 |
| 旧处方追溯表 | [17](evidence/17-prescription-trace-date-boundary.json)：PHARMACY.DRUG_PRESC_TRACE_CODE日期首尾为2024-11-09至2025-07-04；2026-10-11取样0，不能作为2026本期主来源 |

未执行上一轮05“仅按CREATE_TIME扫描”的SQL。主键首尾各100条样本都是IN、CREATE_TIME空，
不能据此推断整表只有入库。随后以处方发药日期索引取业务键，再用TRACE_BILLNO索引关联出库。
本轮没有运行用户2026-07-23到10-12的全期统计，也没有导出原始码、处方号或患者明细。

### 实际发药样本

2026-10-11发药日期窗口，最多10张候选处方，按完整处方日期/号/药房匹配到8条出库扫码记录：

- 8条均为20位数字小码，有PRESC_DETAILID；完整处方日期、药房匹配8/8。
- 6条SPLIT_FLAG=1，2条=0；本样本均IS_DELETED=0，KIND_TYPE为空。
- 8条均有MODIFY_TIME。不能只依赖创建时间发现后续补码/更正。
- 6条拆零记录中，有1条TRACES_SUM大于1，但T_TRACE_CODE只有单个20位数字串。
  不能据此复制码或认定数量应直接等于TRACES_SUM。
- 8条扫码关联7条处方明细：6条明细各1码，1条明细有2条扫码记录。
- 8/8全部唯一匹配明细，D.QUANTITY和D.PACKAGE_UNITS均有值；
  数量是明细层值，尚未证明码级分配或包装换算。

证据：[20出库汇总](evidence/20-linked-out-summary.json)、
[22关联核验](evidence/22-verified-key-detail-overlap.json)、
[24复合明细键](evidence/24-composite-detail-key-check.json)、
[25明细/码度数](evidence/25-detail-code-cardinality.json)。
样本只证明这些记录，不能代表全院、全药房、全历史或退药/住院场景已验证。

## 3. 本期接口与上传字段怎么取

原始[需求确认单](../../../数据上报/1、码上放心平台/(260721)需求确认单-码上放心平台HIS对接出库销售上传(1).docx)
明确模块为“药房发药销售上传”，指定
`alibaba.alihealth.drugtrace.top.yljg.uploadretail`。
因此本期以现有retail profile对接患者发药；此前提供的uploadinoutbill SDK属于另一profile，
不能直接替换本期需求。供应商采购入库/退供应商作为后续独立场景。

| 业务/上传内容 | 来源与处理 | 当前确认程度 |
|---|---|---|
| 来源系统 | 已登记HIS源标识，连接配置固定revision | 实际连接已验 |
| 主单身份source_bill_id | 完整PRESC_DATE＋PRESC_NO；复合路由仍含药房/业务类型/来源系统 | 主键已验；编码规则须固定，不只用处方号 |
| 平台bill_code | 从稳定主单身份生成可重复的字符串，并含必要机构/药房/业务区分；需校验平台长度与既往已报编号 | 实现规则待批准；禁止用LISTAGG明细ID |
| 药房pharmacy_code | M.DISPENSARY；对应T.STORE_CODE | 样本8/8匹配；医院/平台映射仍需批准 |
| 发药业务时间bill_time | M.DISPENSING_DATETIME | 字段/索引已验；不能拿扫码CREATE_TIME代替发药时间 |
| 追溯码trace_codes | T.T_TRACE_CODE，保留原字符串，每条真实扫码记录一行 | 样本8条均20位数字；不转数值、不MAX、不复制码 |
| 码记录身份source_code_record_id | T.RECORD_ID | 真实主键已验 |
| 明细身份source_line_id | T.PRESC_DETAILID原文 | 已证实是复合文本；不能当数字或单据号 |
| 药品/批次（内部核验） | D.DRUG_CODE、D.DRUG_SPEC、D.FIRM_ID、D.BATCH_NO等；T.DRUG_ID不直接假设与D.DRUG_CODE相同 | 需按正式接口支持的字段取舍，不把内部字段全发平台 |
| 明细数量和单位 | D.QUANTITY、D.PACKAGE_UNITS | 样本8/8有值；不能逐码重复整条明细数量 |
| 包装/码层级 | T列注释“小码”，F/S注释大/中码 | 原始语义已查，平台code_level数值映射未批准 |
| 拆零 | T.SPLIT_FLAG及明细数量/包装依据 | 真有拆零样本；每码分配、剩余量/重复使用规则待院方与平台确认 |
| 有效/撤销记录 | T.IS_DELETED、DELETE_TIME，以及处方退药/退费状态 | 源注释已有，未验证删除/退药真实样本 |
| 完成状态finalized | M.DISPENSING_DATETIME结合批准的发药/退药/退费状态及完整码确认 | 不得直接填true；当前未批准完整规则 |
| 增量updated_at/revision | 需要覆盖主单、所有明细和码的补录/修改/删除，包括原先无码单 | 还需变更发现方案；不能只有MAX(CREATE_TIME) |
| ref_user_id（上传） | 平台医院ref_ent_id映射，按批准配置固定 | 需平台提供，不能用HIS ORG_CODE代替 |
| bill_type | 根据患者发药/退药业务匹配平台批准字典 | 不从OUT、FLAG或旧测试323猜值 |
| AppKey/签名身份 | 正式平台应用；AppSecret安全引用 | 需申请/提供，见[申请交付说明](PLATFORM_ACCESS.md) |

患者姓名、证件、手机号、地址不纳入本次基础上传内容。未来某必需患者字段须单独确认最小范围与授权。

### 已验证的候选关联

```sql
-- M：PHARMACY.DRUG_PRESC_MASTER；T：DRUG_USER.MED_TRACE_RECORD
T.TRACE_BILLNO = TO_CHAR(M.PRESC_NO)
AND T.PRESC_DATE = M.PRESC_DATE
AND T.STORE_CODE = M.DISPENSARY

-- D：PHARMACY.DRUG_PRESC_DETAIL；完整处方主键先收窄
D.PRESC_DATE = M.PRESC_DATE AND D.PRESC_NO = M.PRESC_NO
AND T.PRESC_DETAILID = TO_CHAR(D.PRESC_DATE,'YYYYMMDDHH24MISS')
    || '_' || TO_CHAR(D.PRESC_NO) || '_' || TO_CHAR(D.ITEM_NO)
```

这里PRESC_DATE保留秒级完整值，不TRUNC成日。上述关联在8条样本上通过，
仍需院方签认及更多反例核验。禁止将药品编码的偶然相等作为明细关联。

对用户原SQL的更正：现在证实RECORD_ID是主键，单表按它分组每组仅1条，
所以不能笼统说该SQL的MAX必然吞掉不同记录的码。实际问题是它产生扫码记录级“单据”，
PRESC_DETAILID并非处方号，CREATE_TIME并非发药时间，且无稳定主单/整单完整性和变更发现。
旧报告保留当时判断，此处按新证据修正，不能继续引用“必然丢码”作为本表既定事实。

## 4. 源注释与状态不能混用

[07源注释](evidence/07-column-comments.json)明确IN/OUT为入/出库、
IS_DELETED的0/1为未删除/删除，SPLIT_FLAG的0/1为否/是。
这是源端注释证据，尚未形成平台confirmed值域或本项目批准字典。

[13处方注释](evidence/13-prescription-comments.json)记录FLAG的1/2/3为正常发药/全退/部分退，
PRESC_SOURCE的0/1为门诊/住院；但[21样本](evidence/21-header-state-sample.json)
的100张发药日期已填写处方中，出现42张PRESC_SOURCE=1且FLAG=0，58张SOURCE=0且FLAG=1。
因此不能全院一律用FLAG=1判定全部已完成发药，FLAG=0的住院口径要另确认。
本次成功关联的8条是SOURCE=0/FLAG=1，不能据此批准住院或退药分支。
TRACE_STATUS样本为空，不能拿它作为“平台已报”或重复上报依据。

## 5. 下一AI的开发顺序与验收

1. **冻结本轮来源事实**：程序已有r2档案及PrescriptionTraceFacts；r1不改。
   r2为LIVE_READONLY_PROBED，不能改为LIVE_CONTRACT_VERIFIED或绕过EXT门禁。
   本次登录成功只解决EXT-01的连接/服务名子项，生产受限只读绑定仍未完成。
2. **批准来源契约r7**：沿r6新增本轮真实三表/键/字段长度、0/1/N码、明细复合键及状态反例；
   主单/逐码两层，保留原始ID，拒绝错药房/错日期/多明细匹配，不用DISTINCT掩盖重复。
   确认平台bill_code编码与既往已报编号，禁止从新编号生成后盲目历史重报。
3. **分别处理业务分支**：正常门诊、拆零、退药/全退/部分退、住院分别给出规则/反例。
   暂无批准的分支进入质量等待并可见，不能静默丢弃、装成正常发药或自动生成数量。
   处方明细数量不得重复赋给每个码；TRACES_SUM不是已确认的码数/码级quantity。
4. **实现有索引的变更发现**：初始发现可走M.DISPENSING_DATETIME索引，再按完整业务键取整单。
   只查该时间无法发现旧单补码/删码、原无码单后补、退药修改。
   与HIS厂商/DBA确认受控变更日志、可索引修改时间或其他可靠事件源；需要源端索引/DDL时另行授权，
   不在此次只读核查中创建。不能用全表GREATEST/MAX视图假装已解决839万行性能问题。
5. **交付两张视图与受限权限方案**：SQL须先可评审，再由院方另行执行；
   不伪造finalized、source_revision或expected_code_count。限制生产账号仅SELECT约定视图，
   安全注入<prod-host>；<relay-jump>的核验凭据不复制到本机或<prod-host>。
6. **测试和真实只读预览**：本地测试覆盖复合键前导零/分隔符/同处方号跨日期、错药房、重复码记录、
   一明细多码、一码多事件、拆零quantity缺依据、FLAG0/1分支、退药、旧单补码/删码、质量等待和重跑幂等。
   再在单独批准的窗口进行实际视图只读预览与对账，不将8条样本当全期验收。
7. **平台联调**：申请医疗机构retail上传与状态查询权限、企业ID/服务有效期、SDK签名向量、正式HTTPS转发链路。
   先核对权限和签名；真实上传仍需独立范围授权，按首批小样本→终态→码级对账签认推进。

以上未完成项仍保留在EXT-01—12，不因一次HIS登录成功而全部关闭。
本轮代码只增加来源事实与拒绝未验收来源的检查，没有发布运行配置或启用采集/发送。

## 6. 证据、验证与交接

详见[本轮交接](../../ai/sessions/HIS-LIVE-VERIFY-20261019.md)。
本轮前置备份 `.ai/backups/HIS-LIVE-VERIFY-20261019/` 保存17份修改前文件及hash，
不含运行库或凭据。程序r2事实位置：
[source_profiles.py](../../../implementation/plugins/sources/db_view/source_profiles.py)。
上一轮的5条准备SQL没有全部照跑：05因无时间索引停止，改走处方索引与业务键。
probe的源SQL只投影结构/聚合/脱敏ID形状；没有把真实码或处方号写入报告、AI输出。

本轮24条live探查全部成功（包括空样本与统计查询），25条SQL全部静态校验通过，
其中05未执行。局部测试32 passed/1 skipped、exit0；skip为既有openssl依赖用例，
没有运行全量回归。程序事实与活库证据逐项核对通过，r1历史字段值保持。
证据：[验证汇总](evidence/verification-summary.json)、[事实一致性](evidence/fact-consistency.json)。
`git diff --check` exit0；文档目录inventory199份、exit0；全仓文档检查exit1，
233处失效链接均在本轮未改的历史快照/证据文档，本轮编辑/新增文档失效链接0、来源hash异常0。
未为造绿而修改历史或放宽检查，见[文档检查](evidence/document-checks.json)。
