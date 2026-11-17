# 下一AI长任务：药品追溯真实接入与首张上传

任务包：DTR-FIRSTBILL。编制日期：2026-10-21。工作区：`F:\python\blyb`。
本文件是可直接交给下一AI的执行提示词。**本次仅编制任务，不代表下列工作已执行或获验收。**

## 直接执行要求

你接手现有医院上报系统，目标是：在保留病理等现有功能的基础上，完成药品追溯剩余开发、
真实配置和必要部署；具备真实输入及有效授权时，由<prod-host>正式应用链路上传**1张完整且确认未报的单据**，
查询最终处理结果并核对。如果外部条件未具备，把全部不依赖这些条件的工作做完，留下可以继续执行的交付物。

用户已反复表达真实测试推送意图；不用再泛问“要不要继续开发/是否愿意测试”，不用重新索要已保存密钥。
但不要把聊天意图当成未提供的单据范围、业务口径、DBA写入批准、有效维护窗口或院方签认。
需要某项缺失输入时，明确缺什么、谁提供、为什么影响该步，同时继续完成其他工作。

不要只输出建议或又写一版计划；请实际读代码、修复、测试、打包，并在现有有效现场委托范围内落地。
不要重搭骨架、替换框架、拆成多套新项目。默认顺序执行，不自动开启多个子代理。

## 一、接手入口与当前事实

1. 从仓库根目录读取 `AGENTS.md`、`docs/ai/START_HERE.md`、`STATE.md`、`HANDOFF.md`、`BACKLOG.md`。
   修改 implementation 前再读其适用的局部约定。
2. 读取本目录 [最新现场复核](README.md) 和 evidence/target.json、dmz.json、followup.json，
   再读[上一棒安装报告](../../deployment/drug-trace-install-20261021/EXECUTION-REPORT.md)。
3. 运行 `python scripts/ai_session.py status`；无重叠活动任务后登记实际工具/模型及真实修改范围。
   模型未知写 unknown，不猜测。任务建议 DTR-FIRSTBILL-EXEC-<实际日期>，不要复用已结束session。
4. 记录 `git status --short`，保护已有未提交改动；先保留受限、可恢复基线及哈希，再做增量修改。
   不能 reset/clean、清运行库或覆盖其他AI的未提交成果。

截至2026-10-21 23:29现场事实如下，接手时先做轻量复核；新证据优先，历史文件不改写：

| 对象 | 当前状态 |
|---|---|
| <prod-host>应用 | `/opt/blyb/releases/dpl-20261021T111000Z`；6容器healthy；迁移001—011 |
| DMZ应用 | `/opt/blyb-dmz/releases/20261021T113750Z`；Anolis7.9、systemd219、私有Python3.11 |
| 应用入口 | `https://<prod-host>/ui/`；后端8000/8001/8002不发布宿主机 |
| 网络 | <prod-host>→DMZ443 TCP及worker严格mTLS health200；DMZ→eco.taobao.com443 HTTPS HEAD200；<prod-host>→HIS1521 TCP通 |
| worker | UID10001；Oracle Thick19.32；AppSecret已注入；client.key可读不可写 |
| 来源 | `his-drugtrace` DRAFT，无active config；worker Oracle DSN为空 |
| 目的地 | `alihealth-drugtrace-retail` Mock DRAFT，config_revision=null；destination配置revision为0 |
| 字典/证据/数据 | 三字典draft-1；生产证据0；单据/采集/配额/submission均0 |
| 签名资格 | 已部署 `SIGNATURE_VERIFIED=False`，真实transport调用会被CapabilityError拦截 |
| DMZ允许方法 | 只有query.billstatus；没有uploadretail；库存接口关闭 |
| 证书/时钟 | pki-r3 TEST-ONLY链；DMZ慢约32分钟，NTP未同步 |

