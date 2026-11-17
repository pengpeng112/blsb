# 药品追溯模块详细开发计划

版本：1；2026-10-16；适用源码基线 `2d46bb8` / 现场发行 `20261016T114735Z`。本文件是开发要求，不是完成报告。执行入口见[NEXT_AI_PROMPT](NEXT_AI_PROMPT.md)，现场事实见[复核报告](DEPLOYMENT_REVIEW.md)。

## 1. 本轮架构与范围决策

保留 Python 后端、Vue 前端、PostgreSQL、现有持久任务与插件结构。稳定性主要通过持久快照、任务租约、幂等、版本固定、回执和可恢复部署保证；本轮不换语言、不重建骨架、不引入新的队列/调度中心/微服务群。

首期交付两个独立profile的离线完整实现：①供应商采购入库/退供应商，优先对应用户明确给出的SDK；②医院患者发药/患者退药，承接原始接口资料。代码与测试两者都完成，**只激活院方确认并具备视图、字典和平台权限的业务分支**。销毁、疫苗、包装关系补建、单据删除/改码等只列能力边界，不因示例存在自动启用。

系统依次完成：HIS只读视图→<prod-host>整单抽取→持久来源快照→质量校验→固定配置生成业务包→持久提交→DMZ专用HTTPS转发→平台→主动查询处理结果→逐单逐码对账。平台状态、游标、错误和人工处置只写本系统，不回写HIS上传标志。

```text
HIS <his-host>:1521（只读视图，院方建视图/授权）
        ↑ <prod-host> 主动只读查询
<prod-host>（API / worker / PostgreSQL / Vue）
        ↓ HTTPS 443，服务身份验证
<relay-net-host>（专用转发，固定上游，不存业务任务）
        ↓ HTTPS 443，校验官方证书与SNI
https://eco.taobao.com/router/rest
```

响应沿原连接返回；状态查询由<prod-host>主动发起。本轮不要求互联网主动入院，不要求DMZ主动回调<prod-host>。<jump-host>:<ssh-port>是独立运维跳板，不参与正常上报。

## 2. 现场整改阶段 P0（先于真实对接）

| 任务 | 明确修改位置/交付 | 验收 |
|---|---|---|
| P0.1 TLS | 修正install/status/nginx相关临时证书与校验方式；可受信IP SAN临时测试证书，生产使用院方正式证书；证书路径与轮换runbook | OPS-01；客户端链/名称验证，无 `-k` 或关闭TLS验证作为通过依据 |
| P0.2 恢复 | restore/lib：统一compose入口，支持显式独立项目名/数据根/配置根，备份先安全检查再映射到空目标；归档防路径穿越/符号链接逃逸；镜像和schema兼容判断 | OPS-02/03/07；任意cwd与不同配置路径恢复成功；非空/越界拒绝且不改现网 |
| P0.3 备份 | backup/lib：统一实际挂载路径；先冻结所有写入口、排空在途，再采所有快照；区分仅DB与含对象备份；trap恢复原服务状态；敏感产物创建即受限 | OPS-04；故障注入、跨库/对象一致、异机恢复演练 |
| P0.4 生产守卫 | core_api/core_handlers+药品插件：新增按domain/profile/destination/revision判定的必需证据白名单；显式批准后才能正式采集/发送 | DTR-A02/A03；空证据不能放行，病理既有规则回归不退化 |
| P0.5 时钟和网络 | 院方NTP/DNS/DMZ443出口，服务端证书、DMZ材料；先产配置模板、检查脚本、离线包，不猜实际地址/CA | EXT-03/04/05；不把外部缺项变成停做全部本地代码的理由 |

恢复不能直接把备份配置解到生产`/etc/blyb`再问是否安全；迁移/恢复/回滚必须先在独立栈演练。真实服务器重启、修改网络与现网升级安排维护窗口后执行。本次规划不授权真实业务发送。

## 3. 复用边界与代码落点

下表“新增”为建议文件名，执行AI可拆分小文件，但保持项目布局并在交付中列实际路径。

