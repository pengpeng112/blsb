# 追溯上报内容确定单（2026-10-22）

**五个核心必填字段都已找到来源；门诊正常发药的候选筛选规则已写清楚。**
本轮不再把药名、码级数量、单位、患者证件或供应商资料列为本次零售接口的必填缺项。
已交付机器可读契约和两个视图的r10修订草案，活库仅做SELECT，未改视图、未启用上报。

## 1. 本次究竟上传什么

需求确认单明确要求 **`alibaba.alihealth.drugtrace.top.yljg.uploadretail`**（医疗机构零售上传）。
用户早期给的`UploadinoutbillRequest`用于另一条出入库接口，不能据此额外要求患者发药提供采购收发企业字段。
依据：[原始需求单](<../../../数据上报/1、码上放心平台/(260721)需求确认单-码上放心平台HIS对接出库销售上传(1).docx>)、
[已取得官方请求参数快照](../platform-live-test-20261020/evidence/official-uploadretail.txt)、
[2026.01官方指南快照](../drug-trace-firstbill-exec-20261021/reference/official-guide-202601.md)。

| 平台必填参数 | 本次确定的来源和处理 | 当前边界 |
|---|---|---|
| `bill_code` 单据编号 | 沿用程序`ensure_bill_code`的持久映射；基于来源系统+药房+业务类型+完整处方键生成稳定编号 | 不直接用LISTAGG的明细号；旧厂商单号规则和既往已报清单尚未取得，不能历史全量重报 |
| `bill_time` 单据时间 | `PHARMACY.DRUG_PRESC_MASTER.DISPENSING_DATETIME`；格式yyyy-MM-dd HH:mm:ss，按院内GMT+8业务墙钟 | 追溯表CREATE_TIME是记录创建时间，不能宣称与发药时间等价 |
| `bill_type` 单据类型 | 本期使用出库拟沿用现有已批准字典`321`，依据2026.01指南 | API说明文字同时出现323、示例321的冲突仍未消除，以官方最终口径为准；不能轮流试两个值 |
| `ref_user_id` 医院标识 | **用户已提供并保存的医院ref_ent_id**，由本院药房代码映射取得 | 不是医院名称、HIS的ORG_CODE、ent_id或供应商ID；本机资料齐全，不再向用户索要 |
| `trace_codes` 追溯码列表 | `DRUG_USER.MED_TRACE_RECORD.T_TRACE_CODE`，整张处方全部有效码按字符串原值取出，程序拼逗号 | 一条码一行抽取；不转数值、不用MAX取一个码，不用DISTINCT掩盖重复，不混报父子包装码 |

TOP公共参数method/app_key/timestamp/sign/sign_method/format/v由现有适配器生成，
AppSecret仅用于签名，不作为业务字段发送。已有AppKey/AppSecret/ref_ent_id无需重新提供。
不把`query.billstatus`的A选择器当成上传bill_type数字值。

**本次最小零售上传不需要先解决的字段：**药名、规格、批号、码级quantity、unit、患者姓名/证件/电话。
physic_type/from_user_id/提交者/购买人字段在官方快照列为可选，最小请求省略。
`quantity/unit/code_level/expected_code_count`可供内部核验，并非这条接口的五个必填表单字段。
拆零的来源含义和重复扫码流程仍须分支核实，不能因数量可选就擅自复制码或填数量1。

## 2. 您原SQL能用到哪一步

原件保存在[original_user.sql](sql/original_user.sql)，SHA记录在解析证据中；本轮没有执行原始多月范围SQL。
已经证实`RECORD_ID`是启用主键，按它与CREATE_TIME分组每组只有一条：

- `MAX(T_TRACE_CODE)`在这张原始单表、现主键成立时**不会必然吞掉其他记录的码**；不能继续笼统判它必丢码。
- `LISTAGG(PRESC_DETAILID, ',')`通常退化为该记录的原始PRESC_DETAILID；它是**处方明细号码**，不是处方主单号。
- 该SQL得到的是“扫码记录级结果”，没有明确一张完整发药处方的所有码、退药/退费和删除处理。
- BETWEEN的上界是2026-10-12 **00:00:00**，并未覆盖10月12日全天；后续批次建议半开区间，包含12日全天时上界才是13日00:00。
- 改用发药时间筛选属于业务时间口径修订，不是原CREATE_TIME条件的机械等价改写。

[记录级化简版](sql/original_equivalent.sql)保留原时间字段与BETWEEN条件、增加记录主键，仅用于解释原逻辑，不作为真实上传入口。

## 3. 单据与码怎样对应

