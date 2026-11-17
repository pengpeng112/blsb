# FIX3 整改交付（第三次复核 7 组问题）

2026-10-18；任务 DTR-FIX3-20261017；ZCode / GLM-5.3。
基线：review-3-20261017 复核后的未提交工作区（FIX2 字节基线之上增量修改，
不重置/覆盖既有成果；迁移新增 011，不改 009/010 与旧冻结 payload）。

## 1. 结论

review-3 的 7 组问题（R3-01—07、14 个离线反例）已全部按 FIX3-01—06 修复，
反例转换为 26 项正确行为测试（`test_drug_trace_fix3.py`，G3-01—G3-17 + 慢流
G3-19）+ 3 项真实隔离 PG 测试（`test_drug_trace_pg_fix3.py`，G3-18）。
全量回归 **334 passed / 6 skipped / 0 failed**（340 cases，666.79s，JUnit XML
见 evidence/full-regression.xml）。

正式上报仍不开启：SIGNATURE_VERIFIED 保持 False；EXT-01—12 未关闭；现场
（<prod-host>/DMZ/HIS）未做任何访问或变更。本包仅代码/测试/文档/本机隔离演练。

## 2. 逐项处置（R3 → FIX3 → G3 验收）

| R3 | 处置 | 验收 |
|---|---|---|
| R3-01 企业双ID混用 + 查询枚举复用 | FIX3-01：`ref_ent_id` 字典供上传 ref_user_id 与查询 ref_ent_id；新增 `ent_id`（医院收发侧）与 `supplier_ent_id`（供应商侧）独立字典；`query_bill_type` 独立选择器字典（官方 A/AI/AO，映射文档外值拒绝冻结）冻结进 payload；查询日期按冻结 bill_time（GMT+8）±1 天显式折算（payload.py/service.py） | G3-01 双ID双方向（字典值刻意不同）；G3-02 枚举独立+日期时区明确（test_g3_01/02/02b） |
| R3-02 拼接键与列序不一致 | FIX3-02：poll_bills 改「等值前缀+逐字段比较」谓词（since_ss/pc/bk/sb 绑定参数），与 ORDER BY 独立列同构；合成仓储同口径元组比较；游标 since_key 存 4 元组（旧拼接串游标降级为整刻重扫，幂等不漏） | G3-03 YF1/YF10、含分隔符键、同刻多页（test_g3_03/03b + SQL 文本断言） |
| R3-05 跨会话暂停失效 + 页commit拆开checkpoint | FIX3-02：安全点改独立只读会话（先结束本事务再另开会话读 run/connector/oracle 证据，不受 expire_on_commit=False 缓存影响）；采集事务改**页级原子 ADR**（每页全部快照+outbox+推进到本页尾的 checkpoint 同事务提交；重取账本独立提交且先过安全点；外部终态不覆盖：PAUSED 不被 DONE、撤销不改暂停） | G3-04 独立会话暂停即停；G3-05 第二页异常快照/checkpoint 原子一致+重启续跑不漏（test_g3_04/05） |
| R3-05 连接绑定丢失 | FIX3-02：worker 按 ConnectorInstance.active_config_revision 重建连接绑定（tenant/owner/PUBLISHED 校验，绑定失效跳过）；API 不再丢 dsn_env/对象白名单（Oracle [SCHEMA.]OBJECT 标识符校验）；checkpoint 分区 checkpoint_partition（历史补报独立） | G3-06 连接/范围不跨 connector/tenant（test_g3_06/06b） |
| R3-03 受理不自动查询 + 错误误判 + 满页不推进 | FIX3-03：ACCEPTED 自动排第一代查询；_platform_query_full 共用全分页（无 total 满页 page+1、重复页/预算/超页有界）；权限错误→QUERY_ERROR（保留 error 原文，绝不 NOT_FOUND）；半份分页数据不判成功；PROCESSING/UNKNOWN/QUERY_ERROR/NOT_FOUND（传播延迟）受限退避，达上限（12 代）显式告警回执+事件转人工；查询身份固定冻结配置（purpose=query 允许 RETIRED 只读） | G3-07 不点 UI 到 CONFIRMED（test_g3_07 + 浏览器证据）；G3-08 权限≠无记录、晚页错误不 CONFIRMED、轮询上限告警（test_g3_08*）；G3-09 满页推进/重复页/预算有界（test_g3_09*） |
| R3-04 scope 不执行 + 空配置通配 | FIX3-04：EvidenceBody/API/UI 增加 config_release_id + scope{窗口,药房白名单,max_bills}；approve 强校验完整 scope（空 scope/空药房/缺配额拒绝）；发送逐单复核（药房白名单+窗口，UTC 一致时区比较不丢偏移）；配额台账 drug_trace_send_quota 条件原子 UPDATE 并发预留（失败尝试保守占额）；evidence config_release 空值不再通配 | G3-10 空范围/排除药房/时区边界拒绝；G3-11 配额原子预留+耗尽拒绝；（test_g3_10/10b/11） |
| R3-06 reprepare 死路 + 配置跨租户/草稿 | FIX3-04：幂等键含配置代次；未发送旧提交可安全换代（新不可变提交固定新配置、旧提交 SUPERSEDED 不可再发、冻结包按 digest+config 代次区分不返回旧代包）；已发送（ACCEPTED/UNKNOWN/终态）拒绝换配置重发（走查询/对账）；build_client/prepare 校验 ConfigRevision tenant/owner/state（跨租户按不存在拒绝、发送仅 PUBLISHED、查询允许 PUBLISHED/RETIRED）；字典值/revision/version 三元组来自同一条命中记录 | G3-12 跨租户/DRAFT/他 owner 拒绝；G3-13 字典审计闭包；G3-14 换代机制两分支（test_g3_12/13/14） |
| R3-07 对账 page1/无日期可误匹配 | FIX3-05：query 与 reconcile 复用同一 _platform_query_full（含日期+冻结身份+独立选择器）；分页不完整（含 total>去重观测数）或平台错误 → 整轮 UNVERIFIED，绝不 matched/missing_remote/可补报；每单按其冻结 config_release 构建只读客户端；scope 显式声明 remote_extra_checked=False（无范围枚举能力，未检查平台额外单据）；missing_local 仅登记待人工，不自动补发 | G3-15 total=2 只见 1 项不 matched；G3-16 日期/身份/多目的地范围；G3-17 无自动补发（test_g3_15/15b/16/17） |
| R3-05(附) 慢流 deadline 软顶 | FIX3-06：RealTransport 与 DMZ forwarder 改 read1 分块（单次 recv 返回；read(amt) 内部循环读满永不超时）+ 每块检查剩余预算 + 响应大小上限；getresponse 后 conn.sock=None 的引用问题在 request 后捕获 | G3-19 慢流硬顶中断（test_g3_19，本地 TLS 涓流服务） |
| 复核附注：复合唯一索引≠外键 | FIX3-06：迁移 011（drug_trace_send_quota 表；PG 复合 tenant FK：bill_revision→bill、code_index→bill_revision、collection_run/retry_bill→connector_instance；孤儿/跨租户坏数据预检报错隔离不默删；connector_instance 补 (tenant_id,id) 唯一索引） | G3-18 FK 生效+跨租户悬挂拒绝+坏数据预检（test_g3_18/18b） |
| 复核附注：真实PG恢复未演练 | FIX3-06：本机 Docker 隔离 PG（每测试独立库）：灌入租户/连接器/字典/配置/目的地/采集→冻结→PREPARED 提交/任务/配额 → 容器内 pg_dump → 新库 psql 恢复 → 逐类行数+关键内容+迁移台账（001—011）+领域查询校验 | G3-18c 真实 backup→restore 演练（test_g3_18c） |