| 位置 | 修改目的 | 边界 |
|---|---|---|
| `plugins/domains/drug_trace/`（新增） | models/contracts/source_ingest/quality/pipeline/service/api/plugin；领域业务、视图契约、状态编排 | 不在病理models或患者关联代码里塞药品字段 |
| `plugins/sources/db_view/drug_trace.py`（新增）及warehouse | 固定允许视图、参数化SQL、整单读取、一致性校验、Oracle兼容、超时与限速 | 现有MedrecHisConnector仅患者/就诊/字典，不能直接当药品采集器 |
| `plugins/destinations/alihealth_drugtrace/`（新增） | SDK/transport封装、两上传profile、查询、响应解析、错误分类、对账能力 | 不改Mock接口去冒充正式TOP；对账/失败详情无确认接口时明确不支持 |
| `plugins/app_runtime.py` | 注册drug_trace，增量扩展job/event/capability注册；按domain构建上下文 | 现有build_runtime偏病理；保留旧调用兼容、metrics-only不导入病理，未知插件严格报错 |
| `apps/worker/engine.py` | 合并已注册药品jobs/events，复用claim/lease/dedup/backoff；注册collect/prepare/send/poll/reconcile | 目前job类型和事件路由硬编码，必须把实际执行链接通；不另开无持久状态后台线程 |
| `plugins/core_handlers.py` | 按明确目标及能力派发，复用提交/回执存储与审计；修正非Mock误用Mock路径 | 旧病理/metrics输出保持兼容；药品payload不得走病理index/slides模板 |
| `packages/core_db/models.py`、migrate及domain迁移 | 复用SourceRecord/Revision、SourceCheckpoint、DataPackage/PackageRevision、Job、Submission/Item、Attempt、ReceiptEvent、Reconciliation | 不复制第二套Job/队列/Submission；新增约束必须同时支持SQLite与PG |
| `apps/core_api/app.py`+领域router | 注入既有Principal/角色/tenant会话、挂载药品接口，复用配置与审核机制 | 未启用插件时不能出现可调用的正式发送入口 |
| `apps/admin_web/src/{main.ts,api.ts,views/}` | 药品总览、单据、质量、提交回执、对账、配置入口 | router实际在main.ts；同源、令牌和现有角色复用，禁止前端持有平台密钥 |
| `deploy/dmz/`（新增） | 独立专用转发配置、服务运行材料、健康检查、离线安装/升级/回退 | 兼容Anolis7.9；不得占用DMZ既有8001，不引入通用开放代理 |

通用能力只抽取此次两个药品profile需要的部分，不为未来未知上报一次设计任意脚本平台。未来新增上报按“领域契约+来源连接器+目标适配器+配置revision+验收样例”扩展。

## 4. 数据模型、幂等与迁移

以现有core模型为准，药品新增以下最小领域结构（最终名称由实现固定）：

| 结构 | 必要内容与约束 |
|---|---|
| DrugTraceBill | tenant、connector、source_system、pharmacy_code、business_kind、source_bill_id、current_source_revision、来源状态、原单引用；复合业务键唯一，字符串保存原值 |
| DrugTraceBillRevision | 不可变来源header/lines/codes快照、source_revision/content_digest、事件时间、来源证据、配置引用、质量状态；同来源版本重导幂等，内容不一致报冲突 |
| DrugTraceCode/Line或快照明细索引 | 稳定source_code_record_id、source_line_id、原trace_code、quantity/unit、删除标记、父子包装层级（来源可提供时）；唯一键须含tenant/主单版本/来源明细ID |
| 平台单号映射 | tenant+机构profile+业务事件→稳定bill_code；检测编码碰撞与平台长度限制，不能用患者ID/随机重试号代替；配置变更不得隐式重编旧单号 |
| 生产接入证据 | domain/profile/destination/config_revision/capability、必需证据项、状态、批准人、有效期/撤销；空白不能默认通过 |
| 既有core记录 | SourceRecord/Revision血缘，PackageRevision冻结payload，Submission幂等、逐码SubmissionItem、DeliveryAttempt、ReceiptEvent、ReconciliationRun/Item；优先复用，所需新列走迁移 |

