# FIX2整改后复核报告

2026-10-17；Git HEAD `0ca47c6f718b4dccd53704bc151c24008dee5b07` 加当前未提交FIX2工作区修改；基线文件哈希在evidence/baseline-hashes.json。修改前Git状态保留，未覆盖另一AI的改动。

## 1. 当前能否上报

不具备正式上报条件。已有明显实质修复，但“全部可离线项完成”仍不准确。还需要修复下列7组问题，完成SDK签名一致性、院方资料与现场验证，才可申请有限范围真实上报。

本轮未访问<prod-host>/DMZ/HIS，不读取凭据，不发送真实或合成单到正式平台。现场状态沿用最后已知证据，不能推断今天网络是否已放通或新版本是否已升级。

## 2. 本轮确认的进展

| 内容 | 当前证据 |
|---|---|
| 官方上传成功响应 | 当前response_success能够被识别为ACCEPTED；独立探针确认 |
| 部分成功 | result_type=3/sub_process_flag=52不再被标为CONFIRMED，而是PARTIAL；独立探针确认 |
| Mock签名隔离 | 已改为检查transport类型，真实目标拒绝mock:；SIGNATURE_VERIFIED仍False，未冒充官方验签完成 |
| Oracle类型、持久重取、暂停入口、发送状态守卫 | 有实际修订及新增测试；不等于全部边界正确，残余问题见下表 |
| 应用mTLS与DMZ方法白名单 | 新增了应用RealTransport到本地转发器/模拟上游测试；本轮相关7项测试通过 |
| 既有全量回归 | 解析XML：311 cases，其中305 passed/6 skipped，0 failures/errors，519.355秒。本轮没有重跑全量 |
| 本轮局部复跑 | FIX2新增测试26项+DMZ7项，33 passed/0 failed/0 skipped，19.78秒。见evidence/local-tests.xml、local-tests.log |

局部测试通过不等于剩余正确性要求已验证。独立[evidence/probe_review3.py](evidence/probe_review3.py)将socket.connect设为禁止，使用临时SQLite、fake客户端和虚构企业标识；14个反例均复现，结果见[evidence/probe-results.json](evidence/probe-results.json)。探针退出0表示发现成功，不表示修复通过。

## 3. 仍需修复的7组问题

代码路径均相对implementation/。R3-*不与旧R2/EXT编号混用。

| ID / 优先级 | 代码位置 | 复核发现与证据 |
|---|---|---|
| R3-01 / P0 | plugins/domains/drug_trace/payload.py:87；service.py:perform_query/run_reconciliation | 库存单医院一侧from/to直接使用ref_user_id，该值按官方上传定义是医院ref_ent_id，而收发企业字段需ent_id；采购/退供应商方向虽已区分，医院企业标识仍混用。反例HOSP-REF被填进采购to_user_id，独立HOSP-ENT没有被读取。查询还把上传单据类型323传给query.billstatus的bill_type，官方列的查询选择值为A/AI/AO，不应直接复用上传枚举 |
| R3-02 / P0 | plugins/sources/db_view/drug_trace.py:TEMPLATES；ingest.py:composite_key | 已补source_system，但过滤仍按带竖线拼接字符串比较，排序却按独立列。YF1、YF10反例：tuple顺序YF1在前，拼接字符串顺序相反，下一页会跳过YF10。同名“同序”注释不证明等价。该反例是谓词演示，不是活库Oracle执行 |
| R3-03 / P0 | service.py:perform_send/perform_query/_classify_query；plugin.py:_j_send | 正常ACCEPTED没有排查询任务，反例新query jobs=0；只能靠人工点击才可能到终态。查询权限错误被parse为found=None/unknown=False后，service丢弃error并改判NOT_FOUND，反例错误信息也变空。无total_num的满页路径没有page+=1，连续请求1、1、1、1，本探针主动截断，实际代码的QUERY_MAX_PAGES也因page不变无法保护。还有部分页成功后遇错误继续用已有entries判断成功的风险 |
| R3-04 / P0 | service.py:evidence_state/_check_send_scope；routes.py:EvidenceBody/approve | 发送scope只处理同时有起止的时间窗，空scope直接放行；药房白名单和max_bills不执行。反例“排除YF01且配额0”仍通过。API的EvidenceBody及approve调用还不接受scope/config_release_id，所以界面无法录入精确批准。空证据config_release_id被当通配，面对NEW配置仍APPROVED。所谓首批授权门禁不能只靠EXT-12键存在 |
| R3-05 / P0 | service.py:run_collection._should_continue；ingest.py:collect_window | expire_on_commit=False，安全点commit后db.get仍读session缓存，不刷新其他事务的PAUSED；反例独立session暂停后仍读2单并最终DONE。回调每页commit又提前提交来源快照/outbox，第二页失败时反例已持久化1单但checkpoint=0，违背所声称同事务边界。安全点也没重新检查来源证据/connector状态，且重取账本先于安全点执行 |
| R3-06 / P0 | service.py:prepare_submission/build_client/perform_query；payload.py:lookup_mapping_rev | 配置漂移后提示重新prepare，但幂等键不含提交/配置代次，重复prepare只返回旧提交/旧冻结包，无法按提示恢复。反例仍旧绑定。build_client读取指定ConfigRevision没有tenant/owner/PUBLISHED检查，反例本租户目标成功加载另一租户DRAFT配置的虚构AppKey（仅Mock、未读取真实密钥）。字典值用旧批准版本，记录revision却取最新草稿，审计闭包不真实。query/reconcile仍读目标当前配置，未固定原提交身份 |
| R3-07 / P0 | service.py:run_reconciliation | 独立远端查询已接入，这是进展；但其查询路径仍缺必需begin_date/end_date，且只读page1不核total_num。反例total_num=2、只看到1项，仍matched=1。错误响应found=None/unknown=False也可能被判远端缺单，run保持COMPLETE。不能据此批准补报或声称三方对账完成；也没有枚举平台额外单据的能力 |