不能继续用“前置机尚未部署/443一定不通”的旧结论；也不能把本表当成你执行当时的实时监控。
本期范围固定为**零售/患者发药 `uploadretail` + 状态查询**。
历史提到的 `AlibabaAlihealthDrugtraceTopYljgUploadinoutbillRequest` 属于另一个库存出入库场景，
不因此扩大本期范围，不启用库存上传。

## 二、服务器、凭据、资料入口

| 用途 | 地址 | 接入原则 |
|---|---|---|
| 内网上报服务 | <prod-host>:22 | 既有SSH跳板/固定主机指纹；不重装系统 |
| SSH跳板 | <frontend-host>4:<ssh-port> | 现有普通二跳方式；不假定支持direct-tcpip |
| DMZ转发服务 | <relay-net-host>:22，应用443 | 保留现有8001 Java服务；不得占用/杀掉它 |
| HIS | <his-host>:1521 | 历史活库服务名his，接手须核对登记；生产只读来源另行验收 |
| 数据资产入口 | <frontend-host>3:22 | 既有SSH密钥、sjzc统一只读通道 |

- SSH本机受限记录在 `.ai/private/reporting-20261015/`：DPAPI clixml、known_hosts和目标指纹。
  复用 `docs/deployment/reporting-20261015/deploy_remote.py` 的严格跳板/普通二跳实现，
  及 `docs/deployment/drug-trace-install-20261021/deploy_dmz.py` 的固定DMZ指纹连接逻辑。
  **不要直接运行旧部署驱动的所有动作**；先读源代码，区分只读和会备份/停服务/写配置的动作。
- 平台AppKey/AppSecret/ref_ent_id/医院名称、后续查询到的ent_id已保存。
  使用[凭据入口说明](../platform-credentials-20261020/README.md)；AppSecret通过
  `.ai/private/alihealth-drugtrace-20261020/with-credentials.ps1` 注入授权子进程。
  DPAPI受Windows用户绑定，不复制给Linux解密，不在命令行、日志、Markdown、Git、镜像和DMZ放明文。
- AppSecret已存在worker；接手检查只输出存在性，不重复导出整个环境。
  需要授权session时沿用SecretRef；明确医院模式无需session时允许缺省，不强行索取。
- HIS遵守[数据库入口](../../startup/DATABASE_ACCESS.md)。实际查询前完整阅读
  `C:\Users\Administrator\.zcode\skills\sjzc\SKILL.md`、适用源技能及数据资产AGENTS。
  服务器托管数据库凭据禁止读取/打印/复制到<prod-host>；生产绑定由管理员受控交付，不能拿高权限账号绕过。
- 源登记 `his_source_<his_host_id>` 的历史write_policy为medical_dict_push；不得自行改成readonly。
  登记不满足策略时仅阻断live，继续做代码/合成/审批材料；上次临时例外不自动长期继承。
- 官方资料、院方需求单在 `数据上报/1、码上放心平台/`；当期官方指南链接见既有文档。
  解决接口冲突时查官方当前资料并留版本/日期，不把别的AI复述当官方批准。

## 三、任务分阶段执行

### FB-00：冻结基线与可复现现场快照

保存应用/DMZ实际发行、容器健康/重启数、迁移step/name、允许方法、配置状态和布尔存在性。
只取必要字段，不做docker inspect全量输出，不回显runtime.env或任何私钥。
新建本任务evidence目录；历史探针采用排他创建，不能删旧文件以便重跑或覆盖旧失败。

对两机配置、证书和发行的修改前都要有受限备份及恢复路径。备份需确认范围和空间，
`backup.sh --with-objects`会短停多个写入口，须沿已有有效维护窗口执行；不把它称只读命令。
当前已存在备份20261021T111644Z仅是历史基线，不能覆盖之后新增数据。

产物：现场快照、变更清单、停止条件、回退步骤，明确哪些已实测、哪些未知。

### FB-01：修复动态就绪判断与操作工具

