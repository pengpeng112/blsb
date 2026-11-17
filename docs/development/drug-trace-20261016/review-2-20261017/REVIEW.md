# 药品追溯实现复核

日期2026-10-17；基线`0ca47c6`。事实来自当前源码、部署/开发交接、历史XML和本轮离线探针。没有重新探测现场，因此旧网络/证书结果只作为最后已知状态，不冒充今天的现场结论。

## 1. 能否正式上报

**目前不能。** 代码有领域模型、迁移、合成采集、Mock发送查询、API/UI和转发组件，具备继续完善的基础；但真实上传业务参数不完整、官方响应不兼容、部分成功可能错误确认、真实mTLS客户端未接通。即使把EXT项全部填成APPROVED，也不能认定可以上线。

原开发报告277 passed/6 skipped有XML支撑：283 cases、0 failures/errors、474.380秒，是本地历史测试。它证明对应测试通过，不证明官方协议和现场链路通过。当前Mock回执自行构造`success/list`结构，还用`result_type=3/sub_process_flag=52`演示确认，绕过并掩盖了关键差异。

本轮独立探针16个观测全部复现问题，见[evidence/probe-results.json](evidence/probe-results.json)。**探针退出0表示反例成功复现，不是业务缺陷已修复。** 对真实transport的探针将post替换为本地stub；禁止socket外连。数据库仅使用临时SQLite和虚构单据，结束清理。

## 2. 必须修复的问题

路径均相对`implementation/`；“反例”表示本轮离线复现，“静态”表示源码已见问题、现场影响未执行。

