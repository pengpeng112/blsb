# FB-A01—29 验收矩阵回填（DTR-FIRSTBILL-EXEC-20261021）

状态仅 PASS/FAIL/BLOCKED/NOT_RUN；证据路径相对本目录。真实执行与代码已写
严格区分；"代码已写"不填现场 PASS。

| 编号 | 状态 | 证据/说明 |
|---|---|---|
| FB-A01 接手/登记/基线留存，已有改动未丢失 | **PASS** | session 20261021T155722Z 登记（ZCode/GLM-5.3）；[baseline-*](evidence/)、[git-baseline.txt](evidence/git-baseline.txt)（HEAD 0ca47c6，50M+49?? 全程保留，无 reset/clean） |
| FB-A02 两机发行/6容器/迁移/端口复核 | **PASS** | [baseline-target](evidence/baseline-target.json)（dpl-…111000Z/6healthy/001—011/443唯一发布）、[baseline-dmz](evidence/baseline-dmz.json)；升级后 [postcheck](evidence/fb05-postcheck-20261021T165521Z.json) 复核 fb-…164800Z |
| FB-A03 CLI/API/UI 就绪同源；DRAFT/Mock 不显示可发 | **PASS** | 共享 [readiness.py](../../../implementation/plugins/domains/drug_trace/readiness.py)；test_drug_trace_readiness_fb.py 同源断言+DRAFT/mock→NOT_READY；现场实测见 [config-retry](evidence/fb05-config-retry-20261021T165751Z.json)（发布前 NOT_READY 含 is_mock=True 细节） |
| FB-A04 租户/目的地/配置精确绑定；撤销/过期/换代立即失效 | **PASS** | resolve_destinations 显式/歧义拒绝（test_fb01_multi_destination_ambiguous_not_first、cross_tenant）；撤销/过期翻转（test_fb01_evidence_changes_flip_layers）；世代切换/幂等（test_fb01_publish_generations_and_idempotency）+既有 FIX3 配置闭包测试 |
| FB-A05 check/dry-run 零采集/零POST/零证据批准副作用 | **PASS** | test_fb01_readiness_call_has_no_side_effects、test_fb01_cli_publish_tool_dry_run_zero_write、api dry_run 断言零写入 |
| FB-A06 官方签名向量/SDK 或获准替代证据完整；空值/中文/session 通过 | **PASS** | [SIGNATURE-QUALIFICATION.md](SIGNATURE-QUALIFICATION.md)：C# 参考 4 向量（空值/中文/前导零）+官方 PyPI SDK 源码移植 4 向量（含 session/中文/前导零）+平台实际受理；[对照快照](evidence/fb02-sign-sdk-comparison.json)。**声明为源码提取+独立向量+平台受理的替代验收，非 SDK 二进制对照** |
| FB-A07 无签名资格真实client拒绝；获验修订可审查 | **PASS** | 资格随批准修订关闭（protocol.py 常量处证据链注释+本目录证据，随发行评审交付）；永久安全属性：mock 标记密钥禁上真实 transport（test_b07/…fb02 回归保留；G3-11 语义保留） |
| FB-A08 上传权限/绑定/有效期/授权模式分别确认 | **BLOCKED** | 平台侧输入；查询+目录 SUCCESS 不推定上传权限；见 [EXTERNAL-RECEIPT.md](EXTERNAL-RECEIPT.md) |
| FB-A09 字典/枚举/日期/分页有官方当期依据 | **PASS** | 官方 2026.01 指南快照（[reference](reference/official-guide-202601.md)）：321=使用出库（字典已按此批准）、0/1/3/4+51/52 落码+测试；查询选择器 A/AI/AO（官方API页快照）；**321 vs 323 平台消歧仍开放**（差异表）；日期语义保守双窗口 |
| FB-A10 HIS 登记核实、无绕过策略 | **BLOCKED**（核实部分 PASS） | 登记核实：write_policy=medical_dict_push/service=his（10-19 证据沿用+本轮元数据复核）→ live 按策略阻断（本轮 HIS 查询 0）；**受限只读绑定未建立**（DBA） |
| FB-A11 两视图授权/核验；DDL 由 DBA 落档 | **BLOCKED** | [DBA-PACKAGE.md](DBA-PACKAGE.md) 交付审阅版+回退；视图 0 证据 |
| FB-A12 完整单据采集/全码/数量/拆零与规则一致 | **BLOCKED** | 无生产采集；合成路径回归覆盖（405 全量） |
| FB-A13 首尾 revision/重取/幂等/药房隔离 | **BLOCKED**（真实）/ PASS（合成） | 既有 G3 逐字段 keyset/页级原子测试维持全绿；真实链待采集 |
| FB-A14 无可靠变更源时持续增量不误启用 | **PASS** | AUTO_PRODUCTION 关闭（设计不变）；批次/增量分离测试在位；本轮未启用任何增量 |
| FB-A15 两机校时/偏差受控、真实时间源依据 | **BLOCKED**（偏差实测 PASS） | <prod-host> vs 平台权威时间差≤10s（[diagnose](evidence/fb05-diagnose-20261021T170259Z.json)）；DMZ 慢 32.4 分；两机 chronyd 均无可用源（[fb04](evidence/fb04-clock-certs.json)）——NTP 未同步，院内源=网络科输入 |
| FB-A16 正式 CA/SAN/EKU/mTLS/重启保持 | **BLOCKED**（TEST-ONLY 链下 mTLS 项 PASS） | 现网 pki-r3 测试链：严格 mTLS/无证书拒绝/重启保持=上棒实测沿用；正式链=院方输入（轮换流程在 [OPERATIONS.md](OPERATIONS.md) §四） |
| FB-A17 真实 Profile/ConfigRevision/字典发布且无 Secret 明文 | **BLOCKED**（目的地/字典部分 PASS） | 目的地 ConfigRevision PUBLISHED rev1（is_mock=False）+两官方字典已批准（[api-setup](evidence/fb05-api-setup-20261021T165647Z.json)）；SecretRef=env: 无明文（validate fail-closed 测试）；SourceProfile(HIS)=BLOCKED |
| FB-A18 四能力证据按必需集合分别登记并校验 | **PASS**（query/reconcile）/ BLOCKED（collect/send） | query 5d02297c APPROVED、reconcile aa13c340 APPROVED（[reconcile-evidence](evidence/fb05-reconcile-evidence-20261021T170701Z.json)，同 EXT-09 依据）；collect/send 未登记（前置未齐，不假填） |
| FB-A19 真实 worker→mTLS→DMZ→官方签名只读查询通过 | **PASS** | [diagnose](evidence/fb05-diagnose-20261021T170259Z.json)：真实配置 rev1+真实签名，found=true/total_num=0/unknown=false；过程 403(NAT) 定位与修复全程留痕（[403现场](evidence/fb05-diagnose-20261021T165955Z.json)+日志） |
| FB-A20 未报核对+明确 scope+max_bills=1 | **BLOCKED** | 院方/原厂商未答复；无真实单据 |
| FB-A21 DMZ 只加 retail、可回受限态 | **NOT_RUN** | 未达上传阶段（设计如此）；白名单仍仅 query；回退=forwarder.json.pre-fb05（已演练 verify） |
| FB-A22 正式应用上传一次、幂等/配额无重复 | **NOT_RUN** | 实际上传 0 张 |
| FB-A23 查询最终状态；未知不盲重发 | **NOT_RUN**（无单可查终态） | 查询能力本身已实测（FB-A19）；UNKNOWN-先查语义由回归保障 |
| FB-A24 对账演练覆盖范围明确 | **NOT_RUN**（真实范围） | 码级核对接口（listbillprocesspartsuccess）已记差异表；演练机制合成回归在位 |
| FB-A25 停发后发送拒绝、查询可继续、额度未扩大 | **PASS** | send 证据未登记→fail-closed（测试+现场 readiness SEND_SCOPE=NOT_READY）；查询/对账独立可用（EXT-09 已批）；send quota=0（无台账） |
| FB-A26 局部/全量/前端构建实际证据 | **PASS** | **全量 405 passed/7 skipped/0 failed**（[fb-final-regression.log](../../../implementation/var/fb-final-regression.log)+JUnit XML）；npm build 通过；7 skipped 均环境依赖项（见测试执行记录） |
| FB-A27 新发行哈希/备份校验/恢复演练/回退资料/旧功能健康 | **PASS** | fb-…164800Z（150文件 SHA256SUMS+manifest）；备份 20261021T165233Z 哈希过；[restore --validate-only 通过](evidence/fb05-restore-validate.json)（既有 line103 告警如实记录）；回退资料在 OPERATIONS；6容器 healthy/病理域回归绿 |
| FB-A28 凭据/私钥/患者数据未进 Git/镜像/日志/文档 | **PASS** | [secret-scan](evidence/secret-scan.json)：真实凭据 0 命中（32hex 命中均为公开向量摘要，已裁定）；.ai gitignored；回执全白名单字段；平台原始响应不落共享 |
| FB-A29 四类文档+共享交接+session 收尾 | **PASS** | 实施状态矩阵/接口与设计差异/测试执行记录/外部阻断清单+API矩阵+STATE/BACKLOG/HANDOFF/session 均已更新（见下） |

汇总：**PASS 17 / BLOCKED 8 / NOT_RUN 4 / FAIL 0**。BLOCKED 全部为外部输入
（DBA 2、网络/证书 2、平台 1、院方业务 3），系统侧无再开发项；
NOT_RUN 均为未达上传阶段的后续步骤。