重点检查 `implementation/apps/init_drug_trace.py`、领域routes/service、UI readiness展现。
目前init工具SOURCE/PLATFORM/SEND_SCOPE含固定NOT_READY文案，API与CLI判定也有不同口径。

要求：

- 抽取/复用当前有效证据和配置检查，避免CLI、API、UI各维护一套互相矛盾的判定。
- 指定tenant、profile、destination、config release，不能多租户静默取第一条；
  对同profile多目的地不得任意first导致证据串用，选择不明确必须提示。
- Mock/DRAFT、缺active config、签名未验、证据缺项/过期/撤销、配置换代必须如实显示。
- CODE_READY/LOCAL_SERVICE_READY只是对应层状态；不能据此返回可真实发送。
- 网络未知或旧探针不能自动变绿。若持久保存探针结果，绑定源进程、目标、检查方式、时间/有效期，
  不新增无人维护的“永久网络已通”布尔常量；可明确展示外部实测而应用层UNKNOWN。
- 不因readiness查看自动调用HIS、重建草稿、刷新凭据、发POST或批准证据。
- 如果缺安全发布/预览脚本，可增量补充幂等工具：默认check/dry-run，apply必须明确tenant及范围，
  从受限输入生成配置，按现有API发布，不能直接SQL UPDATE生产状态。

验收包含真实配置状态变化、Mock、证据过期/撤销/换代、跨租户/目的地、dry-run零副作用；
不要只测固定字符串输出。

### FB-02：完成签名资格与官方契约核对

沿用现有 `implementation/plugins/destinations/alihealth_drugtrace/` 适配器。
已有空字段签名修复、官方C#参考及独立真实只读查询证据，先读
[历史联调记录](../platform-live-test-20261020/README.md)，不能因历史样例文字摘要冲突把算法改错。

1. 找到适用TOP官方SDK/官方签名规范与可核验向量，记录来源URL、语言/版本、文件哈希、适用方法。
   必要下载只在允许联网开发环境受控取得；不在线改生产依赖、不引入整个新框架。
2. 独立对照排序、UTF-8中文、空值过滤、可选session、百分号编码、时间戳；区分原始参数签名和表单编码。
   对照数据使用公开/合成参数或受控内存密钥，不把真实Secret写进测试夹具。
3. 适配器真实签名资格必须关联可审查证据。若用官方源码提取+独立向量等替代SDK验收，
   写明依据和采用批准；不能宣称“官方SDK二进制对照通过”。
4. 证据充分后才处理签名资格代码与发行；禁止只改SIGNATURE_VERIFIED=True、加force发送、
   用Mock密钥前缀绕过、删除关键断言。未通过资格的真实client继续拒绝。
5. 核实当期零售bill_type、query.billstatus result_type/sub_process_flag、日期语义、分页、终态。
   321/323、0/1/3/4、51/52等数值必须附官方当期证据/批准revision，不沿聊天推断。
6. 状态查询成功不能证明uploadretail有权限；上传方法权限、服务有效期、医院绑定及自用/ISV模式分别确认。
   不用空单/假追溯码往正式平台测试权限，不默认有沙箱。

产物：签名资格对照报告、关键向量测试、接口口径差异表、已确认与待确认字典。
查不到官方批准口径则保留该项阻断，其余开发不停止。

### FB-03：生产HIS来源与整单采集

读取[活库核查事实](../his-live-20261019/README.md)、
[r7契约](../../deployment/reporting-20261015/view-contract-r7.md)与
[r7 SQL目录](../../deployment/reporting-20261015/sql-r7/)。

已核实主/明细/扫码三表路径：PHARMACY.DRUG_PRESC_MASTER、PHARMACY.DRUG_PRESC_DETAIL、
DRUG_USER.MED_TRACE_RECORD。原用户LISTAGG+MAX查询不作为完整上报明细，不能恢复为该聚合。

1. 先确认现有两视图是否已由DBA建立及只读账号是否已批准；先查登记/受限元数据，
   不以工作区有DDL文件推定活库已存在。HIS live仅按技能与当次具体授权、策略执行限量SELECT。