未修（如实登记）：
- SQLite 存量 009 库的旧唯一约束不在 011 重建范围——SQLite 仅测试用途且始终
  按最终模型新建库，无 SQLite 生产部署；生产升级路径（PG）已覆盖。
- Anolis 运行材料/服务权限同 OS 验证：无 Anolis 环境材料，保持阻断（EXT 项）。
- 现场备份脚本（backup.sh/restore.sh）的生产级演练仍待真实窗口；本轮为本机
  隔离 PG 演练（G3-18c），已满足“无需等待生产维护窗口先做本机演练”要求。

## 3. 证据

- 全量回归：evidence/full-regression.log、full-regression.xml（334/6/0，exit 0）
- 局部：`python -m pytest tests/integration/test_drug_trace_domain.py
  test_drug_trace_fix2.py test_drug_trace_fix3.py test_drug_trace_api.py
  test_drug_trace_ops.py test_dmz_forwarder.py -q` → **101 passed / 0 failed**
- 真实 PG：`test_drug_trace_pg.py + test_drug_trace_pg_fix3.py` → 4 passed
  （含 011 升级/FK/坏数据预检/backup→restore 演练；需 Docker）
- 浏览器禁发闭环（G3-19）：evidence/browser/01-send-blocked-no-evidence.png
  （无证据发送被拒 PROFILE_UNPROVED，证据表 MISSING）、
  02-accept-auto-query-confirmed.png（批准后发送→受理→**自动查询**→CONFIRMED，
  全程未点查询按钮；证据表 APPROVED + 新 scope/config 表单可见）。
  服务：`python tests/e2e/serve_drug_trace_ui.py 8791`（内嵌 worker）。
- 离线发行 dry-build：evidence/release-drybuild.txt（闸门通过、白名单解析、
  drug_trace/dmz 相关 19 文件入包、无缺失）。
- 前端：`VITE_CORE_DEFAULT='' VITE_STORAGE_DEFAULT='/storage' npm run build` ✓。

## 4. 行为变更（对既有测试的修正）

旧断言按 FIX3 正确行为更新（不维持错误行为）：t04 查询 bill_type 323→A；
t10 拼接游标断言→逐字段谓词断言；t05/t05b/t08/a02_full 证据登记补完整
scope+精确 config；b03 ref_user_id=ref_ent_id 字典值；字典夹具以
query_bill_type/ent_id/supplier_ent_id 替代已废止的 ref_user_id/supplier_user_id
命名空间。FIX2 既有 26 项测试全部保持通过（含义未削弱）。

## 5. 真实接入边界（不变）

SIGNATURE_VERIFIED=False（须官方 SDK/向量证据随适配器版本启用，不得改常量）。
EXT-01—12（HIS readonly/服务名/视图、SDK 向量、AppKey/权限/企业映射、DMZ
443/DNS/证书/NTP、Anolis 材料、维护窗口、历史口径、首批授权）逐项未关闭。
本包未连接任何真实系统、未发送任何数据、未访问 <prod-host>/DMZ/HIS。材料齐备后
现场仍按：禁发升级 → 只读预览 → 明确机构/药房/时间/配额小批真实单据 → 终态
查询 → 对账签认。