| ID / 优先级 | 证据位置 | 问题、影响和证据级别 |
|---|---|---|
| R2-01 / P0 | plugins/domains/drug_trace/service.py:301 | 业务表单缺bill_time，上传医院字段误用ref_ent_id而非ref_user_id；库存还缺physic_type/from_user_id/to_user_id/client_type，用supplier_ref/warehouse_code自定义字段替代；还把_doc说明发给上游。反例列出6个缺失库存必填项。正式接口无法据此受理完整合法单据 |
| R2-02 / P0 | destinations/alihealth_drugtrace/protocol.py:72、125；service.py:409 | 上传只认success，没有解析官方response_success及错误明细；查询没有展开result.model.result_list.bill_deal_status_search_do。反例：标准上传成功被当UNKNOWN，标准查询成功转MANUAL_REVIEW。查询请求另缺begin_date/end_date/bill_type，且只查第1页 |
| R2-03 / P0 | service.py:453、build_client；Mock脚本回执 | 只要result_type=3便CONFIRMED，忽略sub_process_flag=52部分成功。反例得到CONFIRMED；官方状态说明与此冲突。当前Mock把错误行为当闭环成功，必须改测试和Mock，不可保留错误断言造绿 |
| R2-04 / P0 | protocol.py:30；client.py；service.py:238 | SIGNATURE_VERIFIED=False通过秘密字符串前缀__mock__豁免，未校验transport是Mock；真实目标可引用mock:。反例在RealTransport类型+本地post stub下成功走到post。不是已发生真实外发，但“硬阻断绝不可绕过”表述不成立 |
| R2-05 / P0 | service.py:47、94、payload.py:54 | evidence_state接收但不校验config_release_id，且first()未按目标精确取证；send必需项没有EXT-05校时、EXT-10补报范围、EXT-12真实发送批准。反例：OLD证据对NEW配置仍APPROVED。字典按“最新已批准”查找、client读取destination当前配置，配置没有随提交固定 |
| R2-06 / P0 | service.py:321、409；plugin.py:_j_send/_j_query；workflow_core/jobs.py:enqueue_job | service层允许UNKNOWN/SENDING/CONFIRMED重新send，worker只挡CONFIRMED/ACCEPTED，缺在途失租CAS。dry_run竟创建尝试并把CONFIRMED改回PREPARED（反例）。轮询仅一次，同q:submission+相同payload完成后再入队复用SUCCEEDED，手工重查也不执行；回执固定query:submission:1只保留第一次结果（均有反例） |
| R2-07 / P0 | sources/db_view/drug_trace.py:180；ingest.py:content_digest；quality.py | Oracle未加引号的列通常大写，代码返回大写键而业务读小写；native datetime不能直接json.dumps存摘要/JSON，字符串'0'经bool变True。fake DBAPI分别复现；不是已连接HIS后报错。全局DSN/环境密钥未绑定具体connector、schema，真实多连接配置待补 |
| R2-08 / P0 | sources/db_view/drug_trace.py:TEMPLATES；ingest.py:composite_key/collect_window/import_bill；models.py | SQL游标拼药房|类型|单号，Python游标却含source_system；反例同一尾行再次满足下一页条件，可能重复/卡循环/漏数；拼接键与ORDER BY独立列也不等价。header/明细SQL缺source_system关联，登记唯一键使用展示source_bill_no而非完整来源主键；跳过持续变更单仍把checkpoint推到窗口末，没有持久重取账本 |
| R2-09 / P0 | plugin.py:_j_collect；service.py:run_collection；routes.py:pause_collection | 采集只在API入队时查证据，worker执行时不复核；run_collection直接把PAUSED改RUNNING。反例无collect证据、PAUSED任务照样DONE（仓储用空合成stub代替Oracle）。撤销证据不能保证已排队真实采集停下；请求profile也未实际限定抽取业务范围 |
| R2-10 / P0 | service.py:485 | 名称为三方对账，实际只比较本地来源快照与本地Submission，没有平台查询/远端快照；反例外部client调用0次却matched=1。profile过滤缺失，迭代全部旧来源版本，取Submission还可能未限定所选目标。码级“未核验”标记正确，但不能因此宣称单据级三方已核验 |
| R2-11 / P0 | destinations/alihealth_drugtrace/transport.py:25；service.py:build_client | RealTransport只有CA，无客户端cert/key及load_cert_chain，DMZ却CERT_REQUIRED；静态确定双方未接通。urllib默认重定向策略也未显式拒绝，超时只是连接+读取相加的单一值，未形成文档声称的分层控制 |
| R2-12 / P0/P1 | deploy/dmz/{forwarder.py,README.md,install.sh,blyb-dmz-forwarder.service} | 写“Python≥3.6”但使用future annotations、ThreadingHTTPServer及新ssl接口，3.6不满足；材料甚至列el8 rpm而现场Anolis7.9，未验证ABI/依赖。systemd服务普通用户绑定443无授予低端口能力方案；append日志/隔离指令兼容性待核实际systemd版本。上述不是只差一条安装命令 |
| R2-13 / P0/P1 | deploy/linux/backup.sh；apps/storage_service/app.py | 只停worker/core，却称它们是唯一写入者；storage仍提供写PG/对象的上传接口，exchange也可能写交换库。对象归档仍硬编码DATA_ROOT/objects而非OBJ_DIR。敏感产物末尾才chmod，失败可能残留宽权限文件。单库dump正确不代表多存储共同静止；现场升级前必须修复并验收 |
| R2-14 / P0 | deploy/linux/restore.sh/lib.sh；tests/integration/test_drug_trace_ops.py | 生产路径保护仅比相等，没完整比较包含关系/实际自定义生产路径；演练项目名可取blyb，可能down现网项目。--validate-only会创建/解包临时目录，不是绝对零写。现有恢复测试用fake docker/伪PGDUMP，只验证命令及文件落位；真实PG用例测迁移/采集，不是完整pg_dump→pg_restore→应用恢复 |
| R2-15 / P0 | payload.py:prepare/freeze；plugin.py:route_event；service.py:prepare_submission | 事件路由遍历profile并first选目标，没有按该业务profile/connector绑定唯一目标；prepare只检查目标属于两种之一，没要求恰好匹配请求profile。重复prepare先新冻结再返回旧Submission，有可能返回的新PackageRevision与旧Submission实际引用不一致；需联合配置固定一并修复 |
| R2-16 / P0/P1 | models.py；quality.py；routes.py；UI | 领域表无tenant复合外键；现有tenant过滤不等于owner/connector授权完整，隔离补强属上线前P0。父子包装字段存在但未阻止关联大小码同单；未知业务kind不一定被当等待。列表/对账缺完整keyset范围，overview混计其他领域提交；登录时序等体验项属P1 |