2. 缺视图/授权时，向DBA交付10-create-views.sql、20-grants.sql、30-verify.sql的审阅版和回退说明。
   原r7保留，新修订新建目录。**不得自行在HIS执行CREATE/GRANT/索引/存储过程**；
   此类动作须用户逐项点名批准并走既定写入审批。
3. 明确actual owner/synonym/service；Oracle11g使用ROWNUM，Thick19.32已安装，无需重装。
4. 确认真实主单粒度、药房隔离、秒级PRESC_DATE、行与码关联、前导零、空值、去重/重复扫描语义。
   不TRUNC键、不把编码转数字；TRACES_SUM不能当真实码数。
5. business_kind/finalized/is_void/expected_code_count及拆零数量等r7候选留空字段，
   只有确认业务规则后才在现有business_rules层映射；未批准保持质量等待。
   不凭OUT或FLAG猜全部门诊/住院/退药状态。
6. 首张优先选已确认业务范围内的完整非拆零单；若单内含未支持分支，整单等待，不丢困难行后上传其余行。
   药房、窗口、处方键、全部码、数量单位与平台口径须能对应核对。
7. 先使用显式批次模式：业务键/小窗口→读取整单→全部码→首尾revision复核→持久化→质量检查。
   大表不能全期扫描，MED_TRACE_RECORD历史统计839万行且无时间索引；limit不能代替索引边界。
8. 持续增量需另证实可靠、可索引的变更轴，覆盖旧单补/删码、退药、无码转有码。
   无变更源时保持AUTO_PRODUCTION关闭，首张批次仍可独立完成；不把发药时间当永久变更时间。
9. 生产受限只读绑定、视图核验和规则批准后，新建SourceProfile及ConfigRevision，
   不给r7草案改标签冒充活库验收，不篡改冻结revision。

真实业务行仅在受控运行环境处理；聊天/共享报告只留脱敏摘要及核对结果，
不打印患者信息、原始单号/追溯码。只读HIS不等于不能写<prod-host>自己的采集库；两者证据分开。

### FB-04：校时与正式证书

1. 从现有登记/运维说明查院内NTP源，找不到列为院方输入；不要猜公共NTP或盲目改全局DNS。
   先诊断连通/现有时钟服务，确认有效维护窗口再做时钟调整，记录偏差与同步状态。
2. Anolis7.9/systemd219使用实际兼容命令；`timedatectl show`不兼容时用status，
   不把命令报错当“未安装服务”，不只看NTP enabled=yes就认同步。
3. 取得院方正式信任链，明确服务端SAN和serverAuth、worker clientAuth、运维探针独立clientAuth。
   通过IP访问时证书须包含实际IP SAN；不启用-k、CERT_NONE或跳过主机名验证。
4. 两机协调轮换：保留旧受限配置，先建立合适信任，再切新身份、验链，最后移除旧信任；
   验证<prod-host> worker UID10001可读不可写，证书/key只读挂载，私钥不进入诊断输出。
5. 验证有证书health200、无证书被拒、错SAN/不受信CA拒绝、重启后仍保持。
   没有正式链就保留TEST-ONLY标记和真实发送阻断，不能换一个自签名字就宣布正式证书完成。

先前TEST-ONLY client.key曾进入诊断输出，已由上棒全链轮换为pki-r3；不要重放旧日志或旧私钥。
证书回溯签发只是历史处理，不得用它替代校时验收。

### FB-05：发布真实配置、最小证据与正式链路只读查询

1. 复用现有租户first-hospital及对象，不创建重复租户/目的地。先查询再幂等更新。
   目的地保留历史Mock修订，在现有模型支持范围内发布新的真实配置，不原地篡改已冻结历史。