内部处方键仍采用：**完整秒级PRESC_DATE + PRESC_NO + DISPENSARY**；来源ID保留字符串。
不得只用每日可重复的处方号，不对PRESC_DATE做TRUNC，不把跨药房记录合在一起。

```sql
FROM DRUG_USER.MED_TRACE_RECORD T
JOIN PHARMACY.DRUG_PRESC_MASTER M
  ON T.TRACE_BILLNO = TO_CHAR(M.PRESC_NO)
 AND T.PRESC_DATE = M.PRESC_DATE
 AND T.STORE_CODE = M.DISPENSARY
```

本关系有10-19和10-22限定样本证据。平台当前正式关系/配方查询未命中，
不能写成全量验证或正式审核发布；完整条件已保存在解析补充中。
PRESC_DETAILID在既有样本里不能可靠等同于旧PHARMACY.DRUG_PRESC_DETAIL.ITEM_NO或ORDER_ITEM_COST_ID。
**零售接口只要码列表，因此不必为了凑quantity强行接上不成立的旧处方明细关系。**

## 4. 正常发药规则已经收窄到什么程度

本轮通过受控sjzc再次只读读取现库字段注释，使用`DUMP(COMMENTS,16)`取原字节、按GBK严格解码，
避免把乱码当“字段无说明”。[his-content-r1.json](evidence/his-content-r1.json)中有完整字节证据：

| 来源字段 | 现库明确注释 | 本次使用边界 |
|---|---|---|
| M.PRESC_SOURCE | 0门诊、1住院 | 首批候选只覆盖门诊0 |
| M.FLAG | 1正常发药、2全退、3部分退 | FLAG=0的住院流程不套门诊判断 |
| M.REFUND_FLAG | 1已退费、0/null未退费 | 首批候选排除已退费及未知值 |
| M.DISPENSING_DATETIME | 发药日期 | 必须非空；主表可在收费时生成，仅有主表行不等于完成 |
| T.IN_OUT_TYPE | IN入库、OUT出库 | 原SQL为OUT；不是见OUT就认定患者正常发药 |
| T.IS_DELETED | 0未删除、1删除 | 码视图保留删除原值，程序排除明确删除；未知值不默许 |
| T.SPLIT_FLAG | 0未拆零、1拆零 | 首张建议选整张均明确不拆零的单据，不能只删掉拆零行凑一张单 |
| T.TRACES_SUM | 三级追溯码数量 | 不能直接当已验证的码级quantity或expected_code_count |
| M.TRACE_STATUS | 无注释，样本为空 | 不能把它当“平台未报”标识 |

据此形成的**门诊正常发药候选规则**是：

```sql
M.PRESC_SOURCE = 0
AND M.FLAG = 1
AND M.DISPENSING_DATETIME IS NOT NULL
AND NVL(M.REFUND_FLAG, 0) = 0
```

在r10草案中，满足该规则才给`business_kind=PATIENT_DISPENSE`、`finalized=1`、`is_void=0`；
其他情况继续UNKNOWN/NULL，不把退药都当作void，不把住院FLAG=0直接当已完成。
**这是有现库定义和样本支持的待院方确认规则，尚未人工批准或启用。**
源注释说明值的含义，不自动证明院内实际操作中“发药时间写入”与最终交付/扫码齐套的先后时点。
平台相关confirmed值域仍0条，未自行写入或确认值域/业务字典。

本轮当天100个有发药时间的头样本为门诊0/FLAG1共41张、住院1/FLAG0共59张，REFUND_FLAG/TRACE_STATUS都为空。
20个父键限定的子表样本共20行，均门诊0/FLAG1、OUT、IS_DELETED=0、20位数字码；
其中13行SPLIT_FLAG=0、7行=1，TRACES_SUM均未大于1。它不是全院/全期分布或完成确认。

## 5. 修订材料已经准备好

- [upload-content-contract-r10.json](upload-content-contract-r10.json)：必填字段、来源、键、候选状态、字典差异、首张范围和部署余项，机器可读；`runtime_approved=false`。
- [draft_normal_outpatient_views_r10.sql](sql/draft_normal_outpatient_views_r10.sql)：仅替换PHARMACY下两个已有视图的草案，不新建用户、不扩大账号权限。
- [rollback_to_observed_views.sql](sql/rollback_to_observed_views.sql)：此前实测视图体生成的回退参考。
- r10两视图的SELECT部分已通过**活库WHERE 1=0解析**，见[his-content-parse-r1.json](evidence/his-content-parse-r1.json)；
  **没有在HIS执行CREATE/REPLACE，也没有把语法通过当业务验收**。