所有子实体访问必须同时约束tenant与父对象所属connector/owner范围；请求里传tenant不构成授权。新外键和唯一约束显式包含tenant作用域，不能只在前端过滤。敏感患者字段按现有PII规则保留最小范围。

迁移从**009及后续空闲编号**开始；先枚举core+启用插件迁移，禁止编号冲突（额外迁移未显式编号会落到10000段）。迁移文件先定表/索引/回填策略；不能改已执行001—008原语义。新装建表与既有PG升级两条路径均测试。药品关闭时仍要能兼容已存在药品schema；不依赖把全局插件默认集改大来自动启用。

发布默认`drug_trace`生产能力关闭、connector停用、destination不启用正式发送。将现网当前插件集显式固定，防新增manifest导致升级时默认加载并自动调度。表结构扩展保持旧代码可运行；回退默认回旧镜像并保留新表，不自动删表降级。若确需不可逆迁移，另立revision与恢复方案，不混入首发。

## 5. HIS视图与只读抽取契约

沿用旧 [view-contract](../../deployment/reporting-20261015/view-contract.md) 的字段名称，新建**revision 5草案**，保留revision4原文。交付 `view-contract-r5.md`、字段字典、合成CSV/JSON、Oracle建视图模板与只读授权模板；模板不是已验证HIS业务SQL，标明待院方确认的JOIN/状态。

视图建议仍命名`V_DRUG_TRACE_BILL`和`V_DRUG_TRACE_CODE`；可以按业务拆实体视图并由白名单配置选择，不能任意输入SQL。两分支共享结构、独立业务类型/映射。

主单必需：source_system、source_bill_id/no、source_revision、business_kind、pharmacy_code、bill_time、updated_at、finalized、is_void；source_event_seq优先；expected_code_count确有独立依据才提供。退药/退供应商保留包含药房和来源系统的完整原单业务键。库存分支补warehouse_code、supplier_code、收发机构内部标识/方向；平台企业ID仍由批准字典映射。患者ID、就诊ID分列且按需读取。

明细必需：与主单一致的完整来源复合键、source_revision、source_line_id、source_code_record_id、trace_code、updated_at、is_deleted；quantity/unit可选但拆零必须有明确业务依据。不能只用source_bill_id跨药房JOIN。禁止DISTINCT掩盖重复、提前聚合逗号码串、把追溯码转数值或按数量生成虚构码。

目录元数据候选及证据：DRUG_USER.MED_TRACE_RECORD/MED_TRACE_STORGE，PHARMACY.DRUG_PRESC_TRACE_CODE/DRUG_IMPORT_EXPORT_TRACE_CODE/DRUG_PRESC_MASTER，以及PHA_INP_DISPMASTER/DETAIL；见旧revision9目录JSON。字段相似不证明关系成立；例如DIS_RECORDNO两侧类型不同，需院方验证。不得用备份表/COPY表替代真实来源。

用户本轮追加的MED_TRACE_RECORD历史SQL已完整保存在[SQL_REVIEW](SQL_REVIEW.md)。新增子任务**DTR-W02-SQL/DTR-W04-SQL**必须随W02/W04完成，验收DTR-S01—S08。尤其不能保留MAX取单码、拼接PRESC_DETAILID当单据号、只按CREATE_TIME长期增量、只凭OUT选择上传接口的做法。历史区间是否包含10月12日全天、是否已上报须院方确认，不能默认批量重报。

抽取算法必须满足：

1. 优先可靠单调事件序列；否则用`(updated_at, 完整来源复合键)`稳定keyset游标、固定本轮上界与可配置重叠回看，不能只按更新时间/单号或offset分页。
2. 主单发现分页与整单明细分页分开；读取所有明细后才校验/生成提交，不能一页当一单。
3. 明细新增、删除、扫码关联、作废均须推动主单变更标记。若视图不能保证，登记漏数边界，采用经批准变更账本或周期全量对账，不宣称绝对不漏。
4. 使用受限只读连接/事务；一致性SCN需要院方可用权限，不默认有Flashback。否则主单版本前后对比，变化/未齐套转质量等待后重取，不推进错误版本。
5. 在本系统一个事务内保存快照、导入去重、outbox/job和checkpoint；提交失败全部回滚。checkpoint仅表示已安全持久化，不表示已发送。
6. 0/1/N码分别处理；0码等待、不伪造成功；重复明细/同单重码/大小包装码冲突进入明确质量问题，不静默删重。未能判定父子包装关系的情况必须显示校验能力边界。
7. 已受理/确认单的来源更正形成新版本和待核实事项；未确认平台更正规则前不自动覆盖或重新上传。退药是独立事件，不能撤销原始发送历史。
8. Oracle兼容以院方版本为准，Oracle11g用ROWNUM/keyset模板；参数绑定、标识符白名单、读超时/取消、连接池上限、单轮上限与限速；离线测试用fake DBAPI与合成数据。

