# 用户历史上报SQL复核与追加任务

2026-10-16追加；只做静态分析，**未执行HIS查询**。此SQL作为用户提供的历史来源证据保留，不等于已批准的视图定义。结论：可以帮助定位追溯来源，但必须修改后才能作为正式抽取契约；不能把聚合结果直接发送平台。

## 1. 原SQL（完整保留）

```sql
SELECT
  LISTAGG(PRESC_DETAILID, ',') WITHIN GROUP (ORDER BY PRESC_DETAILID) AS 单据号,
  CREATE_TIME as 单据时间,
  MAX(T_TRACE_CODE) AS 追溯码
FROM
  "MED_TRACE_RECORD"
WHERE
  IN_OUT_TYPE = 'OUT'
  AND CREATE_TIME BETWEEN TO_DATE('2026-07-23 00:00:00', 'yyyy-mm-dd hh24:mi:ss')
                      AND TO_DATE('2026-10-12 00:00:00', 'yyyy-mm-dd hh24:mi:ss')
  AND PRESC_DETAILID IS NOT NULL
GROUP BY
  RECORD_ID,
  CREATE_TIME;
```

## 2. 必须处理的问题

| 问题 | 影响 | 任务要求 |
|---|---|---|
| MAX(T_TRACE_CODE) | 如果组内有多个不同码，仅保留排序最大的一个；若只有一个非空码才不会在此处丢码，但目前没有这种唯一性证据 | 每条真实扫码记录独立保留，不聚合挑码；验证0/1/N码、空码、同单重复码与包装关系 |
| LISTAGG(PRESC_DETAILID) | 聚合的是处方明细ID，不是追溯码；一条记录可能仅得到一个明细ID，多条会拼出列表，这不等于稳定单据号；可能重复且受数据库版本/字符串长度约束 | 先确认主单业务键与明细关联；平台bill_code来自稳定映射，不能用拼接明细列表作主键 |
| GROUP BY RECORD_ID, CREATE_TIME | RECORD_ID若是每条记录主键，分组可能根本不汇成整张处方；如果不是唯一，又会把多码压成一行。最终SELECT还丢掉了RECORD_ID，无法追溯/去重 | 核实RECORD_ID粒度/约束；保留原主键、来源系统、机构、药房、主单/明细键与版本 |
| CREATE_TIME作为“单据时间” | 目前只能证明是该表记录创建时间，未证明是实际发药/出库完成时间；收费、扫码、入表、发药可能不是同一事件 | 字段映射需HIS/药学确认，实际完成时间单独提供；不能每次用当前时间或原记录创建时间冒充 |
| BETWEEN结束于10-12零点 | 包括恰好2026-10-12 00:00:00，不包括该日后续时间；相邻批次用BETWEEN还可能重复端点 | 使用半开区间`>= :begin_time AND < :end_time`；若意图包含10月12日全天，end_time为10月13日零点。是否补历史由院方确认 |
| 仅扫描CREATE_TIME | 后续补码、修改、删除/作废、退药关联变化可能不被发现 | 主单统一updated_at/事件序列；明细变化驱动父单发现，稳定keyset游标、重叠回看与对账 |
| IN_OUT_TYPE='OUT' | 只表达表中的出库方向；不能仅凭OUT判断是患者发药、供应商退货或其他出库，也不覆盖患者退药/采购入库 | KIND_TYPE/业务单据关系需核验；零售、库存独立profile，业务分类不明进入等待 |
| PRESC_DETAILID IS NOT NULL | 可偏向有处方明细关联的记录，但不是业务分类证明；可能遗漏采购/退药等没有该字段的业务 | 保留为待验证的零售候选条件，不套用到库存分支；用分类统计验证遗漏范围 |
| 未处理IS_DELETED/完成/撤销 | 可能采到作废、删除、未完成数据；直接加猜测的`IS_DELETED=0`也可能错误 | 确认实际值域与完成依据；作废/删除要在变更流中显式可见，不能只把历史行过滤消失 |
| 未保留组织/药房/药品/版本 | 同号跨药房串单，无法质量校验、定位差异或冻结来源版本 | 使用完整复合键和原始字段；不依赖一列单据号全院唯一 |
| 未明确T_TRACE_CODE层级 | 表有F/S/T_TRACE_CODE，名字不能证明T就是唯一应报层级；MAX也不能完成层级选择 | 核验字段字典、实际扫码单位、拆零和父子包装；禁止把三个码列盲目COALESCE/全发 |
| 表未限定owner | `"MED_TRACE_RECORD"`依赖当前schema/同义词；目录候选为DRUG_USER，不保证用户当前授权对象相同 | 与院方核实owner、同义词、readonly对象授权和schema白名单 |

