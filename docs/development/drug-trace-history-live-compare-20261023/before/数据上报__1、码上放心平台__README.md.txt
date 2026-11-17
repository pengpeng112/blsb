# 码上放心平台 · 目录说明与AI接手入口

更新日期：2026-10-23。工作区：`F:\python\blyb`。本目录放原始接口材料及业务导航；程序源码仍在`implementation/`，后续实施证据仍在`docs/`，不搬迁或复制成另一套项目。

**接手顺序：根目录[AGENTS.md](../../AGENTS.md) → [共享启动入口](../../docs/ai/START_HERE.md) → [最新状态](../../docs/ai/STATE.md) / [交接](../../docs/ai/HANDOFF.md) → 本文所列对应材料。**
本文状态是注明日期的摘要；后续以共享状态与最新实际证据为准，旧计划不覆盖新回执。

## 1. 官方接口文档和原始需求在哪里

| 材料 | 路径/链接 | 阅读用途 |
|---|---|---|
| 医院原始需求确认单 | [(260721)需求确认单-码上放心平台HIS对接出库销售上传(1).docx](<(260721)需求确认单-码上放心平台HIS对接出库销售上传(1).docx>) | 本期业务场景与需求，保留原件 |
| 政策PDF原件 | [山东省药品追溯码采集应用工作方案通知](<关于印发《山东省药品经营使用环节全品种信息化追溯体系建设及药品追溯码采集应用工作方案》的通知(1).pdf>) | 政策来源，不能代替API字段定义 |
| 原始接口链接文件 | [接口地址.txt](接口地址.txt) | 原始交付链接保留，不按文件名推断当前生产调用方法 |
| 官方在线指南 | [医疗机构接口对接指南-2026.01](https://qg6r2i.yuque.com/qg6r2i/dxne69/gxe667idmsa9b75e?singleDoc=) | 阅读时注意实际更新日期及本地取证日期 |
| 最近一次获取的完整指南 | [2026-10-22重新获取正文](../../docs/development/drug-trace-official-view-audit-20261022/official-guide-fresh.md) | 在线暂不可用时查本地快照；不是保证永远最新 |
| 官方API字段定义 | [uploadretail官方页面](https://developer.alibaba.com/docs/api.htm?apiId=52553)、[已保存字段快照](../../docs/development/platform-live-test-20261020/evidence/official-uploadretail.txt) | 核对必填、参数、重复码、解析策略等 |
| 指南/实现/视图逐项核对 | [最新核查报告](../../docs/development/drug-trace-official-view-audit-20261022/README.md) | 优先读结论和A01—A05完善任务，再看历史任务书 |

## 2. 先读哪份开发与运维说明

| 要做什么 | 对应文件 |
|---|---|
| 看完整推送流程、机器用途、视图、接口及人工比对方法 | [清晰流程说明](../../docs/operations/drug-trace-flow-20261022/README.md) |
| 看已有真实上传是否成功 | [1单4码真实试报及平台回执](../../docs/development/drug-trace-one-bill-trial-20261022/README.md) |
| 看五个必填字段从哪里取 | [上传内容确定单](../../docs/development/drug-trace-content-contract-20261022/README.md)；其中当时“未上传”已被上述真实回执更新 |
| 看实际视图定义 | [r10视图SQL](../../docs/development/drug-trace-content-contract-20261022/sql/draft_normal_outpatient_views_r10.sql)、[用户执行后核验](../../docs/development/drug-trace-r10-readiness-20261022/README.md) |
| 看来源接入、程序发行和采集证据 | [来源接入与部署记录](../../docs/development/drug-trace-source-deploy-20261022/README.md)；本轮早于真实发送，不把其上传0当作最新事实 |
| 看两台主机安装及运行材料 | [安装部署记录](../../docs/deployment/drug-trace-install-20261021/EXECUTION-REPORT.md) |
| 看最近NTP配置/备份/网络缺项 | [2026-10-23时间同步实测](../../docs/deployment/drug-trace-ntp-20261023/README.md) |
| 查询HIS/其他业务库的统一方式 | [sjzc受控只读入口](../../docs/startup/DATABASE_ACCESS.md) |
| 查看密码和平台凭据登记方式 | [凭证登记说明](../../docs/development/platform-credentials-20261020/README.md) |
| 查看院方要求保存的完整账号密码文档 | [仅本机受限完整版](../../.ai/private/drug-trace-flow-20261022/推送流程及账号密码_仅本机.md)；Git忽略、受限ACL，不复制到本目录或聊天 |
| 提供既往人工记录核对视图 | 原始Excel/CSV可直接交付；另有[空白核对模板](../../docs/operations/drug-trace-flow-20261022/人工上报核对模板.xlsx) |
| 追溯最初部署规划 | [早期执行计划](../../docs/deployment/reporting-20261015/execution-plan.md)；只作历史背景，执行前以最新交接修正 |

SQL文件名中的draft、旧计划中的“未部署/未上传”是当时编制状态。r10已由用户执行并活库核验，**不能因为文件名而重新建用户、重跑视图或重发成功单据**。

## 3. 业务、系统和实际接口

本期是正常已完成且未退费的门诊发药，采用零售上传；其他业务分支不凭OUT标志自动推断。

```text
<prod-host> worker ── TCP1521 ──> <his-host>:1521/his
                                 PHARMACY.V_DRUG_TRACE_BILL
                                 PHARMACY.V_DRUG_TRACE_CODE
<prod-host> worker ── HTTPS/mTLS443 ──> <relay-net-host>/alihealth/router/rest
                                            └── HTTPS443 ──> eco.taobao.com/router/rest
```

| 项目 | 当前已记录配置 |
|---|---|
| HIS抽取身份 | 专用只读BLYB_DRUGTRACE，不能用SSH root作为数据库账号 |
| 本期上传method | `alibaba.alihealth.drugtrace.top.yljg.uploadretail` |
| 状态查询method | `alibaba.alihealth.drugtrace.top.yljg.query.billstatus` |
| 上传/查询类型 | 上传沿用批准的321；查询选择器A，不能互换；所有数值以官方当期文档为准 |
| 五个必填业务参数 | bill_code、bill_time、bill_type、ref_user_id、trace_codes；来源见内容确定单 |
| 医院企业标识 | ref_user_id取医院ref_ent_id，已登记，不向用户重复索要 |
| 最早沟通的uploadinoutbill | 是另一类出入库接口，当前库存上传仍关闭，不替代本期零售接口 |
| SSH管理跳板 | <frontend-host>4:<ssh-port>，仅管理通道，不经过它发送平台业务数据 |
| 数据资产辅助查询入口 | <frontend-host>3，sjzc受控核验；生产worker直接连HIS |
| 应用服务器当前已记录发行 | `/opt/blyb/current/implementation`，发行src-20261022T103000Z；实际变更后更新共享状态 |

## 4. 源码具体路径

| 责任 | 本机目录 |
|---|---|
| 业务分类、质量、冻结、发送、查询与对账 | [implementation/plugins/domains/drug_trace/](../../implementation/plugins/domains/drug_trace/) |
| 两视图抽取/Oracle兼容分页 | [plugins/sources/db_view/drug_trace.py](../../implementation/plugins/sources/db_view/drug_trace.py) |
| 来源身份、reader与限定视图版本 | [source_profiles.py](../../implementation/plugins/sources/db_view/source_profiles.py) |
| TOP协议、签名、请求与转发传输 | [plugins/destinations/alihealth_drugtrace/](../../implementation/plugins/destinations/alihealth_drugtrace/) |
| DMZ转发器与安装材料 | [implementation/deploy/dmz/](../../implementation/deploy/dmz/) |
| Linux安装、备份、恢复与状态 | [implementation/deploy/linux/](../../implementation/deploy/linux/) |
| 本地启动/检查 | [统一启动说明](../../docs/startup/README.md)，生产不要运行Dev/Demo合成入口 |
| 详细实施状态、API、测试与外部依赖 | [implementation/docs/README.md](../../implementation/docs/README.md) |

## 5. 接手时必须知道的实际边界

- 截至既有2026-10-22证据：1张4码已真实发送并获平台result_type=3/sub_process_flag=51、本地CONFIRMED。用户要求不撤销；单笔额度已用，未开启自动批量。新任务不得重发该单或假定旧审批仍有效。
- 2026-10-22只读复核27条SELECT成功，正常门诊batch视图可用；样本不是全历史/全部业务验收。
- 主单更新时间仍是发药时间，码明细存在20～251秒迟到；齐套、晚到变更/滚动回看、expected_code_count完整性尚须完善。
- 当前未发送可选的ignore_part_success_flag；若要整单解析失败即整单拒绝，须新增配置/冻结映射；不能原地改历史payload。
- 包装关系、退药/住院/拆零、人工历史对账及本地4条明细PENDING展示见A01—A05；不靠猜值补齐。
- 2026-10-23时间源已持久配置<relay-net-host>：DMZ已同步；<prod-host>仍未收到UDP123回包，已配置不等于已同步。
- 正式证书等后续完善按共享BACKLOG处理，不把已被用户延期的材料重新一律当成既有单笔试报未成功的理由。

## 历史清单核对与当前窗口独立复核（2026-10-23）

[25份历史上报文件的离线检查及实时比对计划](../../docs/development/drug-trace-history-compare-20261023/README.md)：489613行已读取，原件未修改；发现的重复出现与跨单码暂不判错。原码明细工作簿在仓库外受限目录。实时视图尚未查询。用户已说明任务误发，改由另一窗口核查；本窗口停止等待/执行原白天窗口确认，不宣称已一致、不补报或重发。

当前BLYB窗口已根据用户指定交接记录完成[独立复核](../../docs/development/drug-trace-history-transfer-audit-20261023/README.md)：25份Excel与受限SQLite逐行及统计一致；原探针索引/时间范围/可发送口径需按新报告修正。HIS白天窗口尚未收到本次批准，真实视图比对仍0，不补报或重发。

## 6. 继续开发时怎么维护

按[本目录统一维护要求](../README.md)登记任务，阅读最新共享状态，核查相关代码和证据后再改。
原始docx/PDF/txt不覆盖；新接口文档或视图/字典/冻结配置采用新revision，留来源、日期及路径。
新增或迁移代码/视图SQL/部署/测试/人工核对结果后，**同步更新本README的路径与证据链接**，并更新共享STATE/BACKLOG/HANDOFF。
不另建一套业务私有“当前状态”替代共享记录。真实HIS查询和实际发送分别按当前用户授权处理，读本文不产生登录/发送授权。