真实HIS通道必须先消除service_name/SID冲突、获得合规的readonly登记/账号及对象授权。开发阶段不试密码、不扫业务表、不执行建视图DDL；建视图与授权由院方单独执行。

## 6. 平台协议与配置固定

核对来源是2026-10-16官方API页：[供应商出入库](https://developer.alibaba.com/docs/api.htm?apiId=52556)、[患者发退药](https://developer.alibaba.com/docs/api.htm?apiId=52553)、[处理状态](https://developer.alibaba.com/docs/api.htm?apiId=52558)。实施时再核验并记录来源日期、版本、SDK哈希；所有数值与枚举**以官方文档当期版本及平台确认为准**，不能把示例当批准字典。

| profile | 上游方法 | 映射差异/注意 |
|---|---|---|
| alihealth_yljg_inout_v1 | alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill | 用户给的SDK类对应此方法；供应商流转，增加from/to企业、药品/客户端类型等要求 |
| alihealth_yljg_retail_v1 | alibaba.alihealth.drugtrace.top.yljg.uploadretail | 患者发退药；可选患者信息必须经过批准，不能强制库存单提供患者信息 |
| query capability | alibaba.alihealth.drugtrace.top.yljg.query.billstatus | 与上传共享身份但请求/响应结构独立；按企业、创建日期范围、单号、分页读取 |

配置revision固定：医院ref_ent_id、医院/供应商ent_id映射、bill_type字典、拆零/退药口径、签名/编码策略、码数保护值、DMZ端点/信任证书引用、权限、限速、重试、轮询与对账窗口。AppSecret只存受限文件/既有SecretRef后端；引用必须校验tenant/目标权限；容器只读挂载所需文件。响应与审计不含密钥、签名原串、完整患者信息。

当前文档零售列表标3500、库存标10000，同时描述要求小于对应数量；边界有歧义，分别设可配置保护，接近限制阻断而非截断。拆单须另有平台确认的稳定子单号/父子对账规则。零售bill_type示例321与说明323有差异，不默认选任一值上线。状态中result_type=3、sub_process_flag=51/52作为当期文档记录，组合映射必须经批准版本与样例验证。

优先使用可核验的官方SDK，封装在适配器内。类名有Java/C#写法不能决定全平台换语言。取得Python兼容SDK或官方签名规范/测试向量后完成真实transport；如官方SDK确实仅支持院方指定语言，再以ADR选择一个小型签名适配组件，不重写平台。缺SDK仍须完成接口、Mock transport、解析与测试；签名一致性未验证明确标阻断，不用正式上传“试签名”。

TOP使用正式HTTPS网关，按协议构造表单与签名，指定JSON响应并解析各方法独立envelope和error_response。冻结业务payload及digest；timestamp/sign等瞬时传输参数与业务快照分开审计，日志脱敏。不能把JSON REST POST当TOP表单，不把SDK返回字符串直接当成功。

## 7. 提交、查询、异常和对账

复用SubmissionState：PREPARED→SENDING→ACCEPTED/REJECTED/PARTIAL/UNKNOWN；全部业务确认才CONFIRMED。来源/质量等待用独立领域状态或既有QualityIssue，不扩散不兼容枚举。

发送之前把不可变请求快照、明确目标ID、配置revision、bill_code和SENDING尝试持久化，再执行网络I/O。不能持有长数据库事务等待外网。网络返回后通过版本/租约CAS落结果；进程在发送前后崩溃均可定位原Submission。恢复SENDING一律先查询，不把“没有本地响应”当“平台没收到”。

幂等范围包含tenant/机构、目标profile/destination、完整来源业务键、允许发送的版本；bill_code按批准规则稳定。只创建一个逻辑Submission，尝试序号递增。并发租约能防本地重复执行，但无法撤销已发出HTTP；租约失效在途请求必须进入待核实，禁止第二worker直接再发。不能承诺跨系统天然exactly-once。

HTTP200、response_success、队列ID仅为相应层受理。轮询必须识别创建日期与业务日期差异、实际上传时间及跨日窗口，正确遍历全部页；查询无记录/权限失败/分页异常不能直接判未发送。result_type与子状态、机构/单号/业务profile均匹配后才推进结果；未知枚举或多条冲突转人工，不自动降级已确认状态。

错误分类：认证/权限/字典/数据错误暂停并显示可处置原因；限流、只读查询临时故障采用有上限指数退避及抖动；上传超时/断连/502等可能已到上游的情况进入UNKNOWN优先查询。网关和SDK必须关闭自动POST重试；确定未写出也需统一策略，不能让多层各重试。

部分成功保存成功/失败码明细，未取得可核验明细时保持部分待核实，不能把整单重复发一次。ReceiptEvent去重并保存允许的原始状态/解析版本，按业务键和状态证据处理乱序回执。人工重查、补报、忽略、关闭差异都要角色授权、理由、审计和复核；已确认单不提供无条件“重新上报”。

对账复用ReconciliationRun/Item：固定tenant、connector、药房、业务类型、目标、时间窗口和时区；比较来源快照、本地提交、平台三方。先查数量再核单号、状态与码集合，数量相等仍可能错单。查询接口覆盖零售/库存范围和失败详情下载需要平台确认；只具单据状态时展示“单据级对账，码级未核验”，不显示全部一致。遗漏/重复/差码/部分成功分类持久化，重跑幂等，未经查明远端不存在不补发。

## 8. DMZ专用转发交付

固定内部服务契约建议：`POST https://<院内转发域名>:443/alihealth/router/rest`，映射官方`https://eco.taobao.com/router/rest`；8443仅备用配置，不默认同时监听。健康接口`GET /health`返回本服务状态，不通过周期真实上传探活。内网API入口证书与DMZ证书各自配置，不能共用不匹配域名的证书。

采用<prod-host>客户端mTLS身份，服务端限制源IP并验证客户端证书；缺院方PKI时先交离线测试CA材料/生成工具，私钥不进Git，正式启用等证书。若院方批准改为服务令牌方案，记录ADR和轮换/脱敏机制；不能无鉴权开放。

只允许批准的TOP方法、固定上游与路径；拒绝任意URL/Host/CONNECT/重定向外跳。验证允许方法时不得修改签名字段或重新编码已签表单；原请求body、上游HTTP状态与body语义透传，代理身份头不传上游。平台AppSecret留在<prod-host>，DMZ不签名、不重放、不缓存响应。

关闭POST重试和代理缓存；连接/读取/总超时层级协调（上游超时小于内层请求等待、外层有适当余量），明确请求大小、并发与限速。候选2MiB请求上限只是工程初值，按两个profile实测表单体积核定，不限制病理文件通道。错误页不伪装成平台成功JSON，应用超时一律按未知处理。

选择能在Anolis7.9运行的受支持转发材料，记录libc/OpenSSL/CA兼容、签名/哈希、离线安装、最小服务用户、systemd重启与日志轮转、升级与回退。可优先使用专用HTTP转发组件/审核过的反向代理组合，不要求DMZ安装整套业务平台。仅配置固定反代不足以提供方法白名单时，增加最小审查层并测试原字节保持。

网络开通以源/目的明确：<prod-host>→HIS TCP1521；<prod-host>→DMZ TCP443（8443备用）；DMZ→eco域名TCP443、到批准DNS UDP/TCP53；两机→院内NTP UDP123；运维源→目标SSH实际端口另列。状态防火墙放行已建立连接回包；不为响应额外开反向业务服务。平台域名解析可能变化，不固定某次解析IP上线；必要时院方配置域名白名单/NAT/SNI策略。

exchange是现有PULL/视图服务，不能当成DMZ转发器。本轮维持现有exchange运行；若另行关闭，必须声明PULL/发布和依赖它的现有验收不可用，并调整compose/备份/健康门禁，不能只停容器。

## 9. API与管理界面交付契约

新增建议路由统一`/api/v1/drug-trace`；沿用既有响应外壳、错误码、request_id、角色、幂等键与keyset分页。通用submissions/reconciliations可复用，领域路由提供药品筛选与血缘，不复制矛盾状态。

| 路由/操作 | 要求 |
|---|---|
| GET overview；GET bills；GET bills/{id} | 按药房/业务/时间/质量/提交状态筛选；显示来源版本、码数、单号与当前阻断；列表不返回完整患者/码串 |
| GET bills/{id}/revisions；GET revisions/{id}/codes | 固定版本分页、字段血缘、敏感读取授权与审计 |
| POST previews | 只用已存合成/脱敏快照，产生映射/质量预览；dry-run不连HIS、不查平台、不发网络 |
| POST collection-runs；POST collection-runs/{id}/pause | 先验证readonly资料与启用证据再排持久job；支持固定时间范围/上界/断点；没有任意SQL或任意目标URL参数 |
| POST bills/{id}/prepare | 幂等生成冻结包/提交预览，指定source_revision/profile/destination/config_release，冲突返回409 |
| POST submissions/{id}/send；POST submissions/{id}/query | send需明确批准、revision未变、角色允许；query只排查询job，UNKNOWN时默认只查询 |
| POST reconciliation-runs；GET runs/{id}/items；POST items/{id}/dispositions | 固定范围，差异持久化；补报不可绕过原提交状态与授权，处置必须理由 |
| GET readiness；配置发布/机构字典维护 | 展示每个profile的必需证据、缺项和有效revision；密钥只显示引用/已配置，写后不回读 |

operator执行采集/重查，business_reviewer处理质量和业务批准，config_editor维护草稿，platform_admin配置连接与发布，auditor只读脱敏审计；具体沿用现有角色组合，不能任一登录用户都有send权限。对send、批量重查、批量补报设速率/批量限制。

界面交付：药品总览；单据及版本详情；质量待处理；提交/回执和UNKNOWN原因；对账差异及处置；连接/profile配置与接入就绪检查。MOCK、DRY_RUN、PRODUCTION必须显著区分。操作人员看到“未齐套/等待平台/结果未知/部分成功/已确认”等业务状态，不暴露内部任务调试字段替代解释。

## 10. 顺序开发工作包

| 顺序/任务ID | 完成内容 | 依赖 | 交付完成证据 |
|---|---|---|---|
| 0 / DTR-W00 | 登记会话，读最新状态/AGENTS，Git与现场版本基线，备份本地待改文件，登记外部缺项 | 无 | baseline/任务范围，无覆盖其他工具 |
| 1 / DTR-W01 | P0.1—P0.4脚本及守卫修订；补独立恢复测试 | W00 | OPS验收、故障回退证据 |
| 2 / DTR-W02 | 两profile契约、view-r5、字段字典、SDK策略ADR、状态机与合成样例 | W00 | schema/样例验证、未决口径清单 |
| 3 / DTR-W03 | 009起编号迁移、领域模型、插件jobs/events注册、默认停用 | W01/W02 | SQLite+真实隔离PG迁移/回退兼容 |
| 4 / DTR-W04 | 整单抽取、事务checkpoint、质量校验、冻结payload与幂等 | W03 | A类采集/质量测试、并发/故障注入 |
| 5 / DTR-W05 | 两上传adapter、签名/transport、查询及状态归一、持久尝试/UNKNOWN恢复 | W03/W04；正式签名证据依赖SDK | B类测试；SDK未齐只能相应证据标未完成 |
| 6 / DTR-W06 | DMZ组件/配置、mTLS、固定上游/方法、限流与离线包 | W02；上线依赖环境材料 | C类本地TLS/上游模拟测试 |
| 7 / DTR-W07 | 三方对账、人工处置、重查/补报守卫、API角色审计 | W04/W05 | D类差异演练及API契约 |
| 8 / DTR-W08 | Vue界面与合成全链路浏览器验收 | W07 | E类前端构建/浏览器证据 |
| 9 / DTR-W09 | 必要全量回归、四类实施文档/API矩阵、发行白名单/离线依赖/迁移说明 | W01—W08 | 日志/XML/构建/manifest与未测项 |
| 10 / DTR-W10 | 具备维护窗口后<prod-host>升级，DMZ部署和只读联调；未齐资料仅部署禁用模块 | W09及相应EXT关闭 | 现场版本/备份/恢复/健康/禁发证明 |
| 11 / DTR-W11 | 批准小范围真实单据→查询最终结果→三方核对→逐步扩大 | 单独真实发送授权及全部必要EXT | 首批签认、监控/RPO/RTO、停发与回滚演练 |

按任务顺序持续完成可执行代码，不在每个函数处等确认。外部材料缺失只阻断依赖环节；需交完整可测试代码、模拟环境、模板和明确待接资料，不能留下大量TODO后宣称一次性开发完成。

## 11. 测试、发行与上线顺序

详细测试见[验收矩阵](ACCEPTANCE.md)。先局部业务测试和修复项验证，再一次必要全量回归；不得为了复刻历史数字而删断言/跳过新失败。真实PG在独立项目/端口/目录，mock上游与DMZ测试只发合成数据；dry-run测试禁止任何外连。现有功能脚本会清目录/杀端口，未确认隔离不得运行。

命令入口保持明确：根目录会话登记；`implementation/`执行`python -m pytest tests -q -ra`并落本轮XML/log；前端目录`npm run build`；Linux脚本用隔离bash环境及真实隔离PG演练，缺材料如实标未测。不得联网安装至HIS/DMZ；开发新增依赖先固定来源/版本/哈希/许可证，制作离线wheel/运行材料，不能让目标机临时pip公网安装。

发行必须包含新插件、migrations、SDK/运行依赖、前端dist、DMZ包、合成验收脚本与runbook；更新build_release白名单，校验Docker上下文不带真实SQLite、runtime.env、私钥、备份、日志和患者样例。源码、镜像、依赖、配置revision、数据库schema版本可追踪，保留旧发行。

现场先核对现状与授权窗口→停正式采集/发送并排空或冻结在途→可信一致备份→迁移→升级→校验证书/权限/6服务及合成闭环→检查原任务/游标/提交不丢→仅启用已批准只读源→比对预览→另获真实发送批准→有限首批→查询确认/对账签认→扩大。升级失败先停发送，按兼容性回旧镜像；数据库仅通过已验证恢复流程，不自动清卷或降级表。

运维至少监控：服务/worker心跳、队列最老年龄、采集滞后、UNKNOWN数与持续时间、拒绝/部分成功率、对账差异、DMZ出口/TLS、证书到期、时钟同步、磁盘与备份年龄。工程建议初值：采集60秒、回看10分钟、轮询30秒起逐步退避至10分钟、单机构上传并发1；它们不是官方容量值，需按HIS性能/平台限额/院方时效要求调优。达到告警阈值通知现有院内通道，不把临时SSH手工看日志当运维交付。

## 12. 完成定义与责任边界

开发完成＝工作包W01—W09及对应离线验收通过、可安装可升级可恢复、文档与证据齐全；存在官方SDK签名等实现阻断时，应标“部分实现/该项阻断”，不能以Mock替代正式adapter完成。

现场可运行＝W10相关资料齐备且实测；正式业务完成＝W11院方签认。三者分别报告。系统healthy、页面能打开、SDK类能导入、HTTP200均不等于最终上报确认。

执行AI收尾必须交：变更/迁移清单、真实命令退出码、局部与全量XML/log、浏览器证据、隔离恢复与升级结果、离线包manifest、尚未关闭EXT表、四类实施文档、STATE/BACKLOG/HANDOFF及单任务交接。业务/API/状态变化同步现有四类文档和API矩阵；原始接口文件、冻结字典、历史payload与旧报告保留。
