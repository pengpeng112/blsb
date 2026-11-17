# 药品追溯剩余开发与部署收尾计划（2026-10-20）

任务：**DTR-FINALIZE-20261020**；本文件为下一AI执行任务书，不是完成报告。
编制任务：DTR-FINALIZATION-PLAN-20261020，Codex / unknown。

目标：一轮完成可独立推进的开发、自动验收、发行及安装工具，具备“网络未开时也能部署与自检，网络开后按固定步骤完成首张真实联调”的交付能力。
**目前剩余项不止网络。** 证书、HIS生产只读视图/状态口径、平台签名资格与业务字典等仍需落实；不能承诺只开网络就自动成功。
资料未齐时完成代码、模板、离线验收和可执行诊断，逐项登记确切缺口，不因一个外部项停掉全部任务。

配套：[验收矩阵](ACCEPTANCE.md)、[网络与资料协调单](EXTERNAL_INPUTS_AND_NETWORK.md)、[下一AI提示词](NEXT_AI_PROMPT.md)。
共享进度仍只维护docs/ai/STATE.md、BACKLOG.md、HANDOFF.md；本计划不建立平行状态系统。

## 1. 最新事实与范围

| 项目 | 已证实 | 不可据此推断 |
|---|---|---|
| <prod-host> | 2026-10-20约12:00仍发行20261016T114735Z，6容器healthy，443入口 | 本地药品代码已开发不等于服务器已升级 |
| DMZ | 可SSH登录；Anolis7.9；无443/8443监听；既有8001归属未核 | 不得占用、停止或重装既有8001服务 |
| 官方访问 | Windows两项真实只读调用SUCCESS，医院名称/ref_ent_id一致，ent_id已保存私有profile | 不等于DMZ出口通、上传权限/服务有效期/应用归属全部验收 |
| 网络 | <prod-host>→DMZ443/8443拒绝；DMZ→eco.taobao.com443超时 | 无监听不能直接认定防火墙没开；过去HTTP80通不能证明HTTPS443通 |
| HIS | 服务his、真实三表/主键/索引/8条OUT复合关联已核；r2=LIVE_READONLY_PROBED | 非生产readonly认证、非全院状态/拆零/退药批准、非实际视图已建 |
| 本地程序 | FIX3历史全量334通过6跳过；10-20空项签名修复局部80通过1跳过 | 历史测试数不替代本轮全量和现场证据 |
| 真实上传 | 0笔 | 两项查询SUCCESS不能说成已上报 |

主要证据：[真实平台与现场联调](../platform-live-test-20261020/README.md)、
[HIS活库事实](../his-live-20261019/README.md)、[FIX3交付](../drug-trace-20261016/fix3-20261017/README.md)。
旧r6含拼接游标示意，已被FIX3逐字段keyset替代；新r7必须采用当前实现，不能照抄旧错误。

本期业务固定为患者发药零售接口`alibaba.alihealth.drugtrace.top.yljg.uploadretail`，以及状态查询。
供应商出入库profile保留已有能力，默认不启用；病理与metrics继续保留。
首批业务范围由院方确认；未确认的住院、拆零、退药分支可开发和合成测试，但真实数据须质量等待。
不重建框架、不换编程语言、不搬迁implementation布局、不清库、不覆盖未提交FIX2/FIX3/10-20成果。

## 2. 三种交付终态，防止最后只剩一句“等网络”

| 终态 | 必须满足 | 不能标为完成的内容 |
|---|---|---|
| 软件交付完成 | FIN-00—09可离线项完成、局部及必要全量通过、两发行包可校验、诊断/回退可运行 | 假装现场部署或真实业务成功 |
| 现场安装完成、业务禁发 | 两机新发行/服务有效、可信TLS/mTLS本机及已通路径验收、配置和备份留证；collect/send按缺项关闭 | 只有socket占端口、只有systemctl active、只有模板或fake docker |
| 首张真实业务验收完成 | 网络/平台/HIS/字典齐全，明确一张完整未上报单据，上传一次、终态确认、数量和码核对 | HTTP200、上传受理、Mock或空单号查询 |

