# RT-04 B2 提取报告（23 交易树形契约 revision r2-23txn）

- 生成时间：2026-11-14T09:11:56.540323+00:00
- 规范原件：山东省医疗保障信息平台定点医疗机构医保电子病历接口规范V2.0(1)(2).docx (sha256=14c03954e4ef1387…)
- 交易集合：**23 = 原19（清单表） + 12601~12604（正文标题）**， transactions/fields/samples 一一对应、无重复/孤儿
- fields：23 + _envelope/_output；samples：23（全部合成，根对象只含协议字段）
- UNRESOLVED_PATH 待核字段：22（逐笔见 unresolved.json；同名字段多路径不猜、规范有样例无的显式待核）

## 保留待核项（如实列出，不以删告警换零告警）
- 长度单位（字符/字节）规范未说明 → length_unit=UNCONFIRMED
- 签名算法（cainfo/signtype）规范未给 → SIGNATURE_UNCONFIRMED
- info 长度 4 与交易号 5 位矛盾 → max_len 置空待医保确认
- UNRESOLVED_PATH：22 项

## 错误
- 0 errors

## UNRESOLVED_PATH 明细（代码/原因）
- 11102 vali_flag NO_SAMPLE_PATH
- 11103 vali_flag NO_SAMPLE_PATH
- 11401 oprn_patn_type _code NO_SAMPLE_PATH
- 11402 vali_flag MULTI_SAMPLE_PATH
- 11405 oprn_oprt_tagt_ part_name NO_SAMPLE_PATH
- 11405 oprn_oprt_mtd_ dscr NO_SAMPLE_PATH
- 11406 vali_flag NO_SAMPLE_PATH
- 11407 vali_flag NO_SAMPLE_PATH
- 12101 etip_flag NO_SAMPLE_PATH
- 12101 rx_id NO_SAMPLE_PATH
- 12101 etip_org_code NO_SAMPLE_PATH
- 12101 etip_org_name NO_SAMPLE_PATH
- 12201 rpotc_no MULTI_SAMPLE_PATH
- 12201 appy_no MULTI_SAMPLE_PATH
- 12201 sapl_date MULTI_SAMPLE_PATH
- 12201 spcm_no MULTI_SAMPLE_PATH
- 12201 spcm_name MULTI_SAMPLE_PATH
- 12401 etip_flag NO_SAMPLE_PATH
- 12401 trt_servitem_code NO_SAMPLE_PATH
- 12401 rx_id NO_SAMPLE_PATH
- 12401 etip_org_code NO_SAMPLE_PATH
- 12401 etip_org_name NO_SAMPLE_PATH
