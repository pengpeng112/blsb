# 药品追溯第二轮详细完善计划

2026-10-17；承接[复核报告](REVIEW.md)R2-01—16。目标是在现有Python/Vue/PG架构内补齐真实链路，不重建骨架；开发、现场演练与真实业务批准分开验收。

## 1. 执行顺序和完成边界

| 阶段 | 工作包 | 必须交付 | 依赖/完成标准 |
|---|---|---|---|
| 0 | FIX2-00 | 认领范围、当前Git与配置基线、冻结旧证据、复现本包反例并转换为正确行为测试 | 不覆盖旧测试/源码；16反例可复现，无外连 |
| 1 | FIX2-01 | TOP请求/响应契约和状态修复 | R2-01/02/03；按官方契约的上传/查询/错误/部分成功fixture通过 |
| 2 | FIX2-02 | 生产门禁、配置与目标固定、Mock隔离、mTLS真实客户端 | R2-04/05/11/15；空/失效/范围不符全部拒绝，不能靠改bool开正式发送 |
| 3 | FIX2-03 | Oracle类型规范化、完整业务键、稳定分页/失败重取和视图契约revision6 | R2-07/08；真实DBAPI形态fake、复杂分页与恢复测试通过；活库签认仍等EXT-01/02 |
| 4 | FIX2-04 | worker执行时停发/暂停/批准复核、发送CAS、轮询代次、回执历史 | R2-06/09；并发/崩溃/撤销/重复任务测试通过，UNKNOWN不盲重发 |
| 5 | FIX2-05 | 平台侧查询快照与三方对账、部分成功明细、人工处置 | R2-10；远端多单/少单/差码/不可用均有明确结果，不可调用则未核验 |
| 6 | FIX2-06 | DMZ运行材料/服务兼容、严格转发边界、完整备份恢复 | R2-12/13/14；匹配OS的隔离安装及真实PG恢复证据 |
| 7 | FIX2-07 | tenant/owner约束与专项越权、父子码规则、UI/分页/输入修复 | R2-16及原BACKLOG小项；与前述权限/状态改动一起测试 |
| 8 | FIX2-08 | 相关局部→必要全量回归，浏览器闭环与发行、四类实施文档/状态/交接 | 报告真实结果；Mock不自造错误协议，新增边界测试不被skip掩盖 |
| 9 | SITE2-01 | 有维护窗口后升级<prod-host>、DMZ部署、真实只读预览与本地合成闭环 | 完成代码整改及相应EXT材料；停真实发送，不把合成单送生产 |
| 10 | LIVE2-01 | 首批允许单据→最终状态→三方核对→院方签认→扩大 | 所有必要门禁+明确发送授权；缺一项就停对应真实步骤 |

阶段1—8能在无真实患者数据情况下持续完成；外部材料缺失不能作为不修参数/状态/停发逻辑的理由。新SDK验证、OS材料等确实缺失时保留具体阻断，不伪造“全部完成”。

## 2. FIX2-01 官方协议与正确状态

文件：service.py build_biz_params/perform_query/_classify_query；protocol.py；client.py；contracts.py；对应测试和Mock。

- 建立两个独立请求schema与来源/字典映射：共同包含bill_code、bill_time、bill_type、ref_user_id、trace_codes；库存增加官方必需药品类型、收/发企业ent_id、client_type，按方向区分医院/供应商。内部ref_ent_id只在内部模型表达，上传字段用ref_user_id；query字段仍按其正式契约使用。warehouse、患者可选字段只在批准映射下转换到官方字段；_doc及内部supplier_ref不得直接透传。
- bill_time使用权威业务完成时间与明确GMT+8转换；TOP timestamp显式时区，不能依赖容器未验证的本地时区。所有数字串码/单号保持原值，数量定点规范化。绑定正式profile和不可变机构字典revision。
- 解析真实response_success/model/msg_code/msg_info/sub_msg_code/operation_info_map及error_response；统一正式成功/拒绝/不确定/部分结果，不能用success兜底制造兼容假象。官方样例裁剪为独立fixture，保留来源日期，避免同一Mock代码生成请求和预期。
- query提供ref_ent_id、begin_date/end_date（创建日）、bill_type、单号和完整分页；保存total_num、页级证据、查询时间/窗口；避免业务日期与上传创建日期混淆。边界/覆盖不明先扩大批准查询窗口，不扩大真实发送范围。
- 结果判定使用已批准状态映射revision：全部成功才CONFIRMED；部分成功到PARTIAL并保存明细，失败/处理中/未知/缺字段不确认为成功。result_type=3不能覆盖子状态52；未知数值转待核实。
- Mock返回相同正式envelope，覆盖受理、处理中、全部、部分、失败、错误页和丢响应；修正现有把3/52当确认的演示/断言，新增对应失败反例。状态枚举及限制以当期官方文档/平台确认为准。