r10保留17/18列和现有字段名，主/码两视图使用相同分类与revision表达式；revision补入退费原值和R10标记。
原始`source_bill_kind_raw`继续保留`PRESC_SOURCE=0;FLAG=1`，不为迁就字典而丢掉来源事实。
`updated_at`仍按发药时间，仅用于受控批次；它无法发现全部旧单补码/删码/退药，连续自动生产仍不得开启。

**实际发现的新配置差异：**payload优先拿source_bill_kind_raw查字典，现已批准字典源code却只有PATIENT_DISPENSE。
本轮已从<prod-host>实际只读查询确认，见[runtime-dictionaries-r2.json](evidence/runtime-dictionaries-r2.json)；
首轮SSH255无结构结果保留在r1，未把它算成功，第二轮SSH0/worker0成功。
因此下一AI必须新建字典revision，保留已有项并补：

| 命名空间 | 新增来源code | target |
|---|---|---|
| drugtrace.bill_type | PRESC_SOURCE=0;FLAG=1 | 321（延用已有拟定值，官方最终口径为准） |
| drugtrace.query_bill_type | PRESC_SOURCE=0;FLAG=1 | A |
| drugtrace.ref_ent_id | 首张实际药房代码 | 已登记本院ref_ent_id |

这些只是待发布的映射草案；不得以AI名义伪造approved_by或覆盖旧批准revision。
同样不能只把business_kind改成正常，就认为整个上报请求已能生成。

## 6. 下一AI最小接续任务

1. 使用本机已有BLYB_DRUGTRACE凭据及PHARMACY两视图，不重建账号，不重索平台密钥。
2. 院方确认本页门诊正常/完成候选口径及一张未报测试单后，由DBA执行r10视图修订并只读复核。
   首张整单选择所有有效行均明确未删除/未拆零、码非空、无重复且齐套的单据；未知状态等待。
3. 正常创建新的来源契约/配置和三类字典revision，绑定<prod-host> worker专用账号，发布/启用来源及目的地。
   不把原平台源medical_dict_push改名冒充生产只读资格。
4. 将已修复并通过110项回归的Oracle首屏改动构建新发行，按已有备份/升级流程部署；现网仍旧版。
5. 限定一张采集，质量VALID、冻结预览，检查五必填参数与全部码；业务单号沿用程序持久映射。
6. 落实既有真实发送证据/平台权限、必要DMZ uploadretail方法及max_bills=1范围，正常系统流程发送一次，
   查最终处理状态并核对全部码；未知先查，不能自动改号重发。

**仍无法从这些表结构/原SQL直接确定：**住院FLAG=0的完成时点、退药/部分退与拆零操作流程、
原厂商历史单号及哪些已报、首张确认未报的单据。不要反复让用户补已齐的平台凭证或非必填数量资料。
本轮当前成果足以把外部问题缩成具体业务确认，而不是再次笼统说“缺整个HIS口径”。

## 7. 来源时效与执行边界

本轮先查<relay-jump>平台：相关字段42条，但未填业务注释；追溯查询资产0、目标正式关系0/配方0/值域0。
因此回退同系统源端资产，按已知缺口查两份设计文档，并通过本次延续授权的受限SELECT补现库注释与样本。
使用查询资产流程只进行了检索/本地草案/校验，未提交平台查询或生成run，不伪造执行refs。

- `数据资产_HIS源端资产包`的catalog生成2026-07-03，无manifest.json；不据此承诺它是完整实时结构。
- 天健《HIS系统数据库表结构》是2006年版历史结构依据；FLAG2的旧概括不能覆盖现库全退/部分退注释。
- 药房药库设计文档列MED_TRACE_RECORD 26列、T_TRACE_CODE长度32，当前源端已有30列/长度255；
  结构以当前平台/近期活库证据为准。未修改用户的源文档、CSV或正式关系资产。
- 原关系解析器CLI不兼容旧HIS关系CSV列名，首次KeyError；后按平台与旧CSV均无目标关系的真实结果，
  使用解析器函数加人工补全三字段JOIN条件，保存intake-r2，不伪称发现正式关系。
- 本轮追加HIS 5条SELECT（注释、两条受限样本、两条零行解析）全部成功；源写0/平台写0/上传0。
- 当期网页工具未能重新取到官方API正文/语雀内容，参数判断依据已保存官方正文快照；未声称在线已消除321/323冲突。

账号凭据及先前视图实测见[账号/视图报告](../drug-trace-view-verify-20261022/README.md)，
已修复的Oracle分页及部署边界见[分页修复报告](../drug-trace-oracle-first-page-20261022/README.md)。