R3-04及历史checkpoint分离等虽然在开发报告列为“后续小项”，其涉及真实发送范围、漏数和重报，必须作为首批上线前条件处理。

## 4. 协议依据

2026-10-17本轮重新核对：

- [供应商出入库API](https://developer.alibaba.com/docs/api.htm?apiId=52556)：上传ref_user_id对应医院ref_ent_id；from_user_id/to_user_id是收发企业ent_id。需要分别映射，不能因字段都叫“医院ID”就共用。
- [状态查询API](https://developer.alibaba.com/docs/api.htm?apiId=52558)：查询有独立日期/类型/分页契约。参数、枚举与边界以官方当期文档及平台确认版本为准；如平台另外支持数字型bill_type，应取得证据并固定profile，不猜兼容。

## 5. 其他改善/证据缺口（未新增活库判断）

1. 真实HIS视图/readonly服务名、SDK官方向量、平台权限/企业映射、DMZ出口/证书/NTP、Anolis材料与维护窗口、历史范围和首批授权仍没有完整签认证据。
2. 当前仍未见<prod-host>升级到FIX2发行及药品现场闭环证据；不要把工作区源码改好等同于服务器已运行新版本。
3. 真实隔离PG的完整backup→restore→应用/对象/任务校验仍未完成，迁移PG测试不代替恢复演练。无需等待访问生产服务器才能先在本机隔离环境做此演练。
4. migration010给表补复合唯一索引不等于tenant复合外键；SQLite旧009真实升级路径未完整调整旧约束。需要对应结构/坏数据/跨租户关系测试，而不是只查API本租户列表。
5. transport/DMZ的total_deadline是在resp.read完成后检查，不是硬超时中断。慢速持续响应可能长时间占用连接，应补剩余时间预算、响应大小和慢流测试。
6. connector_config的dsn_env虽在构造函数支持，API入队仅传mode，worker未从连接器批准配置恢复完整绑定；profile抽取范围和owner授权也需系统性校验，不能靠全局DSN与单tenant演示推定完成。
7. 正式签名保持False是正确的保守状态；不能为演示上报而直接改True。工作区大批修改尚未提交/发行，后续修复应建立包含当前字节的可恢复基线，再交付可追踪发行包。

## 6. 建议下一步

先修R3-01—07并加入独立边界测试；再完成本机隔离PG恢复、完整浏览器状态闭环和必要回归。材料齐备后升级<prod-host>/DMZ并仅做受控只读预览；正式发送只在机构/药房/日期/单据/配额授权齐备后分小批进行，逐批查询终态并对账签认。

执行任务与验收见[NEXT_AI_PROMPT](NEXT_AI_PROMPT.md)。本轮不替用户开启生产，不把外部未验收标成已失败或已通过。