网络未开时可以达到第一层；材料齐全且维护窗口可用时可以达到第二层。
第三层必须在真实依赖满足后执行，不删除发送门禁来满足“一次完成”。

## 3. 开工规则与执行顺序

先读根AGENTS、docs/ai/START_HERE/STATE/HANDOFF及本包四文档。核对其他活动session并登记：

```powershell
python scripts/ai_session.py status
python scripts/ai_session.py start --task DTR-FINALIZE-20261020 --tool <实际工具> --model <实际标识或unknown> --scope implementation --scope docs
```

默认单AI顺序执行。保留当前未提交工作字节、Git状态/基础发行清单；不得reset、clean或覆盖他人工作。
按FIN-00→01→02→03→04→05→06→07→08→09推进；FIN-04/05/06互不依赖的离线工作可交替完成，不需要再问“是否继续”。
FIN-10现场变更按用户交付本任务时的实际部署委托和既有维护窗口执行；用户只要求编计划时不自行上机变更。
FIN-11在业务输入与网络齐备后执行。遇一项缺失只阻断其依赖步骤，完成其余工作再统一交接。

## 4. 工作包

### FIN-00：基线、资料与可运行环境盘点

交付现状清单（无秘密）：当前源码/镜像/发行ID、迁移台账、插件集、活动任务、Python/OpenSSL/systemd版本、已有证书SAN/有效期、端口占用与待输入。
本轮旧结果只能作为起点；现场实际盘点允许只读，不自动重启服务或扫描其他网段。

明确区分构建机、<prod-host> openEuler22.03、DMZ Anolis7.9。DMZ实测Python/systemd版本优先，不凭系统名称断言版本。
预先记录安装前8001监听、原有进程所属服务及资源基线；只查必要身份，不输出进程里的秘密参数。
建立本轮代码/配置可恢复基线，冻结包、原扫描文件、r1/r2来源档案及旧payload保留。
已有平台凭证位置与HIS通道见协调单，不重复索要密码，不读取服务器托管数据库凭据内容。

### FIN-01：DMZ运行时与旧systemd兼容（必须改，非可选文档项）

