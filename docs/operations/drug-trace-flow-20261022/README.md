# 追溯码推送流程说明

整理日期：2026-10-22。医院：山东省第二人民医院（山东省耳鼻喉医院、山东省耳鼻喉研究所）。
本文按已经运行、并真实成功上传一张单据的配置整理；后续已补充官方指南与HIS只读复核，未新增上传。

**已证实：1张单据、4个追溯码上传后，平台返回“处理成功、全部成功”，系统状态CONFIRMED。**
当前自动批量推送尚未开启，单笔测试额度1/1已用完。相关配置按用户要求保留。

密码及AppSecret已单独写入本机受限文件：
`F:\python\blyb\.ai\private\drug-trace-flow-20261022\推送流程及账号密码_仅本机.md`。
该文件包含本文和完整凭据表，便于医院内部留存；本文可用于沟通流程，不含密码或AppSecret。

## 1. 哪台机器做什么

| 用途 | 地址/入口 | 账号或身份 | 功能 |
|---|---|---|---|
| HIS Oracle源库 | `<his-host>:1521/his` | **BLYB_DRUGTRACE** | 专用只读账号，读取下述两个视图；服务名为his |
| 数据抽取、组包、签名、查询 | **<prod-host>**；SSH端口22 | SSH：root；实际worker：容器内UID10001 | 主动连接HIS抽数，在本地保存快照/核验/冻结，签名后调用DMZ |
| 系统管理网页 | `https://<prod-host>/ui/`，端口443 | 管理令牌，不使用SSH的root密码 | 管理取数、单据、提交、证据与结果；管理入口证书SAN仍需后续完善 |
| DMZ追溯前置机 | **<relay-net-host>**；SSH端口22，转发端口443 | SSH：root；服务用户：blyb-dmz；业务连接使用mTLS客户端证书 | 接收<prod-host>的签名表单，转发到官方HTTPS网关；不查询HIS、不保存AppSecret、不负责生成追溯码 |
| SSH运维跳板 | `<frontend-host>4:<ssh-port>` | root | 本机维护<prod-host>时使用；不经过这台机器上报业务数据 |
| 数据资产辅助平台 | `<frontend-host>3:22` | root＋已有SSH密钥 | sjzc元数据/受控只读核查辅助入口；日常生产抽取由<prod-host>直接连HIS，不依赖<relay-jump>中转 |
| 码上放心官方平台 | `https://eco.taobao.com/router/rest`，443 | AppKey＋AppSecret签名＋医院ref_ent_id | 接收零售上传、返回处理状态 |

**root是服务器SSH账号，BLYB_DRUGTRACE是HIS数据库账号；它们都不是平台AppKey，也不是网页管理员令牌。**
旧聊天中hisuer/hisuser等历史连接信息不作为当前程序配置，当前已实测的抽取账号以上表为准。

网络调用方向：

```text
<prod-host> 抽取worker ── Oracle TCP1521 ──→ <his-host> / 服务his
                                     读取两个PHARMACY视图

<prod-host> 签名worker ── HTTPS/mTLS TCP443 ──→ <relay-net-host>
                                              /alihealth/router/rest
                                                   │
                                                   └── HTTPS TCP443 ──→ eco.taobao.com/router/rest
```

返回结果沿原请求连接返回；HIS或平台不需要主动SSH登录<prod-host>才能完成上传。
目前实际使用443，不需要把8000/8001/8002暴露到宿主机。8443是此前申请的备用端口，本链路未用。

## 2. 调用哪些视图，数据怎么来的

实际读取：

| 视图 | 一行代表什么 | 关键字段 |
|---|---|---|
| **PHARMACY.V_DRUG_TRACE_BILL** | 一张处方/发药主单 | source_bill_id、source_bill_no、pharmacy_code、business_kind、bill_time、source_revision、finalized、is_void |
| **PHARMACY.V_DRUG_TRACE_CODE** | 一条来源扫码记录 | source_bill_id、source_line_id、source_code_record_id、trace_code、is_deleted、source_revision |