举例（合成）：同一分组有码`000123`和`000124`，MAX只返回`000124`。即使把MAX改成LISTAGG追溯码，也仍未解决主单粒度、长串、重复、版本和整单一致性，因此正式视图应逐条返回码，由平台按已确认单据边界组装。

## 3. 只用于核对来源字段的草案

以下不是正式上报视图；仅在院方批准只读连接、时间范围和字段后，供执行者做小范围核查。owner使用目录候选，必须核实；时间参数必须绑定成数据库日期类型，禁止字符串拼接SQL。没有设置任意全表扫描或批量导出任务。

```sql
SELECT
    r.ORG_CODE,
    r.STORE_CODE,
    r.RECORD_ID,
    r.PRESC_DETAILID,
    r.PRESC_DATE,
    r.TRACE_BILLNO,
    r.DRUG_ID,
    r.T_TRACE_CODE,
    r.KIND_TYPE,
    r.IN_OUT_TYPE,
    r.CREATE_TIME,
    r.MODIFY_TIME,
    r.IS_DELETED
FROM DRUG_USER.MED_TRACE_RECORD r
WHERE r.IN_OUT_TYPE = :in_out_type
  AND r.CREATE_TIME >= :begin_time
  AND r.CREATE_TIME < :end_time
  AND r.PRESC_DETAILID IS NOT NULL
ORDER BY r.CREATE_TIME, r.RECORD_ID;
```

该草案故意保留删除/业务类型原字段供核对，不代表所有返回行可上报；实际核查应由只读连接器加受控行数/超时/分页。RECORD_ID能否唯一、日期精度、字段类型及该ORDER BY能否构成稳定游标均未实测。不能把这段CREATE_TIME筛选草案原封不动用于长期增量采集。

## 4. 追加工作包 DTR-W02-SQL / DTR-W04-SQL

1. 保存原SQL与SHA256，列为字段映射来源；只读核对前先解决HIS readonly登记及服务名冲突。
2. 获取MED_TRACE_RECORD实际DDL/唯一约束/索引、字段值域、主单与PRESC_DETAILID关系；核实TRACE_BILLNO能否作主单键及其机构/药房唯一范围，不能直接认定可用。
3. 用授权的合成/脱敏小样本证明一单多明细/一码多关联/多码/跨药房重号、退药、库存与患者场景；不将患者原始值写进通用交接包。
4. 建立“患者发退药”和“供应商入出库”分类规则与未分类等待，记录业务签认；原OUT+PRESC_DETAILID条件仅为零售候选。
5. 交付view-contract-r5主单与明细视图草案、逐字段来源映射、正确半开时间和更新游标；院方建视图/授权后才能实测，开发AI不直接在HIS执行DDL。
6. 添加DTR-S01—S08验收样例：证明不会因MAX/聚合丢码、不会混用处方明细ID与单据号，变更/作废可发现，并说明未能验证的来源假设。

此追加项已纳入[开发计划](DEVELOPMENT_PLAN.md)、[验收矩阵](ACCEPTANCE.md)与[下一AI提示词](NEXT_AI_PROMPT.md)。
