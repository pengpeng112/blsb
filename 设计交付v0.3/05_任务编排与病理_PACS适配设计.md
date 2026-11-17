# 任务编排、病理流程与PACS参考适配

## 1. 标准处理链

通用链条为“获取源事件→保存原始输入→解析→标准化→关联→质量校验→等待依赖→冻结包→目标映射→目标校验→推送或发布→收集证据→业务确认/对账”。文件归集可独立先完成，业务数据到达后再触发关联。

接入层只保证输入可恢复；领域层决定数据语义；交换层决定对外行为；任务内核只按声明式流程驱动节点，不能将病理条件写死进调度器。

## 2. 流程DSL与节点契约

WorkflowRevision包含版本、触发器、输入Schema、节点及端口、边与条件、并发/时间预算、失败策略、审批策略、输出契约。首期只支持有限DAG，定时轮询在调度器产生新run，不通过画布回边实现无限循环。

| 节点 | 输入→输出 | 副作用与失败处理 |
|---|---|---|
| source.receive / source.poll | 事件/游标→RawRecordRef | 只有原始事件落库才推进checkpoint |
| parse | RawRecordRef→TypedRecords | 坏行进入错误集，保留原始位置 |
| normalize | TypedRecords→CanonicalRecords | 纯转换；固定规则版本 |
| resolve | 标准记录＋来源引用→关联结果 | 单候选匹配；多候选WAITING_HUMAN |
| validate | 记录/包→QualityResult | 字段、关系、字典、时序与目标约束 |
| wait_dependencies | 包候选→满足依赖的包 | 持久等待，不占线程睡眠；事件或调度重评估 |
| assets.ensure | AssetRefs→可用文件集合 | 不重新上传已AVAILABLE资产 |
| format.convert | 源AssetRef＋参数→衍生AssetRefs | CPU/磁盘预算、幂等任务key、输出质量验证 |
| package.freeze | 完整候选→PackageRevisionRef | 原子冻结引用与数据修订，后续不可变 |
| target.map | 包＋Profile→目标报文/视图数据 | 纯映射，输出摘要与逐字段血缘 |
| target.validate | 目标快照→验证通过引用 | 未确认Profile/不支持格式阻断 |
| deliver.push | 已验证提交→远端受理证据 | 外部副作用；稳定幂等键；未知结果查询 |
| deliver.publish | 快照＋可达文件→Publication | 构建交换投影，发布可见性，不标医保成功 |
| receipt.wait / reconcile | 提交/发布→业务证据 | 接收回执/查询/人工录证，能力未知时保持待确认 |
| notify | IssueRef→通知记录 | 首期控制台告警；外部邮件/短信按医院实际配置 |

每个节点定义输入输出JSON Schema、是否有副作用、幂等策略、可取消阶段、资源类别。保存时检查节点存在、版本、端口类型、环、必填配置和未绑定凭据。发布时跑脱敏样本和失败注入；dry-run节点禁止真实发送和外部发布。

## 3. 版本与执行恢复

Run固定workflow、mapping、dictionary、domain schema、index profile、destination profile、hospital pack的具体revision，不引用“latest”。配置切换只影响新run；旧run保持原上下文，必要时取消未发送步骤并创建新run。

重试同节点使用相同输入和逻辑操作ID，attempt单独增加。重放是新run，必须先显示副作用计划；默认仅重放纯转换，已有发送步骤通过原提交结果复用。外部更正是新submission，不是原任务改内容重试。

优先队列按tenant/source/destination设置配额；实时与历史补传分别限流，建议给实时保留份额，但具体比例可配置。退避初值、最大间隔、最大尝试、熔断恢复探测均配置化；永久数据错误不能无限重试。

## 4. 病理领域完整流程

### 4.1 以真实标识关联

扫描事件必须有设备/来源namespace、scan_id/scan_revision和玻片候选标识。没有玻片ID时进入待匹配，不因为文件名相同就认为是同片或重扫。

PIS应提供稳定检查登记ID、病理号、申请引用、患者/就诊引用、玻片清单和报告变更；HIS/集成平台补充身份、申请时就诊类别及费用信息。记录每字段权威来源，不默认所有字段都来自HIS或最新就诊。

自动匹配条件：来源命名空间已确定；PIS检查唯一；玻片ID/条码在该检查唯一；患者/申请/就诊引用没有矛盾；来源事件版本有效。姓名等仅辅助展示，不作为自动唯一键。

### 4.2 齐套和增量

预期玻片清单定义哪些物理玻片应有图像；每片选择一个批准的有效扫描版本，版本下的必需文件成员全可用。报告、结算等依赖按目标Profile声明，不能把PACS最终结算要求一律套到所有病理上报。