专用reader下面已有同名私有同义词，所以也可用`V_DRUG_TRACE_BILL`、`V_DRUG_TRACE_CODE`访问；
当前程序发布配置固定使用带PHARMACY owner的名称，避免同名歧义。
账号只有登录和这两个视图的SELECT权限，无角色、无转授权，不写HIS。

来源关系：

- 主单：`PHARMACY.DRUG_PRESC_MASTER M`。
- 扫码记录：`DRUG_USER.MED_TRACE_RECORD T`。
- 关联条件同时使用三项：

```sql
T.TRACE_BILLNO = TO_CHAR(M.PRESC_NO)
AND T.PRESC_DATE = M.PRESC_DATE
AND T.STORE_CODE = M.DISPENSARY
```

主单稳定来源标识：`PRESC_DATE（含时分秒）＋PRESC_NO＋DISPENSARY`。
程序登记身份还包含来源系统、药房和业务类型；不能只拿显示处方号判断同一张单。
追溯码当前来自**T.T_TRACE_CODE**；`T.RECORD_ID`是扫码记录标识，
`T.PRESC_DETAILID`是来源明细标识，不能直接当成整张主单号。
F/S/T原字段保留，但包装层级尚未独立验收，不据字段字母自行推断业务含义。

当前r10视图只把下列分支识别为正常门诊发药、已完成、未作废：

```sql
M.PRESC_SOURCE = 0
AND M.FLAG = 1
AND M.DISPENSING_DATETIME IS NOT NULL
AND NVL(M.REFUND_FLAG, 0) = 0
```

此时`business_kind=PATIENT_DISPENSE`、`finalized=1`、`is_void=0`。
码视图另有限制`T.IN_OUT_TYPE='OUT'`；程序对删除记录按is_deleted处理，不把它当有效上传码。
其他分支可能仍在视图中，但标为UNKNOWN/未确认，不能理解为所有出库、退药、住院都已具备自动上报条件。
数量、单位、码层级目前可为空；本次零售五个必填参数不依赖它们。

业务时间`bill_time`取**M.DISPENSING_DATETIME（发药时间）**，不是扫码CREATE_TIME，也不是上传时间。
主单`updated_at`目前也是发药时间，因此持续补码、删除、退费等晚到变化仍须另外完善发现机制。

用户已经执行的r10定义及实测对应关系见：
[视图SQL原稿](../../development/drug-trace-content-contract-20261022/sql/draft_normal_outpatient_views_r10.sql)、
[用户执行后实测](../../development/drug-trace-r10-readiness-20261022/README.md)。
SQL文件开头的DRAFT/未安装是编制时注释；后续用户已执行并实测生效，**本次无需再次执行建视图或建用户**。

## 3. 从抽取到平台确认，程序做哪些步骤

1. <prod-host>按指定时间窗口分页读取主单视图，Oracle兼容，窗口为`[开始时间,结束时间)`。
2. 按同一来源系统、药房、业务类型、source_bill_id读取该单全部扫码明细，不用MAX或DISTINCT代替整单读取。
3. 复读主单版本，检查是否中途变化，核验码重复、删除、状态和映射。异常进入等待/冲突，不静默当成成功。
4. 在<prod-host>应用数据库中保存来源快照、质量结果和稳定平台单号映射，再冻结本次要发送的内容。
5. 由worker使用AppSecret生成TOP签名；AppSecret只在<prod-host>签名进程使用，不交给DMZ。
6. 通过mTLS访问DMZ固定转发路径；DMZ将签名表单转发到官方HTTPS网关。
7. 上传HTTP成功或ACCEPTED表示受理，随后调用状态查询接口。
8. 平台返回处理成功且全部成功时，本地推进到CONFIRMED；结果未知先查询，已确认的同一提交不盲重发。

上报五个业务必填字段：

