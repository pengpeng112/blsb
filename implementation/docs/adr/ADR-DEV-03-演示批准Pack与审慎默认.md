# ADR-DEV-03：演示批准 Pack（合成成功路径）与 review-draft 默认并存

日期：2026-09-30　状态：已接受

## 背景
07 §9C 要求"另用明确批准的合成规则跑成功路径，不能把所有记录阻断当业务完成"。
同时 v0.4 首院 pack（设计样例）所有字典/时间/机构映射均未批准——按它跑会全量等待。

## 决策
种子发布两个 HospitalPack 修订：
- `first-hospital-review-draft`（r1，DRAFT）：与设计样例一致（无批准字典/桥接/权威时间）。
- `first-hospital-demo-approved`（r2，PUBLISHED，`production_enabled=false`）：
  含**合成批准**字典（id_type_ga/encounter_kind/report_org_insurance_code/pathology_category）、
  权威申请时间字段=集成申请表申请日期、齐套闭合信号批准、PIS住院号→VISIT桥接（演示）、
  PIS校验位→GA111回退（`SYNTHETIC_DEMO_APPROVED` 证据）。
- 字典按 pack 的 `approved_dictionaries` 引用装载：review-draft 不引用 → 全部等待
  （F09/F11/F12 用例切换该 pack 断言等待语义）。

## 后果
- 正：成功路径与等待路径都可自动验收；批准/未批准语义由配置而非代码分支表达。
- 负：演示 pack 不得用于生产（`production_enabled=false` + B01—B09 全 OPEN 时
  API 拒绝启用生产）。