2. 真实连接器绑定已验SourceProfile；真实目的地绑定对应profile、AppKey和
   `env:BLYB_ALIHEALTH_APP_SECRET`，mTLS目标为https://<relay-net-host>:443及当前正式证书路径。
   Secret只在worker。医院ref_ent_id、ent_id不能混用，不给retail强加库存供应商字段。
3. 通过现有配置API创建、校验、发布ConfigRevision/ConfigRelease、批准字典；
   先检查当前API schema/权限，不凭本任务书杜撰路由、字段，不直接改库状态为PUBLISHED/APPROVED。
4. `POST /api/v1/drug-trace/evidence`只是证据登记入口，不是生成批准事实的工具。
   依据当前service.REQUIRED_EVIDENCE逐项提供真实证据；保留其固定必需项，不减少集合：
   collect=EXT-01/02；send=EXT-03/04/05/06/07/08/10/12；query及reconcile=EXT-09。
   每项关联tenant/profile/destination/config_release、提供者/时间/有效期及证据引用。
5. **先只开query能力，send仍关闭。** DMZ白名单继续仅query.billstatus；从真实worker、
   真实client、有效配置和独立query证据，经mTLS→DMZ→平台做固定只读诊断。
   可使用独特的非真实单号避免取出患者业务；timestamp使用校准时间，时区/日期选择依已验契约。
6. 记录平台业务结果、签名/权限/网络分类，响应脱敏；真实query成功且total0只说明该查询成功，
   不能认上传权限或首张数据已上报。Windows/DMZ curl旁路只能当诊断，不能代替本步骤。
7. 生效配置/证书/签名适配器有变化时，复核证据绑定与失效规则；重启worker确认真正生效。

### FB-06：首张真实上传与结果核对

只有来源、签名、证书、时钟、字典、权限、query以及send所有必需证据满足时执行：

1. 明确**1张完整且确认未报**的单据。把医院/药房/业务分类、日期窗口、单据受控引用、
   profile、destination、config release及max_bills=1登记为首批scope。
   既往其他厂商是否报过未明确时，不假定“没有”；让院方核对并留下证据。
   用户已有单笔测试意图，但缺具体范围必须补齐，不扩大成旧SQL的多月历史批量。
2. 只采该整单，做冻结payload预览、完整码数量/数量单位/前导零/重复码/关联核验、源revision复核；
   dry-run必须零真实业务POST、零平台配额消耗。不用假码试传、不随意取第一行代替一单。
3. 确认旧病理任务和Java8001不受影响；暂停本任务自动批量，保留止发路径。
4. DMZ允许方法只新增 `alibaba.alihealth.drugtrace.top.yljg.uploadretail`，保留query，
   enable_inout_upload仍false；<prod-host>源IP白名单保留，不能开任意URL/方法/公网通用代理。
   做配置校验、必要重载、只读自检，失败立即回受限配置。
5. 通过正式应用服务发送一次，保留请求业务快照哈希/幂等键、submission/attempt和脱敏回执。
   配额并发预留/消费必须不超过1；不从本机脚本或DMZ curl绕过系统直接上传。
6. 按已确认查询日期语义查最终状态；HTTP受理/上传成功阶段不能当处理成功。
   对处理中、失败、部分成功、UNKNOWN/网络中断分类；未知先查不盲重发，不改bill_code规避去重。
7. 最终整单结果与本地冻结快照核对；有官方逐码查询时按授权核码。
   缺码级/全范围接口时明确bill-level已核、code-level未核、remote_extra_checked=False；
   不把单据成功冒充全部码/历史外部多报都已对账。
8. 首张结束后保持max_bills=1/发送额度耗尽或撤销本次发送授权，后续只读查询独立可用。
   不自动扩大药房/日期、启动持续生产或补发历史。

### FB-07：本地验证、打包、现场升级与交接

- 修复后按影响跑有意义局部检查，重点是未批准必拒、已批准准确放行、租户/owner隔离、
  配置换代/撤销、Mock隔离、UNKNOWN禁止重发、首单并发配额、整单采集与历史日期查询。