涉及`implementation/deploy/dmz/`。
现有service使用AmbientCapabilities、ProtectSystem=strict、ReadOnlyPaths/ReadWritePaths和StandardOutput=append，
不能直接宣称兼容旧systemd；现有ExecStart固定/usr/bin/python3，也不适合私有运行时。
旧systemd219文档支持socket activation及journal，但对应文件系统选项使用Directories命名，ProtectSystem仅布尔/full。
依据：[systemd219 socket](https://raw.githubusercontent.com/systemd/systemd/v219/man/systemd.socket.xml)、
[exec](https://raw.githubusercontent.com/systemd/systemd/v219/man/systemd.exec.xml)。现场仍须核对精确版本并实测。

实施方案固定：**新增.socket托管443，非root转发器接收systemd传入监听FD**。不依赖旧系统不支持的AmbientCapabilities；不对通用Python解释器setcap。
`.socket`使用ListenStream=<relay-net-host>:443、Accept=no；service使用专用blyb-dmz账号、journal日志与经旧版本验证的最小权限选项。
不得因为兼容困难而永久root运行、关闭SELinux/防火墙或自动增加端口重定向。

转发器实现受控systemd监听FD模式：验证LISTEN_PID、FD数量/类型/实际地址端口，关闭不用FD；错误则明确拒绝。
本地测试可保留显式高端口独立监听模式；生产socket模式不得静默降级成另一地址监听。
服务重启可复用socket；停发、卸载和回退要同时控制socket与service，避免stop service后被socket再次拉起。
**443被socket占用不等于服务健康**：安装验收还须触发真实TLS/mTLS与GET /health验证。

提供独立、精确版本的el7/x86_64兼容运行时，建议受维护的Python3.11系列，最终版本/来源/哈希/许可证/安全维护说明写manifest。
必须在目标等效glibc/OpenSSL环境验证ssl、SNI、TLS1.2+和CA校验；不拷Windows运行时或el8 rpm。
安装到应用私有路径并在unit引用，不替换系统Python、不升级整机systemd。网络未开时从构建机交付离线材料。
材料无法取得时，完成构建脚本/manifest/校验/安装逻辑，将“目标兼容运行时材料”单独BLOCKED，不写假哈希或宣称已验证。

### FIN-02：转发器补强及超时、日志与健康诊断

保留固定上游HTTPS eco.taobao.com:443/router/rest、固定本地POST /alihealth/router/rest、mTLS与精确源IP允许列表。
请求/响应body保持原字节，不重签、不重编码、不缓存、不自动重试；不接收任意目标URL、CONNECT开放代理或重定向。
本期默认允许retail上传和状态查询；已有inout能力仅在明确配置启用时放行；不能因企业目录探针成功就扩展所有方法。

必须核查并修复以下边界：

1. TLS握手、请求头、请求体、上游连接/响应头/响应体、向客户端回写均有明确预算；总时限覆盖所有阶段，不只read1循环。
2. TLS未握手/半请求/慢请求不能阻塞accept或耗尽线程；在握手和读body之前限制连接并发，完成/异常后释放名额。
3. 严格拒绝歧义Content-Length、Transfer-Encoding冲突、非法/重复TOP关键参数、畸形UTF-8/表单、负长度/超限；不以errors=replace改变校验语义。
4. 校验端口1—65535、正的超时/限额、精确有效IP、TLS证书链/私钥配对/CA可加载、SAN及证书用途；--check-config不能只看文件存在。
5. `_reject`、BaseHTTPRequestHandler日志、上游Location和异常均不得记录原始查询串、请求行、签名、session、追溯码或请求body。只记随机关联ID/方法枚举/大小/状态/耗时；不要用签名表单哈希当稳定业务ID。
6. `/health`仅表示本地进程/TLS可服务；上游不可达不能变成“平台正常”。提供CLI分层diagnose（运行时/配置/TLS/上游），错误返回非零并输出脱敏JSON。
7. 健康探测也要鉴权：业务POST只允许<prod-host>；本机可使用单独受限运维探针证书访问只读health，不能把<prod-host>客户端私钥复制到DMZ。公开health不带业务和敏感配置。

上游无路由、DNS失败、TLS不可信/主机名错、超时、3xx、超大/慢流响应均有稳定错误分类。
失败不能伪造平台业务JSON；发送过后失败属于结果未知，上层先查询。生产上游常量不可由请求/Host/环境代理变量改写。
若院方明确只允许固定出口代理，在同轮补受限CONNECT模式（仅固定官方网关443、验证上游TLS、不重试）；
否则直连为本期确定方案，代理未配置时不自动继承HTTP_PROXY，不把代理支持当必做新框架。

### FIN-03：真正可用的离线安装、验证、升级和回退

现有install.sh --verify在inactive/无443时只警告且可exit0，必须整改；还需解决PATH python、旧版本python已存在不安装新运行时、service来源与材料哈希脱节、仅代码prev回退等问题。

交付打包器、preflight/install/status/diagnose/rollback/uninstall命令及参数说明（名称可整合，能力不能省略）。
材料manifest覆盖**实际使用**的代码、unit/socket、运行时、依赖、配置schema和公共证书指纹；SHA256SUMS缺失/篡改/路径越界/目录穿越一律安装前失败。
私钥及真实环境配置走受限配置目录单独校验，不进入通用发行包/镜像/共享哈希日志。

升级采用版本目录+原子current切换，区分不可变代码与可轮换配置/证书；保存旧代码、unit/socket、配置/证书引用、运行时路径和启停状态。
安装前检查端口占用且不抢占；先离线配置校验，再切换、daemon-reload、启动并健康验收；任何失败恢复上一可用版本。
回退覆盖代码+unit/socket+相容配置引用，私钥轮换不盲回旧证书；无上一版本时明确说明首装恢复/卸载路径。
升级成功但上游未通应明确“LOCAL_READY / UPSTREAM_BLOCKED”，安装成功不能写“平台可上报”。

`--verify`要求版本/路径/权限、对应socket+进程身份、实际监听地址、TLS/mTLS与health全部通过，否则非零。
不要求上游通才允许本地安装验收；上游检查是独立状态及退出码，结果不得被警告吞掉。
日志用journal及受控保留设置，不依赖缺失的logrotate配置；本轮新增日志策略不得修改其他服务全局保留规则。
不覆盖DMZ8001，不安装Docker作为转发器前提；重复安装不清配置、不轮换密钥、不重复创建规则。

### FIN-04：HIS来源r7契约、SQL与完整性/变更发现

交付新`view-contract-r7.md`、Oracle11g建视图/最小授权审核脚本、只读核验SQL、来源档案新revision和对应自动测试。
原始r1/r2、旧r6和历史SQL均保留；未验活库的SQL清楚标候选草案，不能填LIVE_CONTRACT_VERIFIED。
本任务不得自行在HIS执行DDL/DML/触发器/索引；用户另行点名具体对象授权且通道符合要求后，方按该授权办理。

继续使用已验证三表及完整键：

- M=PHARMACY.DRUG_PRESC_MASTER，PK=PRESC_DATE+PRESC_NO，时间发现索引M.DISPENSING_DATETIME。
- D=PHARMACY.DRUG_PRESC_DETAIL，PK再加ITEM_NO；T=DRUG_USER.MED_TRACE_RECORD，PK=RECORD_ID。
- T.TRACE_BILLNO=TO_CHAR(M.PRESC_NO)，T.PRESC_DATE=M.PRESC_DATE，T.STORE_CODE=M.DISPENSARY。
- T.PRESC_DETAILID=TO_CHAR(D.PRESC_DATE,'YYYYMMDDHH24MISS')||'_'||TO_CHAR(D.PRESC_NO)||'_'||TO_CHAR(D.ITEM_NO)。

设计主单与逐码两层；主单身份保留PRESC_DATE秒级完整值，不能TRUNC、只用处方号、LISTAGG明细ID或药品码偶然相等关联。
保留T_TRACE_CODE原字符串；不MAX抽码、不复制码凑数、不用DISTINCT掩盖多匹配。一码多事件与同单重复码分别处理。
全部模板引用列必须显式存在，缺项带类型NULL和原因；不能伪填finalized=true、quantity=1、expected_code_count或source_revision。

实现可批准的业务规则适配层：保存源原始状态，正常门诊/拆零/退药/住院分支独立；缺批准规则进入质量等待并展示，不静默跳过。
样本住院SOURCE=1/FLAG=0，不得全院套FLAG=1。D.QUANTITY为明细数量，不重复赋给每个码；TRACES_SUM不是已确认的码数或码级数量。
首批未批准拆零/退药时整个相关业务范围明确禁用，不能丢掉难处理的行后宣称“整张单据完整上传”。

交付两种明确模式：
1. **显式批次模式**：指定小时间窗/完整业务键，读取整单与全部码，首尾复核revision，持久重取及幂等；可用于首张联调。
2. **持续增量模式**：只在有可靠、可索引的变更来源时启用，覆盖旧单补码/删码/无码转有码/退药。接口与测试先实现；实际变更源由HIS/DBA确认。

MED_TRACE_RECORD约839万统计行且没有时间索引，不能全表GREATEST/MAX扫描当解决方案。
短回看/周期抽样只能作为有边界补充，不能保证捕获任意旧单变化；无可靠变更源时AUTO_PRODUCTION显式阻断，不影响批次开发验收。
SourceProfile必须基于生产受限readonly登记、已验视图/映射、业务口径和相关范围创建新revision，不能删require_runtime_ready或把r2改标签。
真实核验先读[sjzc统一入口](../../startup/DATABASE_ACCESS.md)，已有密码不再索取，<relay-jump>托管凭据不复制到<prod-host>。

### FIN-05：平台契约、签名资格、配置与查询正确性

保留10-20空参数过滤修复及独立参考用例。取得官方SDK语言/版本/来源/哈希/适用方法后完成跨语言表单向量对照；
官方文字示例摘要与代码不一致已有证据，不能据此把程序改成错签或冒称官方SDK通过。
若技术方采用官方源码提取+独立向量+真实只读调用作为替代验收，写明证据与采用依据，明确不是SDK包验证。
只有证据满足批准口径，才能在可审查适配器revision中处理签名资格；禁止只改SIGNATURE_VERIFIED常量或加“强制发送”开关。

补通可选session授权配置的端到端：SecretRef/安全解析→build_client→签名→冻结配置引用/轮换；不把明文session放配置快照、日志或浏览器。
医院自用确实无需session时允许缺省，不能强行要求用户提供；当前已成功两项无session查询不代表所有方法永久无需授权。

解决平台当期契约口径：bill_type的321/323、result_type/sub_process_flag状态、查询日期究竟是上传创建日期还是业务日期、日期格式和历史范围、码数边界。
这些数值一律以官方当期确认及批准revision为准；用户指南摘录和API示例冲突不得猜。
特别检查`service._query_dates`：历史业务bill_time与实际上传日期可能不同，未确认官方查询日期语义前不得固定仅按业务日期±1天查询。
保留实际发送时间/时区，并依官方确认选择查询窗口；旧提交依据不足时显式人工核查，不静默NOT_FOUND→重发。

AppKey/Secret/ref_ent_id/医院名称已保存；ent_id也已查询保存，零售不因缺供应商ent_id阻断。
企业目录匹配不等于AppKey医院归属/上传权限证明；不要通过伪造空业务单据测试权限。
规范化含空列表/空model/缺字段/权限错/异步处理中/部分成功/处理失败响应；未知组合转人工，不误确认。
查询自动轮询、配额、配置revision、租户/owner、撤销停发、UNKNOWN不重发等FIX3约束全部保留。
现有三方对账能力边界：若未有官方码级/全范围接口，明确单据级已核、码级未核及remote_extra_checked=False；不得把码详情查询当全流转对账。

### FIN-06：<prod-host>配置、安全注入、只读预览与初始化工具

交付幂等初始化/检查工具：配置schema校验、connector/destination草稿、医院/药房字典草稿、资料清单、dry-run预览及部署就绪报告。
默认只生成草稿和脱敏差异，不写APPROVED证据、发布未知字典、生成任意租户通配范围或启动真实采集/发送。
具备既有有效授权和具体批准输入时，工具可执行相应发布步骤；不因文档模板存在就自动批准。
重复执行不重复建目标/任务、不轮换管理员令牌、不覆盖现有字典或历史提交。

检查Compose对**真正执行签名/查询的worker以及确有需要的API进程**的SecretRef环境/密钥后端、mTLS证书/CA只读挂载；
Oracle受限DSN/驱动/Instant Client也须在实际采集进程中可用。仅Windows包装器解密成功不算Linux已注入。
AppSecret只在<prod-host>受控签名侧保存；DMZ、镜像层、Git、网页、共享文档不保存。
现有仅支持env:解析时，明确以哪个受限服务环境注入，并防止docker compose config/debug/env输出泄漏；不假称已支持file:。
平台登记的生产数据库凭据按管理员受控交付，不从<relay-jump>凭据文件取明文复用。

就绪报告区分：CODE_READY、LOCAL_SERVICE_READY、NETWORK_READY、SOURCE_READY、PLATFORM_READY、SEND_SCOPE_READY及总体状态。
每项有实际检测时间、配置/发行revision、缺失原因和下一步；未测=UNKNOWN，模板齐全不等于READY。
现有`/api/v1/drug-trace/readiness`按能力准确反映阻断；新增报告不能绕过原证据门禁或跨租户暴露。
管理UI展示真实/Mock、禁发原因、质量等待、候选码数、首批配额、受理/处理中/终态/对账边界；无原始患者信息。

### FIN-07：证书、运维与一键诊断

补齐服务端SAN证书申请CSR、<prod-host>客户端mTLS证书、DMZ只读运维探针证书、CA链/有效期/用途检查与轮换流程。
现有certs.sh主要签serverAuth，不能直接拿同一张服务端证书当clientAuth证书。
院方PKI缺失时可以用明确TEST-ONLY链完成隔离验收，不能自动把测试根加入生产全局信任或关闭名称校验。
若院方明确批准专用内网CA用于正式链路，记录信任范围、保管/续期责任；根私钥不放DMZ发行包。

两机时间/DNS诊断支持旧版timedatectl/chronyc/ntpq命令，未识别参数不能伪称NTP通过；时间源由院方提供。
备份包含数据库、对象、配置/字典/任务/配额/迁移/必要证书引用；敏感备份加密或受控保护。
提供可核实隔离库的backup→restore演练，不自动向生产restore；暂停/撤销/回退要覆盖正在运行的worker及socket自动激活。
本机/DMZ/平台三段关联追踪采用随机请求ID与内部submission引用，不输出原始码、签名、Secret/session或患者信息。
诊断只读命令按组件逐层执行，不测试真实上传；错误区分DNS、拒绝、TCP超时、TLS、HTTP、签名/权限、业务状态。

### FIN-08：局部、全量、发行与隔离闭环

按[验收矩阵](ACCEPTANCE.md)逐项实测。先相关局部再一次必要全量；新变更/失败才扩大或重复，不靠测试数量当证据。
关键TLS/socket/旧systemd/Oracle等效验证缺失时标受影响项BLOCKED，不用skip/xfail/弱化断言造绿。
已有openssl缺失可使用现有可核实隔离Linux环境完成TLS用例；无环境则如实列出，不运行会杀共享端口的功能脚本。

构建两份独立可校验发行：<prod-host>应用离线包、DMZ转发器+兼容运行时离线包。
附release manifest、完整变更清单、运行时/依赖来源和哈希、安装/校验/回退入口、日志/XML及隔离验证结果。
<prod-host>包沿用`implementation/deploy/release/build_release.py`与实际`deploy/compose/docker-compose.yml`，不要误写deploy/linux/docker-compose.yml。
发行白名单、.dockerignore、镜像层不得含var/真实SQLite/.ai/凭据/私钥/患者文件；不仅看最终容器目录。
打包可引用受控外置证书/配置，不能把私有目录整体打入包。
隔离模拟“应用→本地mTLS转发→本地假平台”走真实socket/HTTPS，实现采集→冻结→发送→自动查询→对账；每处显式SYNTHETIC，零官方业务上传。

### FIN-09：完整交付与下一AI无需重新猜的操作入口

更新既有四类实施文档、API矩阵及DMZ部署README、r7、运维runbook；更新共享STATE/BACKLOG/HANDOFF。
交付本轮session、代码/发行manifest、逐项验收结果、未测/阻断表、脱敏候选预览样例、网络开后操作命令。
每个未完成项必须说明提供方、准确缺失、恢复步骤、影响能力及哪些工作已完成；无秘密值。
给出安装命令的**实际已实现参数**，不得将本任务计划中的新命令名当已经存在。
结束前运行文档目录/链接/源码档案hash检查，保留历史233处链接问题，不修改冻结证据或加白名单造绿。

### FIN-10：现场禁发部署（材料和窗口满足后同轮完成）

先确认实际维护委托和窗口；有授权不逐个函数重复询问。现有基础栈不重装，原密码和运行数据不重置。
按序：只读预检/备份→DMZ运行时及socket+service→本机可信TLS/mTLS健康检查→<prod-host>新发行→迁移及插件→实际配置注入→禁发状态确认。
生产PG从实际001—008升级时要包含009/010/011及本轮新增编号迁移；不能只测试新建空库。
`BLYB_PLUGINS=pathology,metrics,drug_trace`仅代表加载能力，不批准采集/发送；只有局部证据有效时才开启对应能力。
主机只保留约定SSH/443，后端8000/8001/8002及DB5432不发布宿主；DMZ8001原服务保持。
两台分别记录安装前后发行、进程用户、监听归属、mTLS健康、重启/开机启动、回退和原服务可用性。
网络未通时完成本地安装验收，保留UPSTREAM_BLOCKED及禁发；不以安装失败为由回退成HTTP或直连绕过DMZ。
没有现场窗口时，完整材料和命令先交付，明确“未部署”，不伪报现场完成。

### FIN-11：网络开通后固定联调与首张真实验收

网络开通申请与方向见协调单，分层保存证据：<prod-host>→DMZ TCP→可信TLS/mTLS健康→DMZ DNS/官方HTTPS→经正式链路真实只读查询。
此时使用实际应用签名路径，不用Windows独立探针冒充应用验收；已有探针回执不删除/覆盖来重跑。
确认院方业务/平台字典、HIS视图与readonly、既往上报口径及具体一张完整候选。
将机构/药房/日期窗/max_bills=1/配置revision及核对人固定；未明确范围时只生成具体预览，不自动任取历史单据。
用户已有范围等价授权时继续执行，不重新笼统问“允许测试吗”；缺少的仅询问具体业务资料。
上传前核单号与所有真实码；POST一次→保存受理→自动查询终态→核单据及码数→签认。
查询权限错/日期错/网络中断不是NOT_FOUND，不能换单号重发；部分成功、未知或不一致立即停止扩大批量。
没有官方码级对账能力时保留人工平台核码证据，不能签“三方码级全部通过”。
本轮不默认补报2026-07-23至10-12全历史；历史补报是独立明确范围的后续批次。

## 5. 建议文件落点与依赖

| 工作包 | 主要文件/目录 | 必须输出 |
|---|---|---|
| FIN-00—03 | implementation/deploy/dmz/；新增.socket、打包/诊断/回退脚本 | 兼容运行时manifest、可离线安装包、严格退出码和本机实测 |
| FIN-04 | plugins/sources/db_view/；domains/drug_trace/；docs/deployment/reporting-20261015/view-contract-r7.md（待新建） | 真来源草案/新revision、两视图SQL及限量核验、完整性和变更模式 |
| FIN-05 | plugins/destinations/alihealth_drugtrace/；domains/drug_trace/ | 签名/授权/日期/状态契约及独立证据 |
| FIN-06/07 | deploy/compose/；deploy/linux/；apps/admin_web/；领域readiness | 受控初始化、真实进程密钥挂载、证书/运维/诊断工具 |
| FIN-08/09 | tests/；deploy/release/；既有实施文档、docs/ai/ | 回归log/XML、两发行manifest、按项交接 |
| FIN-10/11 | 两台现场及受控证据目录 | 禁发安装证明/正式路径核验/首张真实终态，分别标状态 |

FIN-01—03的软件开发不等待公网；FIN-04/05的批准材料未齐不阻断其他离线工作。
FIN-06草稿不等于发布；FIN-10不因FIN-11未满足而默认开启发送。
不扩大到病理新功能或供应商库存新需求，既有病理正常工作须回归验证。

## 6. 执行方最终报告格式

必须逐项给出：工作包/验收编号、改动路径、真实命令、退出码、证据路径、实测通过/实现未测/外部阻断。
独立列出：本机代码完成、发行完成、两台各自部署状态、网络分段状态、HIS实际视图状态、官方权限/字典状态、真实上传笔数与最终状态。
剩余清单若仍有SDK/证书/DBA/业务口径，不能写“只剩开网络”；全部落实后才可这样总结。
