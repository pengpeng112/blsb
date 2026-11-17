# PII 保护边界（S07 盘点 · 本地收尾轮第4轮成文）

范围：患者身份证号等明文敏感值在系统内的**落库位置、读取方与防护现状**。
合成演示值与真实值的边界不变：当前全部为合成数据（B01—B09 全 OPEN，未接真实库）。

## 一、明文证件号存在的位置与读取方

| 位置 | 形态 | 写入方 | 读取方 | 防护现状 |
|---|---|---|---|---|
| `PatientReference.identity_snapshot` | **不存明文**：`outcome`（状态/类型码）、`pis_id_masked`、`his_patients[].id_no_masked` 均为掩码或引用 | `resolver.resolve_patient_and_encounter` | 病例详情 API（`GET /pathology/cases/{id}`） | 已做（写入即掩码，API 原样返回掩码形态） |
| 冻结 payload 的索引串 `payload["index"]["serialized"]` | 第2段为**完整证件号**（业务必需：医保正式索引） | `pipeline.build_index` → 冻结 | 投递链（send 节点→目的地）；`PathologyIndexRecord.serialized_value`（DB） | **读取路径防护（本轮 T10）**：`GET /packages/{id}/revisions/{rev}` 返回掩码形态（前4后4），`digest` 仍为冻结原文摘要供比对；病例详情只返回 `display_value`（掩码列），`serialized_value` 不出 API |
| 交换投影/文件路由 | 投影 payload 不含证件号（chk_info 只含检查号/病理号/索引对象引用）；路由只含 hash/物理键 | `h_deliver_publish` | collector（按租户+scope 授权） | 已做（投影字段清单制） |
| 回执 `ReceiptEvent.raw_payload` | 不含证件号（回执为结果语义） | 回执入口 | 对账/发布证据 API | 已做 |
| `AssociationCandidate.match_basis` | **不存明文**：仅 `{method, truncated}` | `resolver` | 关联复核链路 | 已做 |
| HIS/PIS 仓储内存比对（`_lookup_plain_id`/`p._id_no_plain`） | 进程内临时明文（仅比对用，不落库不外发） | resolver | 无外部读取方 | 受控来源读取（生产应经密钥服务，已在差异文档登记） |

## 二、匹配摘要 / 受控密文：已做与 deferred

**已做**：
1. 列表视图不回传完整证件号——本轮核查 T1—T8 新增端点与既有列表端点
   （assets/submissions/acquisitions/alerts/audit/quality-issues/operations/publications
   响应字段均无患者证件列）；回归断言：`tests/integration/test_local_gaps.py::
   test_T10_read_paths_do_not_expose_plain_id`。
2. 固定包读取端点（T3）索引串掩码（见上表），断言：`test_T3_*`。
3. 病例详情/列表只给 `display_value`（掩码列）与 `identity_snapshot`（掩码快照）。
4. 关联候选 `match_basis` 只记方法与截断标志，无源值。

**deferred（登记，不强行做）**：
- **对外索引明文的密文化**：正式索引串必须以明文提交医保（业务序列化要求），
  无法简单加密存储后仍满足上送；边界=传输与目标侧权限，属 B09 正式协议范围。
- **identity 快照的哈希摘要比较**：`AssociationCandidate` 匹配如改为摘要比较需要
  新增摘要列（schema 变更+迁移+历史回填），收益有限（现 match_basis 已无明文），
  登记为 LG 延后项（见 `docs/audit/2026-10-01/本地收尾-变更与核查.md`）。
- **受控密文存储（KMS/密钥服务）**：当前合成阶段 `secret_ref` 模型已就位，
  真实凭据注入路径属部署阶段（S07 治理项延续）。

## 三、错误/日志边界（延续 E01）

- 统一错误信封不回显连接串/令牌/患者值；trace 截断在 error handler 内。
- 下载/复核/人工决定写审计但不落患者明文（target 为资源引用）。
