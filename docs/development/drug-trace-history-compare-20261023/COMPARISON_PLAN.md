# 历史上报与现视图比对计划

2026-10-23；用户要求核对指定25份历史上报Excel与现视图是否一致。

## 已完成的离线检查

原件仅只读，489613行，205334个历史单据号，473614个不同追溯码；日期为2026-07-23 00:02:14至2026-10-11 23:52:04。所有码均为20位ASCII数字文本，未发现数值精度损坏、空必填或首尾空白。

无完全相同的“单号+时间+码”重复行；同“单号+码”多出31行，14个单号有多个时间，3562个码出现于多个单号。此为待解释现象，不先认定错报；需结合拆零/再次发药/不同处方明细与历史取数分组核对。

原历史SQL以PRESC_DETAILID为单号、CREATE_TIME为单据时间；当前视图分别提供source_line_id、source_bill_id及bill_time（发药时间）。两套单号/时间不能直接要求相等。真实匹配先检查历史单号能否对应source_line_id，再核对追溯码和出现次数、归属主单、删除/业务状态。

## 待执行的真实只读范围

- 目标：<his-host>:1521/his，已登记药品追溯专用只读reader；仅PHARMACY.V_DRUG_TRACE_BILL与PHARMACY.V_DRUG_TRACE_CODE及必要对象结构元数据。
- 通道：sjzc规定的<relay-jump>/data-asset-api/OracleConnector.execute_readonly；复用已有安全注入方式，不读取平台托管凭据、不修改登记、视图、数据或上报状态。
- 先做限定旧单号的匹配探针及执行耗时核对，确认键和时间语义；再按历史对应日期逐日、必要时按小时串行分批读取，单次最多10000行、30秒，累计初始预算10分钟。超时/截断/身份授权不符则停止相关批次，保留未核对覆盖，不将截断当全量。
- 时间比较：历史时间是扫码创建，不把发药时间机械当历史上传时间。根据样本实证选择边界；视图范围外仅标待核，不据查询无行就断言HIS无记录。
- 结果在受控进程比较；患者字段不选。原码/原单号不上聊天或共享文档，只输出统计和脱敏差异；需完整差异时保存仓库外受限目录，文件按文本保留工号/码。
- 完整核对保留multiset出现次数，不用DISTINCT抹掉重复；区分历史有/视图无、视图有/历史无、分组变化、时间差异、状态变化、无法确定。
- 不查询真实平台、不补报、不重发、不更改既有成功单或额度。

## 只读SQL模板（核查版，实际参数不在共享文件记录）

```sql
SELECT source_system,source_bill_id,source_line_id,source_code_record_id,
       pharmacy_code,business_kind,trace_code,is_deleted,bill_time,updated_at
FROM PHARMACY.V_DRUG_TRACE_CODE
-- 历史单号先对应处方明细标识；不能直接等同主单复合键。
WHERE source_line_id IN (:legacy_id_0,:legacy_id_1,:legacy_id_2)
  AND ROWNUM<=101
```

```sql
SELECT source_system,source_bill_id,source_bill_no,pharmacy_code,
       business_kind,finalized,is_void,bill_time,source_revision
FROM PHARMACY.V_DRUG_TRACE_BILL
-- 仅查前一步实际命中主单，复核药房/时间/业务状态与完整归属。
WHERE source_bill_id=:source_bill_id
  AND bill_time>=:begin_time AND bill_time<:end_time
  AND ROWNUM<=3
```

当前为白天。数据库共用规则要求此类HIS实时/批量核查具备该时段批准；本次用户已授权比对目标与对象，但未明确白天执行窗口。已完成离线输入与SQL计划，等待仅此窗口确认；不是再次索要凭据或询问是否开始分析。

补充离线证据：多数历史单号有可解析日期前缀，但5077行不符合该日期模式；可解析者最早至2026-03-08，与文件时间最多相差196天。前缀含义尚未活库证实，不能据此前缀直接认定处方时间；同时不能按文件扫码日期限制发药视图后就宣布未命中。首轮明细号探针改为仅限定3个完整历史单号与101行上限，先看真实主单时间，再确定时间分批范围。需要扩大查询对象或预算时单独明确，不把十分钟预算当成必须完成全量的承诺。