验收FIX2-T01：两profile请求必填/字段名/方向正确，wire不带内部字段；T02：真实形状上传/查询/错误fixture；T03：部分/失败/缺失永不CONFIRMED；T04：分页、跨创建日/权限失败/查无记录可区分。

## 3. FIX2-02 接入门禁、版本、身份

文件：service evidence/build_client/prepare；payload lookup/freeze；plugin route_event；目标配置/SecretRef；transport；必要编号迁移。

- 以tenant+connector/机构+profile+destination+config_release+capability精确匹配证据，不能first取另一目标再判断；必需项由代码/批准profile固定，不信任用户可缩减的required_items。未知profile/capability不允许空集合批准。期限、撤销、审批身份与证据引用都校验，不能仅提供EXT键名就自动视为外部材料有效。
- send加入时钟就绪、历史/重复策略与明确首批授权（EXT-05/10/12）；发送批准保存机构/药房/业务/时间/单据或批量上限、签认人、有效期、停发状态。回执只读查询允许哪些权限应单独定义，避免为查清UNKNOWN被迫重新允许发送。
- 发布配置生成闭包：目标、API配置、医院/供应商/业务字典、证书与SecretRef版本、schema、协议/状态映射revision全部固定到Submission。旧任务不得读“最新”字典或destination当前配置；新配置需要新批准，旧配置撤销让旧任务停发。
- 重复prepare先找旧Submission并返回其真实PackageRevision；避免先建新包再返回旧提交。业务profile必须与destination完全匹配；多目标/无目标不first猜选，返回配置等待。
- Mock凭据与MockTransport强类型绑定；正式目标禁止mock:引用/__mock__值。SDK签名未验证时，真实client构建/发送入口必拒绝，不能只靠参数字符串前缀豁免。取得官方向量后按证据与固定适配器版本启用，禁止简单把常量改True。
- RealTransport支持受限客户端证书/私钥/CA路径只读挂载、load_cert_chain，构造时校验引用权限；证书到期与轮换。明确禁止重定向和任意URL；配置主机/路径白名单。实现准确连接/读取/总deadline，不把两个数字相加冒充分层。

验收FIX2-T05：不同目标/配置/审批范围/过期/撤销全部拒绝；T06：真实transport+mock引用拒绝且未发网络；T07：用应用RealTransport对本地mTLS转发器与本地模拟官方上游跑完整闭环，不使用测试自写HTTPS客户端替代；T08：配置改变后重启旧任务仍用原固定revision或因撤销停发。

## 4. FIX2-03 Oracle、来源契约和不漏数

文件：sources/db_view/drug_trace.py、ingest、quality、models；新建view-contract-r6，保留r5。

- DBAPI输出在唯一适配边界规范化：列名明确小写或带引号别名；datetime/DATE/TIMESTAMP、Decimal、NULL和Oracle空串采用可逆、确定、带时区口径的JSON规范；原始追溯码/键不丢前导零；状态按允许值域映射，禁止bool('0')。缺必需字段/未知值明确质量错误，不默认已完成。
- 每个connector用独立受限SecretRef/DSN/service/schema及对象白名单，不用全进程唯一DSN替代全部来源。遵守readonly登记和用户提供216受控接入方式；不从数据资产平台取写权限凭据绕过。调用前明确数据库readonly权限与对象范围。
- 真SQL与合成仓储使用同一套排序/过滤定义。游标逐字段保存updated_at、source_system、pharmacy、business_kind、source_bill_id，SQL使用等值前缀展开比较，ORDER BY完全一致；不拼字符串比较。header和codes都按完整复合键过滤。
- 增加游标严格推进、重复页、最大页/时间预算保护；防止源错误导致死循环。时间上界固定，下轮回看清空旧tie-key。迟到/失败/持续变更单持久化重取任务/隔离账本；未安全落库不能仅依靠10分钟回看后永久跨过。配置历史补报与持续增量独立checkpoint，避免旧历史窗口被较新游标跳过。
- 稳定来源身份使用source_bill_id并含tenant/connector/source_system/药房/业务；source_bill_no仅展示。检查已有009数据冲突后用010及后续空闲迁移保数据调整唯一约束/复合外键，不编辑已执行009原文。
- r6草案补齐实际SELECT涉及的全部字段，包括可选字段的显式有类型NULL列；不能声明可选却SQL固定读不存在列。确认DATE与TIMESTAMP格式兼容、NULL MODIFY_TIME回退和所有明细变更传播。原MED_TRACE_RECORD SQL的MAX/LISTAGG问题和F/S/T业务签认继续保留，不猜TRACE_BILLNO关系。