允许策略：whole_case、per_slide_incremental、staged。首期内部Mock以whole_case验证完整闭环；生产策略由平台确认。没有完整预期清单时等待PIS完成信号或人工确认，计数/静默窗口不证明齐套。

新增玻片、重扫、补充报告、患者身份更正分别触发候选新包；当前PUBLISHED/CONFIRMED快照保持不变。对方支持增量就提交差异，不支持则按其更正/整包替代协议。未知协议进入待处理，不自行覆盖外部旧记录。

### 4.3 病理索引

IndexCodec取证件类型、证件号码、申请时就诊类型、申请单号、病理号、申请时间、报告机构医保代码、申请类别。字段不足就等待，不填零伪造。源字典、医保数据集字典和索引公安字典分开。

一病理号多申请/多类别时，先形成清晰业务关系，再由已批准的索引范围策略确定一个或多个IndexRecord。开发阶段不得用“取第一项”解决多值问题。

### 4.4 病理内部Schema与数据集草案

内部Schema包括patient_ref、encounter_ref、applications、case、specimens/blocks/slides、scans、reports、diagnoses、fee_links、index_candidates。正式必需字段以已批准的目标Profile校验，内部允许等待状态下字段不全。

本地Excel草案38个主字段及玻片、诊断、费用三个子表只作为映射参考。电话号码、报告下载/访问链接等按对应Profile定义，不能因为未来可能需要就让Agent采集所有患者信息。

## 5. PACS V2参考Profile

Profile名建议 `jinan-imaging-v2-draft`，引用2025-12-15 V2修订记录及01冲突单。模式为PULL_PUBLICATION，文件下载优先HTTPS（须采集器支持），DICOM C-STORE等通过另一个出口适配器启用。它不是正式病理云Profile。

### 5.1 公共模型到视图的映射边界

| 外部数据集 | 我方来源 | 关联及注意事项 |
|---|---|---|
| APPLY_INFO | Application＋患者/就诊快照＋医生/项目映射 | applyId是申请身份；applyNum可能是展示合并值，内部保留申请数组 |
| CHK_INFO（别名待定） | Examination/病理case的批准兼容投影 | risStudyId为稳定外部检查ID；systemId标明来源 |
| RIS_PACS | examination与imaging-study关联 | 不能按当前磁盘目录生成studyUid；跨系统歧义用稳定映射解决 |
| SERIES_INFO | 真实序列或批准的兼容分组 | 病理如何分组须确认；可按玻片扫描组生成，非官方默认规则 |
| IMAGE_INFO | 已发布Asset成员 | studyUid、pacsSeriesId、sopInstanceUid、hostId及imagePath绑定固定版本 |
| PACS_HOST_INFO | 图像发布网关配置 | 描述我方交换存储/网关，而非把扫描工作站直接交给对方访问 |
| RIS_HOST_INFO | 报告/申请附件网关配置 | 可与图像服务共物理服务器，但外部hostId保持契约 |
| RPT_INFO | ReportVersion | 保留报告发布/更正状态；医生院内码引用DOC_INFO |
| RPT_PATH_INFO | 报告快照Asset | PDF/图片必须有真实文件与已验证访问地址 |
| RPT_IMAGE | 报告插图Asset | 与WSI全片分开，不以缩略图代替原件 |
| SETL_INFO | Settlement | setlType保留本地/异地/自费语义；退费与删除不同 |
| SETL_DETAIL_INFO | FeeItem/申请关联 | 通过hospitalSetNo关联主结算，并携带applyNum；支持一结算多申请多费用 |
| APPLY_ITEM_INFO/DIC | 院内项目字典快照 | 输出视图名等待确认，不将医保项目编码重新塞回已删除字段 |
| DOC_INFO | 院内医生字典及医保码映射 | 院内医生ID与医保D码不同；HIS/PIS命名空间需要统一映射 |
| BOOK_INFO | 预约记录 | 数据源不存在时是否必交须平台确认，不造预约 |
| RPT_INFO_DETAILS | 结构化报告观察项 | 已有版本冲突，默认禁用、保留实现接口 |

### 5.2 禁止直接等同的字段

- hospitalCode与fixHospitalCode：社会信用/医疗机构编码与医保H码不同。
- cardType=01与病理索引证件代码：字典不同，不做字符串补零。
- patType与索引就诊类型：依据申请时实际就诊语义映射，不靠数值巧合。
- reportStatus、checkStatus、submission.state：临床状态、检查流程状态、交换状态不同。
- studyImageCount、seriesImageCount与玻片数量：文件/实例数与物理玻片数不同，多文件格式不能随意一文件算一片。
- sopInstanceUid用于非DICOM的外部兼容标识，不自动变成合法DICOM UID；真正DICOM UID须满足标准并与文件头一致。
- dep=GM是该PACS字典病理值，不应写成通用DICOM Modality；转换器按DICOM WSI要求设置真正的元数据。
- imageType=400表示其他并不表示对方接受任意KFB；接受性单独确认。