- 局部通过后做必要全量：implementation下 `python -m pytest tests -q -ra`；UI有改动才运行
  apps/admin_web现有依赖的 `npm run build`。不靠删断言/xfail/改成错误预期造绿。
- 上棒全量380 passed/7 skipped/2 failed，修补fake后局部56通过，完整未重跑；
  此次留下新的log/XML、命令/退出码，实际失败/跳过原因单列，不能沿旧通过数报告。
- 应用/DMZ代码改动必须生成**新release id**、manifest/hash和依赖记录；不原地改已发布目录。
  保留BLYB_DRUGTRACE_ENABLED统一compose覆盖闭包，backup/status/install/rollback不能丢药品覆盖。
- 维护窗口内做受限一致性备份、哈希校验、安装、迁移和真实进程后验；不擅自重装驱动或DB。
  新DB变化用下一未占用编号迁移，先核对台账，不假定012一定空闲。
- 回退保留旧发行、runtime.env受限副本和数据；数据库不逆向删表。
  DMZ旧previous=110326Z有systemctl --value兼容缺口，先核实再用，不能盲目自动rollback。
- 同步四类实施文档：实施状态矩阵、接口与设计差异、测试执行记录、外部阻断清单；
  API/流程变更同步现有API矩阵/架构说明。STATE/BACKLOG/HANDOFF只写真实当前摘要。
- 一个任务一个session，长任务分阶段checkpoint，结束后end关联真实交接；
  有外部阻断的业务步骤标BLOCKED，不把整个未完成推送标完成。

## 四、验收矩阵（必须逐项回填证据）

采用独立前缀FB-A，避免与EXT/B01—B09混号。状态仅PASS/FAIL/BLOCKED/NOT_RUN，
每项写证据路径、命令/退出码或批准来源；“代码已写”不能填现场PASS。

| 编号 | 验收内容 |
|---|---|
| FB-A01 | 接手/登记/基线留存，已有改动未丢失 |
| FB-A02 | 当前两机发行、6容器、迁移及端口复核 |
| FB-A03 | CLI/API/UI就绪判断同源，DRAFT/Mock不能显示可发 |
| FB-A04 | 租户/目的地/配置精确绑定，撤销/过期/换代立即失效 |
| FB-A05 | check/dry-run无采集发布、业务POST及证据批准副作用 |
| FB-A06 | 官方签名向量/SDK或获准替代证据完整，空值/中文/session通过 |
| FB-A07 | 无签名资格真实client继续拒绝，获验修订可审查 |
| FB-A08 | 上传权限/医院绑定/有效期/授权模式分别确认 |
| FB-A09 | bill_type/状态枚举/日期/分页字典有官方当期批准依据 |
| FB-A10 | HIS生产受限只读登记和服务名/schema核实，无绕过策略 |
| FB-A11 | 两视图实际授权及字段/键核验，DDL由DBA按授权落档 |
| FB-A12 | 完整单据采集、全码/数量/拆零/完成状态与规则一致 |
| FB-A13 | 首尾revision变更/重取/幂等与药房隔离通过 |
| FB-A14 | 无可靠变更源时持续增量不误启用 |
| FB-A15 | 两机校时/同步状态和偏差受控，有真实时间源依据 |
| FB-A16 | 正式CA/SAN/EKU/mTLS、无证书拒绝、密钥UID权限及重启保持 |
| FB-A17 | 真实SourceProfile/ConfigRevision/ConfigRelease/字典发布且无Secret明文 |
| FB-A18 | collect/query/send/reconcile证据按必需集合分别登记并校验 |
| FB-A19 | 真实worker→mTLS DMZ→官方签名只读查询通过 |
| FB-A20 | 原始未报核对及明确单据/药房/时间scope，max_bills=1 |
| FB-A21 | DMZ只加retail、不加库存/任意转发，配置有效且可回受限态 |
| FB-A22 | 正式应用上传一次，冻结快照/审计/幂等/配额无重复 |
| FB-A23 | 查询到最终状态；部分成功/未知不误确认、不自动重发 |
| FB-A24 | 对账演练，整单/逐码/外部多报覆盖范围明确 |
| FB-A25 | 停发后发送拒绝、授权范围内查询可继续，额度未扩大 |
| FB-A26 | 相关局部/必要全量/前端构建有实际证据，失败跳过如实说明 |
| FB-A27 | 新发行哈希、备份校验/恢复只读演练、回退资料及旧功能健康 |
| FB-A28 | 凭据/私钥/患者数据未进Git、镜像、公开日志及交付文档 |
| FB-A29 | 四类文档及共享交接、任务登记准确收尾 |

