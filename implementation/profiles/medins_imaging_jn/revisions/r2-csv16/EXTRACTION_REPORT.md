# RT-06 F02 提取报告（medins_imaging_jn r2-csv16）

- 生成时间：2026-11-15T14:40:46.810275+00:00
- 来源原件：PACS_V2_字段清单_原样提取.csv (sha256=dfd4c68ea3c4729a…)
- 数据集 **16**、字段 **288**（288 全行校验通过）
- 约束覆盖：type 288/288、length 285/288、requirement 214/288
- SPEC_FIELD_CONSTRAINT_PENDING 合计 **77**（长度/必填缺失，逐字段见 fields/*.json notes）
- 条件必填（放射/医保结算等原文保留）13 条
- status=EXTRACTED_NOT_APPROVED 全量保留——不升级为正式批准
- asset_info 为内部投影概念，不在 16 数据集清单（不新增第17个官方数据集）
- 数据集代码为规范大写原样（APPLY_INFO/CHK_INFO/…）；中文名归一展示但原文保留在 label_raw