| 平台字段 | 程序取值 |
|---|---|
| bill_code | 本地稳定映射：零售前缀DTRT＋来源复合键的确定性摘要，持久保存；不会每次上传随机换号 |
| bill_time | 主单发药时间，格式yyyy-MM-dd HH:mm:ss |
| bill_type | 目前批准映射**321**，本次真实单已被平台处理成功 |
| ref_user_id | 已登记医院的**ref_ent_id**；不是root、HIS账号，也不是医院ent_id |
| trace_codes | 同一整单的有效原始追溯码，按接口格式逗号拼接，保留原始字符串 |

正式调用还带method、app_key、sign_method、sign、timestamp、v等TOP公共参数。
当前批次不发送患者姓名、身份证或电话；人工核查也不需要这些字段。
零售单码数当前达到3500即阻断，不截断/拆单；指南中的10000限制属于另一出入库接口，数值以官方当期文档为准。
目前未传可选的`ignore_part_success_flag`，平台允许部分解析；后续若要求任一码解析失败则整单拒绝，须新配置明确设为1并补对应表单映射，旧冻结包不改。

## 4. 前置机和平台的具体接口

| 项目 | 当前使用值 |
|---|---|
| <prod-host>→DMZ业务入口 | **POST https://<relay-net-host>/alihealth/router/rest** |
| DMZ→平台业务入口 | **POST https://eco.taobao.com/router/rest** |
| 零售上传method | **alibaba.alihealth.drugtrace.top.yljg.uploadretail** |
| 状态查询method | **alibaba.alihealth.drugtrace.top.yljg.query.billstatus** |
| 上传bill_type | 321（当前批准映射且已实测本笔成功） |
| 查询bill_type | A，独立查询选择器，不能把上传的321直接当查询选择器 |
| 当前DMZ允许方法 | 上述上传＋查询两个method |
| 库存上传 | uploadinoutbill仍关闭；最早沟通的UploadinoutbillRequest不是本次实际成功使用的方法 |
| 历史HTTP地址 | http://gw.api.taobao.com/router/rest为早期资料地址；当前真实链路使用上面的HTTPS地址 |

DMZ本地`GET /health`只表示前置服务健康，不等于平台业务成功。
状态枚举沿用已取证指南和程序映射，正式解释以官方当期文档为准；本次实际回执是3/51：

| 平台结果 | 本地理解 |
|---|---|
| result_type=0或1 | 处理中/上传成功但尚未最终完成，继续查 |
| result_type=3且sub_process_flag=51 | 处理成功、全部成功，CONFIRMED |
| result_type=3且sub_process_flag=52 | 部分成功，PARTIAL，不能当全部成功 |
| result_type=4 | 处理失败，REJECTED |

## 5. 以后怎样用人工上报记录核对视图

**可以核对。已有的原始Excel/CSV直接提供即可，不要求重做或改列名。**
另附[人工上报核对模板.xlsx](人工上报核对模板.xlsx)作为字段参考，模板不含真实患者、单号或追溯码。

最有帮助的是：**人工单据号、业务发生时间、药房编码或名称、完整追溯码**。
如有上报时间、平台单号、成功/失败结果、数量、退药/拆零原始标记，也一并保留；没有的不必编填。
每行一个追溯码最容易比较；如果原文件一格多个码，保留原文件，先确认分隔符再展开，保留原行号。
**追溯码和单号按文本保存**，不要转数值；Excel若已经把长码改成科学计数或截断，需要原文件恢复，不能靠末尾补零猜回。

核对会按四层进行：

| 层次 | 比较什么 | 可以判断什么 |
|---|---|---|
| 范围 | 人工文件覆盖哪几天/药房/业务，是否含失败、退药、住院；业务时间与上报时间分开 | 避免比较不同范围造成假漏单 |
| 单据 | 人工原始单据与来源复合键、药房、发药时间、明细标识 | 是否少主单、分组串单、日期错位 |
| 码明细 | 每单原始码、出现次数、来源记录、删除/退药/拆零状态 | 是否漏码、多码、错误去重或关联错误 |
| 平台结果 | 人工已报成功、本地冻结/attempt/receipt/CONFIRMED | 是否确实上报成功，是否可能跨系统重复 |