R2-06与R2-09会影响停发承诺，R2-03/R2-10会导致操作人员看到错误成功/匹配结果。因此不能把其归为上线后的“小优化”。

## 3. 官方文档核对依据

2026-10-17重新读取官方页，记录来源而不调用业务API：

- [医疗机构零售上传](https://developer.alibaba.com/docs/api.htm?apiId=52553)：上传医院参数ref_user_id、必需bill_time及response_success等响应；非SDK自行假设的success字段。
- [供应商出入库上传](https://developer.alibaba.com/docs/api.htm?apiId=52556)：另有收发企业、药品/客户端类型要求；ref_ent_id和ent_id语义不得混用。
- [处理状态查询](https://developer.alibaba.com/docs/api.htm?apiId=52558)：需创建日期范围、类型和分页；查询嵌套与全部/部分结果必须按正式契约解析。51/52及其他数值均以官方当期文档、批准口径与真实样例为准，不能照示例盲启用。
- [Python ThreadingHTTPServer文档](https://docs.python.org/3.11/library/http.server.html)：该类3.7新增，与当前“3.6可运行”的材料承诺冲突。

## 4. 已有实现与证据应如何定级

| 内容 | 正确定级 |
|---|---|
| 领域模型/009迁移/合成采集/部分API与UI | 有实现和本地测试证据，不能整体说“完全未开发” |
| 官方TOP参数/响应/状态、真实签名 | 部分实现存在已复现缺陷，外加SDK一致性待验；不是只等密钥 |
| Oracle真实接入 | 模板/类已有，合成测试未覆盖真实驱动数据形态；契约SQL仍含占位，待修代码与院方签认 |
| DMZ | 本机TLS测试已有；没有证明应用RealTransport能经mTLS接入，更没有Anolis安装证据 |
| 备份恢复 | fake docker脚本测试已有；真实PG迁移测试已有；完整隔离恢复及一致性仍未证明 |
| 三方对账 | 当前是本地状态核对雏形；平台侧未接入，不能登记三方完成 |
| 现场药品模块上线 | 上一开发者明确零现场改动；没有新发行升级或药品现场闭环证据 |

## 5. 本轮测试记录

历史全量277/6已核对XML，没有重跑全量。独立反例探针执行退出0，16个观测均复现；不是一次真实平台验证。

本轮还重跑原4组局部测试（domain/api/ops/dmz）。首次PATH选中不可用WSL bash且找不到OpenSSL，结果30 passed/12 failed/5 skipped，保留initial-environment-regression.xml；这些失败是测试环境问题，不列为12个业务缺陷。随后使用本机既有Git Bash/OpenSSL重跑，**47 passed、0 failed、0 skipped，81.28秒，退出0**，证据见evidence/local-regression.xml、local-regression.log与local-test-command.json。本轮没有安装依赖或通过修改测试规避失败。47个旧测试通过与16个反例复现并不矛盾，表明旧测试尚未覆盖这些正确业务边界。

## 6. 还缺哪些外部资料

EXT-01/02：合规readonly登记、准确SERVICE_NAME/SID、建好的两视图及完整字段/主键/变更机制、已批准小样本；原SQL不能直接当生产视图。

EXT-03/04/05/11：DMZ443出口/DNS、两段可信SAN证书和mTLS身份、院内NTP、匹配Anolis7.9的运行材料、维护窗口及异机备份/告警。当前不重新宣布“网络已通/不通”，要以维护阶段实测更新旧证据。

EXT-06/07/08/09：受限平台密钥、医院和供应商两种企业标识、API权限、官方SDK/签名向量、业务/码量/部分成功字典、查询与失败明细/码级对账的确切接口能力。不能用HTTP80与Mock代替HTTPS及平台权限。

EXT-10/12：历史是否已上报、起止范围和单号映射、第一批药房/业务/单据清单及真实发送授权。授权必须能约束已排队任务，不只是UI确认按钮。
