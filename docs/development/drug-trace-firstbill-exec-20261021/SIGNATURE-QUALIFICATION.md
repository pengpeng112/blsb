# FB-02 签名资格与官方契约对照报告（DTR-FIRSTBILL-EXEC-20261021）

结论：**EXT-07 签名一致性证据链已闭合，SIGNATURE_VERIFIED 按批准修订置 True
（随新发行生效）**；官方当期契约口径（321/查询枚举/部分成功）已取证并落码。
本文与 evidence/ 下文件构成可审查依据；不含任何密钥明文。

## 一、签名算法对照（三源一致）

| 对照源 | 版本/位置 | 哈希/取证 | 结果 |
|---|---|---|---|
| 官方 C# 签名参考 | developer.alibaba.com articleId=107727（上棒 2026-10-20 提取 OfficialTopReference.cs） | 4 公开向量（含官方文档示例 AF4396FC…） | 4/4 一致（test_top_official_reference.py） |
| 官方 PyPI TOP SDK | Top-1.0.0（`top/api/base.py::sign`） | zip sha256 `9ca70d8368baf0fb58da09334b88713437537ca2fe989a5a64d88d5d52b8ea82`（implementation/var/signature-ref/，不入 Git） | 4/4 一致（含官方文档示例向量、中文/空格/前导零、session、典型查询/上传形状；见 evidence/fb02-sign-sdk-comparison.json 与 test_drug_trace_fb02_official.py） |
| 平台实际受理 | 本实现 `protocol.top_sign` 构造 query.billstatus 只读请求（随机探针单号）直连官方网关 | evidence/fb02-our-signer-query-receipt-r2.json：HTTP200、response_success=true、msg_code=SUCCESS、total_num=0 | **平台受理本实现签名产物**（2026-10-21 实测） |

算法口径（三方一致）：`uppercase(md5(secret + 按key排序的k+v拼接 + secret))`，
UTF-8；签名覆盖原始参数（非百分号编码串）；session 可选；timestamp
`yyyy-MM-dd HH:mm:ss`（GMT+8 墙钟显式生成，官方文档示例同格式）。

已知无害差异（均记录在 evidence known_divergence）：
1. 官方 PyPI SDK（Python2 时代）不过滤空值；官方 C# 参考跳过空项——本项目按
   C# 参考实现空值过滤，且 build_biz_params 保证必填非空，实际流量等价。
2. 表单编码 quote(safe='')（%20）vs SDK urlencode（+）；不影响签名与网关解析。
3. 官方 PyPI SDK `keys().sort()` 为 py2 代码，py3 对照采用语义等价 sorted()
   适配（拼接公式与顺序保持官方原码，适配点在对照脚本中明示）。

声明：以上为**官方源码提取 + 独立向量 + 平台实际受理**的替代验收，不宣称
"官方 SDK 二进制对照通过"。SIGNATURE_VERIFIED=True 是随代码评审/测试/发行
交付的批准修订（protocol.py 常量处注释含完整证据链），不是运行时开关；
回滚资格=随发行回退。真实发送门禁（证据/配额/DMZ 白名单/scope）不受影响。
新增永久安全属性：mock 标记密钥（`__mock__…`）在任何资格状态下都禁止上真实
transport（assert_signing_allowed；T06/B07/G3-11 测试语义保留）。

## 二、官方当期契约口径（2026.01 指南，2026-10-21 取证）

来源：《医疗机构接口对接指南-2026.01》（码上放心官方运营文档，yuque
qg6r2i/dxne69/gxe667idmsa9b75e，markdown 快照
reference/official-guide-202601.md，39KB）。

| 项 | 官方口径 | 本项目处理 |
|---|---|---|
| uploadretail 单据类型 | **[321,使用出库][322,疫苗接种][116,消费者退货入库]** | 首张 PATIENT_DISPENSE→321（经批准字典，不硬编码）；322/116 不预置（业务未批准分类不猜）；323 出现在 open.taobao.com API 页参数表——**两官方页面冲突仍需平台确认**（见差异表），首张按指南 321 走批准字典 |
| query.billstatus result_type | 0=处理中 1=上传成功 3=处理成功 4=处理失败 | contracts.QUERY_STATUS_MAP 补 ("0",\*)→PROCESSING、("4",\*)→REJECTED（service 状态推进含 REJECTED 终态）；1→PROCESSING 维持 |
| sub_process_flag | 51=全部成功 52=部分成功（部分成功=rt3+spf52） | 既有映射一致；PARTIAL 不 CONFIRMED |
| 整单成功模式 | 上传时 ignore_part_success_flag=1 → 任一码解析失败整单返回错误；默认部分解析成功 | 已记入差异表；首张采用默认部分成功模式（查询侧可识别 52 并触发对账），不额外传该标志（待批准后可作为配置项） |
| 部分成功明细 | 接口15 listbillprocesspartsuccess 查失败码明细 | 对账 code-level 核对的候选接口；未纳入本期 DMZ 白名单（记待确认） |
| 服务有效期 | 企业须在服务期内；接口13 service.getenddate 可查 | 未验证（需 DMZ 白名单或平台确认；EXT-06 部分项） |
| APPKEY 权限 | 企业授权 APPKEY + APPKEY 具备医疗机构权限包（申请通过后重新下载 SDK） | **未验证**：查询成功≠上传权限；EXT-06 权限确认仍开放（见回执表） |
| 限流 | query.billstatus 最易超限，需失败后退避 | 已有 QUERY_BACKOFF/代次上限设计一致 |

## 三、接口口径差异表

| 差异点 | 现状 | 处理建议 |
|---|---|---|
| bill_type 321 vs 323 | 指南 2026.01 写 321（使用出库）；open.taobao.com docId=52553 参数表描述写 323、示例写 321 | **待平台技术方书面确认**；首张按指南 321 走批准字典 revision，确认后如需改 323 新建字典 revision（不原地改冻结） |
| 空查询结果形状 | 平台实测 total_num=0 时 result_list 为空对象（无 bill_deal_status_search_do 键） | 已修 parse_query_response（total_num=0 → found=True 空集）；total_num>0 无列表仍 unknown |
| 官方 SDK 空值签名 | PyPI Top-1.0.0 不过滤空值；C# 参考过滤 | 本项目按 C# 参考过滤；必填参数非空校验兜底，两口径对实际流量等价 |

## 四、已确认与待确认字典

- 已确认（有官方当期证据）：result_type/sub_process_flag 枚举（已落码）；
  查询选择器 A=全部/AI=全部入库/AO=全部出库（官方 API 页）；
  retail bill_type 321=使用出库（指南口径，待平台消歧 323）。
- 待确认（不猜、不预置）：322/116 对应业务分类映射；患者退药是否=116；
  ignore_part_success_flag 是否启用；bill_code 长度上限；page_size 上限。

## 五、证据清单

- evidence/fb02-sign-sdk-comparison.json（SDK 对照快照+向量结果）
- evidence/fb02-our-signer-query-receipt.json（首轮；解析器缺陷现场，保留）
- evidence/fb02-our-signer-query-receipt-r2.json（修复后；平台受理确认）
- reference/official-guide-202601.md（官方指南快照）
- ../platform-live-test-20261020/evidence/public-reference-vectors.json（C# 向量）
- implementation/tests/integration/test_drug_trace_fb02_official.py（回归）
- implementation/tests/integration/test_top_official_reference.py（既有 C# 向量回归）