**不能只比较人工单据号和DTRT平台单号是否相等**：两边编号规则可能不同。
先利用原始单据、药房、时间和追溯码建立对应关系；如果人工单号实际来自PRESC_DETAILID，也要保留这种来源说明。
码比较保留出现次数，不先用DISTINCT把重复抹掉；同一码不同单或拆零记录须先解释业务，不能直接认定重复错误。

核查结果分类交付：

- 一致：对应主单、时间、药房及码明细一致。
- 人工有、视图无：检查原表是否有记录、三项关联是否命中、视图条件和时间范围是否排除。
- 视图有、人工无：可能是人工漏报、未覆盖范围、业务新增或上报失败，不能直接判视图多报。
- 单据有、码不一致：分清漏码、多码、重复记录和分组差异，追到原始记录/文件行号。
- 业务或时点差异：当前HIS可能已退费或改状态，而人工报的是历史时点；现视图不是历史时态快照，须分别记录。
- 无法判定：保留原始差异和待核事项，不用猜值填平。

交付会包含日期/药房汇总、匹配单据数、双方缺失数、码及次数差异、疑似重复、
具体人工文件/工作表/行号和来源字段依据，以及有证据支持的视图修订建议。
只要有一组范围一致、保留原码且能追溯单据的人工记录，就能验证该范围内视图是否准确；
不能用一张成功样例或“总数相同”宣称所有日期/业务全部准确。

**核查阶段只读、不补报、不重发，不因“本地没有记录”就重新上传人工已报的单。**
旧系统的上报单号可能不同，仅查询新DTRT单号查无记录不能排除旧系统已报同一码。
完整码留在本机受控材料中比对；聊天及可转发的汇总对码脱敏，不需要患者个人信息。

## 6. 维护位置与已成功样例

| 内容 | 位置 |
|---|---|
| <prod-host>当前应用目录 | `/opt/blyb/current/implementation`，本轮实际发行src-20261022T103000Z |
| 抽取/组包/发送代码 | 本仓implementation/plugins/sources/db_view/drug_trace.py；plugins/domains/drug_trace/；plugins/destinations/alihealth_drugtrace/ |
| <prod-host>运行配置 | `/etc/blyb/runtime.env`，root:root/600；勿将内容贴入聊天 |
| DMZ配置 | `/etc/blyb-dmz/forwarder.json` |
| DMZ服务 | `blyb-dmz-forwarder`；代码`/opt/blyb-dmz/current/forwarder.py` |
| 专用reader受控凭据 | `.ai/private/drug-trace-view-verify-20261022/his-reader.credential.clixml` |
| 平台受控凭据 | `.ai/private/alihealth-drugtrace-20261020/`，AppSecret为DPAPI加密保存 |

成功测试：submission `7ed95aaa-ee66-4510-b80d-630f6c9dc381`，1单4码、仅一次上传，平台3/51，本地CONFIRMED。
[真实推送回执及结果](../../development/drug-trace-one-bill-trial-20261022/README.md)。
平台3/51证实这张整单全部成功；本地4条明细状态仍PENDING是已登记的显示/核验边界，
尚未做独立码级对账，不能据此说平台处理失败，也不手工改明细冒充核验。


## 7. 本次官方文档与HIS复核结论

[详细核查报告及下一步完善任务](../../development/drug-trace-official-view-audit-20261022/README.md)。
2026-10-22重新获取语雀正文，规范标题后与既有快照相同；五必填、零售方法、321及3/51状态解释相符。
27条HIS只读SELECT成功，两个视图VALID、17/18列且与r10定义一致；30主单/100码样本，8张整单11条有效码未发现主从版本、时间或同单重复异常。

**现视图可用于正常完成门诊发药的批次上报，不能据样本确认全部历史/业务无漏。**
特别是100条码明细修改时间晚于发药20～251秒，而主单updated_at仍是发药时间；expected_code_count恒空。
持续自动推送前需完善等待齐套、码集合变化检测/回看与既往人工记录比对，防止取到尚未齐全的码。
包装关系、退药/住院/拆零及独立码级核验仍按实际证据推进；本轮没有重建视图、修改HIS或重发数据。
