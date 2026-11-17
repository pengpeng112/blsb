# 上报平台契约 Profile（LT-07 起用）

各上报平台的接口契约派生物目录（由 `tools/spec_extract` 从规范原件生成；
运行时只读）。

```
profiles/<target_code>/
  profile.json        元信息：spec_source[{path,sha256}]（原件可追溯）、
                      extracted_at、extractor_version、status(DRAFT|REVIEWED)、
                      msgid_rule/signature/length_unit 确认状态
  transactions.json   交易清单 [{code,name,direction,has_fields,has_sample_shape,...}]
  fields/<code>.json  字段契约 {nodes:[{code,name,type,max_len,dict,required,
                        sensitive,source(spec|sample_only),notes}]}，
                      _envelope.json 为公共报文头
  dicts/<code>.json   规范中明确给出的代码表
  samples/<code>.json 合成样例（_synthetic 标记；全部合成值）
  EXTRACTION_REPORT.md 覆盖率/规范要点/人工待核项
```

约定：
- **派生物**：原件更新 → 重新生成（新目录或升 spec_version），旧的不原地改；
- **不含真实值**：样例结构只取 shape；合成值白名单见提取器（证件号
  `000000199001010000`、"测试患者"、`TEST-` 前缀等）；
- **未知即 UNCONFIRMED**：签名/长度单位/矛盾点显式标注，不猜。