## 五、外部输入回执（先查既有记录，缺项才问）

为每项标“已有且有效/已找到待核/待提供/不适用及依据”；不只列OPEN不说明责任方。

| 输入 | 由谁提供或确认 | 本任务需要的具体内容 |
|---|---|---|
| HIS生产只读 | DBA/数据资产管理员 | 登记只读策略、actual service/schema、两视图SELECT授权、凭据受控交付方式 |
| r7视图实施 | DBA | 审阅/执行revision、时间、核验结果及回退说明；未执行不得宣称存在 |
| 业务规则 | 药学部/HIS负责人 | 首期药房、业务分类、完成/作废/退药/拆零、码数量单位规则 |
| NTP | 网络/运维 | 可达院内时间源、现有服务及调整窗口；不能只给“网络通了” |
| 正式证书 | 院方运维/CA | CA链、服务端SAN/EKU、客户端/探针身份及私钥受控交付 |
| 平台权限 | 平台管理员/接口支持 | uploadretail权限、医院绑定、有效期、自用/ISV及session要求 |
| 官方契约/签名 | 平台接口支持 | SDK/签名验收依据、321/323及查询日期/状态/分页正式口径 |
| 首张未报范围 | 药学部/原厂商/院方 | 受控单据引用、药房/业务/窗口、全码完整性、既往是否已报、max_bills=1 |
| 维护窗口 | 服务负责人 | 如需停写备份/升级/重启/校时，确认现有委托是否覆盖当前时段 |

外部输入缺失时：先完成FB-00/01、可用的FB-02签名测试、配置预览、合成回归、
发行/安装演练及回执表；把最后一个依赖点保留为BLOCKED。不要因缺正式CA就停止修动态readiness，
也不要因代码可开发就跳过真实上报的证据门禁。

## 六、强制交付物与最终汇报

在你登记的任务目录交付：

1. `EXECUTION-REPORT.md`：FB-00—07完成情况，真实执行与未测分开，实际上传笔数及最终结果。
2. `ACCEPTANCE.md`：FB-A01—29逐项状态及证据；不能只写“全部开发完成”。
3. `EXTERNAL-RECEIPT.md`：上述输入表回填、责任方、具体请求、阻断哪项和可继续做什么。
4. `FIRST-BILL-RESULT.md`：已上传则记录受控引用/哈希、实际方法/配置revision、时间/回执/终态/
   对账覆盖/配额；未上传明确0笔及阻断原因，不伪造样例成功回执。
5. `OPERATIONS.md`：真实配置发布、证书轮换、只读联调、手动批次、停发/回退命令与副作用，
   命令先按实际参数核验；不出现密码/Secret/私钥/原始患者及码明细。
6. 代码、测试、发行manifest/hash、日志/XML与四类文档更新；共享STATE/BACKLOG/HANDOFF及session。

最终回复给用户用简明中文，先回答：**能否真实上报、实际上报几张、是否到最终成功、是否逐码核对**。
再说明剩余确切输入，指出哪份文件能交给谁处理。无外部材料时交付其余全部工作，
不能承诺仅靠AI补代码就消除DBA、业务和平台审批事实的缺项。