验收FIX2-T09：fake DBAPI大写+datetime/Decimal/0/1/NULL，全部能够真实导入而非仅单独测render_sql；T10：同刻>两页、不同来源/药房前缀、重复页和非推进游标；T11：历史回补/迟到/持续变更失败重取和断点崩溃；T12：跨来源同展示单号不合并。活库小范围验证等EXT-01/02，不执行HIS DDL。

## 5. FIX2-04 停发、状态机、任务与回执

- worker开始前检查当前run状态/批准/证据/来源连接器启用状态；暂停或撤销不构造真实连接；循环页/每单/发送前再次复核，执行中撤销在安全点停止并保留checkpoint。不得无条件PAUSED→RUNNING；已取消任务保持终态。
- send服务统一状态转换：只有合法PREPARED且来源/质量/配置仍有效才能建立发送尝试；SENDING/UNKNOWN只查询，CONFIRMED不发送，PARTIAL按逐项批准处置。禁止其他通用入口绕过（core_handlers、手工job、重放工作流均覆盖）。
- claim到网络副作用之间采用Submission版本/CAS和lease epoch，禁止在handler无fencing直接覆盖结果。发送前持久SENDING后任何异常（包括签名/证书/SDK构造异常）留可恢复尝试；区别明确未发出与结果未知。失租worker不得写覆盖新状态，更不能开启第二次无判定外发。
- dry-run在任何client/网络/业务写入之前分支返回；不增加DeliveryAttempt、不改变Submission状态。需留预览审计则仅记录明确无业务副作用的预览记录。
- 轮询有generation/cursor/available_at；处理中排下一代并退避，手工重查创建明确新查询代次，已完成query job不吞掉后续查询。设置次数/总时限/告警，不无限poll。所有页读完再判结果。
- 回执按远端receipt/event或结果digest+观察时间/查询代次唯一，保留从受理到终态的每次有效变化。不能固定`:1`导致仅第一条有证据；重复同证据幂等、乱序不回退确认、差异能审计，敏感原始数据受限保留不只存keys/size。

验收FIX2-T13：排队后撤销、暂停后worker、采集一半撤销；T14：UNKNOWN/SENDING/CONFIRMED多入口禁重发；T15：双worker/失租/签名异常/发后崩溃，DB内外状态可恢复；T16：dry-run业务记录和外连均零；T17：处理中→再次查询→终态、手工第三次查询可执行，回执至少保留实际状态变化。

## 6. FIX2-05 真正的对账与处置

- 按固定tenant/connector/profile/destination/机构/药房/业务与时间窗口读取三方数据：来源当前及需核版本、本地提交、独立平台查询快照。远端适配器提供分页完整性与能力声明；查询失败、不支持、范围不完整为未核验，不能matched。
- 区分源本地差异、远端缺/多/重复、状态冲突、逐码差异、部分成功与不可核验。平台成功状态不等于本地宣告成功；用独立远端证据交叉检查。
- profile严格过滤，版本选择规则明确（不可每个旧版本都要求CONFIRMED）；Submission精确限定目标。审计范围与实际查询范围一致。对账任务dedup键包含窗口/药房/业务/代次，不能不同范围都复用同一个已完成任务。
- 没有码级接口先完成真实单据级对账，输出code_level_verified=false；独立测试证明尚未核码。码级插件可用模拟实现与明确unsupported，不伪造实际平台能力。
- 补报前独立查询原平台单号；UNKNOWN/权限失败不作未发送。部分成功只对失败项按官方允许方式处置；更正、作废与新版本先业务审核，不能因为source_revision变了就复用相同bill_code自动重新发。

验收FIX2-T18：本地CONFIRMED但平台无单/部分/失败不得匹配；T19：两profile/双目标/多旧版本的范围不串；T20：总数相等但不同单号/码集合、远端额外单据、超页/缺页、权限拒绝均有差异或未核验；T21：差异处置理由、审批范围、重新对账闭环。

## 7. FIX2-06 DMZ及运维闭环