### 5.3 兼容序列和ID

原文允许NON-DICOM虚拟序列，但这是外部采集映射，不是把所有WSI伪装成有效DICOM。内部scan/bundle保持原样。若接收方要求严格DICOM，必须通过有效转换并校验真实UID、层级、像素和元数据，不把文件改后缀冒充DICOM。

外部ID使用持久映射表，遵守各字段长度和字符集，采用碰撞检测和稳定分配；不截断hash/UUID后不检测。相同源数据重放要得到相同外部ID，源内容更正是否沿用ID或发新ID由目标规则决定。

## 6. 视图发布与文件一致性

### 6.1 首选发布实现

1. 冻结package及目标投影，分配publication_id与generation_id。
2. 检查所有引用资产可用；如跨交换区，将文件复制至交换存储并校验。
3. 在交换库写PREPARING批次及完整投影，外部视图不可见。
4. 发布前执行跨表引用、计数、类型/长度、文件可达检查。
5. 在交换库单事务内标批次PUBLISHED并切换适用的当前版本指针；外部视图只读取已发布行。
6. 核心库通过幂等发布回执更新本地publication状态；失败时根据generation查询，不能重复生成版本。
7. 采集器读取视图/文件后记录访问证据，等待平台回执或对账结果。

视图从已经生成的投影表读取，不能实时跨HIS/PIS/存储拼接。减少源系统负载，保证输出可重放。

**事务边界的限制：** 交换库一次原子发布只能保证某一时刻所有行同时可见，不能保证采集器多次独立查询期间看到同一generation。应优先要求采集器使用一致性读取事务/批次令牌；旧采集器无此能力时，与平台约定父子重读机制、版本发布窗口和旧文件保留，必要时提供批次专属快照库。不得宣称只靠数据库事务就解决跨请求一致性。

### 6.2 增量、更正和墓碑

对于有lastChangeTime字段的表，出口时间应反映实际发布变更且不回拨，保存源变更时间和发布变更时间两种含义；最终给哪个值遵循平台确认。子表无时间戳时，确定父表触发重读或全量核对方式。

作废/删除通过disableFlag等约定输出墓碑，墓碑保留至少覆盖采集器离线与补读窗口，具体天数待确认。发布撤回不立即删除资产，否则旧视图记录会指向失效地址。退款用refundFlag及被退费流水号表达，不误用逻辑删除。

新版本替代旧版本时，外部ID是否复用、老行如何撤销、URL是否继续有效、采集失败如何补读必须进入Profile；默认生成待确认更正任务，不凭内部状态擅自替换平台记录。

### 6.3 权限和凭据

交换库账户只读批准视图，无核心库、HIS或系统表权限。原文含userName/userPassword字段，不意味着可把底层存储管理员密码写入视图。优先提供专用只读交换身份及HTTPS认证；若采集器强制读取凭据字段，使用最小权限专用账号、限定网络与导出范围、轮换审计并由医院确认。普通业务页面与日志不得显示这些列。

## 7. 主动API推送通用适配

配置请求方法、路径模板、字段/头映射、认证插件、签名顺序、编码、压缩、时间格式、成功判定、业务错误表、回执解析、超时、重试和大小限制。模板只引用白名单字段和secret_ref，不允许任意URL跳转或任意代码。

资产上传可能先于报文，也可能由报文返回上传凭据。Adapter使用prepare→transfer_assets→submit→query接口表达，不让流程引擎假设所有平台都先传文件。签名必须绑定实际发送字节；重试token变化与业务快照不变可以并存。

回执优先通过远端request_id/业务键对应submission。异步回调先持久化再应答；验签失败不推进状态。schema校验、HTTP成功、远端业务码成功和文件处理成功分层存储。

本地Mock包含：同步成功、异步受理、部分拒绝、超时但受理、重复回执、乱序回执、格式不支持、长期处理中、无回执能力。Mock成功不得打开真实生产Profile。

## 8. 通用性验证场景

新增 `metrics.monthly` 插件/Schema，导入CSV列“机构、月份、项目、数量、金额”，映射到JSON模拟API。不含患者、病理号、玻片、扫描仪。复用同一SourceConnector框架、Schema验证、DSL、Workflow、Submission、Receipt和UI。

验收标准：新增此场景只增加Schema/Mapping/Profile及必要的领域规则，不修改upload_session、blob、job、workflow_run核心结构，不在核心代码增加 `if domain == pathology`。病理专属UI仍作为插件页面注册，而不是所有数据包都跳病理详情。