- 统一转发器代码实际最低Python/ssl能力与安装器/README；选院方允许且有维护支持的运行版本，离线包匹配Anolis7.9及CPU/glibc/OpenSSL/CA，不用未经验证el8包当el7材料。记录版本/hash/依赖/许可证，禁止现场联网碰运气安装。
- 检查systemd实际版本与安全指令支持；普通服务用户监听443需明确低端口能力或院方认可的高位端口/前层转发方案。密钥/配置不归服务进程可写；代码/证书版本化、健康校验失败退出非零、升级失败可回旧版。维护既有8001。
- 实现TOP方法白名单（不是只限制HTTP POST），拒绝重复/歧义method、未知Content-Type、任意上游/重定向；验证不修改签名字节。设置下游握手/读体deadline、并发前置限制、响应体上限、连接释放finally；慢连接不能无限占线程。原字节与无重试要求保留。
- 备份覆盖实际所有写入口：经维护门禁阻止新写、排空、停或只读化storage/exchange/worker/core必要写者，再快照；状态恢复trap只恢复原运行服务。创建敏感文件前umask/权限，失败产物标不完整；实际OBJ_DIR自定义路径正确归档。
- restore校验独立项目名不可等生产名、最终实际生产路径相等/祖先/子孙关系和符号链接；从runtime.env读取自定义生产目录；--validate-only解释为不改业务/目标数据，临时目录行为如实记录。验证备份内必须项全被manifest覆盖。
- 真实隔离栈做pg_dump→pg_restore、exchange与合成对象校验、配置/令牌一致、迁移/worker/读接口、任务续跑与禁止重发；不能用fake docker或仅迁移PG测试替代。升级时现网旧脚本与新脚本顺序明确，先离线验证新backup可用于旧发行，再切换，不能只口头“用新脚本”。

验收FIX2-T22：同OS/ABI环境实际systemd启停、服务用户绑定和可信mTLS、回退；T23：未知TOP方法/慢读/大响应/上游断连/并发限制；T24：storage仍写时备份拒绝或能真正冻结，含对象的多存储一致；T25：完整真实PG恢复、错项目/自定义路径/越界拒绝，生产字节不变。

## 8. FIX2-07/08 权限、UI、验证与交付

- 为药品父子表增加tenant复合关系/唯一约束（编号迁移），有历史坏数据则隔离报告不默删。API、服务、job三层都验证tenant/owner/connector/destination归属；SecretRef、证据批准、来源读取不能靠payload传tenant完成隔离。
- 质量校验未知业务一律等待，父子包装同单阻断，空明细ID/码/非法状态均明确；数量/拆零与患者/就诊最小字段按批准字典，不能只是警告后发送。
- Vue显示平台真实状态、未核验对账层级、证据/授权范围、当前运行版本；修登录地址时序，补错误提示、keyset分页/状态筛选、范围展示、限流与批量上限；overview仅药品领域。Mock/Dry-run/正式持续显著区分。
- 新增FIX2-T01—T29的独立测试，再运行相关原测试和必要全量回归；新增正确断言，不把旧错误契约当兼容要求。原测试因环境缺bash/OpenSSL时先使用本机既有Git Bash，缺材料只阻断对应验证。
- 发行包含迁移/新插件、正式运行依赖、前端、DMZ材料与校验/恢复说明；白名单与镜像上下文排除真实库、凭据、私钥、患者数据、备份；manifest+源码/镜像/schema/config revision可追溯。

验收FIX2-T26：A15扩展跨tenant/owner/connector的列表/直读/prepare/send/query/reconcile/evidence/SecretRef；T27：父子码、未知分类及输入时间/上限；T28：真实形状Mock浏览器全链路含部分、未知、重查、撤销、对账未核验；T29：发行与旧版回退、四类实施文档/API矩阵/共享交接一致。

## 9. 现场和首批真实接入

1. 先取得EXT-01/02及批准的只读小范围，通过院方受控路线验证service/schema、字段、稳定主单/明细、更新/退药/作废；视图DDL由院方执行。保存脱敏对照证据，禁止默认扫描全历史。
2. EXT-03/04/05/11齐备后安排维护窗口，备份恢复演练通过，再升级<prod-host>/部署DMZ。记录实际发行、插件集与schema；禁发状态下用本地模拟上游做现场合成闭环，不把合成单交正式平台。
3. EXT-06/07/08/09具备后先完成身份与只读查询，SDK向量一致和必要能力验证；不以修改SIGNATURE_VERIFIED绕过。无法提供的平台查询能力保留未核验，不强行关闭EXT。
4. EXT-10/12明确首次机构/业务/药房/日期/单据/批量，先核既往已上报及单号规则；发送一批后等待最终状态和院方核对，再批准下一批。不自动用历史SQL从7月扫到10月全部补报。
5. 停发机制实际演练：撤销授权后未开始任务拒绝、在途任务记录UNKNOWN并只查询、暂停源停止后续读取、关闭插件停止调度；停发不意味着撤销已被平台受理的数据。

## 10. 收尾必须报告

逐条R2问题处置（代码路径/正确行为/测试证据/未决限制）、FIX2工作包状态、真实命令/退出码与XML/log、OS与mTLS集成/真实PG恢复/浏览器证据、发行包manifest和回退步骤、外部EXT剩余清单。分别报告“离线代码完成”“现场验收通过”“正式业务已签认”，不可用一个W01—W09全部完成覆盖三者。
